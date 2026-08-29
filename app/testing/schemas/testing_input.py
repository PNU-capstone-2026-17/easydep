"""한 Testing 작업이 검사할 구현 파일 묶음을 지정한다.

구현 작업은 DB에 저장한 각 파일 묶음의 ID를 기록한다. Testing 작업은 이 ID 목록만
받아 시작할 때 애플리케이션 폴더를 한 번 복원한다. 검사 단계마다 버전, digest 같은
정보를 따로 들고 다니지 않으므로 모든 검사가 자연스럽게 같은 파일을 사용한다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
REQUIRED_TESTING_ARTIFACT_TYPES = frozenset(
    {TYPE_SOURCE_CODE, TYPE_DEPLOYMENT_FILE}
)


class TestingInput(BaseModel):
    """하나의 구현 작업과 그 작업이 저장한 파일 묶음 ID 목록이다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: str = Field(min_length=1)
    implementation_job_id: str = Field(min_length=1)
    artifact_version_ids: dict[str, int]

    @model_validator(mode="after")
    def _valid_artifact_ids(self) -> TestingInput:
        unknown = sorted(set(self.artifact_version_ids) - TESTING_ARTIFACT_TYPES)
        if unknown:
            raise ValueError(
                "테스트에서 지원하지 않는 산출물 종류가 포함되었습니다: "
                + ", ".join(unknown)
            )
        missing = sorted(
            REQUIRED_TESTING_ARTIFACT_TYPES - set(self.artifact_version_ids)
        )
        if missing:
            raise ValueError(
                "테스트 실행에 필요한 산출물 ID가 없습니다: " + ", ".join(missing)
            )
        invalid = sorted(
            artifact_type
            for artifact_type, version_id in self.artifact_version_ids.items()
            if not isinstance(version_id, int)
            or isinstance(version_id, bool)
            or version_id < 1
        )
        if invalid:
            raise ValueError(
                "산출물 ID는 1 이상의 정수여야 합니다: " + ", ".join(invalid)
            )
        return self
