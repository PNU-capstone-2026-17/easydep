"""Top-level requirements-to-design LangGraph.

Agent graphs are treated as external, resumable services. The orchestration
graph stores each returned payload before entering its own interrupt node, so
resuming never repeats an already completed LLM call.
"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.core.orchestration.adapters import DesignAdapter, RequirementsAdapter
from app.core.orchestration.contracts import (
    DESIGN_COMPLETE,
    REQUIREMENTS_COMPLETE,
    FlowResponse,
    OrchestrationState,
)
from app.repositories import artifact_repository


def _requirements_prompt(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "phase": result.get("phase"),
        "prompt": result.get("feedback_prompt"),
        "questions": result.get("questions"),
        "resource_questions": result.get("resource_questions"),
        "edit_stage": result.get("edit_stage"),
        "edit_targets": result.get("edit_targets"),
    }


def _design_prompt(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "stage": result.get("stage"),
        "prompt": result.get("feedback_prompt"),
    }


def build_orchestration_graph(
    *,
    requirements: RequirementsAdapter | None = None,
    design: DesignAdapter | None = None,
    checkpointer: Any | None = None,
):
    """Compile the orchestration graph with replaceable agent adapters."""
    requirements_adapter = requirements or RequirementsAdapter()
    design_adapter = design or DesignAdapter()
    builder = StateGraph(OrchestrationState)

    def start_requirements(state: OrchestrationState) -> dict[str, Any]:
        result = requirements_adapter.start(
            app_id=state["app_id"],
            thread_id=state["requirements_thread_id"],
            requirements=state["requirements"],
            constraints_text=state.get("resource_constraints_text", ""),
        )
        return {
            "requirements_result": result,
            "current_stage": "requirements",
            "status": result.get("status", "unknown"),
        }

    def resume_requirements(state: OrchestrationState) -> dict[str, Any]:
        answer = interrupt(_requirements_prompt(state["requirements_result"]))
        result = requirements_adapter.resume(
            app_id=state["app_id"],
            thread_id=state["requirements_thread_id"],
            answer=answer,
        )
        return {"requirements_result": result, "status": result.get("status", "unknown")}

    def start_design(state: OrchestrationState) -> dict[str, Any]:
        result = design_adapter.start(app_id=state["app_id"])
        return {
            "design_result": result,
            "current_stage": "design",
            "status": result.get("status", "unknown"),
        }

    def resume_design(state: OrchestrationState) -> dict[str, Any]:
        feedback = interrupt(_design_prompt(state["design_result"]))
        if not isinstance(feedback, str):
            raise TypeError("Design feedback must be a string")
        result = design_adapter.resume(app_id=state["app_id"], feedback=feedback)
        return {"design_result": result, "status": result.get("status", "unknown")}

    def finish(_state: OrchestrationState) -> dict[str, Any]:
        return {"current_stage": "completed", "status": "completed"}

    def after_requirements(state: OrchestrationState) -> str:
        if state["requirements_result"].get("status") == REQUIREMENTS_COMPLETE:
            return "start_design"
        return "resume_requirements"

    def after_design(state: OrchestrationState) -> str:
        if state["design_result"].get("status") == DESIGN_COMPLETE:
            return "finish"
        return "resume_design"

    builder.add_node("start_requirements", start_requirements)
    builder.add_node("resume_requirements", resume_requirements)
    builder.add_node("start_design", start_design)
    builder.add_node("resume_design", resume_design)
    builder.add_node("finish", finish)
    builder.add_edge(START, "start_requirements")
    builder.add_conditional_edges("start_requirements", after_requirements)
    builder.add_conditional_edges("resume_requirements", after_requirements)
    builder.add_conditional_edges("start_design", after_design)
    builder.add_conditional_edges("resume_design", after_design)
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=checkpointer or MemorySaver())


graph = build_orchestration_graph()


def _response(result: dict[str, Any], run_id: str) -> FlowResponse:
    interruptions = result.get("__interrupt__") or []
    serializable_result = {
        key: value for key, value in result.items() if key != "__interrupt__"
    }
    if interruptions:
        prompt = interruptions[0].value
        stage = "design" if "stage" in prompt else "requirements"
        return {
            "run_id": run_id,
            "app_id": result.get("app_id", ""),
            "status": "needs_input",
            "stage": stage,
            "prompt": prompt,
            "result": serializable_result,
        }
    return {
        "run_id": run_id,
        "app_id": result.get("app_id", ""),
        "status": result.get("status", "unknown"),
        "stage": result.get("current_stage", "completed"),
        "result": serializable_result,
    }


def start_workflow(
    requirements: list[str],
    *,
    resource_constraints_text: str = "",
    app_id: str | None = None,
    run_id: str | None = None,
) -> FlowResponse:
    """Start a workflow and return at the first requirements/design gate."""
    actual_app_id = app_id or artifact_repository.create_app(
        requirements_text="\n".join(requirements),
        resource_constraints_text=resource_constraints_text,
    )
    actual_run_id = run_id or uuid.uuid4().hex
    initial: OrchestrationState = {
        "run_id": actual_run_id,
        "app_id": actual_app_id,
        "requirements_thread_id": f"orchestration:{actual_run_id}:requirements",
        "requirements": requirements,
        "resource_constraints_text": resource_constraints_text,
        "current_stage": "requirements",
        "status": "running",
    }
    config = {"configurable": {"thread_id": actual_run_id}}
    return _response(dict(graph.invoke(initial, config)), actual_run_id)


def resume_workflow(run_id: str, answer: Any) -> FlowResponse:
    """Resume whichever agent gate the workflow is currently waiting at."""
    config = {"configurable": {"thread_id": run_id}}
    return _response(dict(graph.invoke(Command(resume=answer), config)), run_id)
