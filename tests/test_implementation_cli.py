from __future__ import annotations

import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.implementation.agents.workspace import load_strict_task
from app.implementation.interfaces import cli
from app.implementation.workflows.repair import ReviewerProviderError, apply_repair_directives


def test_run_owner_validates_one_task_without_running_a_workflow(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    job_path = tmp_path / "job" / "job.json"
    run_root = tmp_path / "job" / "generated" / "runs" / "run-1"
    run_root.mkdir(parents=True)
    job_path.parent.mkdir(exist_ok=True)
    job_path.write_text("{}", encoding="utf-8")
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        cli,
        "load_job",
        lambda path: (
            calls.append(("job", path)),
            SimpleNamespace(output_root=tmp_path / "job" / "generated" / "runs"),
        )[1],
    )
    monkeypatch.setattr(
        cli,
        "load_strict_task",
        lambda run, task_id, *, allowed_task_types: calls.append(
            ("task", (run, task_id, allowed_task_types))
        )
        or {"task_id": task_id},
    )
    monkeypatch.setattr(
        cli,
        "execute_openhands_task",
        lambda run, task_id: calls.append(("execute", (run, task_id)))
        or {"task_id": task_id, "status": "SUCCEEDED"},
    )
    monkeypatch.setattr(
        cli, "plan_workflow", lambda *_args: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(
        cli, "run_workflow", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))

    assert cli.main(["run-owner", str(run_root), str(job_path), "implement-backend"]) == 0

    assert calls == [
        ("job", job_path.resolve()),
        ("task", (run_root.resolve(), "implement-backend", cli.OWNER_TASK_TYPES)),
        ("execute", (run_root.resolve(), "implement-backend")),
    ]
    assert json.loads(capsys.readouterr().out) == {
        "task_id": "implement-backend",
        "status": "SUCCEEDED",
    }


def test_run_workflow_emits_only_a_structured_provider_validation_marker(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    job_path = tmp_path / "job.json"
    run_root = tmp_path / "run"
    monkeypatch.setattr(
        cli,
        "load_job",
        lambda _path: SimpleNamespace(output_root=tmp_path),
    )
    monkeypatch.setattr(
        cli,
        "run_workflow",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ReviewerProviderError({"status": 400, "message": "not emitted"})
        ),
    )

    assert cli.main(["run-workflow", str(run_root), str(job_path)]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "failure": {
            "kind": "provider_request_validation",
            "status_code": 400,
        }
    }


def _write_owner_task(
    run_root: Path, task_id: str, task_type: str = "backend-implementation"
) -> dict[str, object]:
    reports = run_root / "reports"
    tasks = reports / "implementation-tasks"
    tasks.mkdir(parents=True)
    prompt = tasks / f"{task_id}.prompt.md"
    prompt.write_text("current prompt", encoding="utf-8")
    context = tasks / f"{task_id}.context.json"
    context.write_text("{}", encoding="utf-8")
    import hashlib

    task = {
        "task_id": task_id,
        "task_type": task_type,
        "prompt_file": prompt.relative_to(run_root).as_posix(),
        "context_file": context.relative_to(run_root).as_posix(),
        "prompt_sha256": hashlib.sha256(prompt.read_bytes()).hexdigest(),
    }
    (tasks / f"{task_id}.task.json").write_text(json.dumps(task), encoding="utf-8")
    (reports / "run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [task]}), encoding="utf-8"
    )
    return task


def test_strict_owner_task_loader_rejects_duplicate_or_non_owner(tmp_path: Path) -> None:
    duplicate_run = tmp_path / "duplicate"
    task = _write_owner_task(duplicate_run, "owner-1")
    duplicate = duplicate_run / "reports" / "implementation-tasks" / "other.task.json"
    duplicate.write_text(json.dumps(task), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one task file"):
        load_strict_task(duplicate_run, "owner-1")

    non_owner_run = tmp_path / "non-owner"
    _write_owner_task(non_owner_run, "control-1", "control")
    with pytest.raises(ValueError, match="not an owner task"):
        load_strict_task(
            non_owner_run,
            "control-1",
            allowed_task_types=cli.OWNER_TASK_TYPES,
        )


def test_strict_owner_task_loader_rejects_runtime_scope_mismatch(tmp_path: Path) -> None:
    run_root = tmp_path / "scope-mismatch"
    _write_owner_task(run_root, "owner-1")
    sidecar_path = run_root / "reports" / "implementation-tasks" / "owner-1.task.json"
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["allowed_write_paths"] = ["backend/src/main/java/Unexpected.java"]
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")

    with pytest.raises(ValueError, match="sidecar and manifest disagree"):
        load_strict_task(
            run_root,
            "owner-1",
            allowed_task_types=cli.OWNER_TASK_TYPES,
        )


def test_strict_owner_task_loader_normalizes_crlf_prompt_hash(tmp_path: Path) -> None:
    run_root = tmp_path / "crlf"
    task = _write_owner_task(run_root, "owner-1")
    prompt = run_root / str(task["prompt_file"])
    prompt.write_bytes(b"current\r\nprompt\r\n")
    import hashlib

    task["prompt_sha256"] = hashlib.sha256(b"current\nprompt\n").hexdigest()
    task_file = run_root / "reports" / "implementation-tasks" / "owner-1.task.json"
    task_file.write_text(json.dumps(task), encoding="utf-8")
    (run_root / "reports" / "run-manifest.json").write_text(
        json.dumps({"implementation_tasks": [task]}), encoding="utf-8"
    )

    assert load_strict_task(
        run_root,
        "owner-1",
        allowed_task_types=cli.OWNER_TASK_TYPES,
    ) == task


def test_strict_owner_loader_accepts_system_repaired_prompt_and_rejects_tampering(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "repair-hash"
    task = _write_owner_task(run_root, "owner-1")
    reports = run_root / "reports"
    candidate_path = "reports/agent-executions/unit.frozen-test.java"
    candidate = run_root / candidate_path
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("class FrozenTest {}", encoding="utf-8")
    repair_plan = {
        "schemaVersion": "implementation-repair-plan/v4",
        "entries": [
            {
                "failedTaskId": "unit-1",
                "owner": "backend",
                "ownerTaskIds": ["owner-1"],
                "recheckTaskIds": ["unit-1"],
                "frozenTestCandidate": {
                    "path": candidate_path,
                    "sourcePath": "application/src/test/java/example/FrozenTest.java",
                    "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                },
                "relatedPaths": ["application/src/main/java/example/Service.java"],
                "repairPaths": ["application/src/main/java/example/Service.java"],
                "strategy": "focused source correction",
                "evidence": "The frozen unit assertion failed on the current implementation.",
            }
        ],
    }
    (reports / "repair-plan.json").write_text(json.dumps(repair_plan), encoding="utf-8")

    apply_repair_directives(run_root)

    repaired_task = load_strict_task(
        run_root,
        "owner-1",
        allowed_task_types=cli.OWNER_TASK_TYPES,
    )
    assert repaired_task["repair_prompt_file"] == (
        "reports/implementation-tasks/owner-1.repair.md"
    )
    repair_prompt = run_root / str(repaired_task["repair_prompt_file"])
    repair_prompt.write_text("tampered repair directive", encoding="utf-8")
    with pytest.raises(ValueError, match="prompt hash is inconsistent"):
        load_strict_task(
            run_root,
            "owner-1",
            allowed_task_types=cli.OWNER_TASK_TYPES,
        )
