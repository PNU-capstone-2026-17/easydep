"""클래스 설계의 generate·resume·revise 공개 흐름을 조율한다.

입력은 graph adapter가 검증한 ``ScenarioIndex``와 필요할 때 현재 ``BCEModel``이다. 출력은
그대로 저장할 수 있는 새 ``BCEModel``이다. 이 모듈만 inventory, operation,
collaboration의 실행 순서와 설정 기반 병렬도, 진행 preview를 결정한다.

하위 단계는 저장소나 graph state를 모르며 서로의 LLM 호출을 시작하지 않는다. 이 서비스도
raw JSON 직렬화는 하지 않는다. 외부 state와 typed 모델 사이 변환은 graph adapter의
책임이므로 체크포인트·API JSON 모양이 내부 실행 단위와 섞이지 않는다.
"""
from __future__ import annotations

import logging
from collections.abc import Set as AbstractSet
from typing import Any

from app.config import settings
from app.validation import run_checks
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
    """inventory를 먼저 고정한 뒤 모든 유스케이스 operation fragment를 합친다.

    inventory preview는 구조가 수락된 직후 한 번 발행한다. operation worker는 이 같은
    immutable inventory를 읽으므로 병렬 실행 중 클래스·field 기준이 달라지지 않는다.
    """
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
    """선택된 collaboration 그룹만 feedback 단계의 bounded runner에 위임한다.

    서비스가 직접 executor나 repair loop를 만들지 않아 generate, resume, revise가 동일한
    호출 횟수·오류 격리 규약을 공유한다.
    """
    return feedback_stage.replace_selected_groups(
        index,
        model,
        groups,
        feedback=feedback_text,
        workers=workers,
    )


def _workers(count: int) -> int:
    """요청 수와 설정 상한으로 실제 worker 수를 결정한다."""
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
    # 1. 구조와 operation contract를 수락해야 collaboration의 receiver 후보를 유한하게
    # 만들 수 있다. 이 순서를 뒤집으면 호출 계획이 존재하지 않는 operation을 발명한다.
    skeleton, accepted_inventory, fragments = _build_skeleton(index)
    # 2. 독립 execution group을 설정 상한 안에서 처리한다. 결과는 입력 그룹 순서다.
    workers = _workers(len(index.groups))
    results = _replace_groups(index, skeleton, list(index.groups), workers=workers)
    failures = [result for result in results if result.collaboration is None]
    if failures:
        try:
            # 3. collaboration 실패가 operation 계약 부족을 가리키면 실패 group이 추적한
            # 유스케이스 fragment만 handoff repair한다. 성공한 비영향 group은 보존한다.
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
            # 구조·operation skeleton 자체는 이미 수락됐다. handoff 실패를 이유로 그것까지
            # 폐기하지 않고 누락 collaboration을 최종 검증 finding으로 명시한다.
            logger.warning(
                "class collaboration operation handoff failed during generation: %s",
                error,
                exc_info=True,
            )
    # 4. 완료 preview는 수락된 collaboration만 누적한다. 실패 객체나 repair telemetry는
    # 저장 JSON 계약에 들어가지 않는다.
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
    # 5. 최종 검증은 보고서만 만든다. 여기서 또 LLM repair를 시작하면 호출 예산과 국소성
    # 계약을 깨므로 오류는 그대로 호출자에 전달한다.
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
    # 1. collaborationId가 execution group의 안정적인 체크포인트 키다.
    existing = {
        item.collaboration_id: item for item in current.Collaborations
    }
    current_payload = _payload(current)
    selected: list[ExecutionGroup] = []
    # 2. 없는 그룹과 현재 scenario/model 문맥에서 검증에 실패한 그룹만 선택한다.
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
            # 3. 영속 모델에서 inventory와 fragment를 역투영하므로 별도 checkpoint
            # migration 없이 generate와 같은 operation handoff를 재사용한다.
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
    # 4. 선택되지 않은 기존 collaboration과 새 수락 결과를 원래 group 순서로 합친다.
    # 실패한 선택 그룹을 오래된 값으로 되돌리지 않는 것이 resume의 핵심 실패 계약이다.
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
    # 1. scope는 inventory/operation/collaboration 중 정확히 하나다. 명시 target으로
    # 판정할 수 있을 때는 분류 LLM을 호출하지 않는다.
    scope = feedback_stage.feedback_scope(index, current, feedback, targets)
    accepted_inventory = feedback_stage.inventory_from_model(current)
    fragments = feedback_stage.fragments_from_model(index, current)
    existing = {
        item.collaboration_id: item for item in current.Collaborations
    }

    if scope.kind == "inventory":
        # 2a. 구조 변경은 operation 후보 자체를 바꿀 수 있어 fragment와 모든
        # collaboration을 다시 만든다. target 밖 inventory item은 feedback 단계가 보존한다.
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
        # 2b. 선택 use case에 execution group이 있으면 기존 handoff repair 경로를 쓴다.
        # group이 없는 slice도 같은 checked_fragment 계약으로 한 번만 교체한다.
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
        # 2c. 호출 순서·binding 피드백은 operation skeleton을 손대지 않고 선택한
        # collaboration만 비운 모델에서 재계획한다.
        skeleton = BCEModel.model_validate({
            **_payload(current), "Collaborations": [],
        })
        selected = set(scope.ids) or {group.id for group in index.groups}
        selected_groups = [group for group in index.groups if group.id in selected]

    # 3. collaboration 피드백만 call-plan directive로 전달한다. inventory/operation
    # 피드백을 다시 보내면 이미 반영된 요구를 다른 단계가 중복 해석하게 된다.
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
    # 4. 선택 group의 새 결과와 비선택 group의 원본을 ScenarioIndex 순서로 합친다.
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
    # 5. 최종 보고서는 schema·deterministic·semantic finding을 한 경계에서 제공한다.
    # service는 이 시점에 추가 전역 repair를 수행하지 않는다.
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
