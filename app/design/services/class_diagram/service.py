"""클래스 모델의 최초 생성·재개·사용자 피드백 수정을 조율한다."""
from __future__ import annotations

from collections.abc import Set as AbstractSet
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.config import settings
from app.design.schemas.class_model import BCEModel, Collaboration
from app.design.services.class_diagram import collaboration, generation, inventory, operations
from app.design.services.class_diagram import feedback as feedback_stage
from app.design.services.class_diagram.cache import AcceptedUnitCache
from app.design.services.class_diagram.scenario import ScenarioIndex, UseCase, id_key
from app.design.services.class_diagram.validation.collaboration import (
    COLLABORATION_CHECKS,
    CollaborationContext,
)
from app.design.services.class_diagram.validation.model import validate_class_model
from app.design.services.common.structured import bind_context
from app.validation import run_checks


def _payload(model: BCEModel) -> dict[str, Any]:
    return model.model_dump(by_alias=True)


def _standalone(index: ScenarioIndex) -> list[UseCase]:
    return [
        use_case for use_case in index.use_cases
        if any(group.use_case_id == use_case.id for group in index.groups)
    ]


def _replace_use_cases(
    index: ScenarioIndex,
    model: BCEModel,
    use_cases: list[UseCase],
    *,
    feedback: str = "",
    cache: AcceptedUnitCache | None = None,
) -> tuple[
    dict[str, Collaboration],
    list[collaboration.CombinedReplacementRequired],
]:
    """유스케이스별 collaboration을 설정된 수만큼 병렬로 교체한다."""

    directive = f"Apply this feedback to the call plan only: {feedback}" if feedback else ""

    def run(
        use_case: UseCase,
    ) -> tuple[
        str,
        Collaboration | None,
        collaboration.CombinedReplacementRequired | None,
    ]:
        try:
            value = collaboration.process_use_case(
                index, model, use_case, directive, cache=cache,
            )
            return use_case.id, value, None
        except collaboration.CombinedReplacementRequired as signal:
            return use_case.id, None, signal

    workers = max(1, min(
        len(use_cases) or 1,
        int(getattr(settings, "design_class_behavior_parallelism", 4)),
    ))
    if workers == 1:
        results = [run(use_case) for use_case in use_cases]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(bind_context(run), use_case) for use_case in use_cases]
            results = [future.result() for future in futures]
    return (
        {use_case_id: value for use_case_id, value, _signal in results if value},
        [signal for _use_case_id, _value, signal in results if signal],
    )


def _collaboration_valid(
    index: ScenarioIndex,
    model: BCEModel,
    use_case: UseCase,
    value: Collaboration,
) -> bool:
    report = run_checks(
        COLLABORATION_CHECKS,
        value.model_dump(by_alias=True),
        CollaborationContext(index, _payload(model), use_case),
    )
    return not report.errors and not report.findings


def _emit_preview(
    index: ScenarioIndex,
    skeleton: BCEModel,
    accepted: dict[str, Collaboration],
    use_case_id: str,
) -> None:
    standalone = _standalone(index)
    operations.emit_preview(
        {
            **_payload(skeleton),
            "Collaborations": [
                accepted[use_case.id].model_dump(by_alias=True)
                for use_case in standalone if use_case.id in accepted
            ],
        },
        "collaborations",
        use_case_id,
        len(accepted),
        len(standalone),
    )


def _complete_collaborations(
    index: ScenarioIndex,
    model: BCEModel,
    existing: dict[str, Collaboration],
    selected: list[UseCase],
    *,
    feedback: str = "",
    cache: AcceptedUnitCache | None = None,
) -> BCEModel:
    """국소 call-plan 수리부터 결합 유스케이스 교체까지 자동으로 이어 간다."""

    skeleton = BCEModel.model_validate({**_payload(model), "Collaborations": []})
    standalone = _standalone(index)
    accepted = {
        use_case.id: value
        for use_case in standalone
        if (value := existing.get(use_case.id)) is not None
        and _collaboration_valid(index, skeleton, use_case, value)
    }
    pending_ids = {use_case.id for use_case in selected} | {
        use_case.id for use_case in standalone if use_case.id not in accepted
    }
    while pending_ids:
        pending = [use_case for use_case in standalone if use_case.id in pending_ids]
        replacements, signals = _replace_use_cases(
            index, skeleton, pending, feedback=feedback, cache=cache,
        )
        for use_case in pending:
            value = replacements.get(use_case.id)
            if value is None:
                continue
            accepted[use_case.id] = value
            _emit_preview(index, skeleton, accepted, use_case.id)
        if not signals:
            break

        # 여러 worker가 동시에 확대 신호를 보냈더라도 operation 교체는 한 유스케이스씩
        # 적용한다. 새 skeleton에서 나머지 collaboration을 다시 검사해야 하기 때문이다.
        signal = signals[0]
        use_case = index.use_case(signal.use_case_id)
        skeleton, value = generation.replace_use_case_unit(
            index, skeleton, use_case, signal,
        )
        accepted[use_case.id] = value
        accepted = {
            item.id: current
            for item in standalone
            if (current := accepted.get(item.id)) is not None
            and _collaboration_valid(index, skeleton, item, current)
        }
        _emit_preview(index, skeleton, accepted, use_case.id)
        pending_ids = {
            item.id for item in standalone if item.id not in accepted
        }
        feedback = ""

    return BCEModel.model_validate({
        **_payload(skeleton),
        "Collaborations": [
            accepted[use_case.id]
            for use_case in standalone if use_case.id in accepted
        ],
    })


