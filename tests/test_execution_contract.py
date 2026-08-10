from __future__ import annotations

from evaluation.execution_contract import censored, may_start_bundle, retry_delay


def test_censoring_is_orthogonal_to_subject_outcome():
    record = censored(
        phase="stabilize", reason="providerOperationTimeout", elapsed_seconds=2700
    )

    assert record["subjectOutcome"] == "notObserved"
    assert record["executionStatus"] == "censored"
    assert record["censorReason"] == "providerOperationTimeout"


def test_budget_gate_preserves_cleanup_reserve_and_blocks_residuals():
    assert may_start_bundle(
        actual_campaign_cost_usd=120, estimated_bundle_cost_usd=10
    ) == (True, "within-budget")
    assert may_start_bundle(
        actual_campaign_cost_usd=130, estimated_bundle_cost_usd=10
    ) == (False, "cleanup-reserve-would-be-consumed")
    assert may_start_bundle(
        actual_campaign_cost_usd=0, estimated_bundle_cost_usd=1,
        residual_resources=["resource-1"],
    ) == (False, "residual-resources-block-provider")


def test_transient_retry_schedule_is_bounded():
    assert [retry_delay(index) for index in range(1, 4)] == [15, 30, None]
