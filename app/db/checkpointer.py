"""에이전트가 공유하는 SQL 체크포인터 — 테이블은 각 에이전트가 자기 것을 가진다.

**왜 여기 있나.** 원래 이 구현은 `app/requirements/session_store.py` 안에 있었고 모델
클래스 세 개를 하드코딩했다. 그런데 로직 자체는 에이전트와 무관하다 — 어느 그래프의
체크포인트든 넣고 꺼내는 방식은 같다. 설계 에이전트도 대화형 게이트를 갖게 되면서 같은
것이 두 벌 필요해졌으므로, 로직은 여기 한 벌만 두고 **테이블만 에이전트별로** 붙인다.

테이블을 공유하지 않고 접두사로 나누는 이유는 `session_store.py`가 원래 적어둔 그대로다:
이 저장소를 여러 에이전트가 공유하므로 누구 것인지 이름으로 보여야 한다. 그래서 이
모듈은 컬럼 정의를 **믹스인**으로 주고, 테이블 이름은 상속하는 쪽이 정한다.

**스키마는 LangGraph 공식 저장소(SQLite/Postgres)와 같은 모양이다.** 체크포인트 본문,
채널별 값(blob), 보류 중 쓰기(writes)를 나눠 담는다. 이 분리는 취향이 아니라 규약이라
— `InMemorySaver`가 하는 것과 같은 순서로 넣고 꺼내야 재개가 맞는다.
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
from sqlalchemy import Integer, LargeBinary, String, delete, select
from sqlalchemy.dialects.mysql import LONGBLOB
from sqlalchemy.orm import Mapped, mapped_column

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


# ---------------------------------------------------------------------------
# 컬럼 정의 믹스인 — 테이블 이름과 Base 상속은 에이전트별 모듈이 정한다.
# ---------------------------------------------------------------------------
class CheckpointMixin:
    """체크포인트 본문(채널 값은 뺀 뼈대) + 메타데이터."""

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(128), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    checkpoint_type: Mapped[str] = mapped_column(String(32))
    checkpoint: Mapped[bytes] = mapped_column(_Blob)
    metadata_type: Mapped[str] = mapped_column(String(32))
    checkpoint_metadata: Mapped[bytes] = mapped_column(_Blob)


class CheckpointBlobMixin:
    """채널 하나의 값 하나. 버전이 올라갈 때만 새 행이 생긴다."""

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(128), primary_key=True, default="")
    channel: Mapped[str] = mapped_column(String(255), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    blob_type: Mapped[str] = mapped_column(String(32))
    blob: Mapped[bytes | None] = mapped_column(_Blob, nullable=True)


class CheckpointWriteMixin:
    """아직 체크포인트에 반영되지 않은 쓰기(보류 중 쓰기).

    interrupt로 멈춘 세션을 재개할 때 이게 있어야 멈춘 지점이 복원된다.
    """

    thread_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(128), primary_key=True, default="")
    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(255))
    write_type: Mapped[str] = mapped_column(String(32))
    blob: Mapped[bytes] = mapped_column(_Blob)
    task_path: Mapped[str] = mapped_column(String(255), default="")


class SqlCheckpointSaver(BaseCheckpointSaver):
    """산출물 저장소와 같은 MySQL에 체크포인트를 쓰는 LangGraph 체크포인터.

    `InMemorySaver`와 **같은 순서로** 넣고 꺼낸다 — 채널 값은 버전별 blob으로 쪼개고,
    체크포인트 뼈대는 `channel_versions`만 들고 있다가 읽을 때 blob에서 되살린다.
    이 규약이 어긋나면 재개가 조용히 어긋난다(예외가 아니라 잘못된 상태로 이어진다).

    테이블 세 벌은 생성자로 받는다 — 에이전트마다 자기 접두사 테이블을 쓰기 때문이다.
    `extra_thread_models`는 스레드 단위로 같이 지워야 할 부수 테이블(예: 세션 모드 기록)
    이다. `thread_id` 컬럼만 있으면 된다.
    """

    def __init__(
        self,
        checkpoint_model: type,
        blob_model: type,
        write_model: type,
        extra_thread_models: Sequence[type] = (),
    ) -> None:
        super().__init__(serde=_serde)
        self._checkpoint = checkpoint_model
        self._blob = blob_model
        self._write = write_model
        self._extra = tuple(extra_thread_models)

    # -- 쓰기 ---------------------------------------------------------------
    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
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
                    self._blob(
                        thread_id=thread_id,
                        checkpoint_ns=checkpoint_ns,
                        channel=channel,
                        version=str(version),
                        blob_type=blob_type,
                        blob=blob,
                    )
                )
            db.merge(
                self._checkpoint(
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
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        with session_scope() as db:
            for position, (channel, value) in enumerate(writes):
                idx = WRITES_IDX_MAP.get(channel, position)
                write_type, blob = _dump(value)
                row = self._write(
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
                        self._write,
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
            select(self._blob).where(
                self._blob.thread_id == thread_id,
                self._blob.checkpoint_ns == checkpoint_ns,
                self._blob.version.in_([str(v) for v in versions.values()]),
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
            select(self._write)
            .where(
                self._write.thread_id == thread_id,
                self._write.checkpoint_ns == checkpoint_ns,
                self._write.checkpoint_id == checkpoint_id,
            )
            .order_by(self._write.task_id, self._write.idx)
        ).all()
        return [
            (r.task_id, r.channel, self.serde.loads_typed((r.write_type, r.blob)))
            for r in rows
        ]

    def _to_tuple(self, db, row) -> CheckpointTuple:
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
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)

        with session_scope() as db:
            query = select(self._checkpoint).where(
                self._checkpoint.thread_id == thread_id,
                self._checkpoint.checkpoint_ns == checkpoint_ns,
            )
            if checkpoint_id:
                query = query.where(self._checkpoint.checkpoint_id == checkpoint_id)
            else:
                # id를 안 주면 최신 체크포인트. id는 단조 증가하는 UUIDv6라 정렬이 곧 시간순이다.
                query = query.order_by(self._checkpoint.checkpoint_id.desc()).limit(1)
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
        # 제너레이터로 만들지 않는다. `with session_scope()` 안에서 yield 하면 소비자가
        # 중간에 멈출 때(LangGraph는 흔히 그런다) 세션이 열린 채 남고, 그 커넥션 위에서
        # 다음 put이 "transaction within a transaction"으로 깨진다. 세션 안에서 다 만들고
        # 밖에서 넘긴다 — limit은 SQL에서 걸리므로 메모리는 여전히 유계다.
        with session_scope() as db:
            query = select(self._checkpoint)
            if config is not None:
                query = query.where(
                    self._checkpoint.thread_id == config["configurable"]["thread_id"]
                )
                checkpoint_ns = config["configurable"].get("checkpoint_ns")
                if checkpoint_ns is not None:
                    query = query.where(self._checkpoint.checkpoint_ns == checkpoint_ns)
            if before is not None and (bound := get_checkpoint_id(before)):
                query = query.where(self._checkpoint.checkpoint_id < bound)
            query = query.order_by(self._checkpoint.checkpoint_id.desc())
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
        with session_scope() as db:
            for model in (self._write, self._blob, self._checkpoint, *self._extra):
                db.execute(delete(model).where(model.thread_id == thread_id))
