"""Pure adapters from frozen feedback execution units to stage inputs.

These values only describe stage request shapes. Producing a
``DesignStageInput`` does not prove that the current design cascade will run
the class unit in isolation; the vertical executor must enforce that boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.artifact_trace import TraceRef
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


@dataclass(frozen=True)
class SequenceProjectionInput:
    execution_unit_id: str
    source_refs: tuple[str, ...]
    adapter: str
    version: str


StageInput = RequirementsStageInput | DesignStageInput | SequenceProjectionInput


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
        if unit.action is ExecutionAction.REPROJECT:
            return _sequence_projection(change_set, unit)
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


def _sequence_projection(change_set: ChangeSet, unit: ExecutionUnit) -> SequenceProjectionInput:
    if unit.artifact.kind != "sequence":
        raise StageAdapterError("reproject is supported only for sequence targets")
    sources = tuple(sorted({dependency.producer_ref.format() for dependency in unit.dependencies}))
    if not sources:
        raise StageAdapterError("sequence projection requires explicit source refs")
    consumer_ref = TraceRef(unit.artifact.kind, unit.artifact.element_id)
    consumer = (
        next(
            contract
            for contract in change_set.pre_change_trace.projection_contracts
            if contract.consumer == consumer_ref
        )
        if sum(
            contract.consumer == consumer_ref
            for contract in change_set.pre_change_trace.projection_contracts
        )
        == 1
        else None
    )
    if consumer is None or tuple(sorted(ref.format() for ref in consumer.producer_refs)) != sources:
        raise StageAdapterError("sequence projection contract is missing or mismatched")
    return SequenceProjectionInput(
        execution_unit_id=unit.execution_unit_id,
        source_refs=sources,
        adapter=consumer.adapter,
        version=consumer.version,
    )


def _validate_common(decision: Decision, unit: ExecutionUnit) -> None:
    if decision.status != "NORMALIZED" or decision.normalized_meaning is None:
        raise StageAdapterError("adapter requires a NORMALIZED decision")
    if not decision.normalized_meaning.requested_effect.strip():
        raise StageAdapterError("normalized instruction must not be blank")


__all__ = [
    "DesignStageInput",
    "RequirementsStageInput",
    "SequenceProjectionInput",
    "StageAdapterError",
    "StageInput",
    "adapt_execution_unit",
]
