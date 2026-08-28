"""테스트가 사용할 구현 파일 snapshot을 고정하고 임시 폴더에 복원한다.

구현 agent는 생성한 파일 묶음을 DB에 버전별로 저장한다. Trivy와 Gradle 같은 검사 도구는
DB 행을 직접 읽을 수 없으므로 파일을 임시 폴더에 다시 써야 한다. 이때 최신 버전을 매번
조회하면 긴 테스트 도중 다른 구현 결과가 섞일 수 있다. 따라서 새 Testing job은 시작할 때
snapshot 참조를 :class:`TestingInput`에 기록하고 이후에는 그 버전만 복원한다.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
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
from app.testing.schemas.testing_input import (
    REQUIRED_TESTING_ARTIFACT_TYPES,
    TESTING_ARTIFACT_TYPES,
    ArtifactSnapshotRef,
    TestingInput,
)

# 저장할 때 ``frontend/`` 접두사를 제거하므로 복원 시에만 다시 붙인다. 나머지 파일은
# 구현 agent가 사용한 ``<run_root>/application`` 기준 상대 경로를 그대로 보존한다.
ARTIFACT_APPLICATION_PREFIXES: dict[str, str] = {
    TYPE_SOURCE_CODE: "",
    TYPE_FRONTEND_SOURCE_CODE: "frontend",
    TYPE_TEST_CODE: "",
    TYPE_DEPLOYMENT_FILE: "",
    TYPE_IAC_CODE: "",
}


class ArtifactSourceUnavailable(Exception):
    """요청한 앱이나 파일 snapshot을 DB에서 찾을 수 없을 때 발생한다."""


class ArtifactSnapshotMismatch(Exception):
    """찾은 snapshot이 테스트 시작 시 고정한 구현 결과와 다를 때 발생한다."""


def _safe_relative(file_path: str) -> Path:
    """임시 복원 폴더 밖으로 나가는 파일 경로를 거부한다.

    저장소도 파일 경로를 정리하지만, 여기서는 DB 문자열을 실제 파일로 쓰기 때문에 다시
    확인한다. 절대 경로나 ``..``를 허용하면 잘못된 데이터가 다른 프로젝트 파일을 덮어쓸
    수 있다.
    """
    candidate = PurePosixPath(file_path.replace("\\", "/"))
    if candidate.is_absolute() or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise ValueError(f"안전하지 않은 산출물 파일 경로입니다: {file_path}")
    return Path(*candidate.parts)


def _snapshot_digest(snapshot: Mapping[str, Any]) -> str:
    """저장소와 같은 규칙으로 파일 묶음의 SHA-256 digest를 계산한다."""
    stored = snapshot.get("snapshot_digest")
    if stored:
        return str(stored).casefold()

    files = snapshot.get("files") or {}
    digest_rows: list[str] = []
    for path, raw_item in sorted(files.items()):
        item = raw_item if isinstance(raw_item, Mapping) else {}
        sha256 = str(item.get("sha256") or "")
        if not sha256:
            # 오래된 mock이나 직접 만든 입력에 SHA가 없을 때도 같은 계산을 할 수 있게
            # content로 보완한다. 실제 저장소 응답에는 항상 sha256이 들어 있다.
            content = str(item.get("content") or "")
            sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        digest_rows.append(f"{path}\0{sha256}\n")
    return hashlib.sha256("".join(digest_rows).encode("utf-8")).hexdigest()


def _reference_from_snapshot(
    artifact_type: str,
    snapshot: Mapping[str, Any],
) -> ArtifactSnapshotRef:
    """저장소 응답을 검사 단계가 공유하는 작은 참조 모델로 바꾼다."""
    return ArtifactSnapshotRef(
        artifact_type=artifact_type,
        version_id=snapshot.get("version_id"),
        version_no=snapshot["version_no"],
        digest=_snapshot_digest(snapshot),
        created_at=snapshot["created_at"],
        file_count=len(snapshot.get("files") or {}),
    )


def _load_selected_snapshot(
    app_id: str,
    artifact_type: str,
    *,
    version_no: int | None = None,
    version_id: int | None = None,
) -> dict[str, Any] | None:
    """선택자가 없을 때만 기존의 두 인자 repository 호출을 유지한다.

    기존 테스트와 외부 adapter는 두 인자 함수를 mock할 수 있다. 명시적 버전이 없는 호출은
    세 번째 인자를 억지로 전달하지 않아 그 호출 계약을 그대로 지킨다.
    """
    if version_id is not None:
        return load_file_snapshot(app_id, artifact_type, version_id=version_id)
    if version_no is not None:
        return load_file_snapshot(app_id, artifact_type, version_no)
    return load_file_snapshot(app_id, artifact_type)


def _verify_snapshot(
    *,
    artifact_type: str,
    snapshot: Mapping[str, Any],
    reference: ArtifactSnapshotRef | None,
    expected_implementation_job_id: str | None,
) -> ArtifactSnapshotRef:
    """버전과 provenance가 테스트 시작 시 기록한 값과 같은지 확인한다."""
    actual = _reference_from_snapshot(artifact_type, snapshot)
    if reference is not None:
        comparisons = (
            ("version_id", reference.version_id, actual.version_id),
            ("version_no", reference.version_no, actual.version_no),
            ("digest", reference.digest, actual.digest),
            ("created_at", reference.created_at, actual.created_at),
            ("file_count", reference.file_count, actual.file_count),
        )
        for field, expected, found in comparisons:
            # 이전 입력처럼 version_id가 없으면 version_no와 파일 정보만 확인한다.
            if expected is not None and expected != found:
                raise ArtifactSnapshotMismatch(
                    f"{artifact_type} snapshot의 {field}가 테스트 입력과 다릅니다: "
                    f"expected={expected}, actual={found}"
                )

    metadata = snapshot.get("metadata") or {}
    actual_job_id = metadata.get("implementation_job_id")
    if (
        expected_implementation_job_id is not None
        and actual_job_id != expected_implementation_job_id
    ):
        raise ArtifactSnapshotMismatch(
            f"{artifact_type} snapshot은 요청한 구현 작업의 결과가 아닙니다: "
            f"expected={expected_implementation_job_id}, actual={actual_job_id or 'missing'}"
        )
    return actual


def capture_testing_input(
    app_id: str,
    implementation_job_id: str,
    run_root: str | Path,
    *,
    artifact_version_ids: Mapping[str, int] | None = None,
    completed_at: str | datetime | None = None,
) -> TestingInput:
    """구현 작업이 저장한 snapshot을 찾아 새 Testing job의 고정 입력을 만든다.

    ``artifact_version_ids``가 전달되면 해당 목록만 사용한다. 목록에 없는 선택 산출물을
    현재 최신 버전으로 채우지 않는다. 이 규칙 덕분에 job-1이 끝난 뒤 job-2가 같은 앱에
    새 파일을 저장했더라도 job-1의 DB 식별자로 원래 파일을 찾을 수 있다.
    """
    references: dict[str, ArtifactSnapshotRef] = {}
    fixed_ids = dict(artifact_version_ids) if artifact_version_ids is not None else None
    if fixed_ids is not None:
        unknown = sorted(set(fixed_ids) - TESTING_ARTIFACT_TYPES)
        if unknown:
            raise ArtifactSnapshotMismatch(
                "구현 작업에 지원하지 않는 산출물 ID가 기록되어 있습니다: "
                + ", ".join(unknown)
            )
        missing_required = sorted(REQUIRED_TESTING_ARTIFACT_TYPES - set(fixed_ids))
        if missing_required:
            raise ArtifactSourceUnavailable(
                "구현 작업에 테스트 필수 산출물 ID가 없습니다: "
                + ", ".join(missing_required)
            )

    for artifact_type in ARTIFACT_APPLICATION_PREFIXES:
        if fixed_ids is not None and artifact_type not in fixed_ids:
            continue
        try:
            snapshot = _load_selected_snapshot(
                app_id,
                artifact_type,
                version_id=(fixed_ids or {}).get(artifact_type),
            )
        except AppNotFound as error:
            raise ArtifactSourceUnavailable(f"알 수 없는 앱 ID입니다: {app_id}") from error
        if not snapshot or not snapshot.get("files"):
            if artifact_type in REQUIRED_TESTING_ARTIFACT_TYPES:
                raise ArtifactSourceUnavailable(
                    f"테스트에 필요한 {artifact_type} snapshot을 찾을 수 없습니다: app={app_id}"
                )
            continue
        reference = _verify_snapshot(
            artifact_type=artifact_type,
            snapshot=snapshot,
            reference=None,
            expected_implementation_job_id=implementation_job_id,
        )
        references[artifact_type] = reference

    parsed_completed_at = (
        datetime.fromisoformat(completed_at)
        if isinstance(completed_at, str)
        else completed_at
    )
    return TestingInput(
        app_id=app_id,
        implementation_job_id=implementation_job_id,
        run_root=Path(run_root),
        implementation_completed_at=parsed_completed_at,
        artifacts=references,
    )


def materialize_artifact(
    app_id: str,
    artifact_type: str,
    destination: Path,
    *,
    version_no: int | None = None,
    snapshot_ref: ArtifactSnapshotRef | None = None,
    expected_implementation_job_id: str | None = None,
) -> dict[str, Any]:
    """파일 snapshot 하나를 정확한 버전으로 읽어 ``destination``에 복원한다.

    참조를 생략한 기존 호출은 현재 snapshot을 읽는다. 참조가 있으면 DB에서 그 버전만
    읽고 저장 당시의 digest, 생성 시각, 파일 수와 구현 작업 ID까지 확인한 뒤 파일을 쓴다.
    """
    if snapshot_ref is not None and snapshot_ref.artifact_type != artifact_type:
        raise ArtifactSnapshotMismatch(
            "요청한 산출물 종류와 snapshot 참조의 종류가 다릅니다: "
            f"requested={artifact_type}, reference={snapshot_ref.artifact_type}"
        )
    if (
        snapshot_ref is not None
        and version_no is not None
        and snapshot_ref.version_no != version_no
    ):
        raise ArtifactSnapshotMismatch(
            f"{artifact_type}에 서로 다른 버전 번호가 전달되었습니다: "
            f"version_no={version_no}, reference={snapshot_ref.version_no}"
        )

    selected_version = snapshot_ref.version_no if snapshot_ref is not None else version_no
    selected_id = snapshot_ref.version_id if snapshot_ref is not None else None
    try:
        snapshot = _load_selected_snapshot(
            app_id,
            artifact_type,
            version_no=None if selected_id is not None else selected_version,
            version_id=selected_id,
        )
    except AppNotFound as error:
        raise ArtifactSourceUnavailable(f"알 수 없는 앱 ID입니다: {app_id}") from error
    if not snapshot or not snapshot.get("files"):
        selector = selected_id if selected_id is not None else selected_version
        detail = f", version={selector}" if selector is not None else ""
        raise ArtifactSourceUnavailable(
            f"저장된 {artifact_type} snapshot이 없습니다: app={app_id}{detail}"
        )

    actual = _verify_snapshot(
        artifact_type=artifact_type,
        snapshot=snapshot,
        reference=snapshot_ref,
        expected_implementation_job_id=expected_implementation_job_id,
    )
    written: list[str] = []
    for file_path, item in sorted(snapshot["files"].items()):
        content = str(item["content"])
        expected_file_digest = str(item.get("sha256") or "").casefold()
        observed_file_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if not expected_file_digest or observed_file_digest != expected_file_digest:
            raise ArtifactSnapshotMismatch(
                f"{artifact_type} snapshot의 파일 digest가 다릅니다: path={file_path}"
            )
        target = destination / _safe_relative(file_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(file_path)

    return {
        "source": "db",
        "artifact_type": artifact_type,
        "version_id": actual.version_id,
        "version_no": actual.version_no,
        "snapshot_digest": actual.digest,
        "created_at": actual.created_at.isoformat(),
        "implementation_job_id": (snapshot.get("metadata") or {}).get(
            "implementation_job_id"
        ),
        "file_count": len(written),
        "files": written,
    }


@contextmanager
def materialized_artifact(
    app_id: str,
    artifact_type: str,
    *,
    version_no: int | None = None,
    snapshot_ref: ArtifactSnapshotRef | None = None,
    expected_implementation_job_id: str | None = None,
) -> Iterator[tuple[Path, dict[str, Any]]]:
    """snapshot 하나를 임시 폴더에 복원하고 검사가 끝나면 폴더를 지운다."""
    with tempfile.TemporaryDirectory(prefix="easydep-testing-scan-") as temporary:
        directory = Path(temporary)
        provenance = materialize_artifact(
            app_id,
            artifact_type,
            directory,
            version_no=version_no,
            snapshot_ref=snapshot_ref,
            expected_implementation_job_id=expected_implementation_job_id,
        )
        yield directory, provenance


@contextmanager
def materialized_testing_application(testing_input: TestingInput) -> Iterator[Path]:
    """고정 입력의 모든 파일을 새 ``<run_root>/application`` tree로 복원한다.

    반환하는 경로는 ``application`` 자체가 아니라 임시 ``run_root``다. 기존 unit test
    adapter가 ``run_root/application``을 찾기 때문에 호출자는 adapter 계약을 바꿀 필요가
    없다. 임시 폴더는 context를 벗어나면 자동으로 제거된다.
    """
    with tempfile.TemporaryDirectory(prefix="easydep-testing-run-") as temporary:
        materialized_run_root = Path(temporary)
        application = materialized_run_root / "application"
        application.mkdir(parents=True)
        occupied_paths: dict[str, str] = {}

        for artifact_type, prefix in ARTIFACT_APPLICATION_PREFIXES.items():
            reference = testing_input.snapshot_for(artifact_type)
            if reference is None:
                # 고정 입력에 없는 선택 산출물은 최신 DB 값으로 보충하지 않는다.
                continue
            target = application / prefix if prefix else application
            provenance = materialize_artifact(
                testing_input.app_id,
                artifact_type,
                target,
                snapshot_ref=reference,
                expected_implementation_job_id=testing_input.implementation_job_id,
            )
            for file_path in provenance["files"]:
                restored = f"{prefix}/{file_path}" if prefix else file_path
                previous_type = occupied_paths.get(restored)
                if previous_type is not None:
                    raise ArtifactSnapshotMismatch(
                        "서로 다른 산출물이 같은 복원 경로를 사용합니다: "
                        f"path={restored}, first={previous_type}, second={artifact_type}"
                    )
                occupied_paths[restored] = artifact_type

        yield materialized_run_root
