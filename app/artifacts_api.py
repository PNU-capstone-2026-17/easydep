"""앱 컨테이너와 산출물 저장소의 서빙 레이어 — **어느 에이전트의 것도 아니다.**

여기 있는 것은 세 에이전트가 함께 쓰는 것들이다:

  POST /api/apps                              앱 컨테이너 발급
  GET  /api/apps/{app_id}                     저장된 산출물 전부
  POST /api/apps/{app_id}/stages/{s}/content  밖에서 만든 산출물 넣기
  GET  /api/apps/{app_id}/stages/{s}/versions 버전 이력
  GET  /api/apps/{app_id}/stages/{s}/versions/{no}
  GET  /api/apps/{app_id}/stages/{s}/image.{ext}   PlantUML → 이미지

**왜 따로 뺐나.** 원래 이것들은 `app/design/api.py`에 있었다. 그런데 다루는 대상은
설계 산출물 5개가 아니라 **9개 전부**다 — 요구사항 분석이 만든
refined_requirements·usecase_spec·usecase_diagram·resource_spec 도 이 주소로 저장되고
조회되고 그려진다. `app_id` 발급도 마찬가지로 요구사항 화면이 부르는 것이었다.

즉 설계 폴더에 있었지만 설계 것이 아니었다. 그 상태에서는 설계 코드를 고치다가 다른
에이전트의 화면이 조용히 깨질 수 있다(실제로 한 번 그랬다). 소유를 이름으로 드러낸다.

에이전트 고유의 것은 각자 라우터에 있다:
  app/requirements/api.py  요구사항 분석 세션
  app/design/api.py        설계 파이프라인(게이트·되감기·추적표)
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from app.db.models import FORMAT_JSON, ORIGIN_IMPORTED
from app.design.schemas.architecture_state import ArchitectureState
from app.design.services.common.plantuml import render_plantuml
from app.design.services.common.validation import validate_puml_artifact
from app.design.services.sequence_diagram.plantuml import generate_sequence_from_model
from app.repositories import artifact_repository
from app.repositories.artifact_repository import STAGE_ARTIFACTS, AppNotFound

router = APIRouter(tags=["apps"])

#: 저장 가능한 모든 산출물 — 요구사항 4개 + 설계 5개.
STAGES = list(STAGE_ARTIFACTS)

#: 이미지로 렌더할 수 있는 산출물과, 그 PlantUML 이 상태의 어느 칸에 있는지.
#: usecase_diagram(요구사항 것)이 여기 들어 있는 것이 이 모듈이 공용인 이유를 보여준다.
PUML_FIELDS = {
    "usecase_diagram": {
        "code": "usecase_diagram_puml",
        "valid": "usecase_diagram_syntax_valid",
        "errors": "usecase_diagram_syntax_errors",
        "label": "use case diagram",
    },
    "class_diagram": {
        "code": "class_diagram_puml",
        "valid": "class_diagram_syntax_valid",
        "errors": "class_diagram_syntax_errors",
        "label": "class diagram",
    },
    "sequence_diagram": {
        "code": "sequence_diagram_puml",
        "valid": "sequence_diagram_syntax_valid",
        "errors": "sequence_diagram_syntax_errors",
        "label": "sequence diagram",
    },
    "erd": {
        "code": "erd_puml",
        "valid": "erd_syntax_valid",
        "errors": "erd_syntax_errors",
        "label": "ERD",
    },
    "deployment_diagram": {
        "code": "deployment_diagram_puml",
        "valid": "deployment_diagram_syntax_valid",
        "errors": "deployment_diagram_syntax_errors",
        "label": "deployment diagram",
    },
}


class CreateAppRequest(BaseModel):
    requirements_text: str = ""
    resource_constraints_text: str = ""


class ImportRequest(BaseModel):
    content: Any


# ---------------------------------------------------------------------------
# 공용 헬퍼 — 에이전트 라우터들도 이걸 쓴다.
# ---------------------------------------------------------------------------
def validate_app_id(app_id: str) -> None:
    try:
        uuid.UUID(app_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid app id.") from error


def require_app(app_id: str) -> ArchitectureState:
    """앱의 상태 전체를 복원하거나 404. 존재 확인만이면 require_app_exists 를 쓴다."""
    try:
        return artifact_repository.load_state(app_id)
    except AppNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown app id.") from error


def require_app_exists(app_id: str) -> None:
    """앱이 없으면 404. 상태는 만들지 않는다.

    존재 확인만 하는 곳은 이걸 써야 한다 — load_state 는 모든 산출물을 읽고 다이어그램을
    전부 다시 렌더하므로, 결과를 버릴 거면 순전한 낭비다.
    """
    try:
        artifact_repository.ensure_app_exists(app_id)
    except AppNotFound as error:
        raise HTTPException(status_code=404, detail="Unknown app id.") from error


def validate_stage_name(stage: str) -> None:
    if stage not in STAGES:
        raise HTTPException(status_code=404, detail=f"Unknown stage: {stage}")


def sequence_diagrams_from_state(state: ArchitectureState) -> list[dict[str, Any]]:
    """저장 모델에서 프론트엔드가 개별 렌더링할 시퀀스 목록을 가져온다."""
    model = state.get("sequence_diagram_model") or {}
    if not isinstance(model, dict):
        return []
    diagrams = model.get("Diagrams") if isinstance(model, dict) else None
    if isinstance(diagrams, list):
        normalized: list[dict[str, Any]] = []
        for index, diagram in enumerate(diagrams):
            if not isinstance(diagram, dict):
                continue
            use_case_id = str(diagram.get("use_case_id") or f"sequence-{index + 1}")
            normalized.append(
                {
                    **diagram,
                    "use_case_id": use_case_id,
                    "use_case_name": str(
                        diagram.get("use_case_name") or use_case_id
                    ),
                }
            )
        return normalized
    if model.get("Participants") or model.get("Messages"):
        return [
            {
                "use_case_id": "sequence",
                "use_case_name": "Sequence Diagram",
                **model,
            }
        ]
    return []


def to_web_response(result: dict[str, Any]) -> dict[str, Any]:
    """상태 → 화면이 읽는 형태 {artifacts, validation, artifact_status}.

    `validation`은 **두 가지 다른 질문**을 함께 싣는다. `valid`/`errors`는 렌더된 산출물이
    문법에 맞는지를 묻고, 이건 렌더러가 보장하므로 거의 언제나 참이다. `findings`는
    **LLM이 낸 모델이 규칙을 지켰는지**를 묻고, 그건 보장된 것이 없다. 화면이 후자를
    전자와 같은 자리에서 읽어야 "문법은 통과했지만 내용이 틀렸다"를 보여줄 수 있다.

    `check_key`가 없는 스테이지는 `findings`가 빈 목록이고 `check_status`가 None이다.
    **그건 "깨끗하다"가 아니라 "검사하지 않았다"이다** — 화면은 둘을 구별해야 한다.
    """
    artifacts: dict[str, Any] = {}
    validation: dict[str, Any] = {}
    artifact_status = dict(result.get("artifact_status", {}))

    for stage, config in STAGE_ARTIFACTS.items():
        empty: Any = {} if config["format"] == FORMAT_JSON else ""
        check = result.get(config.get("check_key") or "") or {}
        findings = list(check.get("findings", []))
        artifact = result.get(config["state_key"], empty)
        # Findings make an artifact a draft, not an absent artifact.  Keep it
        # visible so the user can review what must be repaired; advancement is
        # still blocked by the design-readiness gate.
        artifacts[stage] = artifact
        if findings and artifact:
            artifact_status[stage] = "needs_review"
        validation[stage] = {
            "valid": result.get(config["valid_key"]) if config["valid_key"] else None,
            "errors": (
                result.get(config["errors_key"], []) if config["errors_key"] else []
            ),
            "findings": findings,
            "check_status": check.get("stopped"),
            "repair_iters": check.get("repair_iters", 0),
        }

    return {
        "artifacts": artifacts,
        "validation": validation,
        "artifact_status": artifact_status,
    }


# ---------------------------------------------------------------------------
# 앱 컨테이너
# ---------------------------------------------------------------------------
@router.post("/api/apps")
def create_app(request: CreateAppRequest) -> JSONResponse:
    """app_id 를 발급한다. 이후 모든 요청은 이 id 하나로 동작한다.

    사용자가 쓴 원문을 앱 행에 남긴다 — 구체화된 요구사항으로는 되돌릴 수 없고,
    설계 에이전트가 재생성할 때 필요하다.
    """
    app_id = artifact_repository.create_app(
        requirements_text=request.requirements_text,
        resource_constraints_text=request.resource_constraints_text,
    )
    return JSONResponse(content={"app_id": app_id})


@router.get("/api/apps/{app_id}")
def get_app(app_id: str) -> JSONResponse:
    """이 앱에 저장된 산출물 전부.

    브라우저는 상태를 안 들고 있으므로, 새로고침한 화면이 자기를 복원하는 통로다.
    진행 상황(어느 게이트에서 멈췄나)은 각 에이전트가 자기 주소로 따로 알려준다 —
    그건 저장소가 아는 것이 아니라 실행이 아는 것이다.
    """
    validate_app_id(app_id)
    return JSONResponse(content={"app_id": app_id, **to_web_response(require_app(app_id))})


# ---------------------------------------------------------------------------
# 산출물 저장소
# ---------------------------------------------------------------------------
@router.post("/api/apps/{app_id}/stages/{stage}/content")
def import_stage_content(app_id: str, stage: str, request: ImportRequest) -> JSONResponse:
    """밖에서 만든 산출물을 저장한다.

    구조화 모델을 갖는 산출물(설계 5개)은 **모델 JSON** 을 넣는다 — 다이어그램이나
    OpenAPI 문서는 거기서 결정론적으로 렌더되므로, 렌더된 것을 넣을 자리가 없다.
    """
    validate_app_id(app_id)
    validate_stage_name(stage)
    require_app_exists(app_id)

    config = STAGE_ARTIFACTS[stage]
    source_key = config.get("source_key")
    if source_key:
        state: ArchitectureState = {source_key: request.content}
        if config.get("hydrate"):
            state.update(config["hydrate"](request.content))
        if config.get("derive_state"):
            state.update(config["derive_state"](request.content))
            puml = state.get(config["state_key"], "")
        else:
            puml = config["derive"](request.content)
            state[config["state_key"]] = puml
        validation = validate_puml_artifact(puml)
        state[config["valid_key"]] = validation["syntax_valid"]
        state[config["errors_key"]] = validation["syntax_errors"]
    else:
        state = {config["state_key"]: request.content}
        if config["valid_key"] and stage in PUML_FIELDS:
            validation = validate_puml_artifact(request.content)
            state[config["valid_key"]] = validation["syntax_valid"]
            state[config["errors_key"]] = validation["syntax_errors"]

    version_id = artifact_repository.save_stage(
        app_id, stage, state, origin=ORIGIN_IMPORTED
    )
    if version_id is None:
        raise HTTPException(status_code=400, detail="Content is empty.")

    return JSONResponse(
        content={"app_id": app_id, **to_web_response(require_app(app_id))}
    )


@router.get("/api/apps/{app_id}/stages/{stage}/versions")
def list_stage_versions(app_id: str, stage: str) -> JSONResponse:
    validate_app_id(app_id)
    validate_stage_name(stage)
    require_app_exists(app_id)
    return JSONResponse(
        content={"versions": artifact_repository.list_versions(app_id, stage)}
    )


@router.get("/api/apps/{app_id}/stages/{stage}/versions/{version_no}")
def get_stage_version(app_id: str, stage: str, version_no: int) -> JSONResponse:
    validate_app_id(app_id)
    validate_stage_name(stage)
    require_app_exists(app_id)
    content = artifact_repository.get_version_content(app_id, stage, version_no)
    if content is None:
        raise HTTPException(status_code=404, detail="Version not found.")
    return JSONResponse(content={"version_no": version_no, "content": content})


@router.get("/api/apps/{app_id}/stages/{stage}/image.{extension}")
def get_stage_image(app_id: str, stage: str, extension: str) -> Response:
    """저장된 PlantUML 을 그 자리에서 이미지로 렌더해 돌려준다.

    이미지는 저장하지 않는다 — MySQL 의 산출물 텍스트에서 매번 다시 만들어 흘려보낸다.
    그래서 렌더 결과가 낡거나 디렉터리가 충돌할 자리가 없다.
    """
    validate_app_id(app_id)
    validate_stage_name(stage)
    if extension not in ("png", "svg"):
        raise HTTPException(status_code=404, detail="Unsupported image format.")
    if stage not in PUML_FIELDS:
        raise HTTPException(status_code=404, detail="Stage has no diagram image.")

    state = require_app(app_id)
    puml_text = state.get(PUML_FIELDS[stage]["code"], "")
    if not puml_text:
        raise HTTPException(status_code=404, detail="Artifact has not been generated.")

    image = render_plantuml(puml_text, extension)
    if not image:
        raise HTTPException(status_code=500, detail="Diagram rendering failed.")

    return Response(
        content=image,
        media_type="image/svg+xml" if extension == "svg" else "image/png",
    )


@router.get(
    "/api/apps/{app_id}/stages/deployment_diagram/views/{view}/image.{extension}"
)
def get_deployment_diagram_view_image(
    app_id: str, view: str, extension: str
) -> Response:
    """Render one explicit deployment-diagram semantic view."""
    validate_app_id(app_id)
    if extension not in ("png", "svg"):
        raise HTTPException(status_code=404, detail="Unsupported image format.")
    fields = {
        "runtime": "deployment_diagram_puml",
        "provisioning": "deployment_diagram_provisioning_puml",
    }
    field = fields.get(view)
    if field is None:
        raise HTTPException(status_code=404, detail="Unknown deployment diagram view.")
    puml_text = str(require_app(app_id).get(field) or "")
    if not puml_text:
        raise HTTPException(status_code=404, detail="Artifact has not been generated.")
    image = render_plantuml(puml_text, extension)
    if not image:
        raise HTTPException(status_code=500, detail="Diagram rendering failed.")
    return Response(
        content=image,
        media_type="image/svg+xml" if extension == "svg" else "image/png",
    )


@router.get("/api/apps/{app_id}/stages/sequence_diagram/diagrams")
def list_sequence_diagrams(app_id: str) -> JSONResponse:
    """프론트엔드 갤러리용 유스케이스별 시퀀스 다이어그램 목록."""
    validate_app_id(app_id)
    diagrams = sequence_diagrams_from_state(require_app(app_id))
    return JSONResponse(
        content={
            "diagrams": [
                {
                    "use_case_id": str(diagram.get("use_case_id") or ""),
                    "use_case_name": str(diagram.get("use_case_name") or ""),
                }
                for diagram in diagrams
            ]
        }
    )


@router.get(
    "/api/apps/{app_id}/stages/sequence_diagram/diagrams/{use_case_id}/image.{extension}"
)
def get_sequence_diagram_image(
    app_id: str, use_case_id: str, extension: str
) -> Response:
    """선택한 유스케이스의 시퀀스 다이어그램 하나만 이미지로 렌더링한다."""
    validate_app_id(app_id)
    if extension not in ("png", "svg"):
        raise HTTPException(status_code=404, detail="Unsupported image format.")
    state = require_app(app_id)
    diagram = next(
        (
            item
            for item in sequence_diagrams_from_state(state)
            if str(item.get("use_case_id") or "") == use_case_id
        ),
        None,
    )
    if diagram is None:
        raise HTTPException(status_code=404, detail="Sequence diagram not found.")
    image = render_plantuml(generate_sequence_from_model(diagram), extension)
    if not image:
        raise HTTPException(status_code=500, detail="Diagram rendering failed.")
    return Response(
        content=image,
        media_type="image/svg+xml" if extension == "svg" else "image/png",
    )
