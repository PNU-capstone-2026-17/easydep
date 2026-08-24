from __future__ import annotations

import re
from pathlib import Path


def repair_nested_e2e_members(path: Path) -> bool:
    """Unwrap class members accidentally nested in a synthetic test method."""
    if not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    wrapper_index = next(
        (
            index
            for index, line in enumerate(lines[:-1])
            if re.search(r"^\s*@Test\s*$", line)
            and re.search(
                r"\bvoid\s+[A-Za-z_$][\w$]*\s*\(\s*\)\s*\{",
                lines[index + 1],
            )
        ),
        None,
    )
    if wrapper_index is None:
        return False

    depth = 0
    wrapper_close_index: int | None = None
    nested_member = False
    for offset, line in enumerate(lines[wrapper_index + 1 :], wrapper_index + 1):
        stripped = line.strip()
        if depth > 0 and re.match(r"@(Autowired|BeforeEach|Test)\b", stripped):
            nested_member = True
        depth += line.count("{") - line.count("}")
        if depth == 0 and wrapper_close_index is None:
            wrapper_close_index = offset
            break
    if not nested_member or wrapper_close_index is None:
        return False

    # Keep the wrapper contents and remove its annotation/declaration.  If a
    # separate class closing brace follows, remove the wrapper's own closing
    # brace as well; older system-generated wrappers reused the class brace.
    remove_end = wrapper_index + 2
    trailing_lines = lines[wrapper_close_index + 1 :]
    if any(line.strip() for line in trailing_lines):
        del lines[wrapper_close_index]
    del lines[wrapper_index:remove_end]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def repair_orphaned_java_test_statements(path: Path) -> bool:
    """Wrap executable statements accidentally emitted outside an E2E test method.

    LLM retries occasionally close the last ``@Test`` method before appending its
    remaining assertions, producing ``invalid method declaration`` compiler
    errors.  When the orphan block is unambiguous (class-level statements
    followed by one extra closing brace), repair it deterministically before
    asking the agent to retry.
    """
    if not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    class_seen = False
    depth = 0
    orphan_start: int | None = None
    orphan_end: int | None = None
    executable = re.compile(
        r"^(?:assertThat\b|assert\w*\b|[A-Z][\w<>?, ]+\s+\w+\s*=|"
        r"(?:response|request|test\w*)\s*\()"
    )
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not class_seen and re.search(r"\bclass\s+\w+", line):
            class_seen = True
        if class_seen and depth == 1 and executable.match(stripped):
            if orphan_start is None:
                orphan_start = index
        if orphan_start is not None and stripped == "}" and depth == 1:
            orphan_end = index
            break
        depth += line.count("{") - line.count("}")
    if orphan_start is None or orphan_end is None or orphan_end <= orphan_start:
        return False

    orphan = lines[orphan_start:orphan_end]
    indent = re.match(r"\s*", orphan[0]).group(0)
    body = [indent + "    " + item[len(indent):] if item.startswith(indent) else indent + "    " + item
            for item in orphan]
    wrapper = [indent + "@Test", indent + "void generatedOrphanFlowAssertions() {", *body, indent + "}"]
    lines = lines[:orphan_start] + wrapper + lines[orphan_end + 1:]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


_STATUS_ENUMS = {
    "200": "OK",
    "201": "CREATED",
    "202": "ACCEPTED",
    "204": "NO_CONTENT",
    "400": "BAD_REQUEST",
    "401": "UNAUTHORIZED",
    "403": "FORBIDDEN",
    "404": "NOT_FOUND",
    "409": "CONFLICT",
    "422": "UNPROCESSABLE_ENTITY",
    "500": "INTERNAL_SERVER_ERROR",
}


