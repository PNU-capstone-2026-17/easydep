"""시스템 설계 파이프라인을 제어하는 HTTP API다.

앱 ID 발급과 공통 산출물 조회는 요구사항·설계·구현 단계가 함께 사용하므로
``app/artifacts_api.py``가 담당한다. 이 모듈은 설계 실행과 수정 요청만 처리한다.

설계 산출물을 생성하는 API 흐름은 다음과 같다.

    /design/start    파이프라인을 처음부터. 첫 게이트에서 멈춘다.
    /design/resume   멈춘 게이트에 답한다. 빈 피드백이면 다음 스테이지.
    /design/rewind   특정 스테이지로 되감아 거기서부터 다시 만든다.
    /design/session  지금 어디서 멈췄나 (새로고침한 화면이 복원할 때)
    /design/trace    추적표 — 무엇이 무엇에서 나왔고, 고치면 무엇이 영향받나

이전의 ``/stages/{stage}/generate``와 ``/feedback`` API는 stage 하나만 따로 실행했다.
이 방식에서는 API 명세만 다시 만들었을 때 그 API를 입력으로 사용한 배포 다이어그램이
예전 내용으로 남을 수 있었다. 현재 API는 파이프라인 순서를 유지한다. 특정 stage부터
다시 만들려면 ``/design/rewind``로 돌아간 뒤 진행하여 뒤쪽 산출물도 새 입력으로 갱신한다.

``app_id``는 요구사항 분석이 발급하고, 설계 화면은 ``localStorage["easydep_app_id"]``로 이어받아
쓰기만 한다.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from app.artifacts_api import (
    require_app,
    require_app_exists,
    to_web_response,
    validate_app_id,
)
from app.design.cascade import UnknownTarget, persist_cascade, revise_and_cascade
from app.design.graphs.design_graph import (
    StageNotReached,
    has_active_session,
    has_design_run,
    reset_design,
    resume_design,
    retry_design,
    rewind_design,
    session_status,
    start_design,
    sync_design_state,
)
from app.design.graphs.subgraphs import DESIGN_STAGES
from app.design.rtm import build_design_rtm, render_design_rtm_md
from app.design.validation import design_readiness_report
from app.repositories.artifact_repository import STAGE_ARTIFACTS

router = APIRouter(tags=["design"])


def _stage_has_findings(payload: dict, stage: str) -> bool:
    validation = payload.get("validation") if isinstance(payload, dict) else None
    item = validation.get(stage) if isinstance(validation, dict) else None
    return bool(item.get("findings")) if isinstance(item, dict) else False


def _stage_artifact_exists(state: dict, stage: str) -> bool:
    """검증 결과와 별개로 다음 단계가 소비할 산출물이 실제 존재하는지 확인한다."""
    config = STAGE_ARTIFACTS.get(stage)
    if not config:
        return False
    artifact = state.get(config["state_key"])
    if isinstance(artifact, str):
        return bool(artifact.strip())
    return bool(artifact)


class StageRequest(BaseModel):
    pass


class FeedbackRequest(StageRequest):
    feedback: str = ""


class RewindRequest(BaseModel):
    stage: str


class ReviseRequest(BaseModel):
    #: "{stage}:{element}" — 추적표의 change_plan 이 주는 ref 그대로.
    target: str
    feedback: str = ""


class BatchReviseRequest(BaseModel):
    """대상을 명시한 여러 설계 수정을 모두 성공할 때만 적용하는 요청."""

    revisions: list[ReviseRequest] = Field(min_length=1, max_length=20)

    @field_validator("revisions")
    @classmethod
    def targets_are_unique(cls, revisions: list[ReviseRequest]) -> list[ReviseRequest]:
        """각 수정에 고유한 대상과 비어 있지 않은 피드백이 있는지 확인한다."""
        targets = [revision.target.strip() for revision in revisions]
        if any(not target for target in targets):
            raise ValueError("Every targeted revision needs an element reference.")
        if len(targets) != len(set(targets)):
            raise ValueError("A target can appear only once in a revision batch.")
        if any(not revision.feedback.strip() for revision in revisions):
            raise ValueError("Every targeted revision needs feedback text.")
        return revisions


class ResolveIssuesRequest(BaseModel):
    stage: str
    #: 설계 agent가 수정할 때 검사기가 찾은 문제와 함께 전달할 사용자 요구사항.
    feedback: str = ""


@router.post("/api/apps/{app_id}/design/start")
def start_design_session(app_id: str, request: StageRequest) -> JSONResponse:
    """설계 파이프라인을 첫 stage부터 실행하고 첫 검토 지점에서 멈춘다.

    응답에는 클래스 다이어그램과 ``need_feedback`` 상태가 들어 있다. ``/design/resume``에
    빈 feedback을 보내면 시퀀스 다이어그램으로 진행하고, 내용을 보내면 현재 클래스
    다이어그램을 수정한 뒤 같은 검토 지점에서 다시 멈춘다.

    기존 LangGraph thread를 그대로 실행하면 중간 지점에서 재개되므로, 새로 시작할 때는
    checkpoint를 먼저 지운다. DB에 저장된 산출물은 삭제하지 않고 실행 위치만 초기화한다.
    """
    validate_app_id(app_id)
    state = require_app(app_id)
    # 모든 설계 산출물은 use case specification을 출발점으로 순서대로 만들어진다. 나머지
    # 선행 조건은 graph가 stage 순서로 보장하므로 시작 API에서는 이 항목만 확인한다.
    if not state.get("usecase_spec"):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "The use case specification must exist first. Run "
                "POST /api/requirements/analyze with this app_id.",
                "missing": ["usecase_spec"],
            },
        )

    reset_design(app_id)
    try:
        return JSONResponse(content=start_design(app_id, state))
    except Exception as error:
        raise HTTPException(
            status_code=502, detail=f"Design pipeline failed: {error}"
        ) from error


@router.post("/api/apps/{app_id}/design/resume")
def resume_design_session(app_id: str, request: FeedbackRequest) -> JSONResponse:
    """파이프라인이 기다리는 검토 지점에 사용자의 선택을 전달한다.

    feedback이 비어 있으면 다음 stage로 진행한다. 내용이 있으면 현재 stage를 수정하고
    결과를 다시 검토할 수 있도록 같은 지점에서 멈춘다.
    """
    validate_app_id(app_id)
    # resume_design은 DB 산출물이 아니라 checkpoint에서 실행 상태를 복원한다. 여기서는
    # 앱이 존재하는지만 확인하고 전체 산출물을 다시 읽지 않는다.
    require_app_exists(app_id)
    require_active_session(app_id)
    if not request.feedback.strip():
        active_stage = session_status(app_id).get("stage")
        if active_stage:
            readiness = design_readiness_report(
                require_app(app_id), stages=[str(active_stage)]
            )
            findings = list(readiness.get("findings") or [])
            if findings:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Resolve the active design findings before advancing.",
                        "stage": active_stage,
                        "findings": findings,
                    },
                )
    try:
        return JSONResponse(content=resume_design(app_id, request.feedback))
    except Exception as error:
        raise HTTPException(
            status_code=502, detail=f"Design pipeline failed: {error}"
        ) from error


@router.post("/api/apps/{app_id}/design/retry")
def retry_design_session(app_id: str, request: StageRequest) -> JSONResponse:
    """실패한 node를 재시도하거나, 이미 검토 지점이면 현재 검토 화면을 복원한다."""
    validate_app_id(app_id)
    require_app_exists(app_id)
    status = session_status(app_id)
    if not status.get("retryable"):
        # 사용자가 finding이 남은 초안을 진행하려 하면 Workspace 명령은 실패할 수 있지만,
        # graph 자체는 검토 지점에 정상적으로 멈춰 있다. 이때 retry는 LLM을 다시 호출하지
        # 않고 현재 결과를 돌려주며, 새로고침 후 Workspace 명령 상태도 다시 맞출 수 있다.
        if status.get("active") and status.get("stage"):
            state = require_app(app_id)
            return JSONResponse(
                content={
                    "app_id": app_id,
                    **to_web_response(state),
                    "status": "need_feedback",
                    "stage": status["stage"],
                }
            )
        raise HTTPException(
            status_code=409,
            detail={
                "message": "No failed design stage is available to retry.",
                "session": status,
            },
        )
    try:
        return JSONResponse(content=retry_design(app_id))
    except Exception as error:
        raise HTTPException(
            status_code=502, detail=f"Design pipeline failed: {error}"
        ) from error


@router.post("/api/apps/{app_id}/design/rewind")
def rewind_design_session(app_id: str, request: RewindRequest) -> JSONResponse:
    """지정한 stage로 돌아가 해당 산출물과 그 뒤의 산출물을 다시 만든다.

    예를 들어 ERD를 다시 만들면 그 결과를 사용한 뒤쪽 stage도 예전 내용일 수 있다.
    ``rewind``는 한 산출물만 바꿔 서로 내용이 달라지는 상태를 만들지 않는다. 요청한
    stage를 재실행하고 검토 지점에서 멈추며, 이후 진행하면 뒤쪽 산출물도 새 입력으로 만든다.
    """
    validate_app_id(app_id)
    require_app_exists(app_id)
    # 완료된 설계도 수정할 수 있어야 하므로 활성 session이 아니라 실행 이력이 있는지
    # 확인한다. 모든 산출물을 만든 뒤 API만 잘못된 경우에도 rewind를 사용할 수 있다.
    require_design_run(app_id)

    if request.stage not in DESIGN_STAGES:
        raise HTTPException(
            status_code=404, detail=f"Unknown design stage: {request.stage}"
        )
    if request.stage == DESIGN_STAGES[0]:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Rewinding to the first stage is what /design/start does.",
                "stage": request.stage,
            },
        )

    try:
        return JSONResponse(content=rewind_design(app_id, request.stage))
    except StageNotReached as error:
        raise HTTPException(
            status_code=409,
            detail={"message": str(error), "stage": request.stage},
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=502, detail=f"Design pipeline failed: {error}"
        ) from error


@router.post("/api/apps/{app_id}/design/revise")
def revise_design_element(app_id: str, request: ReviseRequest) -> JSONResponse:
    """설계 요소 하나와 추적 관계상 그 요소에 의존하는 부분만 수정한다.

    완료된 설계의 작은 부분을 고칠 때 사용한다. rewind는 stage 전체를 다시 생성하지만,
    이 API는 대상이 아닌 요소를 원본에서 그대로 복사한다. 자세한 수정 범위 계산은
    ``app/design/cascade.py``에 있다.
    """
    return revise_design_elements(
        app_id, BatchReviseRequest(revisions=[request])
    )


@router.post("/api/apps/{app_id}/design/revise-batch")
def revise_design_elements(app_id: str, request: BatchReviseRequest) -> JSONResponse:
    """서로 다른 피드백을 가진 여러 대상 수정을 한 번에 적용한다.

    각 수정은 바로 앞 수정의 메모리 결과를 입력으로 사용한다. 모든 대상이 성공하기 전에는
    DB에 저장하지 않으므로, 중간 use case 수정이 실패해도 앞부분만 저장되지 않는다.
    """
    validate_app_id(app_id)
    working = require_app(app_id)
    changed: list[str] = []
    touched: dict[str, set[str]] = {}
    related: dict[str, list[str]] = {}

    try:
        for revision in request.revisions:
            result = revise_and_cascade(working, revision.target, revision.feedback)
            working = result["state"]
            for stage in result["changed"]:
                if stage not in changed:
                    changed.append(stage)
            for stage, elements in result["touched"].items():
                touched.setdefault(stage, set()).update(elements)
            related[revision.target] = result.get("related", [])
    except UnknownTarget as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=502, detail=f"Revision failed; no batch changes were saved: {error}"
        ) from error

    combined = {
        "state": working,
        "changed": changed,
        "touched": {stage: sorted(elements) for stage, elements in touched.items()},
    }
    persist_cascade(app_id, combined)
    # 파이프라인 밖에서 고쳤으므로 체크포인트도 맞춰둔다 — 안 그러면 재개할 때
    # 고치기 전 상태로 돌아간다.
    sync_design_state(app_id, working)

    return JSONResponse(
        content={
            "app_id": app_id,
            **to_web_response(working),
            "changed": changed,
            "touched": combined["touched"],
            "related": related,
        }
    )


@router.post("/api/apps/{app_id}/design/resolve")
def resolve_design_issues(app_id: str, request: ResolveIssuesRequest) -> JSONResponse:
    """화면에 표시된 설계 문제를 수정하고 기존 설계 검사를 다시 실행한다.

    사용자는 ``feedback``에 추가 요구사항을 적을 수 있다. 검사기가 찾은 finding도 항상
    수정 요청에 포함하므로, 사용자 문장만 반영하고 단계 진행을 막는 계약 오류를 남기는
    일을 줄인다.
    """
    validate_app_id(app_id)
    state = require_app(app_id)
    if request.stage not in DESIGN_STAGES:
        raise HTTPException(status_code=404, detail=f"Unknown design stage: {request.stage}")
    report = design_readiness_report(state, (request.stage,))
    findings = [
        str(item.get("finding", ""))
        for item in report.get("findings", [])
        if isinstance(item, dict) and item.get("finding")
    ]
    if not findings:
        return JSONResponse(
            content={
                "app_id": app_id,
                **to_web_response(state),
                "status": "ready",
                "stage": request.stage,
            }
        )
    directive = (
        "Resolve every deterministic design-contract finding below. Preserve all "
        "unrelated design decisions. Update the structured model so the endpoint, "
        "BCE Control contract, sequence flow, argument sources, and documented HTTP "
        "outcomes agree; do not hide a gap with Object, TODO, or a fabricated value.\n\n"
        + "\n".join(f"- {finding}" for finding in findings)
    )
    if request.feedback.strip():
        directive += "\n\nAdditional user requirements:\n" + request.feedback.strip()

    try:
        needs_upstream_repair = request.stage == "api_spec" and any(
            rule in finding
            for finding in findings
            for rule in (
                "api.control-binding-exists",
                "api.control-call-in-sequence",
                "api.control-outcomes-cover-responses",
            )
        )
        if needs_upstream_repair:
            # Control method나 sequence call이 없으면 OpenAPI만 고쳐서는 계약이 맞지 않는다.
            # 클래스, 시퀀스, API를 순서대로 다시 만들고 각 수정에 같은 finding과 사용자
            # 요구를 전달한다. 마지막에는 API 검토 지점에서 멈춰 일반 검사로 결과를 확인한다.
            reset_design(app_id)
            start_design(app_id, state)
            result = resume_design(app_id, directive)  # 클래스/BCE 수정
            if _stage_has_findings(result, "class_diagram"):
                return JSONResponse(content=result)
            result = resume_design(app_id, "")  # 수정된 BCE에서 시퀀스 생성
            if _stage_has_findings(result, "sequence_diagram"):
                return JSONResponse(content=result)
            result = resume_design(app_id, directive)  # 시퀀스 수정
            if _stage_has_findings(result, "sequence_diagram"):
                return JSONResponse(content=result)
            result = resume_design(app_id, "")  # BCE와 시퀀스에서 API 생성
            if _stage_has_findings(result, "api_spec"):
                return JSONResponse(content=result)
            result = resume_design(app_id, directive)  # API 수정 후 재검사
        else:
            session = session_status(app_id)
            if session["active"]:
                if session.get("stage") != request.stage:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": "Resolve the active design stage first.",
                            "activeStage": session.get("stage"),
                            "requestedStage": request.stage,
                        },
                    )
                result = resume_design(app_id, directive)
            else:
                # 이전 client가 finding이 있는데도 진행했을 수 있다. 문제가 있는 stage까지만
                # 돌아간 뒤 같은 수정 지시를 적용한다.
                if request.stage == DESIGN_STAGES[0]:
                    reset_design(app_id)
                    start_design(app_id, state)
                else:
                    rewind_design(app_id, request.stage)
                result = resume_design(app_id, directive)
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=502, detail=f"Design issue resolution failed: {error}"
        ) from error
    return JSONResponse(content=result)


@router.get("/api/apps/{app_id}/design/session")
def get_design_session(app_id: str) -> JSONResponse:
    """설계 파이프라인이 현재 어느 stage에 멈춰 있는지 반환한다.

    산출물 저장소는 무엇을 만들었는지는 알지만 실행이 어디까지 진행됐는지는 알 수 없다.
    새로고침한 화면은 이 API로 새 실행을 시작할지, 열려 있는 검토 지점에 응답할지 판단한다.
    """
    validate_app_id(app_id)
    require_app_exists(app_id)
    return JSONResponse(content={"app_id": app_id, "session": session_status(app_id)})


@router.get("/api/apps/{app_id}/design/trace")
def get_design_trace(app_id: str, format: str = "json") -> Response:
    """각 설계 요소의 근거와 앞 단계 변경이 영향을 주는 범위를 반환한다.

    모델에 이미 저장된 trace 필드를 모아 만들며 LLM을 호출하지 않는다. 따라서 추적표는
    현재 산출물이 가진 ID와 관계를 그대로 반영한다. 계산 방식은 ``app/design/rtm.py``에 있다.

    ``format=md``를 지정하면 같은 내용을 Markdown 표로 반환한다.
    """
    validate_app_id(app_id)
    matrix = build_design_rtm(require_app(app_id))
    if format == "md":
        return Response(
            content=render_design_rtm_md(matrix, title=app_id),
            media_type="text/markdown; charset=utf-8",
        )
    return JSONResponse(content={"app_id": app_id, **matrix})


def require_active_session(app_id: str) -> None:
    """설계 session이 검토 지점에 멈춰 있지 않으면 HTTP 409를 발생시킨다.

    이 검사가 없으면 LangGraph가 알 수 없는 thread의 resume 요청을 빈 입력의 새 실행으로
    처리할 수 있다. 그러면 오류를 알려야 할 상황에서 빈 산출물이 만들어질 수 있다.
    """
    if not has_active_session(app_id):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "No design session is in progress. "
                "Start one with POST /design/start.",
            },
        )


def require_design_run(app_id: str) -> None:
    """완료 여부와 관계없이 이 앱의 설계 실행 이력이 없으면 HTTP 409를 발생시킨다."""
    if not has_design_run(app_id):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This app has no design run to rewind. "
                "Start one with POST /design/start.",
            },
        )
