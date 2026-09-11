import pytest

from app.design.services.executable_behavior import (
    FailureCategory,
    RepairAction,
    RepairBudget,
    RepairBudgetExceeded,
    RepairFinding,
    RepairLedger,
    RepairOwner,
    RepeatedRepairState,
    repair_action,
)


def finding(
    category: FailureCategory = FailureCategory.INVALID_CANDIDATE,
    owner: RepairOwner = RepairOwner.BINDING,
) -> RepairFinding:
    return RepairFinding("BINDING_SOURCE_MISSING", category, owner, "source missing")


def test_repeat_detection_uses_input_candidate_and_normalized_findings() -> None:
    ledger = RepairLedger()
    ledger.record(
        owner=RepairOwner.BINDING,
        input_snapshot={"catalog": 1},
        candidate={"source": "a"},
        findings=(finding(),),
    )

    with pytest.raises(RepeatedRepairState, match="must terminate"):
        ledger.record(
            owner=RepairOwner.BINDING,
            input_snapshot={"catalog": 1},
            candidate={"source": "a"},
            findings=(finding(),),
        )

    ledger.record(
        owner=RepairOwner.BINDING,
        input_snapshot={"catalog": 1},
        candidate={"source": "b"},
        findings=(finding(),),
    )
    assert len(ledger.attempts) == 2


def test_repair_budget_is_shared_and_bounded_per_owner() -> None:
    ledger = RepairLedger(RepairBudget(max_attempts=3, max_attempts_per_owner=2))
    ledger.record(
        owner=RepairOwner.BINDING,
        input_snapshot={},
        candidate={"try": 1},
        findings=(finding(),),
    )
    ledger.record(
        owner=RepairOwner.BINDING,
        input_snapshot={},
        candidate={"try": 2},
        findings=(finding(),),
    )

    with pytest.raises(RepairBudgetExceeded, match="BINDING"):
        ledger.record(
            owner=RepairOwner.BINDING,
            input_snapshot={},
            candidate={"try": 3},
            findings=(finding(),),
        )


def test_only_one_owner_candidate_errors_are_retried_locally() -> None:
    assert repair_action((finding(),)) is RepairAction.RETRY_OWNER
    assert repair_action(
        (finding(FailureCategory.PROVIDER_FAILURE, RepairOwner.PROVIDER),)
    ) is RepairAction.RETRY_PROVIDER
    assert repair_action(
        (finding(FailureCategory.UNSAT_UNDER_CONTRACT),)
    ) is RepairAction.REOPEN_UPSTREAM
    assert repair_action(
        (finding(FailureCategory.SPECIFICATION_GAP, RepairOwner.SCENARIO),)
    ) is RepairAction.STOP
