from __future__ import annotations

from app.testing import progress


def test_planning_progress_does_not_count_as_test_pass():
    snapshot = None
    for state in ("PENDING", "RUNNING", "PASS"):
        snapshot = progress.reduce_testing_progress(snapshot, progress.testing_progress_event(
            phase="planning", scope="workflow", status=state, label="UC7 · Join waitlist",
            workflow_id="workflow-UC7", use_case_id="UC7", use_case_name="Join waitlist",
        ))
    snapshot = progress.reduce_testing_progress(snapshot, progress.testing_progress_event(
        phase="dynamic", scope="workflow", status="RUNNING", label="Executing UC7",
        workflow_id="workflow-UC7",
    ))
    assert snapshot["plans"]["workflow-UC7"]["status"] == "PASS"
    assert snapshot["plans"]["workflow-UC7"]["use_case_name"] == "Join waitlist"
    assert snapshot["plan_counts"]["passed"] == 1
    assert snapshot["workflow_counts"]["passed"] == 0


def test_progress_reducer_tracks_workflows_steps_and_gates() -> None:
    snapshot = progress.reduce_testing_progress(
        None,
        progress.testing_progress_event(
            phase="dynamic",
            scope="workflow",
            status="RUNNING",
            label="Create calculation",
            workflow_id="workflow-UC-1",
            total_workflows=2,
            total_steps=2,
            updated_at="2026-09-07T00:00:00+00:00",
        ),
    )
    snapshot = progress.reduce_testing_progress(
        snapshot,
        progress.testing_progress_event(
            phase="dynamic",
            scope="step",
            status="PASS",
            label="POST /calculations",
            workflow_id="workflow-UC-1",
            step_id="create",
            operation_id="createCalculation",
            method="post",
            path="/calculations",
            status_code=201,
            contract_status="pass",
            semantic_status="pass",
            elapsed_ms=12,
            updated_at="2026-09-07T00:00:01+00:00",
        ),
    )
    assert snapshot["active_workflow_id"] == "workflow-UC-1"
    snapshot = progress.reduce_testing_progress(
        snapshot,
        progress.testing_progress_event(
            phase="static",
            scope="gate",
            status="RUNNING",
            label="Validating infrastructure code",
            gate="iac",
            updated_at="2026-09-07T00:00:02+00:00",
        ),
    )

    workflow = snapshot["workflows"]["workflow-UC-1"]
    assert workflow["status"] == "RUNNING"
    assert workflow["total_steps"] == 2
    assert workflow["steps"]["create"]["operation_id"] == "createCalculation"
    assert workflow["steps"]["create"]["method"] == "POST"
    assert workflow["steps"]["create"]["status_code"] == 201
    assert workflow["steps"]["create"]["semantic_status"] == "PASS"
    assert snapshot["gates"]["iac"]["status"] == "RUNNING"
    assert snapshot["active_gate"] == "iac"
    assert snapshot["workflow_counts"] == {
        "total": 2,
        "passed": 0,
        "failed": 0,
        "running": 1,
        "pending": 0,
        "reused": 0,
        "inconclusive": 0,
    }


def test_scoped_observer_is_restored_and_failures_do_not_change_execution() -> None:
    received: list[dict[str, object]] = []

    with progress.testing_progress_scope(received.append):
        assert progress.testing_progress_enabled() is True
        progress.emit_testing_progress(
            phase="prepare",
            scope="phase",
            status="RUNNING",
            label="Preparing fixed snapshot",
        )
    progress.emit_testing_progress(
        phase="prepare",
        scope="phase",
        status="PASS",
        label="Prepared fixed snapshot",
    )
    assert progress.testing_progress_enabled() is False

    assert [item["status"] for item in received] == ["RUNNING"]

    def broken(_event: dict[str, object]) -> None:
        raise RuntimeError("UI observer unavailable")

    with progress.testing_progress_scope(broken):
        progress.emit_testing_progress(
            phase="summary",
            scope="phase",
            status="PASS",
            label="Testing report ready",
        )
