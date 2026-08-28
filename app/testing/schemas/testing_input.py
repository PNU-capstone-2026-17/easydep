"""한 테스트 작업이 끝까지 사용할 구현 산출물 버전을 표현한다.

테스트를 시작한 뒤 같은 앱에서 구현을 다시 실행할 수 있다. 이때 검사 단계마다 DB의
"최신 버전"을 다시 읽으면 unit test는 첫 번째 구현을, IaC 검사는 두 번째 구현을 보는
식으로 결과가 섞일 수 있다. 이 모듈의 모델은 테스트 시작 시점의 파일 snapshot 정보를
한 번 기록하고 모든 검사 단계에 같은 값을 전달하기 위한 계약이다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.models import (
    TYPE_DEPLOYMENT_FILE,
    TYPE_FRONTEND_SOURCE_CODE,
    TYPE_IAC_CODE,
    TYPE_SOURCE_CODE,
    TYPE_TEST_CODE,
)

TESTING_ARTIFACT_TYPES = frozenset(
    {
        TYPE_SOURCE_CODE,
        TYPE_FRONTEND_SOURCE_CODE,
        TYPE_TEST_CODE,
        TYPE_DEPLOYMENT_FILE,
        TYPE_IAC_CODE,
    }
)
"""테스트 실행에 사용할 수 있는 구현 파일 산출물 종류다."""

REQUIRED_TESTING_ARTIFACT_TYPES = frozenset(
    {TYPE_SOURCE_CODE, TYPE_DEPLOYMENT_FILE}
)
"""애플리케이션을 빌드하고 실행하려면 반드시 필요한 산출물 종류다."""


class ArtifactSnapshotRef(BaseModel):
    """DB에 저장된 파일 snapshot 하나를 다시 찾고 확인하는 데 필요한 정보다.

    ``version_no``는 정확한 DB 버전을 찾는 열쇠다. 나머지 값은 그 번호가 테스트를
    시작할 때 보았던 파일 묶음과 정말 같은지 확인하는 데 사용한다. digest는 파일 경로와
    각 파일의 SHA-256을 정렬해 계산한 값이므로 파일 내용이 하나라도 달라지면 바뀐다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_type: str
    # ``version_id``는 구현 작업 기록이 보관하는 내부 DB 식별자다. 새 입력은 이 값을
    # 사용해 처음 snapshot을 찾고, 이전 호출자가 번호만 아는 경우에는 생략할 수 있다.
    version_id: int | None = Field(default=None, ge=1)
    version_no: int = Field(ge=1)
    digest: str = Field(min_length=64, max_length=64)
    created_at: datetime
    file_count: int = Field(ge=1)

    @field_validator("artifact_type")
    @classmethod
    def _known_artifact_type(cls, value: str) -> str:
        if value not in TESTING_ARTIFACT_TYPES:
            raise ValueError(f"테스트에서 지원하지 않는 산출물 종류입니다: {value}")
        return value

    @field_validator("digest")
    @classmethod
    def _sha256_digest(cls, value: str) -> str:
        normalized = value.casefold()
        if any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("snapshot digest는 64자리 SHA-256 16진수여야 합니다.")
        return normalized


class TestingInput(BaseModel):
    """테스트 작업 전체가 공유하는 앱, 구현 작업과 파일 snapshot 정보다.

    frontend, test, IaC 파일은 프로젝트 성격에 따라 없을 수 있다. 반면 source와
    deployment 파일이 없으면 애플리케이션을 빌드할 수 없으므로 입력 생성 단계에서 바로
    실패한다. ``artifacts``의 key와 각 참조의 ``artifact_type``도 같아야 한다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: str = Field(min_length=1)
    implementation_job_id: str = Field(min_length=1)
    run_root: Path
    implementation_completed_at: datetime | None = None
    artifacts: dict[str, ArtifactSnapshotRef]

    @model_validator(mode="after")
    def _consistent_artifacts(self) -> TestingInput:
        unknown = sorted(set(self.artifacts) - TESTING_ARTIFACT_TYPES)
        if unknown:
            raise ValueError(
                "테스트에서 지원하지 않는 산출물 종류가 포함되었습니다: "
                + ", ".join(unknown)
            )
        mismatched = sorted(
            artifact_type
            for artifact_type, reference in self.artifacts.items()
            if artifact_type != reference.artifact_type
        )
        if mismatched:
            raise ValueError(
                "artifacts key와 snapshot의 artifact_type이 다릅니다: "
                + ", ".join(mismatched)
            )
        missing = sorted(REQUIRED_TESTING_ARTIFACT_TYPES - set(self.artifacts))
        if missing:
            raise ValueError(
                "테스트 실행에 필요한 산출물 snapshot이 없습니다: "
                + ", ".join(missing)
            )
        return self

    def snapshot_for(self, artifact_type: str) -> ArtifactSnapshotRef | None:
        """산출물 종류에 맞는 고정 snapshot 참조를 반환한다."""
        return self.artifacts.get(artifact_type)

    def version_map(self) -> dict[str, int]:
        """DB 조회 함수에 바로 전달할 ``산출물 종류 → 버전 번호``를 만든다."""
        return {
            artifact_type: reference.version_no
            for artifact_type, reference in self.artifacts.items()
        }

    def version_id_map(self) -> dict[str, int]:
        """내부 DB 식별자가 있는 snapshot만 ``산출물 종류 → ID``로 반환한다."""
        return {
            artifact_type: reference.version_id
            for artifact_type, reference in self.artifacts.items()
            if reference.version_id is not None
        }
