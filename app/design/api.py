"""시스템 설계 에이전트의 서빙 레이어 — **설계 고유의 것만** 있다.

앱 컨테이너 발급과 산출물 저장소는 여기 없다. 그건 세 에이전트가 함께 쓰는 것이라
`app/artifacts_api.py`로 나갔다. 이 파일이 아는 것은 설계 파이프라인 하나다.

**산출물을 만드는 길은 하나다.**

    /design/start    파이프라인을 처음부터. 첫 게이트에서 멈춘다.
    /design/resume   멈춘 게이트에 답한다. 빈 피드백이면 다음 스테이지.
    /design/rewind   특정 스테이지로 되감아 거기서부터 다시 만든다.
    /design/session  지금 어디서 멈췄나 (새로고침한 화면이 복원할 때)
    /design/trace    추적표 — 무엇이 무엇에서 나왔고, 고치면 무엇이 영향받나

예전에는 `/stages/{stage}/generate`·`/feedback`으로 스테이지 하나만 따로 돌리는 두 번째
길이 있었다. 지웠다 — 그 길은 **산출물을 낡게 만들 수 있었다.** API 명세만 다시 만들어도
그것을 재료로 만들어진 배포 다이어그램은 옛 API 기준으로 남았다. 파이프라인은 앞으로만
흐르므로 그 상태가 구조적으로 불가능하다. "그것만 다시"가 필요하면 `/design/rewind`가
그 스테이지로 되감고, 이어서 진행하면 뒤쪽도 새 재료로 다시 만들어진다.

app_id는 요구사항 분석이 발급하고, 설계는 `localStorage["easydep_app_id"]`로 이어받아
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
    """An all-or-nothing group of explicitly targeted design revisions."""

    revisions: list[ReviseRequest] = Field(min_length=1, max_length=20)

    @field_validator("revisions")
    @classmethod
    def targets_are_unique(cls, revisions: list[ReviseRequest]) -> list[ReviseRequest]:
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
    #: Combined with the deterministic findings before the design agent revises.
    feedback: str = ""


@router.post("/api/apps/{app_id}/design/start")
def start_design_session(app_id: str, request: StageRequest) -> JSONResponse:
    """Run the design pipeline from the first stage, stopping at the first gate.

    The response carries the class diagram and status "need_feedback". Answer it
    with /design/resume — an empty feedback advances to the sequence diagram, a
    non-empty one revises the class diagram and asks again.

    Restarting an app that already has a session would resume mid-pipeline rather
    than begin again (LangGraph continues the thread), so the checkpoint is cleared
    first. The stored artifacts are untouched; only "how far we got" is discarded.
    """
    validate_app_id(app_id)
    state = require_app(app_id)
    # The pipeline's only prerequisite: everything downstream is derived from the
    # use case specification, and the stage order takes care of the rest.
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
    """Answer the gate the pipeline is waiting at.

    Empty feedback advances to the next stage; anything else revises the current
    stage and returns to the same gate.
    """
    validate_app_id(app_id)
    # Only the 404 check is needed — resume_design restores the state it works
    # from out of the checkpoint, not out of the artifact store.
    require_app_exists(app_id)
    require_active_session(app_id)
    if not request.feedback.strip():
        active_stage = session_status(app_id).get("stage")
        if active_stage:
            readiness = design_readiness_report(
                require_app(app_id), stages=[str(active_stage)]
            )
            findings = list(readiness.get("findings") or [])
            # 규칙 findings는 사용자에게 계속 보이지만, 렌더/저장된 산출물이 있으면
            # 다음 설계 단계의 입력으로 사용할 수 있다. 산출물 자체가 없을 때만 멈춘다.
            if findings and not _stage_artifact_exists(require_app(app_id), str(active_stage)):
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
    """Retry a failed node, or restore review when the graph already reached a gate."""
    validate_app_id(app_id)
    require_app_exists(app_id)
    status = session_status(app_id)
    if not status.get("retryable"):
        # A command can fail because the user (or auto mode) tried to advance a
        # draft with findings.  The graph itself is still safely paused at its
        # review gate, so retry is an idempotent restore rather than another LLM
        # run.  This also repairs the workspace command state after a refresh.
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
    """Go back to a stage and remake it — and everything after it.

    This is how "just redo the ERD" is done. It deliberately does not remake only
    that one artifact: the stages after it were derived from it, so leaving them
    alone is how two artifacts end up disagreeing. Rewinding re-runs the stage and
    stops at its gate; advancing from there rebuilds the rest on the new material.
    """
    validate_app_id(app_id)
    require_app_exists(app_id)
    # A finished run is exactly when rewinding matters most ("everything is made,
    # but the API spec is wrong"), so this asks for a run to exist — not for one
    # to still be paused at a gate.
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
    """Change one element, and only what the trace says depends on it.

    This is the ordinary way to fix a finished design. Rewinding regenerates whole
    stages from scratch, which throws away everything the user already approved;
    here the untargeted elements are copied from the original and the model's output
    for them is never read. See app/design/cascade.py.
    """
    return revise_design_elements(
        app_id, BatchReviseRequest(revisions=[request])
    )


@router.post("/api/apps/{app_id}/design/revise-batch")
def revise_design_elements(app_id: str, request: BatchReviseRequest) -> JSONResponse:
    """Apply several independently-worded targeted changes atomically.

    Each revision is evaluated against the in-memory result of the preceding
    one, but nothing is persisted until every target has completed.  Thus a
    failed UC cannot leave earlier UCs in a partially saved batch.
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
    """Repair a visible mismatch, then let the normal design checker re-run.

    The user may add requirements in ``feedback``.  The deterministic findings
    are always included, so the revision cannot silently address only the prose
    request while leaving the blocking contract mismatch behind.
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
            # A missing Control method or sequence call cannot be fixed by an
            # OpenAPI-only edit. Rebuild the three dependent design stages in
            # order, feeding the same explicit finding and user request into
            # each model revision. The result pauses at API again, where its
            # normal checker decides whether hand-off is now safe.
            reset_design(app_id)
            start_design(app_id, state)
            result = resume_design(app_id, directive)  # Class/BCE repair.
            if _stage_has_findings(result, "class_diagram"):
                return JSONResponse(content=result)
            result = resume_design(app_id, "")  # Generate sequence from BCE.
            if _stage_has_findings(result, "sequence_diagram"):
                return JSONResponse(content=result)
            result = resume_design(app_id, directive)  # Sequence repair.
            if _stage_has_findings(result, "sequence_diagram"):
                return JSONResponse(content=result)
            result = resume_design(app_id, "")  # Generate API from BCE + sequence.
            if _stage_has_findings(result, "api_spec"):
                return JSONResponse(content=result)
            result = resume_design(app_id, directive)  # API repair + recheck.
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
                # An older client could have advanced despite findings. Rewind
                # only to the invalid stage, then apply the repair directive.
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
    """Where the pipeline is paused, if anywhere.

    The artifact store cannot answer this — it knows what was made, not how far the
    run got. A refreshed screen asks here to tell "start a new run" from "answer the
    gate you left open".
    """
    validate_app_id(app_id)
    require_app_exists(app_id)
    return JSONResponse(content={"app_id": app_id, "session": session_status(app_id)})


@router.get("/api/apps/{app_id}/design/trace")
def get_design_trace(app_id: str, format: str = "json") -> Response:
    """Where each design element came from, and what an upstream change touches.

    Aggregated from the trace fields the models already carry — no LLM call, so
    the matrix cannot disagree with the artifacts it describes. See app/design/rtm.py.

    format=md returns the same thing as a markdown table.
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
    """409 unless a design session is paused at a gate.

    Without this, LangGraph answers a resume for an unknown thread by running the
    pipeline from the top with empty input — producing and storing an empty
    artifact instead of failing.
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
    """409 unless the pipeline has run for this app — finished runs count."""
    if not has_design_run(app_id):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "This app has no design run to rewind. "
                "Start one with POST /design/start.",
            },
        )
