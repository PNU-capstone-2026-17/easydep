from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.artifacts_api import (
    get_sequence_diagram_image,
    get_stage_image,
    list_sequence_diagrams,
    sequence_diagrams_from_state,
    to_web_response,
)
from app.design.api import (
    FeedbackRequest,
    StageRequest,
    resume_design_session,
    retry_design_session,
)

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
    assert response.headers["cache-control"] == "no-store, max-age=0"


def test_sequence_diagram_image_returns_404_for_unknown_use_case() -> None:
    with (
        patch("app.artifacts_api.require_app", return_value=_state()),
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

    with (
        patch("app.artifacts_api.require_app", return_value=state),
        patch("app.artifacts_api.render_plantuml", return_value=b"draft") as render,
    ):
        image = get_sequence_diagram_image(APP_ID, "UC-01", "png")
    assert image.body == b"draft"
    assert render.called

    with (
        patch("app.artifacts_api.require_app", return_value=state),
        patch("app.artifacts_api.render_plantuml", return_value=b"draft"),
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
        patch("app.design.api.require_app_exists"),
        patch("app.design.api.require_active_session"),
        patch(
            "app.design.api.session_status",
            return_value={"active": True, "stage": "sequence_diagram"},
        ),
        patch("app.design.api.require_app", return_value=state),
        patch("app.design.api.design_readiness_report") as readiness,
        patch("app.design.api.resume_design", return_value={}) as resume,
    ):
        readiness.return_value = {
            "status": "NEEDS_INPUT",
            "findings": [{"stage": "sequence_diagram", "finding": "invalid"}],
        }
        with pytest.raises(HTTPException) as raised:
            resume_design_session(APP_ID, FeedbackRequest(feedback=""))

    assert raised.value.status_code == 409
    resume.assert_not_called()


def test_design_does_not_advance_when_findings_have_no_artifact() -> None:
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
        patch("app.design.api.resume_design", return_value={}) as resume,
    ):
        readiness.return_value = {
            "status": "NEEDS_INPUT",
            "findings": [{"stage": "sequence_diagram", "finding": "invalid"}],
        }
        with pytest.raises(HTTPException) as error:
            resume_design_session(APP_ID, FeedbackRequest(feedback=""))

    assert error.value.status_code == 409
    resume.assert_not_called()


def test_retry_at_a_review_gate_restores_the_draft_without_rerunning() -> None:
    state = {
        **_state(),
        "sequence_diagram_puml": "@startuml\n@enduml",
        "sequence_diagram_check": {"findings": ["invalid receiver method"]},
        "artifact_status": {"sequence_diagram": "implemented"},
    }
    with (
        patch("app.design.api.require_app_exists"),
        patch(
            "app.design.api.session_status",
            return_value={
                "active": True,
                "retryable": False,
                "stage": "sequence_diagram",
            },
        ),
        patch("app.design.api.require_app", return_value=state),
        patch("app.design.api.retry_design") as retry,
    ):
        response = retry_design_session(APP_ID, StageRequest())

    payload = json.loads(response.body)
    assert payload["status"] == "need_feedback"
    assert payload["stage"] == "sequence_diagram"
    assert payload["artifact_status"]["sequence_diagram"] == "needs_review"
    retry.assert_not_called()


def test_frontend_renders_sequence_diagrams_as_individual_image_cards() -> None:
    source = (
        Path(__file__).parents[1]
        / "frontend"
        / "src"
        / "lib"
        / "components"
        / "ArtifactPane.svelte"
    ).read_text(encoding="utf-8")

    assert "getSequenceDiagrams(currentAppId)" in source
    assert 'class="sequence-diagram-gallery' in source
    assert 'class="sequence-diagram-card' in source
    assert 'class="sequence-diagram-image' in source
    assert "selected === 'sequence_diagram'" in source
    assert "sequenceImageEpoch" in source
    assert "?revision=${sequenceImageEpoch}" in source
