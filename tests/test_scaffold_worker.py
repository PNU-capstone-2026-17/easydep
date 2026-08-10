import json
from pathlib import Path

from app.core.orchestration.scaffold_worker import (
    APPROVAL_MISMATCH,
    MemberPlannerExhausted,
    _preserve_failed_generation_cache,
    _run_member_workflow_with_current_approvals,
)


def test_scaffold_retry_preserves_every_prior_generation_before_rebuild(tmp_path):
    output_root = tmp_path / "generated" / "runs"
    failed = output_root / "run_failed" / "reports"
    complete = output_root / "run_complete" / "reports"
    failed.mkdir(parents=True)
    complete.mkdir(parents=True)
    (failed / "run-manifest.json").write_text(
        json.dumps({"status": "FAILED"}), encoding="utf-8"
    )
    (complete / "run-manifest.json").write_text(
        json.dumps({"status": "COMPLETE"}), encoding="utf-8"
    )
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps(
            {"workspaceRoot": str(tmp_path), "outputRoot": "generated/runs"}
        ),
        encoding="utf-8",
    )

    preserved = _preserve_failed_generation_cache(job)

    assert len(preserved) == 2
    assert not (output_root / "run_failed").exists()
    assert not (output_root / "run_complete").exists()
    assert all(Path(path, "reports", "run-manifest.json").is_file() for path in preserved)


def test_member_workflow_refreshes_only_stale_transmission_approval(monkeypatch, tmp_path):
    calls = []

    def run(*_args, **kwargs):
        assert kwargs["max_cycles"] == 1
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError(APPROVAL_MISMATCH)
        return {"status": "COMPLETE"}

    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.run_workflow_to_completion", run
    )

    result = _run_member_workflow_with_current_approvals(
        tmp_path, object(), approved_by="tester"
    )

    assert result["status"] == "COMPLETE"
    assert len(calls) == 2


def test_member_workflow_does_not_hide_other_approval_failures(monkeypatch, tmp_path):
    def run(*_args, **_kwargs):
        raise PermissionError("approval scope mismatch")

    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.run_workflow_to_completion", run
    )

    import pytest

    with pytest.raises(PermissionError, match="scope mismatch"):
        _run_member_workflow_with_current_approvals(
            tmp_path, object(), approved_by="tester"
        )


def test_member_workflow_does_not_refresh_approval_beyond_repair_limit(
    monkeypatch, tmp_path
):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "one-time-run-approval.json").write_text(
        json.dumps({"delegationScope": {"maxRepairRounds": 3}}), encoding="utf-8"
    )
    (reports / "repair-plan.json").write_text(
        json.dumps({"entries": [{"revision": 4}]}), encoding="utf-8"
    )

    def run(*_args, **_kwargs):
        raise PermissionError(APPROVAL_MISMATCH)

    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.run_workflow_to_completion", run
    )

    import pytest

    with pytest.raises(RuntimeError, match="repair-round limit"):
        _run_member_workflow_with_current_approvals(
            tmp_path, object(), approved_by="tester"
        )


def test_member_workflow_stops_repeating_one_failed_task(monkeypatch, tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "workflow-state.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {"taskId": "task-a", "status": "PENDING", "attempts": 4}
                ]
            }
        ),
        encoding="utf-8",
    )

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("member workflow must stop before another attempt")

    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.run_workflow_to_completion",
        must_not_run,
    )

    import pytest

    with pytest.raises(MemberPlannerExhausted, match="exceeded 4 attempts"):
        _run_member_workflow_with_current_approvals(
            tmp_path, object(), approved_by="tester"
        )
