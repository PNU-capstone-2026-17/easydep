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
    implementation_job_id: str,
    files: dict[str, str],
) -> dict:
    return {
        "artifact_type": artifact_type,
        "metadata": {"implementation_job_id": implementation_job_id},
        "files": {
            path: {
                "content": content,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
            for path, content in files.items()
        },
    }


def test_job_snapshot_is_loaded_once_and_shared_as_one_application(
    monkeypatch,
) -> None:
    """각 산출물은 ID로 한 번만 읽고 같은 application 폴더에 합쳐진다."""
    snapshots = {
        101: _snapshot(
            TYPE_SOURCE_CODE,
            implementation_job_id="job-1",
            files={"src/Main.java": "class JobOne {}\n"},
        ),
        102: _snapshot(
            TYPE_DEPLOYMENT_FILE,
            implementation_job_id="job-1",
            files={"Dockerfile": "FROM eclipse-temurin:21-jre\n"},
        ),
    }
    calls: list[int] = []

    def load(_app_id, _artifact_type, _version_no=None, *, version_id=None):
        calls.append(version_id)
        return deepcopy(snapshots[version_id])

    monkeypatch.setattr(artifact_source, "load_file_snapshot", load)
    testing_input = capture_testing_input(
        "app-1",
        "job-1",
        artifact_version_ids={TYPE_SOURCE_CODE: 101, TYPE_DEPLOYMENT_FILE: 102},
    )

    with materialized_testing_application(testing_input) as run_root:
        application = run_root / "application"
        assert (application / "src/Main.java").read_text(encoding="utf-8") == (
            "class JobOne {}\n"
        )
        assert (application / "Dockerfile").is_file()

    assert calls == [101, 102]


def test_materialization_rejects_a_corrupted_file(monkeypatch) -> None:
    snapshots = {
        101: _snapshot(
            TYPE_SOURCE_CODE,
            implementation_job_id="job-1",
            files={"src/Main.java": "class Original {}\n"},
        ),
        102: _snapshot(
            TYPE_DEPLOYMENT_FILE,
            implementation_job_id="job-1",
            files={"Dockerfile": "FROM eclipse-temurin:21-jre\n"},
        ),
    }

    def load(_app_id, _artifact_type, _version_no=None, *, version_id=None):
        result = deepcopy(snapshots[version_id])
        if version_id == 101:
            result["files"]["src/Main.java"]["content"] = "class Corrupted {}\n"
        return result

    monkeypatch.setattr(artifact_source, "load_file_snapshot", load)
    testing_input = capture_testing_input(
        "app-1",
        "job-1",
        artifact_version_ids={TYPE_SOURCE_CODE: 101, TYPE_DEPLOYMENT_FILE: 102},
    )

    with (
        pytest.raises(ArtifactSnapshotMismatch, match="digest"),
        materialized_testing_application(testing_input),
    ):
        pass
