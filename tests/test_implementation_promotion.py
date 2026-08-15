from pathlib import Path
from types import SimpleNamespace

import pytest

from app.implementation.generation import orchestrator as orchestrator_module
from app.implementation.generation.orchestrator import PrototypeOrchestrator


def _orchestrator_with_output_root(tmp_path: Path) -> PrototypeOrchestrator:
    orchestrator = object.__new__(PrototypeOrchestrator)
    orchestrator.spec = type("Spec", (), {"output_root": tmp_path / "runs"})()
    return orchestrator


def _windows_lock_error() -> PermissionError:
    error = PermissionError("Docker bind mount has not released the directory")
    error.winerror = 5  # type: ignore[attr-defined]
    return error


def test_promote_retries_a_transient_windows_directory_lock(monkeypatch, tmp_path: Path) -> None:
    orchestrator = _orchestrator_with_output_root(tmp_path)
    staging = tmp_path / "runs" / ".run.staging"
    final = tmp_path / "runs" / "run"
    staging.mkdir(parents=True)
    (staging / "artifact.txt").write_text("ready", encoding="utf-8")

    real_replace = orchestrator_module.os.replace
    calls = 0
    delays: list[float] = []

    def replace_after_docker_release(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _windows_lock_error()
        real_replace(source, target)

    monkeypatch.setattr(orchestrator_module.os, "replace", replace_after_docker_release)
    monkeypatch.setattr(orchestrator_module.time, "sleep", delays.append)

    orchestrator._promote(staging, final)

    assert calls == 3
    assert delays == [0.25, 0.5]
    assert not staging.exists()
    assert (final / "artifact.txt").read_text(encoding="utf-8") == "ready"


def test_promote_does_not_retry_unrelated_filesystem_errors(monkeypatch, tmp_path: Path) -> None:
    orchestrator = _orchestrator_with_output_root(tmp_path)
    staging = tmp_path / "runs" / ".run.staging"
    final = tmp_path / "runs" / "run"
    staging.mkdir(parents=True)
    error = PermissionError("access denied by path policy")
    error.winerror = 3  # type: ignore[attr-defined]
    delays: list[float] = []

    def reject_replace(source: Path, target: Path) -> None:
        raise error

    monkeypatch.setattr(orchestrator_module.os, "replace", reject_replace)
    monkeypatch.setattr(orchestrator_module.time, "sleep", delays.append)

    with pytest.raises(PermissionError, match="path policy"):
        orchestrator._promote(staging, final)

    assert delays == []


def test_promote_preserves_an_existing_immutable_run(tmp_path: Path) -> None:
    orchestrator = _orchestrator_with_output_root(tmp_path)
    staging = tmp_path / "runs" / ".run.staging"
    final = tmp_path / "runs" / "run"
    staging.mkdir(parents=True)
    final.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="Refusing to overwrite immutable run"):
        orchestrator._promote(staging, final)


def test_failed_run_gets_an_immutable_retry_destination(tmp_path: Path) -> None:
    orchestrator = _orchestrator_with_output_root(tmp_path)
    orchestrator.manifest = type("Manifest", (), {"input_hash": "a" * 64})()
    failed = tmp_path / "runs" / "run_aaaaaaaaaaaa" / "reports"
    failed.mkdir(parents=True)
    (failed / "run-manifest.json").write_text(
        '{"input_hash": "' + "a" * 64 + '", "status": "FAILED"}',
        encoding="utf-8",
    )

    staging, final = orchestrator._select_run_paths()

    assert staging.name == ".run_aaaaaaaaaaaa_retry_1.staging"
    assert final.name == "run_aaaaaaaaaaaa_retry_1"


def test_successful_retry_is_reused_after_an_earlier_failure(tmp_path: Path) -> None:
    orchestrator = _orchestrator_with_output_root(tmp_path)
    orchestrator.manifest = type("Manifest", (), {"input_hash": "b" * 64})()
    for name, status in (
        ("run_bbbbbbbbbbbb", "FAILED"),
        ("run_bbbbbbbbbbbb_retry_1", "SUCCEEDED"),
    ):
        reports = tmp_path / "runs" / name / "reports"
        reports.mkdir(parents=True)
        (reports / "run-manifest.json").write_text(
            '{"input_hash": "' + "b" * 64 + f'", "status": "{status}"}}',
            encoding="utf-8",
        )

    _, final = orchestrator._select_run_paths()

    assert final.name == "run_bbbbbbbbbbbb_retry_1"


def test_run_reuses_existing_successful_checkpoint_without_regenerating(
    monkeypatch, tmp_path: Path
) -> None:
    orchestrator = _orchestrator_with_output_root(tmp_path)
    orchestrator.manifest = SimpleNamespace(status="", input_hash="")
    reports = tmp_path / "runs" / "run_cccccccccccc" / "reports"
    reports.mkdir(parents=True)
    (reports / "run-manifest.json").write_text(
        '{"input_hash": "' + "c" * 64 + '", "status": "SUCCEEDED"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(orchestrator, "_validate_inputs", lambda: None)
    monkeypatch.setattr(orchestrator, "_combined_input_hash", lambda: "c" * 64)
    monkeypatch.setattr(
        orchestrator,
        "_reset_target",
        lambda _target: pytest.fail("a reusable run must not recreate staging"),
    )

    result = orchestrator.run()

    assert result == reports.parent
