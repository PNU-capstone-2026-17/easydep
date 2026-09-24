from __future__ import annotations

from app.design.services.class_diagram.cache import ProcessLocalAcceptedUnitCache
from app.design.services.class_diagram.public_contract_review import (
    review_public_contract_closure,
    semantic_evidence_for_readiness,
)
from app.design.services.class_diagram.scenario import build_scenario_index


def _scenario(source: str = "caller_input") -> dict:
    return {
        "use_cases": [{"id": "UC1", "name": "Submit", "primary_actor_ref": "ACT1", "primary_actor": "Member"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [{"step_number": 1, "subject_ref": "ACT1", "sentence": "Member submits details."}],
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
            "stableId": "op-boundary", "operationId": "SubmitBoundary::submit(details:RequestData)", "name": "submit",
            "parameters": [{"name": "details", "type": "RequestData", "stableRef": "param-details"}], "returnType": "void", "stepRefs": ["UC1:main:1"],
        }]}, {"className": "SubmitControl", "stereotype": "Control", "fields": [], "operations": [{
            "stableId": "op-control", "operationId": "SubmitControl::submit(details:RequestData)", "name": "submit",
            "parameters": [{"name": "details", "type": "RequestData", "stableRef": "param-details-control"}], "returnType": "Receipt", "stepRefs": ["UC1:main:1"],
        }]}],
        "Collaborations": [{"collaborationId": "UC1", "calls": [{
            "callId": "UC1-call-1", "stableId": "call-boundary", "receiverOperationId": "SubmitBoundary::submit(details:RequestData)",
        }, {"callId": "UC1-call-2", "stableId": "call-control", "receiverOperationId": "SubmitControl::submit(details:RequestData)",
            "parentCallId": "UC1-call-1", "argumentBindings": [{"parameter": "details", "sourceRef": "UC1:main:1#details"}],
        }]}],
    }


def _pass_response() -> dict:
    return {"status": "pass", "finding": "", "mappings": [{
        "obligationId": "value:1", "operationRef": "op-control", "operationId": "SubmitControl::submit(details:RequestData)",
        "callRef": "call-control", "parameterRef": "param-details-control", "fieldRef": None,
        "rationale": "The accepted Control receives the caller-supplied details on this bound call.",
    }]}


def test_dto_field_ref_is_accepted_only_for_the_cited_concrete_type(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    model = _model()
    model["DataTypes"] = [
        {"name": "RequestData", "kind": "valueObject", "fields": ["value : String"], "fieldRefs": ["field-request-value"]},
        {"name": "Receipt", "kind": "valueObject", "fields": ["receiptId : String"], "fieldRefs": ["field-receipt-id"]},
    ]
    payload = _pass_response()
    payload["mappings"][0]["fieldRef"] = "field-request-value"
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: payload)
    assert subject.review_public_contract_closure(model, build_scenario_index(_scenario()))[0] == []

    payload["mappings"][0]["fieldRef"] = "field-receipt-id"
    findings, evidence = subject.review_public_contract_closure(model, build_scenario_index(_scenario()))
    assert len(findings) == 1
    assert "does not belong" in evidence["verdicts"][0]["finding"]


def test_display_rename_does_not_change_field_ref_evidence(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    model = _model()
    model["DataTypes"] = [{"name": "RequestData", "kind": "valueObject", "fields": ["renamed : String"], "fieldRefs": ["field-request-value"]}]
    payload = _pass_response()
    payload["mappings"][0]["fieldRef"] = "field-request-value"
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: payload)
    assert subject.review_public_contract_closure(model, build_scenario_index(_scenario()))[0] == []


def test_wrapped_parameter_type_exposes_declared_dto_field_evidence(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    model = _model()
    model["Classes"][1]["operations"][0]["parameters"][0]["type"] = "Optional<RequestData>"
    model["DataTypes"] = [{"name": "RequestData", "kind": "valueObject", "fields": ["value : String"], "fieldRefs": ["field-request-value"]}]
    payload = _pass_response()
    payload["mappings"][0]["fieldRef"] = "field-request-value"
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: payload)
    assert subject.review_public_contract_closure(model, build_scenario_index(_scenario()))[0] == []


def test_operation_rename_keeps_stable_operation_and_parameter_evidence(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    model = _model()
    old = "SubmitControl::submit(details:RequestData)"
    renamed = "SubmitControl::accept(payload:RequestData)"
    control_op = model["Classes"][1]["operations"][0]
    control_op["name"] = "accept"
    control_op["operationId"] = renamed
    model["Collaborations"][0]["calls"][1]["receiverOperationId"] = renamed
    payload = _pass_response()
    payload["mappings"][0]["operationId"] = renamed
    assert control_op["stableId"] == "op-control"
    assert old != renamed
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: payload)
    assert subject.review_public_contract_closure(model, build_scenario_index(_scenario()))[0] == []


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


