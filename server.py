from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.db.session import init_db
from app.design.api import router as design_router
from app.requirements.api import STATIC_DIR as REQUIREMENTS_UI_DIR
from app.artifacts_api import router as artifacts_router
from app.requirements.api import router as requirements_router
from app.requirements.classifier import warmup
from app.implementation.interfaces.http import router as implementation_router
from app.implementation.application.jobs import worker as implementation_worker


BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"


app = FastAPI(title="EasyDep Agents")
# 앱 컨테이너·산출물 저장소는 세 에이전트가 함께 쓴다 — 어느 에이전트의 것도 아니다.
app.include_router(artifacts_router)
app.include_router(requirements_router)
app.include_router(design_router)
app.include_router(implementation_router)


@app.on_event("startup")
def startup() -> None:
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    _ = OpenAI
    init_db()
    # BERT 가중치를 1회 프리로드해 이후 요청은 캐시를 재사용한다.
    # (enable_bert_verify=False면 건너뛴다.)
    loaded = warmup()
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


@app.get("/design", include_in_schema=False)
def design_ui_redirect() -> RedirectResponse:
    """마운트는 "/design/"에만 걸린다.

    보통은 Starlette이 슬래시를 붙여 리다이렉트해 주지만, 아래 "/" catch-all이
    "/design"을 먼저 받아가 404로 끝난다. 주소창에 "/design"을 친 경우를 위해 직접 넘긴다.
    """
    return RedirectResponse("/design/")


# 워크플로우 순서대로 붙인다. 첫 화면 "/"는 요구사항 분석이고, 설계는 그 다음인
# "/design". "/"는 나머지 전부를 받아가는 catch-all이라 반드시 마지막에 마운트한다.
app.mount("/design", StaticFiles(directory=FRONTEND_DIR, html=True), name="design-ui")
app.mount(
    "/",
    StaticFiles(directory=REQUIREMENTS_UI_DIR, html=True),
    name="requirements-ui",
)
