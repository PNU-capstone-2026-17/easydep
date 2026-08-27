"""수락된 inventory에서 실행 슬라이스별 BCE 연산 조각을 생성하고 조립한다.

LLM에는 한 ``OperationUnit``의 단계, 고정 클래스·구조 타입과 앞선 wave가 예약한 이름만
전달한다. ``OperationFragment`` 응답은 actor-entry 소유권과 타입을 정규화하고
``OPERATION_CHECKS``를 통과한 뒤에만 ``AcceptedFragment``가 된다.

이 모듈의 부작용은 제한된 LLM 호출, 최대 병렬도 2의 wave 실행과 클래스 미리보기 이벤트
발행이다. collaboration을 만들거나 graph state·저장소를 직접 읽지 않는다.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping, MutableMapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any

from app.config import settings
from app.design import progress as design_progress
from app.design.schemas.class_model import BCEModel, canonical_operation_id
from app.design.services.class_diagram.cache import (
    AcceptedUnitCache,
    accepted_unit_key,
    record_cache_outcome,
)
from app.design.services.class_diagram.inventory import finding_text
from app.design.services.class_diagram.models import (
    AcceptedFragment,
    AcceptedInventory,
    CollaborationResult,
)
from app.design.services.class_diagram.models import (
    Collision as _Collision,
)
from app.design.services.class_diagram.models import (
    DataTypeCollision as _DataTypeCollision,
)
from app.design.services.class_diagram.models import (
    OperationUnit as _OperationUnit,
)
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.proposals import OperationFragment
from app.design.services.class_diagram.scenario import (
    ExecutionGroup,
    ScenarioIndex,
    UseCase,
    id_key,
    text,
)
from app.design.services.class_diagram.type_system import (
    field_type,
    reachable_data_type_names,
    referenced_type_names,
    structure_type_contract,
    structured_field_types,
    types_compatible,
)
from app.design.services.class_diagram.validation import OPERATION_CHECKS, OperationContext
from app.design.services.class_diagram.validation.model import (
    class_name,
    runtime_value_source,
    type_can_default,
)
from app.design.services.common import fields
from app.design.services.common.structured import bind_context, parse_structured
from app.validation import run_checks

logger = logging.getLogger(__name__)


_OPERATION_PROMPT = ("""
Build the complete operation fragment for exactly one execution slice within one use case and the
fixed inventory. Use only listed classes, structural types, and locally declared
DataTypes. Cover every allowedStepRef.
An actor entry is owned by one Boundary operation and delegates to Control.
Persistent state behavior is owned by Entity and called through Control.
Do not emit placeholder operations such as none or noop. Only Boundary owns the
actor entry step; delegated Control and Entity operations own system steps.

Group adjacent specification steps into cohesive reusable operations; do not
create one method per sentence. A Boundary operation may cover both an actor
request and the later result produced by its return. Do not invent display,
notify, or inform methods merely to cover an output sentence. Actor-facing
operations that return stated data or outcomes are non-void. Do not require
unstated generated identifiers, clocks, or defaults as caller inputs.

Operations in reservedOperations already belong to accepted use cases. Reuse
an exact signature or choose a distinct cohesive name; do not overload a name
with another parameter or return signature. DataTypes in reservedDataTypes also
belong to accepted fragments: reuse their exact definition or choose a distinct
name. Return no calls, bindings,
relationships, operation ids, or classes outside the inventory. You may declare
request, command, criteria, summary, detail, result, or export DataTypes used by
this use case's operation signatures. Do not redeclare a fixed type. Declare no
unused local type.

Expose one orchestration operation per Control class rather than public helper
operations or Control self-calls. An Entity may expose distinct read and change
operations only when the flow actually calls both. Select
only the Entity candidates whose persistent state this use case directly reads
or changes. Prefer one local request or criteria valueObject when four or more
cohesive inputs would otherwise expand a Boundary or Control signature.

Design signatures as one closed value flow. Every delegated parameter must be
obtainable from an ancestor operation parameter or from the result of an
earlier completed call. If actor or precondition context is needed downstream,
expose it through the Boundary signature. If later work needs data discovered
by an earlier operation, return that data in a declared result type. Keep
generated clocks, sequence positions, and defaults inside their owning
operation instead of inventing caller inputs.
When an Entity mutation applies actor-supplied data, consume a compatible
upstream request or details value. Do not make that mutation parameterless
merely to evade provenance.
An Entity operation must not accept its own complete Entity type as a
parameter: the Entity is already the receiver. Accept compatible upstream
request fields or create its owned state internally.