def _java_test_method_bodies(source: str) -> list[str]:
    """Return complete Java ``@Test`` bodies without assuming their formatting."""
    declaration = re.compile(
        r"(?ms)@Test(?:\s*\([^)]*\))?\s*"
        r"(?:(?:public|protected|private)\s+)?void\s+"
        r"[A-Za-z_$][\w$]*\s*\([^)]*\)\s*\{"
    )
    bodies: list[str] = []
    for match in declaration.finditer(source):
        start = match.end() - 1
        depth = 0
        for index in range(start, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    bodies.append(source[match.start() : index + 1])
                    break
    return bodies


def _http_path_evidence_pattern(path: str) -> re.Pattern[str]:
    """Match a literal path or Java string concatenation resolving to that path."""
    parts = re.split(r"(\{[^}]+\})", path)
    expression: list[str] = []
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            expression.append(
                r'"\s*\+\s*[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*'
                r'\s*\+\s*"'
            )
        else:
            expression.append(re.escape(part))
    return re.compile(
        r"(?<![A-Za-z0-9_/-])" + "".join(expression) + r"(?![A-Za-z0-9_/-])"
    )


def _status_assertion_pattern(status: object) -> re.Pattern[str]:
    value = str(status)
    alternatives = [re.escape(value)]
    symbolic_name = _STATUS_ENUMS.get(value)
    if symbolic_name:
        alternatives.append(rf"(?:HttpStatus\.)?{symbolic_name}")
    return re.compile(
        rf"(?im)^.*(?:assert|expect|status).*\b(?:{'|'.join(alternatives)})\b.*$"
    )


def _http_method_evidence(method: str, source: str) -> bool:
    verbs = {
        "GET": r"\b(?:getFor(?:Entity|Object)|get)\s*\(|HttpMethod\.GET",
        "POST": r"\bpostFor(?:Entity|Object|Location)\s*\(|HttpMethod\.POST",
        "PUT": r"\bput\s*\(|HttpMethod\.PUT",
        "PATCH": r"\bpatchForObject\s*\(|HttpMethod\.PATCH",
        "DELETE": r"\bdelete\s*\(|HttpMethod\.DELETE",
    }
    pattern = verbs.get(method.upper(), rf"HttpMethod\.{re.escape(method.upper())}")
    return bool(re.search(pattern, source))


def _scenario_contract_violations(source: str, scenarios: object) -> list[str]:
    """Verify method, resolved path, and status together for every E2E row."""
    if not isinstance(scenarios, list):
        return []
    bodies = _java_test_method_bodies(source)
    violations: list[str] = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        method = str(scenario.get("method", "")).upper()
        path = str(scenario.get("path", ""))
        status = scenario.get("status")
        if not method or not path or status is None:
            continue
        operation = f"{method} {path}"
        method_bodies = [
            body for body in bodies if _http_method_evidence(method, body)
        ]
        if not method_bodies:
            violations.append(f"Missing HTTP method evidence for scenario: {operation}")
            continue
        matching_bodies = [
            body for body in method_bodies if _http_path_evidence_pattern(path).search(body)
        ]
        if not matching_bodies:
            violations.append(f"Missing HTTP path evidence: {path}")
            continue
        if not any(_status_assertion_pattern(status).search(body) for body in matching_bodies):
            violations.append(
                f"Missing asserted HTTP status for scenario {operation}: {status}"
            )
    return violations


def e2e_contract_violations(
    path: Path, contract: dict[str, object] | None = None
) -> list[str]:
    """Return semantic defects that make the generated E2E test non-evidentiary."""
    if not path.is_file():
        return ["E2E test file is missing"]

    source = path.read_text(encoding="utf-8")
    violations: list[str] = []
    required_groups: dict[str, tuple[str, ...]] = {
        "real HTTP client": ("TestRestTemplate", "MockMvc"),
    }
    if contract is not None:
        repositories = tuple(
            str(repository)
            for repository in contract.get("repositories", [])
            if str(repository).strip()
        )
        if repositories:
            # The planner lists all generated repositories, but one E2E test
            # cannot truthfully exercise every persistence aggregate. Require
            # at least one concrete repository evidence and let scenario-level
            # HTTP assertions cover the remaining aggregates.
            required_groups["repository evidence"] = repositories
        for gateway in contract.get("gatewayAdapters", []):
            required_groups[f"concrete gateway {gateway}"] = (str(gateway),)
    else:
        # Compatibility for runs planned before semantic contracts were added.
        required_groups.update({
            "concrete deterministic trading gateway": ("InMemoryTradingSiteGatewayAdapter", "GatewayAdapter", "Gateway"),
            "purchase persistence evidence": ("PurchaseRecordRepository", "Repository"),
        })
    for label, alternatives in required_groups.items():
        if not any(token in source for token in alternatives):
            violations.append(f"Missing {label}: {' or '.join(alternatives)}")

    if contract and contract.get("scenarios"):
        violations.extend(_scenario_contract_violations(source, contract["scenarios"]))
    elif contract:
        for expected_path in contract.get("paths", []):
            pattern = re.escape(str(expected_path))
            pattern = re.sub(r"\\\{[^}]+\\\}", r'[^"\\s]+', pattern)
            if not re.search(pattern, source):
                violations.append(f"Missing HTTP path evidence: {expected_path}")
        for expected_status in contract.get("statuses", []):
            status = str(expected_status)
            # Spring tests commonly assert the symbolic HttpStatus enum rather
            # than the numeric wire value (e.g. HttpStatus.OK for 200).  Both
            # forms are equivalent evidence and must satisfy the contract.
            status_names = {
                "200": "OK",
                "201": "CREATED",
                "202": "ACCEPTED",
                "204": "NO_CONTENT",
                "400": "BAD_REQUEST",
                "401": "UNAUTHORIZED",
                "403": "FORBIDDEN",
                "404": "NOT_FOUND",
                "409": "CONFLICT",
                "422": "UNPROCESSABLE_ENTITY",
                "500": "INTERNAL_SERVER_ERROR",
            }
            alternatives = [re.escape(status)]
            symbolic_name = status_names.get(status)
            if symbolic_name:
                alternatives.append(rf"(?:HttpStatus\.)?{symbolic_name}")
            assertion = re.compile(
                rf"(?im)^.*(?:assert|expect|status).*\b(?:{'|'.join(alternatives)})\b.*$"
            )
            if not assertion.search(source):
                violations.append(f"Missing asserted HTTP status: {status}")

    test_count = len(re.findall(r"(?m)^\s*@Test\b", source))
    minimum_tests = int(contract.get("minimumTests", 1)) if contract else 4
    if test_count < minimum_tests:
        violations.append(
            f"Expected at least {minimum_tests} independent E2E scenarios, found {test_count}"
        )

    forbidden = {
        "test bean configuration": ("@TestConfiguration", "@MockBean", "@MockitoBean"),
        "reflection-based gateway access": ("java.lang.reflect", ".getMethod("),
        "weak dual-outcome assertion": (
            "accept both",
            "may be null",
            "no strict assertion",
            "simplified integration test",
        ),
        "disabled test": ("@Disabled",),
    }
    lowered = source.lower()
    for label, tokens in forbidden.items():
        found = [token for token in tokens if token.lower() in lowered]
        if found:
            violations.append(f"Forbidden {label}: {', '.join(found)}")
    return violations
