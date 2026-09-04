"""유스케이스별 작은 계획을 만들고 고정 OpenAPI로 실행한다."""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from app.config import settings
from app.llm_profiles import profile_for
from app.testing.runtime.provider import (
    configured_api_key,
    configured_base_url,
    configured_headers,
    configured_model,
)
from app.testing.schemas.functional_plan import FunctionalTestCase, FunctionalTestPlan
from app.testing.schemas.testing_state import TestingState
from app.testing.utils.functional_executor import (
    UpstreamAmbiguity,
    execute_functional_plan,
    operation_for_id,
    operation_prompt_projection,
)
from app.validation import stable_digest

PLAN_SYSTEM_PROMPT = """Create one strict JSON FunctionalTestCase for this use case.
Keep case_id, requirement_ids, and use_case_id exactly as supplied. Choose only
listed operationId values in the needed order, at most once each. Every step has a unique short
step_id and operation_id. Do not add paths, methods, auth, status, values,
assertions, Python, or prose."""
_NON_FUNCTIONAL = frozenset(
    {"NFR", "NON_FUNCTIONAL", "NON-FUNCTIONAL", "NONFUNCTIONAL", "CONSTRAINT"}
)


def _report(status: str, gate_status: str, reason: str, defect_class: str) -> dict[str, Any]:
    return {
        "status": status,
        "gateStatus": gate_status,
        "reason": reason,
        "defectClass": defect_class,
        "defect": repair_route(defect_class),
    }


def repair_route(defect_class: str) -> dict[str, Any]:
    """실패 종류를 다음 수리 담당으로 연결한다."""
    route, preserve = {
        "TEST_DEFECT": ("testing", False),
        "SUT_DEFECT": ("implementation", True),
        "ENVIRONMENT_DEFECT": ("environment", True),
        "UPSTREAM_AMBIGUITY": ("requirements-or-design", True),
    }.get(defect_class, ("testing", False))
    return {
        "class": defect_class,
        "defectClass": defect_class,
        "route": route,
        "repairOwner": route,
        "preserveTests": preserve,
        "preserveCandidate": preserve,
    }


def classify_dynamic_failure(report: dict[str, Any]) -> dict[str, Any]:
    """executor 결과를 service가 쓰는 수리 routing으로 바꾼다."""
    routed = repair_route(str(report.get("defectClass") or "SUT_DEFECT"))
    routed["message"] = str(report.get("reason") or "Dynamic functional plan failed.")[-2000:]
    return routed


def _frozen(state: TestingState) -> dict[str, Any]:
    raw = state.get("testing_input") or {}
    contracts = raw.get("contract_artifacts") if isinstance(raw, dict) else None
    if not isinstance(contracts, dict):
        return {}
    return {
        name: item.get("content")
        for name in ("requirements", "use_cases", "openapi")
        if isinstance((item := contracts.get(name)), dict) and "content" in item
    }


def _functional_requirement_ids(value: Any) -> set[str]:
    """고정 요구사항에서 기능 요구사항 ID만 읽는다."""

    if not isinstance(value, list):
        raise UpstreamAmbiguity("TestingContracts.requirements content가 list가 아닙니다.")
    result: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        identifier = str(item.get("id") or "").strip()
        requirement_type = str(item.get("type") or "").strip().upper()
        if (
            identifier
            and not identifier.upper().startswith("NFR")
            and requirement_type not in _NON_FUNCTIONAL
        ):
            result.add(identifier)
    return result