A Control operation owns the full contiguous coordination span of the calls it
delegates. Its stepRefs begin no later than any nested Entity or Control call;
do not label a coordinator only with a later persistence or retrieval step
when it must first perform validation or authorization.
""".strip() + "\n\n" + structure_type_contract())


def operation_reasoning_effort() -> str:
    """연산 전용 reasoning 설정이 없던 실행은 기존 정책으로 유지한다."""

    return str(getattr(
        settings,
        "design_class_operation_reasoning_effort",
        settings.design_reasoning_effort,
    ))


def operation_max_completion_tokens() -> int:
    """연산 전용 output cap이 없던 실행은 기존 collaboration cap을 유지한다."""

    return int(getattr(
        settings,
        "design_class_operation_max_completion_tokens",
        settings.design_class_collaboration_max_completion_tokens,
    ))


def _reserved_operations(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "className": class_name(item),
            "operations": list(item.get("operations") or []),
        }
        for item in model.get("Classes") or []
        if isinstance(item, dict) and item.get("operations")
    ]


def _operation_payload(
    index: ScenarioIndex,
    inventory: dict[str, Any],
    use_case: UseCase,
    *,
    previous: dict[str, Any] | None = None,
    findings: list[str] | None = None,
    reserved: list[dict[str, Any]] | None = None,
    reserved_types: list[dict[str, Any]] | None = None,
    allowed_step_ids: tuple[str, ...] = (),
    execution_group_id: str = "",
) -> dict[str, Any]:
    """한 실행 슬라이스의 유한한 연산 선택 공간을 JSON payload로 만든다.

    ``fixedClasses``와 ``fixedDataTypes``는 inventory가 수락한 후보만 담고,
    ``allowedStepRefs``는 현재 unit이 소유할 수 있는 단계만 담는다. ``previousFragment``와
    ``findings``는 최초 실패 뒤 full replacement를 요청할 때만 추가한다.
    """
    source_summary = next((
        item for item in index.raw.get("use_cases") or []
        if isinstance(item, dict) and text(item.get("id")) == use_case.id
    ), {})
    # 원문 시나리오 본문은 executionSlice.steps로만 전달한다. 하지만 goal, actor,
    # pre/postcondition, business rule 같은 use-case 문맥은 operation 설계에 필요하므로
    # specification 전체를 버리지 않고 main/extension flow만 제거한다.
    specification = use_case.specification
    if not settings.design_class_compact_operation_payload:
        summary = deepcopy(source_summary)
        if isinstance(specification, dict):
            summary["specification"] = deepcopy(specification)
    else:
        summary = {
            key: deepcopy(value)
            for key, value in source_summary.items()
            if key not in {"main_scenario", "extensions"}
        }
    if settings.design_class_compact_operation_payload and isinstance(specification, dict):
        compact_specification = {
            key: deepcopy(value)
            for key, value in specification.items()
            if key not in {"main_scenario", "extensions"}
        }
        # raw use-case의 goal·actor 문맥은 top-level에 유지한다. specification 안에만 있던
        # pre/postcondition과 business rule은 아래 compact specification에 한 번만 남긴다.
        summary["specification"] = compact_specification
    scoped_classes = [
        {
            key: value for key, value in item.items()
            if key not in {"useCaseIds", "values"}
        }
        for item in inventory.get("Classes") or []
        if use_case.id in set(item.get("useCaseIds") or [])
    ]
    scoped_types = [
        {
            key: value for key, value in item.items()
            if key not in {"useCaseIds", "identifier"}
        }
        for item in inventory.get("DataTypes") or []
        if use_case.id in set(item.get("useCaseIds") or [])
    ]
    scoped_names = {class_name(item) for item in scoped_classes}
    scoped_reserved = [
        item for item in (reserved or [])
        if text(item.get("className")) in scoped_names
    ]
    allowed = set(allowed_step_ids) or {step.id for step in use_case.steps}
    payload: dict[str, Any] = {
        "useCase": summary,
        "executionSlice": {
            "id": execution_group_id or use_case.id,
            "steps": [
                {
                    "stepRef": step.id,
                    "subject": step.subject,
                    "sentence": step.sentence,
                    "condition": step.condition,
                }
                for step in use_case.steps if step.id in allowed
            ],
        },
        "allowedStepRefs": sorted(allowed, key=id_key),
        "fixedClasses": scoped_classes,
        "fixedDataTypes": scoped_types,
        "reservedOperations": scoped_reserved,
        "reservedDataTypes": list(reserved_types or []),
    }
    if previous is not None:
        payload["previousFragment"] = previous
    if findings:
        payload["task"] = (
            "Return a full replacement for this use-case fragment only. "
            "Preserve valid operations and resolve every finding."
        )
        payload["findings"] = findings
    return payload


def _canonicalize_downstream_input_types(
    candidate: dict[str, Any], inventory: dict[str, Any],
) -> dict[str, Any]:
    """호출할 수 없는 layer별 DTO 대신 근거 있는 upstream DTO를 재사용하도록 정규화한다."""

    for class_set in candidate.get("Classes") or []:
        if not isinstance(class_set, dict):
            continue
        class_set["operations"] = [
            operation
            for operation in class_set.get("operations") or []
            if isinstance(operation, dict)
            and re.sub(
                r"[^a-z0-9]", "", text(operation.get("name")).casefold(),
            ) not in {"none", "noop", "notapplicable"}
        ]
    candidate["Classes"] = [
        class_set for class_set in candidate.get("Classes") or []
        if isinstance(class_set, dict) and class_set.get("operations")
    ]
    local_types = {
        text(item.get("name")): item
        for item in candidate.get("DataTypes") or []
        if isinstance(item, dict) and text(item.get("name"))
    }
    if not local_types:
        return candidate
    fields_by_type = structured_field_types({
        "Classes": inventory.get("Classes") or [],
        "DataTypes": [
            *(inventory.get("DataTypes") or []),
            *(candidate.get("DataTypes") or []),
        ],
    })
    stereotypes = {
        class_name(item): text(item.get("stereotype"))
        for item in inventory.get("Classes") or [] if isinstance(item, dict)
    }
    class_sets = [
        item for item in candidate.get("Classes") or [] if isinstance(item, dict)
    ]

    def parameter_types(allowed: set[str]) -> list[str]:
        return list(dict.fromkeys(
            text(parameter.get("type"))
            for class_set in class_sets
            if stereotypes.get(text(class_set.get("className"))) in allowed
            for operation in class_set.get("operations") or []
            if isinstance(operation, dict)
            for parameter in operation.get("parameters") or []
            if isinstance(parameter, dict) and text(parameter.get("type"))
        ))

    for class_set in class_sets:
        stereotype = stereotypes.get(text(class_set.get("className")), "")
        if stereotype not in {"Control", "Entity"}:
            continue
        allowed = {"Boundary"} if stereotype == "Control" else {"Boundary", "Control"}
        upstream_types = parameter_types(allowed)
        named_upstream: dict[str, set[str]] = {}
        for source_type in upstream_types:
            for name, source_field_type in fields_by_type.get(source_type, {}).items():
                named_upstream.setdefault(name.casefold(), set()).add(source_field_type)
        for operation in class_set.get("operations") or []:
            if not isinstance(operation, dict):
                continue
            for parameter in operation.get("parameters") or []:
                if not isinstance(parameter, dict):
                    continue
                target_type = text(parameter.get("type"))
                target_fields = fields_by_type.get(target_type, {})
                if target_type not in local_types or not target_fields:
                    continue
                fully_derived = all(
                    any(
                        types_compatible(source, expected)
                        for source in named_upstream.get(name.casefold(), set())
                    )
                    or runtime_value_source(expected)
                    or type_can_default(expected)
                    for name, expected in target_fields.items()
                )
                if fully_derived:
                    continue
                replacements: list[tuple[int, str]] = []
                for source_type in upstream_types:
                    source_fields = fields_by_type.get(source_type, {})
                    if not source_fields:
                        continue
                    overlap = sum(
                        1
                        for name, source_type_value in source_fields.items()
                        if name in target_fields
                        and types_compatible(source_type_value, target_fields[name])
                    )
                    if overlap:
                        replacements.append((overlap, source_type))
                if not replacements:
                    continue
                best_size = max(size for size, _source in replacements)
                best = sorted({
                    source for size, source in replacements if size == best_size
                })
                if len(best) == 1:
                    parameter["type"] = best[0]

    referenced = {
        name
        for class_set in class_sets
        for operation in class_set.get("operations") or []
        if isinstance(operation, dict)
        for expression in [
            *(text(parameter.get("type")) for parameter in operation.get("parameters") or []),
            text(operation.get("returnType")),
        ]
        for name in referenced_type_names(expression)
        if name in local_types
    }
    pending = list(referenced)
    while pending:
        owner = pending.pop()
        for raw_field in local_types[owner].get("fields") or []:
            for target in referenced_type_names(field_type(raw_field)):
                if target in local_types and target not in referenced:
                    referenced.add(target)
                    pending.append(target)
    candidate["DataTypes"] = [
        item for item in candidate.get("DataTypes") or []
        if text(item.get("name")) in referenced
    ]
    return candidate


def _canonicalize_step_ownership(
    candidate: dict[str, Any],
    inventory: dict[str, Any],
    actor_entry_refs: set[str],
) -> dict[str, Any]:
    """추가 LLM 호출 없이 actor-entry step의 고정 Boundary 소유 규칙을 투영한다."""

    normalized = deepcopy(candidate)
    stereotypes = {
        class_name(item): text(item.get("stereotype")).casefold()
        for item in inventory.get("Classes") or [] if isinstance(item, dict)
    }
    class_sets: list[dict[str, Any]] = []
    for class_set in normalized.get("Classes") or []:
        if not isinstance(class_set, dict):
            continue
        owner = text(class_set.get("className"))
        operations: list[dict[str, Any]] = []
        for operation in class_set.get("operations") or []:
            if not isinstance(operation, dict):
                continue
            owned = deepcopy(operation)
            if stereotypes.get(owner) != "boundary":
                owned["stepRefs"] = [
                    ref for ref in owned.get("stepRefs") or []
                    if text(ref) not in actor_entry_refs
                ]
            if owned.get("stepRefs"):
                operations.append(owned)
        if operations:
            class_sets.append({**class_set, "operations": operations})
    normalized["Classes"] = class_sets
    return normalized


def _propose_fragment(
    index: ScenarioIndex,
    inventory: dict[str, Any],
    use_case: UseCase,
    *,
    previous: dict[str, Any] | None = None,
    findings: list[str] | None = None,
    reserved: list[dict[str, Any]] | None = None,
    reserved_types: list[dict[str, Any]] | None = None,
    allowed_step_ids: tuple[str, ...] = (),
    execution_group_id: str = "",
    operation: str = "InteractionOperations",
) -> dict[str, Any]:
    """한 연산 unit을 LLM에 요청하고 저장 직전 fragment shape로 정규화한다.

    응답은 ``OperationFragment``로 제한된다. 이 함수는 규칙 finding을 판단하지 않으며,
    deterministic normalization까지만 소유한다. 검사와 한 번의 replacement는
    ``_checked_fragment``가 담당한다.
    """
    # 1. 허용 step, 고정 타입과 예약 이름을 먼저 payload로 좁힌다. LLM이 전체 모델을
    # 보지 않으므로 다른 use case의 성공한 연산을 임의로 다시 작성할 수 없다.
    prompt_payload = _operation_payload(
        index,
        inventory,
        use_case,
        previous=previous,
        findings=findings,
        reserved=reserved,
        reserved_types=reserved_types,
        allowed_step_ids=allowed_step_ids,
        execution_group_id=execution_group_id,
    )
    parsed = parse_structured(
        [
            {"role": "system", "content": _OPERATION_PROMPT},
            {"role": "user", "content": json.dumps(prompt_payload, ensure_ascii=False)},
        ],
        OperationFragment,
        reasoning_effort=operation_reasoning_effort(),
        max_completion_tokens=operation_max_completion_tokens(),
        operation=operation,
        metadata={
            "useCaseId": use_case.id,
            "executionSlice": execution_group_id or use_case.id,
            "candidateCount": len(prompt_payload["fixedClasses"])
            + len(prompt_payload["executionSlice"]["steps"]),
        },
    )
    # 2. 설명문이나 임의 필드를 거부하고 일시적 proposal schema만 수락한다.
    candidate = OperationFragment.model_validate(parsed).model_dump(by_alias=True)
    fixed_names = {
        class_name(item) for item in inventory.get("Classes") or []
        if isinstance(item, dict)
    } | {
        text(item.get("name")) for item in inventory.get("DataTypes") or []
        if isinstance(item, dict)
    } | {
        text(item.get("name")) for item in reserved_types or []
        if isinstance(item, dict)
    }
    # 3. 전역·앞선 wave가 이미 소유한 타입은 지역 선언에서 제거한다. 같은 이름의 다른
    # 정의는 compose 시 충돌로 드러나며 조용히 덮어쓰지 않는다.
    candidate["DataTypes"] = [
        {
            **item,
            "fields": [
                fields.normalize_java_field(f"{field['name']} : {field['type']}")
                for field in item.get("fields") or []
            ],
        }
        for item in candidate.get("DataTypes") or []
        if text(item.get("name")) not in fixed_names
    ]
    # 4. 하류 DTO가 실제 상류 값에서 만들어질 수 있는지 보고 불필요한 layer DTO를
    # 재사용 가능한 입력 타입으로 정규화한다. 새 LLM 호출은 발생하지 않는다.
    candidate = _canonicalize_downstream_input_types(candidate, inventory)
    actor_entry_refs = {
        group.actor_step
        for group in index.groups
        if group.use_case_id == use_case.id
        and group.actor_step
        and (not execution_group_id or group.id == execution_group_id)
    }
    # 5. actor entry는 Boundary만 소유한다. Control/Entity의 중복 stepRef를 제거한 뒤
    # 근거가 사라진 placeholder operation도 함께 제외한다.
    return _canonicalize_step_ownership(
        candidate, inventory, actor_entry_refs,
    )


def _checked_fragment_uncached(
    index: ScenarioIndex,
    inventory: dict[str, Any],
    use_case: UseCase,
    *,
    previous: dict[str, Any] | None = None,
    findings: list[str] | None = None,
    reserved: list[dict[str, Any]] | None = None,
    reserved_types: list[dict[str, Any]] | None = None,
    allowed_step_ids: tuple[str, ...] = (),
    execution_group_id: str = "",
    operation: str = "InteractionOperations",
) -> dict[str, Any]:
    """연산 fragment를 제안·검사하고 현재 unit만 최대 한 번 교체한다.

    첫 보고서의 finding은 ``previousFragment``와 함께 repair 호출에 전달한다. 두 번째
    보고서가 깨끗하지 않으면 더 넓은 재생성으로 승격하지 않고 명시적으로 실패한다.
    """
    # 최초 후보에는 caller가 전달한 피드백이나 collision finding만 포함된다.
    candidate = _propose_fragment(
        index,
        inventory,
        use_case,
        previous=previous,
        findings=findings,
        reserved=reserved,
        reserved_types=reserved_types,
        allowed_step_ids=allowed_step_ids,
        execution_group_id=execution_group_id,
        operation=operation,
    )
    validation_inventory = {
        **inventory,
        "DataTypes": [
            *(inventory.get("DataTypes") or []),
            *(
                item for item in (reserved_types or [])
                if isinstance(item, dict)
                and text(item.get("name")) not in {
                    text(existing.get("name"))
                    for existing in inventory.get("DataTypes") or []
                    if isinstance(existing, dict)
                }
            ),
        ],
    }
    # 검증 context는 다른 wave의 예약 타입까지 포함한다. 그렇지 않으면 유효한 재사용
    # 타입을 "존재하지 않음"으로 오판한다.
    context = OperationContext(
        index,
        validation_inventory,
        use_case,
        allowed_step_ids,
        (execution_group_id,) if execution_group_id else (),
    )
    report = run_checks(OPERATION_CHECKS, candidate, context)
    if report.errors:
        raise RuntimeError("; ".join(report.errors))
    if report.findings:
        # 같은 입력 범위와 같은 rule set으로 full replacement를 한 번만 요청한다.
        candidate = _propose_fragment(
            index,
            inventory,
            use_case,
            previous=candidate,
            findings=finding_text(report.findings),
            reserved=reserved,
            reserved_types=reserved_types,
            allowed_step_ids=allowed_step_ids,
            execution_group_id=execution_group_id,
            operation=(
                "InteractionOperationsRepair"
                if operation == "InteractionOperations"
                else f"{operation}Repair"
            ),
        )
        report = run_checks(OPERATION_CHECKS, candidate, context)
    if report.errors or report.findings:
        raise ValueError(
            f"operation fragment {use_case.id} remains invalid: "
            + "; ".join([*report.errors, *finding_text(report.findings)])
        )
    return candidate


def _operation_cache_key(
    index: ScenarioIndex,
    inventory: dict[str, Any],
    use_case: UseCase,
    *,
    previous: dict[str, Any] | None,
    findings: list[str] | None,
    reserved: list[dict[str, Any]] | None,
    reserved_types: list[dict[str, Any]] | None,
    allowed_step_ids: tuple[str, ...],
    execution_group_id: str,
    operation: str,
) -> str:
    """execution slice의 수락 fragment에만 대응하는 cache key를 만든다."""

    payload = _operation_payload(
        index,
        inventory,
        use_case,
        previous=previous,
        findings=findings,
        reserved=reserved,
        reserved_types=reserved_types,
        allowed_step_ids=allowed_step_ids,
        execution_group_id=execution_group_id,
    )
    if isinstance(payload.get("findings"), list):
        payload["findings"] = [
            " ".join(str(item).split()) for item in payload["findings"]
        ]
    return accepted_unit_key(
        "operation-fragment",
        unit_slice=payload,
        inventory=inventory,
        feedback={
            "findings": [" ".join(str(item).split()) for item in findings or []],
            "operation": operation,
        },
        prompt=_OPERATION_PROMPT,
        schema=OperationFragment,
        provider="nvidia-nim",
        model=settings.model,
        seed=settings.seed,
        temperature=settings.temperature,
        reasoning_effort=operation_reasoning_effort(),
        max_completion_tokens=operation_max_completion_tokens(),
    )


def _validate_accepted_fragment(
    candidate: dict[str, Any],
    index: ScenarioIndex,
    inventory: dict[str, Any],
    use_case: UseCase,
    *,
    reserved_types: list[dict[str, Any]] | None,
    allowed_step_ids: tuple[str, ...],
    execution_group_id: str,
) -> dict[str, Any]:
    """cache hit을 Pydantic과 기존 operation validator로 다시 수락한다."""

    normalized = deepcopy(candidate)
    # 실제 cache value는 이미 ``name : Type`` 수락 표기다. test double이나 호환 caller가
    # parse 직후의 field mapping을 넣어도 같은 수락 표기로만 바꾼 뒤 영속 BCE schema를
    # 검증한다. raw ``OperationFragment`` schema로 normalized value를 다시 읽지는 않는다.
    for item in normalized.get("DataTypes") or []:
        if not isinstance(item, dict):
            continue
        item["fields"] = [
            fields.normalize_java_field(f"{field['name']} : {field['type']}")
            if isinstance(field, dict) else field
            for field in item.get("fields") or []
        ]
    try:
        _compose(inventory, [(use_case.id, normalized)])
    except Exception as error:
        raise ValueError(
            f"cached operation fragment {use_case.id} is not a valid accepted BCE fragment: {error}"
        ) from error
    validation_inventory = {
        **inventory,
        "DataTypes": [
            *(inventory.get("DataTypes") or []),
            *(
                item for item in (reserved_types or [])
                if isinstance(item, dict)
                and text(item.get("name")) not in {
                    text(existing.get("name"))
                    for existing in inventory.get("DataTypes") or []
                    if isinstance(existing, dict)
                }
            ),
        ],
    }
    report = run_checks(
        OPERATION_CHECKS,
        normalized,
        OperationContext(
            index,
            validation_inventory,
            use_case,
            allowed_step_ids,
            (execution_group_id,) if execution_group_id else (),
        ),
    )
    if report.errors or report.findings:
        raise ValueError(
            f"cached operation fragment {use_case.id} is invalid: "
            + "; ".join([*report.errors, *finding_text(report.findings)])
        )
    return normalized


def _checked_fragment(
    index: ScenarioIndex,
    inventory: dict[str, Any],
    use_case: UseCase,
    *,
    previous: dict[str, Any] | None = None,
    findings: list[str] | None = None,
    reserved: list[dict[str, Any]] | None = None,
    reserved_types: list[dict[str, Any]] | None = None,
    allowed_step_ids: tuple[str, ...] = (),
    execution_group_id: str = "",
    operation: str = "InteractionOperations",
    cache: AcceptedUnitCache | None = None,
) -> dict[str, Any]:
    """수락된 fragment만 저장하고 cache hit도 동일한 검사를 다시 수행한다."""

    def compute() -> dict[str, Any]:
        return _checked_fragment_uncached(
            index,
            inventory,
            use_case,
            previous=previous,
            findings=findings,
            reserved=reserved,
            reserved_types=reserved_types,
            allowed_step_ids=allowed_step_ids,
            execution_group_id=execution_group_id,
            operation=operation,
        )
    prompt_payload = _operation_payload(
        index,
        inventory,
        use_case,
        previous=previous,
        findings=findings,
        reserved=reserved,
        reserved_types=reserved_types,
        allowed_step_ids=allowed_step_ids,
        execution_group_id=execution_group_id,
    )
    metadata = {
        "executionSlice": execution_group_id or use_case.id,
        "candidateCount": len(prompt_payload["fixedClasses"])
        + len(prompt_payload["fixedDataTypes"]),
    }
    if cache is None:
        record_cache_outcome(
            None,
            operation=operation,
            unit=execution_group_id or use_case.id,
            metadata=metadata,
        )
        candidate = compute()
    else:
        result = cache.get_or_compute(
            _operation_cache_key(
                index,
                inventory,
                use_case,
                previous=previous,
                findings=findings,
                reserved=reserved,
                reserved_types=reserved_types,
                allowed_step_ids=allowed_step_ids,
                execution_group_id=execution_group_id,
                operation=operation,
            ),
            compute,
        )
        record_cache_outcome(
            result,
            operation=operation,
            unit=execution_group_id or use_case.id,
            metadata=metadata,
        )
        candidate = result.value
    return _validate_accepted_fragment(
        candidate,
        index,
        inventory,
        use_case,
        reserved_types=reserved_types,
        allowed_step_ids=allowed_step_ids,
        execution_group_id=execution_group_id,
    )


def _operation_signature(operation: dict[str, Any]) -> tuple[Any, ...]:
    return (
        tuple(
            (text(parameter.get("name")), text(parameter.get("type")))
            for parameter in operation.get("parameters") or [] if isinstance(parameter, dict)
        ),
        text(operation.get("returnType")),
    )


def _data_type_signature(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        text(item.get("kind")),
        tuple(item.get("fields") or []),
        tuple(item.get("values") or []),
    )


def _compose(
    inventory: dict[str, Any],
    fragments: list[tuple[str, dict[str, Any]]],
    *,
    final: bool = False,
) -> dict[str, Any]:
    """Inventory와 수락 fragment를 canonical ``BCEModel`` skeleton으로 합친다.

    동일 이름·동일 시그니처는 step provenance를 합치고, 동일 이름·다른 정의는
    ``Collision``로 현재 fragment repair를 요구한다. ``final=True``이면 operation에서
    도달할 수 없는 클래스·관계·지역 타입을 제거한다.
    """
    classes = {
        class_name(item): {
            **{
                key: deepcopy(value) for key, value in item.items()
                if key not in {"useCaseIds", "values"}
            },
            "use_case_ids": [],
            "operations": [],
        }
        for item in inventory.get("Classes") or [] if isinstance(item, dict)
    }
    data_type_index = {
        text(item.get("name")): {
            key: deepcopy(value) for key, value in item.items()
            if key not in {"useCaseIds", "identifier"}
        }
        for item in inventory.get("DataTypes") or [] if isinstance(item, dict)
    }
    # 입력 순서가 곧 충돌 소유권이다. 앞서 수락된 fragment는 고정하고 현재 fragment가
    # 충돌을 해결해야 병렬 완료 순서와 무관한 모델을 얻는다.
    for use_case_id, fragment in fragments:
        for proposed_type in fragment.get("DataTypes") or []:
            if not isinstance(proposed_type, dict):
                continue
            name = text(proposed_type.get("name"))
            existing_type = data_type_index.get(name)
            if existing_type is not None:
                if _data_type_signature(existing_type) != _data_type_signature(proposed_type):
                    raise _DataTypeCollision(name)
                continue
            data_type_index[name] = deepcopy(proposed_type)
        for class_set in fragment.get("Classes") or []:
            if not isinstance(class_set, dict):
                continue
            owner = text(class_set.get("className"))
            if owner not in classes:
                raise ValueError(f"fragment selected class outside inventory: {owner}")
            target = classes[owner]
            for proposed in class_set.get("operations") or []:
                if not isinstance(proposed, dict):
                    continue
                existing = next((
                    item for item in target["operations"]
                    if text(item.get("name")) == text(proposed.get("name"))
                ), None)
                if existing is not None:
                    if _operation_signature(existing) != _operation_signature(proposed):
                        raise _Collision(owner, text(proposed.get("name")))
                    existing["stepRefs"] = list(dict.fromkeys([
                        *(existing.get("stepRefs") or []),
                        *(proposed.get("stepRefs") or []),
                    ]))
                    continue
                parameters = list(proposed.get("parameters") or [])
                target["operations"].append({
                    "operationId": canonical_operation_id(
                        owner, text(proposed.get("name")), parameters,
                    ),
                    **deepcopy(proposed),
                })
            if class_set.get("operations") and use_case_id not in target["use_case_ids"]:
                target["use_case_ids"].append(use_case_id)
    result_classes = list(classes.values())
    relationships = deepcopy(inventory.get("Relationships") or [])
    data_types = list(data_type_index.values())
    if final:
        # 화면을 단순화하기 위한 임의 삭제가 아니다. 실제 operation 계약에서 도달할 수
        # 없는 구조만 제거해 API/sequence 소비자가 쓸 수 없는 타입을 저장하지 않는다.
        retained = {
            class_name(item) for item in result_classes if item.get("operations")
        }
        result_classes = [item for item in result_classes if class_name(item) in retained]
        relationships = [
            item for item in relationships if isinstance(item, dict)
            and text(item.get("source")) in retained and text(item.get("target")) in retained
        ]
        reachable = reachable_data_type_names(result_classes, data_types)
        data_types = [
            item for item in data_types if isinstance(item, dict)
            and text(item.get("name")) in reachable
        ]
    return BCEModel.model_validate({
        "Classes": result_classes,
        "DataTypes": data_types,
        "Relationships": relationships,
        "Collaborations": [],
    }).model_dump(by_alias=True)


def emit_preview(
    model: dict[str, Any], phase: str, unit: str, completed: int, total: int,
) -> None:
    """한 수락 경계의 BCE skeleton을 UI 진행 스냅샷으로 발행한다.

    빈 PlantUML은 이벤트를 만들지 않는다. 이 함수가 operations 모듈의 유일한 UI 부작용이며
    이벤트 실패를 validation 결과로 위장하지 않는다.
    """
    puml = generate_plantuml_from_bce_json(model)
    if puml:
        design_progress.emit_progress(
            "classDiagramSnapshotAccepted",
            puml=puml,
            phase=phase,
            unit=unit,
            completed=completed,
            total=total,
            detail={
                "inventory": "Building the class inventory",
                "operations": f"Adding operations for {unit}",
                "collaborations": f"Planning collaboration {unit}",
            }.get(phase, "Updating the class contract"),
        )


def _build_fragments(
    index: ScenarioIndex,
    inventory: dict[str, Any],
    *,
    reconstruct_fragments: Callable[[ScenarioIndex, dict[str, Any]], dict[str, dict[str, Any]]],
    cache: AcceptedUnitCache | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Operation unit을 안정적인 wave로 실행하고 수락 순서대로 조립한다.

    같은 wave는 동일한 reserved catalog를 보므로 독립적으로 병렬 실행할 수 있다. wave가
    끝나면 결과를 unit ID 순서로 commit하고 다음 wave에 새 이름을 예약한다.
    """
    groups_by_use_case: dict[str, list[ExecutionGroup]] = {}
    for group in index.groups:
        groups_by_use_case.setdefault(group.use_case_id, []).append(group)
    units = [
        _OperationUnit(
            group.id,
            use_case,
            tuple(group.step_ids),
            group.id,
        )
        for use_case in index.use_cases
        for group in groups_by_use_case.get(use_case.id, [])
    ] + [
        _OperationUnit(
            use_case.id,
            use_case,
            tuple(step.id for step in use_case.steps),
        )
        for use_case in index.use_cases
        if not groups_by_use_case.get(use_case.id)
    ]
    units.sort(key=lambda unit: id_key(unit.id))
    # 설정값이 커도 현재 unit 수를 넘지 않는다. 기본값 2가 긴 LLM 호출의 동시성 경계다.
    workers = max(1, min(
        len(units) or 1,
        int(getattr(settings, "design_class_behavior_parallelism", 2)),
    ))
    committed: list[tuple[str, dict[str, Any]]] = []
    position = 0
    for offset in range(0, len(units), workers):
        wave = units[offset:offset + workers]
        # wave 시작 시점의 snapshot을 모든 worker가 공유한다. worker별로 서로 다른 예약
        # 목록을 주면 완료 순서에 따라 이름 충돌 결과가 달라진다.
        current = _compose(inventory, committed)
        reserved = _reserved_operations(current)
        reserved_types = list(current.get("DataTypes") or [])
        if len(wave) == 1:
            proposals = [
                _checked_fragment(
                    index,
                    inventory,
                    wave[0].use_case,
                    reserved=reserved,
                    reserved_types=reserved_types,
                    allowed_step_ids=wave[0].step_ids,
                    execution_group_id=wave[0].execution_group_id,
                    cache=cache,
                )
            ]
        else:
            with ThreadPoolExecutor(max_workers=len(wave)) as executor:
                futures = [
                    executor.submit(
                        bind_context(_checked_fragment),
                        index,
                        inventory,
                        unit.use_case,
                        reserved=reserved,
                        reserved_types=reserved_types,
                        allowed_step_ids=unit.step_ids,
                        execution_group_id=unit.execution_group_id,
                        cache=cache,
                    )
                    for unit in wave
                ]
                proposals = [future.result() for future in futures]
        for unit, fragment in zip(wave, proposals, strict=True):
            try:
                candidate = _compose(
                    inventory, [*committed, (unit.use_case.id, fragment)],
                )
            except (_Collision, _DataTypeCollision) as collision:
                # 충돌의 후발 소유자인 현재 unit만 한 번 교체한다. 이미 committed된 형제
                # fragment를 다시 호출하지 않는 것이 국소성 보장의 핵심이다.
                current = _compose(inventory, committed)
                fragment = _checked_fragment(
                    index,
                    inventory,
                    unit.use_case,
                    previous=fragment,
                    findings=[str(collision)],
                    reserved=_reserved_operations(current),
                    reserved_types=list(current.get("DataTypes") or []),
                    allowed_step_ids=unit.step_ids,
                    execution_group_id=unit.execution_group_id,
                    operation="InteractionOperationCollisionRepair",
                    cache=cache,
                )
                candidate = _compose(
                    inventory, [*committed, (unit.use_case.id, fragment)],
                )
            committed.append((unit.use_case.id, fragment))
            position += 1
            emit_preview(candidate, "operations", unit.id, position + 1, len(units) + 1)
    skeleton = _compose(inventory, committed, final=True)
    return skeleton, reconstruct_fragments(index, skeleton)


