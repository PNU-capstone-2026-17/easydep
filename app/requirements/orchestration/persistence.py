"""현재 requirements 세션을 프로세스 밖에 저장하는 canonical persistence 경계다.

**왜.** 지금까지 진행 중인 세션은 `MemorySaver`(프로세스 메모리)와 모듈 전역 dict에만
있었다. 서버를 재시작하면 진행 중인 분석이 전멸하고, 그래서 `k8s replicas: 1`은 선택이
아니라 제약이었다. 대화형 피드백 게이트가 이 에이전트의 핵심 기능인데 그 상태가
휘발성이었다는 뜻이다.

**어디에 저장하나.** `app/db`의 엔진·세션·`Base`를 **재사용**하되 모델은 여기 둔다.
같은 MySQL, 같은 `init_db()`(그 안의 `create_all`이 이 테이블도 만든다), 새 의존성 없음.
산출물 저장소(`artifact_repository`)에 얹지 않은 이유는 둘이다:
  - `artifact_versions`는 개정 이력을 영구 보존하는 테이블인데, 체크포인트는 superstep
    마다 쓰이므로 이력이 폭발한다.
  - 산출물은 `app_id`가 있어야 하는데, `app_id` 없이 도는 세션이 정상 경로다.

**스키마는 LangGraph 공식 저장소(SQLite/Postgres)와 같은 모양이다.** 체크포인트 본문,
채널별 값(blob), 보류 중 쓰기(writes)를 나눠 담는다. 이 분리는 취향이 아니라 규약이라
— `InMemorySaver`가 하는 것과 같은 순서로 넣고 꺼내야 재개가 맞는다.

테이블 이름에 `requirements_` 접두사를 붙인 것은 이 저장소를 여러 에이전트가 공유하기
때문이다. 누구 것인지 이름으로 보여야 한다.
"""
from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from sqlalchemy import Boolean, Integer, LargeBinary, String, delete, select
from sqlalchemy.dialects.mysql import LONGBLOB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.db.session import session_scope

# 체크포인트 하나가 MySQL BLOB(64KiB)을 넘기 쉽다 — 명세가 UC 수만큼 들어간다.
# SQLite에서는 그냥 BLOB이라 테스트는 같은 코드로 돈다.
_Blob = LargeBinary().with_variant(LONGBLOB(), "mysql")

_serde = JsonPlusSerializer()

#: `dumps_typed`가 값 없음을 표시하는 타입. `loads_typed`는 이걸 모르므로 걸러내야 한다.
_EMPTY = "empty"


def _dump(value: Any) -> tuple[str, bytes]:
    """직렬화하고 **바이트로 굳힌다.**

    `dumps_typed`는 페이로드에 따라 `memoryview`를 돌려준다. 그걸 그대로 바인딩하면
    바탕 버퍼가 먼저 풀렸을 때 드라이버가 "bad parameter or other API misuse"로 죽는다.
    GC 압력이 있어야 나타나므로 단위 테스트로는 안 보이고 부하가 걸린 뒤에 터진다.
    """
    type_, payload = _serde.dumps_typed(value)
    return type_, bytes(payload)


class RequirementsCheckpoint(Base):
    """체크포인트 본문(채널 값은 뺀 뼈대) + 메타데이터."""

    __tablename__ = "requirements_checkpoints"

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(128), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checkpoint_type: Mapped[str] = mapped_column(String(32))
    checkpoint: Mapped[bytes] = mapped_column(_Blob)
    metadata_type: Mapped[str] = mapped_column(String(32))
    checkpoint_metadata: Mapped[bytes] = mapped_column(_Blob)


class RequirementsCheckpointBlob(Base):
    """채널 하나의 값 하나. 버전이 올라갈 때만 새 행이 생긴다."""

    __tablename__ = "requirements_checkpoint_blobs"

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(128), primary_key=True, default="")
    channel: Mapped[str] = mapped_column(String(255), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    blob_type: Mapped[str] = mapped_column(String(32))
    blob: Mapped[bytes | None] = mapped_column(_Blob, nullable=True)


class RequirementsCheckpointWrite(Base):
    """아직 체크포인트에 반영되지 않은 쓰기(보류 중 쓰기).

    interrupt로 멈춘 세션을 재개할 때 이게 있어야 멈춘 지점이 복원된다.
    """

    __tablename__ = "requirements_checkpoint_writes"

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(128), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(255))
    write_type: Mapped[str] = mapped_column(String(32))
    blob: Mapped[bytes] = mapped_column(_Blob)
    task_path: Mapped[str] = mapped_column(String(255), default="")


