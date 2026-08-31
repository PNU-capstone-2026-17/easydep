from pathlib import Path
from types import SimpleNamespace

import pytest

from app.implementation.generation.orchestrator import PrototypeOrchestrator


def _orchestrator_with_output_root(tmp_path: Path) -> PrototypeOrchestrator:
    orchestrator = object.__new__(PrototypeOrchestrator)
    orchestrator.spec = type("Spec", (), {"output_root": tmp_path / "runs"})()
    return orchestrator


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