def test_authentication_policy_is_not_a_class_operation_obligation(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    scenario = _scenario()
    contract = scenario["use_case_specs"][0]["public_contract"]
    contract["identity_obligations"] = [{"obligation": "authenticate", "requirement_ids": ["R2"]}]
    contract["required_values"] = []
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("reviewer must not be called")))

    findings, evidence = review_public_contract_closure(_model(), build_scenario_index(scenario))

    assert findings == []
    assert evidence["status"] == "not_required"
    assert evidence["verdicts"] == []


def test_authentication_policy_is_skipped_while_required_values_stay_reviewed(monkeypatch) -> None:
    import json
    from app.design.services.class_diagram import public_contract_review as subject

    scenario = _scenario()
    scenario["use_case_specs"][0]["public_contract"]["identity_obligations"] = [
        {"obligation": "authenticate", "requirement_ids": ["R2"]},
    ]
    reviewed_obligations: list[list[dict]] = []

    def fake_parse(messages, *_args, **_kwargs):
        payload = json.loads(messages[1]["content"])
        reviewed_obligations.append(payload["publicContractObligations"])
        return _pass_response()

    monkeypatch.setattr(subject, "parse_structured", fake_parse)
    findings, evidence = review_public_contract_closure(_model(), build_scenario_index(scenario))

    assert findings == []
    assert evidence["status"] == "pass"
    assert len(reviewed_obligations) == 1
    assert [item["obligationId"] for item in reviewed_obligations[0]] == ["value:1"]


def test_public_contract_review_rejects_uncited_or_wrong_call_evidence(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject
    bad = _pass_response()
    bad["mappings"][0]["callRef"] = "invented"
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: bad)
    findings, evidence = review_public_contract_closure(_model(), build_scenario_index(_scenario()))
    assert len(findings) == 1
    assert findings[0].requires_user_input is False
    assert "does not invoke" in evidence["verdicts"][0]["finding"]


def test_public_contract_review_rejects_boundary_only_mapping(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject
    boundary = _pass_response()
    boundary["mappings"][0].update({
        "operationRef": "op-boundary", "operationId": "SubmitBoundary::submit(details:RequestData)", "callRef": "call-boundary",
    })
    boundary["mappings"][0]["parameterRef"] = "param-details"
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: boundary)
    findings, evidence = review_public_contract_closure(_model(), build_scenario_index(_scenario()))
    assert len(findings) == 1
    assert "must be mapped to a Control call" in evidence["verdicts"][0]["finding"]


def test_public_contract_review_corrects_a_boundary_citation_once(monkeypatch) -> None:
    import json
    from app.design.services.class_diagram import public_contract_review as subject

    boundary = _pass_response()
    boundary["mappings"][0].update({
        "operationRef": "op-boundary",
        "operationId": "SubmitBoundary::submit(details:RequestData)",
        "callRef": "call-boundary",
        "parameterRef": "param-details",
    })
    calls: list[dict] = []

    def fake_parse(messages, *_args, **_kwargs):
        calls.append(json.loads(messages[1]["content"]))
        return boundary if len(calls) == 1 else _pass_response()

    monkeypatch.setattr(subject, "parse_structured", fake_parse)
    model = _model()
    index = build_scenario_index(_scenario())
    cache = ProcessLocalAcceptedUnitCache()
    findings, evidence = subject.review_public_contract_closure(model, index, cache=cache)

    assert findings == []
    assert len(calls) == 2
    assert calls[1]["previousResponse"] == boundary
    assert "must be mapped to a Control call" in calls[1]["validatorDiagnostic"]
    assert evidence["verdicts"][0]["mappings"][0]["operationRef"] == "op-control"
    assert subject.review_public_contract_closure(model, index, cache=cache) == ([], evidence)
    assert len(calls) == 2


