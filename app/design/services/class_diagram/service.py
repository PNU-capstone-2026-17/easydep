"""수락된 시나리오와 BCE 모델을 조율하는 클래스 설계 공개 API다."""
from __future__ import annotations

import logging
from collections.abc import Set as AbstractSet
from typing import Any

from app.core.config import settings
from app.core.validation import run_checks
from app.design.schemas.class_model import BCEModel, Collaboration
from app.design.services.class_diagram import feedback as feedback_stage
from app.design.services.class_diagram import inventory, operations
from app.design.services.class_diagram.models import (
    AcceptedFragment,
    AcceptedInventory,
    CollaborationResult,
)
from app.design.services.class_diagram.scenario import (
    ExecutionGroup,
    ScenarioIndex,
    id_key,
)
from app.design.services.class_diagram.validation.collaboration import (
    COLLABORATION_CHECKS,
    CollaborationContext,
)
from app.design.services.class_diagram.validation.model import validate_class_model

logger = logging.getLogger(__name__)


def _payload(model: BCEModel) -> dict[str, Any]:
    """기존 검사와 미리보기 경계에만 별칭 JSON 모양을 제공한다."""
    return model.model_dump(by_alias=True)


def _build_skeleton(
    index: ScenarioIndex,
) -> tuple[BCEModel, AcceptedInventory, dict[str, AcceptedFragment]]:
    """인벤토리와 연산 단계를 명시적 수락 경계로 연결한다."""
    accepted_inventory = inventory.inventory_proposal(index)
    operations.emit_preview(
        _payload(inventory.inventory_model(accepted_inventory)),
        "inventory",
        "inventory",
        1,
        len(index.use_cases) + 1,
    )
    skeleton, fragments = operations.build_fragments(
        index,
        accepted_inventory,
        reconstruct_fragments=feedback_stage.fragments_from_model,
    )
    return skeleton, accepted_inventory, fragments


def _replace_groups(
    index: ScenarioIndex,
    model: BCEModel,
    groups: list[ExecutionGroup],
    *,
    feedback_text: str = "",
    workers: int | None = None,
) -> list[CollaborationResult]:
    """협업 단계가 가진 병렬성 및 국소 수리를 그대로 사용한다."""
    return feedback_stage.replace_selected_groups(
        index,
        model,
        groups,
        feedback=feedback_text,
        workers=workers,
    )


def _workers(count: int) -> int:
    return max(1, min(
        count or 1,
        int(getattr(settings, "design_class_behavior_parallelism", 2)),
    ))


def generate_class_model(index: ScenarioIndex) -> BCEModel:
    """시나리오 인덱스에서 수락된 BCE 모델을 생성한다.

    Args:
        index: 원시 유스케이스를 정규화한 불변 인덱스다.

    Returns:
        인벤토리, 연산, 협업을 포함한 저장 가능한 BCE 모델이다.

    Raises:
        ValueError: LLM 제안이 제한된 repair 뒤에도 단계 계약을 만족하지 못한 경우다.

    Notes:
        인벤토리와 연산을 먼저 수락하고 실행 그룹별 협업을 최대 설정 병렬도 2로 만든다.
        실패한 그룹은 소유 연산 조각만 한 번 repair하며 다른 그룹은 보존한다.
    """
    if not index.use_cases:
        return BCEModel()
    skeleton, accepted_inventory, fragments = _build_skeleton(index)
    workers = _workers(len(index.groups))
    results = _replace_groups(index, skeleton, list(index.groups), workers=workers)
    failures = [result for result in results if result.collaboration is None]
    if failures:
        try:
            repaired_use_cases = operations.repair_failed_operations(
                index, accepted_inventory, fragments, failures,
            )
            skeleton = operations.compose_fragments(accepted_inventory, fragments)
            affected = [
                group for group in index.groups
                if set(group.trace_use_case_ids) & repaired_use_cases
            ]
            affected_ids = {group.id for group in affected}
            retained = {
                result.group_id: result for result in results
                if result.collaboration is not None and result.group_id not in affected_ids
            }
            retried = _replace_groups(index, skeleton, affected, workers=workers)
            results = [
                retained.get(group.id)
                or next(result for result in retried if result.group_id == group.id)
                for group in index.groups
            ]
        except Exception as error:
            # Keep the valid skeleton and explicit missing-collaboration finding.
            logger.warning(
                "class collaboration operation handoff failed during generation: %s",
                error,
                exc_info=True,
            )
    collaborations: list[Collaboration] = []
    for position, result in enumerate(results, start=1):
        if result.collaboration is not None:
            collaborations.append(result.collaboration)
            preview = {
                **_payload(skeleton),
                "Collaborations": [item.model_dump(by_alias=True) for item in collaborations],
            }
            operations.emit_preview(
                preview,
                "collaborations",
                result.group_id,
                position,
                len(index.groups),
            )
    model = BCEModel.model_validate({
        **_payload(skeleton),
        "Collaborations": collaborations,
    })
    # 최종 검증은 finding이 가리킨 소유 단위 외의 repair 경로를 시작하지 않는다.
    validate_class_model(model, index)
    return model


