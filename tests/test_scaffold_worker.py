import json
from pathlib import Path

from app.implementation.runtime.scaffold_worker import (
    APPROVAL_MISMATCH,
    _explicit_checkpoint,
    _preserve_failed_generation_cache,
    _run_member_workflow_with_current_approvals,
)


def test_explicit_checkpoint_requires_same_job_and_input_hashes(monkeypatch, tmp_path):
    source = tmp_path / "design.json"
    source.write_text('{"version": 1}', encoding="utf-8")
    import hashlib

    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    checkpoint = tmp_path / "generated" / "runs" / "run_valid"
    reports = checkpoint / "reports"
    reports.mkdir(parents=True)
    (reports / "run-manifest.json").write_text(
        json.dumps(
            {
                "status": "SUCCEEDED",
                "job_name": "example",
                "inputs": {"design": {"sha256": digest}},
            }
        ),
        encoding="utf-8",
    )
    (reports / "workflow-state.json").write_text(
        json.dumps({"status": "FAILED", "tasks": []}), encoding="utf-8"
    )
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps(
            {
                "name": "example",
                "workspaceRoot": str(tmp_path),
                "outputRoot": "generated/runs",
                "inputs": {"design": "design.json"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("EASYDEP_MEMBER_CHECKPOINT_RUN", "run_valid")

    assert _explicit_checkpoint(job) == checkpoint

    source.write_text('{"version": 2}', encoding="utf-8")
    import pytest

    with pytest.raises(ValueError, match="does not match"):
        _explicit_checkpoint(job)


def test_scaffold_retry_quarantines_failed_generation_but_keeps_task_checkpoint(
    tmp_path,
):
    output_root = tmp_path / "generated" / "runs"
    failed = output_root / "run_failed" / "reports"
    complete = output_root / "run_complete" / "reports"
    failed.mkdir(parents=True)
    complete.mkdir(parents=True)
    (failed / "run-manifest.json").write_text(
        json.dumps({"status": "FAILED"}), encoding="utf-8"
    )
    (complete / "run-manifest.json").write_text(
        json.dumps({"status": "SUCCEEDED"}), encoding="utf-8"
    )
    workflow = complete / "workflow-state.json"
    workflow.write_text(
        json.dumps(
            {
                "status": "RUNNING",
                "tasks": [
                    {"taskId": "done", "status": "SUCCEEDED"},
                    {"taskId": "next", "status": "RUNNING"},
                ],
            }
        ),
        encoding="utf-8",
    )
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps(
            {"workspaceRoot": str(tmp_path), "outputRoot": "generated/runs"}
        ),
        encoding="utf-8",
    )

    preserved = _preserve_failed_generation_cache(job)

    assert len(preserved) == 1
    assert not (output_root / "run_failed").exists()
    assert (output_root / "run_complete").is_dir()
    assert json.loads(workflow.read_text(encoding="utf-8"))["tasks"][0][
        "status"
    ] == "SUCCEEDED"
    assert all(Path(path, "reports", "run-manifest.json").is_file() for path in preserved)


def test_member_workflow_refreshes_only_stale_transmission_approval(monkeypatch, tmp_path):
    calls = []

    def run(*_args, **kwargs):
        assert kwargs["max_cycles"] == 1
        assert kwargs["retry_failed"] is True
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError(APPROVAL_MISMATCH)
        return {"status": "COMPLETE"}

    monkeypatch.setattr(
        "app.implementation.workflows.coordinator.run_workflow_to_completion", run
    )

    result = _run_member_workflow_with_current_approvals(
        tmp_path, object(), approved_by="tester", retry_failed=True
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
