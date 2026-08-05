"""Top-level requirements-to-design LangGraph.

Agent graphs are treated as external, resumable services. The orchestration
graph stores each returned payload before entering its own interrupt node, so
resuming never repeats an already completed LLM call.
"""

from __future__ import annotations

import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.core.orchestration.adapters import (
    CloudDesignAdapter,
    DesignAdapter,
    ImplementationAdapter,
    InfrastructureRecommendationAdapter,
    RequirementsAdapter,
)
from app.core.orchestration.artifacts import persist_run_artifacts
from app.core.orchestration.checkpoint import (
    DEFAULT_CHECKPOINT_PATH,
    SqliteMemorySaver,
)
from app.core.orchestration.contracts import (
    DESIGN_COMPLETE,
    REQUIREMENTS_COMPLETE,
    FlowResponse,
    OrchestrationState,
)


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
    cloud_design: CloudDesignAdapter | None = None,
    infrastructure: InfrastructureRecommendationAdapter | None = None,
    implementation: ImplementationAdapter | None = None,
    checkpointer: Any | None = None,
):
    """Compile the orchestration graph with replaceable agent adapters."""
    requirements_adapter = requirements or RequirementsAdapter()
    design_adapter = design or DesignAdapter()
    cloud_design_adapter = cloud_design or CloudDesignAdapter()
    infrastructure_adapter = infrastructure or InfrastructureRecommendationAdapter()
    implementation_adapter = implementation or ImplementationAdapter()
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
        result = design_adapter.start(
            session_id=f"orchestration:{state['run_id']}:design",
            requirements_result=state["requirements_result"],
        )
        return {
            "design_result": result,
            "current_stage": "design",
            "status": result.get("status", "unknown"),
        }

    def resume_design(state: OrchestrationState) -> dict[str, Any]:
        feedback = interrupt(_design_prompt(state["design_result"]))
        if not isinstance(feedback, str):
            raise TypeError("Design feedback must be a string")
        result = design_adapter.resume(
            session_id=f"orchestration:{state['run_id']}:design",
            feedback=feedback,
        )
        return {"design_result": result, "status": result.get("status", "unknown")}

    def finish(_state: OrchestrationState) -> dict[str, Any]:
        return {"current_stage": "completed", "status": "completed"}

    def halt_implementation(state: OrchestrationState) -> dict[str, Any]:
        return {
            "current_stage": "implementation",
            "status": state["implementation_result"].get("status", "failed"),
        }

    def finalize_cloud_design(state: OrchestrationState) -> dict[str, Any]:
        result = cloud_design_adapter.finalize(
            requirements_result=state["requirements_result"],
            design_result=state["design_result"],
        )
        design_result = dict(state["design_result"])
        design_result["logical_deployment_diagram_puml"] = result.get(
            "logical_deployment_diagram_puml", ""
        )
        design_result["deployment_diagram_puml"] = result.get(
            "deployment_diagram_puml", design_result.get("deployment_diagram_puml", "")
        )
        return {"cloud_design_result": result, "design_result": design_result}

    def recommend_infrastructure(state: OrchestrationState) -> dict[str, Any]:
        result = infrastructure_adapter.recommend(
            requirements_result=state["requirements_result"],
            cloud_design_result=state["cloud_design_result"],
        )
        return {"infrastructure_recommendation": result}

    def enter_implementation(_state: OrchestrationState) -> dict[str, Any]:
        answer = interrupt(
            {
                "stage": "implementation",
                "prompt": "Start provisional infrastructure planning and implementation?",
                "action": "start_implementation",
            }
        )
        approved = answer is True or (
            isinstance(answer, str) and answer.strip().lower() in {"yes", "y", "approve"}
        )
        return {
            "implementation_authorized": approved,
            "implementation_result": (
                {} if approved else {"status": "rejected", "reason": "not authorized"}
            ),
            "current_stage": "implementation",
        }

    def start_implementation(state: OrchestrationState) -> dict[str, Any]:
        result = implementation_adapter.start(
            run_id=state["run_id"],
            app_id=state["app_id"],
            requirements_result=state["requirements_result"],
            design_result=state["design_result"],
            cloud_design_result=state["cloud_design_result"],
            infrastructure_recommendation=state["infrastructure_recommendation"],
        )
        return {
            "implementation_result": result,
            "current_stage": "implementation",
            "status": result.get("status", "unknown"),
        }

    def resume_implementation(state: OrchestrationState) -> dict[str, Any]:
        answer = interrupt(
            {
                "stage": "implementation",
                "prompt": "Approve sending the listed implementation tasks to the LLM?",
                "transmission_request": state["implementation_result"].get(
                    "transmission_request"
                ),
            }
        )
        approved = answer is True or (
            isinstance(answer, str) and answer.strip().lower() in {"yes", "y", "approve"}
        )
        result = implementation_adapter.resume(
            state["implementation_result"], approved=approved
        )
        return {"implementation_result": result, "status": result.get("status", "unknown")}

    def after_requirements(state: OrchestrationState) -> str:
        if state["requirements_result"].get("status") == REQUIREMENTS_COMPLETE:
            return "start_design"
        return "resume_requirements"

    def after_design(state: OrchestrationState) -> str:
        if state["design_result"].get("status") == DESIGN_COMPLETE:
            return "finalize_cloud_design"
        return "resume_design"

    def after_implementation(state: OrchestrationState) -> str:
        status = state["implementation_result"].get("status")
        if status == "completed":
            return "finish"
        if status in {"needs_approval", "failed"}:
            return "resume_implementation"
        return "halt_implementation"

    def after_implementation_boundary(state: OrchestrationState) -> str:
        return (
            "recommend_infrastructure"
            if state.get("implementation_authorized")
            else "halt_implementation"
        )

    def entry_route(state: OrchestrationState) -> str:
        if state.get("cloud_design_result"):
            return "enter_implementation"
        result = state.get("requirements_result") or {}
        return (
            "start_design"
            if result.get("status") == REQUIREMENTS_COMPLETE
            else "start_requirements"
        )

    builder.add_node("start_requirements", start_requirements)
    builder.add_node("resume_requirements", resume_requirements)
    builder.add_node("start_design", start_design)
    builder.add_node("resume_design", resume_design)
    builder.add_node("finalize_cloud_design", finalize_cloud_design)
    builder.add_node("recommend_infrastructure", recommend_infrastructure)
    builder.add_node("enter_implementation", enter_implementation)
    builder.add_node("start_implementation", start_implementation)
    builder.add_node("resume_implementation", resume_implementation)
    builder.add_node("halt_implementation", halt_implementation)
    builder.add_node("finish", finish)
    builder.add_conditional_edges(START, entry_route)
    builder.add_conditional_edges("start_requirements", after_requirements)
    builder.add_conditional_edges("resume_requirements", after_requirements)
    builder.add_conditional_edges("start_design", after_design)
    builder.add_conditional_edges("resume_design", after_design)
    builder.add_edge("finalize_cloud_design", "enter_implementation")
    builder.add_conditional_edges("enter_implementation", after_implementation_boundary)
    builder.add_edge("recommend_infrastructure", "start_implementation")
    builder.add_conditional_edges("start_implementation", after_implementation)
    builder.add_conditional_edges("resume_implementation", after_implementation)
    builder.add_edge("halt_implementation", END)
    builder.add_edge("finish", END)
    return builder.compile(
        checkpointer=checkpointer
        or SqliteMemorySaver(DEFAULT_CHECKPOINT_PATH, "orchestration")
    )