def _functional_requirements(value: Any, requirement_trace: dict[str, Any]) -> set[str]:
    """고정 요구사항에서 유스케이스로 실행할 수 있는 기능 ID만 고른다.

    FR/NFR은 문장의 분류이고, 실제 유스케이스 동작인지 정책인지는 RTM의 관계가
    알려 준다. 따라서 적용 대상 유스케이스가 없는 전역 정책은 HTTP 흐름으로 억지로
    시험하지 않는다. 반대로 일반 기능 요구사항의 연결이 빠졌다면 아래 coverage
    검사에서 상류 명세 오류로 그대로 보고한다.
    """
    result = _functional_requirement_ids(value)
    for identifier in tuple(result):
        trace = requirement_trace.get(identifier)
        if isinstance(trace, dict) and trace.get("modeled_as_constraint") is True:
            linked_use_cases = [
                use_case_id
                for key in ("use_cases", "realized_by_use_cases", "constrains_use_cases")
                for use_case_id in trace.get(key) or []
                if isinstance(use_case_id, str) and use_case_id.strip()
            ]
            if not linked_use_cases:
                result.remove(identifier)
    return result


def _operation_ids(openapi: dict[str, Any], use_case_id: str) -> list[str]:
    paths = openapi.get("paths")
    if not isinstance(paths, dict):
        raise UpstreamAmbiguity("고정 OpenAPI paths가 비어 있습니다.")
    result: list[str] = []
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for operation in path_item.values():
            trace = operation.get("x-easydep-use-case-ids") if isinstance(operation, dict) else None
            operation_id = (
                str(operation.get("operationId") or "").strip()
                if isinstance(operation, dict)
                else ""
            )
            if (
                isinstance(trace, list)
                and use_case_id in trace
                and operation_id
                and operation_id not in result
            ):
                operation_for_id(openapi, operation_id, use_case_id=use_case_id)
                result.append(operation_id)
    if not result:
        raise UpstreamAmbiguity(
            f"유스케이스와 연결된 OpenAPI operation trace가 없습니다: {use_case_id}"
        )
    return result


def build_functional_cases(requirements: Any, use_cases: Any, openapi: Any) -> list[dict[str, Any]]:
    """canonical requirement/spec/OpenAPI 직접 연결만 LLM 입력으로 만든다."""
    specs = use_cases.get("use_case_specs") if isinstance(use_cases, dict) else None
    if not isinstance(specs, list):
        raise UpstreamAmbiguity("TestingContracts.use_cases에 use_case_specs가 없습니다.")
    traceability = use_cases.get("traceability") if isinstance(use_cases, dict) else None
    requirement_trace = traceability.get("requirements") if isinstance(traceability, dict) else {}
    if not isinstance(requirement_trace, dict):
        raise UpstreamAmbiguity("TestingContracts.use_cases의 requirement trace가 잘못되었습니다.")
    requirement_ids = _functional_requirements(requirements, requirement_trace)
    if not requirement_ids:
        return []
    if not isinstance(openapi, dict):
        raise UpstreamAmbiguity("TestingContracts.openapi content가 object가 아닙니다.")
    cases: list[dict[str, Any]] = []
    covered: set[str] = set()
    for spec in specs:
        if not isinstance(spec, dict):
            raise UpstreamAmbiguity("use_case_specs 항목이 object가 아닙니다.")
        use_case_id = str(spec.get("use_case_id") or "").strip()
        linked = spec.get("requirement_ids")
        if (
            not use_case_id
            or not isinstance(linked, list)
            or not all(isinstance(item, str) and item.strip() for item in linked)
        ):
            raise UpstreamAmbiguity(
                "use_case_specs의 use_case_id 또는 requirement_ids가 비어 있습니다."
            )
        selected = [item for item in linked if item in requirement_ids]
        # 시나리오 한 단계가 아니라 여러 유스케이스를 제한하는 동시성 같은 요구사항도
        # 저장된 RTM의 정확한 UC ID 연결을 따라 같은 실행 case에 포함한다.
        for requirement_id in sorted(requirement_ids - set(selected)):
            trace = requirement_trace.get(requirement_id)
            if not isinstance(trace, dict):
                continue
            traced_cases = [
                case_id
                for key in ("use_cases", "realized_by_use_cases", "constrains_use_cases")
                for case_id in trace.get(key) or []
                if isinstance(case_id, str)
            ]
            if use_case_id in traced_cases:
                selected.append(requirement_id)
        if not selected:
            continue
        if len(set(selected)) != len(selected):
            raise UpstreamAmbiguity(f"use-case requirement_ids가 중복됩니다: {use_case_id}")
        operation_ids = _operation_ids(openapi, use_case_id)
        cases.append(
            {
                "case_id": use_case_id,
                "requirement_ids": selected,
                "use_case_id": use_case_id,
                "use_case": spec,
                "operations": operation_prompt_projection(
                    openapi, operation_ids, use_case_id=use_case_id
                ),
            }
        )
        covered.update(selected)
    missing = sorted(requirement_ids - covered)
    if missing:
        raise UpstreamAmbiguity(
            "기능 requirement의 use-case 또는 OpenAPI trace가 없습니다: " + ", ".join(missing)
        )
    if not cases:
        raise UpstreamAmbiguity("실행할 requirement → use case → OpenAPI 연결이 없습니다.")
    return cases


