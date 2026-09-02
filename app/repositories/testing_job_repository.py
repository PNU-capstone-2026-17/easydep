"""Testing 작업의 재개에 필요한 작은 상태를 MySQL에 저장한다.

Testing 서비스는 비동기 thread와 HTTP 요청에서 같은 작업을 갱신한다. 이 모듈은 DB transaction과
ORM 변환만 담당하며, 어떤 검사를 실행하거나 어느 단계로 수리할지는 판단하지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.db.models import TestingJob
from app.db.session import session_scope


class TestingJobRecord(BaseModel):
    """DB와 Testing 서비스가 주고받는 한 작업의 전체 상태다."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    job_id: str = Field(min_length=1, max_length=36)
    app_id: str = Field(min_length=1, max_length=36)
    implementation_job_id: str = Field(min_length=1, max_length=36)
    status: str = Field(min_length=1, max_length=24)
    current_node: str | None = Field(default=None, max_length=64)
    testing_input: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None
    repair_of_job_id: str | None = Field(default=None, max_length=36)
    repair_history: dict[str, Any] = Field(default_factory=dict)
    previous_findings: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime | None = None


class TestingJobChanges(BaseModel):
    """백그라운드 실행이 갱신할 수 있는 필드만 허용한다."""

    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(default=None, min_length=1, max_length=24)
    current_node: str | None = Field(default=None, max_length=64)
    result: dict[str, Any] | None = None
    error: str | None = None
    repair_history: dict[str, Any] | None = None
    previous_findings: list[str] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


def insert_testing_job(record: TestingJobRecord) -> TestingJobRecord:
    """새 작업을 한 번 저장하고 DB가 기록한 시각을 포함해 반환한다."""

    values = record.model_dump(
        mode="python",
        exclude={"created_at", "updated_at"},
        exclude_none=True,
    )
    row = TestingJob(**values)
    with session_scope() as session:
        session.add(row)
        session.flush()
        session.refresh(row)
        return TestingJobRecord.model_validate(row)


def load_testing_job(job_id: str) -> TestingJobRecord | None:
    """ID가 일치하는 작업을 반환하며, 없으면 ``None``을 반환한다."""

    with session_scope() as session:
        row = session.get(TestingJob, job_id)
        return TestingJobRecord.model_validate(row) if row is not None else None


def update_testing_job(job_id: str, changes: TestingJobChanges) -> TestingJobRecord:
    """명시적으로 전달된 필드만 갱신한다."""

    values = changes.model_dump(
        mode="python",
        include=changes.model_fields_set,
    )
    with session_scope() as session:
        row = session.get(TestingJob, job_id)
        if row is None:
            raise KeyError(job_id)
        for field_name, value in values.items():
            setattr(row, field_name, value)
        # SQLite와 MySQL 설정에 관계없이 조회 직후에도 갱신 시각이 분명하게 보이게 한다.
        row.updated_at = datetime.now(UTC).replace(tzinfo=None)
        session.flush()
        session.refresh(row)
        return TestingJobRecord.model_validate(row)


def unfinished_testing_jobs() -> list[TestingJobRecord]:
    """프로세스 종료로 중단됐을 수 있는 작업을 오래된 순서로 반환한다."""

    with session_scope() as session:
        rows = session.scalars(
            select(TestingJob)
            .where(TestingJob.status.in_(("QUEUED", "RUNNING")))
            .order_by(TestingJob.created_at, TestingJob.job_id)
        ).all()
        return [TestingJobRecord.model_validate(row) for row in rows]
