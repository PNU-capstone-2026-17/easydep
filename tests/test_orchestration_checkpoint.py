from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.core.orchestration.checkpoint import SqliteMemorySaver


def _graph(path, store_id="test"):
    builder = StateGraph(dict)

    def ask(state):
        answer = interrupt({"question": "Continue?"})
        return {**state, "answer": answer}

    builder.add_node("ask", ask)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", END)
    return builder.compile(checkpointer=SqliteMemorySaver(path, store_id))


def test_checkpoint_resumes_after_saver_is_recreated(tmp_path):
    path = tmp_path / "checkpoints.sqlite3"
    config = {"configurable": {"thread_id": "run-1"}}

    first = _graph(path).invoke({"value": 1}, config)
    assert first["__interrupt__"][0].value == {"question": "Continue?"}

    recreated = _graph(path)
    resumed = recreated.invoke(Command(resume="yes"), config)
    assert resumed["value"] == 1
    assert resumed["answer"] == "yes"
    assert recreated.get_state(config).next == ()


def test_logical_stores_do_not_overwrite_each_other(tmp_path):
    path = tmp_path / "checkpoints.sqlite3"
    config = {"configurable": {"thread_id": "same-thread"}}
    _graph(path, "one").invoke({"value": 1}, config)
    _graph(path, "two").invoke({"value": 2}, config)

    one = _graph(path, "one").get_state(config)
    two = _graph(path, "two").get_state(config)
    assert one.values["value"] == 1
    assert two.values["value"] == 2