def resume_class_model(index: ScenarioIndex, current: BCEModel) -> BCEModel:
    """기존 BCE 모델에서 빠졌거나 유효하지 않은 협업만 완성한다.

    Args:
        index: 현재 체크포인트와 대응하는 정규화된 시나리오 인덱스다.
        current: 저장소에서 검증해 복원한 BCE 모델이다.

    Returns:
        유효한 기존 협업을 유지하고 필요한 실행 그룹만 보완한 모델이다.

    Raises:
        ValueError: 선택된 실행 그룹을 유효한 협업으로 만들 수 없는 경우다.

    Notes:
        정상 예: 네 그룹 중 한 협업이 없으면 나머지 세 협업은 LLM에 다시 보내지 않는다.
        실패 예: 체크포인트와 다른 시나리오 인덱스를 섞어 누락 범위를 넓히면 안 된다.
    """
    existing = {
        item.collaboration_id: item for item in current.Collaborations
    }
    current_payload = _payload(current)
    selected: list[ExecutionGroup] = []
    for group in index.groups:
        current_collaboration = existing.get(group.id)
        if current_collaboration is None:
            selected.append(group)
            continue
        report = run_checks(
            COLLABORATION_CHECKS,
            current_collaboration.model_dump(by_alias=True),
            CollaborationContext(index, current_payload, group),
        )
        if report.errors or report.findings:
            selected.append(group)
    if not selected:
        return current
    workers = _workers(len(selected))
    results = _replace_groups(index, current, selected, workers=workers)
    selected_ids = {group.id for group in selected}
    working_model = current
    failures = [result for result in results if result.collaboration is None]
    if failures:
        try:
            accepted_inventory = feedback_stage.inventory_from_model(current)
            fragments = feedback_stage.fragments_from_model(index, current)
            repaired_use_cases = operations.repair_failed_operations(
                index, accepted_inventory, fragments, failures,
            )
            working_model = operations.compose_fragments(accepted_inventory, fragments)
            affected = [
                group for group in index.groups
                if set(group.trace_use_case_ids) & repaired_use_cases
            ]
            retried = _replace_groups(index, working_model, affected, workers=workers)
            affected_ids = {group.id for group in affected}
            results = [
                result for result in results if result.group_id not in affected_ids
            ] + retried
            selected_ids.update(affected_ids)
        except Exception as error:
            logger.warning(
                "class collaboration operation handoff failed during resume: %s",
                error,
                exc_info=True,
            )
    accepted = {
        **{
            group_id: existing_collaboration
            for group_id, existing_collaboration in existing.items()
            if group_id not in selected_ids
        },
        **{
            result.group_id: result.collaboration
            for result in results if result.collaboration is not None
        },
    }
    return BCEModel.model_validate({
        **_payload(working_model),
        "Collaborations": [
            accepted[group.id] for group in index.groups if group.id in accepted
        ],
    })


