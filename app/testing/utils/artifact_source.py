"""구현 작업의 파일 묶음을 임시 애플리케이션 폴더에 한 번 복원한다."""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

from app.db.models import (
    TYPE_DEPLOYMENT_FILE,
    TYPE_FRONTEND_SOURCE_CODE,
    TYPE_IAC_CODE,
    TYPE_SOURCE_CODE,
    TYPE_TEST_CODE,
)
from app.repositories.artifact_repository import AppNotFound, load_file_snapshot
from app.testing.schemas.testing_input import TestingInput

# frontend 산출물은 저장할 때 ``frontend/`` 접두사를 떼므로 복원할 때 다시 붙인다.
ARTIFACT_APPLICATION_PREFIXES: dict[str, str] = {
    TYPE_SOURCE_CODE: "",
    TYPE_FRONTEND_SOURCE_CODE: "frontend",
    TYPE_TEST_CODE: "",
    TYPE_DEPLOYMENT_FILE: "",
    TYPE_IAC_CODE: "",
}


class ArtifactSourceUnavailable(Exception):
    """구현 작업이 가리키는 파일 묶음을 읽을 수 없을 때 발생한다."""


class ArtifactSnapshotMismatch(Exception):
    """파일 묶음이 요청한 구현 작업의 결과가 아니거나 손상됐을 때 발생한다."""


def _safe_relative(file_path: str) -> Path:
    """임시 폴더 밖으로 나갈 수 있는 파일 경로를 거부한다."""
    candidate = PurePosixPath(file_path.replace("\\", "/"))
    if candidate.is_absolute() or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise ValueError(f"안전하지 않은 산출물 파일 경로입니다: {file_path}")
    return Path(*candidate.parts)


def capture_testing_input(
    app_id: str,
    implementation_job_id: str,
    *,
    artifact_version_ids: Mapping[str, int] | None,
) -> TestingInput:
    """구현 작업 기록의 파일 묶음 ID를 Testing 입력으로 고정한다."""
    if artifact_version_ids is None:
        raise ArtifactSourceUnavailable(
            "구현 작업에 저장된 산출물 ID 목록이 없습니다."
        )
    try:
        return TestingInput(
            app_id=app_id,
            implementation_job_id=implementation_job_id,
            artifact_version_ids=dict(artifact_version_ids),
        )
    except ValueError as error:
        raise ArtifactSourceUnavailable(str(error)) from error


def _load_snapshot(testing_input: TestingInput, artifact_type: str) -> Mapping[str, Any]:
    version_id = testing_input.artifact_version_ids[artifact_type]
    try:
        snapshot = load_file_snapshot(
            testing_input.app_id,
            artifact_type,
            version_id=version_id,
        )
    except AppNotFound as error:
        raise ArtifactSourceUnavailable(
            f"알 수 없는 앱 ID입니다: {testing_input.app_id}"
        ) from error
    if not snapshot or not snapshot.get("files"):
        raise ArtifactSourceUnavailable(
            f"구현 산출물을 찾을 수 없습니다: type={artifact_type}, id={version_id}"
        )
    actual_job_id = (snapshot.get("metadata") or {}).get("implementation_job_id")
    if actual_job_id != testing_input.implementation_job_id:
        raise ArtifactSnapshotMismatch(
            f"{artifact_type}은 요청한 구현 작업의 결과가 아닙니다: "
            f"expected={testing_input.implementation_job_id}, "
            f"actual={actual_job_id or 'missing'}"
        )
    return snapshot


@contextmanager
def materialized_testing_application(testing_input: TestingInput) -> Iterator[Path]:
    """모든 파일 묶음을 한 번 복원하고 임시 ``run_root``를 반환한다."""
    with tempfile.TemporaryDirectory(prefix="easydep-testing-run-") as temporary:
        run_root = Path(temporary)
        application = run_root / "application"
        application.mkdir(parents=True)
        occupied_paths: dict[str, str] = {}

        for artifact_type in testing_input.artifact_version_ids:
            snapshot = _load_snapshot(testing_input, artifact_type)
            prefix = ARTIFACT_APPLICATION_PREFIXES[artifact_type]
            destination = application / prefix if prefix else application
            for raw_path, raw_item in sorted(snapshot["files"].items()):
                relative = _safe_relative(str(raw_path))
                restored = (
                    (Path(prefix) / relative).as_posix()
                    if prefix
                    else relative.as_posix()
                )
                previous_type = occupied_paths.get(restored)
                if previous_type is not None:
                    raise ArtifactSnapshotMismatch(
                        "서로 다른 산출물이 같은 복원 경로를 사용합니다: "
                        f"path={restored}, first={previous_type}, second={artifact_type}"
                    )

                item = raw_item if isinstance(raw_item, Mapping) else {}
                content = str(item.get("content") or "")
                expected_digest = str(item.get("sha256") or "").casefold()
                actual_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if not expected_digest or expected_digest != actual_digest:
                    raise ArtifactSnapshotMismatch(
                        f"{artifact_type} 파일 digest가 다릅니다: path={raw_path}"
                    )

                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                occupied_paths[restored] = artifact_type

        yield run_root