class RequirementsSession(Base):
    """세션이 어느 토폴로지로 시작됐는지.

    게이트 on/off는 서로 다른 그래프로 컴파일되고 각자 체크포인트를 갖는다. 그래서
    재개할 때 **시작할 때와 같은 그래프**를 골라야 체크포인트가 맞는다. 예전에는 이걸
    모듈 전역 dict(`_thread_gates`)가 들고 있어서, 재시작하면 서버 기본값으로
    떨어지고 체크포인트를 못 찾았다.
    """

    __tablename__ = "requirements_sessions"

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    gated: Mapped[bool] = mapped_column(Boolean, default=False)


def remember_session_mode(thread_id: str, gated: bool) -> None:
    """이 세션이 어느 토폴로지로 시작됐는지 기록한다(있으면 갱신)."""
    with session_scope() as db:
        row = db.get(RequirementsSession, thread_id)
        if row is None:
            db.add(RequirementsSession(thread_id=thread_id, gated=gated))
        else:
            row.gated = gated


def session_mode(thread_id: str) -> bool | None:
    """기록된 토폴로지(없으면 None — 부르는 쪽이 기본값을 정한다)."""
    with session_scope() as db:
        row = db.get(RequirementsSession, thread_id)
        return None if row is None else bool(row.gated)


