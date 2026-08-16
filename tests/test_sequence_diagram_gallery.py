from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.artifacts_api import (
    get_stage_image,
    get_sequence_diagram_image,
    list_sequence_diagrams,
    sequence_diagrams_from_state,
    to_web_response,
)
from app.design.api import FeedbackRequest, resume_design_session


APP_ID = "00000000-0000-0000-0000-000000000001"


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


def test_sequence_diagram_image_renders_only_requested_use_case() -> None:
    with (
        patch("app.artifacts_api.require_app", return_value=_state()),
        patch("app.artifacts_api.render_plantuml", return_value=b"<svg />") as render,
    ):
        response = get_sequence_diagram_image(APP_ID, "UC-02", "svg")

    plantuml = render.call_args.args[0]
    assert "@startuml UC_02" in plantuml
    assert "title UC-02 - 주문 조회" in plantuml
    assert "UC-01" not in plantuml
    assert render.call_args.args[1] == "svg"
    assert response.body == b"<svg />"
    assert response.media_type == "image/svg+xml"


def test_sequence_diagram_image_returns_404_for_unknown_use_case() -> None:
    with patch("app.artifacts_api.require_app", return_value=_state()):
        with pytest.raises(HTTPException) as error:
            get_sequence_diagram_image(APP_ID, "UC-99", "png")

    assert error.value.status_code == 404


def test_sequence_diagram_images_are_blocked_while_findings_remain() -> None:
    state = {
        **_state(),
        "sequence_diagram_puml": "@startuml\n@enduml",
        "sequence_diagram_check": {
            "findings": ["invalid receiver method"],
            "stopped": "no_improvement",
        },
    }
    response = to_web_response(state)
    assert response["artifacts"]["sequence_diagram"] == ""
    assert response["validation"]["sequence_diagram"]["findings"]

    with patch("app.artifacts_api.require_app", return_value=state):
        with pytest.raises(HTTPException) as error:
            get_sequence_diagram_image(APP_ID, "UC-01", "png")
    assert error.value.status_code == 409

    with patch("app.artifacts_api.require_app", return_value=state):
        with pytest.raises(HTTPException) as error:
            get_stage_image(APP_ID, "sequence_diagram", "png")
    assert error.value.status_code == 409


def test_design_cannot_advance_past_unresolved_sequence_findings() -> None:
    state = {
        **_state(),
        "sequence_diagram_check": {"findings": ["invalid receiver method"]},
    }
    with (
        patch("app.design.api.require_app_exists"),
        patch("app.design.api.require_active_session"),
        patch(
            "app.design.api.session_status",
            return_value={"active": True, "stage": "sequence_diagram"},
        ),
        patch("app.design.api.require_app", return_value=state),
        patch("app.design.api.design_readiness_report") as readiness,
        patch("app.design.api.resume_design") as resume,
    ):
        readiness.return_value = {
            "status": "NEEDS_INPUT",
            "findings": [{"stage": "sequence_diagram", "finding": "invalid"}],
        }
        with pytest.raises(HTTPException) as error:
            resume_design_session(APP_ID, FeedbackRequest(feedback=""))

    assert error.value.status_code == 409
    resume.assert_not_called()


def test_frontend_renders_sequence_diagrams_as_individual_image_cards() -> None:
    source = (Path(__file__).parents[1] / "frontend" / "design" / "index.html").read_text(
        encoding="utf-8"
    )

    assert "renderSequenceDiagramGallery" in source
    assert "/stages/sequence_diagram/diagrams`" in source
    assert 'card.className = "sequence-diagram-card"' in source
    assert 'image.className = "sequence-diagram-image"' in source
    assert 'stageId === "sequence_diagram"' in source