def _response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "FunctionalTestCase",
            "strict": True,
            "schema": FunctionalTestCase.model_json_schema(),
        },
    }


def _prompt(candidate: dict[str, Any]) -> str:
    header = {key: candidate[key] for key in ("case_id", "requirement_ids", "use_case_id")}
    return (
        PLAN_SYSTEM_PROMPT
        + "\nRequired plan header:\n"
        + json.dumps(header, ensure_ascii=False)
        + "\n\nUse-case specification:\n"
        + json.dumps(candidate["use_case"], ensure_ascii=False)
        + "\n\nAllowed OpenAPI operation schemas:\n"
        + json.dumps(candidate["operations"], ensure_ascii=False)
    )


def _validate(case: FunctionalTestCase, candidate: dict[str, Any]) -> None:
    if (
        case.case_id != candidate["case_id"]
        or case.use_case_id != candidate["use_case_id"]
        or case.requirement_ids != candidate["requirement_ids"]
    ):
        raise ValueError("Generated plan header does not match the frozen test case.")
    allowed = {operation["operationId"] for operation in candidate["operations"]}
    if any(step.operation_id not in allowed for step in case.steps):
        raise ValueError("Generated plan selected an untraced operationId.")


def _without_repeated_operations(case: FunctionalTestCase) -> FunctionalTestCase:
    """같은 입력으로 같은 endpoint를 되풀이하는 의미 없는 단계를 한 번만 남긴다.

    현재 계획에는 호출별 입력값이나 다른 의도가 없다. 따라서 같은 operationId를 여러 번
    적어도 실행은 완전히 같으며 테스트 범위는 늘지 않는다. 입력 구분이 필요해지는 날에는
    먼저 계획 계약에 그 의미를 명시해야 한다.
    """

    seen: set[str] = set()
    steps = []
    for step in case.steps:
        if step.operation_id in seen:
            continue
        seen.add(step.operation_id)
        steps.append(step)
    return case if len(steps) == len(case.steps) else case.model_copy(update={"steps": steps})


def _generate(client: OpenAI, candidate: dict[str, Any]) -> FunctionalTestCase:
    model = configured_model()
    profile = profile_for(
        model,
        fallback_temperature=settings.temperature,
        fallback_max_tokens=settings.llm_max_completion_tokens or 16384,
    )
    request: dict[str, Any] = {
        "model": model,
        "temperature": profile.temperature,
        "messages": [{"role": "user", "content": _prompt(candidate)}],
        "response_format": _response_format(),
        "max_tokens": profile.completion_limit(settings.llm_max_completion_tokens),
    }
    if profile.top_p is not None:
        request["top_p"] = profile.top_p
    if reasoning_effort := profile.resolve_reasoning():
        request["reasoning_effort"] = reasoning_effort
    if extra_body := profile.extra_body():
        request["extra_body"] = extra_body
    response = client.chat.completions.create(**request)
    case = _without_repeated_operations(
        FunctionalTestCase.model_validate_json(
            (response.choices[0].message.content if response.choices else "") or ""
        )
    )
    _validate(case, candidate)
    return case


