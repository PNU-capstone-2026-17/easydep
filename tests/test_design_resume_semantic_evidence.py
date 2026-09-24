from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import app.design.validation as design_validation
from app.design import service as design_service
from app.design.services.class_diagram.public_contract_review import _obligations
from app.design.services.class_diagram.scenario import build_scenario_index
from app.validation import ValidationReport, stable_digest


APP_ID = "00000000-0000-0000-0000-000000000042"


def _scenario() -> dict:
    return {
        "use_cases": [{"id": "UC1", "name": "Submit", "primary_actor_ref": "ACT1"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [{"step_number": 1, "subject_ref": "ACT1", "sentence": "Member submits details."}],
            "extensions": [],
            "public_contract": {
                "identity_obligations": [],
                "required_values": [{
                    "name": "request details", "source": "caller_input",
                    "value_type": "object", "usage": "control", "requirement_ids": ["R1"],
                }],
            },
        }],
        "relationships": {"includes": [], "extends": []},
    }


def _model() -> dict:
    return {"Classes": [{"className": "SubmitControl", "stereotype": "Control"}]}


def _passing_evidence(model: dict, scenario: dict) -> dict:
    index = build_scenario_index(scenario)
    return {
        "version": "class-public-contract-review/v3",
        "modelDigest": stable_digest(model),
        "contractDigest": stable_digest([_obligations(item) for item in index.use_cases]),
        "status": "pass",
        "verdicts": [{"useCaseId": "UC1", "status": "pass", "finding": ""}],
    }


@pytest.mark.parametrize("stale", [False, True])
def test_resume_class_gate_uses_checkpoint_evidence_but_keeps_digest_validation(
    monkeypatch, stale: bool
) -> None:
    model = _model()
    scenario = _scenario()
    evidence = _passing_evidence(model, scenario)
    if stale:
        evidence["modelDigest"] = "old-model-digest"
    state = {"usecase_spec": scenario, "extracted_bce_classes": model}
    monkeypatch.setattr(
        design_validation, "class_diagram_validation_report", lambda *_args: ValidationReport(status="clean")
    )

    with (
        patch("app.design.service.artifact_repository.ensure_app_exists"),
        patch("app.design.service.has_active_session", return_value=True),
        patch(
            "app.design.service.session_status",
            return_value={"active": True, "stage": "class_diagram"},
        ),
        patch("app.design.service.artifact_repository.load_state", return_value=state),
        patch(
            "app.design.service.design_graph.get_state",
            return_value=SimpleNamespace(values={"class_diagram_check": {"semanticEvidence": evidence}}),
        ),
        patch("app.design.service.resume_design", return_value={"status": "advanced"}) as resume,
    ):
        if stale:
            with pytest.raises(ValueError, match="Resolve the active design findings"):
                design_service.resume_design_session(APP_ID)
        else:
            assert design_service.resume_design_session(APP_ID) == {"status": "advanced"}

    if stale:
        resume.assert_not_called()
    else:
        resume.assert_called_once_with(APP_ID, "")
