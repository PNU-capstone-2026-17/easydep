import hashlib
import json
from pathlib import Path

import pytest

import app.implementation.diagnostics.owner_replay as owner_replay
from app.implementation.diagnostics.owner_replay import (
    OwnerReplayError,
    replay_owner_task,
    replay_owner_tasks_sequentially,
)


def _task(*, task_type: str = "backend-implementation", prompt: str = "new") -> dict[str, object]:
    return {
        "task_id": "owner-1",
        "owner": "backend",
        "task_type": task_type,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "prompt_file": "reports/implementation-tasks/owner-1.prompt.md",
        "context_file": "reports/implementation-tasks/owner-1.context.json",
    }


def _write_task(run_root: Path, task: dict[str, object], prompt: str) -> None:
    tasks = run_root / "reports" / "implementation-tasks"
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / "owner-1.task.json").write_text(json.dumps(task), encoding="utf-8")
    (tasks / "owner-1.prompt.md").write_text(prompt, encoding="utf-8")
    (tasks / "owner-1.context.json").write_text('{"scope":"owner"}', encoding="utf-8")
    (run_root / "reports" / "run-manifest.json").write_text(
        json.dumps({"app_id": "app", "implementation_tasks": [task]}), encoding="utf-8"
    )


class FakeClient:
    def __init__(
        self,
        work_root: Path,
        *,
        repository_root: Path | None = None,
        fail: bool = False,
        interrupt: bool = False,
        escape_run: bool = False,
    ):
        self.calls: list[object] = []
        self.cloned_jobs: list[dict[str, object]] = []
        self.fail = fail
        self.interrupt = interrupt
        self.escape_run = escape_run
        self.settings = type(
            "Settings",
            (),
            {
                "work_root": work_root,
                "repository_root": repository_root or work_root.parent,
            },
        )()

    def generate(self, job_path: Path) -> Path:
        self.calls.append("generate")
        if self.escape_run:
            return job_path.parent.parent / "escaped-run"
        run = job_path.parent / "generated" / "runs" / "fresh"
        run.mkdir(parents=True)
        return run

    def run_owner(self, run_root: Path, job_path: Path, task_id: str) -> dict[str, object]:
        self.cloned_jobs.append(json.loads(job_path.read_text(encoding="utf-8")))
        self.calls.append(("owner", run_root, job_path, task_id))
        if self.fail:
            execution_dir = run_root / "reports" / "agent-executions"
            execution_dir.mkdir(parents=True)
            journal = execution_dir / f"{task_id}.attempt-001.events.jsonl"
            journal.write_text('{"access_token":"journal-secret","prompt_tokens":7}\n', encoding="utf-8")
            conversation = run_root / "reports" / "openhands-conversations" / "conversation-1"
            conversation.mkdir(parents=True)
            (conversation / "base_state.json").write_text('{"state":"saved"}', encoding="utf-8")
            result = {
                "taskId": task_id,
                "eventJournal": journal.relative_to(run_root).as_posix(),
                "conversationCheckpoint": conversation.relative_to(run_root).as_posix(),
                "source_artifacts": [str(run_root / "application" / "temporary-source")],
                "access_token": "result-secret",
                "prompt_tokens": 17,
                "completion_tokens": 23,
                "maxOutputTokens": 29,
            }
            (execution_dir / f"{task_id}.attempt-001.result.json").write_text(
                json.dumps(result), encoding="utf-8"
            )
            (execution_dir / f"{task_id}.result.json").write_text(json.dumps(result), encoding="utf-8")
            raise RuntimeError("owner failed")
        if self.interrupt:
            raise KeyboardInterrupt
        return {"status": "SUCCEEDED", "token": "secret"}


def _saved(tmp_path: Path) -> tuple[Path, Path]:
    saved = tmp_path / "saved"
    old_run = saved / "run"
    saved.mkdir()
    (saved / "design.json").write_text('{"design":"old"}', encoding="utf-8")
    job = saved / "job.json"
    job.write_text(
        json.dumps(
            {
                "appId": "app",
                "workspaceRoot": str(saved),
                "inputs": {"design": "design.json"},
                "requiredInputs": ["design"],
            }
        ),
        encoding="utf-8",
    )
    _write_task(old_run, _task(prompt="old"), "old")
    return job, old_run


def _materializer(
    calls: list[tuple[Path, object]],
    *,
    task_type: str = "backend-implementation",
    duplicate: bool = False,
):
    def materialize(run_root: Path, spec: object) -> list[dict[str, object]]:
        calls.append((run_root, spec))
        task = _task(task_type=task_type)
        _write_task(run_root, task, "new")
        return [task, task] if duplicate else [task]

    return materialize


