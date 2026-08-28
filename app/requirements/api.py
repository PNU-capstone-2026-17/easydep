"""요구사항 분석 에이전트의 서빙 레이어.

원래는 자체 FastAPI 앱(app/main.py)이었지만, 통합 저장소에서는 설계 에이전트와
같은 프로세스로 서빙하므로 라우터로만 남기고 앱 생성은 server.py가 맡는다.
그래프 자체(app.requirements.agent)는 서빙 방식과 무관하게 재사용된다.

산출물은 단계가 끝날 때마다 설계 에이전트와 같은 MySQL 저장소에 저장된다.
요청에 app_id가 있을 때만 저장하므로, 저장소 없이 단독으로 돌려보는 것도 그대로 된다.
"""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from app.metrics import langsmith as langsmith_metrics
from app.repositories import artifact_repository
from app.requirements.agent import resume_analysis, start_analysis
from app.requirements.config import settings
from app.requirements.contracts.request import AnalyzeRequest, ResourceAnswer
from app.requirements.runtime import telemetry
from app.requirements.schemas import AnalyzeResponse

# 서버 진입점(server.py)은 이 에이전트의 것이 아니라 로깅 설정을 거기 둘 수 없다.
# 라우터가 로드되는 시점에 한 번 설정한다 — 여러 번 불러도 핸들러가 겹치지 않는다.
telemetry.configure_logging()

router = APIRouter(prefix="/api/requirements", tags=["requirements"])

