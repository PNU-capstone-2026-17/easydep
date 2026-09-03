"""기존 Workspace command를 이용한 Testing 재시작 복구 계약을 검증한다."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.models import TYPE_DEPLOYMENT_FILE, TYPE_SOURCE_CODE, App, Base, WorkspaceCommand
from app.testing import service as testing_service
from app.testing.schemas.testing_input import TestingInput as FrozenTestingInput
from app.workspace import repository

_TABLES = [App.__table__, WorkspaceCommand.__table__]


def _fixed_input() -> dict:
    return {
        "app_id": "app-1",
        "implementation_job_id": "implementation-1",
        "artifact_version_ids": {
            "SOURCE_CODE": 11,
            "DEPLOYMENT_FILE": 12,
        },
        "contract_artifacts": {},
    }


def _checkpoint() -> dict:
    return {
        "implementation_job_id": "implementation-1",
        "testing_input": _fixed_input(),
        "current_node": "verification",
        "result": {"preservedCandidatePlan": {"cases": ["health"]}},
        "repair_history": {
            "status": "ACTIVE",
            "attempts": [{"strategy_key": "initial_generation"}],
        },
        "previous_findings": ["testing.dynamicFunctional:HTTP 500"],
    }


def _install_sqlite(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'testing-checkpoint.db'}", future=True)
    Base.metadata.create_all(engine, tables=_TABLES)
    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(
        db_session,
        "_session_factory",
        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False),
    )
    with db_session.session_scope() as session:
        session.add(App(app_id="app-1"))
    return engine


def _create_testing_command(command_id: str = "testing-command-1") -> str:
    repository.create_command(
        command_id,
        "app-1",
        "start_testing",
        "testing",
        {"implementation_job_id": "implementation-1"},
    )
    return command_id


def test_testing_checkpoint_survives_restart_without_a_new_table(monkeypatch, tmp_path) -> None:
    engine = _install_sqlite(monkeypatch, tmp_path)
    try:
        command_id = _create_testing_command()
        payload = {
            "implementation_job_id": "implementation-1",
            "testing_checkpoint": _checkpoint(),
        }
        repository.update_command(
            command_id,
            status="RUNNING",
            started_at=repository.now(),
            payload=payload,
        )

        with db_session.session_scope() as session:
            assert session.get(WorkspaceCommand, command_id).payload == payload

        assert repository.interrupt_unfinished() == 1
        recoverable = repository.interrupted_testing_commands()
        assert [item["command_id"] for item in recoverable] == [command_id]
        assert recoverable[0]["payload"]["testing_checkpoint"] == _checkpoint()
    finally:
        engine.dispose()


def test_only_checkpointed_interrupted_testing_commands_are_resumable(monkeypatch, tmp_path) -> None:
    engine = _install_sqlite(monkeypatch, tmp_path)
    try:
        command_id = _create_testing_command()
        repository.update_command(command_id, status="RUNNING")
        repository.interrupt_unfinished()
        assert repository.interrupted_testing_commands() == []

        repository.update_command(command_id, status="FAILED", error="Docker unavailable")
        assert repository.interrupted_testing_commands() == []
    finally:
        engine.dispose()


def test_testing_service_checkpoints_latest_repair_ledger(monkeypatch) -> None:
    fixed_input = FrozenTestingInput(
        app_id="app-1",
        implementation_job_id="implementation-1",
        artifact_version_ids={TYPE_SOURCE_CODE: 11, TYPE_DEPLOYMENT_FILE: 12},
    )
    checkpoints: list[dict] = []
    monkeypatch.setattr(
        testing_service.implementation_worker,
        "get_testing_input",
        lambda _job_id: {
            "app_id": "app-1",
            "job_id": "implementation-1",
            "status": "COMPLETED",
            "artifact_version_ids": fixed_input.artifact_version_ids,
            "contract_artifacts": {},
        },
    )
    monkeypatch.setattr(
        testing_service,
        "capture_testing_input",
        lambda *_args, **_kwargs: fixed_input,
    )

    def run(_run_id, _testing_input, **kwargs):
        kwargs["progress"](
            {
                "current_node": "verification_complete",
                "result": {"passed": False},
                "repair_history": {
                    "status": "ACTIVE",
                    "attempts": [{"strategy_key": "second_candidate"}],
                },
                "previous_findings": ["testing.dynamicFunctional:HTTP 409"],
            }
        )
        return {"passed": False}, {"status": "ACTIVE", "attempts": []}

    monkeypatch.setattr(testing_service, "_run_test", run)
    testing_service.run_testing(
        "app-1",
        "implementation-1",
        run_id="testing-command-1",
        progress=checkpoints.append,
    )

    assert checkpoints[-1]["current_node"] == "verification_complete"
    assert checkpoints[-1]["repair_history"]["attempts"][0]["strategy_key"] == (
        "second_candidate"
    )
    assert checkpoints[-1]["previous_findings"] == [
        "testing.dynamicFunctional:HTTP 409"
    ]