def test_replay_executes_one_owner_and_removes_workspace(tmp_path, monkeypatch):
    job, old_run = _saved(tmp_path)
    old_checkpoint = (old_run / "reports" / "implementation-tasks" / "owner-1.task.json").read_bytes()
    materializer_calls: list[tuple[Path, object]] = []
    monkeypatch.setattr(owner_replay, "materialize_owner_tasks", _materializer(materializer_calls))
    work_root = tmp_path / "diagnostics"
    client = FakeClient(work_root)

    result = replay_owner_task(
        client, job_path=job, old_run_root=old_run, task_id="owner-1", output_root=tmp_path / "results"
    )

    payload = json.loads(result.read_text(encoding="utf-8"))
    assert client.calls[0] == "generate"
    assert client.calls[1][0] == "owner"
    cloned_job = client.calls[1][2]
    cloned = client.cloned_jobs[0]
    job_root = cloned_job.parent
    repository_root = tmp_path.resolve()
    assert cloned["workspaceRoot"] == str(repository_root)
    assert job_root.is_relative_to(work_root.resolve())
    assert job_root.is_relative_to(repository_root)
    for value in [*cloned["inputs"].values(), cloned["outputRoot"], cloned["progressPath"]]:
        assert not Path(value).is_absolute()
        assert (repository_root / value).resolve().is_relative_to(job_root)
    assert len(materializer_calls) == 1
    assert payload["oldPromptSha256"] == hashlib.sha256(b"old").hexdigest()
    assert payload["newPromptSha256"] == hashlib.sha256(b"new").hexdigest()
    assert payload["execution"]["token"] == "[REDACTED]"  # noqa: S105
    evidence_root = (tmp_path / "results") / payload["evidence"]["root"]
    assert (evidence_root / "task" / "task.json").is_file()
    assert (evidence_root / "task" / "prompt.md").is_file()
    assert (evidence_root / "task" / "context.json").is_file()
    assert payload["task"]["prompt_file"].startswith(payload["evidence"]["root"])
    assert (old_run / "reports" / "implementation-tasks" / "owner-1.task.json").read_bytes() == old_checkpoint
    assert not list(work_root.glob("owner-replay-*"))
    assert result.is_file()


def test_replay_can_map_a_saved_owner_to_one_fresh_operation_task(
    tmp_path, monkeypatch
):
    job, old_run = _saved(tmp_path)
    fresh_id = "owner-1-operation-place-order"

    def materialize(run_root: Path, _spec: object) -> list[dict[str, object]]:
        task = {
            **_task(),
            "task_id": fresh_id,
            "prompt_file": f"reports/implementation-tasks/{fresh_id}.prompt.md",
            "context_file": f"reports/implementation-tasks/{fresh_id}.context.json",
        }
        tasks = run_root / "reports" / "implementation-tasks"
        tasks.mkdir(parents=True, exist_ok=True)
        (tasks / f"{fresh_id}.task.json").write_text(
            json.dumps(task), encoding="utf-8"
        )
        (tasks / f"{fresh_id}.prompt.md").write_text("new", encoding="utf-8")
        (tasks / f"{fresh_id}.context.json").write_text(
            '{"scope":"operation"}', encoding="utf-8"
        )
        return [task]

    monkeypatch.setattr(owner_replay, "materialize_owner_tasks", materialize)
    client = FakeClient(tmp_path / "diagnostics")

    result = replay_owner_task(
        client,
        job_path=job,
        old_run_root=old_run,
        task_id="owner-1",
        fresh_task_id=fresh_id,
        output_root=tmp_path / "results",
    )

    payload = json.loads(result.read_text(encoding="utf-8"))
    assert client.calls[1][3] == fresh_id
    assert payload["baselineTaskId"] == "owner-1"
    assert payload["taskId"] == fresh_id
    assert payload["task"]["task_id"] == fresh_id


def test_replay_rejects_old_app_mismatch(tmp_path):
    job, old_run = _saved(tmp_path)
    manifest_path = old_run / "reports" / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["app_id"] = "other-app"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(OwnerReplayError, match="identity do not match"):
        replay_owner_task(
            FakeClient(tmp_path / "diagnostics"),
            job_path=job,
            old_run_root=old_run,
            task_id="owner-1",
            output_root=tmp_path / "results",
        )


