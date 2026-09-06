"""Generate, execute, and preserve Arazzo functional workflows."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from openai import OpenAI

from app.config import settings
from app.llm_connection import build_llm_connection
from app.llm_profiles import profile_for
from app.llm_schema import remove_non_ascii_descriptions
from app.testing.progress import emit_testing_progress
from app.testing.schemas.arazzo import ArazzoValidationError, validate_arazzo_document
from app.testing.schemas.testing_state import TestingState
from app.testing.utils.arazzo_executor import execute_arazzo_workflow
from app.testing.utils.arazzo_planner import (
    ArazzoPlanningError,
    attach_workflow_trace,
    build_arazzo_document,
    build_workflow_candidates,
)
from app.testing.utils.functional_executor import InputValueRequest, UpstreamAmbiguity
from app.validation import stable_digest

PLAN_SYSTEM_PROMPT = """Return exactly one Arazzo v1.1 Workflow Object as JSON.
Use the supplied workflowId exactly. Use only listed operationId values, but treat traceHints as
ranking evidence rather than an allowlist. Express ordering, repeated calls, data flow, assertions,
retry, and cleanup only with standard Arazzo fields. Every OpenAPI parameter must include its
declared `in` value. Use application/json request bodies and JSON Pointer replacements only.
Use simple conditions or RFC 9535 JSONPath. Add successCriteria only when the frozen requirement
or use-case guarantee directly states the expected result; otherwise leave the workflow
contract-only. Do not invent operations, paths, methods, status codes, schemas, credentials,
external URLs, requirements, custom extensions, or implementation-derived expected values.
Do not return an Arazzo document envelope, Markdown, comments, or prose outside the JSON object."""


def _report(status: str, gate_status: str, reason: str, defect_class: str) -> dict[str, Any]:
    return {
        "status": status,
        "gateStatus": gate_status,
        "reason": reason,
        "defectClass": defect_class,
        "defect": repair_route(defect_class),
    }


def repair_route(defect_class: str) -> dict[str, Any]:
    """Map a failure class to the existing repair owner contract."""
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
    """Convert an executor result to the repair routing payload."""
    routed = repair_route(str(report.get("defectClass") or "SUT_DEFECT"))
    routed["message"] = str(report.get("reason") or "Dynamic functional workflow failed.")[-2000:]
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


def _response_format() -> dict[str, str]:
    """Use JSON mode; the official Arazzo schema remains the source of truth."""
    return {"type": "json_object"}


def _prompt(candidate: dict[str, Any], validation_error: str = "") -> str:
    correction = (
        "\nThe previous output failed validation. Correct only this reported issue:\n"
        + validation_error[-2000:]
        if validation_error
        else ""
    )
    return (
        PLAN_SYSTEM_PROMPT
        + correction
        + "\n\nFrozen authoring context:\n"
        + json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
    )


def _client() -> OpenAI:
    connection = build_llm_connection()
    if not connection.api_key:
        raise RuntimeError("API key is not configured for functional workflow planning.")
    return OpenAI(
        api_key=connection.api_key,
        base_url=connection.base_url,
        default_headers=connection.default_headers(),
        max_retries=0,
        timeout=settings.llm_timeout_seconds,
    )


def _generate(
    client: OpenAI,
    candidate: dict[str, Any],
    validation_error: str = "",
) -> dict[str, Any]:
    connection = build_llm_connection()
    profile = profile_for(
        connection.model,
        fallback_temperature=settings.temperature,
        fallback_max_tokens=settings.llm_max_completion_tokens or 16384,
    )
    request: dict[str, Any] = {
        "model": connection.model,
        "temperature": profile.temperature,
        "messages": [{"role": "user", "content": _prompt(candidate, validation_error)}],
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
    content = (response.choices[0].message.content if response.choices else "") or ""
    value = json.loads(content)
    if not isinstance(value, dict):
        raise TypeError("The workflow response must be one JSON object.")
    return attach_workflow_trace(value, candidate)


def _trace_catalog(candidates: list[dict[str, Any]]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {
        "requirementIds": set(),
        "useCaseIds": set(),
        "evidenceRefs": set(),
    }
    for candidate in candidates:
        trace = candidate.get("trace")
        if not isinstance(trace, dict):
            continue
        for key in result:
            result[key].update(
                item for item in trace.get(key) or [] if isinstance(item, str) and item
            )
    return result


def _validate_document(
    document: dict[str, Any],
    candidates: list[dict[str, Any]],
    openapi: dict[str, Any],
) -> dict[str, Any]:
    expected = [str(candidate["workflowId"]) for candidate in candidates]
    actual = [
        str(workflow.get("workflowId"))
        for workflow in document.get("workflows") or []
        if isinstance(workflow, dict)
    ]
    if actual != expected:
        raise ArazzoValidationError(
            "Generated workflow IDs or order do not match the frozen use-case scope."
        )
    frozen = validate_arazzo_document(
        document,
        openapi=openapi,
        trace_catalog=_trace_catalog(candidates),
    )
    for workflow, candidate in zip(frozen["workflows"], candidates, strict=True):
        expected_trace = attach_workflow_trace({"workflowId": candidate["workflowId"]}, candidate)[
            "x-easydep-trace"
        ]
        if workflow.get("x-easydep-trace") != expected_trace:
            raise ArazzoValidationError("Generated workflow trace does not match frozen evidence.")
    return frozen


def _generate_document(
    client: OpenAI,
    candidates: list[dict[str, Any]],
    openapi: dict[str, Any],
) -> dict[str, Any]:
    error = ""
    for attempt in range(2):
        try:
            workflows = [_generate(client, candidate, error) for candidate in candidates]
            return _validate_document(build_arazzo_document(workflows), candidates, openapi)
        except (ArazzoPlanningError, ArazzoValidationError, TypeError, ValueError) as exc:
            error = str(exc)
            if attempt:
                raise ValueError(f"Arazzo workflow generation failed validation: {error}") from exc
    raise AssertionError("The bounded workflow generation loop did not terminate.")


def _preserved(
    value: Any,
    candidates: list[dict[str, Any]],
    openapi: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("Preserved Arazzo candidatePlan must be an object.")
    return _validate_document(value, candidates, openapi)


def _input_prompt(request: InputValueRequest) -> str:
    return (
        "Suggest one plausible English success-path input value for this OpenAPI leaf. "
        "Return only the JSON object required by the response schema. Do not invent or return "
        "any other request field.\n"
        + json.dumps(
            {
                "operationId": request.operation_id,
                "operationContext": request.operation_context,
                "location": request.location,
                "schema": remove_non_ascii_descriptions(request.schema),
            },
            ensure_ascii=False,
        )
    )


def _propose_input(client: OpenAI, request: InputValueRequest) -> Any:
    connection = build_llm_connection()
    profile = profile_for(
        connection.model,
        fallback_temperature=settings.temperature,
        fallback_max_tokens=settings.llm_max_completion_tokens or 16384,
    )
    response_schema = remove_non_ascii_descriptions(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"value": request.schema},
            "required": ["value"],
        }
    )
    llm_request: dict[str, Any] = {
        "model": connection.model,
        "temperature": max(0.2, profile.temperature),
        "messages": [{"role": "user", "content": _input_prompt(request)}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "ArazzoInputValue",
                "strict": False,
                "schema": response_schema,
            },
        },
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


def _fixed_mapping(value: Any, expected: set[str], *, name: str) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(key not in expected for key in value):
        raise ValueError(f"Preserved {name} do not match the Arazzo workflows.")
    result: dict[str, dict[str, Any]] = {}
    for workflow_id, items in value.items():
        if not isinstance(items, dict):
            raise TypeError(f"Preserved {name} for {workflow_id} must be an object.")
        result[str(workflow_id)] = deepcopy(items)
    return result


def _fixed_input_values(value: Any, expected: set[str]) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(key not in expected for key in value):
        raise ValueError("Preserved input values do not match the Arazzo workflows.")
    result: dict[str, dict[str, Any]] = {}
    for workflow_id, items in value.items():
        if not isinstance(items, list):
            raise TypeError(f"Preserved input values for {workflow_id} must be an array.")
        values: dict[str, Any] = {}
        for item in items:
            if not isinstance(item, dict):
                raise TypeError(f"Preserved input value for {workflow_id} must be an object.")
            operation_id = item.get("operationId")
            location = item.get("location")
            if (
                not isinstance(operation_id, str)
                or not isinstance(location, str)
                or "value" not in item
            ):
                raise TypeError(
                    f"Preserved input value for {workflow_id} requires operationId and location."
                )
            key = f"{operation_id}|{location}"
            if key in values:
                raise ValueError(f"Preserved input value is duplicated for {workflow_id}: {key}")
            values[key] = deepcopy(item.get("value"))
        result[str(workflow_id)] = values
    return result


def _input_records(values: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for workflow_id, items in values.items():
        records = []
        for key, value in sorted(items.items()):
            operation_id, separator, location = key.partition("|")
            if not separator:
                raise ValueError(f"Preserved input key is invalid for {workflow_id}: {key}")
            records.append(
                {"operationId": operation_id, "location": location, "value": deepcopy(value)}
            )
        if records:
            result[workflow_id] = records
    return result


def _workflow_record(
    workflow: dict[str, Any],
    result: dict[str, Any],
    input_values: list[dict[str, Any]],
    workflow_inputs_by_id: dict[str, dict[str, Any]],
    input_values_by_id: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    trace = workflow.get("x-easydep-trace")
    return {
        "workflowId": workflow["workflowId"],
        "requirementIds": list((trace or {}).get("requirementIds") or []),
        "useCaseIds": list((trace or {}).get("useCaseIds") or []),
        "workflow": deepcopy(workflow),
        "inputValues": deepcopy(input_values),
        "workflowInputsById": deepcopy(workflow_inputs_by_id),
        "inputValuesById": deepcopy(input_values_by_id),
        "result": result,
    }


def _guaranteed_requirement_ids(candidate: dict[str, Any]) -> set[str]:
    use_case = candidate.get("useCase")
    guarantees = use_case.get("success_guarantee") if isinstance(use_case, dict) else None
    result: set[str] = set()
    for guarantee in guarantees or []:
        if not isinstance(guarantee, dict):
            continue
        result.update(
            item
            for item in guarantee.get("covered_req_ids") or []
            if isinstance(item, str) and item
        )
    return result


def _requirements(
    results: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    expected: dict[str, set[str]] = {}
    direct_evidence: dict[str, set[str]] = {}
    all_ids: set[str] = set()
    for candidate in candidates:
        workflow_id = str(candidate["workflowId"])
        trace = candidate.get("trace") or {}
        guaranteed = _guaranteed_requirement_ids(candidate)
        for requirement_id in trace.get("requirementIds") or []:
            requirement_id = str(requirement_id)
            all_ids.add(requirement_id)
            expected.setdefault(requirement_id, set()).add(workflow_id)
            if requirement_id in guaranteed:
                direct_evidence.setdefault(requirement_id, set()).add(workflow_id)
    contract_passed: set[str] = set()
    semantic_passed: set[str] = set()
    for item in results:
        workflow_id = str(item["workflowId"])
        result = item.get("result") or {}
        if (
            str(result.get("gateStatus") or "").upper() == "PASS"
            and str(result.get("contractStatus") or "").upper() == "PASS"
        ):
            contract_passed.add(workflow_id)
        if str(result.get("semanticStatus") or "").upper() == "PASS":
            semantic_passed.add(workflow_id)
    contract_ids = sorted(
        requirement_id
        for requirement_id, workflow_ids in expected.items()
        if workflow_ids and workflow_ids <= contract_passed
    )
    semantic_ids = sorted(
        requirement_id
        for requirement_id, workflow_ids in expected.items()
        if workflow_ids
        and workflow_ids <= semantic_passed
        and direct_evidence.get(requirement_id) == workflow_ids
    )
    return {
        "source": "TestingInput",
        "artifact_type": "REFINE_REQ",
        "count": len(semantic_ids),
        "ids": semantic_ids,
        "semanticStatus": "PASS" if semantic_ids else "NOT_EVALUATED",
        "contractCount": len(contract_ids),
        "contractIds": contract_ids,
        "unverifiedIds": sorted(all_ids - set(semantic_ids)),
    }


def _failed_step(result: dict[str, Any]) -> dict[str, Any]:
    for step in result.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if (
            step.get("finding")
            or step.get("status") == "failed"
            or step.get("semanticStatus") == "FAIL"
        ):
            return step
    return {}


def _failure_finding(workflow_id: str, result: dict[str, Any]) -> dict[str, Any]:
    step = _failed_step(result)
    failed_workflow_id = str(
        result.get("failedWorkflowId") or step.get("workflowId") or workflow_id
    )
    finding = dict(result.get("finding") or {})
    finding.update(
        {
            "workflowId": failed_workflow_id,
            "stepId": step.get("stepId"),
            "operationId": step.get("operationId"),
            "request": step.get("request"),
            "responseBody": step.get("responseBody"),
        }
    )
    step_finding = step.get("finding")
    if isinstance(step_finding, dict):
        finding.update({key: value for key, value in step_finding.items() if value is not None})
    return finding


def dynamic_functional_node(state: TestingState) -> dict[str, Any]:
    """Plan once, execute Arazzo workflows, and preserve exact inputs for repair."""
    scope = state.get("gate_scope")
    if scope is not None and "dynamicFunctional" not in scope:
        emit_testing_progress(
            phase="dynamic",
            scope="phase",
            status="REUSED",
            label="Reusing dynamic functional verification",
        )
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
            if previous_job_id := str(state.get("previous_job_id") or ""):
                report["reusedFromJobId"] = previous_job_id
        return {"current_node": "dynamic_functional", "dynamic_functional_report": report}

    target_url = str(state.get("target_url") or "").strip()
    if not target_url:
        emit_testing_progress(
            phase="dynamic",
            scope="phase",
            status="SKIPPED",
            label="Skipping dynamic functional verification",
            detail="No running application was available.",
        )
        return {
            "current_node": "dynamic_functional",
            "dynamic_functional_report": {
                "status": "SKIPPED",
                "gateStatus": "NOT_APPLICABLE",
                "reason": "No running application was available to test against.",
            },
        }
    if not state.get("app_id"):
        emit_testing_progress(
            phase="dynamic",
            scope="phase",
            status="FAIL",
            label="Dynamic functional verification failed",
            detail="The application ID is missing.",
        )
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
        emit_testing_progress(
            phase="dynamic",
            scope="phase",
            status="INCONCLUSIVE",
            label="Dynamic functional verification is unavailable",
            detail="Frozen contracts are unavailable.",
        )
        return {
            "current_node": "dynamic_functional",
            "errors": [reason],
            "dynamic_functional_report": _report(
                "UNAVAILABLE", "INCONCLUSIVE", reason, "UPSTREAM_AMBIGUITY"
            ),
        }

    emit_testing_progress(
        phase="dynamic",
        scope="phase",
        status="RUNNING",
        label="Preparing Arazzo functional workflows",
    )
    try:
        candidates = build_workflow_candidates(
            frozen["requirements"], frozen["use_cases"], frozen["openapi"]
        )
        if not candidates:
            emit_testing_progress(
                phase="dynamic",
                scope="phase",
                status="SKIPPED",
                label="No functional workflows are required",
            )
            return {
                "current_node": "dynamic_functional",
                "dynamic_functional_report": {
                    "status": "SKIPPED",
                    "gateStatus": "NOT_APPLICABLE",
                    "reason": "The frozen contracts contain no executable functional use cases.",
                },
            }
        if state.get("fixed_arazzo_document") is not None:
            document = _preserved(state["fixed_arazzo_document"], candidates, frozen["openapi"])
            client: OpenAI | None = None
            plan_source = "preserved"
        else:
            client = _client()
            document = _generate_document(client, candidates, frozen["openapi"])
            plan_source = "generated"
    except (ArazzoPlanningError, UpstreamAmbiguity) as error:
        emit_testing_progress(
            phase="dynamic",
            scope="phase",
            status="INCONCLUSIVE",
            label="Arazzo workflow planning is unavailable",
        )
        return {
            "current_node": "dynamic_functional",
            "errors": [str(error)],
            "dynamic_functional_report": _report(
                "UNAVAILABLE", "INCONCLUSIVE", str(error), "UPSTREAM_AMBIGUITY"
            ),
        }
    except (ArazzoValidationError, TypeError, ValueError, json.JSONDecodeError) as error:
        emit_testing_progress(
            phase="dynamic",
            scope="phase",
            status="FAIL",
            label="Arazzo workflow planning failed",
        )
        return {
            "current_node": "dynamic_functional",
            "errors": [str(error)],
            "dynamic_functional_report": _report(
                "FAILED", "FAIL", f"Arazzo test plan failed validation: {error}", "TEST_DEFECT"
            ),
        }
    except Exception as error:
        emit_testing_progress(
            phase="dynamic",
            scope="phase",
            status="INCONCLUSIVE",
            label="Arazzo workflow planning is unavailable",
        )
        return {
            "current_node": "dynamic_functional",
            "errors": [str(error)],
            "dynamic_functional_report": _report(
                "UNAVAILABLE",
                "INCONCLUSIVE",
                f"LLM functional workflow generation failed: {error}",
                "ENVIRONMENT_DEFECT",
            ),
        }

    workflow_ids = {str(workflow["workflowId"]) for workflow in document["workflows"]}
    emit_testing_progress(
        phase="dynamic",
        scope="phase",
        status="PASS",
        label="Arazzo workflows are ready",
        detail=f"Using {plan_source} workflow plan.",
        total_workflows=len(workflow_ids),
    )
    try:
        workflow_inputs = _fixed_mapping(
            state.get("fixed_workflow_inputs"), workflow_ids, name="workflow inputs"
        )
        input_values = _fixed_input_values(state.get("fixed_input_values"), workflow_ids)
    except (TypeError, ValueError) as error:
        emit_testing_progress(
            phase="dynamic",
            scope="phase",
            status="FAIL",
            label="Arazzo workflow inputs are invalid",
        )
        report = _report("FAILED", "FAIL", str(error), "TEST_DEFECT")
        return {
            "current_node": "dynamic_functional",
            "errors": [str(error)],
            "dynamic_functional_report": report,
        }

    previous_results = {
        str(item.get("workflowId")): item
        for item in state.get("preserved_workflow_results") or []
        if isinstance(item, dict)
        and str((item.get("result") or {}).get("gateStatus") or "").upper() == "PASS"
    }
    results: list[dict[str, Any]] = []
    reused_workflow_ids: list[str] = []
    first_failure: tuple[str, dict[str, Any]] | None = None
    pending_workflow_ids: list[str] = []
    priority_workflow_id = str(state.get("priority_workflow_id") or "").strip()
    execution_workflows = list(document["workflows"])
    if priority_workflow_id:
        execution_workflows.sort(
            key=lambda item: str(item.get("workflowId")) != priority_workflow_id
        )
    current_workflow_id = ""

    def propose(request: InputValueRequest) -> Any:
        nonlocal client
        input_workflow_id = (
            request.operation_context
            if request.operation_context in workflow_ids
            else current_workflow_id
        )
        key = f"{request.operation_id}|{request.location}"
        values = input_values.setdefault(input_workflow_id, {})
        if key in values:
            return deepcopy(values[key])
        if client is None:
            client = _client()
        value = _propose_input(client, request)
        values[key] = deepcopy(value)
        return value

    for index, workflow in enumerate(execution_workflows):
        workflow_id = str(workflow["workflowId"])
        current_workflow_id = workflow_id
        previous = previous_results.get(workflow_id)
        saved_workflow_input_map = (
            previous.get("workflowInputsById") if isinstance(previous, dict) else None
        )
        saved_input_value_map = (
            previous.get("inputValuesById") if isinstance(previous, dict) else None
        )
        saved_ids = (
            set(saved_workflow_input_map) if isinstance(saved_workflow_input_map, dict) else set()
        )
        saved_workflow_inputs = (
            _fixed_mapping(
                saved_workflow_input_map,
                saved_ids,
                name="workflow inputs",
            )
            if saved_ids
            else {}
        )
        saved_input_values = (
            _fixed_input_values(saved_input_value_map, saved_ids)
            if saved_ids and isinstance(saved_input_value_map, dict)
            else {}
        )
        current_workflow_inputs = {
            saved_id: workflow_inputs.get(saved_id, {}) for saved_id in saved_ids
        }
        current_input_values = {saved_id: input_values.get(saved_id, {}) for saved_id in saved_ids}
        reusable = (
            previous is not None
            and previous.get("workflow") == workflow
            and bool(saved_ids)
            and current_workflow_inputs == saved_workflow_inputs
            and current_input_values == saved_input_values
        )
        if reusable:
            assert isinstance(previous, dict)
            reused = deepcopy(previous)
            reused_result = reused.get("result")
            if isinstance(reused_result, dict):
                reused_result["reused"] = True
                if previous_job_id := str(state.get("previous_job_id") or ""):
                    reused_result["reusedFromJobId"] = previous_job_id
            for saved_id, saved_workflow_values in saved_workflow_inputs.items():
                workflow_inputs[saved_id] = deepcopy(saved_workflow_values)
            for saved_id, saved_values in saved_input_values.items():
                input_values[saved_id] = deepcopy(saved_values)
            results.append(reused)
            reused_workflow_ids.append(workflow_id)
            emit_testing_progress(
                phase="dynamic",
                scope="workflow",
                status="REUSED",
                label=f"Reusing workflow {workflow_id}",
                workflow_id=workflow_id,
                total_workflows=len(workflow_ids),
                total_steps=len(workflow.get("steps") or []),
            )
            continue
        try:
            result = execute_arazzo_workflow(
                document,
                workflow_id,
                openapi=frozen["openapi"],
                target_url=target_url,
                workflow_inputs=workflow_inputs.get(workflow_id),
                workflow_inputs_by_id=workflow_inputs,
                propose_input=propose,
            )
        except Exception as error:
            result = _report(
                "UNAVAILABLE",
                "INCONCLUSIVE",
                f"Functional workflow input or execution failed: {error}",
                "ENVIRONMENT_DEFECT",
            )
        saved_inputs = result.get("workflowInputs")
        if isinstance(saved_inputs, dict):
            workflow_inputs[workflow_id] = deepcopy(saved_inputs)
        resolved_inputs = result.get("workflowInputsById")
        if isinstance(resolved_inputs, dict):
            for resolved_workflow_id, resolved_values in resolved_inputs.items():
                if resolved_workflow_id in workflow_ids and isinstance(resolved_values, dict):
                    workflow_inputs[resolved_workflow_id] = deepcopy(resolved_values)
        used_workflow_ids = (
            set(resolved_inputs).intersection(workflow_ids)
            if isinstance(resolved_inputs, dict)
            else {workflow_id}
        )
        used_workflow_inputs = {
            used_id: deepcopy(workflow_inputs.get(used_id, {})) for used_id in used_workflow_ids
        }
        used_input_values = _input_records(
            {used_id: input_values.get(used_id, {}) for used_id in used_workflow_ids}
        )
        results.append(
            _workflow_record(
                workflow,
                result,
                used_input_values.get(workflow_id, []),
                used_workflow_inputs,
                used_input_values,
            )
        )
        if first_failure is None and str(result.get("gateStatus") or "").upper() != "PASS":
            first_failure = (
                str(result.get("failedWorkflowId") or workflow_id),
                result,
            )
            pending_workflow_ids = [
                str(item["workflowId"])
                for item in execution_workflows[index + 1 :]
                if str(item["workflowId"]) not in previous_results
            ]
            break

    fixed_inputs = {
        "workflowInputs": workflow_inputs,
        "inputValues": _input_records(input_values),
    }
    common = {
        "candidatePlan": document,
        "candidateDigest": stable_digest({"document": document, "fixedInputs": fixed_inputs}),
        "planDigest": stable_digest(document),
        "workflowInputs": workflow_inputs,
        "inputValues": fixed_inputs["inputValues"],
        "workflows": results,
        "reusedWorkflowIds": reused_workflow_ids,
        "executionOrder": [item["workflowId"] for item in results],
        "requirements": _requirements(results, candidates),
        "targetUrl": target_url,
    }
    if first_failure is not None:
        workflow_id, failed_result = first_failure
        finding = _failure_finding(workflow_id, failed_result)
        report = {
            **failed_result,
            **common,
            "finding": finding,
            "failedRequestDigest": (
                stable_digest(finding["request"]) if finding.get("request") else ""
            ),
            "failedWorkflowId": workflow_id,
            "failedStepId": finding.get("stepId") or "",
            "pendingWorkflowIds": pending_workflow_ids,
        }
        report["defect"] = classify_dynamic_failure(report)
        return {"current_node": "dynamic_functional", "dynamic_functional_report": report}
    return {
        "current_node": "dynamic_functional",
        "dynamic_functional_report": {
            "status": "passed",
            "gateStatus": "PASS",
            **common,
            "pendingWorkflowIds": [],
        },
    }


__all__ = [
    "build_workflow_candidates",
    "classify_dynamic_failure",
    "dynamic_functional_node",
    "repair_route",
]
