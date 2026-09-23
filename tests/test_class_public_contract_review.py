from __future__ import annotations

from app.design.services.class_diagram.cache import ProcessLocalAcceptedUnitCache
from app.design.services.class_diagram.public_contract_review import (
    review_public_contract_closure,
    semantic_evidence_for_readiness,
)
from app.design.services.class_diagram.scenario import build_scenario_index


def _scenario(source: str = "caller_input") -> dict:
    return {
        "use_cases": [{"id": "UC1", "name": "Submit", "primary_actor": "Member"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [{"step_number": 1, "subject_ref": "Member", "sentence": "Member submits details."}],
            "extensions": [],
            "public_contract": {"identity_obligations": [], "required_values": [{
                "name": "request details", "source": source, "value_type": "object", "usage": "control", "requirement_ids": ["R1"],
            }]},
        }],
        "relationships": {"includes": [], "extends": []},
    }


def _model() -> dict:
    return {
        "Classes": [{"className": "SubmitBoundary", "stereotype": "Boundary", "fields": [], "operations": [{
            "operationId": "SubmitBoundary::submit(details:RequestData)", "name": "submit",
            "parameters": [{"name": "details", "type": "RequestData"}], "returnType": "void", "stepRefs": ["UC1:main:1"],
        }]}, {"className": "SubmitControl", "stereotype": "Control", "fields": [], "operations": [{
            "operationId": "SubmitControl::submit(details:RequestData)", "name": "submit",
            "parameters": [{"name": "details", "type": "RequestData"}], "returnType": "Receipt", "stepRefs": ["UC1:main:1"],
        }]}],
        "Collaborations": [{"collaborationId": "UC1", "calls": [{
            "callId": "UC1-call-1", "receiverOperationId": "SubmitBoundary::submit(details:RequestData)",
        }, {"callId": "UC1-call-2", "receiverOperationId": "SubmitControl::submit(details:RequestData)",
            "parentCallId": "UC1-call-1", "argumentBindings": [{"parameter": "details", "sourceRef": "UC1:main:1#details"}],
        }]}],
    }


def _pass_response() -> dict:
    return {"status": "pass", "finding": "", "mappings": [{
        "obligationId": "value:1", "operationId": "SubmitControl::submit(details:RequestData)",
        "callId": "UC1-call-2", "parameterName": "details", "fieldOwner": None, "fieldName": None,
        "rationale": "The accepted Control receives the caller-supplied details on this bound call.",
    }]}


def test_public_contract_review_persists_digest_bound_pass_and_reuses_it(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    calls = 0
    def fake_parse(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _pass_response()
    monkeypatch.setattr(subject, "parse_structured", fake_parse)
    index = build_scenario_index(_scenario())
    model = _model()
    findings, evidence = review_public_contract_closure(model, index, cache=ProcessLocalAcceptedUnitCache())
    assert findings == []
    assert evidence["status"] == "pass"
    assert calls == 1

    reused, repeated = review_public_contract_closure(model, index, cache=ProcessLocalAcceptedUnitCache(), evidence=evidence)
    assert reused == []
    assert repeated == evidence
    assert calls == 1
    assert semantic_evidence_for_readiness(model, {"class_diagram_check": {"semanticEvidence": evidence}}, index) == []


def test_public_contract_review_rejects_uncited_or_wrong_call_evidence(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject
    bad = _pass_response()
    bad["mappings"][0]["callId"] = "invented"
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: bad)
    findings, evidence = review_public_contract_closure(_model(), build_scenario_index(_scenario()))
    assert len(findings) == 1
    assert findings[0].requires_user_input is False
    assert "does not invoke" in evidence["verdicts"][0]["finding"]


def test_public_contract_review_rejects_boundary_only_mapping(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject
    boundary = _pass_response()
    boundary["mappings"][0].update({
        "operationId": "SubmitBoundary::submit(details:RequestData)", "callId": "UC1-call-1",
    })
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: boundary)
    findings, evidence = review_public_contract_closure(_model(), build_scenario_index(_scenario()))
    assert len(findings) == 1
    assert "must be mapped to a Control call" in evidence["verdicts"][0]["finding"]


def test_authenticated_context_cannot_be_satisfied_by_caller_input_binding(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject
    model = _model()
    model["Collaborations"][0]["calls"][1]["argumentBindings"][0]["sourceRef"] = "UC1:main:1#details"
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: _pass_response())
    findings, evidence = review_public_contract_closure(
        model, build_scenario_index(_scenario("authenticated_actor_context")),
    )
    assert len(findings) == 1
    assert "must use trusted context" in evidence["verdicts"][0]["finding"]


def test_readiness_blocks_missing_semantic_evidence_without_calling_reviewer() -> None:
    findings = semantic_evidence_for_readiness(_model(), {}, build_scenario_index(_scenario()))
    assert len(findings) == 1
    assert findings[0].requires_user_input is False


def test_design_readiness_consumes_persisted_evidence_without_a_reviewer_call(monkeypatch) -> None:
    import app.design.validation as design_validation
    from app.design.services.class_diagram import public_contract_review as subject
    from app.validation import ValidationReport

    # This test isolates the persisted-evidence boundary from the class schema
    # validator; no semantic service is patched or invoked on this read-only path.
    monkeypatch.setattr(
        design_validation,
        "class_diagram_validation_report",
        lambda *_args: ValidationReport(status="clean"),
    )
    index = build_scenario_index(_scenario())
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: _pass_response())
    _findings, evidence = review_public_contract_closure(_model(), index)
    state = {"usecase_spec": _scenario(), "extracted_bce_classes": _model(), "class_diagram_check": {"semanticEvidence": evidence}}
    assert design_validation.design_readiness_report(state, stages=["class_diagram"])["status"] == "READY"
    state["class_diagram_check"] = {}
    assert design_validation.design_readiness_report(state, stages=["class_diagram"])["status"] == "BLOCKED"


def test_validate_class_model_accepts_prebuilt_scenario_index(monkeypatch) -> None:
    import app.design.validation as design_validation
    from app.validation import ValidationReport

    monkeypatch.setattr(
        design_validation,
        "class_diagram_validation_report",
        lambda *_args: ValidationReport(status="clean"),
    )
    index = build_scenario_index(_scenario())
    report = design_validation.validate_class_model(_model(), index)
    assert "class.public-contract-semantic" not in report.checked_rule_ids
