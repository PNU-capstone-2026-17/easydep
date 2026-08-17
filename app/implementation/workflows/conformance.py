"""Deterministic post-test conformance checks for generated source contracts.

This module deliberately does not use an LLM.  The generated BCE/OpenAPI Java
files are an immutable implementation boundary, so their byte hashes are kept
from the moment the prototype is generated.  A small structural parser makes
the failure evidence useful to a developer without turning a heuristic into the
pass/fail decision.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..domain.implementation_ir import parse_components


SCHEMA_VERSION = "source-design-conformance/v1alpha1"
SNAPSHOT_FILE = "reports/generated-source-contracts.json"
REPORT_FILE = "reports/source-design-conformance.json"


def capture_generated_contracts(run_root: Path, base_package: str) -> dict[str, object]:
    """Persist the immutable Java contract baseline before an agent can edit it."""
    package_root = run_root / "application" / "src" / "main" / "java" / Path(
        base_package.replace(".", "/")
    )
    files: list[dict[str, object]] = []
    for area in ("bce", "api"):
        root = package_root / area
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.java")):
            content = path.read_text(encoding="utf-8")
            files.append({
                "path": path.relative_to(run_root).as_posix(),
                "sha256": _sha256(content),
                "content": content,
                "structure": _java_structure(content),
            })
    payload: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "basePackage": base_package,
        "files": files,
    }
    target = run_root / SNAPSHOT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def verify_source_design_conformance(run_root: Path, spec) -> dict[str, object]:
    """Verify immutable contracts and statically observable sequence calls.

    Call this only after Gradle's compile, unit, and E2E verification has
    succeeded.  The report is always written before a failure is raised.
    """
    snapshot_path = run_root / SNAPSHOT_FILE
    violations: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    checks: dict[str, object] = {"generatedContracts": [], "sequenceCalls": []}
    if not snapshot_path.is_file():
        warnings.append({
            "code": "MISSING_CONTRACT_BASELINE",
            "message": "This run predates generated source contract snapshots; immutable contract verification was skipped.",
        })
    else:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        for item in snapshot.get("files", []):
            relative = str(item["path"])
            path = run_root / relative
            check: dict[str, object] = {
                "path": relative,
                "status": "PASSED",
                "integrity": "PASSED",
                "contract": "PASSED",
            }
            if not path.is_file():
                check["status"] = "FAILED"
                check["integrity"] = "FAILED"
                check["contract"] = "FAILED"
                violations.append({"code": "GENERATED_CONTRACT_REMOVED", "path": relative,
                                   "message": "Generated BCE/OpenAPI contract file is missing."})
            else:
                current = path.read_text(encoding="utf-8")
                if _sha256(current) != item.get("sha256"):
                    check["status"] = "FAILED"
                    check["integrity"] = "FAILED"
                    changes = _structural_changes(
                        item.get("structure", {}), _java_structure(current)
                    )
                    check["changes"] = changes
                    violations.append({"code": "GENERATED_CONTRACT_MODIFIED", "path": relative,
                                   "message": "Generated BCE/OpenAPI contract differs from its pre-agent snapshot."})
                    if _has_structural_changes(changes):
                        check["contract"] = "FAILED"
                        violations.append({
                            "code": "GENERATED_CONTRACT_STRUCTURE_CHANGED",
                            "path": relative,
                            "message": (
                                "Generated skeleton class, field, or method signature "
                                "was added, modified, or deleted."
                            ),
                        })
            checks["generatedContracts"].append(check)

    sequence_path = spec.inputs.get("sequence")
    bce_path = spec.inputs.get("bceClass")
    if sequence_path and sequence_path.is_file() and bce_path and bce_path.is_file():
        components = {component.name for component in parse_components(bce_path.read_text(encoding="utf-8"))}
        invocations = _implementation_invocations(run_root, spec.base_package)
        sequence_source = sequence_path.read_text(encoding="utf-8")
        for diagram_id, document in _sequence_documents(sequence_source):
            aliases = _participant_aliases(document, components)
            expected_by_source: dict[str, list[dict[str, str]]] = {}
            for sequence_call in _sequence_calls(document):
                source, target, method = (
                    sequence_call["source"], sequence_call["target"], sequence_call["method"]
                )
                resolved_source = aliases.get(source, source)
                resolved_target = aliases.get(target, target)
                if resolved_source not in components or resolved_target not in components:
                    warnings.append({"code": "UNMAPPABLE_SEQUENCE_CALL",
                                     "diagram": diagram_id,
                                     "message": f"Cannot map sequence call {source} -> {target}: {method} to BCE components."})
                    continue
                matched = any(
                    item["method"] == method and resolved_target in item["dependencies"]
                    for item in invocations.get(resolved_source, [])
                )
                check = {"diagram": diagram_id, "from": resolved_source,
                         "to": resolved_target, "method": method,
                         "status": "PASSED" if matched else "FAILED"}
                checks["sequenceCalls"].append(check)
                if not matched:
                    violations.append({"code": "SEQUENCE_CALL_NOT_IMPLEMENTED",
                                       "path": "application/src/main/java",
                                       "message": f"Sequence call {resolved_source} -> {resolved_target}: {method}(...) has no matching source-to-target invocation."})
                    continue
                expected_by_source.setdefault(resolved_source, []).append({
                    "method": method,
                    "branch": sequence_call["branch"],
                })
                branch_tokens = _branch_tokens(sequence_call["branch"])
                if branch_tokens and not any(
                    any(token in item["source"].lower() for token in branch_tokens)
                    for item in invocations.get(resolved_source, [])
                ):
                    violations.append({"code": "SEQUENCE_BRANCH_NOT_IMPLEMENTED",
                                       "path": "application/src/main/java",
                                       "message": f"Sequence branch '{sequence_call['branch']}' for {method}(...) is not observable in {resolved_source}."})
            for source, expected in expected_by_source.items():
                ordered = any(
                    _calls_in_order(
                        item["calls"], [call["method"] for call in expected]
                    )
                    for item in invocations.get(source, [])
                )
                checks.setdefault("sequenceOrder", []).append({
                    "diagram": diagram_id,
                    "source": source,
                    "methods": [call["method"] for call in expected],
                    "status": "PASSED" if ordered else "FAILED",
                })
                if not ordered:
                    violations.append({"code": "SEQUENCE_CALL_ORDER_NOT_IMPLEMENTED",
                                       "path": "application/src/main/java",
                                       "message": f"Sequence call order for {source} in {diagram_id} is not preserved in one implementation class."})
    else:
        warnings.append({"code": "MISSING_SEQUENCE_INPUT", "message": "Sequence call verification was skipped because no sequence/BCE input is available."})

    report: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "FAILED" if violations else "PASSED",
        "verificationOrder": ["Gradle compileJava", "Gradle unit/E2E tests", "source design conformance"],
        "checks": checks,
        "violations": violations,
        "warnings": warnings,
    }
    target = run_root / REPORT_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if violations:
        raise SourceDesignConformanceError(report)
    return report


def restore_generated_contracts(run_root: Path) -> list[str]:
    """Restore changed generated contracts from their local pre-agent baseline."""
    snapshot_path = run_root / SNAPSHOT_FILE
    if not snapshot_path.is_file():
        return []
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    restored: list[str] = []
    for item in snapshot.get("files", []):
        relative = str(item.get("path", ""))
        content = item.get("content")
        if not relative or not isinstance(content, str):
            continue
        path = run_root / relative
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        if current != content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            restored.append(relative)
    return restored


class SourceDesignConformanceError(RuntimeError):
    def __init__(self, report: dict[str, object]):
        self.report = report
        super().__init__("Source/design conformance verification failed; see " + REPORT_FILE)


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _java_structure(source: str) -> dict[str, object]:
    clean = re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.DOTALL)
    types = {
        match.group("name"): match.group("kind")
        for match in re.finditer(
            r"(?m)^\s*(?:public\s+)?(?:abstract\s+|final\s+)?"
            r"(?P<kind>class|interface|enum|record)\s+(?P<name>\w+)", clean
        )
    }
    fields = {
        match.group("name"): _normalize_java_type(match.group("type"))
        for match in re.finditer(
            r"(?m)^\s*(?!(?:package|import)\b)(?:public|protected|private)?\s*(?:static\s+)?"
            r"(?:final\s+)?(?P<type>[A-Za-z_$][\w$<>,.?\[\] ]*)\s+"
            r"(?P<name>[A-Za-z_$]\w*)\s*(?:=[^;]*)?;\s*$",
            clean,
        )
    }
    # Normalize multiline signatures into continuous space for robust regex matching
    normalized_signatures = re.sub(r"\s*[\r\n]+\s*", " ", clean)
    methods: dict[str, str] = {}
    for match in re.finditer(
        r"(?:(?:public|protected|private|static|abstract|default|final|"
        r"synchronized|native)\s+)*(?P<return>[A-Za-z_$][\w$<>,.?\[\] ]*)\s+"
        r"(?P<name>[A-Za-z_$]\w*)\s*\((?P<params>[^)]*)\)\s*"
        r"(?:throws\s+(?P<throws>[^\{;]+))?[\{;]",
        normalized_signatures,
    ):
        parameters = _normalize_parameters(match.group("params"))
        throws = _normalize_java_type(match.group("throws") or "")
        key = f"{match.group('name')}({parameters})"
        methods[key] = _normalize_java_type(match.group("return")) + (f" throws {throws}" if throws else "")
    return {"types": types, "fields": fields, "methods": methods}


def _normalize_java_type(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _normalize_parameters(value: str) -> str:
    value = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", value)
    return _normalize_java_type(value)


def _structural_changes(before: object, after: dict[str, object]) -> dict[str, dict[str, list[str]]]:
    original = before if isinstance(before, dict) else {}
    changes: dict[str, dict[str, list[str]]] = {}
    for key in ("types", "fields", "methods"):
        baseline = original.get(key, {})
        current = after.get(key, {})
        # Compatibility with snapshots created by the first implementation of
        # this gate, which represented each item as a list of names.
        if not isinstance(baseline, dict):
            baseline = {str(item): "" for item in baseline}
        if not isinstance(current, dict):
            current = {str(item): "" for item in current}
        removed = sorted(set(baseline) - set(current))
        added = sorted(set(current) - set(baseline))
        modified = sorted(
            f"{name}: {baseline[name]} -> {current[name]}"
            for name in set(baseline) & set(current)
            if baseline[name] != current[name]
        )
        changes[key] = {"removed": removed, "added": added, "modified": modified}
    return changes


def _has_structural_changes(changes: dict[str, dict[str, list[str]]]) -> bool:
    return any(
        items
        for group in changes.values()
        for items in group.values()
    )


def _participant_aliases(sequence: str, components: set[str]) -> dict[str, str]:
    aliases = {name: name for name in components}
    pattern = re.compile(r"(?im)^\s*(?:participant|boundary|control|entity|database|collections?|actor)\s+(?:\"([^\"]+)\"|([A-Za-z_]\w*))\s+as\s+([A-Za-z_]\w*)")
    for match in pattern.finditer(sequence):
        display = match.group(1) or match.group(2) or ""
        alias = match.group(3)
        if display in components:
            aliases[alias] = display
        elif alias in components:
            aliases[display] = alias
    return aliases


def _sequence_documents(sequence: str) -> list[tuple[str, str]]:
    """여러 PlantUML 문서를 유스케이스별 독립 검증 단위로 분리한다."""
    matches = list(
        re.finditer(
            r"(?ims)^\s*@startuml(?:\s+([^\r\n]+))?.*?^\s*@enduml\s*$",
            sequence,
        )
    )
    if not matches:
        return [("sequence", sequence)]
    return [
        ((match.group(1) or f"sequence-{index}").strip(), match.group(0))
        for index, match in enumerate(matches, start=1)
    ]


def _sequence_calls(sequence: str) -> list[dict[str, str]]:
    calls: list[dict[str, str]] = []
    branches: list[str] = []
    pattern = re.compile(r"(?m)^\s*([A-Za-z_]\w*)\s*(?:-|--)+>\s*([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*)\s*\(")
    for line in sequence.splitlines():
        stripped = line.strip()
        if stripped.startswith("alt "):
            branches.append(stripped[4:].strip())
            continue
        if stripped.startswith("else ") and branches:
            branches[-1] = stripped[5:].strip()
            continue
        if stripped == "end" and branches:
            branches.pop()
            continue
        match = pattern.match(line)
        if match:
            calls.append({"source": match.group(1), "target": match.group(2),
                          "method": match.group(3), "branch": branches[-1] if branches else ""})
    return calls


def _branch_tokens(value: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z_0-9]{3,}", value)}


def _calls_in_order(actual: list[str], expected: list[str]) -> bool:
    cursor = 0
    for method in expected:
        try:
            cursor = actual.index(method, cursor) + 1
        except ValueError:
            return False
    return True


def _implementation_invocations(run_root: Path, base_package: str) -> dict[str, list[dict[str, object]]]:
    root = run_root / "application" / "src" / "main" / "java" / Path(base_package.replace(".", "/"))
    values: dict[str, list[dict[str, object]]] = {}
    for path in root.rglob("*.java") if root.is_dir() else []:
        relative = path.relative_to(root).as_posix()
        if relative.startswith("bce/") or relative.startswith("api/"):
            continue
        source = re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
        implemented = _implemented_interfaces(source)
        dependencies = set(re.findall(r"\b([A-Za-z_$]\w*)\s+[A-Za-z_$]\w*\s*[;,=)]", source))
        dependencies.update(_generic_dependency_types(source))
        method_calls = _method_call_sequences(source)
        for component in implemented:
            for calls in method_calls:
                values.setdefault(component, []).extend(
                    {
                        "method": method,
                        "dependencies": dependencies,
                        "calls": calls,
                        "source": source,
                    }
                    for method in set(calls)
                )
    return values


def _implemented_interfaces(source: str) -> set[str]:
    interfaces: set[str] = set()
    for match in re.finditer(r"\bimplements\s+([^\{]+)", source):
        for candidate in match.group(1).split(","):
            names = re.findall(r"[A-Za-z_$]\w*", re.sub(r"<.*>", "", candidate))
            if names:
                interfaces.add(names[-1])
    return interfaces


def _generic_dependency_types(source: str) -> set[str]:
    dependencies: set[str] = set()
    for match in re.finditer(
        r"\b[A-Za-z_$]\w*\s*<([^;=(){}]+)>\s+[A-Za-z_$]\w*\s*[;,=)]",
        source,
    ):
        dependencies.update(re.findall(r"\b[A-Z][A-Za-z_$0-9]*\b", match.group(1)))
    return dependencies


def _method_call_sequences(source: str) -> list[list[str]]:
    """Return qualified calls scoped to individual Java method bodies."""
    declaration = re.compile(
        r"(?:^|[;}])\s*(?:@[A-Za-z_$][\w.$]*(?:\([^)]*\))?\s*)*"
        r"(?:public|protected|private|static|final|synchronized|abstract|native|default|\s)+"
        r"[A-Za-z_$][\w.$<>?,\[\] ]*\s+[A-Za-z_$]\w*\s*\([^;{}]*\)\s*\{",
        re.MULTILINE,
    )
    sequences: list[list[str]] = []
    for match in declaration.finditer(source):
        opening = match.end() - 1
        depth = 0
        closing = None
        for index in range(opening, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        if closing is None:
            continue
        body = source[opening + 1:closing]
        calls = re.findall(r"\.\s*([A-Za-z_$]\w*)\s*\(", body)
        if calls:
            sequences.append(calls)
    return sequences
