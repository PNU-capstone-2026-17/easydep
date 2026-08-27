"""Public interaction-design orchestration over independently owned stages."""
from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from app.core.config import settings
from app.core.validation import run_checks
from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram import (
    feedback as feedback_stage,
)
from app.design.services.class_diagram import (
    inventory,
    operations,
)
from app.design.services.class_diagram.models import GroupResult
from app.design.services.class_diagram.scenario import (
    ExecutionGroup,
    build_scenario_index,
    id_key,
    text,
)
from app.design.services.class_diagram.validation.collaboration import (
    COLLABORATION_CHECKS,
    CollaborationContext,
)
from app.design.services.class_diagram.validation.model import final_model_findings

logger = logging.getLogger(__name__)


def _build_skeleton(
    index: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    """Coordinate the inventory and operation stages with an explicit handoff."""

    accepted_inventory = inventory.inventory_proposal(index)
    operations.emit_preview(
        inventory.inventory_model(accepted_inventory),
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
    index: Any,
    model: dict[str, Any],
    groups: list[ExecutionGroup],
    *,
    feedback_text: str = "",
    workers: int | None = None,
) -> list[GroupResult]:
    """Keep collaboration concurrency and local repair inside its stage owner."""

    return feedback_stage.replace_selected_groups(
        index,
        model,
        groups,
        feedback=feedback_text,
        workers=workers,
    )


def generate_class_model(scenario: dict[str, Any]) -> dict[str, Any]:
    """Generate the persisted BCE model through bounded owned stages."""

    index = build_scenario_index(scenario)
    if not index.use_cases:
        return {}
    skeleton, accepted_inventory, fragments = _build_skeleton(index)
    workers = max(1, min(
        len(index.groups) or 1,
        int(getattr(settings, "design_class_behavior_parallelism", 2)),
    ))
    results = _replace_groups(
        index,
        skeleton,
        list(index.groups),
        workers=workers,
    )
    failures = [result for result in results if result.collaboration is None]
    if failures:
        try:
            repaired_use_cases = operations.repair_failed_operations(
                index,
                accepted_inventory,
                fragments,
                failures,
            )
            skeleton = operations.compose_fragments(accepted_inventory, fragments)
            affected = [
                group for group in index.groups
                if set(group.trace_use_case_ids) & repaired_use_cases
            ]
            retained = {
                result.group_id: result for result in results
                if result.collaboration is not None
                and result.group_id not in {group.id for group in affected}
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
    collaborations: list[dict[str, Any]] = []
    for position, result in enumerate(results, start=1):
        if result.collaboration is not None:
            collaborations.append(result.collaboration)
            preview = {**deepcopy(skeleton), "Collaborations": deepcopy(collaborations)}
            operations.emit_preview(
                preview,
                "collaborations",
                result.group_id,
                position,
                len(index.groups),
            )
    model = BCEModel.model_validate({
        **skeleton,
        "Collaborations": collaborations,
    }).model_dump(by_alias=True)
    # The final check deliberately does not start another repair path.
    final_model_findings(model, index)
    return model


def resume_class_model(
    scenario: dict[str, Any], current: dict[str, Any],
) -> dict[str, Any]:
    """Complete only missing or invalid collaboration groups in an accepted skeleton."""

    index = build_scenario_index(scenario)
    model = BCEModel.model_validate(current).model_dump(by_alias=True)
    existing = {
        text(item.get("collaborationId")): item
        for item in model.get("Collaborations") or [] if isinstance(item, dict)
    }
    selected: list[ExecutionGroup] = []
    for group in index.groups:
        current_collaboration = existing.get(group.id)
        if current_collaboration is None:
            selected.append(group)
            continue
        report = run_checks(
            COLLABORATION_CHECKS,
            current_collaboration,
            CollaborationContext(index, model, group),
            parallel=True,
        )
        if report.errors or report.findings:
            selected.append(group)
    if not selected:
        return model
    workers = max(1, min(
        len(selected),
        int(getattr(settings, "design_class_behavior_parallelism", 2)),
    ))
    results = _replace_groups(index, model, selected, workers=workers)
    selected_ids = {group.id for group in selected}
    failures = [result for result in results if result.collaboration is None]
    working_model = model
    if failures:
        try:
            accepted_inventory = feedback_stage.inventory_from_model(model)
            fragments = feedback_stage.fragments_from_model(index, model)
            repaired_use_cases = operations.repair_failed_operations(
                index,
                accepted_inventory,
                fragments,
                failures,
            )
            working_model = operations.compose_fragments(accepted_inventory, fragments)
            affected = [
                group for group in index.groups
                if set(group.trace_use_case_ids) & repaired_use_cases
            ]
            retried = _replace_groups(
                index,
                working_model,
                affected,
                workers=workers,
            )
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
    working_model["Collaborations"] = [
        accepted[group.id] for group in index.groups if group.id in accepted
    ]
    return BCEModel.model_validate(working_model).model_dump(by_alias=True)


def revise_class_model(
    current: dict[str, Any],
    scenario: dict[str, Any],
    feedback: str,
    targets: set[str] | None = None,
) -> dict[str, Any]:
    """Apply feedback through its smallest owner and deterministically reassemble."""

    if not current or not feedback.strip():
        return current or {}
    index = build_scenario_index(scenario)
    scope = feedback_stage.feedback_scope(index, current, feedback, targets or set())
    accepted_inventory = feedback_stage.inventory_from_model(current)
    fragments = feedback_stage.fragments_from_model(index, current)
    existing = {
        text(item.get("collaborationId")): deepcopy(item)
        for item in current.get("Collaborations") or [] if isinstance(item, dict)
    }

    if scope.kind == "inventory":
        accepted_inventory = feedback_stage.propose_inventory_revision(
            index,
            accepted_inventory,
            feedback,
            set(scope.ids),
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
                    GroupResult(
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
                reserved_types=list(base.get("DataTypes") or []),
                operation="InteractionOperationFeedback",
            )
        skeleton = operations.compose_fragments(accepted_inventory, fragments)
        selected_groups = [
            group for group in index.groups
            if set(group.trace_use_case_ids) & selected_use_cases
        ]
    else:
        skeleton = BCEModel.model_validate({
            **deepcopy(current), "Collaborations": [],
        }).model_dump(by_alias=True)
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
        if (replacements.get(group.id) or existing.get(group.id)) is not None
    ]
    revised = BCEModel.model_validate({
        **skeleton,
        "Collaborations": collaborations,
    }).model_dump(by_alias=True)
    findings = final_model_findings(revised, index)
    if findings:
        raise ValueError("feedback result is invalid: " + "; ".join(
            f"{finding.location}: {finding.message}" for finding in findings
        ))
    return revised


__all__ = [
    "generate_class_model",
    "resume_class_model",
    "revise_class_model",
]



