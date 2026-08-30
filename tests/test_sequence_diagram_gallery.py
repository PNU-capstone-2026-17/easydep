from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.artifact_images import artifact_image_cache, warm_artifact_images
from app.artifacts_api import (
    get_sequence_diagram_image,
    get_stage_image,
    list_sequence_diagrams,
    sequence_diagrams_from_state,
    to_web_response,
)
from app.design.service import resume_design_session, retry_design_session

APP_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def clear_artifact_image_cache():
    """각 테스트가 이전 테스트에서 준비한 이미지의 영향을 받지 않게 한다."""
    artifact_image_cache.clear()
    yield
    artifact_image_cache.clear()


def _diagram(use_case_id: str, use_case_name: str) -> dict:
    return {
        "use_case_id": use_case_id,
        "use_case_name": use_case_name,
        "Participants": [
            {"kind": "actor", "name": "User", "alias": "User"},
            {"kind": "control", "name": "OrderService", "alias": "OrderService"},
        ],
        "Messages": [
            {
                "kind": "sync",
                "from": "User",
                "to": "OrderService",
                "label": "createOrder()",
            }
        ],
    }


def _state() -> dict:
    return {
        "sequence_diagram_model": {
            "Diagrams": [
                _diagram("UC-01", "주문 생성"),
                _diagram("UC-02", "주문 조회"),
            ]
        }
    }


def test_sequence_diagrams_from_state_supports_multiple_and_legacy_models() -> None:
    diagrams = sequence_diagrams_from_state(_state())
    assert [item["use_case_id"] for item in diagrams] == ["UC-01", "UC-02"]

    legacy = _diagram("", "")
    legacy.pop("use_case_id")
    legacy.pop("use_case_name")
    diagrams = sequence_diagrams_from_state({"sequence_diagram_model": legacy})
    assert diagrams[0]["use_case_id"] == "sequence"
    assert diagrams[0]["use_case_name"] == "Sequence Diagram"


def test_sequence_diagram_list_exposes_each_use_case() -> None:
    with patch("app.artifacts_api.require_app", return_value=_state()):
        response = list_sequence_diagrams(APP_ID)

    assert json.loads(response.body) == {
        "diagrams": [
            {"use_case_id": "UC-01", "use_case_name": "주문 생성"},
            {"use_case_id": "UC-02", "use_case_name": "주문 조회"},
        ]
    }


def test_sequence_diagram_image_serves_the_pre_rendered_use_case() -> None:
    def render(puml: str, image_format: str) -> bytes:
        use_case = "UC-02" if "UC-02" in puml else "UC-01"
        return f"{image_format}:{use_case}".encode()

    with patch("app.artifact_images.render_plantuml", side_effect=render) as renderer:
        warm_artifact_images(APP_ID, "sequence_diagram", _state())

    # cache hit에서는 앱 전체 상태를 다시 읽거나 renderer를 다시 호출하면 안 된다.
    with patch(
        "app.artifacts_api.require_app",
        side_effect=AssertionError("cache hit loaded the database"),
    ):
        response = get_sequence_diagram_image(APP_ID, "UC-02", "svg")

    assert any(
        "title UC-02 - 주문 조회" in call.args[0] and call.args[1] == "svg"
        for call in renderer.call_args_list
    )
    assert response.body == b"svg:UC-02"
    assert response.media_type == "image/svg+xml"
    assert response.headers["cache-control"] == "no-store, max-age=0"


def test_sequence_diagram_image_returns_404_for_unknown_use_case() -> None:
    with (
        patch("app.artifacts_api.require_app", return_value=_state()),
        patch("app.artifact_images.render_plantuml", return_value=b"image"),
        pytest.raises(HTTPException) as error,
    ):
        get_sequence_diagram_image(APP_ID, "UC-99", "png")

    assert error.value.status_code == 404


def test_sequence_diagram_draft_remains_visible_while_findings_block_advance() -> None:
    state = {
        **_state(),
        "sequence_diagram_puml": "@startuml\n@enduml",
        "sequence_diagram_check": {
            "findings": ["invalid receiver method"],
            "stopped": "no_improvement",
        },
    }
    response = to_web_response(state)
    assert response["artifacts"]["sequence_diagram"] == "@startuml\n@enduml"
    assert response["artifact_status"]["sequence_diagram"] == "needs_review"
    assert response["validation"]["sequence_diagram"]["findings"]

    with patch("app.artifact_images.render_plantuml", return_value=b"draft") as render:
        warm_artifact_images(APP_ID, "sequence_diagram", state)

    with patch(
        "app.artifacts_api.require_app",
        side_effect=AssertionError("cache hit loaded the database"),
    ):
        image = get_sequence_diagram_image(APP_ID, "UC-01", "png")
    assert image.body == b"draft"
    assert render.called

    with patch(
        "app.artifacts_api.require_app",
        side_effect=AssertionError("cache hit loaded the database"),
    ):
        image = get_stage_image(APP_ID, "sequence_diagram", "png")
    assert image.body == b"draft"


def test_design_refuses_to_advance_past_sequence_findings() -> None:
    state = {
        **_state(),
        "sequence_diagram_puml": "@startuml\n@enduml",
        "sequence_diagram_check": {"findings": ["invalid receiver method"]},
    }
    with (
        patch("app.design.service.artifact_repository.ensure_app_exists"),
        patch("app.design.service.has_active_session", return_value=True),
        patch(
            "app.design.service.session_status",
            return_value={"active": True, "stage": "sequence_diagram"},
        ),
        patch("app.design.service.artifact_repository.load_state", return_value=state),
        patch("app.design.service.design_readiness_report") as readiness,
        patch("app.design.service.resume_design", return_value={}) as resume,
    ):
        readiness.return_value = {
            "status": "NEEDS_INPUT",
            "findings": [{"stage": "sequence_diagram", "finding": "invalid"}],
        }
        with pytest.raises(ValueError, match="Resolve the active design findings"):
            resume_design_session(APP_ID)

    resume.assert_not_called()


def test_retry_at_a_review_gate_restores_the_draft_without_rerunning() -> None:
    state = {
        **_state(),
        "sequence_diagram_puml": "@startuml\n@enduml",
        "sequence_diagram_check": {"findings": ["invalid receiver method"]},
        "artifact_status": {"sequence_diagram": "implemented"},
    }
    with (
        patch("app.design.service.artifact_repository.ensure_app_exists"),
        patch(
            "app.design.service.session_status",
            return_value={
                "active": True,
                "retryable": False,
                "stage": "sequence_diagram",
            },
        ),
        patch("app.design.service.artifact_repository.load_state", return_value=state),
        patch("app.design.service.retry_design") as retry,
    ):
        payload = retry_design_session(APP_ID)

    assert payload["status"] == "need_feedback"
    assert payload["stage"] == "sequence_diagram"
    assert payload["artifact_status"]["sequence_diagram"] == "needs_review"
    retry.assert_not_called()
