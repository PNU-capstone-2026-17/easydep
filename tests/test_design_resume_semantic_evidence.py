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
        "version": "class-public-contract-review/v6",
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


def _stalled_class_readiness(*, requires_user_input: bool = False) -> dict:
    return {
        "status": "NEEDS_INPUT" if requires_user_input else "BLOCKED",
        "findings": [{"stage": "class_diagram", "finding": "class check finding"}],
        "findingRecords": [{
            "stage": "class_diagram",
            "ruleId": "class.required-value-source",
            "finding": "class check finding",
            "message": "A required value source is missing.",
            "location": "UC1:SubmitControl::submit()",
            "requiresUserInput": requires_user_input,
        }],
    }


def test_stalled_class_gate_reenters_only_when_reconcile_changes_saved_model(
    monkeypatch,
) -> None:
    original_model = {"Classes": [{"className": "SubmitControl"}]}
    normalized_model = {
        "Classes": [{"className": "SubmitControl", "operations": []}]
    }
    state = {
        "extracted_bce_classes": original_model,
        "class_diagram_check": {"stopped": "stalled"},
    }
    spec = SimpleNamespace(
        model_key="extracted_bce_classes",
        reconcile=lambda _state: {"extracted_bce_classes": normalized_model},
    )
    started: list[str] = []
    monkeypatch.setitem(design_service.DESIGN_SPECS, "class_diagram", spec)
    monkeypatch.setattr(
        design_service, "session_status",
        lambda _app_id: {"active": True, "stage": "class_diagram"},
    )
    monkeypatch.setattr(
        design_service, "start_design_session",
        lambda app_id: started.append(app_id)
        or {"status": "need_feedback", "stage": "class_diagram"},
    )

    result = design_service._retry_stalled_class_gate_after_reconcile(
        APP_ID, state, _stalled_class_readiness()
    )

    assert result == {"status": "need_feedback", "stage": "class_diagram"}
    assert started == [APP_ID]


@pytest.mark.parametrize(
    ("stopped", "requires_user_input", "reconcile_result"),
    [
        ("stalled", False, {}),
        ("stalled", True, {"extracted_bce_classes": {"Classes": [{"id": "new"}]}}),
        ("clean", False, {"extracted_bce_classes": {"Classes": [{"id": "new"}]}}),
    ],
)
def test_stalled_class_gate_keeps_current_checkpoint_without_eligible_reconcile(
    monkeypatch, stopped: str, requires_user_input: bool, reconcile_result: dict
) -> None:
    state = {
        "extracted_bce_classes": {"Classes": [{"className": "SubmitControl"}]},
        "class_diagram_check": {"stopped": stopped},
    }
    spec = SimpleNamespace(
        model_key="extracted_bce_classes",
        reconcile=lambda _state: reconcile_result,
    )
    monkeypatch.setitem(design_service.DESIGN_SPECS, "class_diagram", spec)
    monkeypatch.setattr(
        design_service, "session_status",
        lambda _app_id: {"active": True, "stage": "class_diagram"},
    )
    started: list[str] = []
    monkeypatch.setattr(design_service, "start_design_session", started.append)

    result = design_service._retry_stalled_class_gate_after_reconcile(
        APP_ID,
        state,
        _stalled_class_readiness(requires_user_input=requires_user_input),
    )

    assert result is None
    assert started == []


def test_stalled_technical_class_gate_reenters_once_before_any_repair_attempt(
    monkeypatch,
) -> None:
    state = {
        "extracted_bce_classes": {"Classes": [{"className": "SubmitControl"}]},
        "class_diagram_check": {
            "stopped": "stalled",
            "repair_iters": 0,
            "repair_history": {"status": "STALLED", "attempts": []},
        },
    }
    spec = SimpleNamespace(
        model_key="extracted_bce_classes",
        reconcile=lambda _state: {},
    )
    monkeypatch.setitem(design_service.DESIGN_SPECS, "class_diagram", spec)
    monkeypatch.setattr(
        design_service, "session_status",
        lambda _app_id: {"active": True, "stage": "class_diagram"},
    )
    started: list[str] = []
    monkeypatch.setattr(
        design_service,
        "start_design_session",
        lambda app_id: started.append(app_id) or {"status": "need_feedback"},
    )

    result = design_service._retry_stalled_class_gate_after_reconcile(
        APP_ID, state, _stalled_class_readiness()
    )

    assert result == {"status": "need_feedback"}
    assert started == [APP_ID]


def test_retry_design_session_reenters_stalled_class_gate_without_reconcile_patch(
    monkeypatch,
) -> None:
    state = {
        "extracted_bce_classes": {"Classes": [{"className": "SubmitControl"}]},
        "class_diagram_check": {
            "stopped": "stalled",
            "repair_iters": 0,
            "repair_history": {"status": "STALLED", "attempts": []},
        },
    }
    monkeypatch.setattr(design_service, "_validate_app_id", lambda _app_id: None)
    monkeypatch.setattr(design_service, "_require_app_exists", lambda _app_id: None)
    monkeypatch.setattr(
        design_service,
        "session_status",
        lambda _app_id: {"active": True, "stage": "class_diagram", "retryable": False},
    )
    monkeypatch.setattr(design_service, "_load_app", lambda _app_id: state)
    monkeypatch.setattr(
        design_service,
        "_readiness_state_at_active_class_gate",
        lambda _app_id, loaded, _stage: loaded,
    )
    monkeypatch.setattr(
        design_service,
        "design_readiness_report",
        lambda _state, stages: _stalled_class_readiness(),
    )
    spec = SimpleNamespace(
        model_key="extracted_bce_classes",
        reconcile=lambda _state: {},
    )
    monkeypatch.setitem(design_service.DESIGN_SPECS, "class_diagram", spec)
    started: list[str] = []
    monkeypatch.setattr(
        design_service,
        "start_design_session",
        lambda app_id: started.append(app_id) or {"status": "need_feedback"},
    )

    result = design_service.retry_design_session(APP_ID)

    assert result == {"status": "need_feedback"}
    assert started == [APP_ID]


def test_stalled_class_gate_does_not_reenter_after_recorded_repair_attempt(
    monkeypatch,
) -> None:
    state = {
        "extracted_bce_classes": {"Classes": [{"className": "SubmitControl"}]},
        "class_diagram_check": {
            "stopped": "stalled",
            "repair_iters": 1,
            "repair_history": {"status": "STALLED", "attempts": [{"target": "UC1"}]},
        },
    }
    spec = SimpleNamespace(
        model_key="extracted_bce_classes",
        reconcile=lambda _state: {},
    )
    monkeypatch.setitem(design_service.DESIGN_SPECS, "class_diagram", spec)
    monkeypatch.setattr(
        design_service, "session_status",
        lambda _app_id: {"active": True, "stage": "class_diagram"},
    )
    started: list[str] = []
    monkeypatch.setattr(design_service, "start_design_session", started.append)

    result = design_service._retry_stalled_class_gate_after_reconcile(
        APP_ID, state, _stalled_class_readiness()
    )

    assert result is None
    assert started == []
