from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from app.design.graphs.subgraphs import DESIGN_SUBGRAPHS
from app.requirements.orchestration.subgraphs import build_stage_subgraphs

from .catalog import checkpoint_after, jsonable
from .evidence import validate_state

TaskRecorder = Callable[[str, dict[str, Any], dict[str, Any], float], None]

REQUIREMENT_GROUPS = {
    "requirements": (
        "refine_requirements",
        "analyze_cloud_inputs",
        "structure_constraints",
    ),
    "use_cases": ("model_use_cases",),
    "specifications": ("write_specifications",),
    "usecase_diagram": ("draw_diagram",),
}


def initial_state(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "raw_requirements": list(case["requirements"]),
        "resource_constraints_text": case["resourceConstraintsText"],
        "initial_cloud_constraints": dict(case["initialCloudConstraints"]),
        "_case": {
            "caseId": case["caseId"],
            "inputPath": case["inputPath"],
        },
        "deployment_planning_facts": list(case.get("deploymentPlanningFacts") or []),
    }


def _stream_graph(
    graph: Any,
    state: dict[str, Any],
    *,
    prefix: str,
    record: TaskRecorder,
) -> dict[str, Any]:
    current = dict(state)
    last = time.perf_counter()
    for event in graph.stream(current, stream_mode="updates"):
        now = time.perf_counter()
        for node, raw_delta in event.items():
            delta = dict(raw_delta or {})
            before = jsonable(_without_transient(current))
            current.update(delta)
            record(f"{prefix}.{node}", before, jsonable(_without_transient(delta)), now - last)
            last = now
    return current


def _without_transient(state: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in state.items()
        if key not in {"messages", "gate_route", "stage_origin"}
    }


def _architecture_projection(state: dict[str, Any]) -> dict[str, Any]:
    projected = dict(_without_transient(state))
    projected.update(
        refined_requirements=list(state.get("classified") or []),
        capability_contract=dict(state.get("capability_contract") or {}),
        resource_intake=dict(state.get("resource_intake") or {}),
        resource_spec=dict(state.get("resource_spec") or {}),
        usecase_spec={
            "actors": list(state.get("actors") or []),
            "use_cases": list(state.get("use_cases") or []),
            "use_case_specs": list(state.get("use_case_specs") or []),
            "relationships": dict(state.get("relationships") or {}),
        },
        usecase_diagram_puml=str(state.get("diagram") or ""),
    )
    return projected


def run_transition(
    source_checkpoint: str,
    state: dict[str, Any],
    record: TaskRecorder,
) -> tuple[str, dict[str, Any]]:
    target = checkpoint_after(source_checkpoint)
    current = dict(state)
    if source_checkpoint == "requirements":
        report = validate_state("requirements", current)
        if report["status"] == "failed":
            raise ValueError(
                "Requirements checkpoint is invalid: " + "; ".join(report["errors"])
            )
    if source_checkpoint == "usecase_diagram":
        report = validate_state("usecase_diagram", current)
        if report["status"] == "failed":
            raise ValueError(
                "Design checkpoint is blocked: " + "; ".join(report["errors"])
            )
    if target in REQUIREMENT_GROUPS:
        subgraphs = build_stage_subgraphs()
        for group in REQUIREMENT_GROUPS[target]:
            current = _stream_graph(
                subgraphs[group], current, prefix=group, record=record
            )
        if target == "usecase_diagram":
            current = _architecture_projection(current)
    else:
        current = _stream_graph(
            DESIGN_SUBGRAPHS[target]["generate"],
            current,
            prefix=target,
            record=record,
        )
    return target, jsonable(_without_transient(current))
