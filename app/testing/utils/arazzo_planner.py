"""Deterministic context builders for the EasyDep Arazzo planning boundary."""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping
from typing import Any

_METHODS = ("delete", "get", "head", "options", "patch", "post", "put", "trace")
_IDENTIFIER = re.compile(r"[^A-Za-z0-9_-]+")


class ArazzoPlanningError(ValueError):
    """Frozen planning inputs do not form a deterministic Arazzo context."""


def _records(value: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        for key in keys:
            nested = value.get(key)
            if isinstance(nested, list):
                value = nested
                break
    if not isinstance(value, list) or any(not isinstance(item, Mapping) for item in value):
        raise ArazzoPlanningError("Planning records must be a list of objects.")
    return [copy.deepcopy(dict(item)) for item in value]


def _id(record: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ArazzoPlanningError(f"Planning record is missing an identifier ({', '.join(keys)}).")


def _unique(records: Iterable[dict[str, Any]], *keys: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        identifier = _id(record, *keys)
        if identifier in result:
            raise ArazzoPlanningError(f"Duplicate frozen identifier: {identifier}")
        result[identifier] = record
    return result


def _strings(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item.strip() for item in value if isinstance(item, str) and item.strip()}


def _record_links(record: Mapping[str, Any], *keys: str) -> set[str]:
    links: set[str] = set()
    for key in keys:
        links.update(_strings(record.get(key)))
    return links


def _nested_requirement_ids(value: Any) -> set[str]:
    """Collect only explicitly named requirement-link fields from a use-case spec."""
    if isinstance(value, Mapping):
        result = _record_links(value, "requirementIds", "requirement_ids", "covered_req_ids")
        for child in value.values():
            result.update(_nested_requirement_ids(child))
        return result
    if isinstance(value, list):
        return set().union(*(_nested_requirement_ids(item) for item in value), set())
    return set()


def _workflow_id(use_case_id: str) -> str:
    normalized = _IDENTIFIER.sub("-", use_case_id).strip("-")
    if not normalized:
        raise ArazzoPlanningError(f"Use case cannot produce a workflowId: {use_case_id}")
    return "workflow-" + normalized


def _is_functional(use_case: Mapping[str, Any]) -> bool:
    """Respect only an explicit classification; unclassified use cases are functional."""
    for key in ("functional", "isFunctional", "is_functional"):
        if key in use_case:
            return use_case[key] is True
    for key in ("type", "kind", "category"):
        value = use_case.get(key)
        if isinstance(value, str) and value.strip().lower() in {"functional", "nonfunctional"}:
            return value.strip().lower() == "functional"
    return True


def _is_functional_requirement(requirement: Mapping[str, Any]) -> bool:
    for key in ("type", "requirementType", "requirement_type"):
        value = requirement.get(key)
        if isinstance(value, str):
            return value.strip().lower() in {"fr", "functional"}
    return False


def _traceability_links(use_cases: Any) -> dict[str, set[str]]:
    if not isinstance(use_cases, Mapping):
        return {}
    traceability = use_cases.get("traceability")
    requirements = traceability.get("requirements") if isinstance(traceability, Mapping) else None
    if not isinstance(requirements, Mapping):
        return {}
    result: dict[str, set[str]] = {}
    for requirement_id, detail in requirements.items():
        if isinstance(requirement_id, str) and isinstance(detail, Mapping):
            result[requirement_id] = _record_links(
                detail,
                "use_cases",
                "useCaseIds",
                "use_case_ids",
                "realized_by_use_cases",
                "constrains_use_cases",
            )
    return result


def _effective_parameters(
    path_item: Mapping[str, Any], operation: Mapping[str, Any]
) -> list[dict[str, Any]]:
    values: dict[tuple[str, str], dict[str, Any]] = {}
    for owner in (path_item, operation):
        raw = owner.get("parameters")
        if raw is None:
            continue
        if not isinstance(raw, list):
            raise ArazzoPlanningError("Frozen OpenAPI parameters must be a list.")
        for parameter in raw:
            if not isinstance(parameter, Mapping):
                raise ArazzoPlanningError("Frozen OpenAPI parameter must be an object.")
            name, location = parameter.get("name"), parameter.get("in")
            if (
                not isinstance(name, str)
                or not name.strip()
                or not isinstance(location, str)
                or not location.strip()
            ):
                raise ArazzoPlanningError("Frozen OpenAPI parameter lacks name or in.")
            values[(location, name)] = copy.deepcopy(dict(parameter))
    return [values[key] for key in sorted(values, key=lambda item: (item[0], item[1]))]


def _request_contract(operation: Mapping[str, Any]) -> dict[str, Any] | None:
    request_body = operation.get("requestBody")
    if request_body is None:
        return None
    if not isinstance(request_body, Mapping):
        raise ArazzoPlanningError("Frozen OpenAPI requestBody must be an object.")
    content = request_body.get("content")
    if content is not None and not isinstance(content, Mapping):
        raise ArazzoPlanningError("Frozen OpenAPI requestBody content must be an object.")
    json_content = content.get("application/json") if isinstance(content, Mapping) else None
    return {
        "required": bool(request_body.get("required")),
        "contentType": "application/json",
        "schema": copy.deepcopy(json_content.get("schema"))
        if isinstance(json_content, Mapping)
        else None,
    }


def _response_contracts(operation: Mapping[str, Any]) -> list[dict[str, Any]]:
    responses = operation.get("responses")
    if not isinstance(responses, Mapping):
        raise ArazzoPlanningError("Frozen OpenAPI operation responses must be an object.")
    result: list[dict[str, Any]] = []
    for status, response in sorted(responses.items(), key=lambda item: str(item[0])):
        if not isinstance(response, Mapping):
            raise ArazzoPlanningError("Frozen OpenAPI response must be an object.")
        content = response.get("content")
        if content is not None and not isinstance(content, Mapping):
            raise ArazzoPlanningError("Frozen OpenAPI response content must be an object.")
        json_content = content.get("application/json") if isinstance(content, Mapping) else None
        result.append(
            {
                "status": str(status),
                "schema": copy.deepcopy(json_content.get("schema"))
                if isinstance(json_content, Mapping)
                else None,
            }
        )
    return result


def _operation_projection(
    operation_id: str,
    method: str,
    path: str,
    operation: Mapping[str, Any],
    path_item: Mapping[str, Any],
    *,
    use_case_id: str,
    requirement_ids: set[str],
) -> dict[str, Any]:
    use_case_links = _record_links(
        operation, "x-easydep-use-case-ids", "useCaseIds", "use_case_ids"
    )
    scenario_links = _record_links(
        operation,
        "x-easydep-scenario-refs",
        "x-easydep-step-refs",
        "x-easydep-scenario-step-refs",
        "scenarioRefs",
        "scenario_refs",
    )
    requirement_links = _record_links(
        operation, "x-easydep-requirement-ids", "requirementIds", "requirement_ids"
    )
    relevance = (
        use_case_id in use_case_links
        or any(
            reference == use_case_id or reference.startswith(use_case_id + ":")
            for reference in scenario_links
        )
        or bool(requirement_ids.intersection(requirement_links))
    )
    return {
        "operationId": operation_id,
        "method": method.upper(),
        "path": path,
        "parameters": _effective_parameters(path_item, operation),
        "requestBody": _request_contract(operation),
        "responses": _response_contracts(operation),
        "traceHints": {
            "useCaseIds": sorted(use_case_links),
            "scenarioRefs": sorted(scenario_links),
            "requirementIds": sorted(requirement_links),
            "relevant": relevance,
        },
    }


def _operations(
    openapi: Mapping[str, Any], use_case_id: str, requirement_ids: set[str]
) -> list[dict[str, Any]]:
    paths = openapi.get("paths")
    if not isinstance(paths, Mapping) or not paths:
        raise ArazzoPlanningError("Frozen OpenAPI document has no paths object.")
    by_id: dict[str, dict[str, Any]] = {}
    for path, path_item in paths.items():
        if not isinstance(path, str) or not isinstance(path_item, Mapping):
            raise ArazzoPlanningError("Frozen OpenAPI path item is invalid.")
        for method in _METHODS:
            operation = path_item.get(method)
            if operation is None:
                continue
            if not isinstance(operation, Mapping):
                raise ArazzoPlanningError("Frozen OpenAPI operation is invalid.")
            operation_id = _id(operation, "operationId")
            if operation_id in by_id:
                raise ArazzoPlanningError(f"Duplicate frozen OpenAPI operationId: {operation_id}")
            by_id[operation_id] = _operation_projection(
                operation_id,
                method,
                path,
                operation,
                path_item,
                use_case_id=use_case_id,
                requirement_ids=requirement_ids,
            )
    if not by_id:
        raise ArazzoPlanningError("Frozen OpenAPI document has no operationIds.")
    return sorted(
        by_id.values(), key=lambda item: (not item["traceHints"]["relevant"], item["operationId"])
    )


def _evidence_refs(*records: Mapping[str, Any]) -> list[str]:
    values: set[str] = set()
    for record in records:
        values.update(_record_links(record, "evidenceRefs", "evidence_refs"))
    return sorted(values)


def build_workflow_candidates(
    requirements: Any, use_cases: Any, openapi: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Build one complete, non-filtering planning context per functional use case."""
    requirement_records = _records(requirements, "requirements", "functional_requirements")
    requirement_index = _unique(requirement_records, "id", "requirement_id", "requirementId")
    specs_source = (
        use_cases.get("use_case_specs", []) if isinstance(use_cases, Mapping) else use_cases
    )
    specs = _records(specs_source, "use_case_specs")
    if not specs:
        raise ArazzoPlanningError("Frozen use-case specifications are required.")
    spec_index = _unique(specs, "use_case_id", "useCaseId")
    use_case_records = _records(
        use_cases.get("use_cases", []) if isinstance(use_cases, Mapping) else [], "use_cases"
    )
    use_case_index = (
        _unique(use_case_records, "id", "use_case_id", "useCaseId") if use_case_records else {}
    )
    trace_links = _traceability_links(use_cases)
    functional_requirements = {
        identifier: record
        for identifier, record in requirement_index.items()
        if _is_functional_requirement(record)
    }
    result: list[dict[str, Any]] = []
    covered_functional_requirements: set[str] = set()
    for use_case_id, spec in spec_index.items():
        use_case = use_case_index.get(use_case_id, {})
        merged = {**copy.deepcopy(use_case), **copy.deepcopy(spec)}
        if not _is_functional(merged):
            continue
        use_case_spec = merged
        linked_ids = _nested_requirement_ids(use_case_spec)
        linked_ids.update(_record_links(use_case_spec, "requirements"))
        linked_ids.update(
            requirement_id
            for requirement_id, linked in trace_links.items()
            if use_case_id in linked
        )
        linked_ids.intersection_update(functional_requirements)
        operations = _operations(openapi, use_case_id, linked_ids)
        has_exact_operation_link = any(
            operation["traceHints"]["relevant"] for operation in operations
        )
        if not linked_ids and not has_exact_operation_link:
            raise ArazzoPlanningError(
                "Functional use case has neither a functional requirement nor an exact "
                f"OpenAPI operation link: {use_case_id}"
            )
        selected = [functional_requirements[identifier] for identifier in linked_ids]
        selected.sort(key=lambda record: _id(record, "id", "requirement_id", "requirementId"))
        selected_ids = {_id(record, "id", "requirement_id", "requirementId") for record in selected}
        covered_functional_requirements.update(selected_ids)
        result.append(
            {
                "workflowId": _workflow_id(use_case_id),
                "requirements": selected,
                "useCase": use_case_spec,
                "operations": operations,
                "trace": {
                    "requirementIds": sorted(selected_ids),
                    "useCaseIds": [use_case_id],
                    "evidenceRefs": sorted(
                        {
                            *(f"requirement:{identifier}" for identifier in selected_ids),
                            f"use_case:{use_case_id}",
                            *_evidence_refs(*selected, use_case_spec),
                        }
                    ),
                },
            }
        )
    for requirement_id, requirement in functional_requirements.items():
        scoped = bool(
            _record_links(
                requirement, "useCaseIds", "use_case_ids", "use_cases", "use_case_id", "useCaseId"
            )
            or trace_links.get(requirement_id)
        )
        is_constraint = (
            requirement.get("modeled_as_constraint") is True
            or requirement.get("modeledAsConstraint") is True
        )
        if scoped and requirement_id not in covered_functional_requirements and not is_constraint:
            raise ArazzoPlanningError(
                f"Scoped functional requirement is uncovered: {requirement_id}"
            )
    return sorted(result, key=lambda item: item["workflowId"])


def attach_workflow_trace(
    workflow: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Replace model traceability with the deterministic frozen candidate trace."""
    if not isinstance(workflow, Mapping):
        raise ArazzoPlanningError("Workflow must be an object.")
    workflow_id = _id(candidate, "workflowId")
    trace = candidate.get("trace")
    if not isinstance(trace, Mapping):
        raise ArazzoPlanningError("Candidate has no deterministic trace.")
    value = copy.deepcopy(dict(workflow))
    value.pop("x-easydep-trace", None)
    value["workflowId"] = workflow_id
    frozen_trace = {
        key: sorted(_strings(trace.get(key)))
        for key in ("requirementIds", "useCaseIds", "evidenceRefs")
        if _strings(trace.get(key))
    }
    value["x-easydep-trace"] = frozen_trace
    return value


def build_arazzo_document(workflows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Wrap frozen workflow objects in EasyDep's canonical local Arazzo envelope."""
    values: list[dict[str, Any]] = []
    for workflow in workflows:
        if not isinstance(workflow, Mapping):
            raise ArazzoPlanningError("Arazzo workflows must be objects.")
        values.append(copy.deepcopy(dict(workflow)))
    return {
        "arazzo": "1.1.0",
        "info": {"title": "EasyDep Functional Workflows", "version": "1.0.0"},
        "sourceDescriptions": [{"name": "application", "url": "openapi.json", "type": "openapi"}],
        "workflows": values,
    }


__all__ = [
    "ArazzoPlanningError",
    "attach_workflow_trace",
    "build_arazzo_document",
    "build_workflow_candidates",
]
