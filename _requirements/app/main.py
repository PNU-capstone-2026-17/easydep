"""FastAPI 서빙 레이어.

요구사항 분석 에이전트 그래프를 HTTP로 노출하고 정적 웹 UI를 제공한다.
그래프 자체(app.graph)는 서빙 방식과 무관하게 재사용된다.
"""
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agent import resume_analysis, start_analysis
from app.classifier import warmup
from app.schemas import AnalyzeRequest, AnalyzeResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    """서버 기동 시 BERT 가중치를 1회 프리로드해 이후 요청은 캐시를 재사용한다.

    (요청마다 로드하지 않도록 여기서 미리 데운다. enable_bert_verify=False면 건너뜀.)
    """
    loaded = warmup()
    print(f"[startup] BERT 분류기 프리로드: {'완료' if loaded else '건너뜀(비활성 또는 로드 실패)'}")
    yield


app = FastAPI(title="Cloud-Native Requirements Analysis Agent", lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"


@app.get("/healthz")
def healthz() -> dict:
    """쿠버네티스 liveness/readiness 프로브용."""
    return {"status": "ok"}


@app.post("/analyze", response_model=AnalyzeResponse)
def analyze_endpoint(req: AnalyzeRequest) -> AnalyzeResponse:
    """요구사항 분석 세션을 시작하거나(구체화 질문에 대한) 답변으로 재개한다.

    - 신규 세션: requirements 를 담아 호출 (thread_id는 서버가 발급).
    - 구체화 답변: answer + 기존 thread_id 로 호출.
    응답이 need_clarification 이면 questions 를 사용자에게 보여주고,
    답변을 다시 /analyze 로 보내면 세션이 이어진다.
    """
    # 답변 재개 경로
    if req.answer is not None:
        if not req.thread_id:
            raise HTTPException(status_code=400, detail="answer에는 thread_id가 필요합니다.")
        payload = resume_analysis(req.answer, req.thread_id)
        return AnalyzeResponse(**payload)

    # 신규 분석 시작 경로
    if not req.requirements:
        raise HTTPException(
            status_code=400,
            detail="requirements(요구사항 문장 배열) 또는 answer+thread_id가 필요합니다.",
        )
    thread_id = req.thread_id or str(uuid.uuid4())
    payload = start_analysis(req.requirements, thread_id, req.feedback_gates)
    return AnalyzeResponse(**payload)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# /static/* 로 정적 자산 서빙 (필요 시 확장용)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
