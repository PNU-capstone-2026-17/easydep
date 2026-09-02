"""한 유스케이스의 여러 actor entry를 하나의 호출 계획으로 구체화한다."""
from __future__ import annotations

import json
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, create_model

from app.config import settings
from app.design.schemas.class_model import BCEModel, Collaboration, canonical_call_id
from app.design.services.class_diagram.cache import (
    AcceptedUnitCache,
    accepted_unit_key,
    configured_provider_identity,
    record_cache_outcome,
)
from app.design.services.class_diagram.proposals import CallPlanProposal, ProposedCall
from app.design.services.class_diagram.scenario import (
    ExecutionGroup,
    ScenarioIndex,
    UseCase,
    text,
)
from app.design.services.class_diagram.type_system import (
    projected_field_type,
    structured_field_types,
    types_compatible,
)
from app.design.services.class_diagram.validation.collaboration import (
    COLLABORATION_CHECKS,
    CollaborationContext,
)
from app.design.services.class_diagram.validation.model import (
    derived_value_source,
    operation_catalog,
    optional_inner_type,
    runtime_value_source,
    type_can_default,
)
from app.design.services.common.structured import parse_structured
from app.validation import Finding, RepairAttempt, RepairLedger, run_checks, stable_digest

CALL_PLAN_PROMPT = """
Build one ordered call forest for the complete use case. Select only supplied
receiverOperationId values. Return only receiverOperationId and parentCallIndex.
Each actorEntry creates exactly one root in the supplied order; no other root is
allowed. A root has no parent. Every non-root uses the one-based position of an
earlier call in the same root as parentCallIndex. A root is Boundary and
delegates to Control, which may delegate state work to Entity. Ordinary results
return through the existing call chain. Use a Control-to-Boundary call only when
the scenario explicitly requires the system to initiate a separate interaction
with an external actor or system through that Boundary, such as an asynchronous
notification; parent it to Control, never to Boundary. Entities may collaborate
with other Entities, but do not call a Control or Boundary directly. The Boundary
class used by a root must not appear again inside that root. Cover all
required steps inside the matching actor entry. The same operation may be used
in more than one root. Do not return ids, step refs, values, or bindings.
If the supplied operations contain Entity behavior for durable domain information
used by this execution, call that Entity behavior from Control. Read-only stored
data is still Entity behavior. When no Entity operation is supplied for the
execution, do not invent one in the call plan.
""".strip()

BINDING_PROMPT = """
Select one sourceRef for each supplied finite choice. Prefer the source whose
name and role match the receiver parameter. Return no explanation.
""".strip()


def call_plan_reasoning_effort() -> str:
    return str(getattr(
        settings, "design_class_call_plan_reasoning_effort", settings.design_reasoning_effort,
    ))


def call_plan_max_completion_tokens() -> int:
    return int(getattr(
        settings,
        "design_class_call_plan_max_completion_tokens",
        settings.design_class_collaboration_max_completion_tokens,
    ))


def _finite_schema(name: str, **fields: Any) -> type[BaseModel]:
    return cast(type[BaseModel], create_model(name, **fields))


def _finding_text(findings: tuple[Finding, ...]) -> list[str]:
    return [
        f"{finding.location}: {finding.message}" if finding.location else finding.message
        for finding in findings
    ]


def _groups(index: ScenarioIndex, use_case: UseCase) -> tuple[ExecutionGroup, ...]:
    return tuple(group for group in index.groups if group.use_case_id == use_case.id)


def _use_case_operations(
    index: ScenarioIndex, model: dict[str, Any], use_case: UseCase,
) -> dict[str, dict[str, Any]]:
    groups = _groups(index, use_case)
    allowed = {use_case.id} | {
        use_case_id for group in groups for use_case_id in group.trace_use_case_ids
    }
    class_scope = {
        text(item.get("className")): {
            text(value) for value in item.get("use_case_ids") or []
        }
        for item in model.get("Classes") or [] if isinstance(item, dict)
    }
    return {
        operation_id: operation
        for operation_id, operation in operation_catalog(model).items()
        if allowed & class_scope.get(text(operation.get("className")), set())
    }