def test_replay_rejects_design_input_escape(tmp_path):
    job, old_run = _saved(tmp_path)
    (tmp_path / "outside.json").write_text("outside", encoding="utf-8")
    saved_job = json.loads(job.read_text(encoding="utf-8"))
    saved_job["inputs"] = {"design": "../outside.json"}
    job.write_text(json.dumps(saved_job), encoding="utf-8")

    with pytest.raises(OwnerReplayError, match="escapes workspaceRoot"):
        replay_owner_task(
            FakeClient(tmp_path / "diagnostics"),
            job_path=job,
            old_run_root=old_run,
            task_id="owner-1",
            output_root=tmp_path / "results",
        )


def test_replay_rejects_generated_run_escape(tmp_path, monkeypatch):
    job, old_run = _saved(tmp_path)
    materializer_calls: list[tuple[Path, object]] = []
    monkeypatch.setattr(owner_replay, "materialize_owner_tasks", _materializer(materializer_calls))
    client = FakeClient(tmp_path / "diagnostics", escape_run=True)

    with pytest.raises(OwnerReplayError, match="escaped work root"):
        replay_owner_task(client, job_path=job, old_run_root=old_run, task_id="owner-1", output_root=tmp_path / "results")

    assert materializer_calls == []
    assert not [call for call in client.calls if isinstance(call, tuple) and call[0] == "owner"]
    assert not list((tmp_path / "diagnostics").glob("owner-replay-*"))


def test_replay_rejects_duplicate_fresh_task_without_execution(tmp_path, monkeypatch):
    job, old_run = _saved(tmp_path)
    materializer_calls: list[tuple[Path, object]] = []
    monkeypatch.setattr(owner_replay, "materialize_owner_tasks", _materializer(materializer_calls, duplicate=True))
    client = FakeClient(tmp_path / "diagnostics")

    with pytest.raises(OwnerReplayError, match="exactly one task"):
        replay_owner_task(client, job_path=job, old_run_root=old_run, task_id="owner-1", output_root=tmp_path / "results")

    assert len(materializer_calls) == 1
    assert not [call for call in client.calls if isinstance(call, tuple) and call[0] == "owner"]


def test_replay_rejects_non_owner_fresh_task_without_execution(tmp_path, monkeypatch):
    job, old_run = _saved(tmp_path)
    materializer_calls: list[tuple[Path, object]] = []
    monkeypatch.setattr(owner_replay, "materialize_owner_tasks", _materializer(materializer_calls, task_type="not-owner"))
    client = FakeClient(tmp_path / "diagnostics")

    with pytest.raises(OwnerReplayError, match="not an owner task"):
        replay_owner_task(client, job_path=job, old_run_root=old_run, task_id="owner-1", output_root=tmp_path / "results")

    assert len(materializer_calls) == 1
    assert not [call for call in client.calls if isinstance(call, tuple) and call[0] == "owner"]


def test_replay_writes_failure_result_and_cleans_up_when_owner_fails(tmp_path, monkeypatch):
    job, old_run = _saved(tmp_path)
    materializer_calls: list[tuple[Path, object]] = []
    monkeypatch.setattr(owner_replay, "materialize_owner_tasks", _materializer(materializer_calls))
    work_root = tmp_path / "diagnostics"
    output = tmp_path / "results"

    client = FakeClient(work_root, fail=True)
    with pytest.raises(RuntimeError, match="owner failed"):
        replay_owner_task(
            client,
            job_path=job,
            old_run_root=old_run,
            task_id="owner-1",
            output_root=output,
        )

    result = next(output.glob("owner-replay-result-*.json"))
    payload = json.loads(result.read_text(encoding="utf-8"))
    evidence_root = output / payload["evidence"]["root"]
    copied_result = json.loads(
        (evidence_root / "agent-executions" / "owner-1.result.json").read_text(encoding="utf-8")
    )
    run_root = client.calls[1][1]
    assert payload["status"] == "FAILED"
    assert (evidence_root / "task" / "task.json").is_file()
    assert (evidence_root / "task" / "prompt.md").is_file()
    assert (evidence_root / "task" / "context.json").is_file()
    assert (evidence_root / "agent-executions" / "owner-1.attempt-001.events.jsonl").is_file()
    assert not (evidence_root / "openhands-conversations").exists()
    assert copied_result["eventJournal"].startswith(payload["evidence"]["root"])
    assert "conversationCheckpoint" not in copied_result
    assert "source_artifacts" not in copied_result
    assert copied_result["access_token"] == "[REDACTED]"  # noqa: S105
    assert copied_result["prompt_tokens"] == 17
    assert copied_result["completion_tokens"] == 23
    assert copied_result["maxOutputTokens"] == 29
    assert str(run_root) not in json.dumps(payload)
    assert str(run_root) not in json.dumps(copied_result)
    assert len(materializer_calls) == 1
    assert not list(work_root.glob("owner-replay-*"))


