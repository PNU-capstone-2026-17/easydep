"""Specification-bounded dynamic test generation and failure routing."""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

from app.testing.runtime.provider import configured_api_key, configured_model
from app.testing.runtime.provider import settings as provider_settings
from app.testing.schemas.testing_state import TestingState
from app.testing.utils.requirements_source import RequirementsUnavailable, functional_requirements
from app.testing.utils.test_runner import run_dynamic_test
from app.validation import RepairLedger, stable_digest

SYSTEM_PROMPT = """You are an expert QA automation engineer.
Write one small Python pytest script using synchronous Playwright and/or httpx.
The target application is available at TARGET_URL. Only test the supplied
requirement candidates and fixed OpenAPI operations. Every test must contain a
real assertion; never use pass as an assertion or silently skip a candidate.
Name each test after the requirement id, for example test_fr1_register.
If an endpoint requires HTTP Basic authentication, use the credentials from
EASYDEP_TEST_USERNAME and EASYDEP_TEST_PASSWORD. Do not embed production secrets.
Return ONLY Python code.

Requirement candidates:
{requirements_text}

Use-case context:
{use_cases_text}

OpenAPI operations (fixed path and method; do not invent endpoints):
{openapi_text}
"""

_TEST_ID = re.compile(r"(?i)(?:^|[_-])((?:fr|req|r)\d+)(?:[_-]|$)")
_PATH_LITERAL = re.compile(r"[\"'](?P<path>/[^\"']*)[\"']")
_METHOD_PATH = re.compile(r"\.(?P<method>get|post|put|patch|delete|head|options)\s*\(\s*[\"'](?P<path>/[^\"']*)[\"']", re.IGNORECASE)
_REFERENCE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9]){value}(?![A-Za-z0-9])", re.IGNORECASE
)
_REQUEST_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_REQUIREMENT_REFERENCE_KEYS = {
    "requirement",
    "requirements",
    "requirementid",
    "requirementids",
    "requirementref",
    "requirementrefs",
    "requirementreference",
    "requirementreferences",
}
_USE_CASE_REFERENCE_KEYS = {
    "usecase",
    "usecases",
    "usecaseid",
    "usecaseids",
    "usecaseref",
    "usecaserefs",
    "usecasereference",
    "usecasereferences",
    # API projection은 OpenAPI가 허용하는 vendor extension 이름으로 추적 정보를 보낸다.
    "xeasydepusecaseids",
}
_NON_FUNCTIONAL_TYPES = {
    "NFR",
    "NON_FUNCTIONAL",
    "NON-FUNCTIONAL",
    "NONFUNCTIONAL",
    "CONSTRAINT",
}


def _is_functional_requirement(item: Any) -> bool:
    """Exclude non-functional constraints from the executable test contract."""
    if not isinstance(item, dict):
        return False
    identifier = str(item.get("id") or "").strip().upper()
    kind = str(item.get("type") or item.get("category") or "").strip().upper()
    return not identifier.startswith("NFR") and kind not in _NON_FUNCTIONAL_TYPES and not kind.startswith("NFR")