def _preserved(value: Any, candidates: list[dict[str, Any]]) -> FunctionalTestPlan:
    plan = FunctionalTestPlan.model_validate(value)
    expected = {candidate["case_id"]: candidate for candidate in candidates}
    if set(expected) != {case.case_id for case in plan.cases}:
        raise ValueError("Preserved plan cases do not match the frozen trace scope.")
    plan = plan.model_copy(
        update={"cases": [_without_repeated_operations(case) for case in plan.cases]}
    )
    for case in plan.cases:
        _validate(case, expected[case.case_id])
    return plan


def _case_result(case: FunctionalTestCase, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "caseId": case.case_id,
        "requirementIds": case.requirement_ids,
        "useCaseId": case.use_case_id,
        "plan": case.model_dump(mode="json"),
        "result": result,
    }


def _requirements(results: list[dict[str, Any]], frozen_requirements: Any) -> dict[str, Any]:
    """통과한 기능 요구사항과 아직 동작 증거가 없는 요구사항을 함께 표시한다."""

    outcomes: dict[str, list[bool]] = {}
    for item in results:
        passed = str((item["result"]).get("gateStatus") or "").upper() == "PASS"
        for requirement in item["requirementIds"]:
            outcomes.setdefault(requirement, []).append(passed)
    # 같은 요구사항을 여러 유스케이스가 실현하면 그중 하나만 성공했다고 검증된 것으로
    # 표시하지 않는다. 연결된 실행 결과가 모두 통과해야 이 HTTP 보고서의 증거가 된다.
    executed = sorted(
        requirement for requirement, statuses in outcomes.items() if statuses and all(statuses)
    )
    return {
        "source": "TestingInput",
        "artifact_type": "REFINE_REQ",
        "count": len(executed),
        "ids": executed,
        # 전역 정책처럼 유스케이스 HTTP 계획에 포함되지 않은 요구사항을 숨기지 않는다.
        # 이 목록은 실패를 뜻하지 않고, 이 보고서만으로는 동작을 입증하지 않았다는 뜻이다.
        "unverifiedIds": sorted(_functional_requirement_ids(frozen_requirements) - set(executed)),
    }


