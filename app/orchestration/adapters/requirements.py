"""Persistent, database-independent requirements-agent boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from app.orchestration.checkpoint import (
    DEFAULT_CHECKPOINT_PATH,
    SqliteMemorySaver,
)
from app.requirements.contracts.state import AgentState
from app.requirements.runtime import telemetry


class RequirementsAdapter:
    def __init__(
        self,
        checkpoint_path: str | Path = DEFAULT_CHECKPOINT_PATH,
        *,
        feedback_gates: bool = False,
    ) -> None:
        from app.requirements.orchestration.graph import build_graph

        saver = SqliteMemorySaver(checkpoint_path, "requirements")
        self.graph = build_graph(feedback_gates=feedback_gates, saver=saver)
        self.last_telemetry: dict[str, Any] = {}

    @staticmethod
    def _payload(result: dict[str, Any], thread_id: str) -> dict[str, Any]:
        from app.requirements.orchestration.graph import result_payload

        payload = result_payload(result, thread_id)
        telemetry_result = result.get("_orchestration_telemetry")
        if isinstance(telemetry_result, dict):
            payload["telemetry"] = telemetry_result
        return payload

    def _invoke(self, value: Any, config: RunnableConfig) -> dict[str, Any]:
        thread_id = str(config.get("configurable", {}).get("thread_id") or "requirements")
        stats: telemetry.RunStats | None = None
        try:
            with telemetry.run_scope(f"orchestration:{thread_id}") as stats:
                result = dict(self.graph.invoke(value, config))
                if not self.graph.get_state(config).next:
                    result.pop("__interrupt__", None)
        finally:
            if stats is not None:
                self.last_telemetry = stats.as_dict()
        result["_orchestration_telemetry"] = stats.as_dict()
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
