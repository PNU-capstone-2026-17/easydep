from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.artifacts_api import router as artifacts_router
from app.db.session import init_db
from app.design.api import router as design_router
from app.implementation.application.jobs import worker as implementation_worker
from app.implementation.interfaces.http import router as implementation_router
from app.testing.api import router as testing_router
from app.requirements.api import router as requirements_router
from app.requirements.classifier import warmup_or_raise

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"


app = FastAPI(title="EasyDep Agents")
# 앱 컨테이너·산출물 저장소는 세 에이전트가 함께 쓴다 — 어느 에이전트의 것도 아니다.
app.include_router(artifacts_router)
app.include_router(requirements_router)
app.include_router(design_router)
app.include_router(implementation_router)
app.include_router(testing_router)


@app.on_event("startup")
def startup() -> None:
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    _ = OpenAI
    init_db()
    # BERT 가중치를 1회 프리로드해 이후 요청은 캐시를 재사용한다.
    # (enable_bert_verify=False면 건너뛴다.)
    # STEP 1 has no second FR/NFR classifier.  If BERT was requested but cannot
    # load, fail startup instead of serving deceptively healthy, all-FR results.
    loaded = warmup_or_raise()
    print(f"[startup] BERT 분류기 프리로드: {'완료' if loaded else '건너뜀(비활성 또는 로드 실패)'}")


@app.on_event("shutdown")
def shutdown() -> None:
    implementation_worker.shutdown()


@app.get("/api/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """쿠버네티스 liveness/readiness 프로브용 (k8s/base/deployment.yaml)."""
    return {"status": "ok"}


@app.get("/requirements", include_in_schema=False)
def requirements_ui_redirect() -> RedirectResponse:
    return RedirectResponse("/requirements/")


@app.get("/design", include_in_schema=False)
def design_ui_redirect() -> RedirectResponse:
    return RedirectResponse("/design/")


@app.get("/implementation", include_in_schema=False)
def implementation_ui_redirect() -> RedirectResponse:
    return RedirectResponse("/implementation/")


@app.get("/testing", include_in_schema=False)
def testing_ui_redirect() -> RedirectResponse:
    return RedirectResponse("/testing/")


# UI 파일은 frontend/ 아래에 단계별로 둔다. API 라우터가 먼저 등록되어 있으므로
# 마지막의 루트 mount는 화면 요청만 처리한다.
app.mount(
    "/requirements",
    StaticFiles(directory=FRONTEND_DIR / "requirements", html=True),
    name="requirements-ui",
)
app.mount(
    "/design",
    StaticFiles(directory=FRONTEND_DIR / "design", html=True),
    name="design-ui",
)
app.mount(
    "/implementation",
    StaticFiles(directory=FRONTEND_DIR / "implementation", html=True),
    name="implementation-ui",
)
app.mount(
    "/testing",
    StaticFiles(directory=FRONTEND_DIR / "testing", html=True),
    name="testing-ui",
)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="workbench-ui")
