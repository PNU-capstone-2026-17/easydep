"""유스케이스별 작은 계획을 만들고 고정 OpenAPI로 실행한다."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from app.config import settings
from app.llm_connection import build_llm_connection
from app.llm_profiles import profile_for
from app.testing.schemas.functional_plan import (
    FunctionalInputValue,
    FunctionalTestCase,
    FunctionalTestPlan,
)
from app.testing.schemas.testing_state import TestingState
from app.testing.utils.functional_executor import (
    InputValueRequest,
    UpstreamAmbiguity,
    execute_functional_plan,
    operation_for_id,
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
        raise UpstreamAmbiguity("TestingContracts.requirements content must be a list.")
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
        raise UpstreamAmbiguity("The frozen OpenAPI document has no paths.")
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
            f"No OpenAPI operation trace is linked to use case {use_case_id}."
        )
    return result


def build_functional_cases(requirements: Any, use_cases: Any, openapi: Any) -> list[dict[str, Any]]:
    """canonical requirement/spec/OpenAPI 직접 연결만 LLM 입력으로 만든다."""
    specs = use_cases.get("use_case_specs") if isinstance(use_cases, dict) else None
    if not isinstance(specs, list):
        raise UpstreamAmbiguity("TestingContracts.use_cases has no use_case_specs list.")
    traceability = use_cases.get("traceability") if isinstance(use_cases, dict) else None
    requirement_trace = traceability.get("requirements") if isinstance(traceability, dict) else {}
    if not isinstance(requirement_trace, dict):
        raise UpstreamAmbiguity("TestingContracts.use_cases has an invalid requirement trace.")
    requirement_ids = _functional_requirements(requirements, requirement_trace)
    if not requirement_ids:
        return []
    if not isinstance(openapi, dict):
        raise UpstreamAmbiguity("TestingContracts.openapi content must be an object.")
    cases: list[dict[str, Any]] = []
    covered: set[str] = set()
    for spec in specs:
        if not isinstance(spec, dict):
            raise UpstreamAmbiguity("Each use_case_specs item must be an object.")
        use_case_id = str(spec.get("use_case_id") or "").strip()
        linked = spec.get("requirement_ids")
        if (
            not use_case_id
            or not isinstance(linked, list)
            or not all(isinstance(item, str) and item.strip() for item in linked)
        ):
            raise UpstreamAmbiguity(
                "A use_case_specs item has an empty use_case_id or requirement_ids list."
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
            raise UpstreamAmbiguity(
                f"Duplicate requirement_ids were found for use case {use_case_id}."
            )
        operation_ids = _operation_ids(openapi, use_case_id)
        cases.append(
            {
                "case_id": use_case_id,
                "requirement_ids": selected,
                "use_case_id": use_case_id,
                # LLM은 정상 흐름에 필요한 API의 선택과 순서만 정한다. 실패 분기와
                # OpenAPI field는 실행기가 이미 처리하므로 같은 정보를 다시 보내지 않는다.
                "use_case_flow": {
                    key: spec[key]
                    for key in ("name", "preconditions", "trigger", "main_scenario")
                    if key in spec
                },
                "allowed_operation_ids": operation_ids,
            }
        )
        covered.update(selected)
    missing = sorted(requirement_ids - covered)
    if missing:
        raise UpstreamAmbiguity(
            "Functional requirements have no use-case or OpenAPI trace: " + ", ".join(missing)
        )
    if not cases:
        raise UpstreamAmbiguity("No executable requirement-to-use-case-to-OpenAPI trace exists.")
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
        + "\n\nEnglish happy-path flow:\n"
        + json.dumps(candidate["use_case_flow"], ensure_ascii=False)
        + "\n\nAllowed operationId values:\n"
        + json.dumps(candidate["allowed_operation_ids"], ensure_ascii=False)
    )


def _validate(case: FunctionalTestCase, candidate: dict[str, Any]) -> None:
    if (
        case.case_id != candidate["case_id"]
        or case.use_case_id != candidate["use_case_id"]
        or case.requirement_ids != candidate["requirement_ids"]
    ):
        raise ValueError("Generated plan header does not match the frozen test case.")
    allowed = set(candidate["allowed_operation_ids"])
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
    connection = build_llm_connection()
    model = connection.model
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
    if extra_body := profile.extra_body(connection.provider):
        request["extra_body"] = extra_body
    response = client.chat.completions.create(**request)
    case = _without_repeated_operations(
        FunctionalTestCase.model_validate_json(
            (response.choices[0].message.content if response.choices else "") or ""
        )
    )
    _validate(case, candidate)
    return case


def _input_prompt(request: InputValueRequest) -> str:
    """정상 흐름용 값 하나를 묻되 다른 HTTP 입력은 보내지 않는다."""

    return (
        "Suggest one plausible English success-path input value for this OpenAPI leaf. "
        "Return only the JSON object required by the response schema. Do not invent or "
        "return any other request field.\n"
        + json.dumps(
            {
                "operationId": request.operation_id,
                "operationContext": request.operation_context,
                "location": request.location,
                "schema": request.schema,
            },
            ensure_ascii=False,
        )
    )


def _propose_input(client: OpenAI, request: InputValueRequest) -> Any:
    """공통 LLM 설정으로 OpenAPI 근거가 없는 leaf 값 하나만 제안받는다."""

    connection = build_llm_connection()
    model = connection.model
    profile = profile_for(
        model,
        fallback_temperature=settings.temperature,
        fallback_max_tokens=settings.llm_max_completion_tokens or 16384,
    )
    response_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"value": request.schema},
        "required": ["value"],
    }
    llm_request: dict[str, Any] = {
        "model": model,
        # 실행용 예시값은 다양성보다 재현성이 중요하지만 0도는 쓰지 않는다.
        "temperature": max(0.2, profile.temperature),
        "messages": [{"role": "user", "content": _input_prompt(request)}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "FunctionalInputValue",
                "strict": False,
                "schema": response_schema,
            },
        },
        # 값 하나만 받으므로 전체 계획과 같은 큰 출력 한도는 필요하지 않다.
        "max_tokens": min(1024, profile.completion_limit(settings.llm_max_completion_tokens)),
    }
    if profile.top_p is not None:
        llm_request["top_p"] = profile.top_p
    if reasoning_effort := profile.resolve_reasoning():
        llm_request["reasoning_effort"] = reasoning_effort
    if extra_body := profile.extra_body(connection.provider):
        llm_request["extra_body"] = extra_body
    response = client.chat.completions.create(**llm_request)
    content = (response.choices[0].message.content if response.choices else "") or ""
    parsed = json.loads(content)
    if not isinstance(parsed, dict) or "value" not in parsed:
        raise ValueError("The input suggestion response has no value field.")
    return parsed["value"]


def _preserved(
    value: Any,
    candidates: list[dict[str, Any]],
) -> tuple[FunctionalTestPlan, dict[str, list[FunctionalInputValue]]]:
    """이전 계획과 당시 사용한 leaf 입력을 함께 복원한다."""

    if not isinstance(value, dict):
        raise TypeError("Preserved functional test plan must be an object.")
    plan_value = dict(value)
    raw_inputs = plan_value.pop("inputValues", {})
    plan = FunctionalTestPlan.model_validate(plan_value)
    expected = {candidate["case_id"]: candidate for candidate in candidates}
    if set(expected) != {case.case_id for case in plan.cases}:
        raise ValueError("Preserved plan cases do not match the frozen trace scope.")
    plan = plan.model_copy(
        update={"cases": [_without_repeated_operations(case) for case in plan.cases]}
    )
    for case in plan.cases:
        _validate(case, expected[case.case_id])
    if not isinstance(raw_inputs, dict):
        raise TypeError("Preserved functional inputValues must be an object.")
    input_values: dict[str, list[FunctionalInputValue]] = {}
    for case_id, items in raw_inputs.items():
        if case_id not in expected or not isinstance(items, list):
            raise ValueError("Preserved functional inputValues do not match the plan cases.")
        parsed = [FunctionalInputValue.model_validate(item) for item in items]
        case = next(item for item in plan.cases if item.case_id == case_id)
        operation_ids = {step.operation_id for step in case.steps}
        if any(item.operation_id not in operation_ids for item in parsed):
            raise ValueError(
                f"Preserved functional inputValues include an operation outside {case_id}."
            )
        keys = [(item.operation_id, item.location) for item in parsed]
        if len(keys) != len(set(keys)):
            raise ValueError(f"Preserved functional inputValues are duplicated: {case_id}")
        input_values[str(case_id)] = parsed
    return plan, input_values


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
    scope = state.get("gate_scope")
    if scope is not None and "dynamicFunctional" not in scope:
        previous = (state.get("previous_reports") or {}).get("dynamicFunctional")
        report = deepcopy(previous) if isinstance(previous, dict) else {}
        if not report:
            report = _report(
                "UNAVAILABLE",
                "INCONCLUSIVE",
                "A reusable dynamic test report is unavailable.",
                "ENVIRONMENT_DEFECT",
            )
        else:
            report["reused"] = True
            previous_job_id = str(state.get("previous_job_id") or "")
            if previous_job_id:
                report["reusedFromJobId"] = previous_job_id
        return {
            "current_node": "dynamic_functional",
            "dynamic_functional_report": report,
        }
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
        client: OpenAI | None = None
        preserved_inputs: dict[str, list[FunctionalInputValue]] = {}
        if state.get("fixed_test_plan") is not None:
            plan, preserved_inputs = _preserved(state["fixed_test_plan"], candidates)
        else:
            connection = build_llm_connection()
            if not connection.api_key:
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
                api_key=connection.api_key,
                base_url=connection.base_url,
                default_headers=connection.default_headers(),
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
    except (ValidationError, TypeError, ValueError, json.JSONDecodeError) as error:
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
    plan_value = plan.model_dump(mode="json")
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
    input_values: dict[str, list[dict[str, Any]]] = {}

    def propose(request: InputValueRequest) -> Any:
        """처음 필요한 시점에만 client를 만들고 leaf 하나를 제안받는다."""

        nonlocal client
        if client is None:
            connection = build_llm_connection()
            if not connection.api_key:
                raise UpstreamAmbiguity(
                    "API key is not configured for an ambiguous functional test input."
                )
            client = OpenAI(
                api_key=connection.api_key,
                base_url=connection.base_url,
                default_headers=connection.default_headers(),
                max_retries=0,
                timeout=settings.llm_timeout_seconds,
            )
        return _propose_input(client, request)

    for case in plan.cases:
        previous = previous_results.get(case.case_id)
        if previous is not None and previous.get("plan") == case.model_dump(mode="json"):
            results.append(dict(previous))
            reused_case_ids.append(case.case_id)
            reused_inputs = (previous.get("result") or {}).get("inputValues")
            if isinstance(reused_inputs, list) and reused_inputs:
                input_values[case.case_id] = [
                    dict(item) for item in reused_inputs if isinstance(item, dict)
                ]
            continue

        try:
            result = execute_functional_plan(
                case,
                openapi=frozen["openapi"],
                target_url=target_url,
                propose_input=propose,
                preserved_inputs=preserved_inputs.get(case.case_id),
            )
        except Exception as error:
            result = _report(
                "UNAVAILABLE",
                "INCONCLUSIVE",
                f"LLM functional input suggestion failed: {error}",
                "ENVIRONMENT_DEFECT",
            )
        proposed = result.get("inputValues")
        if isinstance(proposed, list) and proposed:
            input_values[case.case_id] = [
                dict(item) for item in proposed if isinstance(item, dict)
            ]
        results.append(_case_result(case, result))
        if first_failure is None and str(result.get("gateStatus") or "").upper() != "PASS":
            first_failure = result
            first_failure_case_id = case.case_id
    candidate_plan = dict(plan_value)
    if input_values:
        # 후보 계획에 값 제안을 함께 보관한다. 다음 수리는 이 값을 그대로 복원하므로
        # 비결정적인 LLM을 다시 호출해 테스트 조건이 바뀌는 일을 막는다.
        candidate_plan["inputValues"] = input_values
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
