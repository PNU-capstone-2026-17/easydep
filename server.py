from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.artifacts_api import router as artifacts_router
from app.db.session import init_db
from app.design.api import router as design_router
from app.implementation.application.jobs import worker as implementation_worker
from app.implementation.interfaces.http import router as implementation_router
from app.requirements.api import router as requirements_router
from app.requirements.classifier import warmup_or_raise
from app.testing.api import router as testing_router
from app.workspace.api import router as workspace_router
from app.workspace.service import workspace_service

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_BUILD_DIR = BASE_DIR / "frontend" / "build"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    _ = OpenAI
    # Docker images and dependency caches are warmed in their own executor.
    # Startup/readiness and user-job workers stay available while it runs.
    if implementation_worker.start_warmup():
        print("[startup] 구현 런타임 워밍업 시작")
    init_db()
    interrupted = workspace_service.startup()
    loaded = warmup_or_raise()
    print(
        "[startup] BERT classifier preloaded: "
        f"{'yes' if loaded else 'skipped or unavailable'}; "
        f"interrupted workspace commands: {interrupted}"
    )
    try:
        yield
    finally:
        workspace_service.shutdown()
        implementation_worker.shutdown()


app = FastAPI(title="EasyDep Agents", lifespan=lifespan)
app.include_router(artifacts_router)
app.include_router(requirements_router)
app.include_router(design_router)
app.include_router(implementation_router)
app.include_router(testing_router)
app.include_router(workspace_router)


@app.get("/api/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/requirements", include_in_schema=False)
@app.get("/design", include_in_schema=False)
@app.get("/implementation", include_in_schema=False)
@app.get("/testing", include_in_schema=False)
def legacy_ui_redirect() -> RedirectResponse:
    return RedirectResponse("/workspace/")


if FRONTEND_BUILD_DIR.is_dir():
    app.mount(
        "/",
        StaticFiles(directory=FRONTEND_BUILD_DIR, html=True),
        name="workbench-ui",
    )
else:

    @app.get("/", include_in_schema=False)
    def frontend_not_built() -> HTMLResponse:
        return HTMLResponse(
            "<h1>EasyDep</h1><p>Run <code>npm run build</code> in "
            "<code>frontend/</code> to build the workbench.</p>",
            status_code=503,
        )