def dynamic_functional_node(state: TestingState) -> dict[str, Any]:
    """유스케이스마다 한 번 계획하고, 계획 묶음을 보존해 HTTP로 실행한다."""
    target_url = str(state.get("target_url") or "").strip()
    if not target_url:
        return {
            "current_node": "dynamic_functional",
            "dynamic_functional_report": {
                "status": "SKIPPED",
                "gateStatus": "NOT_APPLICABLE",
                "reason": "No running application was available to test against.",
            },
        }
    if not state.get("app_id"):
        return {
            "current_node": "dynamic_functional",
            "errors": [f"Missing app_id in state for run {state.get('run_id')}"],
            "dynamic_functional_report": {
                "status": "FAILED",
                "gateStatus": "FAIL",
                "reason": "Missing app_id",
            },
        }
    frozen = _frozen(state)
    missing = [name for name in ("requirements", "use_cases", "openapi") if name not in frozen]
    if missing:
        reason = "Frozen TestingInput contracts are unavailable: " + ", ".join(missing)
        return {
            "current_node": "dynamic_functional",
            "errors": [reason],
            "dynamic_functional_report": _report(
                "UNAVAILABLE", "INCONCLUSIVE", reason, "UPSTREAM_AMBIGUITY"
            ),
        }
    try:
        candidates = build_functional_cases(
            frozen["requirements"], frozen["use_cases"], frozen["openapi"]
        )
        if not candidates:
            return {
                "current_node": "dynamic_functional",
                "dynamic_functional_report": {
                    "status": "SKIPPED",
                    "gateStatus": "NOT_APPLICABLE",
                    "reason": "The frozen requirements contain no functional requirements.",
                },
            }
        if state.get("fixed_test_plan") is not None:
            plan = _preserved(state["fixed_test_plan"], candidates)
        else:
            api_key = configured_api_key()
            if not api_key:
                return {
                    "current_node": "dynamic_functional",
                    "dynamic_functional_report": _report(
                        "UNAVAILABLE",
                        "INCONCLUSIVE",
                        "API key not configured for functional test planning.",
                        "ENVIRONMENT_DEFECT",
                    ),
                }
            client = OpenAI(
                api_key=api_key,
                base_url=configured_base_url(),
                default_headers=configured_headers(),
                max_retries=0,
                timeout=settings.llm_timeout_seconds,
            )
            plan = FunctionalTestPlan(
                cases=[_generate(client, candidate) for candidate in candidates]
            )
    except UpstreamAmbiguity as error:
        return {
            "current_node": "dynamic_functional",
            "errors": [str(error)],
            "dynamic_functional_report": _report(
                "UNAVAILABLE", "INCONCLUSIVE", str(error), "UPSTREAM_AMBIGUITY"
            ),
        }
    except (ValidationError, ValueError, json.JSONDecodeError) as error:
        return {
            "current_node": "dynamic_functional",
            "errors": [str(error)],
            "dynamic_functional_report": _report(
                "FAILED", "FAIL", f"Functional test plan failed validation: {error}", "TEST_DEFECT"
            ),
        }
    except Exception as error:
        return {
            "current_node": "dynamic_functional",
            "errors": [str(error)],
            "dynamic_functional_report": _report(
                "UNAVAILABLE",
                "INCONCLUSIVE",
                f"LLM functional plan generation failed: {error}",
                "ENVIRONMENT_DEFECT",
            ),
        }
    candidate_plan = plan.model_dump(mode="json")
    previous_results = {
        str(item.get("caseId")): item
        for item in state.get("preserved_case_results") or []
        if isinstance(item, dict)
        and str((item.get("result") or {}).get("gateStatus") or "").upper() == "PASS"
    }
    results: list[dict[str, Any]] = []
    reused_case_ids: list[str] = []
    first_failure: dict[str, Any] | None = None
    first_failure_case_id = ""
    for case in plan.cases:
        previous = previous_results.get(case.case_id)
        if previous is not None and previous.get("plan") == case.model_dump(mode="json"):
            results.append(dict(previous))
            reused_case_ids.append(case.case_id)
            continue

        result = execute_functional_plan(
            case,
            openapi=frozen["openapi"],
            target_url=target_url,
        )
        results.append(_case_result(case, result))
        if first_failure is None and str(result.get("gateStatus") or "").upper() != "PASS":
            first_failure = result
            first_failure_case_id = case.case_id
    if first_failure is not None:
        report = {
            **first_failure,
            "candidatePlan": candidate_plan,
            "candidateDigest": stable_digest(candidate_plan),
            "caseId": first_failure_case_id,
            "cases": results,
            "reusedCaseIds": reused_case_ids,
            "requirements": _requirements(results, frozen["requirements"]),
            "targetUrl": target_url,
        }
        report["defect"] = classify_dynamic_failure(report)
        return {"current_node": "dynamic_functional", "dynamic_functional_report": report}
    return {
        "current_node": "dynamic_functional",
        "dynamic_functional_report": {
            "status": "passed",
            "gateStatus": "PASS",
            "candidatePlan": candidate_plan,
            "candidateDigest": stable_digest(candidate_plan),
            "cases": results,
            "reusedCaseIds": reused_case_ids,
            "requirements": _requirements(results, frozen["requirements"]),
            "targetUrl": target_url,
        },
    }


__all__ = [
    "FunctionalTestCase",
    "FunctionalTestPlan",
    "build_functional_cases",
    "classify_dynamic_failure",
    "dynamic_functional_node",
    "repair_route",
]