graph = build_orchestration_graph()


def _response(result: dict[str, Any], run_id: str) -> FlowResponse:
    config = {"configurable": {"thread_id": run_id}}
    active = bool(graph.get_state(config).next)
    interruptions = (result.get("__interrupt__") or []) if active else []
    serializable_result = {
        key: value for key, value in result.items() if key != "__interrupt__"
    }
    if interruptions:
        prompt = interruptions[0].value
        stage = prompt.get("stage", "requirements")
        response: FlowResponse = {
            "run_id": run_id,
            "app_id": result.get("app_id", ""),
            "status": "needs_input",
            "stage": stage,
            "prompt": prompt,
            "result": serializable_result,
        }
        persist_run_artifacts(run_id, serializable_result)
        return response
    response = {
        "run_id": run_id,
        "app_id": result.get("app_id", ""),
        "status": result.get("status", "unknown"),
        "stage": result.get("current_stage", "completed"),
        "result": serializable_result,
    }
    persist_run_artifacts(run_id, serializable_result)
    return response


def start_workflow(
    requirements: list[str],
    *,
    resource_constraints_text: str = "",
    app_id: str | None = None,
    run_id: str | None = None,
) -> FlowResponse:
    """Start a workflow and return at the first requirements/design gate."""
    actual_app_id = app_id or uuid.uuid4().hex
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


