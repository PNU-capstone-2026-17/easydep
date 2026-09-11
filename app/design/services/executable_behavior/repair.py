"""Bounded repair policy for scripted validation of the proposed methodology."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .contracts import canonical_digest


class FailureCategory(StrEnum):
    """Categories that must not be collapsed into a generic retry."""

    INVALID_CANDIDATE = "INVALID_CANDIDATE"
    NO_WITNESS_WITHIN_BOUND = "NO_WITNESS_WITHIN_BOUND"
    UNSAT_UNDER_CONTRACT = "UNSAT_UNDER_CONTRACT"
    SPECIFICATION_GAP = "SPECIFICATION_GAP"
    VALIDATOR_ERROR = "VALIDATOR_ERROR"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"


class RepairOwner(StrEnum):
    SCENARIO = "SCENARIO"
    OPERATION = "OPERATION"
    CATALOG = "CATALOG"
    CALL_STRUCTURE = "CALL_STRUCTURE"
    BINDING = "BINDING"
    MATERIALIZER = "MATERIALIZER"
    PROVIDER = "PROVIDER"
    VALIDATOR = "VALIDATOR"


class RepairAction(StrEnum):
    RETRY_OWNER = "RETRY_OWNER"
    REOPEN_UPSTREAM = "REOPEN_UPSTREAM"
    RETRY_PROVIDER = "RETRY_PROVIDER"
    STOP = "STOP"


@dataclass(frozen=True)
class RepairFinding:
    code: str
    category: FailureCategory
    owner: RepairOwner
    message: str
    related_ids: tuple[str, ...] = ()

    def normalized(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category.value,
            "owner": self.owner.value,
            "relatedIds": sorted(set(self.related_ids)),
        }


@dataclass(frozen=True)
class RepairBudget:
    max_attempts: int = 8
    max_attempts_per_owner: int = 3

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.max_attempts_per_owner < 1:
            raise ValueError("repair budgets must be positive")


@dataclass(frozen=True)
class RepairAttempt:
    number: int
    owner: RepairOwner
    input_digest: str
    candidate_digest: str
    findings_digest: str
    state_digest: str


class RepairPolicyError(RuntimeError):
    """Base class for a repair loop that must terminate."""


class RepeatedRepairState(RepairPolicyError):
    """The same inputs, candidate, and findings have already been attempted."""


class RepairBudgetExceeded(RepairPolicyError):
    """The shared repair budget has been exhausted."""


def repair_action(findings: Iterable[RepairFinding]) -> RepairAction:
    """Return one unambiguous action; mixed ownership never triggers a blind retry."""
    values = tuple(findings)
    if not values:
        raise ValueError("a repair decision requires at least one finding")
    categories = {item.category for item in values}
    owners = {item.owner for item in values}
    if categories == {FailureCategory.PROVIDER_FAILURE}:
        return RepairAction.RETRY_PROVIDER
    if categories == {FailureCategory.INVALID_CANDIDATE} and len(owners) == 1:
        return RepairAction.RETRY_OWNER
    if FailureCategory.UNSAT_UNDER_CONTRACT in categories:
        return RepairAction.REOPEN_UPSTREAM
    return RepairAction.STOP


class RepairLedger:
    """Track a single shared budget and reject normalized repeat states."""

    def __init__(self, budget: RepairBudget | None = None) -> None:
        self.budget = budget or RepairBudget()
        self._attempts: list[RepairAttempt] = []
        self._states: set[str] = set()

    @property
    def attempts(self) -> tuple[RepairAttempt, ...]:
        return tuple(self._attempts)

    def record(
        self,
        *,
        owner: RepairOwner,
        input_snapshot: Mapping[str, Any] | Any,
        candidate: Mapping[str, Any] | Any,
        findings: Iterable[RepairFinding],
    ) -> RepairAttempt:
        values = tuple(findings)
        if not values:
            raise ValueError("a failed attempt requires at least one finding")
        if any(item.owner != owner for item in values):
            raise ValueError("repair findings must name the recorded owner")
        input_digest = canonical_digest(input_snapshot)
        candidate_digest = canonical_digest(candidate)
        findings_digest = canonical_digest(
            sorted(
                (item.normalized() for item in values),
                key=canonical_digest,
            )
        )
        state_digest = canonical_digest({
            "owner": owner.value,
            "input": input_digest,
            "candidate": candidate_digest,
            "findings": findings_digest,
        })
        if state_digest in self._states:
            raise RepeatedRepairState(
                f"repair state repeated for {owner.value}; retry must terminate"
            )
        owner_counts = Counter(item.owner for item in self._attempts)
        if len(self._attempts) >= self.budget.max_attempts:
            raise RepairBudgetExceeded("shared repair attempt budget exhausted")
        if owner_counts[owner] >= self.budget.max_attempts_per_owner:
            raise RepairBudgetExceeded(
                f"repair attempt budget exhausted for {owner.value}"
            )
        attempt = RepairAttempt(
            number=len(self._attempts) + 1,
            owner=owner,
            input_digest=input_digest,
            candidate_digest=candidate_digest,
            findings_digest=findings_digest,
            state_digest=state_digest,
        )
        self._states.add(state_digest)
        self._attempts.append(attempt)
        return attempt


__all__ = [
    "FailureCategory",
    "RepairAction",
    "RepairAttempt",
    "RepairBudget",
    "RepairBudgetExceeded",
    "RepairFinding",
    "RepairLedger",
    "RepairOwner",
    "RepairPolicyError",
    "RepeatedRepairState",
    "repair_action",
]