def test_public_contract_review_stops_after_one_persistent_invalid_correction(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    calls = 0
    boundary = _pass_response()
    boundary["mappings"][0].update({
        "operationRef": "op-boundary",
        "operationId": "SubmitBoundary::submit(details:RequestData)",
        "callRef": "call-boundary",
        "parameterRef": "param-details",
    })

    def fake_parse(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return boundary

    monkeypatch.setattr(subject, "parse_structured", fake_parse)
    findings, evidence = subject.review_public_contract_closure(_model(), build_scenario_index(_scenario()))

    assert calls == 2
    assert len(findings) == 1
    assert "must be mapped to a Control call" in evidence["verdicts"][0]["finding"]


def test_authenticated_context_cannot_be_satisfied_by_caller_input_binding(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject
    scenario = _scenario("authenticated_actor_context")
    scenario["use_case_specs"][0]["public_contract"]["identity_obligations"] = [
        {"obligation": "authenticate", "obligation_ref": "auth-1", "subject_ref": "ACT1", "requirement_ids": ["R2"]},
    ]
    scenario["use_case_specs"][0]["public_contract"]["required_values"][0]["source_authenticate_obligation_ref"] = "auth-1"
    model = _model()
    model["Classes"][1]["operations"][0]["parameters"][0]["obligationRef"] = "auth-1"
    model["Collaborations"][0]["calls"][1]["argumentBindings"][0].update({
        "sourceRef": "context#wrong-auth", "sourceKind": "authenticated_context",
    })
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: _pass_response())
    findings, evidence = review_public_contract_closure(model, build_scenario_index(scenario))
    assert len(findings) == 1
    assert "must use trusted context" in evidence["verdicts"][0]["finding"]


def test_authenticated_required_value_accepts_exact_authenticate_binding(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject
    scenario = _scenario("authenticated_actor_context")
    scenario["use_case_specs"][0]["public_contract"]["identity_obligations"] = [
        {"obligation": "authenticate", "obligation_ref": "auth-1", "subject_ref": "ACT1", "requirement_ids": ["R2"]},
    ]
    scenario["use_case_specs"][0]["public_contract"]["required_values"][0]["source_authenticate_obligation_ref"] = "auth-1"
    model = _model()
    model["Classes"][1]["operations"][0]["parameters"][0]["obligationRef"] = "auth-1"
    model["Collaborations"][0]["calls"][1]["argumentBindings"][0].update({
        "sourceRef": "context#auth-1", "sourceKind": "authenticated_context",
    })
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: _pass_response())
    assert subject.review_public_contract_closure(model, build_scenario_index(scenario))[0] == []


def _identity_case(source_kind: str, source_ref: str | None = None) -> tuple[dict, dict]:
    scenario = _scenario()
    contract = scenario["use_case_specs"][0]["public_contract"]
    contract["required_values"] = []
    identify = {"obligation": "identify", "identity_source_kind": source_kind, "requirement_ids": ["R-ID"]}
    if source_kind == "authenticated_context":
        identify["source_authenticate_obligation_ref"] = "auth-1"
        contract["identity_obligations"] = [
            {"obligation": "authenticate", "obligation_ref": "auth-1", "subject_ref": "ACT1", "requirement_ids": ["R-AUTH"]},
            identify,
        ]
    else:
        contract["identity_obligations"] = [identify]
    model = _model()
    model["Collaborations"][0]["calls"][0]["stepRefs"] = ["UC1:main:1"]
    model["Collaborations"][0]["calls"][1]["argumentBindings"][0].update({
        "sourceRef": source_ref or "UC1:main:1#param-details",
        "sourceKind": {"caller_input": "use_case_input", "authenticated_context": "authenticated_context", "system_result": "earlier_step_result"}.get(source_kind, ""),
    })
    mapping = _pass_response()["mappings"][0]
    mapping.update({"obligationId": "identity:2" if source_kind == "authenticated_context" else "identity:1"})
    return scenario, {"status": "pass", "finding": "", "mappings": [mapping]}


def test_identify_caller_input_requires_canonical_actor_binding(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject
    scenario, payload = _identity_case("caller_input")
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: payload)
    assert subject.review_public_contract_closure(_identity_model("caller_input", "UC1:main:1#param-details"), build_scenario_index(scenario))[0] == []


def _model_with_root_step() -> dict:
    model = _model()
    model["Collaborations"][0]["calls"][0]["stepRefs"] = ["UC1:main:1"]
    return model


def _identity_model(source_kind: str, source_ref: str) -> dict:
    model = _model_with_root_step()
    model["Collaborations"][0]["calls"][1]["argumentBindings"][0].update({
        "sourceRef": source_ref,
        "sourceKind": {"caller_input": "use_case_input", "authenticated_context": "authenticated_context", "system_result": "earlier_step_result"}.get(source_kind, ""),
    })
    return model


def test_identify_rejects_wrong_actor_ref_and_unresolved_source(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject
    scenario, payload = _identity_case("caller_input", "invented#subject")
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: payload)
    findings, evidence = subject.review_public_contract_closure(_identity_model("caller_input", "invented#subject"), build_scenario_index(scenario))
    assert findings and "canonical actor input" in evidence["verdicts"][0]["finding"]

    scenario, payload = _identity_case("unresolved")
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: payload)
    findings, evidence = subject.review_public_contract_closure(_identity_model("unresolved", "UC1:main:1#param-details"), build_scenario_index(scenario))
    assert findings and "clarification is required" in evidence["verdicts"][0]["finding"]


def test_identify_authenticated_context_requires_exact_authenticate_ref(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject
    scenario, payload = _identity_case("authenticated_context", "context#wrong-auth")
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: payload)
    findings, evidence = subject.review_public_contract_closure(_identity_model("authenticated_context", "context#wrong-auth"), build_scenario_index(scenario))
    assert findings and "exact accepted authenticate context" in evidence["verdicts"][0]["finding"]

    scenario, payload = _identity_case("authenticated_context", "context#auth-1")
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: payload)
    assert subject.review_public_contract_closure(_identity_model("authenticated_context", "context#auth-1"), build_scenario_index(scenario))[0] == []


