"""구현 수리 후보의 파일 버전 재사용과 폐기 규칙을 검증한다."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import BigInteger, create_engine, event
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.db import session as db_session
from app.db.models import (
    TYPE_DEPLOYMENT_FILE,
    TYPE_SOURCE_CODE,
    TYPE_TEST_CODE,
    App,
    ArtifactFile,
    ArtifactVersion,
    Base,
)
from app.implementation.application.jobs import ImplementationWorker
from app.implementation.config import ImplementationSettings
from app.repositories import artifact_repository
from app.testing.utils.artifact_source import (
    capture_testing_input,
    materialized_testing_application,
)


@compiles(BigInteger, "sqlite")
def _compile_big_integer_as_sqlite_integer(
    _type: BigInteger,
    _compiler: object,
    **_kwargs: object,
) -> str:
    """SQLite 테스트에서 BigInteger PK가 자동 증가하도록 표현한다."""

    return "INTEGER"


_TABLES = [App.__table__, ArtifactVersion.__table__, ArtifactFile.__table__]


@pytest.fixture
def artifact_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[None]:
    """외부 MySQL 없이 repository transaction을 그대로 실행한다."""

    engine = create_engine(f"sqlite:///{tmp_path / 'artifacts.db'}", future=True)

    @event.listens_for(engine, "connect")
    def _install_mysql_compatible_sqlite_helpers(
        connection: object,
        _record: object,
    ) -> None:
        """Production table의 collation과 CHECK 함수를 SQLite에서도 제공한다."""

        connection.create_collation(  # type: ignore[attr-defined]
            "utf8mb4_0900_as_cs", lambda left, right: (left > right) - (left < right)
        )
        connection.create_collation(  # type: ignore[attr-defined]
            "ascii_bin", lambda left, right: (left > right) - (left < right)
        )
        connection.create_function(  # type: ignore[attr-defined]
            "char_length", 1, len
        )
        connection.create_function(  # type: ignore[attr-defined]
            "regexp", 2, lambda pattern, value: re.search(pattern, value) is not None
        )

    Base.metadata.create_all(engine, tables=_TABLES)
    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(
        db_session,
        "_session_factory",
        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False),
    )
    with db_session.session_scope() as session:
        session.add(App(app_id="app-1"))
    try:
        yield
    finally:
        engine.dispose()


def _settings(root: Path) -> ImplementationSettings:
    root.mkdir(parents=True, exist_ok=True)
    python = root / "python.exe"
    python.write_text("test", encoding="utf-8")
    return ImplementationSettings(
        repository_root=root,
        work_root=root / ".easydep" / "implementation-runs",
        python_executable=python,
        max_workers=1,
        command_timeout_seconds=60,
    )


def test_identical_latest_snapshot_reuses_version_but_changed_content_does_not(
    artifact_database: None,
) -> None:
    """Job metadata만 다른 동일 파일은 버전 수를 늘리지 않는다."""

    first = artifact_repository.save_file_snapshot(
        "app-1",
        TYPE_SOURCE_CODE,
        {"src/App.java": "class App {}\n"},
        metadata={"implementation_job_id": "job-original"},
    )
    reused = artifact_repository.save_file_snapshot(
        "app-1",
        TYPE_SOURCE_CODE,
        {"src\\App.java": "class App {}\n"},
        metadata={"implementation_job_id": "job-repair"},
    )
    changed = artifact_repository.save_file_snapshot(
        "app-1",
        TYPE_SOURCE_CODE,
        {"src/App.java": "class App { int value; }\n"},
        metadata={"implementation_job_id": "job-repair"},
    )

    versions = artifact_repository.list_file_artifact_versions(
        "app-1", TYPE_SOURCE_CODE
    )
    assert reused == first
    assert changed != first
    assert [item["version_no"] for item in versions] == [1, 2]


def test_testing_accepts_job_record_that_reuses_an_older_snapshot(
    artifact_database: None,
) -> None:
    """Testing은 metadata Job ID가 아니라 고정된 버전 ID를 출처로 사용한다."""

    source_id = artifact_repository.save_file_snapshot(
        "app-1",
        TYPE_SOURCE_CODE,
        {"src/App.java": "class App {}\n"},
        metadata={"implementation_job_id": "job-original"},
    )
    deployment_id = artifact_repository.save_file_snapshot(
        "app-1",
        TYPE_DEPLOYMENT_FILE,
        {
            "Dockerfile": "FROM eclipse-temurin:21-jre\n",
            "deployment/scripts/deploy.sh": (
                "#!/usr/bin/env bash\nif true; then\n  echo ready\nfi\n"
            ),
        },
        metadata={"implementation_job_id": "job-original"},
    )
    testing_input = capture_testing_input(
        "app-1",
        "job-repair",
        artifact_version_ids={
            TYPE_SOURCE_CODE: source_id,
            TYPE_DEPLOYMENT_FILE: deployment_id,
        },
    )

    with materialized_testing_application(testing_input) as run_root:
        assert (run_root / "application/src/App.java").read_text(
            encoding="utf-8"
        ) == "class App {}\n"
        assert (
            run_root / "application/deployment/scripts/deploy.sh"
        ).read_bytes() == b"#!/usr/bin/env bash\nif true; then\n  echo ready\nfi\n"


def test_testing_input_keeps_current_trace_when_file_version_is_reused(
    artifact_database: None,
) -> None:
    """파일 version과 별개로 현재 구현 Job의 RTM을 고정한다."""

    source_id = artifact_repository.save_file_snapshot(
        "app-1",
        TYPE_SOURCE_CODE,
        {"src/App.java": "class App {}\n"},
        metadata={"implementation_job_id": "job-original"},
    )
    deployment_id = artifact_repository.save_file_snapshot(
        "app-1",
        TYPE_DEPLOYMENT_FILE,
        {"Dockerfile": "FROM eclipse-temurin:21-jre\n"},
    )
    trace = {
        "schemaVersion": "implementation-traceability/v1",
        "mappings": [{"target_file": "src/App.java", "sourceRefs": ["api:getApp"]}],
    }

    testing_input = capture_testing_input(
        "app-1",
        "job-repair",
        artifact_version_ids={
            TYPE_SOURCE_CODE: source_id,
            TYPE_DEPLOYMENT_FILE: deployment_id,
        },
        implementation_traceability=trace,
    )

    assert testing_input.implementation_traceability == trace


def test_discard_feedback_candidate_deletes_only_versions_owned_by_that_job(
    artifact_database: None,
    tmp_path: Path,
) -> None:
    """공유한 이전 버전은 남겨 두고 후보 Job이 만든 버전만 삭제한다."""

    shared_source_id = artifact_repository.save_file_snapshot(
        "app-1",
        TYPE_SOURCE_CODE,
        {"src/App.java": "class App {}\n"},
        metadata={"implementation_job_id": "job-original"},
    )
    reused_source_id = artifact_repository.save_file_snapshot(
        "app-1",
        TYPE_SOURCE_CODE,
        {"src/App.java": "class App {}\n"},
        metadata={"implementation_job_id": "job-repair"},
    )
    owned_test_id = artifact_repository.save_file_snapshot(
        "app-1",
        TYPE_TEST_CODE,
        {"src/AppTest.java": "class AppTest {}\n"},
        metadata={"implementation_job_id": "job-repair"},
    )
    worker = ImplementationWorker(_settings(tmp_path / "worker"))
    worker._write(
        {
            "job_id": "job-repair",
            "job_type": "FEEDBACK_REVISION",
            "app_id": "app-1",
            "status": "COMPLETED",
            "artifact_version_ids": {
                TYPE_SOURCE_CODE: reused_source_id,
                TYPE_TEST_CODE: owned_test_id,
            },
            "created_at": "now",
            "updated_at": "now",
        }
    )
    try:
        result = worker.discard_feedback_candidate(
            "job-repair",
            reason="The same blocker remained after verification.",
        )
    finally:
        worker.shutdown()

    assert shared_source_id == reused_source_id
    assert result["artifact_version_ids"] == {}
    assert result["artifact_status"] == "DISCARDED"
    assert result["discarded_artifact_types"] == [TYPE_TEST_CODE]
    assert (
        artifact_repository.load_file_snapshot(
            "app-1", TYPE_SOURCE_CODE, version_id=shared_source_id
        )
        is not None
    )
    assert (
        artifact_repository.load_file_snapshot(
            "app-1", TYPE_TEST_CODE, version_id=owned_test_id
        )
        is None
    )
