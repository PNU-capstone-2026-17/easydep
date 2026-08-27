"""한 실행 그룹의 유한 호출 계획과 parameter provenance를 수락한다.

입력은 ``ScenarioIndex``, 연산이 확정된 ``BCEModel``과 정확히 한 ``ExecutionGroup``이다.
LLM은 현재 범위의 operation ID와 앞선 부모 index만 선택한다. 코드는 canonical call ID,
step provenance와 parameter source 후보를 투영하고 ``Collaboration``을 검증한다.

부작용은 호출 계획 LLM과, 후보가 복수일 때만 실행되는 binding 선택 LLM이다. 새 클래스,
operation 또는 타입을 만들지 않으며 저장소·graph state를 직접 읽지 않는다.
"""
from __future__ import annotations

import json
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, create_model

from app.config import settings
from app.design.schemas.class_model import BCEModel, Collaboration, canonical_call_id
from app.design.services.class_diagram.models import CollaborationResult
from app.design.services.class_diagram.proposals import (
    CallPlanProposal,
    ProposedCall,
)
from app.design.services.class_diagram.scenario import (
    ExecutionGroup,
    ScenarioIndex,
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
from app.validation import Finding, run_checks

CALL_PLAN_PROMPT = """
Build the ordered call tree for exactly one execution group. Select only the
supplied receiverOperationId values. Step references are projected from the
selected operation contracts; do not return them. Return no classes, methods,
call ids, values, or argument bindings.

The first call has no parent. Every later call names the one-based index of an
earlier caller. An actor group enters through Boundary and delegates to
Control. Control delegates persistent-state work to Entity. Boundary never
calls Entity directly. Cover every requiredStepRef, including included-flow
steps, without repeating calls merely to repeat a step label.
""".strip()


BINDING_PROMPT = """
Select one sourceRef for each supplied choice. The response schema restricts
every field to that parameter's finite candidates. Resolve all fields and
return no explanation.
Prefer the source whose name and role best match the receiver parameter; do
not invent values or identifiers.
""".strip()


def _finite_schema(name: str, **fields: Any) -> type[BaseModel]:
    """런타임 Pydantic 스키마의 동적 타입을 이 경계에서만 좁힌다.

    ``create_model``은 전달된 유한 선택지로 클래스를 생성하므로 정적으로는
    반환 하위 클래스를 알 수 없다. 이 한 곳의 cast가 그 동적 경계를 격리한다.
    """
    return cast(type[BaseModel], create_model(name, **fields))


def _finding_text(findings: tuple[Finding, ...]) -> list[str]:
    return [
        f"{finding.location}: {finding.message}" if finding.location else finding.message
        for finding in findings
    ]


def _group_operations(
    model: dict[str, Any], group: ExecutionGroup,
) -> dict[str, dict[str, Any]]:
    """실행 그룹이 추적하는 유스케이스에 허용된 operation만 catalog로 좁힌다."""
    allowed = set(group.trace_use_case_ids)
    return {
        operation_id: operation
        for operation_id, operation in operation_catalog(model).items()
        if allowed & {
            text(use_case_id)
            for class_item in model.get("Classes") or [] if isinstance(class_item, dict)
            if text(class_item.get("className")) == operation["className"]
            for use_case_id in class_item.get("use_case_ids") or []
        }
    }


def _group_payload(
    index: ScenarioIndex, model: dict[str, Any], group: ExecutionGroup,
) -> dict[str, Any]:
    """LLM이 호출 순서만 고를 수 있는 execution-group payload를 만든다.

    ``receiverOperations``에 없는 ID는 동적 응답 schema에도 들어가지 않는다. 단계 문장은
    판단 근거로 제공하지만 ``stepRefs``는 응답에서 받지 않고 선택된 operation에서 투영한다.
    """
    operations = _group_operations(model, group)
    steps = {
        step.id: step for use_case_id in group.trace_use_case_ids
        for step in index.use_case(use_case_id).steps
    }
    return {
        "collaborationId": group.id,
        "entryActor": group.entry_actor,
        "actorStepRef": group.actor_step,
        "requiredStepRefs": list(group.required_step_ids),
        "steps": [
            {"id": step_id, "sentence": steps[step_id].sentence}
            for step_id in group.required_step_ids if step_id in steps
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
            if set(operation.get("stepRefs") or []) & set(group.required_step_ids)
        ],
    }


def _propose_call_plan(
    index: ScenarioIndex,
    model: dict[str, Any],
    group: ExecutionGroup,
    *,
    previous: CallPlanProposal | None = None,
    finding: str = "",
) -> CallPlanProposal:
    """유한 operation ID enum으로 한 execution group의 호출 계획을 요청한다.

    최초 호출은 ``previous``와 ``finding``이 없다. materialize 또는 validation 실패 뒤에는
    이전 전체 계획과 오류를 함께 보내 같은 group의 full replacement를 한 번 요청한다.
    """
    # 1. 현재 group의 단계와 수락 operation만 payload에 포함한다.
    payload = _group_payload(index, model, group)
    if previous is not None:
        payload["previousPlan"] = previous.model_dump(by_alias=True)
    if finding:
        payload["task"] = "Return one full repaired call plan and resolve the finding."
        payload["finding"] = finding
    operation_ids = tuple(item["operationId"] for item in payload["receiverOperations"])
    if not operation_ids:
        raise ValueError(f"execution group has no receiver operations: {group.id}")
    # 2. receiverOperationId를 실제 후보 Literal로 만든다. 자연어 prompt만으로 목록 밖
    # 선택을 막지 않고 구조화 응답 파싱 단계에서 거부한다.
    finite_call = _finite_schema(
        "FiniteProposedCall",
        __base__=ProposedCall,
        receiver_operation_id=(
            Literal.__getitem__(operation_ids),
            Field(alias="receiverOperationId"),
        ),
    )
    finite_plan = _finite_schema(
        "FiniteCallPlan",
        __base__=CallPlanProposal,
        calls=(list[finite_call], Field(min_length=1, max_length=len(operation_ids))),  # type: ignore[valid-type]
    )
    # 3. LLM은 operation과 earlier parent index만 반환한다. call ID, binding과 stepRefs는
    # materialize 단계의 결정론적 책임이다.
    parsed = parse_structured(
        [
            {"role": "system", "content": CALL_PLAN_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        finite_plan,
        reasoning_effort=settings.design_reasoning_effort,
        max_completion_tokens=settings.design_class_collaboration_max_completion_tokens,
        operation="InteractionCallPlanRepair" if finding else "InteractionCallPlan",
        metadata={"collaborationGroup": group.id},
    )
    return CallPlanProposal.model_validate(
        finite_plan.model_validate(parsed).model_dump(by_alias=True),
    )


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
    index: ScenarioIndex,
    group: ExecutionGroup,
    calls: list[dict[str, Any]],
    call_index: int,
    parameter: dict[str, Any],
    operations: dict[str, dict[str, Any]],
) -> list[str]:
    name = text(parameter.get("name"))
    target_type = text(parameter.get("type"))
    if call_index == 0 and group.actor_step:
        return [f"{group.actor_step}#{name}"]
    if call_index == 0:
        return [
            f"{ref}#{name}" for use_case_id in group.trace_use_case_ids
            for ref in index.use_case(use_case_id).precondition_refs
        ]
    fields_by_type = structured_field_types(model)
    candidates: list[str] = []
    named_sources: dict[str, list[tuple[str, str]]] = {}

    def add_named(source_name: str, source_type: str, source_ref: str) -> None:
        named_sources.setdefault(source_name.casefold(), []).append((source_type, source_ref))

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
                if field_path.casefold() == name.casefold() and types_compatible(projected, target_type):
                    candidates.append(field_ref)
        return_type = text(operation.get("returnType"))
        if return_type.casefold() != "void" and types_compatible(return_type, target_type):
            candidates.append(f"{ancestor['callId']}#result")
        elif types_compatible(optional_inner_type(return_type), target_type):
            candidates.append(f"{ancestor['callId']}#result.unwrap")
        for field_path in fields_by_type.get(return_type, {}):
            projected = projected_field_type(return_type, field_path, fields_by_type)
            field_ref = f"{ancestor['callId']}#result.{field_path}"
            add_named(field_path, projected, field_ref)
            if field_path.casefold() == name.casefold() and types_compatible(projected, target_type):
                candidates.append(field_ref)
    for earlier in reversed(calls[:call_index]):
        if text(earlier.get("callId")) in ancestor_ids:
            continue
        operation = operations[text(earlier.get("receiverOperationId"))]
        return_type = text(operation.get("returnType"))
        if return_type.casefold() != "void" and types_compatible(return_type, target_type):
            candidates.append(f"{earlier['callId']}#result")
        elif types_compatible(optional_inner_type(return_type), target_type):
            candidates.append(f"{earlier['callId']}#result.unwrap")
        for field_path in fields_by_type.get(return_type, {}):
            projected = projected_field_type(return_type, field_path, fields_by_type)
            field_ref = f"{earlier['callId']}#result.{field_path}"
            add_named(field_path, projected, field_ref)
            if field_path.casefold() == name.casefold() and types_compatible(projected, target_type):
                candidates.append(field_ref)
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
    group: ExecutionGroup,
    ambiguous: dict[str, list[str]],
) -> dict[str, str]:
    """복수의 타입 호환 source가 있는 parameter만 LLM 선택으로 해소한다.

    Args:
        group: 관측 metadata와 collaboration ID를 제공하는 실행 그룹이다.
        ambiguous: ``callId#parameter``별 실제 유한 source 후보 목록이다.

    Returns:
        각 parameter 위치를 schema가 허용한 정확한 source 문자열에 연결한 mapping이다.

    Raises:
        ValueError: 후보 목록이 비어 동적 Literal schema를 만들 수 없는 경우다.

    Notes:
        후보가 0개면 상위 materialize가 실패하고, 1개면 코드가 직접 선택한다. 따라서 이
        함수는 후보가 2개 이상인 field에 대해서만 호출되어 불필요한 LLM 판단을 만들지 않는다.
    """
    fields: dict[str, tuple[Any, Any]] = {}
    choices: list[dict[str, Any]] = []
    locations: dict[str, str] = {}
    for position, (parameter, candidates) in enumerate(sorted(ambiguous.items()), start=1):
        field_name = f"choice{position}"
        finite_values = tuple(dict.fromkeys(candidates))
        if not finite_values:
            raise ValueError(f"binding candidates are empty for {parameter}")
        fields[field_name] = (
            Literal.__getitem__(finite_values), Field(description=f"Source for {parameter}"),
        )
        choices.append({"choice": field_name, "parameter": parameter, "candidates": list(finite_values)})
        locations[field_name] = parameter
    # 각 응답 field의 Literal이 서로 다르다. 한 parameter의 유효 source를 다른 parameter에
    # 복사하는 응답도 Pydantic 단계에서 거부된다.
    selection_schema = _finite_schema(
        "FiniteBindingChoices", __config__=ConfigDict(extra="forbid"), **fields,
    )
    parsed = parse_structured(
        [
            {"role": "system", "content": BINDING_PROMPT},
            {"role": "user", "content": json.dumps({
                "collaborationId": group.id, "choices": choices,
            }, ensure_ascii=False)},
        ],
        selection_schema,
        reasoning_effort="low",
        max_completion_tokens=min(settings.design_class_collaboration_max_completion_tokens, 2048),
        operation="InteractionBindingSelection",
        metadata={"collaborationGroup": group.id},
    )
    selected = selection_schema.model_validate(parsed).model_dump()
    return {locations[field_name]: source_ref for field_name, source_ref in selected.items()}


def _materialize(
    index: ScenarioIndex,
    model: dict[str, Any],
    group: ExecutionGroup,
    plan: CallPlanProposal,
) -> dict[str, Any]:
    """LLM call plan을 canonical 호출·binding이 포함된 collaboration으로 만든다.

    정상 예에서 두 번째 call의 ``parentCallIndex=1``은 첫 call의 canonical ID로 바뀐다.
    실패 예에서 현재보다 뒤의 index, Boundary→Entity 직접 호출, 빈 source 후보는 즉시
    ``ValueError``가 되며 임의 fallback call이나 literal을 만들지 않는다.
    """
    operations = _group_operations(model, group)
    calls: list[dict[str, Any]] = []
    allowed = set(group.required_step_ids)
    # 1. 응답 index를 안정적인 call ID와 step provenance로 투영한다.
    for position, proposed in enumerate(plan.calls, start=1):
        operation_id = text(proposed.receiver_operation_id)
        if operation_id not in operations:
            raise ValueError(f"unknown receiverOperationId: {operation_id}")
        parent_index = proposed.parent_call_index
        if position == 1 and parent_index is not None:
            raise ValueError("the first call cannot have a parent")
        if position > 1 and parent_index is None:
            raise ValueError("every delegated call requires an earlier parent")
        if parent_index is not None and parent_index >= position:
            raise ValueError("parentCallIndex must reference an earlier call")
        operation = operations[operation_id]
        declared = {text(ref) for ref in operation.get("stepRefs") or []}
        refs = [ref for ref in group.required_step_ids if ref in declared and ref in allowed]
        if not refs:
            raise ValueError("selected operation has no declared step in this group")
        calls.append({
            "callId": canonical_call_id(group.id, position),
            "parentCallId": canonical_call_id(group.id, parent_index) if parent_index else None,
            "receiverOperationId": operation_id,
            "stepRefs": refs,
            "argumentBindings": [],
        })
    # 2. binding 후보를 계산하기 전에 호출 방향 자체가 BCE 규칙을 지키는지 확인한다.
    # 잘못된 트리를 값 후보 선택으로 덮으려 하면 provenance 오류가 연쇄적으로 늘어난다.
    for call in calls[1:]:
        parent = next(item for item in calls if item["callId"] == call["parentCallId"])
        source = operations[parent["receiverOperationId"]]["stereotype"]
        target = operations[call["receiverOperationId"]]["stereotype"]
        if (source, target) in {
            ("boundary", "boundary"), ("boundary", "entity"),
            ("entity", "boundary"), ("entity", "control"),
        }:
            raise ValueError(f"BCE communication is invalid: {source} -> {target}")
    # 3. 각 parameter의 source 후보를 이전 call과 ancestor 범위에서만 계산한다. 미래 call의
    # return은 타입이 맞아도 인과적으로 사용할 수 없다.
    ambiguous: dict[str, list[str]] = {}
    for call_index, call in enumerate(calls):
        operation = operations[call["receiverOperationId"]]
        for parameter in operation.get("parameters") or []:
            if not isinstance(parameter, dict):
                raise TypeError("operation parameter must be an object")
            candidates = _binding_candidates(model, index, group, calls, call_index, parameter, operations)
            location = f"{call['callId']}#{text(parameter.get('name'))}"
            if not candidates:
                raise ValueError(f"no finite source for {location}")
            if len(candidates) > 1:
                ambiguous[location] = candidates
            else:
                call["argumentBindings"].append({
                    "parameter": text(parameter.get("name")), "sourceRef": candidates[0],
                })
    # 4. 유일 후보는 코드가 이미 기록했고 복수 후보만 한 번의 저비용 LLM 호출로 선택한다.
    selected = select_ambiguous_bindings(group, ambiguous) if ambiguous else {}
    for call in calls:
        operation = operations[call["receiverOperationId"]]
        existing = {text(binding.get("parameter")) for binding in call["argumentBindings"]}
        for parameter in operation.get("parameters") or []:
            name = text(parameter.get("name"))
            if name not in existing:
                call["argumentBindings"].append({
                    "parameter": name, "sourceRef": selected[f"{call['callId']}#{name}"],
                })
    # 5. 모든 deterministic field를 합친 뒤 collaboration rule 전체를 통과해야 typed
    # service 경계로 나갈 수 있다.
    collaboration = {
        "collaborationId": group.id,
        "useCaseIds": list(group.trace_use_case_ids),
        "entryActor": group.entry_actor,
        "calls": calls,
    }
    report = run_checks(
        COLLABORATION_CHECKS, collaboration, CollaborationContext(index, model, group),
    )
    if report.errors or report.findings:
        raise ValueError("; ".join([*report.errors, *_finding_text(report.findings)]))
    return collaboration


def propose_call_plan(
    index: ScenarioIndex,
    model: BCEModel,
    group: ExecutionGroup,
    *,
    previous: CallPlanProposal | None = None,
    finding: str = "",
) -> CallPlanProposal:
    """수락된 BCE 모델에서 한 실행 그룹의 유한 호출 계획을 제안한다.

    Args:
        index: 단계·include 범위와 값 원천을 제공하는 시나리오 인덱스다.
        model: receiver operation이 모두 수락된 BCE skeleton이다.
        group: 이번 계획이 정확히 커버할 execution group이다.
        previous: 한정 repair가 참고할 이전 전체 계획이다.
        finding: 이전 계획·materialize 실패를 설명하는 영어 런타임 메시지다.

    Returns:
        실제 operation ID와 부모 index만 포함한 ``CallPlanProposal``이다.
    """
    return _propose_call_plan(
        index,
        model.model_dump(by_alias=True),
        group,
        previous=previous,
        finding=finding,
    )


def materialize(
    index: ScenarioIndex,
    model: BCEModel,
    group: ExecutionGroup,
    plan: CallPlanProposal,
) -> Collaboration:
    """호출 계획을 canonical ID·binding이 포함된 검증된 협업으로 구체화한다.

    Args:
        index: 단계와 provenance 원천의 기준이다.
        model: operation과 구조 타입 catalog의 기준이다.
        group: collaboration의 정확한 소유 실행 그룹이다.
        plan: 유한 schema를 통과한 호출 계획이다.

    Returns:
        모든 collaboration rule을 통과한 저장 ``Collaboration``이다.
    """
    return Collaboration.model_validate(_materialize(
        index, model.model_dump(by_alias=True), group, plan,
    ))


def process_group(
    index: ScenarioIndex,
    model: BCEModel,
    group: ExecutionGroup,
    directive: str = "",
) -> CollaborationResult:
    """한 실행 그룹을 제안·materialize하고 최대 한 번 국소 교체한다.

    Args:
        index: 실행 그룹의 단계와 추적 범위를 제공한다.
        model: 호출 가능한 operation이 수락된 BCE skeleton이다.
        group: 현재 worker가 독점하는 실행 그룹이다.
        directive: collaboration 피드백 또는 상위 repair 지시다.

    Returns:
        수락된 ``Collaboration`` 또는 두 번째 실패의 명시적 issue를 담은 결과다.

    Notes:
        예외를 전역으로 전파하지 않고 실패 결과로 바꾸는 이유는 형제 worker의 성공을
        보존하고 service가 필요한 operation slice만 handoff repair할 수 있게 하기 위해서다.
    """
    plan: CallPlanProposal | None = None
    try:
        plan = propose_call_plan(index, model, group, finding=directive)
        return CollaborationResult(group.id, materialize(index, model, group, plan))
    except Exception as first_error:  # 현재 group의 한 번 교체이며 전역 반복으로 승격하지 않는다.
        try:
            # 이전 계획이 parse되었다면 함께 제공한다. 최초 호출 자체가 실패했으면 None이며,
            # 오류 text만으로 같은 유한 후보 범위에서 새 전체 계획을 요청한다.
            repaired = propose_call_plan(index, model, group, previous=plan, finding=str(first_error))
            return CollaborationResult(group.id, materialize(index, model, group, repaired))
        except Exception as second_error:
            return CollaborationResult(group.id, None, f"{type(second_error).__name__}: {second_error}")


call_plan = propose_call_plan

__all__ = [
    "call_plan",
    "materialize",
    "process_group",
    "propose_call_plan",
    "select_ambiguous_bindings",
]
