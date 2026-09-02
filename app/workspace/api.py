"""Workspace 화면에서 사용하는 HTTP API를 제공한다.

Workspace는 사용자의 메시지를 명령으로 등록하고, 백그라운드 파이프라인이 남긴 이벤트와
산출물 상태를 프론트엔드에 전달한다. 이 모듈은 HTTP 요청을 검사하고 적절한 서비스 또는
저장소를 호출하는 얇은 경계다. 요구사항 분석이나 설계 생성 같은 실제 작업은
``workspace_service``가 실행하며, 이 모듈 안에서 직접 LLM을 호출하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from app.artifacts_api import require_app, to_web_response, validate_app_id
from app.cloudkb import region_catalog
from app.design.service import (
    apply_deployment_sizing_session,
    deployment_sizing_session,
)
from app.design.services.common.plantuml import render_plantuml
from app.repositories import artifact_repository
from app.requirements.schemas import DeploymentPreferences

from . import repository
from .live_preview import live_previews
from .service import workspace_service

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


def _class_preview(app_id: str, command_id: str):
    """명령에 속한 클래스 다이어그램의 생성 중 미리보기를 찾는다.

    미리보기는 아직 정식 산출물 버전이 아니므로 메모리에만 있다. 다른 앱의 명령 ID를
    넣어 미리보기를 읽지 못하도록 명령과 앱의 관계도 함께 확인한다.
    """
    validate_app_id(app_id)
    command = repository.get_command(command_id)
    if command is None or str(command.get("app_id") or "") != app_id:
        raise HTTPException(status_code=404, detail="Unknown workspace command.")
    preview = live_previews.get(app_id, command_id, "class_diagram")
    if preview is None:
        raise HTTPException(
            status_code=404, detail="Class diagram preview is not available."
        )
    return preview


@router.get("/apps/{app_id}/commands/{command_id}/previews/class_diagram")
def get_class_diagram_preview(app_id: str, command_id: str) -> dict[str, Any]:
    """클래스 다이어그램 생성 진행률과 현재까지의 PlantUML을 반환한다."""
    preview = _class_preview(app_id, command_id)
    return {
        "command_id": preview.command_id,
        "stage": preview.stage,
        "revision": preview.revision,
        "phase": preview.phase,
        "unit": preview.unit,
        "completed": preview.completed,
        "total": preview.total,
        "puml": preview.puml,
    }


@router.get(
    "/apps/{app_id}/commands/{command_id}/previews/class_diagram/image.svg"
)
def get_class_diagram_preview_image(app_id: str, command_id: str) -> Response:
    """생성 중인 클래스 다이어그램을 SVG 이미지로 반환한다."""
    preview = _class_preview(app_id, command_id)
    # 같은 revision을 반복해서 조회할 때마다 PlantUML 서버를 호출하지 않도록 SVG를
    # 미리보기 저장소에 보관한다. revision이 달라지면 cache_svg가 이전 그림을 덮지 않는다.
    image = preview.image_svg or render_plantuml(preview.puml, "svg")
    if not image:
        raise HTTPException(status_code=500, detail="Diagram rendering failed.")
    if preview.image_svg is None:
        live_previews.cache_svg(
            app_id, command_id, preview.stage, preview.revision, image,
        )
    return Response(
        content=image,
        media_type="image/svg+xml",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


class CreateWorkspaceAppRequest(BaseModel):
    """새 앱을 만들 때 프론트엔드가 보내는 최초 입력."""

    message: str = Field(min_length=1, max_length=30000)
    # provider와 region은 이전 화면에서도 보내던 선택 필드다. 현재 화면은 분석 대화가
    # 진행되는 동안 여러 배포 후보를 모으므로, 새 요청에서는 두 값이 없어도 된다.
    provider: Literal["aws", "azure", "gcp"] | None = None
    region: str = Field(default="", max_length=100)
    monthly_budget_amount: float | None = Field(default=None, gt=0)
    monthly_budget_currency: str = Field(default="USD", min_length=3, max_length=3)
    resource_constraints_text: str = Field(default="", max_length=12000)

    @field_validator("monthly_budget_currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        """통화 코드를 ISO 4217에서 사용하는 세 글자 대문자 형태로 정리한다."""
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("monthly_budget_currency must be a three-letter code")
        return value


class WorkspaceCommandRequest(BaseModel):
    """이미 만들어진 앱에서 다음 작업을 요청하는 명령."""

    # action은 프론트엔드가 임의 문자열을 보내지 못하도록 가능한 값을 고정한다. 나머지
    # 필드는 action별 선택 값이며, 실제 조합 검사는 workspace_service가 담당한다.
    action: Literal[
        "message",
        "advance",
        "delegate_repair",
        "confirm_change",
        "dismiss_change",
        "start_design",
        "retry_requirements",
        "retry_design",
        "start_implementation",
        "retry_implementation",
        "rerun_implementation",
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
    auto_approve_method_proposals: bool = False
    deployment_preferences: dict[str, Any] | None = None


class ComputeSizingSelectionRequest(BaseModel):
    """한 compute unit의 최종 VM 선택이다."""

    computeUnitId: str = Field(min_length=1, max_length=200)
    sku: str = Field(min_length=1, max_length=200)
    replicaCount: int = Field(ge=1, le=100)
    replicationConfirmed: bool = False


class ApplyDeploymentSizingRequest(BaseModel):
    """한 deployment target에 적용할 모든 compute 선택이다."""

    targetId: str = Field(min_length=1, max_length=1000)
    selections: list[ComputeSizingSelectionRequest] = Field(min_length=1, max_length=50)


@router.get("/apps")
def list_apps(limit: int = Query(default=50, ge=1, le=100)) -> dict[str, Any]:
    """최근 생성한 앱을 최신순으로 조회한다."""
    return {"apps": repository.list_workspace_apps(limit)}


@router.get("/cloud-options")
def cloud_options() -> dict[str, Any]:
    """배포 후보 입력 화면에 표시할 CSP 지역과 통화 목록을 반환한다."""
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
    """앱을 만든 뒤 최초 요구사항 분석 명령을 비동기로 등록한다."""
    message = request.message.strip()
    region = request.region.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Enter application requirements.")
    # 이전 클라이언트가 사용하는 단일 provider/region 형식은 둘 중 하나만 있으면 의미를
    # 정할 수 없다. 따라서 둘을 함께 보내거나 둘 다 생략하도록 검사한다.
    if bool(request.provider) != bool(region):
        raise HTTPException(
            status_code=422,
            detail="Legacy provider and region values must be supplied together.",
        )
    # 명령보다 앱 행을 먼저 만든다. 이후 명령 실행이 실패해도 사용자가 같은 app_id에서
    # 오류 내용을 확인하고 다시 시도할 수 있다.
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
    """분석 명령을 새로 만들지 않고 사용자가 선택한 배포 후보를 저장한다.

    요구사항 분석 중에도 화면에서 후보를 바꿀 수 있으므로, 활성 명령과 충돌하는 별도
    command를 만들지 않는다. 저장 후 대기 중인 분석이 있으면 서비스가 이어서 진행한다.
    """
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

    # Pydantic 기본값은 이전 클라이언트의 요청도 받을 수 있게 해 주지만, 사용자가 직접
    # 선택했다는 뜻은 아니다. exclude_unset=True로 실제 전송한 필드만 저장한다.
    selection = request.model_dump(mode="json", exclude_unset=True)
    previous = repository.get_deployment_preferences(app_id)
    stored = repository.save_deployment_preferences(app_id, selection)
    if "resource_constraints_text" in selection:
        artifact_repository.update_inputs(
            app_id, resource_constraints_text=request.resource_constraints_text
        )
    # 같은 값을 다시 저장할 때 사용자 메시지를 중복으로 남기지 않는다. 실제 선택이
    # 달라졌을 때만 사람이 읽을 수 있는 요약과 원본 JSON을 이벤트에 함께 기록한다.
    if previous != stored:
        summary = ", ".join(
            (
                f"{target.provider.upper()} {target.region}"
                + (f" [{', '.join(target.zones)}]" if target.zones else "")
            )
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


@router.get("/apps/{app_id}/deployment-sizing")
def get_deployment_sizing(app_id: str, target: str = Query(min_length=1)) -> dict[str, Any]:
    """저장된 target의 VM 후보와 compute-only 예상 비용을 반환한다."""

    validate_app_id(app_id)
    try:
        return deployment_sizing_session(app_id, target)
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.put("/apps/{app_id}/deployment-sizing")
def apply_deployment_sizing(
    app_id: str, request: ApplyDeploymentSizingRequest
) -> dict[str, Any]:
    """검증된 VM 선택을 ResourcePlan과 저장된 배포 산출물에 반영한다."""

    validate_app_id(app_id)
    try:
        return apply_deployment_sizing_session(
            app_id,
            request.targetId,
            [selection.model_dump() for selection in request.selections],
        )
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/apps/{app_id}")
def get_workspace(app_id: str) -> dict[str, Any]:
    """Workspace 화면을 다시 그리는 데 필요한 현재 상태를 한 번에 반환한다."""
    validate_app_id(app_id)
    state = require_app(app_id)
    # 구현 작업은 별도 프로세스에서 끝날 수 있다. 화면 snapshot을 만들기 전에 DB의 실제
    # 작업 상태와 Workspace 명령 상태를 맞춰, 완료됐는데 계속 실행 중으로 보이지 않게 한다.
    workspace_service.reconcile_implementation_command(app_id)
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
        "command": workspace_service.present_command(
            app_id, repository.latest_command(app_id)
        ),
        "events": repository.list_events(app_id, include_llm_timings=False),
        "artifacts": artifacts,
        "deployment_preferences": repository.get_deployment_preferences(app_id),
    }


@router.get("/apps/{app_id}/events/{event_id}/llm-timings")
def get_event_llm_timings(
    app_id: str,
    event_id: int,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """큰 설계 LLM 원문 기록을 Workspace에서 펼친 page만 반환한다."""
    validate_app_id(app_id)
    try:
        return repository.get_event_llm_timings(
            app_id, event_id, offset=offset, limit=limit
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Unknown workspace event.") from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/apps/{app_id}/commands", status_code=202)
def create_command(app_id: str, request: WorkspaceCommandRequest) -> dict[str, Any]:
    """사용자 메시지나 다음 단계 진행 요청을 Workspace 명령으로 등록한다."""
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
    """새 Workspace 이벤트를 SSE(Server-Sent Events) 스트림으로 전달한다.

    브라우저가 연결을 다시 맺으면 ``Last-Event-ID`` 헤더나 ``after`` query parameter를
    사용해 마지막으로 받은 이벤트 다음부터 전송한다. 이 방식으로 네트워크가 잠시 끊겨도
    이미 표시한 메시지는 중복하지 않고, 그동안 생긴 메시지도 빠뜨리지 않는다.
    """
    validate_app_id(app_id)
    try:
        artifact_repository.ensure_app_exists(app_id)
    except artifact_repository.AppNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown app id.") from error
    header = request.headers.get("last-event-id")
    # 헤더와 query parameter가 모두 있으면 더 최근 위치에서 시작한다. 숫자가 아닌 헤더는
    # 잘못된 재연결 정보이므로 무시하고 검증된 after 값을 사용한다.
    cursor = max(after, int(header)) if header and header.isdigit() else after

    async def generate():
        """DB를 짧은 간격으로 확인해 SSE 형식의 문자열을 차례로 내보낸다."""
        nonlocal cursor
        idle = 0
        while not await request.is_disconnected():
            events = repository.list_events(
                app_id, after=cursor, limit=100, include_llm_timings=False
            )
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
                    # 이벤트가 없어도 주기적으로 주석 행을 보내면 프록시가 유휴 연결을
                    # 끊는 일을 줄일 수 있다. SSE 클라이언트는 이 행을 이벤트로 표시하지 않는다.
                    yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        # Nginx 같은 프록시가 응답을 모아서 한꺼번에 보내면 실시간 화면이 늦어진다.
        # buffering을 끄고 브라우저 cache도 금지해 이벤트를 생기는 즉시 전달한다.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
