"""In-memory boundary around the member-owned design graph."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from app.core.orchestration.checkpoint import (
    DEFAULT_CHECKPOINT_PATH,
    SqliteMemorySaver,
)


class DesignContractError(RuntimeError):
    pass


class DesignAdapter:
    """Own a private graph instance without using the design MySQL saver."""

    def __init__(self, checkpoint_path=DEFAULT_CHECKPOINT_PATH) -> None:
        from app.design.graphs.design_graph import build_design_graph

        self.graph = build_design_graph(
            SqliteMemorySaver(checkpoint_path, "design")
        )

    @staticmethod
    def _state(requirements_result: dict[str, Any]) -> dict[str, Any]:
        use_case_specs = requirements_result.get("use_case_specs") or []
        if not use_case_specs:
            raise DesignContractError(
                "Requirements analysis did not produce use_case_specs"
            )
        return {
            "refined_requirements": requirements_result.get("requirements") or [],
            "usecase_spec": {
                "actors": requirements_result.get("actors") or [],
                "use_cases": requirements_result.get("use_cases") or [],
                "use_case_specs": use_case_specs,
            },
            "usecase_diagram_puml": requirements_result.get("diagram") or "",
            "resource_spec": requirements_result.get("resource_spec") or {},
        }

    @staticmethod
    def _payload(result: dict[str, Any], session_id: str) -> dict[str, Any]:
        from app.artifacts_api import to_web_response

        payload: dict[str, Any] = {"app_id": session_id, **to_web_response(result)}
        # Keep structured sources for orchestration-owned post-processing. The web
        # response intentionally exposes rendered artifacts only, but cloud design
        # needs the deployment model to distinguish stateless and stateful layouts.
        for key in (
            "extracted_bce_classes",
            "sequence_diagram_model",
            "api_spec_model",
            "erd_bce_classes",
            "deployment_diagram_model",
        ):
            if result.get(key):
                payload[key] = result[key]
        interruptions = result.get("__interrupt__") or []
        if interruptions:
            value = interruptions[0].value
            payload.update(
                status="need_feedback",
                stage=value.get("stage"),
                feedback_prompt=value.get("prompt"),
            )
        else:
            payload.update(status="completed", stage=None)
        return payload

    def start(
        self, *, session_id: str, requirements_result: dict[str, Any]
    ) -> dict[str, Any]:
        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        result = dict(self.graph.invoke(self._state(requirements_result), config))
        if not self.graph.get_state(config).next:
            result.pop("__interrupt__", None)
        return self._payload(result, session_id)

    def resume(self, *, session_id: str, feedback: str) -> dict[str, Any]:
        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        result = dict(self.graph.invoke(Command(resume=feedback), config))
        if not self.graph.get_state(config).next:
            result.pop("__interrupt__", None)
        return self._payload(result, session_id)