def _requirement_items(value: Any) -> list[dict[str, Any]]:
    """Accept the small stored REFINE_REQ list without inventing requirements."""
    if isinstance(value, dict):
        for key in ("requirements", "refined_requirements", "refinedRequirements", "classified", "items"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                value = candidate
                break
    if not isinstance(value, list):
        return []
    return [
        item
        for item in value
        if _is_functional_requirement(item) and item.get("id") and item.get("text")
    ]


def _reference_values(value: Any) -> set[str]:
    """Flatten reference fields while retaining exact scalar values."""
    if isinstance(value, dict):
        values: set[str] = set()
        for nested in value.values():
            values.update(_reference_values(nested))
        return values
    if isinstance(value, (list, tuple, set)):
        values: set[str] = set()
        for nested in value:
            values.update(_reference_values(nested))
        return values
    if value in (None, ""):
        return set()
    return {str(value).strip().casefold()}


def _operation_references(operation: dict[str, Any]) -> tuple[set[str], set[str], set[str]]:
    """Read only explicit requirement/use-case trace fields and operationId."""
    requirements: set[str] = set()
    use_cases: set[str] = set()
    operation_ids: set[str] = set()

    def visit(value: Any, key: str = "") -> None:
        compact = re.sub(r"[^a-z0-9]", "", key.casefold())
        if compact in _REQUIREMENT_REFERENCE_KEYS:
            requirements.update(_reference_values(value))
        elif compact in _USE_CASE_REFERENCE_KEYS:
            use_cases.update(_reference_values(value))
        if isinstance(value, dict):
            for nested_key, nested in value.items():
                visit(nested, str(nested_key))
        elif isinstance(value, (list, tuple, set)):
            for nested in value:
                visit(nested, key)

    visit(operation)
    for key in ("operationId", "operation_id"):
        value = operation.get(key)
        if value not in (None, ""):
            operation_ids.add(str(value).strip().casefold())
    return requirements, use_cases, operation_ids


def _reference_matches(identifier: str, references: set[str]) -> bool:
    """Match a complete reference token (FR1 must not match FR10)."""
    wanted = str(identifier).strip().casefold()
    if not wanted:
        return False
    pattern = _REFERENCE_TOKEN.pattern.format(value=re.escape(wanted))
    return any(value == wanted or re.search(pattern, value) for value in references)


def _frozen_values(state: TestingState) -> tuple[dict[str, Any], bool]:
    """Read only serialized TestingInput contract content."""
    raw_value = state.get("testing_input")
    raw = raw_value or {}
    # A state carrying the new field is authoritative, even when one of its
    # frozen contracts is missing. Only pre-contract direct graph callers (where
    # the key is absent altogether) retain the old test fixture behaviour.
    has_input = isinstance(raw, dict) and "contract_artifacts" in raw
    if raw_value not in (None, {}) and not has_input:
        return {}, True
    contracts = raw.get("contract_artifacts") if isinstance(raw, dict) else {}
    if not has_input:
        return {}, False
    values: dict[str, Any] = {}
    aliases = {
        "requirements": ("requirements", "refined_requirements", "refinedRequirements"),
        "use_cases": ("use_cases", "useCases", "use_case_specs", "useCaseSpecs"),
        "openapi": ("openapi", "api_spec", "apiSpec"),
        "deployment": ("deployment", "deploymentBundle", "deployment_bundle"),
    }
    for target, names in aliases.items():
        item = contracts.get(target) if isinstance(contracts, dict) else None
        if isinstance(item, dict):
            content = item.get("content")
            if content is not None:
                values[target] = content
        elif item is not None:
            values[target] = item
        for name in names:
            item = contracts.get(name) if isinstance(contracts, dict) else None
            if isinstance(item, dict) and item.get("content") is not None:
                values[target] = item["content"]
    return values, True


def _test_requirement_ids(code: str, known_ids: set[str] | list[str] | None = None) -> set[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    ids: set[str] = set()
    known = {str(value).strip() for value in known_ids or () if str(value).strip()}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
            continue
        ids.update(match.group(1).upper() for match in _TEST_ID.finditer(node.name))
        for decorator in node.decorator_list:
            text = ast.unparse(decorator) if hasattr(ast, "unparse") else ""
            match = re.search(
                r"requirement\s*\(\s*[\"']([^\"']+)", text, re.IGNORECASE
            )
            if match:
                ids.add(match.group(1).upper())
        for identifier in known:
            if _reference_matches(identifier, {node.name, *(
                ast.unparse(decorator) if hasattr(ast, "unparse") else ""
                for decorator in node.decorator_list
            )}):
                ids.add(identifier.upper())
    return ids


def _openapi_operations(openapi: dict[str, Any] | None) -> dict[str, set[str]]:
    operations: dict[str, set[str]] = {}
    for path, item in (openapi or {}).get("paths", {}).items():
        if not isinstance(path, str) or not isinstance(item, dict):
            continue
        methods = {
            str(method).upper()
            for method, operation in item.items()
            if str(method).lower() in {"get", "post", "put", "patch", "delete", "head", "options"}
            and isinstance(operation, dict)
        }
        if methods:
            operations[path] = methods
    for endpoint in (openapi or {}).get("Endpoints") or (openapi or {}).get("endpoints") or []:
        if not isinstance(endpoint, dict):
            continue
        path = str(endpoint.get("path") or "")
        method = str(endpoint.get("method") or "get").lower()
        if path and method in _REQUEST_METHODS:
            operations.setdefault(path, set()).add(method.upper())
    return operations


def _openapi_operation_documents(openapi: dict[str, Any] | None) -> list[tuple[str, str, dict[str, Any]]]:
    """Return path/method/operation triples for trace-aware candidate narrowing."""
    documents: list[tuple[str, str, dict[str, Any]]] = []
    for path, item in (openapi or {}).get("paths", {}).items():
        if not isinstance(path, str) or not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if str(method).lower() in _REQUEST_METHODS and isinstance(operation, dict):
                documents.append((path, str(method).upper(), operation))
    # Some stored API artifacts are the canonical ApiSpecModel rather than the
    # rendered OpenAPI ``paths`` projection. Preserve its trace fields too.
    endpoints = (openapi or {}).get("Endpoints") or (openapi or {}).get("endpoints") or []
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        path = str(endpoint.get("path") or "")
        method = str(endpoint.get("method") or "get").lower()
        if path and method in _REQUEST_METHODS:
            documents.append((path, method.upper(), endpoint))
    return documents


def _path_matches(path: str, known: str) -> bool:
    pattern = re.sub(r"\{[^}/]+\}", r"[^/]+", known.rstrip("/") or "/")
    return bool(re.fullmatch(pattern, path.rstrip("/") or "/"))


def build_test_candidates(requirements: list[dict[str, Any]], use_cases: list[dict[str, Any]] | dict[str, Any] | None = None, openapi: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Narrow each requirement to deterministic context before LLM generation."""
    requirements = _requirement_items(requirements)
    operations = _openapi_operations(openapi)
    use_case_document = use_cases if isinstance(use_cases, dict) else {}
    if isinstance(use_cases, dict):
        use_cases = use_cases.get("use_cases") or use_cases.get("useCaseSpecs") or []
    use_cases = use_cases or []
    traceability = use_case_document.get("traceability") or {}
    requirement_traces = (
        traceability.get("requirements") if isinstance(traceability, dict) else {}
    ) or {}
    candidates: list[dict[str, Any]] = []
    for item in requirements:
        requirement_id = str(item["id"])
        trace = requirement_traces.get(requirement_id) or {}
        traced_case_ids = {
            str(value)
            for key in ("use_cases", "realized_by_use_cases", "constrains_use_cases")
            for value in trace.get(key) or []
        }
        # endpoint 하나로 검증할 수 없는 전역 제약은 이 단계의 후보가 아니다. 반면
        # 동시성처럼 특정 유스케이스를 제한하는 제약은 해당 endpoint 묶음으로 검사한다.
        if trace.get("modeled_as_constraint") is True and not traced_case_ids:
            continue
        requested = item.get("endpoint") or item.get("path") or item.get("apiPath")
        if requested:
            requested_path = str(requested)
            paths = [path for path in operations if _path_matches(requested_path, path) or _path_matches(path, requested_path)]
        else:
            wanted_case_ids = {
                str(value)
                for value in item.get("use_case_ids") or item.get("useCaseIds") or []
            }
            wanted_case_ids.update(traced_case_ids)
            wanted_case_ids.update(
                str(case.get("id"))
                for case in use_cases
                if isinstance(case, dict)
                and _reference_matches(
                    requirement_id,
                    {
                        str(value).casefold()
                        for key in ("requirement_ids", "requirementIds")
                        for value in case.get(key) or []
                    },
                )
            )
            paths = []
            for path, _method, operation in _openapi_operation_documents(openapi):
                operation_requirements, operation_cases, operation_ids = _operation_references(operation)
                if (
                    _reference_matches(requirement_id, operation_requirements | operation_ids)
                    or any(_reference_matches(case_id, operation_cases) for case_id in wanted_case_ids)
                ):
                    paths.append(path)
        wanted = {str(value) for value in item.get("use_case_ids") or item.get("useCaseIds") or []}
        wanted.update(traced_case_ids)
        wanted.update(
            str(case.get("id"))
            for case in use_cases
            if isinstance(case, dict)
            and _reference_matches(
                str(item["id"]),
                {
                    str(value).casefold()
                    for key in ("requirement_ids", "requirementIds")
                    for value in case.get(key) or []
                },
            )
        )
        related = [case for case in use_cases if isinstance(case, dict) and str(case.get("id")) in wanted]
        candidates.append({
            "requirementId": requirement_id,
            "text": str(item["text"]),
            "useCases": related,
            "allowedPaths": paths,
            "allowedMethods": {path: sorted(operations[path]) for path in paths},
            "ambiguity": not paths,
        })
    return candidates


def candidate_batches(candidates: list[dict[str, Any]], max_size: int = 3) -> list[list[dict[str, Any]]]:
    """Split candidates into small deterministic LLM requests."""
    size = max(1, min(int(max_size), 3))
    return [candidates[index : index + size] for index in range(0, len(candidates), size)]


def validate_test_candidate(code: str, *, openapi: dict[str, Any] | None = None, requirement_ids: set[str] | list[str] | None = None) -> dict[str, Any]:
    """Validate Python syntax, collection shape, assertions and API paths."""
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        return {"valid": False, "gateStatus": "FAIL", "defectClass": "TEST_DEFECT", "issues": [f"Generated test has a Python syntax error: {error}"], "requirementIds": [], "paths": []}
    tests = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")]
    issues: list[str] = []
    if not tests:
        issues.append("Generated test candidate contains no pytest test function.")
    ids = _test_requirement_ids(code, requirement_ids)
    wanted = {str(value).upper() for value in requirement_ids or ()}
    if wanted and ids.isdisjoint(wanted):
        issues.append("Generated tests do not identify any supplied requirement.")
    def has_assertion(test: ast.AST) -> bool:
        return any(
            isinstance(node, ast.Assert)
            or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and (
                    node.func.attr in {"assert_that", "to_be", "to_equal", "to_contain"}
                    or node.func.attr.startswith("to_")
                    or node.func.attr.startswith("assert")
                )
            )
            for node in ast.walk(test)
        )

    for test in tests:
        if not has_assertion(test):
            issues.append(f"Generated test {test.name} contains no executable assertion.")
    if tests and all(
        all(isinstance(statement, ast.Pass) for statement in test.body)
        or any("skip" in (ast.unparse(decorator) if hasattr(ast, "unparse") else "").lower() for decorator in test.decorator_list)
        for test in tests
    ):
        issues.append("Every generated test is pass-only or skipped.")
    elif any(
        any("skip" in (ast.unparse(decorator) if hasattr(ast, "unparse") else "").lower() for decorator in test.decorator_list)
        for test in tests
    ):
        issues.append("Generated tests must not silently skip a supplied requirement.")
    known = _openapi_operations(openapi)
    paths = [match.group("path").split("?", 1)[0] for match in _PATH_LITERAL.finditer(code)]
    unknown = [path for path in paths if known and not any(_path_matches(path, candidate) for candidate in known)]
    if unknown:
        issues.append("Generated test uses an OpenAPI path that does not exist: " + ", ".join(sorted(set(unknown))))
    wrong_methods = [
        f"{match.group('method').upper()} {match.group('path')}"
        for match in _METHOD_PATH.finditer(code)
        if known
        and not any(
            _path_matches(match.group("path"), path)
            and match.group("method").upper() in methods
            for path, methods in known.items()
        )
    ]
    if wrong_methods:
        issues.append("Generated test uses an OpenAPI method that does not exist: " + ", ".join(sorted(set(wrong_methods))))
    return {"valid": not issues, "gateStatus": "PASS" if not issues else "FAIL", "defectClass": None if not issues else "TEST_DEFECT", "issues": issues, "requirementIds": sorted(ids), "paths": sorted(set(paths)), "testsCollected": len(tests)}


def executed_requirement_ids(code: str, report: dict[str, Any] | None, requirement_ids: set[str] | list[str] | None = None) -> list[str]:
    """Map only non-skipped collected pytest nodes to requirement IDs."""
    report = report or {}
    tests = report.get("tests") or report.get("testResults") or []
    if isinstance(tests, list) and tests:
        ids: set[str] = set()
        for item in tests:
            if not isinstance(item, dict) or str(item.get("outcome") or item.get("status") or "").lower() in {"skipped", "skip", "xfailed"}:
                continue
            name = str(item.get("nodeid") or item.get("name") or "").split("::")[-1]
            ids.update(_test_requirement_ids(f"def {name}(): pass", requirement_ids))
        return sorted(ids)
    return sorted(_test_requirement_ids(code, requirement_ids)) if str(report.get("status") or "").lower() in {"passed", "pass"} else []


def classify_dynamic_failure(report: dict[str, Any], *, validation: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Classify failures and identify the repair owner without weakening tests."""
    if validation and not validation.get("valid"):
        defect = "TEST_DEFECT"
    else:
        text = " ".join(str(report.get(key) or "") for key in ("reason", "stdout", "stderr", "error")).lower()
        if any(token in text for token in ("docker", "network", "connection refused", "timed out", "timeout", "toolchain")):
            defect = "ENVIRONMENT_DEFECT"
        elif any(token in text for token in ("openapi", "specification", "ambiguous", "requirement mismatch")):
            defect = "UPSTREAM_AMBIGUITY"
        elif any(token in text for token in ("syntaxerror", "fixture", "no tests ran", "no test", "importerror", "collection")):
            defect = "TEST_DEFECT"
        else:
            defect = "SUT_DEFECT"
    route, preserve = {"TEST_DEFECT": ("testing", False), "SUT_DEFECT": ("implementation", True), "ENVIRONMENT_DEFECT": ("environment", True), "UPSTREAM_AMBIGUITY": ("requirements-or-design", True)}[defect]
    message = str(report.get("reason") or report.get("stderr") or "Dynamic test failed.")[-2000:]
    return {
        "class": defect,
        "defectClass": defect,
        "defect_class": defect,
        "route": route,
        "repairOwner": route,
        "repair_owner": route,
        "preserveTests": preserve,
        "preserve_tests": preserve,
        "preserveCandidate": preserve,
        "message": message,
    }


def repair_route(defect_class: str) -> dict[str, Any]:
    """Return the stable repair routing contract for a defect class."""
    values = {
        "TEST_DEFECT": ("testing", False),
        "SUT_DEFECT": ("implementation", True),
        "ENVIRONMENT_DEFECT": ("environment", True),
        "UPSTREAM_AMBIGUITY": ("requirements-or-design", True),
    }
    route, preserve = values.get(defect_class, ("testing", False))
    return {"class": defect_class, "defectClass": defect_class, "route": route, "repairOwner": route, "preserveTests": preserve, "preserveCandidate": preserve}


validate_generated_test = validate_test_candidate
classify_failure = classify_dynamic_failure


def _report(status: str, gate_status: str, reason: str = "", **extra: Any) -> dict[str, Any]:
    result = {"status": status, "gateStatus": gate_status, "reason": reason}
    result.update(extra)
    return result


def dynamic_functional_node(state: TestingState) -> dict[str, Any]:
    """Run the dynamic gate from fixed input content."""
    run_id = state.get("run_id")
    target_url = state.get("target_url") or ""
    if not target_url:
        return {"current_node": "dynamic_functional", "dynamic_functional_report": _report("SKIPPED", "NOT_APPLICABLE", "No running application was available to test against.")}
    frozen, has_snapshot = _frozen_values(state)
    app_id = state.get("app_id")
    if not app_id:
        return {"current_node": "dynamic_functional", "errors": [f"Missing app_id in state for run {run_id}"], "dynamic_functional_report": _report("FAILED", "FAIL", "Missing app_id")}
    if has_snapshot:
        requirements = frozen.get("requirements") or []
    else:
        # Legacy direct graph callers have no TestingInput. A real Testing job always
        # provides contract_artifacts and therefore never takes this path.
        try:
            requirements = functional_requirements(app_id)
        except RequirementsUnavailable as error:
            message = str(error)
            if "no stored requirements" in message.lower():
                return {"current_node": "dynamic_functional", "dynamic_functional_report": _report("SKIPPED", "NOT_APPLICABLE", message)}
            return {"current_node": "dynamic_functional", "dynamic_functional_report": _report("UNAVAILABLE", "INCONCLUSIVE", message, defectClass="ENVIRONMENT_DEFECT")}
        except Exception as error:
            return {"current_node": "dynamic_functional", "errors": [f"Failed to load requirements from DB for app {app_id}: {error}"], "dynamic_functional_report": _report("UNAVAILABLE", "INCONCLUSIVE", "DB error", defectClass="ENVIRONMENT_DEFECT")}
    requirements = _requirement_items(requirements)
    if has_snapshot and "requirements" not in frozen:
        return {"current_node": "dynamic_functional", "dynamic_functional_report": _report("UNAVAILABLE", "INCONCLUSIVE", "Frozen requirements content is unavailable; the dynamic gate cannot run.", defectClass="ENVIRONMENT_DEFECT")}
    if not requirements:
        return {"current_node": "dynamic_functional", "dynamic_functional_report": _report("SKIPPED", "NOT_APPLICABLE", "The stored requirements analysis contains no functional requirements.")}
    use_cases = frozen.get("use_cases") if has_snapshot else []
    openapi = frozen.get("openapi") if has_snapshot else {}
    candidates = build_test_candidates(requirements, use_cases, openapi)
    requirement_ids = [candidate["requirementId"] for candidate in candidates]
    ambiguous = [candidate["requirementId"] for candidate in candidates if candidate.get("ambiguity")]
    if has_snapshot and ambiguous:
        reason = "OpenAPI has no traceable operation for requirements: " + ", ".join(ambiguous)
        defect = repair_route("UPSTREAM_AMBIGUITY")
        return {"current_node": "dynamic_functional", "errors": [reason], "dynamic_functional_report": _report("UNAVAILABLE", "INCONCLUSIVE", reason, defect=defect, requirements={"source": "TestingInput", "count": 0, "ids": []})}
    batches = candidate_batches(candidates)
    raw_history = state.get("repair_history") or {}
    fixed_test_code = str(state.get("fixed_test_code") or "").strip()
    if fixed_test_code:
        # 제품 코드 수리의 성공 여부는 실패를 발견했던 바로 그 테스트로 확인한다. 이때는
        # NIM을 다시 호출하지 않으므로 통과 조건이 수리 과정에서 약해질 수 없다.
        test_code = fixed_test_code
    else:
        api_key = configured_api_key()
        if not api_key:
            report = _report("UNAVAILABLE", "INCONCLUSIVE", "API key not configured for test generation.", defectClass="ENVIRONMENT_DEFECT")
            return {"current_node": "dynamic_functional", "errors": [report["reason"]], "dynamic_functional_report": report}
        client = OpenAI(
            api_key=api_key,
            base_url=provider_settings.base_url,
            max_retries=0,
            timeout=provider_settings.llm_timeout_seconds,
        )
        try:
            generated: list[str] = []
            for index, batch in enumerate(batches):
                batch_paths = {path for candidate in batch for path in candidate.get("allowedPaths", [])}
                batch_operations = {
                    path: sorted(operations)
                    for path, operations in _openapi_operations(openapi).items()
                    if path in batch_paths
                }
                batch_cases: list[dict[str, Any]] = []
                seen_cases: set[str] = set()
                for candidate in batch:
                    for case in candidate.get("useCases", []):
                        identity = str(case.get("id") or stable_digest(case)) if isinstance(case, dict) else str(case)
                        if identity not in seen_cases:
                            seen_cases.add(identity)
                            batch_cases.append(case)
                batch_prompt = SYSTEM_PROMPT.format(requirements_text=json.dumps(batch, ensure_ascii=False, indent=2), use_cases_text=json.dumps(batch_cases, ensure_ascii=False, indent=2), openapi_text=json.dumps(batch_operations, ensure_ascii=False, indent=2))
                if index == 0 and raw_history and raw_history.get("attempts"):
                    batch_prompt += "\n\nPrevious generated-test repair attempts; do not repeat them.\n" + RepairLedger.model_validate(raw_history).prompt_context()
                response = client.chat.completions.create(model=configured_model("openai/gpt-4o"), temperature=0.2, messages=[{"role": "user", "content": batch_prompt}])
                generated.append((response.choices[0].message.content or "").replace("```python", "").replace("```", "").strip())
            test_code = "\n\n".join(generated)
        except Exception as error:
            report = _report("UNAVAILABLE", "INCONCLUSIVE", f"LLM generation failed: {error}", defectClass="ENVIRONMENT_DEFECT")
            return {"current_node": "dynamic_functional", "errors": [report["reason"]], "dynamic_functional_report": report}
    validation = validate_test_candidate(test_code, openapi=openapi, requirement_ids=requirement_ids)
    # Direct graph callers from before the TestingInput contract did not provide a
    # frozen OpenAPI/requirements snapshot. Keep their transport compatibility;
    # every service-created job has ``has_snapshot`` and takes the strict gate.
    if has_snapshot and not validation["valid"]:
        report = _report("FAILED", "FAIL", "Generated test candidate failed pre-execution validation.", validation=validation, defect=classify_dynamic_failure(validation, validation=validation), candidateDigest=stable_digest(test_code), candidateCode=test_code, requirements={"source": "TestingInput", "count": 0, "ids": []})
        return {"current_node": "dynamic_functional", "errors": validation["issues"], "dynamic_functional_report": report}
    repository_root = Path(state.get("application_dir") or os.getcwd())
    network_name = state.get("application_network")
    if network_name:
        report = dict(run_dynamic_test(test_code, target_url, repository_root, network_name=network_name))
    else:
        report = dict(run_dynamic_test(test_code, target_url, repository_root))
    report["candidateDigest"] = stable_digest(test_code)
    # Keep the exact accepted candidate so an SUT repair can rerun the same test.
    report["candidateCode"] = test_code
    report["targetUrl"] = target_url
    executed_report = report.get("report") if isinstance(report.get("report"), dict) and report.get("report") else report
    executed = executed_requirement_ids(test_code, executed_report, requirement_ids)
    report["requirements"] = {"source": "TestingInput" if has_snapshot else "db", "artifact_type": "REFINE_REQ", "count": len(executed), "ids": executed}
    report["validation"] = validation
    runner_gate = str(report.get("gateStatus") or "").upper()
    report["gateStatus"] = runner_gate if runner_gate in {"PASS", "FAIL", "INCONCLUSIVE", "NOT_APPLICABLE"} else "PASS" if str(report.get("status") or "").lower() in {"passed", "pass"} else "FAIL"
    expected = set(validation.get("requirementIds") or ())
    missing = sorted(expected - set(executed))
    if has_snapshot and missing and report["gateStatus"] == "PASS":
        report["status"] = "failed"
        report["gateStatus"] = "FAIL"
        report["reason"] = "Generated tests did not execute all candidate requirements: " + ", ".join(missing)
        report["defect"] = repair_route("TEST_DEFECT")
    if report["gateStatus"] != "PASS":
        report.setdefault("defect", classify_dynamic_failure(report, validation=validation))
    return {"current_node": "dynamic_functional", "dynamic_functional_report": report}