def test_replay_records_cleanup_error_without_masking_owner_failure(tmp_path, monkeypatch):
    job, old_run = _saved(tmp_path)
    monkeypatch.setattr(owner_replay, "materialize_owner_tasks", _materializer([]))
    monkeypatch.setattr(owner_replay.shutil, "rmtree", lambda _path: (_ for _ in ()).throw(OSError("cleanup failed")))
    output = tmp_path / "results"

    with pytest.raises(RuntimeError, match="owner failed"):
        replay_owner_task(
            FakeClient(tmp_path / "diagnostics", fail=True),
            job_path=job,
            old_run_root=old_run,
            task_id="owner-1",
            output_root=output,
        )

    payload = json.loads(next(output.glob("owner-replay-result-*.json")).read_text(encoding="utf-8"))
    assert payload["cleanupError"] == "cleanup failed"


def test_replay_records_keyboard_interrupt_before_reraising(tmp_path, monkeypatch):
    job, old_run = _saved(tmp_path)
    monkeypatch.setattr(owner_replay, "materialize_owner_tasks", _materializer([]))
    output = tmp_path / "results"

    with pytest.raises(KeyboardInterrupt):
        replay_owner_task(
            FakeClient(tmp_path / "diagnostics", interrupt=True),
            job_path=job,
            old_run_root=old_run,
            task_id="owner-1",
            output_root=output,
        )

    payload = json.loads(next(output.glob("owner-replay-result-*.json")).read_text(encoding="utf-8"))
    assert payload["status"] == "INTERRUPTED"


def _operation_task(task_id: str, *, depends_on: list[str] | None = None) -> dict[str, object]:
    source = "application/src/main/java/com/example/CourseOfferingControlService.java"
    return {
        **_task(),
        "task_id": task_id,
        "prompt_file": f"reports/implementation-tasks/{task_id}.prompt.md",
        "context_file": f"reports/implementation-tasks/{task_id}.context.json",
        "depends_on": depends_on or [],
        "allowed_write_paths": [source],
        "required_output_paths": [source],
    }


def _operation_materializer(tasks: list[dict[str, object]]):
    def materialize(run_root: Path, _spec: object) -> list[dict[str, object]]:
        task_root = run_root / "reports" / "implementation-tasks"
        task_root.mkdir(parents=True, exist_ok=True)
        source = run_root / "application" / "src" / "main" / "java" / "com" / "example" / "CourseOfferingControlService.java"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("// marker-details\n// marker-published\n", encoding="utf-8")
        for task in tasks:
            task_id = str(task["task_id"])
            (task_root / f"{task_id}.task.json").write_text(json.dumps(task), encoding="utf-8")
            (task_root / f"{task_id}.prompt.md").write_text("new", encoding="utf-8")
            (task_root / f"{task_id}.context.json").write_text("{}", encoding="utf-8")
        (run_root / "reports" / "run-manifest.json").write_text(
            json.dumps({"app_id": "app", "implementation_tasks": tasks}), encoding="utf-8"
        )
        return tasks

    return materialize


def test_sequence_replay_reuses_one_run_in_requested_dependency_order(tmp_path, monkeypatch):
    job, old_run = _saved(tmp_path)
    details = "owner-details"
    published = "owner-published"
    tasks = [_operation_task(details), _operation_task(published, depends_on=[details])]
    monkeypatch.setattr(owner_replay, "materialize_owner_tasks", _operation_materializer(tasks))
    class MutatingClient(FakeClient):
        def run_owner(self, run_root, job_path, task_id):
            result = super().run_owner(run_root, job_path, task_id)
            source = run_root / "application" / "src" / "main" / "java" / "com" / "example" / "CourseOfferingControlService.java"
            source.write_text(source.read_text(encoding="utf-8") + f"// implemented {task_id}\n", encoding="utf-8")
            return result

    client = MutatingClient(tmp_path / "diagnostics")

    result = replay_owner_tasks_sequentially(
        client, job_path=job, old_run_root=old_run, task_ids=[details, published], output_root=tmp_path / "results"
    )

    payload = json.loads(result.read_text(encoding="utf-8"))
    owner_calls = [call for call in client.calls if isinstance(call, tuple) and call[0] == "owner"]
    assert [call[3] for call in owner_calls] == [details, published]
    assert owner_calls[0][1] == owner_calls[1][1]
    assert client.calls.count("generate") == 1
    assert payload["status"] == "SUCCEEDED"
    assert [step["taskId"] for step in payload["steps"]] == [details, published]
    assert all(step["writableSourceHashes"] for step in payload["steps"])
    snapshots = [step["sourceSnapshots"] for step in payload["steps"]]
    assert all(snapshots)
    assert snapshots[0] != snapshots[1]
    evidence = (tmp_path / "results") / payload["evidence"]["root"]
    assert (evidence / "tasks" / "01" / "task" / "task.json").is_file()
    assert (evidence / "tasks" / "02" / "task" / "task.json").is_file()
    first_source = (tmp_path / "results") / snapshots[0][0]
    second_source = (tmp_path / "results") / snapshots[1][0]
    assert first_source.is_file() and second_source.is_file()
    assert first_source.read_text(encoding="utf-8") != second_source.read_text(encoding="utf-8")
    assert not list((tmp_path / "diagnostics").glob("owner-sequence-replay-*"))


