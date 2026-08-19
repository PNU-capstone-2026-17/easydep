from __future__ import annotations

import re
from pathlib import Path


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
        r"^(?://|assertThat\b|assert\w*\b|[A-Z][\w<>?, ]+\s+\w+\s*=|"
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
        for repository in contract.get("repositories", []):
            required_groups[f"repository evidence {repository}"] = (str(repository),)
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

    if contract:
        for expected_path in contract.get("paths", []):
            pattern = re.escape(str(expected_path))
            pattern = re.sub(r"\\\{[^}]+\\\}", r'[^"\\s]+', pattern)
            if not re.search(pattern, source):
                violations.append(f"Missing HTTP path evidence: {expected_path}")
        for expected_status in contract.get("statuses", []):
            status = str(expected_status)
            assertion = re.compile(
                rf"(?im)^.*(?:assert|expect|status).*\b{re.escape(status)}\b.*$"
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