def _compose_fragments(
    inventory: dict[str, Any], fragments: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return _compose(
        inventory,
        sorted(fragments.items(), key=lambda item: id_key(item[0])),
        final=True,
    )


def _repair_failed_operations(
    index: ScenarioIndex,
    inventory: dict[str, Any],
    fragments: dict[str, dict[str, Any]],
    failures: list[CollaborationResult],
    *,
    operation: str = "InteractionOperationHandoff",
    cache: AcceptedUnitCache | None = None,
) -> set[str]:
    """협업 실패가 가리킨 execution slice의 연산 부분만 보완한다.

    같은 use case 안에서도 실패 group의 ``step_ids``와 겹치지 않는 연산은 preserved로
    분리한다. LLM은 실패 slice의 이전 연산과 issue만 받고, 결과를 preserved 부분에 다시
    합친다. 반환값은 service가 collaboration을 재계획할 use-case ID 집합이다.
    """
    group_by_id = {group.id: group for group in index.groups}
    repaired: set[str] = set()
    for result in sorted(failures, key=lambda item: id_key(item.group_id)):
        group = group_by_id[result.group_id]
        use_case_id = group.use_case_id
        use_case = index.use_case(use_case_id)
        existing = fragments[use_case_id]
        group_steps = set(group.step_ids)
        # 하나의 use case가 여러 actor entry group을 가질 수 있다. 실패 group과 겹치지
        # 않는 operation은 prompt와 replacement 대상에서 모두 분리해 보존한다.
        preserved_classes = []
        previous_classes = []
        for class_set in existing.get("Classes") or []:
            if not isinstance(class_set, dict):
                continue
            preserved_operations = [
                deepcopy(operation)
                for operation in class_set.get("operations") or []
                if isinstance(operation, dict)
                and not (group_steps & set(operation.get("stepRefs") or []))
            ]
            previous_operations = [
                deepcopy(operation)
                for operation in class_set.get("operations") or []
                if isinstance(operation, dict)
                and group_steps & set(operation.get("stepRefs") or [])
            ]
            if preserved_operations:
                preserved_classes.append({
                    "className": text(class_set.get("className")),
                    "operations": preserved_operations,
                })
            if previous_operations:
                previous_classes.append({
                    "className": text(class_set.get("className")),
                    "operations": previous_operations,
                })
        preserved = {
            "DataTypes": deepcopy(existing.get("DataTypes") or []),
            "Classes": preserved_classes,
        }
        base_fragments = {
            **{key: value for key, value in fragments.items() if key != use_case_id},
            **({use_case_id: preserved} if preserved_classes else {}),
        }
        base = _compose_fragments(inventory, base_fragments)
        # Collaboration의 구체적 failure text를 finding으로 전달해 "필요한 operation 없음"
        # 같은 handoff 원인을 현재 slice 안에서 해결하게 한다.
        candidate = _checked_fragment(
            index,
            inventory,
            use_case,
            previous={
                "DataTypes": deepcopy(existing.get("DataTypes") or []),
                "Classes": previous_classes,
            },
            findings=[f"execution group {result.group_id}: {result.issue}"],
            reserved=_reserved_operations(base),
            reserved_types=list(base.get("DataTypes") or []),
            allowed_step_ids=tuple(group.step_ids),
            execution_group_id=group.id,
            operation=operation,
            cache=cache,
        )
        merged_types = {
            text(item.get("name")): deepcopy(item)
            for item in existing.get("DataTypes") or [] if isinstance(item, dict)
        }
        merged_types.update({
            text(item.get("name")): deepcopy(item)
            for item in candidate.get("DataTypes") or [] if isinstance(item, dict)
        })
        merged_classes: dict[str, dict[str, Any]] = {
            text(item.get("className")): deepcopy(item)
            for item in preserved_classes
        }
        for class_set in candidate.get("Classes") or []:
            if not isinstance(class_set, dict):
                continue
            owner = text(class_set.get("className"))
            target = merged_classes.setdefault(
                owner, {"className": owner, "operations": []},
            )
            target_operations = target.get("operations")
            if isinstance(target_operations, list):
                target_operations.extend(deepcopy(class_set.get("operations") or []))
        fragments[use_case_id] = {
            "DataTypes": list(merged_types.values()),
            "Classes": list(merged_classes.values()),
        }
        repaired.add(use_case_id)
    return repaired


def reserved_operations(model: BCEModel) -> list[dict[str, Any]]:
    """수락 모델에서 이후 조각 생성에 예약할 연산 catalog를 읽는다.

    Args:
        model: 앞선 unit까지 조립된 BCE skeleton이다.

    Returns:
        class 이름과 수락 operation 목록만 포함하는 LLM payload 조각이다.
    """
    return _reserved_operations(model.model_dump(by_alias=True))


def propose_fragment(
    index: ScenarioIndex,
    inventory: AcceptedInventory,
    use_case: UseCase,
    *,
    previous: AcceptedFragment | None = None,
    findings: list[str] | None = None,
    reserved: list[dict[str, Any]] | None = None,
    reserved_types: list[dict[str, Any]] | None = None,
    allowed_step_ids: tuple[str, ...] = (),
    execution_group_id: str = "",
    operation: str = "InteractionOperation",
) -> AcceptedFragment:
    """고정 inventory 안에서 한 use-case 연산 fragment를 제안한다.

    Args:
        index: 단계와 실행 그룹의 정규화된 입력이다.
        inventory: LLM이 변경할 수 없는 전역 구조다.
        use_case: 이번 fragment가 소유할 유스케이스다.
        previous: repair에서 참고할 이전 수락 후보다.
        findings: replacement가 해결해야 할 검증·충돌 근거다.
        reserved: 앞선 unit이 이미 소유한 operation 목록이다.
        reserved_types: 앞선 unit이 이미 소유한 지역 타입 목록이다.
        allowed_step_ids: 현재 실행 slice가 소유할 수 있는 단계다.
        execution_group_id: use case보다 작은 실행 단위의 식별자다.
        operation: 관측과 재개에 사용하는 영어 LLM operation 이름이다.

    Returns:
        정규화됐지만 별도 검사 budget은 적용하지 않은 ``AcceptedFragment``다.
    """
    candidate = _propose_fragment(
        index,
        inventory.as_payload(),
        use_case,
        previous=previous.as_payload() if previous else None,
        findings=findings,
        reserved=reserved,
        reserved_types=reserved_types,
        allowed_step_ids=allowed_step_ids,
        execution_group_id=execution_group_id,
        operation=operation,
    )
    return AcceptedFragment(use_case_id=use_case.id, payload=candidate)


def checked_fragment(
    index: ScenarioIndex,
    inventory: AcceptedInventory,
    use_case: UseCase,
    **kwargs: Any,
) -> AcceptedFragment:
    """검사와 최대 한 번의 replacement를 거친 연산 fragment를 생성한다.

    Args:
        index: 시나리오와 허용 단계의 기준이다.
        inventory: 고정된 전역 BCE 구조다.
        use_case: fragment 소유 유스케이스다.
        **kwargs: 이전 후보, finding, 예약 catalog와 operation 이름이다.

    Returns:
        모든 ``OPERATION_CHECKS``를 통과한 ``AcceptedFragment``다.

    Raises:
        RuntimeError: 검사기 자체가 완료되지 못한 경우다.
        ValueError: 최초 후보와 한 번의 replacement가 모두 유효하지 않은 경우다.
    """
    previous = kwargs.pop("previous", None)
    candidate = _checked_fragment(
        index,
        inventory.as_payload(),
        use_case,
        previous=previous.as_payload() if isinstance(previous, AcceptedFragment) else previous,
        **kwargs,
    )
    return AcceptedFragment(use_case_id=use_case.id, payload=candidate)


def build_fragments(
    index: ScenarioIndex,
    inventory: AcceptedInventory,
    *,
    reconstruct_fragments: Callable[[ScenarioIndex, BCEModel], Mapping[str, AcceptedFragment]],
    cache: AcceptedUnitCache | None = None,
) -> tuple[BCEModel, dict[str, AcceptedFragment]]:
    """실행 unit fragment를 bounded wave로 생성하고 BCE skeleton으로 수락한다.

    Args:
        index: unit과 안정적인 실행 순서를 제공하는 시나리오 인덱스다.
        inventory: 모든 worker가 공유하는 고정 구조다.
        reconstruct_fragments: 최종 skeleton에서 소유 fragment를 복원하는 typed adapter다.

    Returns:
        도달 가능한 연산·타입만 담은 skeleton과 use-case별 수락 fragment다.

    Notes:
        병렬 완료 순서가 아니라 unit ID 순서로 commit한다. 충돌 시 현재 unit만 교체한다.
    """
    def reconstruct_raw(
        scenario_index: ScenarioIndex, skeleton: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        accepted = reconstruct_fragments(
            scenario_index, BCEModel.model_validate(skeleton),
        )
        return {key: value.as_payload() for key, value in accepted.items()}

    skeleton, fragments = _build_fragments(
        index,
        inventory.as_payload(),
        reconstruct_fragments=reconstruct_raw,
        cache=cache,
    )
    return (
        BCEModel.model_validate(skeleton),
        {
            use_case_id: AcceptedFragment(use_case_id=use_case_id, payload=fragment)
            for use_case_id, fragment in fragments.items()
        },
    )


def compose_fragments(
    inventory: AcceptedInventory,
    fragments: Mapping[str, AcceptedFragment],
) -> BCEModel:
    """수락된 fragment를 하나의 canonical BCE skeleton으로 조립한다.

    Args:
        inventory: 클래스·구조 타입·관계의 고정 원본이다.
        fragments: use-case ID로 소유권이 지정된 수락 연산 조각이다.

    Returns:
        collaboration이 비어 있고 도달 가능한 계약만 남은 ``BCEModel``이다.
    """
    return BCEModel.model_validate(_compose_fragments(
        inventory.as_payload(),
        {key: value.as_payload() for key, value in fragments.items()},
    ))


def repair_failed_operations(
    index: ScenarioIndex,
    inventory: AcceptedInventory,
    fragments: MutableMapping[str, AcceptedFragment],
    failures: list[CollaborationResult],
    *,
    operation: str = "InteractionOperationHandoff",
    cache: AcceptedUnitCache | None = None,
) -> set[str]:
    """협업 실패 group의 연산 slice만 보완하고 수락 fragment를 갱신한다.

    Args:
        index: 실패 group과 use case의 대응을 제공한다.
        inventory: 변경하지 않는 전역 구조다.
        fragments: 성공한 형제 조각을 포함한 mutable 소유 mapping이다.
        failures: collaboration이 없는 명시적 실패 결과다.
        operation: 호출 목적을 구분하는 LLM operation 이름이다.

    Returns:
        연산이 바뀌어 collaboration 재계획이 필요한 use-case ID 집합이다.

    Notes:
        ``fragments``는 승인 작업 단위의 commit map이므로 의도적으로 제자리 갱신한다.
        raw 후보는 내부에서만 사용하고 완료 후 다시 ``AcceptedFragment``로 감싼다.
    """
    raw_fragments = {key: value.as_payload() for key, value in fragments.items()}
    repaired = _repair_failed_operations(
        index,
        inventory.as_payload(),
        raw_fragments,
        failures,
        operation=operation,
        cache=cache,
    )
    fragments.clear()
    fragments.update({
        use_case_id: AcceptedFragment(use_case_id=use_case_id, payload=fragment)
        for use_case_id, fragment in raw_fragments.items()
    })
    return repaired




__all__ = [
    "build_fragments",
    "checked_fragment",
    "compose_fragments",
    "emit_preview",
    "propose_fragment",
    "repair_failed_operations",
    "reserved_operations",
]