def revise_class_model(
    current: BCEModel,
    index: ScenarioIndex,
    feedback: str,
    targets: AbstractSet[str],
) -> BCEModel:
    """피드백을 가장 작은 소유 단계에 적용해 BCE 모델을 재조립한다.

    Args:
        current: 수정 전의 수락된 BCE 모델이다.
        index: 모델을 만든 시나리오와 실행 그룹 인덱스다.
        feedback: 사용자에게서 받은 피드백 문자열이다.
        targets: UI 또는 검증 finding이 지정한 산출물 식별자 집합이다.

    Returns:
        대상 인벤토리, 연산 또는 협업만 교체한 새 BCE 모델이다.

    Raises:
        ValueError: 국소 재생성 결과가 완성 모델 계약을 만족하지 못한 경우다.

    Notes:
        빈 피드백은 원본 모델을 그대로 반환한다. 대상 해석은 피드백 단계가 소유하며,
        서비스는 선택되지 않은 협업과 연산 조각을 보존한다.
    """
    if not feedback.strip():
        return current
    scope = feedback_stage.feedback_scope(index, current, feedback, targets)
    accepted_inventory = feedback_stage.inventory_from_model(current)
    fragments = feedback_stage.fragments_from_model(index, current)
    existing = {
        item.collaboration_id: item for item in current.Collaborations
    }

    if scope.kind == "inventory":
        accepted_inventory = feedback_stage.propose_inventory_revision(
            index, accepted_inventory, feedback, set(scope.ids),
        )
        skeleton, fragments = operations.build_fragments(
            index,
            accepted_inventory,
            reconstruct_fragments=feedback_stage.fragments_from_model,
        )
        selected_groups = list(index.groups)
    elif scope.kind == "operation":
        selected_use_cases = set(scope.ids) or {use_case.id for use_case in index.use_cases}
        selected_operation_groups = [
            group for group in index.groups if group.use_case_id in selected_use_cases
        ]
        if selected_operation_groups:
            operations.repair_failed_operations(
                index,
                accepted_inventory,
                fragments,
                [
                    CollaborationResult(
                        group.id,
                        None,
                        f"User feedback for this execution slice: {feedback}",
                    )
                    for group in selected_operation_groups
                ],
                operation="InteractionOperationFeedback",
            )
        for use_case_id in sorted(selected_use_cases, key=id_key):
            if any(group.use_case_id == use_case_id for group in selected_operation_groups):
                continue
            use_case = index.use_case(use_case_id)
            others = {key: value for key, value in fragments.items() if key != use_case_id}
            base = operations.compose_fragments(accepted_inventory, others)
            fragments[use_case_id] = operations.checked_fragment(
                index,
                accepted_inventory,
                use_case,
                previous=fragments.get(use_case_id),
                findings=[f"User feedback: {feedback}"],
                reserved=operations.reserved_operations(base),
                reserved_types=list(_payload(base).get("DataTypes") or []),
                operation="InteractionOperationFeedback",
            )
        skeleton = operations.compose_fragments(accepted_inventory, fragments)
        selected_groups = [
            group for group in index.groups
            if set(group.trace_use_case_ids) & selected_use_cases
        ]
    else:
        skeleton = BCEModel.model_validate({
            **_payload(current), "Collaborations": [],
        })
        selected = set(scope.ids) or {group.id for group in index.groups}
        selected_groups = [group for group in index.groups if group.id in selected]

    results = _replace_groups(
        index,
        skeleton,
        selected_groups,
        feedback_text=feedback if scope.kind == "collaboration" else "",
    )
    failures = [result for result in results if result.collaboration is None]
    if failures:
        raise ValueError("feedback could not produce accepted collaborations: " + "; ".join(
            f"{result.group_id}: {result.issue}" for result in failures
        ))
    replacements = {
        result.group_id: result.collaboration for result in results
        if result.collaboration is not None
    }
    collaborations = [
        replacements.get(group.id) or existing.get(group.id)
        for group in index.groups
        if replacements.get(group.id) or existing.get(group.id)
    ]
    revised = BCEModel.model_validate({
        **_payload(skeleton),
        "Collaborations": collaborations,
    })
    report = validate_class_model(revised, index)
    if report.errors or report.findings:
        raise ValueError("feedback result is invalid: " + "; ".join(
            [
                *(f"{finding.location}: {finding.message}" for finding in report.findings),
                *report.errors,
            ]
        ))
    return revised


__all__ = [
    "generate_class_model",
    "resume_class_model",
    "revise_class_model",
]