def _validated(model: BCEModel, index: ScenarioIndex, action: str) -> BCEModel:
    report = validate_class_model(model, index)
    if report.errors or report.findings:
        details = [
            *(f"{finding.location}: {finding.message}" for finding in report.findings),
            *report.errors,
        ]
        raise ValueError(f"{action} class model is incomplete or invalid: " + "; ".join(details))
    return model


def generate_class_model(
    index: ScenarioIndex, *, cache: AcceptedUnitCache | None = None,
) -> BCEModel:
    """inventory 한 번과 유스케이스별 결합 호출로 수락 BCE 모델을 생성한다."""

    if not index.use_cases:
        return BCEModel()
    accepted_inventory = inventory.inventory_proposal(index, cache=cache)
    operations.emit_preview(
        _payload(inventory.inventory_model(accepted_inventory)),
        "inventory", "inventory", 1, len(index.use_cases) + 1,
    )
    return _validated(
        generation.build_model(index, accepted_inventory, cache=cache),
        index,
        "generated",
    )


def resume_class_model(
    index: ScenarioIndex,
    current: BCEModel,
    *,
    cache: AcceptedUnitCache | None = None,
) -> BCEModel:
    """없는 유스케이스 collaboration만 완성하고 기존 수락 결과는 보존한다."""

    existing = {item.collaboration_id: item for item in current.Collaborations}
    current_payload = _payload(current)
    selected: list[UseCase] = []
    for use_case in _standalone(index):
        value = existing.get(use_case.id)
        if value is None:
            selected.append(use_case)
            continue
        report = run_checks(
            COLLABORATION_CHECKS,
            value.model_dump(by_alias=True),
            CollaborationContext(index, current_payload, use_case),
        )
        if report.errors or report.findings:
            selected.append(use_case)
    if not selected:
        return current
    model = _complete_collaborations(
        index, current, existing, selected, cache=cache,
    )
    return _validated(model, index, "resumed")


def revise_class_model(
    current: BCEModel,
    index: ScenarioIndex,
    feedback: str,
    targets: AbstractSet[str],
    *,
    cache: AcceptedUnitCache | None = None,
) -> BCEModel:
    """피드백이 지정한 inventory·operation·유스케이스 협업만 교체한다."""

    if not feedback.strip():
        return current
    scope = feedback_stage.feedback_scope(index, current, feedback, targets)
    accepted_inventory = feedback_stage.inventory_from_model(current)
    if scope.kind == "inventory":
        revised_inventory = feedback_stage.propose_inventory_revision(
            index, accepted_inventory, feedback, set(scope.ids), cache=cache,
        )
        return _validated(
            generation.build_model(index, revised_inventory, cache=cache),
            index,
            "revised",
        )

    existing = {item.collaboration_id: item for item in current.Collaborations}
    fragments = feedback_stage.fragments_from_model(index, current)
    if scope.kind == "operation":
        selected_ids = set(scope.ids) or {use_case.id for use_case in index.use_cases}
        for use_case_id in sorted(selected_ids, key=id_key):
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
                allowed_step_ids=tuple(step.id for step in use_case.steps),
                operation="InteractionOperationFeedback",
                cache=cache,
            )
        skeleton = operations.compose_fragments(accepted_inventory, fragments)
        selected_use_cases = [
            use_case for use_case in _standalone(index)
            if use_case.id in selected_ids or any(
                set(group.trace_use_case_ids) & selected_ids
                for group in index.groups if group.use_case_id == use_case.id
            )
        ]
        directive = ""
    else:
        skeleton = BCEModel.model_validate({**_payload(current), "Collaborations": []})
        selected_ids = set(scope.ids) or {item.id for item in _standalone(index)}
        selected_use_cases = [
            use_case for use_case in _standalone(index) if use_case.id in selected_ids
        ]
        directive = feedback
    revised = _complete_collaborations(
        index,
        skeleton,
        existing,
        selected_use_cases,
        feedback=directive,
        cache=cache,
    )
    return _validated(revised, index, "revised")


__all__ = ["generate_class_model", "resume_class_model", "revise_class_model"]