class SqlCheckpointSaver(BaseCheckpointSaver):
    """산출물 저장소와 같은 MySQL에 체크포인트를 쓰는 LangGraph 체크포인터.

    `InMemorySaver`와 **같은 순서로** 넣고 꺼낸다 — 채널 값은 버전별 blob으로 쪼개고,
    체크포인트 뼈대는 `channel_versions`만 들고 있다가 읽을 때 blob에서 되살린다.
    이 규약이 어긋나면 재개가 조용히 어긋난다(예외가 아니라 잘못된 상태로 이어진다).
    """

    def __init__(self) -> None:
        super().__init__(serde=_serde)

    # -- 쓰기 ---------------------------------------------------------------
    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """current checkpoint와 새 channel version을 원자적 저장 단위로 기록한다."""

        skeleton = checkpoint.copy()
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        values: dict[str, Any] = skeleton.pop("channel_values")  # type: ignore[misc]

        checkpoint_type, checkpoint_bytes = _dump(skeleton)
        metadata_type, metadata_bytes = _dump(get_checkpoint_metadata(config, metadata))

        with session_scope() as db:
            for channel, version in new_versions.items():
                # 값이 없는 채널도 자리를 남긴다 — 나중에 "버전은 있는데 blob이 없다"와
                # "값이 비어 있다"를 구별해야 한다.
                blob_type, blob = (
                    _dump(values[channel]) if channel in values else (_EMPTY, b"")
                )
                db.merge(
                    RequirementsCheckpointBlob(
                        thread_id=thread_id,
                        checkpoint_ns=checkpoint_ns,
                        channel=channel,
                        version=str(version),
                        blob_type=blob_type,
                        blob=blob,
                    )
                )
            db.merge(
                RequirementsCheckpoint(
                    thread_id=thread_id,
                    checkpoint_ns=checkpoint_ns,
                    checkpoint_id=checkpoint["id"],
                    parent_checkpoint_id=config["configurable"].get("checkpoint_id"),
                    checkpoint_type=checkpoint_type,
                    checkpoint=checkpoint_bytes,
                    metadata_type=metadata_type,
                    checkpoint_metadata=metadata_bytes,
                )
            )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """LangGraph task의 pending write를 재시도 규칙에 맞춰 저장한다."""

        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        with session_scope() as db:
            for position, (channel, value) in enumerate(writes):
                idx = WRITES_IDX_MAP.get(channel, position)
                write_type, blob = _dump(value)
                row = RequirementsCheckpointWrite(
                    thread_id=thread_id,
                    checkpoint_ns=checkpoint_ns,
                    checkpoint_id=checkpoint_id,
                    task_id=task_id,
                    idx=idx,
                    channel=channel,
                    write_type=write_type,
                    blob=blob,
                    task_path=task_path,
                )
                if idx >= 0:
                    # 일반 쓰기는 먼저 쓴 것이 이긴다(같은 태스크의 재시도가 덮지 않게).
                    # 음수 idx(에러·인터럽트 등 특수 채널)는 최신이 이긴다.
                    existing = db.get(
                        RequirementsCheckpointWrite,
                        (thread_id, checkpoint_ns, checkpoint_id, task_id, idx),
                    )
                    if existing is not None:
                        continue
                    db.add(row)
                else:
                    db.merge(row)

    # -- 읽기 ---------------------------------------------------------------
    def _load_blobs(
        self, db, thread_id: str, checkpoint_ns: str, versions: ChannelVersions
    ) -> dict[str, Any]:
        if not versions:
            return {}
        rows = db.scalars(
            select(RequirementsCheckpointBlob).where(
                RequirementsCheckpointBlob.thread_id == thread_id,
                RequirementsCheckpointBlob.checkpoint_ns == checkpoint_ns,
                RequirementsCheckpointBlob.version.in_(
                    [str(v) for v in versions.values()]
                ),
            )
        ).all()
        by_key = {(r.channel, r.version): r for r in rows}
        values: dict[str, Any] = {}
        for channel, version in versions.items():
            row = by_key.get((channel, str(version)))
            if row is None or row.blob_type == _EMPTY:
                continue
            values[channel] = self.serde.loads_typed((row.blob_type, row.blob or b""))
        return values

    def _pending_writes(
        self, db, thread_id: str, checkpoint_ns: str, checkpoint_id: str
    ) -> list[tuple[str, str, Any]]:
        rows = db.scalars(
            select(RequirementsCheckpointWrite)
            .where(
                RequirementsCheckpointWrite.thread_id == thread_id,
                RequirementsCheckpointWrite.checkpoint_ns == checkpoint_ns,
                RequirementsCheckpointWrite.checkpoint_id == checkpoint_id,
            )
            .order_by(
                RequirementsCheckpointWrite.task_id, RequirementsCheckpointWrite.idx
            )
        ).all()
        return [
            (r.task_id, r.channel, self.serde.loads_typed((r.write_type, r.blob)))
            for r in rows
        ]

    def _to_tuple(self, db, row: RequirementsCheckpoint) -> CheckpointTuple:
        skeleton: Checkpoint = self.serde.loads_typed(
            (row.checkpoint_type, row.checkpoint)
        )
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.checkpoint_id,
                }
            },
            checkpoint={
                **skeleton,
                "channel_values": self._load_blobs(
                    db, row.thread_id, row.checkpoint_ns, skeleton["channel_versions"]
                ),
            },
            metadata=self.serde.loads_typed(
                (row.metadata_type, row.checkpoint_metadata)
            ),
            pending_writes=self._pending_writes(
                db, row.thread_id, row.checkpoint_ns, row.checkpoint_id
            ),
            parent_config=(
                {
                    "configurable": {
                        "thread_id": row.thread_id,
                        "checkpoint_ns": row.checkpoint_ns,
                        "checkpoint_id": row.parent_checkpoint_id,
                    }
                }
                if row.parent_checkpoint_id
                else None
            ),
        )

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        """지정 checkpoint 또는 현재 namespace의 최신 checkpoint를 복원한다."""

        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)

        with session_scope() as db:
            query = select(RequirementsCheckpoint).where(
                RequirementsCheckpoint.thread_id == thread_id,
                RequirementsCheckpoint.checkpoint_ns == checkpoint_ns,
            )
            if checkpoint_id:
                query = query.where(
                    RequirementsCheckpoint.checkpoint_id == checkpoint_id
                )
            else:
                # id를 안 주면 최신 체크포인트. id는 단조 증가하는 UUIDv6라 정렬이 곧 시간순이다.
                query = query.order_by(
                    RequirementsCheckpoint.checkpoint_id.desc()
                ).limit(1)
            row = db.scalars(query).first()
            return None if row is None else self._to_tuple(db, row)

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,  # noqa: A002 - 상위 인터페이스가 정한 이름
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        """조건·상한에 맞는 checkpoint를 최신 순으로 반환한다."""

        # 제너레이터로 만들지 않는다. `with session_scope()` 안에서 yield 하면 소비자가
        # 중간에 멈출 때(LangGraph는 흔히 그런다) 세션이 열린 채 남고, 그 커넥션 위에서
        # 다음 put이 "transaction within a transaction"으로 깨진다. 세션 안에서 다 만들고
        # 밖에서 넘긴다 — limit은 SQL에서 걸리므로 메모리는 여전히 유계다.
        with session_scope() as db:
            query = select(RequirementsCheckpoint)
            if config is not None:
                query = query.where(
                    RequirementsCheckpoint.thread_id
                    == config["configurable"]["thread_id"]
                )
                checkpoint_ns = config["configurable"].get("checkpoint_ns")
                if checkpoint_ns is not None:
                    query = query.where(
                        RequirementsCheckpoint.checkpoint_ns == checkpoint_ns
                    )
            if before is not None and (bound := get_checkpoint_id(before)):
                query = query.where(RequirementsCheckpoint.checkpoint_id < bound)
            query = query.order_by(RequirementsCheckpoint.checkpoint_id.desc())
            if limit is not None:
                query = query.limit(limit)

            found = []
            for row in db.scalars(query).all():
                tup = self._to_tuple(db, row)
                if filter and not all(
                    tup.metadata.get(k) == v for k, v in filter.items()
                ):
                    continue
                found.append(tup)
        return iter(found)

    # -- 정리 ---------------------------------------------------------------
    def delete_thread(self, thread_id: str) -> None:
        """지정 thread의 current session과 checkpoint 자료만 삭제한다."""

        with session_scope() as db:
            for model in (
                RequirementsCheckpointWrite,
                RequirementsCheckpointBlob,
                RequirementsCheckpoint,
                RequirementsSession,
            ):
                db.execute(delete(model).where(model.thread_id == thread_id))
