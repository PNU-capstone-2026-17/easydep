"""Pure adapters from frozen feedback execution units to stage inputs.

These values only describe stage request shapes. ``DesignStageInput`` is
consumed by the existing class cascade, which may complete its related
deterministic sequence projection in the same composite operation. There is no
separate sequence execution input here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.design.service import ReviseRequest
from app.requirements.contracts.request import FeedbackEdit
from app.workspace.conversation.feedback_change_plan import (
    ChangeSet,
    ExecutionAction,
    ExecutionUnit,
)
from app.workspace.conversation.feedback_envelope import Decision


class StageAdapterError(ValueError):
    """A frozen unit cannot be represented by a supported stage input."""


@dataclass(frozen=True)
class RequirementsStageInput:
    execution_unit_id: str
    edit: FeedbackEdit


@dataclass(frozen=True)
class DesignStageInput:
    """A design request shape, not an independently executable unit."""

    execution_unit_id: str
    revision: ReviseRequest


StageInput = RequirementsStageInput | DesignStageInput


def adapt_execution_unit(
    change_set: ChangeSet,
    execution_unit_id: str,
) -> StageInput:
    """Convert one unit; never infer a target from its display/ref string."""
    decision = change_set.decision_snapshot
    unit = next(
        (
            candidate
            for candidate in change_set.execution_units
            if candidate.execution_unit_id == execution_unit_id
        ),
        None,
    )
    if unit is None:
        raise StageAdapterError("execution unit is not a member of the ChangeSet")
    _validate_common(decision, unit)
    if unit.owner == "requirements":
        return _requirements(decision, unit)
    if unit.owner == "design":
        return _design(decision, unit)
    raise StageAdapterError(f"unsupported execution owner: {unit.owner}")


def _requirements(decision: Decision, unit: ExecutionUnit) -> RequirementsStageInput:
    if unit.action is not ExecutionAction.REBUILD:
        raise StageAdapterError("requirements adapter supports rebuild only")
    target = unit.artifact
    stage: Literal["use_cases", "specs"]
    if target.kind == "use_case":
        stage = "use_cases"
    elif target.kind == "use_case_spec":
        stage = "specs"
    else:
        raise StageAdapterError(f"unsupported requirements target kind: {target.kind}")
    return RequirementsStageInput(
        execution_unit_id=unit.execution_unit_id,
        edit=FeedbackEdit(
            stage=stage,
            scope="local",
            target_ids=[target.element_id],
            instruction=decision.normalized_meaning.requested_effect,
        ),
    )


def _design(decision: Decision, unit: ExecutionUnit) -> DesignStageInput:
    if unit.action is not ExecutionAction.REBUILD:
        raise StageAdapterError("design adapter supports rebuild only")
    target = unit.artifact
    if target.kind != "class":
        raise StageAdapterError(f"unsupported design target kind: {target.kind}")
    return DesignStageInput(
        execution_unit_id=unit.execution_unit_id,
        revision=ReviseRequest(
            target=target.ref,
            feedback=decision.normalized_meaning.requested_effect,
            approved_authority_targets=[target.ref],
            approved_downstream_targets=None,
        ),
    )


def _validate_common(decision: Decision, unit: ExecutionUnit) -> None:
    if decision.status != "NORMALIZED" or decision.normalized_meaning is None:
        raise StageAdapterError("adapter requires a NORMALIZED decision")
    if not decision.normalized_meaning.requested_effect.strip():
        raise StageAdapterError("normalized instruction must not be blank")


__all__ = [
    "DesignStageInput",
    "RequirementsStageInput",
    "StageAdapterError",
    "StageInput",
    "adapt_execution_unit",
]
