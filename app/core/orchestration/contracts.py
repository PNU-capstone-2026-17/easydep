"""Explicit contracts passed between independently owned agents."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

StageName = Literal["requirements", "design", "implementation", "testing", "completed"]


class OrchestrationState(TypedDict, total=False):
    run_id: str
    run_variant: str
    case_id: str
    run_purpose: str
    app_id: str
    requirements_thread_id: str
    requirements: list[str]
    resource_constraints_text: str
    requirements_result: dict[str, Any]
    design_result: dict[str, Any]
    cloud_design_result: dict[str, Any]
    infrastructure_recommendation: dict[str, Any]
    implementation_result: dict[str, Any]
    testing_result: dict[str, Any]
    implementation_authorized: bool
    current_stage: StageName
    status: str
    error: str


class FlowResponse(TypedDict, total=False):
    run_id: str
    app_id: str
    status: str
    stage: StageName
    prompt: Any
    result: dict[str, Any]


REQUIREMENTS_COMPLETE = "completed"
DESIGN_COMPLETE = "completed"