def start_design_from_cached_requirements(
    requirements_run_id: str,
    *,
    run_id: str | None = None,
) -> FlowResponse:
    """Start a new design run from a completed requirements checkpoint."""
    source_config = {"configurable": {"thread_id": requirements_run_id}}
    source = graph.get_state(source_config).values
    requirements_result = source.get("requirements_result") or {}
    if requirements_result.get("status") != REQUIREMENTS_COMPLETE:
        raise ValueError("The source requirements run is not completed")

    actual_run_id = run_id or uuid.uuid4().hex
    initial: OrchestrationState = {
        "run_id": actual_run_id,
        "app_id": source.get("app_id") or uuid.uuid4().hex,
        "requirements_thread_id": source.get("requirements_thread_id", ""),
        "requirements": source.get("requirements") or [],
        "resource_constraints_text": source.get("resource_constraints_text", ""),
        "requirements_result": requirements_result,
        "current_stage": "design",
        "status": "running",
    }
    config = {"configurable": {"thread_id": actual_run_id}}
    return _response(dict(graph.invoke(initial, config)), actual_run_id)


def start_implementation_from_completed_design(
    design_run_id: str, *, run_id: str | None = None
) -> FlowResponse:
    """Start implementation from a cached, cloud-finalized design run."""
    source_config = {"configurable": {"thread_id": design_run_id}}
    source = graph.get_state(source_config).values
    required = ("requirements_result", "design_result", "cloud_design_result")
    missing = [name for name in required if not source.get(name)]
    if missing:
        raise ValueError("The source design run is incomplete: " + ", ".join(missing))
    actual_run_id = run_id or uuid.uuid4().hex
    initial: OrchestrationState = {
        "run_id": actual_run_id,
        "app_id": source.get("app_id") or uuid.uuid4().hex,
        "requirements_thread_id": source.get("requirements_thread_id", ""),
        "requirements_result": source["requirements_result"],
        "design_result": source["design_result"],
        "cloud_design_result": source["cloud_design_result"],
        "current_stage": "implementation",
        "status": "running",
    }
    config = {"configurable": {"thread_id": actual_run_id}}
    return _response(dict(graph.invoke(initial, config)), actual_run_id)


def complete_design(run_id: str, *, max_gates: int = 10) -> FlowResponse:
    """Approve design gates and stop at the implementation transmission gate."""
    config = {"configurable": {"thread_id": run_id}}
    result: FlowResponse | None = None
    for _ in range(max_gates):
        snapshot = graph.get_state(config)
        if not snapshot.next:
            values = dict(snapshot.values)
            return _response(values, run_id)
        if snapshot.values.get("current_stage") != "design":
            raise ValueError("The workflow is waiting for requirements input")
        result = resume_workflow(run_id, "")
        if result.get("stage") == "implementation" or result.get("status") == "completed":
            return result
    raise RuntimeError(f"Design did not complete within {max_gates} gates")


def complete_implementation(run_id: str, *, max_gates: int = 50) -> FlowResponse:
    """Approve each implementation transmission and run until completion."""
    result: FlowResponse | None = None
    for _ in range(max_gates):
        result = resume_workflow(run_id, True)
        if result.get("status") == "completed":
            return result
        if result.get("stage") != "implementation":
            raise RuntimeError(f"Unexpected workflow stage: {result.get('stage')}")
        implementation = (result.get("result") or {}).get("implementation_result") or {}
        if implementation.get("status") == "failed":
            raise RuntimeError(
                "Implementation failed and remains resumable: "
                + str((implementation.get("workflow") or {}).get("blockingReason"))
            )
        if result.get("status") != "needs_input":
            raise RuntimeError(
                "Implementation stopped with status " + str(result.get("status"))
            )
    raise RuntimeError(f"Implementation did not complete within {max_gates} gates")
