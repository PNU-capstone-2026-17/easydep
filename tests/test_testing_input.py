from __future__ import annotations

import hashlib
from copy import deepcopy

import pytest

from app.db.models import TYPE_DEPLOYMENT_FILE, TYPE_SOURCE_CODE
from app.testing.utils import artifact_source
from app.testing.utils.artifact_source import (
    ArtifactSnapshotMismatch,
    capture_testing_input,
    materialized_testing_application,
)


def _snapshot(
    artifact_type: str,
    *,
    version_id: int,
    version_no: int,
    implementation_job_id: str,
    files: dict[str, str],
) -> dict:
    stored_files = {
        path: {
            "content": content,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        for path, content in files.items()
    }
    digest_rows = "".join(
        f"{path}\0{item['sha256']}\n" for path, item in sorted(stored_files.items())
    )
    return {
        "artifact_type": artifact_type,
        "version_id": version_id,
        "version_no": version_no,
        "snapshot_digest": hashlib.sha256(digest_rows.encode("utf-8")).hexdigest(),
        "metadata": {"implementation_job_id": implementation_job_id},
        "files": stored_files,
        "created_at": f"2026-08-29T00:00:0{version_no}+00:00",
    }


def test_testing_input_keeps_using_job_one_after_job_two_becomes_current(
    monkeypatch, tmp_path
) -> None:
    """Testing job을 만든 뒤 새 구현이 저장돼도 첫 구현 파일만 복원한다."""
    job_one = {
        (TYPE_SOURCE_CODE, 101): _snapshot(
            TYPE_SOURCE_CODE,
            version_id=101,
            version_no=1,
            implementation_job_id="job-1",
            files={"src/Main.java": "class JobOne {}\n"},
        ),
        (TYPE_DEPLOYMENT_FILE, 102): _snapshot(
            TYPE_DEPLOYMENT_FILE,
            version_id=102,
            version_no=1,
            implementation_job_id="job-1",
            files={"Dockerfile": "FROM eclipse-temurin:21-jre\n"},
        ),
    }
    current = {
        TYPE_SOURCE_CODE: _snapshot(
            TYPE_SOURCE_CODE,
            version_id=201,
            version_no=2,
            implementation_job_id="job-2",
            files={"src/Main.java": "class JobTwo {}\n"},
        ),
        TYPE_DEPLOYMENT_FILE: _snapshot(
            TYPE_DEPLOYMENT_FILE,
            version_id=202,
            version_no=2,
            implementation_job_id="job-2",
            files={"Dockerfile": "FROM eclipse-temurin:22-jre\n"},
        ),
    }
    calls: list[tuple[str, int | None, int | None]] = []

    def load(_app_id, artifact_type, version_no=None, *, version_id=None):
        calls.append((artifact_type, version_no, version_id))
        selected = job_one.get((artifact_type, version_id)) if version_id else None
        return deepcopy(selected or current.get(artifact_type))

    monkeypatch.setattr(artifact_source, "load_file_snapshot", load)

    testing_input = capture_testing_input(
        "app-1",
        "job-1",
        tmp_path / "old-run-root",
        artifact_version_ids={TYPE_SOURCE_CODE: 101, TYPE_DEPLOYMENT_FILE: 102},
        completed_at="2026-08-29T00:00:10+00:00",
    )
    with materialized_testing_application(testing_input) as restored_run_root:
        restored = restored_run_root / "application"
        assert (restored / "src/Main.java").read_text(encoding="utf-8") == (
            "class JobOne {}\n"
        )
        assert (restored / "Dockerfile").read_text(encoding="utf-8") == (
            "FROM eclipse-temurin:21-jre\n"
        )

    assert calls
    assert all(version_id in {101, 102} for _, _, version_id in calls)
    assert all(version_no is None for _, version_no, _ in calls)


def test_materialization_rejects_file_content_that_does_not_match_its_sha256(
    monkeypatch, tmp_path
) -> None:
    source = _snapshot(
        TYPE_SOURCE_CODE,
        version_id=101,
        version_no=1,
        implementation_job_id="job-1",
        files={"src/Main.java": "class Original {}\n"},
    )
    deployment = _snapshot(
        TYPE_DEPLOYMENT_FILE,
        version_id=102,
        version_no=1,
        implementation_job_id="job-1",
        files={"Dockerfile": "FROM eclipse-temurin:21-jre\n"},
    )
    snapshots = {101: source, 102: deployment}

    def load(_app_id, _artifact_type, _version_no=None, *, version_id=None):
        result = deepcopy(snapshots[version_id])
        if version_id == 101:
            # DB 파일 내용만 바뀌고 저장된 SHA-256은 예전 값인 손상 상태를 재현한다.
            result["files"]["src/Main.java"]["content"] = "class Corrupted {}\n"
        return result

    monkeypatch.setattr(artifact_source, "load_file_snapshot", load)
    testing_input = capture_testing_input(
        "app-1",
        "job-1",
        tmp_path,
        artifact_version_ids={TYPE_SOURCE_CODE: 101, TYPE_DEPLOYMENT_FILE: 102},
    )

    with (
        pytest.raises(ArtifactSnapshotMismatch, match="파일 digest"),
        materialized_testing_application(testing_input),
    ):
        pass