def persist_analysis(app_id: str, payload: dict) -> list[str]:
    """응답에 실린 산출물 중 달라진 것을 새 버전으로 남기고, 저장한 stage를 돌려준다.

    단계가 끝날 때마다(피드백 게이트 응답 포함) 호출되므로, 4단계까지 가지 않고
    중간에 그만둬도 그때까지의 산출물은 남는다.

    액터·유스케이스와 상세 명세는 같은 usecase_spec 산출물의 순차 버전으로 저장한다.
    2단계 게이트에서는 현재까지 완성된 유스케이스 모델을 보여주고, 3단계에서 상세
    명세가 추가되면 같은 산출물의 새 버전으로 갱신한다. 설계 파이프라인은 요구사항
    분석 전체가 끝난 뒤 시작하므로 중간 버전이 설계 입력으로 소비되지는 않는다.

    STAGE_ARTIFACTS(app/repositories/artifact_repository.py)에 이미 자리가 있어
    스키마 변경은 필요 없다. resource_spec은 **2026-07-28부터 이 에이전트가 만든다**
    (`steps/step_resource.py`) — 계약을 만족한 실행에서만 온다.
    """
    if payload.get("status") not in ("need_feedback", "completed"):
        return []  # clarify 질문 응답에는 아직 산출물이 없다

    stored = artifact_repository.load_state(app_id)
    saved: list[str] = []

    def save(stage: str, state_key: str, content: Any) -> None:
        # 내용이 그대로면 건너뛴다. 피드백 없이 다음 단계로 넘어갈 때마다 같은
        # 산출물이 새 버전으로 쌓이는 것을 막는다(응답은 누적 산출물을 매번 싣는다).
        if not content or stored.get(state_key) == content:
            return
        artifact_repository.save_stage(app_id, stage, {state_key: content})
        saved.append(stage)

    save("refined_requirements", "refined_requirements", payload.get("requirements"))
    save("capability_contract", "capability_contract", payload.get("capability_contract"))
    save("resource_intake", "resource_intake", payload.get("resource_intake"))

    actors = payload.get("actors") or []
    use_cases = payload.get("use_cases") or []
    use_case_specs = payload.get("use_case_specs") or []
    traceability = payload.get("traceability") or {}
    if actors or use_cases or use_case_specs:
        # 이 객체는 유스케이스 분석과 상세 명세의 누적 산출물이다. 먼저 분석 결과를
        # 리뷰하고, 다음 게이트에서는 상세 명세가 더해진 같은 객체를 리뷰한다.
        usecase_artifact = {
            "actors": actors,
            "use_cases": use_cases,
            "use_case_specs": use_case_specs,
        }
        if traceability:
            usecase_artifact["traceability"] = traceability
        save("usecase_spec", "usecase_spec", usecase_artifact)

    save("usecase_diagram", "usecase_diagram_puml", payload.get("diagram"))
    # `RESOURCE_SPEC`. **계약을 만족한 것만 온다** — `build_resource_spec`이 통과하지
    # 못한 초안은 `resource_intake`에만 남기고 이 키를 아예 내지 않는다. 그래서 여기서
    # 다시 검사하지 않는다(같은 판정을 두 곳에 두면 한쪽만 고쳐진다).
    save("resource_spec", "resource_spec", payload.get("resource_spec"))
    return saved


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze_endpoint(req: AnalyzeRequest) -> AnalyzeResponse:
    """요구사항 분석 세션을 시작하거나(구체화 질문에 대한) 답변으로 재개한다.

    - 신규 세션: requirements 를 담아 호출 (thread_id는 서버가 발급).
    - 구체화 답변: answer + 기존 thread_id 로 호출.
    응답이 need_clarification 이면 questions 를 사용자에게 보여주고,
    답변을 다시 이 엔드포인트로 보내면 세션이 이어진다.
    app_id를 함께 보내면 단계가 끝날 때마다 그 앱의 저장소에 기록되고,
    이번 호출에서 저장된 stage 목록이 saved_stages로 돌아온다.
    """
    # 재개 값은 **하나**다. 둘 이상 오면 무엇을 따를지가 모호하므로 거절한다 —
    # 골라서 쓰면 화면이 보낸 것과 서버가 쓴 것이 조용히 갈린다.
    given = [
        name
        for name, value in (
            ("answer", req.answer),
            ("edit", req.edit),
            ("resource_answers", req.resource_answers),
            (
                "deployment_preferences",
                req.deployment_preferences if not req.requirements else None,
            ),
        )
        if value is not None
    ]
    if len(given) > 1:
        raise HTTPException(
            status_code=400,
            detail=f"{' / '.join(given)} 은 함께 보낼 수 없습니다. 하나만 보내세요.",
        )

    # 재개 경로 — 자연어(answer) · 구조화 편집(edit) · 되묻기의 답(resource_answers).
    resume: object | None = req.answer if req.answer is not None else req.edit
    if resume is None and req.resource_answers is not None:
        resume = ResourceAnswer(answers=req.resource_answers)
    if resume is None and req.deployment_preferences is not None and not req.requirements:
        resume = req.deployment_preferences
    with langsmith_metrics.trace_metadata(
        {"app_id": req.app_id} if req.app_id else None
    ):
        if resume is not None:
            if not req.thread_id:
                raise HTTPException(
                    status_code=400,
                    detail="answer/edit/resource_answers 에는 thread_id가 필요합니다.",
                )
            payload = resume_analysis(
                resume, req.thread_id, persist=settings.enable_session_persistence
            )
        else:
            # 신규 분석 시작 경로
            if not req.requirements:
                raise HTTPException(
                    status_code=400,
                    detail="requirements(요구사항 문장 배열) 또는 answer+thread_id가 필요합니다.",
                )
            thread_id = req.thread_id or str(uuid.uuid4())
            payload = start_analysis(
                req.requirements,
                thread_id,
                req.feedback_gates,
                persist=settings.enable_session_persistence,
                constraints_text=req.resource_constraints_text or "",
                cloud_constraints=(
                    req.deployment_preferences.model_dump(mode="json", exclude_unset=True)
                    if req.deployment_preferences is not None
                    else (
                        req.cloud_constraints.model_dump(mode="json")
                        if req.cloud_constraints is not None
                        else None
                    )
                ),
            )

    if req.app_id:
        try:
            payload["saved_stages"] = persist_analysis(req.app_id, payload)
        except artifact_repository.AppNotFound:
            raise HTTPException(status_code=404, detail=f"app_id {req.app_id} 를 찾을 수 없습니다.")

    return AnalyzeResponse(**payload)