def _use_case_payload(
    index: ScenarioIndex, model: dict[str, Any], use_case: UseCase,
) -> dict[str, Any]:
    groups = _groups(index, use_case)
    operations = _use_case_operations(index, model, use_case)
    step_by_id = {
        step.id: step for group in groups for use_case_id in group.trace_use_case_ids
        for step in index.use_case(use_case_id).steps
    }
    required = {ref for group in groups for ref in group.required_step_ids}
    return {
        "collaborationId": use_case.id,
        "actorEntries": [
            {
                "actorStepRef": group.actor_step,
                "requiredStepRefs": list(group.required_step_ids),
            }
            for group in groups
        ],
        "steps": [
            {"id": step_id, "sentence": step_by_id[step_id].sentence}
            for step_id in dict.fromkeys(
                ref for group in groups for ref in group.required_step_ids
            )
            if step_id in step_by_id
        ],
        "receiverOperations": [
            {
                "operationId": operation_id,
                "className": operation["className"],
                "stereotype": operation["stereotype"],
                "parameters": operation.get("parameters") or [],
                "returnType": operation.get("returnType"),
                "stepRefs": operation.get("stepRefs") or [],
            }
            for operation_id, operation in sorted(operations.items())
            if required & {text(ref) for ref in operation.get("stepRefs") or []}
        ],
    }


