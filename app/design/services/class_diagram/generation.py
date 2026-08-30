"""유스케이스별 operation 제안 뒤 완성 skeleton에서 협업을 구체화한다."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.config import settings
from app.design.schemas.class_model import BCEModel, Collaboration
from app.design.services.class_diagram import collaboration, operations
from app.design.services.class_diagram.cache import (
    AcceptedUnitCache,
    accepted_unit_key,
    configured_provider_identity,
    record_cache_outcome,
)
from app.design.services.class_diagram.models import (
    AcceptedFragment,
    AcceptedInventory,
    Collision,
    DataTypeCollision,
)
from app.design.services.class_diagram.proposals import (
    CallPlanProposal,
    CombinedUnitProposal,
    OperationFragment,
)
from app.design.services.class_diagram.scenario import (
    ExecutionGroup,
    ScenarioIndex,
    UseCase,
    id_key,
)
from app.design.services.class_diagram.validation.collaboration import (
    COLLABORATION_CHECKS,
    CollaborationContext,
)
from app.design.services.class_diagram.validation.model import validate_class_model
from app.design.services.common.structured import bind_context, parse_structured
from app.validation import run_checks, stable_digest

_COMBINED_PROMPT = operations.operation_prompt() + """

Also return a flat call forest for this complete use case. Refer to operations
only as ClassName.methodName. Each supplied actorEntry creates exactly one
Boundary root in the same order; no other root is allowed. Each non-root uses
the one-based position of an earlier call in the latest root as parentCallIndex.
Ordinary results return through the existing call chain. Use a Control-to-
Boundary call only when the scenario explicitly requires the system to initiate
a separate interaction with an external actor or system through that Boundary,
such as an asynchronous notification, and parent it to Control. The Boundary
class used by a root must not appear again inside that root. Entities may
collaborate with other Entities, but do not call a Control or Boundary directly.
The same operation may be called in several roots. Cover each actor entry's step
range through Boundary to Control and, when needed, Entity. If actorEntries is
empty, return no calls; its operations can be used by an including use case.
"""


def _same_boundary_response_operations(raw: dict[str, Any]) -> set[str]:
    """결합 call forest가 최초 Boundary로 되돌아간 operation을 찾는다.

    같은 root 안에서 최초 Boundary 클래스가 다시 receiver로 등장하면 현재 요청의
    결과를 별도 호출로 표현한 것이다. 다른 Boundary 클래스 호출은 외부 시스템과의
    상호작용일 수 있으므로 그대로 둔다.
    """

    result: set[str] = set()
    root_owner = ""
    for call in raw.get("calls") or []:
        if not isinstance(call, dict):
            continue
        operation_ref = str(call.get("operationRef") or "")
        owner = operation_ref.partition(".")[0]
        if call.get("parentCallIndex") is None:
            root_owner = owner
        elif root_owner and owner == root_owner:
            result.add(operation_ref)
    return result


def _groups(index: ScenarioIndex, use_case: UseCase) -> tuple[ExecutionGroup, ...]:
    return tuple(group for group in index.groups if group.use_case_id == use_case.id)


def _payload(
    index: ScenarioIndex,
    inventory: AcceptedInventory,
    use_case: UseCase,
    *,
    reserved: list[dict[str, Any]],
    reserved_types: list[dict[str, Any]],
    previous: dict[str, Any] | None = None,
    issue: str = "",
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    payload = operations.operation_payload(
        index,
        inventory,
        use_case,
        reserved=reserved,
        reserved_types=reserved_types,
        allowed_step_ids=tuple(step.id for step in use_case.steps),
    )
    payload["actorEntries"] = [
        {
            "actorStepRef": group.actor_step,
            "requiredStepRefs": list(group.required_step_ids),
        }
        for group in _groups(index, use_case)
    ]
    if issue:
        payload.update({
            "task": "Return a full replacement for this use-case unit.",
            "previousCombined": previous,
            "finding": issue,
            "repairHistory": history or [],
        })
    return payload


def _propose_unit(
    index: ScenarioIndex,
    inventory: AcceptedInventory,
    use_case: UseCase,
    *,
    reserved: list[dict[str, Any]],
    reserved_types: list[dict[str, Any]],
    previous: dict[str, Any] | None = None,
    initial_issue: str = "",
) -> tuple[AcceptedFragment, dict[str, Any]]:
    """operation 검사를 통과할 때까지 한 유스케이스 제안만 전체 교체한다."""

    issue = initial_issue
    history: list[dict[str, str]] = []
    seen_states: set[str] = set()
    prior = previous
    while True:
        prompt_payload = _payload(
            index,
            inventory,
            use_case,
            reserved=reserved,
            reserved_types=reserved_types,
            previous=prior,
            issue=issue,
            history=history,
        )
        parsed = parse_structured(
            [
                {"role": "system", "content": _COMBINED_PROMPT},
                {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
            ],
            CombinedUnitProposal,
            reasoning_effort=operations.operation_reasoning_effort(),
            max_completion_tokens=operations.operation_max_completion_tokens(),
            operation="InteractionCombinedUnitRepair" if issue else "InteractionCombinedUnit",
            metadata={
                "useCaseId": use_case.id,
                "executionSlice": use_case.id,
                "candidateCount": len(prompt_payload["fixedClasses"]),
            },
        )
        raw = CombinedUnitProposal.model_validate(parsed).model_dump(by_alias=True)
        try:
            fragment = operations.normalize_operation_fragment(
                raw["fragment"],
                index,
                inventory,
                use_case,
                reserved_types=reserved_types,
                allowed_step_ids=tuple(step.id for step in use_case.steps),
                same_boundary_response_operations=(
                    _same_boundary_response_operations(raw)
                ),
            )
            fragment = operations.validate_operation_fragment(
                fragment,
                index,
                inventory,
                use_case,
                reserved_types=reserved_types,
                allowed_step_ids=tuple(step.id for step in use_case.steps),
            )
            return fragment, raw
        except (ValueError, TypeError) as error:
            issue = f"{type(error).__name__}: {error}"
            candidate_digest = stable_digest(raw)
            state_digest = stable_digest({
                "candidate": candidate_digest, "finding": issue,
            })
            repeated = state_digest in seen_states
            seen_states.add(state_digest)
            history.append({"candidateDigest": candidate_digest, "error": issue})
            if repeated:
                issue += (
                    "\nThe same candidate and finding repeated. Return a materially "
                    "different complete operation fragment and call forest."
                )
            prior = raw


def _catalog(model: BCEModel) -> dict[str, str]:
    return {
        f"{owner.class_name}.{operation.name}": operation.operation_id
        for owner in model.Classes for operation in owner.operations
    }


def _resolved_plan(raw: dict[str, Any], model: BCEModel) -> CallPlanProposal:
    """정규화로 사라진 call을 빼고 자식을 가장 가까운 남은 조상에 연결한다."""

    proposal = CombinedUnitProposal.model_validate(raw)
    catalog = _catalog(model)
    raw_refs = {
        f"{class_set.class_name}.{operation.name}"
        for class_set in proposal.fragment.Classes
        for operation in class_set.operations
    }
    unknown = {
        call.operation_ref for call in proposal.calls
        if call.operation_ref not in raw_refs and call.operation_ref not in catalog
    }
    if unknown:
        raise ValueError("unknown operationRef: " + ", ".join(sorted(unknown)))
    calls = proposal.calls
    kept = [
        position for position, call in enumerate(calls, start=1)
        if call.operation_ref in catalog
    ]
    positions = {old: new for new, old in enumerate(kept, start=1)}
    resolved: list[dict[str, Any]] = []
    for old in kept:
        call = calls[old - 1]
        parent = call.parent_call_index
        visited: set[int] = set()
        while parent is not None and parent not in positions:
            if parent < 1 or parent >= old or parent in visited:
                raise ValueError("parentCallIndex must reference an earlier call")
            visited.add(parent)
            parent = calls[parent - 1].parent_call_index
        resolved.append({
            "receiverOperationId": catalog[call.operation_ref],
            "parentCallIndex": positions.get(parent) if parent is not None else None,
        })
    return CallPlanProposal.model_validate({"calls": resolved})


def _materialize_use_case(
    index: ScenarioIndex,
    skeleton: BCEModel,
    use_case: UseCase,
    raw: dict[str, Any],
) -> Collaboration:
    """임시 calls를 쓰고, 실패하면 operation을 보존한 call-plan 수리를 시작한다."""

    try:
        return collaboration.materialize(
            index, skeleton, use_case, _resolved_plan(raw, skeleton),
        )
    except ValueError as error:
        return collaboration.process_use_case(
            index,
            skeleton,
            use_case,
            directive=(
                "Preserve every operation and replace only the call plan. "
                f"Resolve this exact issue: {type(error).__name__}: {error}"
            ),
        )


def _collaboration_valid(
    index: ScenarioIndex,
    skeleton: BCEModel,
    use_case: UseCase,
    value: Collaboration,
) -> bool:
    report = run_checks(
        COLLABORATION_CHECKS,
        value.model_dump(by_alias=True),
        CollaborationContext(index, skeleton.model_dump(by_alias=True), use_case),
    )
    return not report.errors and not report.findings


def _build_uncached(index: ScenarioIndex, inventory: AcceptedInventory) -> BCEModel:
    use_cases = sorted(index.use_cases, key=lambda item: id_key(item.id))
    inventory_model = operations.compose_operation_units(inventory, [])
    reserved: list[dict[str, Any]] = []
    reserved_types = [item.model_dump(by_alias=True) for item in inventory_model.DataTypes]
    # 1단계: 모든 유스케이스는 같은 inventory snapshot을 보고 설정된 수만큼 병렬 제안한다.
    workers = max(1, min(
        len(use_cases) or 1,
        int(getattr(settings, "design_class_behavior_parallelism", 4)),
    ))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                bind_context(_propose_unit),
                index,
                inventory,
                use_case,
                reserved=reserved,
                reserved_types=reserved_types,
            )
            for use_case in use_cases
        ]
        proposed = [future.result() for future in futures]
    committed: list[AcceptedFragment] = []
    raw_by_use_case: dict[str, dict[str, Any]] = {}
    for position, (use_case, (fragment, raw)) in enumerate(
        zip(use_cases, proposed, strict=True), start=1,
    ):
        collision_states: set[str] = set()
        while True:
            try:
                preview = operations.compose_operation_units(inventory, [*committed, fragment])
                break
            except (Collision, DataTypeCollision) as error:
                snapshot = operations.compose_operation_units(inventory, committed)
                issue = f"{type(error).__name__}: {error}"
                state = stable_digest({"candidate": raw, "finding": issue})
                if state in collision_states:
                    issue += (
                        "\nThe same colliding candidate repeated. Return a materially "
                        "different complete unit."
                    )
                collision_states.add(state)
                fragment, raw = _propose_unit(
                    index,
                    inventory,
                    use_case,
                    reserved=operations.reserved_operations(snapshot),
                    reserved_types=[
                        item.model_dump(by_alias=True) for item in snapshot.DataTypes
                    ],
                    previous=raw,
                    initial_issue=issue,
                )
        committed.append(fragment)
        raw_by_use_case[use_case.id] = raw
        operations.emit_preview(
            preview.model_dump(by_alias=True),
            "operations", use_case.id, position + 1, len(use_cases) + 1,
        )
    skeleton = operations.compose_operation_units(inventory, committed, final=True)

    # 2단계: 완성된 operation catalog에서 provisional calls를 구체화한다. actor 없는
    # include는 독립 collaboration을 만들지 않고 부모 수리에서 후보 operation으로 쓰인다.
    accepted: dict[str, Collaboration] = {}
    standalone = [use_case for use_case in use_cases if _groups(index, use_case)]
    while len(accepted) < len(standalone):
        for use_case in standalone:
            current = accepted.get(use_case.id)
            if current is not None and _collaboration_valid(
                index, skeleton, use_case, current,
            ):
                continue
            try:
                value = _materialize_use_case(
                    index, skeleton, use_case, raw_by_use_case[use_case.id],
                )
            except collaboration.CombinedReplacementRequired as signal:
                # 같은 call-plan 상태가 반복되면 현재 유스케이스의 operation+calls만
                # 다시 받고, 이미 수락된 다른 협업은 새 skeleton에서 재검사한다.
                unit_index = use_cases.index(use_case)
                others = [
                    fragment for position, fragment in enumerate(committed)
                    if position != unit_index
                ]
                snapshot = operations.compose_operation_units(inventory, others)
                previous = raw_by_use_case[use_case.id]
                issue = signal.issue
                while True:
                    fragment, raw = _propose_unit(
                        index,
                        inventory,
                        use_case,
                        reserved=operations.reserved_operations(snapshot),
                        reserved_types=[
                            item.model_dump(by_alias=True) for item in snapshot.DataTypes
                        ],
                        previous=previous,
                        initial_issue=issue,
                    )
                    candidate_fragments = list(committed)
                    candidate_fragments[unit_index] = fragment
                    try:
                        skeleton = operations.compose_operation_units(
                            inventory, candidate_fragments, final=True,
                        )
                        break
                    except (Collision, DataTypeCollision) as error:
                        previous = raw
                        issue = f"{type(error).__name__}: {error}"
                committed = candidate_fragments
                raw_by_use_case[use_case.id] = raw
                accepted = {
                    owner: collaboration_value
                    for owner, collaboration_value in accepted.items()
                    if _collaboration_valid(
                        index,
                        skeleton,
                        index.use_case(owner),
                        collaboration_value,
                    )
                }
                break
            accepted[use_case.id] = value
            ordered_accepted = [
                accepted[item.id] for item in standalone if item.id in accepted
            ]
            operations.emit_preview(
                {
                    **skeleton.model_dump(by_alias=True),
                    "Collaborations": [
                        item.model_dump(by_alias=True) for item in ordered_accepted
                    ],
                },
                "collaborations", use_case.id, len(accepted), len(standalone),
            )
        else:
            break
    return BCEModel.model_validate({
        **skeleton.model_dump(by_alias=True),
        "Collaborations": [
            accepted[use_case.id] for use_case in standalone
        ],
    })


def _previous_combined_unit(
    fragment: AcceptedFragment,
    model: BCEModel,
    plan: CallPlanProposal,
) -> dict[str, Any]:
    """분리 수리에서 반복된 call plan을 결합 수리 입력으로 되돌린다."""

    operation_refs = {
        operation.operation_id: f"{owner.class_name}.{operation.name}"
        for owner in model.Classes for operation in owner.operations
    }
    return {
        "fragment": fragment.as_payload(),
        "calls": [
            {
                "operationRef": operation_refs[call.receiver_operation_id],
                "parentCallIndex": call.parent_call_index,
            }
            for call in plan.calls
        ],
    }


def replace_use_case_unit(
    index: ScenarioIndex,
    current: BCEModel,
    use_case: UseCase,
    signal: collaboration.CombinedReplacementRequired,
) -> tuple[BCEModel, Collaboration]:
    """반복된 call-plan 수리를 operation과 calls의 결합 교체로 넓힌다."""

    from app.design.services.class_diagram.feedback import (
        fragments_from_model,
        inventory_from_model,
    )

    inventory = inventory_from_model(current)
    fragments = fragments_from_model(index, current)
    fragment = fragments.get(use_case.id)
    if fragment is None:
        raise ValueError(f"use case has no operation fragment: {use_case.id}")
    others = {key: value for key, value in fragments.items() if key != use_case.id}
    snapshot = operations.compose_fragments(inventory, others)
    previous = _previous_combined_unit(fragment, current, signal.previous_plan)
    issue = signal.issue
    collision_states: set[str] = set()
    while True:
        replacement, raw = _propose_unit(
            index,
            inventory,
            use_case,
            reserved=operations.reserved_operations(snapshot),
            reserved_types=[
                item.model_dump(by_alias=True) for item in snapshot.DataTypes
            ],
            previous=previous,
            initial_issue=issue,
        )
        candidate_fragments = {**others, use_case.id: replacement}
        try:
            skeleton = operations.compose_fragments(inventory, candidate_fragments)
        except (Collision, DataTypeCollision) as error:
            issue = f"{type(error).__name__}: {error}"
            state = stable_digest({"candidate": raw, "finding": issue})
            if state in collision_states:
                issue += (
                    "\nThe same colliding candidate repeated. Return a materially "
                    "different complete unit."
                )
            collision_states.add(state)
            previous = raw
            continue
        operations.emit_preview(
            skeleton.model_dump(by_alias=True),
            "operations",
            use_case.id,
            len(candidate_fragments) + 1,
            len(index.use_cases) + 1,
        )
        try:
            accepted = _materialize_use_case(index, skeleton, use_case, raw)
        except collaboration.CombinedReplacementRequired as repeated:
            previous = raw
            issue = repeated.issue
            continue
        return skeleton, accepted


def _model_cache_key(index: ScenarioIndex, inventory: AcceptedInventory) -> str:
    return accepted_unit_key(
        "complete-class-model",
        unit_slice=index.raw,
        inventory=inventory.as_payload(),
        feedback={},
        prompt=_COMBINED_PROMPT,
        schema=BCEModel,
        provider=configured_provider_identity(settings.base_url),
        model=settings.model,
        seed=settings.seed,
        temperature=settings.temperature,
        reasoning_effort=operations.operation_reasoning_effort(),
        max_completion_tokens=operations.operation_max_completion_tokens(),
        extra={
            "combinedProposalSchema": CombinedUnitProposal.model_json_schema(),
            "operationFragmentSchema": OperationFragment.model_json_schema(),
            "callPlanPrompt": collaboration.CALL_PLAN_PROMPT,
            "callPlanCap": collaboration.call_plan_max_completion_tokens(),
            "bindingPrompt": collaboration.BINDING_PROMPT,
            "version": 3,
        },
    )


def build_model(
    index: ScenarioIndex,
    inventory: AcceptedInventory,
    *,
    cache: AcceptedUnitCache | None = None,
) -> BCEModel:
    """두 단계 생성 결과 전체만 cache하고 hit에서도 최종 검사를 다시 실행한다."""

    if cache is None:
        record_cache_outcome(None, operation="InteractionClassModel", unit="class-model")
        model = _build_uncached(index, inventory)
    else:
        result = cache.get_or_compute(
            _model_cache_key(index, inventory),
            lambda: _build_uncached(index, inventory).model_dump(by_alias=True),
        )
        record_cache_outcome(result, operation="InteractionClassModel", unit="class-model")
        model = BCEModel.model_validate(result.value)
        if result.status in {"hit", "coalesced"}:
            # whole-model cache도 operation 수락 경계를 건너뛰지 않는다. 저장 모델에서
            # 유스케이스 fragment를 복원해 schema·step/type 검사를 다시 실행한다.
            from app.design.services.class_diagram.feedback import fragments_from_model

            fragments = fragments_from_model(index, model)
            reserved_types = [item.model_dump(by_alias=True) for item in model.DataTypes]
            for use_case in index.use_cases:
                fragment = fragments.get(use_case.id)
                if fragment is not None:
                    operations.validate_operation_fragment(
                        fragment,
                        index,
                        inventory,
                        use_case,
                        reserved_types=reserved_types,
                        allowed_step_ids=tuple(step.id for step in use_case.steps),
                    )
    report = validate_class_model(model, index)
    if report.errors or report.findings:
        details = [
            *report.errors,
            *(
                f"{finding.rule_id} {finding.location}: {finding.message}"
                for finding in report.findings
            ),
        ]
        raise ValueError("class model is invalid: " + "; ".join(details))
    return model


__all__ = ["build_model", "replace_use_case_unit"]
