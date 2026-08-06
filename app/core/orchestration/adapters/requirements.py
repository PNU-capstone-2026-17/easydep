"""Persistent, database-independent requirements-agent boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from app.core.orchestration.checkpoint import (
    DEFAULT_CHECKPOINT_PATH,
    SqliteMemorySaver,
)
from app.requirements.agent.state import AgentState


class RequirementsAdapter:
    def __init__(self, checkpoint_path: str | Path = DEFAULT_CHECKPOINT_PATH) -> None:
        from app.requirements.agent.graph import build_graph

        saver = SqliteMemorySaver(checkpoint_path, "requirements")
        self.graph = build_graph(feedback_gates=False, saver=saver)

    @staticmethod
    def _payload(result: dict[str, Any], thread_id: str) -> dict[str, Any]:
        from app.requirements.agent.graph import _result_payload

        return _result_payload(result, thread_id)

    def _invoke(self, value: Any, config: RunnableConfig) -> dict[str, Any]:
        result = dict(self.graph.invoke(value, config))
        if not self.graph.get_state(config).next:
            result.pop("__interrupt__", None)
        return result

    def start(
        self,
        *,
        app_id: str,
        thread_id: str,
        requirements: list[str],
        constraints_text: str,
    ) -> dict[str, Any]:
        initial: dict[str, Any] = {"raw_requirements": requirements}
        if constraints_text.strip():
            initial["resource_constraints_text"] = constraints_text
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        result = self._invoke(cast(AgentState, initial), config)
        return self._payload(result, thread_id)

    def resume(
        self, *, app_id: str, thread_id: str, answer: Any
    ) -> dict[str, Any]:
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        result = self._invoke(Command(resume=answer), config)
        return self._payload(result, thread_id)
