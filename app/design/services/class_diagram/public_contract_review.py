"""Independent semantic closure review for public use-case contracts.

The normal class validator deliberately proves only finite structural facts.  It
cannot decide whether a DTO called ``Request`` is the requirement's "request
details".  This module keeps that open-world judgement in one bounded,
structured reviewer call and then verifies every reference the reviewer cites.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.design.services.class_diagram.cache import (
    AcceptedUnitCache,
    accepted_unit_key,
    configured_provider_identity,
    record_cache_outcome,
)
from app.design.services.class_diagram.scenario import ScenarioIndex, UseCase, text
from app.design.services.class_diagram.validation.model import operation_catalog
from app.design.services.common.structured import parse_structured
from app.llm_connection import build_llm_connection
from app.llm_profiles import effective_temperature
from app.validation import Finding, stable_digest


_EVIDENCE_VERSION = "class-public-contract-review/v1"
_PROMPT = """You independently review whether an accepted class-model use-case
slice closes every public-contract obligation owned by the class stage.  The
requirements/API/implementation stages own the authentication policy expressed
by identity obligations of kind authenticate; do not require a class operation
or collaboration mapping for that policy precondition.  Do not accept a claim merely
because a DTO, principal, or method has a similar name.  For every obligation,
cite the accepted operation and collaboration call that realize it; cite a
parameter or declared field when that is the evidence for the value/identity.
For required_values with usage control or both, cite the Control call that receives
the value, its exact parameter, and that call's argument binding. For usage result
or both, cite a Control operation with a concrete non-void return type.
If the supplied model does not make the connection clear, return fail or
ambiguous with one precise reason.  Never invent IDs, fields, operations, or
calls.  Return only the response schema."""


class _ReviewMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    obligation_id: str = Field(alias="obligationId", min_length=1)
    operation_id: str = Field(alias="operationId", min_length=1)
    call_id: str = Field(alias="callId", min_length=1)
    parameter_name: str | None = Field(default=None, alias="parameterName")
    field_owner: str | None = Field(default=None, alias="fieldOwner")
    field_name: str | None = Field(default=None, alias="fieldName")
    rationale: str = Field(min_length=1)


class _ReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["pass", "fail", "ambiguous"]
    mappings: list[_ReviewMapping] = Field(default_factory=list)
    finding: str = ""


def _contract(use_case: UseCase) -> dict[str, Any]:
    candidate = use_case.specification.get("public_contract")
    return dict(candidate) if isinstance(candidate, Mapping) else {}


def has_obligations(index: ScenarioIndex) -> bool:
    return any(
        _obligations(use_case)
        for use_case in index.use_cases
    )


def _obligations(use_case: UseCase) -> list[dict[str, Any]]:
    contract = _contract(use_case)
    result: list[dict[str, Any]] = []
    for ordinal, item in enumerate(contract.get("identity_obligations") or [], start=1):
        if isinstance(item, Mapping) and text(item.get("obligation")).casefold() != "authenticate":
            result.append({"obligationId": f"identity:{ordinal}", "kind": "identity", **dict(item)})
    for ordinal, item in enumerate(contract.get("required_values") or [], start=1):
        if isinstance(item, Mapping):
            result.append({"obligationId": f"value:{ordinal}", "kind": "required_value", **dict(item)})
    return result


def _fields(model: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for owner in model.get("Classes") or []:
        if not isinstance(owner, Mapping):
            continue
        name = text(owner.get("className"))
        declared: set[str] = set()
        for field in owner.get("fields") or []:
            raw = text(field)
            field_name, _separator, _field_type = raw.partition(":")
            if field_name.strip():
                declared.add(field_name.strip())
        result[name] = declared
    return result


def _slice(model: dict[str, Any], use_case: UseCase) -> dict[str, Any]:
    prefix = f"{use_case.id}:"
    classes: list[dict[str, Any]] = []
    for owner in model.get("Classes") or []:
        if not isinstance(owner, Mapping):
            continue
        operations = [
            dict(operation) for operation in owner.get("operations") or []
            if isinstance(operation, Mapping)
            and any(text(ref).startswith(prefix) for ref in operation.get("stepRefs") or [])
        ]
        if operations:
            classes.append({"className": text(owner.get("className")), "operations": operations})
    collaboration = next(
        (dict(item) for item in model.get("Collaborations") or []
         if isinstance(item, Mapping) and text(item.get("collaborationId")) == use_case.id),
        {},
    )
    return {"Classes": classes, "Collaboration": collaboration}


def _review_key(index: ScenarioIndex, model: dict[str, Any], use_case: UseCase) -> str:
    return accepted_unit_key(
        "class-public-contract-review",
        unit_slice={"useCase": use_case.id, "obligations": _obligations(use_case), "slice": _slice(model, use_case)},
        inventory={}, feedback={}, prompt=_PROMPT, schema=_ReviewResponse,
        provider=configured_provider_identity(build_llm_connection().base_url),
        model=settings.model, seed=settings.seed,
        temperature=effective_temperature(settings.model, settings.temperature),
        reasoning_effort=None, max_completion_tokens=None,
        extra={"version": _EVIDENCE_VERSION, "scenario": index.raw},
    )


def _review_one(index: ScenarioIndex, model: dict[str, Any], use_case: UseCase) -> _ReviewResponse:
    payload = {"useCaseId": use_case.id, "publicContractObligations": _obligations(use_case), "acceptedFragmentAndCollaboration": _slice(model, use_case)}
    parsed = parse_structured(
        [{"role": "system", "content": _PROMPT}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        _ReviewResponse, operation="ClassPublicContractReview",
        metadata={"useCaseId": use_case.id, "executionSlice": use_case.id},
    )
    return _ReviewResponse.model_validate(parsed)


def _verify_response(model: dict[str, Any], use_case: UseCase, response: _ReviewResponse) -> str:
    expected = {item["obligationId"] for item in _obligations(use_case)}
    mapped = [item.obligation_id for item in response.mappings]
    if response.status != "pass":
        return text(response.finding) or "The public-contract closure is missing or ambiguous."
    if set(mapped) != expected or len(mapped) != len(set(mapped)):
        return "The reviewer did not map every public-contract obligation exactly once."
    operations = operation_catalog(model)
    collaboration = next((item for item in model.get("Collaborations") or [] if isinstance(item, Mapping) and text(item.get("collaborationId")) == use_case.id), {})
    calls = {text(item.get("callId")): item for item in collaboration.get("calls") or [] if isinstance(item, Mapping)}
    fields = _fields(model)
    obligations = {item["obligationId"]: item for item in _obligations(use_case)}
    for mapping in response.mappings:
        operation = operations.get(mapping.operation_id)
        if operation is None:
            return f"Reviewer cited unknown operation '{mapping.operation_id}'."
        call = calls.get(mapping.call_id)
        if call is None or text(call.get("receiverOperationId")) != mapping.operation_id:
            return f"Reviewer cited call '{mapping.call_id}' that does not invoke '{mapping.operation_id}'."
        if mapping.parameter_name is not None and mapping.parameter_name not in {text(item.get("name")) for item in operation.get("parameters") or [] if isinstance(item, Mapping)}:
            return f"Reviewer cited missing parameter '{mapping.parameter_name}' on '{mapping.operation_id}'."
        if (mapping.field_owner is None) != (mapping.field_name is None):
            return "Reviewer field evidence must include both owner and field name."
        if mapping.field_owner is not None and mapping.field_name not in fields.get(mapping.field_owner, set()):
            return f"Reviewer cited missing field '{mapping.field_owner}.{mapping.field_name}'."
        obligation = obligations[mapping.obligation_id]
        usage = text(obligation.get("usage")).casefold() if obligation.get("kind") == "required_value" else ""
        if usage in {"control", "both"}:
            if text(operation.get("stereotype")).casefold() != "control":
                return f"Required value '{mapping.obligation_id}' must be mapped to a Control call."
            if mapping.parameter_name is None:
                return f"Required value '{mapping.obligation_id}' must cite its Control parameter."
            bound_parameters = {
                text(binding.get("parameter")) for binding in call.get("argumentBindings") or []
                if isinstance(binding, Mapping)
            }
            if mapping.parameter_name not in bound_parameters:
                return f"Required value '{mapping.obligation_id}' is not bound on call '{mapping.call_id}'."
            if text(obligation.get("source")).casefold() == "authenticated_actor_context":
                context_refs = {
                    text(binding.get("sourceRef")) for binding in call.get("argumentBindings") or []
                    if isinstance(binding, Mapping)
                    and text(binding.get("parameter")) == mapping.parameter_name
                }
                if not any(source_ref.startswith("context#") for source_ref in context_refs):
                    return f"Required value '{mapping.obligation_id}' must use trusted context for its Control parameter."
        if usage in {"result", "both"} and (
            text(operation.get("stereotype")).casefold() != "control"
            or not text(operation.get("returnType"))
            or text(operation.get("returnType")).casefold() == "void"
        ):
            return f"Required value '{mapping.obligation_id}' must cite a Control operation with a concrete return."
    return ""


def _evidence_matches(evidence: object, model: dict[str, Any], index: ScenarioIndex) -> bool:
    return isinstance(evidence, Mapping) and evidence.get("version") == _EVIDENCE_VERSION and evidence.get("modelDigest") == stable_digest(model) and evidence.get("contractDigest") == stable_digest([_obligations(item) for item in index.use_cases])


def review_public_contract_closure(
    model: dict[str, Any], index: ScenarioIndex, *, cache: AcceptedUnitCache | None = None,
    evidence: object = None,
) -> tuple[list[Finding], dict[str, Any]]:
    """Review only obligation-bearing UCs; matching persisted evidence is authoritative."""
    if not has_obligations(index):
        return [], {"version": _EVIDENCE_VERSION, "modelDigest": stable_digest(model), "contractDigest": stable_digest([_obligations(item) for item in index.use_cases]), "status": "not_required", "verdicts": []}
    if _evidence_matches(evidence, model, index):
        stored = dict(evidence)
        findings = [Finding("class.public-contract-semantic", text(item.get("finding")), text(item.get("useCaseId")), origin="semantic") for item in stored.get("verdicts") or [] if isinstance(item, Mapping) and item.get("status") != "pass"]
        return findings, stored
    findings: list[Finding] = []
    verdicts: list[dict[str, Any]] = []
    for use_case in index.use_cases:
        if not _obligations(use_case):
            continue
        key = _review_key(index, model, use_case)
        def compute() -> dict[str, Any]:
            return _review_one(index, model, use_case).model_dump(by_alias=True)
        try:
            if cache is None:
                record_cache_outcome(None, operation="ClassPublicContractReview", unit=use_case.id)
                raw = compute()
            else:
                cached = cache.get_or_compute(key, compute)
                record_cache_outcome(cached, operation="ClassPublicContractReview", unit=use_case.id)
                raw = cached.value
        except Exception as error:  # a missing verdict must block approval, never pass implicitly
            problem = f"Public-contract semantic reviewer unavailable: {type(error).__name__}: {error}"
            verdicts.append({"useCaseId": use_case.id, "status": "review_error", "mappings": [], "finding": problem, "reviewKey": key})
            findings.append(Finding("class.public-contract-semantic", problem, use_case.id, origin="semantic"))
            continue
        response = _ReviewResponse.model_validate(raw)
        problem = _verify_response(model, use_case, response)
        status = "pass" if not problem else response.status if response.status != "pass" else "fail"
        verdict = {"useCaseId": use_case.id, "status": status, "mappings": response.model_dump(by_alias=True).get("mappings", []), "finding": problem, "reviewKey": key}
        verdicts.append(verdict)
        if problem:
            findings.append(Finding("class.public-contract-semantic", problem, use_case.id, origin="semantic"))
    return findings, {"version": _EVIDENCE_VERSION, "modelDigest": stable_digest(model), "contractDigest": stable_digest([_obligations(item) for item in index.use_cases]), "status": "pass" if not findings else "needs_input", "verdicts": verdicts}


def semantic_evidence_for_readiness(model: dict[str, Any], state: Mapping[str, Any], index: ScenarioIndex) -> list[Finding]:
    """Pure readiness check: never invokes a model or trusts stale evidence."""
    if not has_obligations(index):
        return []
    check = state.get("class_diagram_check")
    evidence = check.get("semanticEvidence") if isinstance(check, Mapping) else None
    if not _evidence_matches(evidence, model, index):
        return [Finding("class.public-contract-semantic", "Public-contract semantic review evidence is missing or stale; rerun the class-stage check.", "class_diagram", origin="semantic")]
    return [Finding("class.public-contract-semantic", text(item.get("finding")), text(item.get("useCaseId")), origin="semantic") for item in evidence.get("verdicts") or [] if isinstance(item, Mapping) and item.get("status") != "pass"]