def test_sequence_replay_failure_preserves_completed_step_and_cleans(tmp_path, monkeypatch):
    job, old_run = _saved(tmp_path)
    first = "owner-first"
    second = "owner-second"
    monkeypatch.setattr(
        owner_replay,
        "materialize_owner_tasks",
        _operation_materializer([_operation_task(first), _operation_task(second, depends_on=[first])]),
    )

    class SecondFailureClient(FakeClient):
        def run_owner(self, run_root, job_path, task_id):
            if task_id == second:
                self.calls.append(("owner", run_root, job_path, task_id))
                raise RuntimeError("second owner failed")
            result = super().run_owner(run_root, job_path, task_id)
            source = run_root / "application" / "src" / "main" / "java" / "com" / "example" / "CourseOfferingControlService.java"
            source.write_text(source.read_text(encoding="utf-8") + "// first complete\n", encoding="utf-8")
            return result

    output = tmp_path / "results"
    with pytest.raises(RuntimeError, match="second owner failed"):
        replay_owner_tasks_sequentially(
            SecondFailureClient(tmp_path / "diagnostics"),
            job_path=job,
            old_run_root=old_run,
            task_ids=[first, second],
            output_root=output,
        )
    payload = json.loads(next(output.glob("owner-sequence-replay-result-*.json")).read_text(encoding="utf-8"))
    assert payload["status"] == "FAILED"
    assert [step["taskId"] for step in payload["steps"]] == [first]
    assert (output / payload["steps"][0]["sourceSnapshots"][0]).is_file()
    assert payload["evidence"]["tasks"][first]["task"]
    assert not list((tmp_path / "diagnostics").glob("owner-sequence-replay-*"))


@pytest.mark.parametrize(
    ("task_ids", "message"),
    [(["missing"], "missing requested"), (["owner-b", "owner-a"], "out of dependency order")],
)
def test_sequence_replay_rejects_missing_or_invalid_order_without_owner_execution(tmp_path, monkeypatch, task_ids, message):
    job, old_run = _saved(tmp_path)
    first = "owner-a"
    second = "owner-b"
    monkeypatch.setattr(
        owner_replay,
        "materialize_owner_tasks",
        _operation_materializer([_operation_task(first), _operation_task(second, depends_on=[first])]),
    )
    client = FakeClient(tmp_path / "diagnostics")

    with pytest.raises(OwnerReplayError, match=message):
        replay_owner_tasks_sequentially(
            client, job_path=job, old_run_root=old_run, task_ids=task_ids, output_root=tmp_path / "results"
        )
    assert not [call for call in client.calls if isinstance(call, tuple) and call[0] == "owner"]
    assert not list((tmp_path / "diagnostics").glob("owner-sequence-replay-*"))


def test_sequence_replay_rejects_non_owner_task_without_execution(tmp_path, monkeypatch):
    job, old_run = _saved(tmp_path)
    invalid = {**_operation_task("not-owner"), "task_type": "not-owner"}
    monkeypatch.setattr(owner_replay, "materialize_owner_tasks", _operation_materializer([invalid]))
    client = FakeClient(tmp_path / "diagnostics")

    with pytest.raises(OwnerReplayError, match="not an owner task"):
        replay_owner_tasks_sequentially(
            client, job_path=job, old_run_root=old_run, task_ids=["not-owner"], output_root=tmp_path / "results"
        )
    assert not [call for call in client.calls if isinstance(call, tuple) and call[0] == "owner"]
