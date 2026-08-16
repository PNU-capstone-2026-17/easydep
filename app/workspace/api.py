from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.artifacts_api import require_app, to_web_response, validate_app_id
from app.core import region_catalog
from app.repositories import artifact_repository
from app.requirements.schemas import DeploymentPreferences

from . import repository
from .service import workspace_service

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


class CreateWorkspaceAppRequest(BaseModel):
    message: str = Field(min_length=1, max_length=30000)
    # Backward-compatible optional fields. The workbench now collects deployment
    # alternatives in the conversation while application analysis is running.
    provider: Literal["aws", "azure", "gcp"] | None = None
    region: str = Field(default="", max_length=100)
    monthly_budget_amount: float | None = Field(default=None, gt=0)
    monthly_budget_currency: str = Field(default="USD", min_length=3, max_length=3)
    resource_constraints_text: str = Field(default="", max_length=12000)

    @field_validator("monthly_budget_currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("monthly_budget_currency must be a three-letter code")
        return value


class WorkspaceCommandRequest(BaseModel):
    action: Literal[
        "message",
        "advance",
        "confirm_change",
        "dismiss_change",
        "start_design",
        "retry_design",
        "start_implementation",
        "approve_implementation",
        "reject_implementation",
        "cancel_implementation",
        "start_testing",
        "apply_deployment_preferences",
    ]
    text: str = Field(default="", max_length=30000)
    context: dict[str, Any] | None = None
    action_id: str | None = None
    job_id: str | None = None
    request_id: str | None = None
    implementation_job_id: str | None = None
    base_package: str = "com.easydep.app"
    allow_assumptions: bool = True
    retry_failed: bool = False
    delegate_repair_approvals: bool = True
    deployment_preferences: dict[str, Any] | None = None


@router.get("/apps")
def list_apps(limit: int = Query(default=50, ge=1, le=100)) -> dict[str, Any]:
    return {"apps": repository.list_workspace_apps(limit)}


@router.get("/cloud-options")
def cloud_options() -> dict[str, Any]:
    providers: dict[str, list[dict[str, Any]]] = {
        name: [] for name in ("aws", "azure", "gcp")
    }
    for item in region_catalog.catalog():
        if item.provider in providers:
            providers[item.provider].append(
                {
                    "code": item.code,
                    "name": item.name,
                    "latitude": item.latitude,
                    "longitude": item.longitude,
                    "zones": list(item.zones),
                }
            )
    return {"regions": providers, "currencies": ["USD", "KRW", "EUR", "JPY"]}


@router.post("/apps", status_code=202)
def create_workspace_app(request: CreateWorkspaceAppRequest) -> dict[str, Any]:
    message = request.message.strip()
    region = request.region.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Enter application requirements.")
    if bool(request.provider) != bool(region):
        raise HTTPException(
            status_code=422,
            detail="Legacy provider and region values must be supplied together.",
        )
    app_id = artifact_repository.create_app(
        requirements_text=message,
        resource_constraints_text=request.resource_constraints_text.strip(),
    )
    try:
        command = workspace_service.submit(
            app_id,
            action="message",
            stage="requirements",
            payload={
                "text": message,
                **({"provider": request.provider, "region": region} if request.provider else {}),
                "monthly_budget_amount": request.monthly_budget_amount,
                "monthly_budget_currency": request.monthly_budget_currency.upper(),
                "resource_constraints_text": request.resource_constraints_text.strip(),
            },
        )
    except Exception as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"app_id": app_id, "command": command}


@router.put("/apps/{app_id}/deployment-preferences")
def save_deployment_preferences(
    app_id: str, request: DeploymentPreferences
) -> dict[str, Any]:
    """Store cloud alternatives without competing with the active analysis command."""
    validate_app_id(app_id)
    try:
        artifact_repository.ensure_app_exists(app_id)
    except artifact_repository.AppNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown app id.") from error
    catalog = {
        (item.provider, item.code): item for item in region_catalog.catalog()
    }
    for target in request.targets:
        region = catalog.get((target.provider, target.region))
        if region is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown {target.provider} region: {target.region}",
            )
        unknown_zones = sorted(set(target.zones) - set(region.zones))
        if unknown_zones:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown zones for {target.provider}/{target.region}: "
                    + ", ".join(unknown_zones)
                ),
            )

    # Defaults support older clients but are not evidence of a user decision.
    selection = request.model_dump(mode="json", exclude_unset=True)
    previous = repository.get_deployment_preferences(app_id)
    stored = repository.save_deployment_preferences(app_id, selection)
    if "resource_constraints_text" in selection:
        artifact_repository.update_inputs(
            app_id, resource_constraints_text=request.resource_constraints_text
        )
    if previous != stored:
        summary = ", ".join(
            f"{target.provider.upper()} {target.region}"
            for target in request.targets
        )
        repository.append_event(
            app_id,
            stage="requirements",
            kind="message",
            actor="user",
            text=f"Deployment alternatives selected: {summary}",
            metadata={"deployment_preferences": stored},
        )
    resume = workspace_service.apply_saved_deployment_preferences(app_id)
    return {"preferences": stored, "resume_command": resume}


@router.get("/apps/{app_id}")
def get_workspace(app_id: str) -> dict[str, Any]:
    validate_app_id(app_id)
    state = require_app(app_id)
    web = to_web_response(state)
    artifacts = {
        name: {
            "available": bool(content),
            "status": web.get("artifact_status", {}).get(name),
            "validation": web.get("validation", {}).get(name),
        }
        for name, content in web.get("artifacts", {}).items()
    }
    return {
        "app_id": app_id,
        "current_stage": repository.get_app_summary(app_id)["current_stage"],
        "command": repository.latest_command(app_id),
        "events": repository.list_events(app_id),
        "artifacts": artifacts,
        "deployment_preferences": repository.get_deployment_preferences(app_id),
    }


@router.post("/apps/{app_id}/commands", status_code=202)
def create_command(app_id: str, request: WorkspaceCommandRequest) -> dict[str, Any]:
    validate_app_id(app_id)
    payload = request.model_dump(mode="json", exclude={"action"}, exclude_none=True)
    if request.action == "message" and not request.text.strip():
        raise HTTPException(status_code=422, detail="Enter a message.")
    try:
        command = workspace_service.submit(app_id, action=request.action, payload=payload)
    except artifact_repository.AppNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown app id.") from error
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"app_id": app_id, "command": command}


@router.get("/apps/{app_id}/events")
async def stream_events(
    app_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    validate_app_id(app_id)
    try:
        artifact_repository.ensure_app_exists(app_id)
    except artifact_repository.AppNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown app id.") from error
    header = request.headers.get("last-event-id")
    cursor = max(after, int(header)) if header and header.isdigit() else after

    async def generate():
        nonlocal cursor
        idle = 0
        while not await request.is_disconnected():
            events = repository.list_events(app_id, after=cursor, limit=100)
            if events:
                idle = 0
                for event in events:
                    cursor = int(event["event_id"])
                    yield (
                        f"id: {cursor}\n"
                        "event: workspace\n"
                        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    )
            else:
                idle += 1
                if idle >= 15:
                    idle = 0
                    yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