def propose_call_plan(
    index: ScenarioIndex,
    model: BCEModel,
    use_case: UseCase,
    *,
    previous: CallPlanProposal | None = None,
    finding: str = "",
) -> CallPlanProposal:
    """완성 skeleton에서 유스케이스 전체의 multiple-root 호출 계획을 제안한다."""

    payload = _use_case_payload(index, model.model_dump(by_alias=True), use_case)
    if previous is not None:
        payload["previousPlan"] = previous.model_dump(by_alias=True)
    if finding:
        payload["task"] = "Return a full repaired call plan and resolve the finding."
        payload["finding"] = finding
    operation_ids = tuple(item["operationId"] for item in payload["receiverOperations"])
    if not operation_ids:
        raise ValueError(f"use case has no receiver operations: {use_case.id}")
    finite_call = _finite_schema(
        "FiniteUseCaseCall",
        __base__=ProposedCall,
        receiver_operation_id=(
            Literal.__getitem__(operation_ids), Field(alias="receiverOperationId"),
        ),
    )
    finite_plan = _finite_schema(
        "FiniteUseCaseCallPlan",
        __base__=CallPlanProposal,
        calls=(list[finite_call], Field(min_length=len(payload["actorEntries"]))),  # type: ignore[valid-type]
    )
    parsed = parse_structured(
        [
            {"role": "system", "content": CALL_PLAN_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        finite_plan,
        reasoning_effort=call_plan_reasoning_effort(),
        max_completion_tokens=call_plan_max_completion_tokens(),
        operation="InteractionCallPlanRepair" if finding else "InteractionCallPlan",
        metadata={
            "useCaseId": use_case.id,
            "executionSlice": use_case.id,
            "candidateCount": len(operation_ids),
        },
    )
    return CallPlanProposal.model_validate(
        finite_plan.model_validate(parsed).model_dump(by_alias=True),
    )


def _root_assignments(
    plan: CallPlanProposal, root_count: int,
) -> tuple[list[int], dict[int, int]]:
    roots = [
        position for position, call in enumerate(plan.calls, start=1)
        if call.parent_call_index is None
    ]
    if len(roots) != root_count:
        raise ValueError("root calls must match actor entries in scenario order")
    root_ordinal = {position: ordinal for ordinal, position in enumerate(roots)}
    assignments: dict[int, int] = {}
    latest_root = -1
    for position, call in enumerate(plan.calls, start=1):
        if position in root_ordinal:
            latest_root = root_ordinal[position]
        parent = call.parent_call_index
        if parent is not None and parent >= position:
            raise ValueError("parentCallIndex must reference an earlier call")
        current = position
        visited: set[int] = set()
        while current not in root_ordinal:
            if current in visited:
                raise ValueError("call parent chain contains a cycle")
            visited.add(current)
            parent = plan.calls[current - 1].parent_call_index
            if parent is None or parent >= current:
                raise ValueError("every non-root call requires an earlier parent")
            current = parent
        assignments[position] = root_ordinal[current]
        if assignments[position] != latest_root:
            raise ValueError("a call cannot return to an earlier actor root")
    return roots, assignments


def _ancestors(calls: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    previous = {text(call.get("callId")): call for call in calls[:index]}
    parent_id = text(calls[index].get("parentCallId"))
    result: list[dict[str, Any]] = []
    while parent_id and parent_id in previous:
        call = previous[parent_id]
        result.append(call)
        parent_id = text(call.get("parentCallId"))
    return result


def _binding_candidates(
    model: dict[str, Any],
    use_case: UseCase,
    actor_step: str | None,
    is_root: bool,
    calls: list[dict[str, Any]],
    call_index: int,
    parameter: dict[str, Any],
    operations: dict[str, dict[str, Any]],
) -> list[str]:
    name = text(parameter.get("name"))
    target_type = text(parameter.get("type"))
    fields_by_type = structured_field_types(model)
    candidates: list[str] = []
    named_sources: dict[str, list[tuple[str, str]]] = {}

    def add_named(source_name: str, source_type: str, source_ref: str) -> None:
        named_sources.setdefault(source_name.casefold(), []).append((source_type, source_ref))

    if is_root and actor_step:
        candidates.append(f"{actor_step}#{name}")
    if is_root:
        candidates.extend(f"{ref}#{name}" for ref in use_case.precondition_refs)
    ancestors = _ancestors(calls, call_index)
    ancestor_ids = {text(item.get("callId")) for item in ancestors}
    for ancestor in ancestors:
        operation = operations[text(ancestor.get("receiverOperationId"))]
        for source in operation.get("parameters") or []:
            if not isinstance(source, dict):
                continue
            source_name = text(source.get("name"))
            source_type = text(source.get("type"))
            source_ref = f"{ancestor['callId']}#{source_name}"
            add_named(source_name, source_type, source_ref)
            if types_compatible(source_type, target_type):
                candidates.append(source_ref)
            for field_path in fields_by_type.get(source_type, {}):
                projected = projected_field_type(source_type, field_path, fields_by_type)
                field_ref = f"{source_ref}.{field_path}"
                add_named(field_path, projected, field_ref)
                if types_compatible(projected, target_type):
                    candidates.append(field_ref)
                elif types_compatible(optional_inner_type(projected), target_type):
                    candidates.append(field_ref + ".unwrap")
    for earlier in reversed(calls[:call_index]):
        operation = operations[text(earlier.get("receiverOperationId"))]
        return_type = text(operation.get("returnType"))
        result_ref = f"{earlier['callId']}#result"
        if return_type.casefold() != "void" and types_compatible(return_type, target_type):
            candidates.append(result_ref)
        elif types_compatible(optional_inner_type(return_type), target_type):
            candidates.append(result_ref + ".unwrap")
        for field_path in fields_by_type.get(return_type, {}):
            projected = projected_field_type(return_type, field_path, fields_by_type)
            field_ref = f"{result_ref}.{field_path}"
            add_named(field_path, projected, field_ref)
            if (
                text(earlier.get("callId")) in ancestor_ids
                or field_path.casefold() == name.casefold()
            ):
                if types_compatible(projected, target_type):
                    candidates.append(field_ref)
                elif types_compatible(optional_inner_type(projected), target_type):
                    candidates.append(field_ref + ".unwrap")
    target_fields = fields_by_type.get(target_type, {})
    if not candidates and target_fields:
        mappings: dict[str, str] = {}
        for field, expected in target_fields.items():
            matching = [
                source_ref for source_type, source_ref in named_sources.get(field.casefold(), [])
                if types_compatible(source_type, expected)
            ]
            if matching:
                mappings[field] = matching[0]
            elif runtime_value_source(expected):
                mappings[field] = runtime_value_source(expected)
            elif not type_can_default(expected):
                break
        else:
            candidates.append(derived_value_source(target_type, mappings))
    if not candidates and runtime_value_source(target_type):
        candidates.append(runtime_value_source(target_type))
    return list(dict.fromkeys(candidates))


def select_ambiguous_bindings(
    use_case: UseCase, ambiguous: dict[str, list[str]],
) -> dict[str, str]:
    fields: dict[str, tuple[Any, Any]] = {}
    choices: list[dict[str, Any]] = []
    locations: dict[str, str] = {}
    for position, (parameter, candidates) in enumerate(sorted(ambiguous.items()), start=1):
        field_name = f"choice{position}"
        values = tuple(dict.fromkeys(candidates))
        fields[field_name] = (
            Literal.__getitem__(values), Field(description=f"Source for {parameter}"),
        )
        choices.append({"choice": field_name, "parameter": parameter, "candidates": list(values)})
        locations[field_name] = parameter
    schema = _finite_schema(
        "FiniteBindingChoices", __config__=ConfigDict(extra="forbid"), **fields,
    )
    parsed = parse_structured(
        [
            {"role": "system", "content": BINDING_PROMPT},
            {"role": "user", "content": json.dumps({
                "collaborationId": use_case.id, "choices": choices,
            }, ensure_ascii=False)},
        ],
        schema,
        reasoning_effort="low",
        max_completion_tokens=min(settings.design_class_collaboration_max_completion_tokens, 2048),
        operation="InteractionBindingSelection",
        metadata={
            "useCaseId": use_case.id,
            "executionSlice": use_case.id,
            "candidateCount": sum(len(choice["candidates"]) for choice in choices),
        },
    )
    selected = schema.model_validate(parsed).model_dump()
    return {locations[field_name]: source_ref for field_name, source_ref in selected.items()}


def materialize(
    index: ScenarioIndex,
    model: BCEModel,
    use_case: UseCase,
    plan: CallPlanProposal,
) -> Collaboration:
    """flat multiple-root 계획을 canonical call·step·binding 협업으로 만든다."""

    model_payload = model.model_dump(by_alias=True)
    operations = _use_case_operations(index, model_payload, use_case)
    groups = _groups(index, use_case)
    roots, assignments = _root_assignments(plan, len(groups))
    root_set = set(roots)
    calls: list[dict[str, Any]] = []
    for position, proposed in enumerate(plan.calls, start=1):
        operation_id = text(proposed.receiver_operation_id)
        operation = operations.get(operation_id)
        if operation is None:
            raise ValueError(f"unknown receiverOperationId: {operation_id}")
        group = groups[assignments[position]]
        refs = [
            ref for ref in group.required_step_ids
            if ref in {text(value) for value in operation.get("stepRefs") or []}
        ]
        if not refs:
            raise ValueError("selected operation has no declared step in its actor entry")
        calls.append({
            "callId": canonical_call_id(use_case.id, position),
            "parentCallId": (
                canonical_call_id(use_case.id, proposed.parent_call_index)
                if proposed.parent_call_index else None
            ),
            "receiverOperationId": operation_id,
            "stepRefs": refs,
            "argumentBindings": [],
        })
    root_boundary_classes = {
        assignments[position]: text(
            operations[calls[position - 1]["receiverOperationId"]].get("className")
        )
        for position in roots
    }
    control_roots: set[int] = set()
    for position, call in enumerate(calls, start=1):
        operation = operations[call["receiverOperationId"]]
        stereotype = text(operation.get("stereotype"))
        if position in root_set:
            if stereotype != "boundary":
                raise ValueError("actor entry must start at Boundary")
        else:
            parent = calls[(plan.calls[position - 1].parent_call_index or 1) - 1]
            parent_operation = operations[parent["receiverOperationId"]]
            source = text(parent_operation.get("stereotype"))
            target_class = text(operation.get("className"))
            if (
                (source == "boundary" and stereotype != "control")
                or (source == "entity" and stereotype != "entity")
                or (
                    source == "control"
                    and stereotype == "boundary"
                    and target_class == root_boundary_classes[assignments[position]]
                )
            ):
                raise ValueError(f"BCE communication is invalid: {source} -> {stereotype}")
        if stereotype == "control":
            control_roots.add(assignments[position])
    if control_roots != set(range(len(groups))):
        raise ValueError("each Boundary root must delegate to Control")
    ambiguous: dict[str, list[str]] = {}
    for call_index, call in enumerate(calls):
        operation = operations[call["receiverOperationId"]]
        group = groups[assignments[call_index + 1]]
        for parameter in operation.get("parameters") or []:
            if not isinstance(parameter, dict):
                raise TypeError("operation parameter must be an object")
            candidates = _binding_candidates(
                model_payload, use_case, group.actor_step,
                call_index + 1 in root_set, calls, call_index, parameter, operations,
            )
            location = f"{call['callId']}#{text(parameter.get('name'))}"
            if not candidates:
                raise ValueError(f"no finite source for {location}")
            if len(candidates) == 1:
                call["argumentBindings"].append({
                    "parameter": text(parameter.get("name")), "sourceRef": candidates[0],
                })
            else:
                ambiguous[location] = candidates
    selected = select_ambiguous_bindings(use_case, ambiguous) if ambiguous else {}
    for call in calls:
        operation = operations[call["receiverOperationId"]]
        existing = {text(item.get("parameter")) for item in call["argumentBindings"]}
        for parameter in operation.get("parameters") or []:
            name = text(parameter.get("name"))
            if name not in existing:
                call["argumentBindings"].append({
                    "parameter": name, "sourceRef": selected[f"{call['callId']}#{name}"],
                })
    trace_ids = list(dict.fromkeys(
        [use_case.id, *(value for group in groups for value in group.trace_use_case_ids)]
    ))
    candidate = {
        "collaborationId": use_case.id,
        "useCaseIds": trace_ids,
        "entryActor": use_case.primary_actor or None,
        "calls": calls,
    }
    report = run_checks(
        COLLABORATION_CHECKS,
        candidate,
        CollaborationContext(index, model_payload, use_case),
    )
    if report.errors or report.findings:
        raise ValueError("; ".join([*report.errors, *_finding_text(report.findings)]))
    return Collaboration.model_validate(candidate)


class CombinedReplacementRequired(RuntimeError):
    """call-plan 수리가 반복되어 유스케이스 전체 교체가 필요함을 알린다."""

    def __init__(
        self,
        use_case_id: str,
        issue: str,
        previous_plan: CallPlanProposal,
    ) -> None:
        super().__init__(issue)
        self.use_case_id = use_case_id
        self.issue = issue
        self.previous_plan = previous_plan


def _accepted_payload(
    index: ScenarioIndex,
    model: BCEModel,
    use_case: UseCase,
    directive: str,
) -> dict[str, Any]:
    ledger = RepairLedger()
    previous: CallPlanProposal | None = None
    finding = directive
    attempt = 0
    seen_states: set[str] = set()
    seen_findings: set[str] = set()
    while True:
        # Provider/schema 예외는 semantic finding으로 바꾸지 않는다.
        candidate = propose_call_plan(
            index, model, use_case, previous=previous, finding=finding,
        )
        try:
            return materialize(index, model, use_case, candidate).model_dump(by_alias=True)
        except ValueError as error:
            error_text = f"{type(error).__name__}: {error}"
            candidate_digest = stable_digest(candidate.model_dump(by_alias=True))
            state_digest = stable_digest({
                "candidate": candidate_digest,
                "finding": error_text,
            })
            # call 순서를 바꿨는데도 같은 값 출처나 BCE 방향 오류가 다시 나오면
            # operation 자체가 원인일 가능성이 높다. 같은 finding을 두 번 고치게 하지
            # 않고 operation+call 결합 교체로 곧바로 범위를 넓힌다.
            repeated = state_digest in seen_states or error_text in seen_findings
            ledger.record(RepairAttempt(
                stage="design.class.collaboration",
                target_ids=(use_case.id,),
                strategy_key=f"full-call-plan-replacement-{attempt + 1}",
                input_digest=stable_digest(_use_case_payload(
                    index, model.model_dump(by_alias=True), use_case,
                )),
                candidate_digest=candidate_digest,
                finding_keys_before=(error_text,),
                finding_keys_after=(error_text,),
                outcome="repeated_candidate" if repeated else "no_improvement",
                detail=error_text,
            ))
            if repeated:
                raise CombinedReplacementRequired(
                    use_case.id,
                    error_text + "\n\nAccumulated call-plan repair history:\n"
                    + ledger.prompt_context(),
                    candidate,
                ) from error
            seen_states.add(state_digest)
            seen_findings.add(error_text)
            previous = candidate
            finding = (
                f"{error_text}\n\nReturn a different full plan. Repair history:\n"
                + ledger.prompt_context()
            )
            attempt += 1


def _cache_key(
    index: ScenarioIndex, model: BCEModel, use_case: UseCase, directive: str,
) -> str:
    return accepted_unit_key(
        "use-case-collaboration",
        unit_slice=_use_case_payload(index, model.model_dump(by_alias=True), use_case),
        inventory=model.model_dump(by_alias=True),
        feedback=" ".join(directive.split()),
        prompt=CALL_PLAN_PROMPT,
        schema=CallPlanProposal,
        provider=configured_provider_identity(settings.base_url),
        model=settings.model,
        seed=settings.seed,
        temperature=settings.temperature,
        reasoning_effort=call_plan_reasoning_effort(),
        max_completion_tokens=call_plan_max_completion_tokens(),
        extra={
            "bindingPrompt": BINDING_PROMPT,
            "bindingReasoningEffort": "low",
            "bindingMaxCompletionTokens": min(
                settings.design_class_collaboration_max_completion_tokens, 2048,
            ),
        },
    )


def process_use_case(
    index: ScenarioIndex,
    model: BCEModel,
    use_case: UseCase,
    directive: str = "",
    *,
    cache: AcceptedUnitCache | None = None,
) -> Collaboration:
    """유스케이스 전체 call plan을 수락할 때까지 국소 교체한다."""

    if not _groups(index, use_case):
        raise ValueError("use case has no actor entry")
    if cache is None:
        record_cache_outcome(None, operation="InteractionCallPlan", unit=use_case.id)
        payload = _accepted_payload(index, model, use_case, directive)
    else:
        result = cache.get_or_compute(
            _cache_key(index, model, use_case, directive),
            lambda: _accepted_payload(index, model, use_case, directive),
        )
        record_cache_outcome(result, operation="InteractionCallPlan", unit=use_case.id)
        payload = result.value
    accepted = Collaboration.model_validate(payload)
    report = run_checks(
        COLLABORATION_CHECKS,
        accepted.model_dump(by_alias=True),
        CollaborationContext(index, model.model_dump(by_alias=True), use_case),
    )
    if report.errors or report.findings:
        raise ValueError("cached collaboration is invalid")
    return accepted


__all__ = [
    "CombinedReplacementRequired",
    "materialize",
    "process_use_case",
    "propose_call_plan",
    "select_ambiguous_bindings",
]
