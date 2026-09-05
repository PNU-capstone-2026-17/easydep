"""Typed, fail-closed adapters from a revision plan to delivery inputs.

The planner decides ownership.  This module only translates its already
validated targets to the small input shapes accepted by the delivery services;
it never parses a ref string to guess a stage, follows pipeline order, or
widens scope from the instruction text.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.artifact_trace import TraceRef
from app.design.service import BatchReviseRequest, ReviseRequest
from app.requirements.contracts.request import FeedbackEdit

from .contracts import RevisionPlan, RevisionTarget


class RevisionDeliveryError(ValueError):
    """A plan cannot be represented by a supported bounded delivery input."""


_REQUIREMENTS_STAGE_BY_KIND = {
    # Refined requirements are edited by the actors feedback path.  The
    # requirements orchestrator owns that mapping; this adapter only records
    # the explicit service stage rather than deriving it from ref text.
    "use_case": "use_cases",
    "use_case_spec": "specs",
}


@dataclass(frozen=True, slots=True)
class DesignRevisionPayload:
    """The bounded design-service input for one approved revision plan."""

    revisions: tuple[ReviseRequest, ...]
    approved_authority_targets: tuple[str, ...]
    approved_downstream_targets: tuple[str, ...]

    def batch_request(self) -> BatchReviseRequest:
        """Build the design service request without dropping frozen scope."""
        return BatchReviseRequest(revisions=list(self.revisions))


@dataclass(frozen=True, slots=True)
class ImplementationRevisionPayload:
    """The only target form accepted by implementation feedback execution."""

    confirmed_target_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TestingRepairPayload:
    """Evidence-backed bridge to the existing implementation repair path."""

    confirmed_target_refs: tuple[str, ...]
    repair_file_hints: tuple[str, ...]


def requirements_feedback_edit(
    targets: Iterable[RevisionTarget], instruction: str
) -> FeedbackEdit:
    """Translate requirements targets into one local ``FeedbackEdit``.

    The requirements service accepts one modeling stage per edit.  The adapter
    uses catalog-owned ``kind`` and ``element_id`` fields, never a partitioned
    ``ref`` string.
    """
    selected = _unique_targets(targets)
    _require_instruction(instruction)
    if not selected or any(target.owner != "requirements" for target in selected):
        raise RevisionDeliveryError("Requirements delivery requires requirements-owned targets.")
    if len(selected) == 1 and selected[0].kind == "requirements_stage":
        stage = selected[0].element_id
        if stage not in {"actors", "relationships"}:
            raise RevisionDeliveryError("The requirements stage has no broad revision adapter.")
        return FeedbackEdit(
            stage=stage,  # type: ignore[arg-type]
            scope="broad",
            target_ids=[],
            instruction=instruction.strip(),
        )
    try:
        stages = {_REQUIREMENTS_STAGE_BY_KIND[target.kind] for target in selected}
    except KeyError as error:
        raise RevisionDeliveryError(
            "Requirements delivery does not support the selected target kind."
        ) from error
    if len(stages) != 1:
        raise RevisionDeliveryError("Requirements delivery requires one modeling stage.")
    target_ids = tuple(target.element_id for target in selected)
    if len(set(target_ids)) != len(target_ids):
        raise RevisionDeliveryError("Requirements delivery target IDs must be unique.")
    return FeedbackEdit(
        stage=stages.pop(),  # type: ignore[arg-type]
        scope="local",
        target_ids=list(target_ids),
        instruction=instruction.strip(),
    )


def design_revision_payload(
    plan: RevisionPlan,
    instruction: str,
    *,
    instructions_by_ref: Mapping[str, str] | None = None,
) -> DesignRevisionPayload:
    """Create explicit design refs plus frozen authority/downstream boundaries."""
    _require_instruction(instruction)
    authority = _execution_targets(plan)
    if not authority or any(target.owner != "design" for target in authority):
        raise RevisionDeliveryError("Design delivery requires design-owned authority targets.")
    authority_refs = _refs(authority)
    downstream_refs = _refs(plan.downstream_targets)
    revisions = tuple(
        ReviseRequest(
            target=target.ref,
            feedback=str((instructions_by_ref or {}).get(target.ref) or instruction).strip(),
            approved_authority_targets=list(authority_refs),
            approved_downstream_targets=list(downstream_refs),
        )
        for target in authority
    )
    return DesignRevisionPayload(
        revisions=revisions,
        approved_authority_targets=authority_refs,
        approved_downstream_targets=downstream_refs,
    )


def implementation_revision_payload(
    targets: Iterable[RevisionTarget],
) -> ImplementationRevisionPayload:
    """Return only validated implementation ``file``/``task`` refs.

    A missing or broader target is an error.  Passing ``None`` would make the
    downstream worker decide scope from feedback prose, which this boundary
    intentionally never permits.
    """
    selected = _unique_targets(targets)
    if not selected:
        raise RevisionDeliveryError("Implementation delivery requires at least one target.")
    invalid = [
        target.ref
        for target in selected
        if target.owner != "implementation" or target.kind not in {"file", "task"}
    ]
    if invalid:
        raise RevisionDeliveryError(
            "Implementation delivery supports only validated file or task targets: "
            + ", ".join(invalid)
        )
    return ImplementationRevisionPayload(confirmed_target_refs=_refs(selected))


def repair_payload_from_testing_evidence(
    evidence: Mapping[str, object],
    targets: Iterable[RevisionTarget],
) -> TestingRepairPayload:
    """Bridge a testing finding only when its existing repair evidence is exact.

    Testing itself has no target-mutation service.  This function therefore
    exposes a repair payload only for an explicitly implementation-owned
    finding with trace-backed file/task targets and file evidence.  It does
    not mark a finding or a test projection editable.
    """
    if str(evidence.get("repair_owner") or "") != "implementation":
        raise RevisionDeliveryError("Testing evidence does not assign repair to implementation.")
    implementation = implementation_revision_payload(targets)
    trace_refs = _string_values(evidence.get("trace_refs"))
    file_hints = _string_values(evidence.get("file_hints"))
    confirmed = set(implementation.confirmed_target_refs)
    trace_confirmed = confirmed.intersection(trace_refs)
    file_confirmed = set()
    for ref in confirmed:
        parsed = TraceRef.parse(ref)
        if parsed.kind == "file" and parsed.id in file_hints:
            file_confirmed.add(ref)
    if not trace_confirmed and not file_confirmed:
        raise RevisionDeliveryError(
            "Testing evidence does not prove an exact implementation repair target."
        )
    if not file_hints:
        raise RevisionDeliveryError("Testing evidence has no trace-backed generated file.")
    return TestingRepairPayload(
        confirmed_target_refs=implementation.confirmed_target_refs,
        repair_file_hints=tuple(sorted(set(file_hints))),
    )


def revision_delivery_payload(
    plan: RevisionPlan,
    instruction: str,
    *,
    instructions_by_ref: Mapping[str, str] | None = None,
) -> FeedbackEdit | DesignRevisionPayload | ImplementationRevisionPayload:
    """Dispatch one executable plan to its supported typed delivery payload."""
    targets = _execution_targets(plan)
    owners = {target.owner for target in targets}
    if len(owners) != 1:
        raise RevisionDeliveryError("Revision delivery requires one delivery owner.")
    owner = owners.pop()
    if owner == "requirements":
        return requirements_feedback_edit(targets, instruction)
    if owner == "design":
        return design_revision_payload(
            plan,
            instruction,
            instructions_by_ref=instructions_by_ref,
        )
    if owner == "implementation":
        return implementation_revision_payload(targets)
    raise RevisionDeliveryError(
        "Testing revisions have no direct mutation capability; use exact repair evidence instead."
    )


def _execution_targets(plan: RevisionPlan) -> tuple[RevisionTarget, ...]:
    return _unique_targets(plan.authority_targets or plan.requested_targets)


def _unique_targets(targets: Iterable[RevisionTarget]) -> tuple[RevisionTarget, ...]:
    selected = tuple(targets)
    if len({target.ref for target in selected}) != len(selected):
        raise RevisionDeliveryError("Revision targets must be unique.")
    return selected


def _refs(targets: Iterable[RevisionTarget]) -> tuple[str, ...]:
    return tuple(sorted({target.ref for target in targets}))


def _string_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(
        sorted({item.strip() for item in value if isinstance(item, str) and item.strip()})
    )


def _require_instruction(instruction: str) -> None:
    if not isinstance(instruction, str) or not instruction.strip():
        raise RevisionDeliveryError("Revision delivery requires a non-empty instruction.")


__all__ = [
    "DesignRevisionPayload",
    "ImplementationRevisionPayload",
    "RevisionDeliveryError",
    "TestingRepairPayload",
    "design_revision_payload",
    "implementation_revision_payload",
    "repair_payload_from_testing_evidence",
    "requirements_feedback_edit",
    "revision_delivery_payload",
]