def test_identify_authenticated_context_requires_exact_source_kind(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    scenario, payload = _identity_case("authenticated_context", "context#auth-1")
    model = _identity_model("authenticated_context", "context#auth-1")
    binding = model["Collaborations"][0]["calls"][1]["argumentBindings"][0]
    binding.pop("sourceKind")
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: payload)
    findings, evidence = subject.review_public_contract_closure(model, build_scenario_index(scenario))
    assert findings and "exact accepted authenticate context" in evidence["verdicts"][0]["finding"]

    binding["sourceKind"] = "use_case_input"
    findings, evidence = subject.review_public_contract_closure(model, build_scenario_index(scenario))
    assert findings and "exact accepted authenticate context" in evidence["verdicts"][0]["finding"]


def test_identify_system_result_requires_prior_nonvoid_call_result(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject
    scenario, payload = _identity_case("system_result", "call-boundary#result")
    model = _identity_model("system_result", "call-boundary#result")
    model["Classes"][0]["operations"][0]["returnType"] = "ActorId"
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: payload)
    assert subject.review_public_contract_closure(model, build_scenario_index(scenario))[0] == []

    scenario, payload = _identity_case("system_result", "call-control#result")
    model = _identity_model("system_result", "call-control#result")
    findings, evidence = subject.review_public_contract_closure(model, build_scenario_index(scenario))
    assert findings and "prior accepted call result" in evidence["verdicts"][0]["finding"]


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


def test_class_graph_adapts_semantic_finding_for_artifact_check_serialization(monkeypatch) -> None:
    from app.design.graphs import subgraphs
    from app.validation import Finding, ValidationReport

    monkeypatch.setattr(subgraphs, "validate_class_model", lambda *_args: ValidationReport(status="clean"))
    monkeypatch.setattr(
        subgraphs,
        "review_public_contract_closure",
        lambda *_args, **_kwargs: ([Finding("class.public-contract-semantic", "Missing contract mapping", "UC1", origin="semantic")], {}),
    )

    model = _model()
    model["Collaborations"][0]["useCaseIds"] = ["UC1"]
    model["DataTypes"] = [
        {"name": "RequestData", "kind": "valueObject", "fields": ["value : String"], "values": []},
        {"name": "Receipt", "kind": "valueObject", "fields": ["value : String"], "values": []},
    ]
    for owner in model["Classes"]:
        operation = owner["operations"][0]
        operation["parameters"][0]["type"] = "String"
        operation["returnType"] = "String" if owner["className"] == "SubmitControl" else "void"
        operation["operationId"] = operation["operationId"].replace("RequestData", "String")
    for call in model["Collaborations"][0]["calls"]:
        call["receiverOperationId"] = call["receiverOperationId"].replace("RequestData", "String")
    findings = subgraphs._class_model_findings(model, {"usecase_spec": _scenario()})

    assert len(findings) == 1
    serialized = findings[0].as_issue()
    assert "UC1: Missing contract mapping" in serialized
    assert "class.public-contract-semantic" in serialized


def test_class_semantic_repair_batch_forces_the_owning_operation_scope(monkeypatch) -> None:
    from app.design.graphs import subgraphs
    from app.design.knowledge.detectors import Finding

    observed: dict = {}
    monkeypatch.setattr(
        subgraphs,
        "revise_class_model",
        lambda model, *_args, **kwargs: observed.update(kwargs) or model,
    )

    model = _model()
    model["Collaborations"][0]["useCaseIds"] = ["UC1"]
    model["DataTypes"] = [
        {"name": "RequestData", "kind": "valueObject", "fields": ["value : String"], "values": []},
        {"name": "Receipt", "kind": "valueObject", "fields": ["value : String"], "values": []},
    ]
    subgraphs._repair_class_batch(
        model,
        "Repair the public contract.",
        {"usecase_spec": _scenario()},
        {"UC1"},
        [Finding("class.public-contract-semantic", "Missing contract mapping", "UC1")],
    )

    assert observed["operation_use_case_ids"] == {"UC1"}
