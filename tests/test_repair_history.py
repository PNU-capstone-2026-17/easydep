from app.validation import (
    RepairAttempt,
    RepairLedger,
    repair_makes_progress,
    repair_retry_delay,
    stable_digest,
)


def _attempt(index: int, *, outcome: str = "improved") -> RepairAttempt:
    return RepairAttempt(
        attempt_id=f"attempt-{index}",
        created_at=f"2026-08-28T00:00:{index:02d}+00:00",
        stage="requirements.specifications",
        target_ids=("UC1",),
        strategy_key=f"strategy-{index}",
        input_digest=stable_digest({"revision": index}),
        candidate_digest=stable_digest({"candidate": index}),
        finding_keys_before=(f"rule-{index}",),
        finding_keys_after=() if outcome == "clean" else (f"rule-{index + 1}",),
        outcome=outcome,
    )


def test_repair_history_has_no_numeric_attempt_cutoff():
    ledger = RepairLedger(episode_id="episode")

    for index in range(100):
        ledger.record(_attempt(index))

    assert len(ledger.attempts) == 100
    assert ledger.status == "ACTIVE"


def test_repair_history_blocks_same_strategy_and_candidate_for_same_state():
    ledger = RepairLedger(episode_id="episode")
    attempt = _attempt(1, outcome="no_improvement")
    ledger.record(attempt)

    assert ledger.strategy_attempted(
        input_digest=attempt.input_digest,
        finding_keys=attempt.finding_keys_before,
        strategy_key=attempt.strategy_key,
    )
    assert ledger.candidate_seen(
        input_digest=attempt.input_digest,
        candidate_digest=attempt.candidate_digest,
    )
    assert not ledger.strategy_attempted(
        input_digest=stable_digest({"revision": "new"}),
        finding_keys=attempt.finding_keys_before,
        strategy_key=attempt.strategy_key,
    )


def test_prompt_context_keeps_all_unique_failures_and_only_recent_details():
    ledger = RepairLedger(episode_id="episode")
    for index in range(8):
        ledger.record(_attempt(index, outcome="no_improvement"))

    context = ledger.prompt_context(recent=5)

    assert '"olderAttemptCount": 3' in context
    assert '"rule-0"' in context
    assert '"rule-8"' in context
    assert '"strategy-0"' in context
    assert '"strategy-7"' in context


def test_progress_accepts_strict_reduction_or_validation_frontier_advance():
    assert repair_makes_progress(("a", "b"), ("b",))
    assert repair_makes_progress(("a",), ("b",), frontier_before=1, frontier_after=2)
    assert not repair_makes_progress(("a",), ("b",))
    assert not repair_makes_progress(("a",), ("a",))


def test_external_retry_delay_is_unbounded_but_interval_is_capped():
    assert [repair_retry_delay(index) for index in range(1, 7)] == [5, 15, 30, 60, 300, 300]
    assert repair_retry_delay(10_000) == 300
