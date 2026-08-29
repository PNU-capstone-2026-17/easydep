"""세션 영속 저장소 테스트 — MySQL 없이 SQLite로 같은 코드를 돌린다.

가장 중요한 것은 마지막 테스트다: **프로세스가 죽은 뒤에도 멈춘 지점에서 이어지는가.**
체크포인터는 예외를 내며 틀리지 않는다 — 조용히 어긋난 상태로 이어진다. 그래서 단위
왕복이 아니라 실제 그래프를 interrupt로 멈추고, 새 인스턴스로 재개해 본다.
"""
import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from typing_extensions import TypedDict

from app.db import session as db_session
from app.db.models import Base
from app.requirements.orchestration import persistence as store

#: 이 저장소가 만드는 테이블만. 다른 에이전트 테이블은 MySQL 전용 타입(MEDIUMTEXT)을
#: 써서 SQLite에 못 만들고, 어차피 여기서 볼 대상도 아니다.
_OUR_TABLES = [
    store.RequirementsCheckpoint.__table__,
    store.RequirementsCheckpointBlob.__table__,
    store.RequirementsCheckpointWrite.__table__,
    store.RequirementsSession.__table__,
]


@pytest.fixture
def sqlite_db(monkeypatch, tmp_path):
    """app.db 의 엔진을 파일 SQLite로 갈아끼운다(그 모듈은 수정하지 않는다).

    **메모리 DB + StaticPool을 쓰면 안 된다.** 그건 커넥션 하나를 모든 스레드가
    공유하는데, LangGraph는 노드를 워커 스레드에서 돌리므로 `put_writes`가 여러
    스레드에서 동시에 들어온다. 한 sqlite3 커넥션을 동시에 쓰면 드라이버가
    "bad parameter or other API misuse"로 죽는다 — 실제 결함이 아니라 픽스처가
    운영(MySQL 풀은 스레드마다 커넥션을 준다)을 재현하지 못한 것이었다.

    파일 DB + 기본 풀이면 스레드마다 커넥션을 받아 운영과 같은 모양이 된다.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / 'session.db'}",
        # 풀에서 꺼낸 커넥션이 만든 스레드와 다른 스레드에서 쓰일 수 있다.
        # timeout은 SQLite의 단일 writer 락을 기다리는 시간(MySQL엔 해당 없음).
        connect_args={"check_same_thread": False, "timeout": 30},
        future=True,
    )
    Base.metadata.create_all(engine, tables=_OUR_TABLES)
    monkeypatch.setattr(db_session, "_engine", engine)
    monkeypatch.setattr(
        db_session, "_session_factory",
        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False),
    )
    yield engine
    engine.dispose()


def test_tables_are_registered_on_the_shared_metadata():
    """app/db/models.py 를 고치지 않고도 init_db()의 create_all 이 우리 테이블을 만든다.

    같은 Base를 상속했으므로 이 모듈이 import되기만 하면 메타데이터에 올라간다.
    올라가지 않으면 서버는 뜨는데 세션 저장만 조용히 안 된다.
    """
    assert {
        "requirements_checkpoints",
        "requirements_checkpoint_blobs",
        "requirements_checkpoint_writes",
        "requirements_sessions",
    } <= set(Base.metadata.tables)


def test_session_mode_survives_a_restart(sqlite_db):
    """게이트 on/off는 서로 다른 그래프다 — 재개할 때 시작한 쪽을 골라야 한다."""
    assert store.session_mode("t-none") is None       # 모르면 모른다고 한다
    store.remember_session_mode("t-1", gated=True)
    assert store.session_mode("t-1") is True
    store.remember_session_mode("t-1", gated=False)   # 갱신도 된다
    assert store.session_mode("t-1") is False


# ---------------------------------------------------------------------------
# 체크포인터 단위 — InMemorySaver와 같은 규약으로 넣고 꺼내는가.
# ---------------------------------------------------------------------------
def _config(thread_id, checkpoint_id=None):
    conf = {"thread_id": thread_id, "checkpoint_ns": ""}
    if checkpoint_id:
        conf["checkpoint_id"] = checkpoint_id
    return {"configurable": conf}


def _checkpoint(cid, values, versions):
    return {
        "v": 1,
        "id": cid,
        "ts": "2026-07-26T00:00:00+00:00",
        "channel_values": values,
        "channel_versions": versions,
        "versions_seen": {},
    }


def test_checkpoint_roundtrip_restores_channel_values(sqlite_db):
    saver = store.SqlCheckpointSaver()
    saver.put(
        _config("t"),
        _checkpoint("c1", {"messages": ["hello"], "count": 3}, {"messages": 1, "count": 1}),
        {"source": "loop", "step": 1},
        {"messages": 1, "count": 1},
    )

    tup = saver.get_tuple(_config("t"))
    assert tup is not None
    assert tup.checkpoint["channel_values"] == {"messages": ["hello"], "count": 3}
    assert tup.metadata["step"] == 1
    assert tup.parent_config is None


def test_get_tuple_without_an_id_returns_the_latest(sqlite_db):
    saver = store.SqlCheckpointSaver()
    saver.put(_config("t"), _checkpoint("c1", {"n": 1}, {"n": 1}), {}, {"n": 1})
    saver.put(_config("t", "c1"), _checkpoint("c2", {"n": 2}, {"n": 2}), {}, {"n": 2})

    latest = saver.get_tuple(_config("t"))
    assert latest.checkpoint["id"] == "c2"
    assert latest.checkpoint["channel_values"] == {"n": 2}
    assert latest.parent_config["configurable"]["checkpoint_id"] == "c1"

    # 특정 id를 지목하면 그걸 준다.
    older = saver.get_tuple(_config("t", "c1"))
    assert older.checkpoint["channel_values"] == {"n": 1}


def test_channels_without_a_value_do_not_come_back_as_garbage(sqlite_db):
    """값 없는 채널은 자리만 남는다 — loads_typed가 모르는 타입이라 걸러야 한다."""
    saver = store.SqlCheckpointSaver()
    saver.put(
        _config("t"),
        _checkpoint("c1", {"a": 1}, {"a": 1, "b": 1}),   # b는 값이 없다
        {},
        {"a": 1, "b": 1},
    )
    assert saver.get_tuple(_config("t")).checkpoint["channel_values"] == {"a": 1}


def test_pending_writes_come_back_with_the_checkpoint(sqlite_db):
    saver = store.SqlCheckpointSaver()
    saver.put(_config("t"), _checkpoint("c1", {}, {}), {}, {})
    saver.put_writes(_config("t", "c1"), [("out", "value")], task_id="task-1")

    tup = saver.get_tuple(_config("t"))
    assert tup.pending_writes == [("task-1", "out", "value")]


def test_a_retried_task_does_not_overwrite_its_earlier_write(sqlite_db):
    """일반 쓰기는 먼저 쓴 것이 이긴다 — InMemorySaver와 같은 규칙."""
    saver = store.SqlCheckpointSaver()
    saver.put(_config("t"), _checkpoint("c1", {}, {}), {}, {})
    saver.put_writes(_config("t", "c1"), [("out", "first")], task_id="task-1")
    saver.put_writes(_config("t", "c1"), [("out", "second")], task_id="task-1")

    assert saver.get_tuple(_config("t")).pending_writes == [("task-1", "out", "first")]


def test_interrupt_writes_are_replaced_not_appended(sqlite_db):
    """특수 채널(음수 idx)은 최신이 이긴다."""
    saver = store.SqlCheckpointSaver()
    saver.put(_config("t"), _checkpoint("c1", {}, {}), {}, {})
    saver.put_writes(_config("t", "c1"), [("__interrupt__", "ask-1")], task_id="task-1")
    saver.put_writes(_config("t", "c1"), [("__interrupt__", "ask-2")], task_id="task-1")

    writes = saver.get_tuple(_config("t")).pending_writes
    assert writes == [("task-1", "__interrupt__", "ask-2")]


def test_list_is_newest_first_and_respects_limit(sqlite_db):
    saver = store.SqlCheckpointSaver()
    for i in (1, 2, 3):
        saver.put(_config("t"), _checkpoint(f"c{i}", {"n": i}, {"n": i}), {}, {"n": i})

    ids = [t.checkpoint["id"] for t in saver.list(_config("t"))]
    assert ids == ["c3", "c2", "c1"]
    assert [t.checkpoint["id"] for t in saver.list(_config("t"), limit=2)] == ["c3", "c2"]
    assert [t.checkpoint["id"] for t in saver.list(_config("t"), before=_config("t", "c3"))] == [
        "c2", "c1"
    ]


def test_threads_do_not_see_each_other(sqlite_db):
    saver = store.SqlCheckpointSaver()
    saver.put(_config("a"), _checkpoint("c1", {"n": 1}, {"n": 1}), {}, {"n": 1})
    saver.put(_config("b"), _checkpoint("c1", {"n": 99}, {"n": 1}), {}, {"n": 1})

    assert saver.get_tuple(_config("a")).checkpoint["channel_values"] == {"n": 1}
    assert saver.get_tuple(_config("b")).checkpoint["channel_values"] == {"n": 99}


def test_delete_thread_removes_everything_for_that_thread(sqlite_db):
    saver = store.SqlCheckpointSaver()
    saver.put(_config("a"), _checkpoint("c1", {"n": 1}, {"n": 1}), {}, {"n": 1})
    saver.put_writes(_config("a", "c1"), [("out", 1)], task_id="t1")
    store.remember_session_mode("a", gated=True)
    saver.put(_config("b"), _checkpoint("c1", {"n": 2}, {"n": 1}), {}, {"n": 1})

    saver.delete_thread("a")
    assert saver.get_tuple(_config("a")) is None
    assert store.session_mode("a") is None
    assert saver.get_tuple(_config("b")) is not None   # 남의 스레드는 그대로


# ---------------------------------------------------------------------------
# 진짜 증명 — 프로세스가 죽어도 멈춘 지점에서 이어지는가.
# ---------------------------------------------------------------------------
class _S(TypedDict):
    log: list


def _paused_graph(saver):
    """중간에 사용자에게 묻고 멈추는 최소 그래프."""

    def before(state: _S) -> dict:
        return {"log": [*state["log"], "before"]}

    def ask(state: _S) -> dict:
        answer = interrupt({"q": "계속할까요?"})
        return {"log": [*state["log"], f"answer={answer}"]}

    def after(state: _S) -> dict:
        return {"log": [*state["log"], "after"]}

    builder = StateGraph(_S)
    builder.add_node("before", before)
    builder.add_node("ask", ask)
    builder.add_node("after", after)
    builder.add_edge(START, "before")
    builder.add_edge("before", "ask")
    builder.add_edge("ask", "after")
    builder.add_edge("after", END)
    return builder.compile(checkpointer=saver)


def test_a_session_resumes_after_the_process_is_gone(sqlite_db):
    config = {"configurable": {"thread_id": "session-1"}}

    # 1) 첫 프로세스: 물어보는 지점에서 멈춘다.
    first = _paused_graph(store.SqlCheckpointSaver())
    paused = first.invoke({"log": []}, config)
    assert paused["__interrupt__"][0].value == {"q": "계속할까요?"}
    assert paused["log"] == ["before"]

    # 2) 프로세스가 죽는다 — 그래프도 세이버도 버린다. 남는 건 DB뿐이다.
    del first

    # 3) 새 프로세스: 새 세이버로 같은 thread_id를 재개한다.
    second = _paused_graph(store.SqlCheckpointSaver())
    done = second.invoke(Command(resume="네"), config)

    # 앞 단계를 다시 돌지 않았고(before가 한 번뿐), 멈춘 지점부터 이어졌다.
    assert done["log"] == ["before", "answer=네", "after"]


# ---------------------------------------------------------------------------
# 배선 — 어느 경로가 영속을 쓰는가, 테이블 등록이 init_db보다 먼저 일어나는가.
# ---------------------------------------------------------------------------
def test_serving_import_chain_pulls_in_the_store():
    """server.py가 init_db()를 부르기 전에 우리 테이블이 메타데이터에 올라와야 한다.

    canonical graph가 persistence를 모듈 수준에서 import하기 때문에 성립한다. 누군가
    이 import를 지연시키면 서버는 뜨는데 세션 테이블만 조용히 안 생긴다.
    """
    import app.requirements.orchestration.service  # noqa: F401 - Workspace 서비스 진입점

    assert "requirements_checkpoints" in Base.metadata.tables


def test_session_mode_goes_to_the_database_when_persisting(sqlite_db):
    store.remember_session_mode("api-thread", gated=True)
    assert store.session_mode("api-thread") is True
