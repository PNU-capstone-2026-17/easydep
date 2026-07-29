"""설계 세션의 체크포인트 테이블.

설계 파이프라인은 스테이지마다 게이트에서 멈춘다(`app/design/graphs/design_graph.py`).
멈춘 지점 — 어느 스테이지까지 왔고 상태가 무엇인지 — 은 체크포인트에만 있으므로,
프로세스 메모리에 두면 서버 재시작이 곧 진행 중인 설계의 소멸이다.

체크포인터 로직은 `app/db/checkpointer.py`가 요구사항 에이전트와 공유한다. 여기서는
테이블만 정한다. 접두사를 나누는 이유는 `app/requirements/session_store.py`가 적어둔
그대로다 — 이 저장소를 여러 에이전트가 공유하므로 누구 것인지 이름으로 보여야 한다.

요구사항 에이전트와 달리 세션 모드 테이블이 없다. 설계 그래프는 토폴로지가 하나뿐이라
"어느 그래프로 시작했는지"를 기억할 필요가 없다. `thread_id`도 앱 하나당 설계 세션
하나이므로 `app_id`를 그대로 쓴다.
"""
from __future__ import annotations

from app.db.checkpointer import (
    CheckpointBlobMixin,
    CheckpointMixin,
    CheckpointWriteMixin,
    SqlCheckpointSaver as _SqlCheckpointSaver,
)
from app.db.models import Base


class DesignCheckpoint(CheckpointMixin, Base):
    __tablename__ = "design_checkpoints"


class DesignCheckpointBlob(CheckpointBlobMixin, Base):
    __tablename__ = "design_checkpoint_blobs"


class DesignCheckpointWrite(CheckpointWriteMixin, Base):
    __tablename__ = "design_checkpoint_writes"


class SqlCheckpointSaver(_SqlCheckpointSaver):
    """공용 체크포인터를 설계 에이전트의 테이블에 붙인 것."""

    def __init__(self) -> None:
        super().__init__(
            DesignCheckpoint,
            DesignCheckpointBlob,
            DesignCheckpointWrite,
        )
