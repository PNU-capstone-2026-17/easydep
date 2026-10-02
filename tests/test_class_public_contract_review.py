from __future__ import annotations

import pytest

from app.design.services.class_diagram.cache import ProcessLocalAcceptedUnitCache
from app.design.services.class_diagram.public_contract_review import (
    review_public_contract_closure,
    semantic_evidence_for_readiness,
)
from app.design.services.class_diagram.scenario import build_scenario_index


def test_result_only_review_does_not_require_a_parameter_reference():
    from app.design.services.class_diagram.public_contract_review import _PROMPT

    prompt = " ".join(_PROMPT.split())
    assert "A result-only value does not require a parameter citation" in prompt
    assert "For a required value with usage control or both" in prompt
    assert "For every required value, the cited parameter's requiredValueRef" not in prompt


def test_system_result_exemption_applies_only_to_result_usage() -> None:
    from app.design.services.class_diagram.public_contract_review import _PROMPT

    prompt = " ".join(_PROMPT.split())
    assert "whose source is system_result and usage is result" in prompt
    assert "system_result value with usage both still requires the exact Control parameter and argument binding" in prompt


def _scenario(source: str = "caller_input") -> dict:
    return {
        "use_cases": [{"id": "UC1", "name": "Submit", "primary_actor_ref": "ACT1", "primary_actor": "Member"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [{"step_number": 1, "subject_ref": "ACT1", "sentence": "Member submits details."}],
            "extensions": [],
            "public_contract": {"identity_obligations": [], "required_values": [{
                "value_ref": "val-1",
                "name": "request details", "source": source,
                "value_type": "identifier" if source == "authenticated_actor_context" else "object",
                "usage": "control", "requirement_ids": ["R1"],
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
            "parameters": [{"name": "details", "type": "RequestData", "stableRef": "param-details-control", "requiredValueRef": "val-1"}], "returnType": "Receipt", "stepRefs": ["UC1:main:1"],
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


def _authenticated_identify_obligations(scenario: dict) -> str:
    scenario["use_case_specs"][0]["public_contract"]["identity_obligations"] = [
        {"obligation": "authenticate", "obligation_ref": "auth-1", "subject_ref": "ACT1", "requirement_ids": ["R-AUTH"]},
        {"obligation": "identify", "obligation_ref": "identify-1", "subject": "student",
         "identity_source_kind": "authenticated_context",
         "source_authenticate_obligation_ref": "auth-1", "subject_ref": "ACT1", "requirement_ids": ["R-ID"]},
    ]
    scenario["use_case_specs"][0]["public_contract"]["required_values"][0]["identity_obligation_ref"] = "identify-1"
    return "identify-1"


def _with_identity_mapping(response: dict, *, operation_id: str | None = None,
                           call_ref: str | None = None, parameter_ref: str | None = None,
                           field_ref: str | None = None) -> dict:
    mapping = next((item for item in response["mappings"]
                    if item.get("obligationId") == "identity:2"), None)
    is_new = mapping is None
    if mapping is None:
        mapping = dict(response["mappings"][0])
        mapping["obligationId"] = "identity:2"
    if operation_id:
        mapping["operationId"] = operation_id
    if call_ref:
        mapping["callRef"] = call_ref
    if parameter_ref:
        mapping["parameterRef"] = parameter_ref
    if field_ref:
        mapping["fieldRef"] = field_ref
    if is_new:
        response["mappings"].append(mapping)
    return response


def test_review_slice_preserves_owner_stereotypes() -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    index = build_scenario_index(_scenario())
    use_case = index.use_cases[0]

    reviewed_classes = {
        item["className"]: item["stereotype"]
        for item in subject._slice(_model(), use_case)["Classes"]
    }

    assert reviewed_classes == {"SubmitBoundary": "Boundary", "SubmitControl": "Control"}


def test_prior_semantic_evidence_version_is_stale_after_slice_change() -> None:
    from app.design.services.class_diagram import public_contract_review as subject
    from app.validation import stable_digest

    model = _model()
    index = build_scenario_index(_scenario())
    stale = {
        "version": "class-public-contract-review/v6",
        "modelDigest": stable_digest(model),
        "contractDigest": stable_digest([subject._obligations(item) for item in index.use_cases]),
    }

    assert not subject._evidence_matches(stale, model, index)


def test_review_slice_includes_directly_referenced_entity_fields_and_links() -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    model = _model()
    model["Classes"][0]["operations"][0]["parameters"][0]["type"] = "CourseOffering"
    model["Classes"][1]["operations"][0]["parameters"].append({
        "name": "student", "type": "Student", "stableRef": "param-student",
    })
    model["Classes"][1]["operations"][0]["returnType"] = "List<CourseOffering>"
    model["Classes"].extend([
        {
            "className": "CourseOffering", "stereotype": "Entity",
            "fields": ["courseId : String", "capacity : Integer"],
            "fieldRefs": ["field-course-id", "field-capacity"], "operations": [],
        },
        {
            "className": "UnrelatedEntity", "stereotype": "Entity",
            "fields": ["id : UUID"], "fieldRefs": ["field-unrelated"], "operations": [],
        },
        {
            "className": "Student", "stereotype": "Entity",
            "fields": ["studentId : UUID"], "fieldRefs": ["field-student-id"], "operations": [],
        },
    ])

    entity = next(
        item for item in subject._slice(model, build_scenario_index(_scenario()).use_cases[0])["Classes"]
        if item["className"] == "CourseOffering"
    )

    assert entity["fields"] == ["courseId : String", "capacity : Integer"]
    assert entity["fieldRefs"] == ["field-course-id", "field-capacity"]
    assert entity["outputEvidence"] == (
        "The cited Control operations return List<CourseOffering>; these fields are "
        "the returned CourseOffering details."
    )
    assert "UnrelatedEntity" not in {
        item["className"] for item in subject._slice(model, build_scenario_index(_scenario()).use_cases[0])["Classes"]
    }
    assert "Student" in {
        item["className"] for item in subject._slice(model, build_scenario_index(_scenario()).use_cases[0])["Classes"]
    }
    student = next(
        item for item in subject._slice(model, build_scenario_index(_scenario()).use_cases[0])["Classes"]
        if item["className"] == "Student"
    )
    assert "outputEvidence" not in student


def test_system_result_value_needs_control_return_not_prior_call_result(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    scenario = _scenario("system_result")
    scenario["use_case_specs"][0]["public_contract"]["required_values"][0].update({
        "name": "receipt identifier",
        "usage": "result",
    })
    model = _model()
    model["DataTypes"] = [{
        "name": "Receipt",
        "kind": "valueObject",
        "fields": ["receiptId : String"],
        "fieldRefs": ["field-receipt-id"],
    }]
    response = _pass_response()
    response["mappings"][0].update({
        "parameterRef": None,
        "fieldRef": "field-receipt-id",
        "rationale": "The Control returns the system-produced receipt identifier.",
    })
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: response)

    findings, _evidence = subject.review_public_contract_closure(model, build_scenario_index(scenario))

    assert findings == []


@pytest.mark.parametrize(
    ("field_ref", "field_type", "expected"),
    [
        ("field-receipt-id", "UUID", ""),
        ("field-receipt-id", "Optional<UUID>", "non-Optional fieldRef"),
        (None, "UUID", "non-Optional fieldRef"),
        ("field-unrelated", "UUID", "does not belong to the cited concrete type"),
    ],
)
def test_system_result_identifier_requires_concrete_non_optional_return_field(
    field_ref: str | None, field_type: str, expected: str,
) -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    scenario = _scenario("system_result")
    scenario["use_case_specs"][0]["public_contract"]["required_values"][0].update({
        "name": "receipt identifier", "usage": "result", "value_type": "identifier",
    })
    model = _model()
    model["DataTypes"] = [{
        "name": "Receipt", "kind": "valueObject",
        "fields": [f"receiptId : {field_type}", "status : String"],
        "fieldRefs": ["field-receipt-id", "field-receipt-status"],
    }]
    response = _pass_response()
    response["mappings"][0].update({
        "parameterRef": None, "fieldRef": field_ref,
        "rationale": "The Control returns the system-produced receipt identifier.",
    })

    problem = subject._verify_response(
        model, build_scenario_index(scenario).use_case("UC1"),
        subject._ReviewResponse.model_validate(response),
    )

    assert expected in problem


def test_system_result_identifier_allows_non_optional_primitive_return() -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    scenario = _scenario("system_result")
    scenario["use_case_specs"][0]["public_contract"]["required_values"][0].update({
        "name": "receipt identifier", "usage": "result", "value_type": "identifier",
    })
    model = _model()
    model["Classes"][1]["operations"][0]["returnType"] = "UUID"
    response = _pass_response()
    response["mappings"][0].update({
        "parameterRef": None, "fieldRef": None,
        "rationale": "The Control directly returns the system-produced identifier.",
    })

    assert subject._verify_response(
        model, build_scenario_index(scenario).use_case("UC1"),
        subject._ReviewResponse.model_validate(response),
    ) == ""


def test_system_result_identifier_rejects_optional_primitive_control_return() -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    scenario = _scenario("system_result")
    scenario["use_case_specs"][0]["public_contract"]["required_values"][0].update({
        "name": "receipt identifier", "usage": "result", "value_type": "identifier",
    })
    model = _model()
    model["Classes"][1]["operations"][0]["returnType"] = "Optional<UUID>"
    response = _pass_response()
    response["mappings"][0].update({
        "parameterRef": None, "fieldRef": None,
        "rationale": "The Control directly returns the system-produced identifier.",
    })

    problem = subject._verify_response(
        model, build_scenario_index(scenario).use_case("UC1"),
        subject._ReviewResponse.model_validate(response),
    )

    assert "non-Optional Control return" in problem


def test_authenticated_context_can_flow_through_prior_control_result_field(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    scenario = _scenario("authenticated_actor_context")
    identify_ref = _authenticated_identify_obligations(scenario)
    model = _model()
    model["Classes"].append({
        "className": "TrustedContextControl", "stereotype": "Control", "fields": [], "operations": [{
            "stableId": "op-context", "operationId": "TrustedContextControl::resolve(context:String)",
            "name": "resolve", "parameters": [{"name": "context", "type": "String", "stableRef": "param-context", "requiredValueRef": "val-1"}],
            "returnType": "ActorRequest", "stepRefs": ["UC1:main:1"],
        }],
    })
    model["Classes"][1]["operations"][0]["parameters"][0].update({
        "type": "ActorRequest", "requiredValueRef": "val-1",
    })
    model["Classes"][1]["operations"][0]["operationId"] = "SubmitControl::submit(details:ActorRequest)"
    calls = model["Collaborations"][0]["calls"]
    calls[1]["stableId"] = "call-context"
    calls[1]["receiverOperationId"] = "TrustedContextControl::resolve(context:String)"
    calls[1]["parentCallId"] = "UC1-call-1"
    calls[1]["argumentBindings"] = [{"parameter": "context", "sourceRef": "value#val-1"}]
    calls.append({
        "callId": "UC1-call-3", "stableId": "call-consumer",
        "receiverOperationId": "SubmitControl::submit(details:ActorRequest)", "parentCallId": "UC1-call-2",
        "argumentBindings": [{"parameter": "details", "sourceRef": "call-context#result"}],
    })
    model["DataTypes"] = [
        {"name": "ActorRequest", "kind": "valueObject", "fields": ["memberKey : String"], "fieldRefs": ["field-actor-request"]},
    ]
    response = _pass_response()
    response["mappings"][0].update({
        "operationRef": "op-control", "operationId": "SubmitControl::submit(details:RequestData)",
        "callRef": "call-consumer", "parameterRef": "param-details-control", "fieldRef": "field-actor-request",
    })
    response["mappings"][0]["obligationId"] = "value:1"
    response["mappings"][0]["operationId"] = "SubmitControl::submit(details:ActorRequest)"
    _with_identity_mapping(
        response, operation_id="SubmitControl::submit(details:ActorRequest)",
        call_ref="call-consumer", parameter_ref="param-details-control",
        field_ref="field-actor-request",
    )
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: response)

    assert subject.review_public_contract_closure(model, build_scenario_index(scenario))[0] == []


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
    identify_ref = _authenticated_identify_obligations(scenario)
    model = _model()
    model["Classes"][1]["operations"][0]["parameters"][0]["requiredValueRef"] = "val-1"
    model["Classes"][1]["operations"][0]["parameters"][0]["type"] = "String"
    model["Classes"][1]["operations"][0]["operationId"] = "SubmitControl::submit(details:String)"
    model["Collaborations"][0]["calls"][1]["receiverOperationId"] = "SubmitControl::submit(details:String)"
    model["Collaborations"][0]["calls"][1]["argumentBindings"][0].update({
        "sourceRef": "value#wrong-identify",
    })
    response = _pass_response()
    response["mappings"][0]["operationId"] = "SubmitControl::submit(details:String)"
    _with_identity_mapping(response, operation_id="SubmitControl::submit(details:String)")
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: response)
    findings, evidence = review_public_contract_closure(model, build_scenario_index(scenario))
    assert len(findings) == 1
    assert "linked to this identity obligation" in evidence["verdicts"][0]["finding"]


def test_authenticated_required_value_accepts_exact_authenticate_binding(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject
    scenario = _scenario("authenticated_actor_context")
    identify_ref = _authenticated_identify_obligations(scenario)
    model = _model()
    model["Classes"][1]["operations"][0]["parameters"][0]["requiredValueRef"] = "val-1"
    model["Classes"][1]["operations"][0]["parameters"][0]["type"] = "String"
    model["Classes"][1]["operations"][0]["operationId"] = "SubmitControl::submit(details:String)"
    model["Collaborations"][0]["calls"][1]["receiverOperationId"] = "SubmitControl::submit(details:String)"
    model["Collaborations"][0]["calls"][1]["argumentBindings"][0].update({
        "sourceRef": "value#val-1",
    })
    response = _pass_response()
    response["mappings"][0]["operationId"] = "SubmitControl::submit(details:String)"
    _with_identity_mapping(response, operation_id="SubmitControl::submit(details:String)")
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: response)
    assert subject.review_public_contract_closure(model, build_scenario_index(scenario))[0] == []


def test_authenticated_context_direct_dto_requires_semantic_review_pass(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    scenario = _scenario("authenticated_actor_context")
    identify_ref = _authenticated_identify_obligations(scenario)
    model = _model()
    model["Classes"][1]["operations"][0]["parameters"][0].update({"type": "ActorRequest", "requiredValueRef": "val-1"})
    model["Classes"][1]["operations"][0]["operationId"] = "SubmitControl::submit(details:ActorRequest)"
    model["Collaborations"][0]["calls"][1]["receiverOperationId"] = "SubmitControl::submit(details:ActorRequest)"
    model["DataTypes"] = [{"name": "ActorRequest", "kind": "valueObject", "fields": ["memberId : String"], "fieldRefs": ["field-actor-id"]}]
    model["Collaborations"][0]["calls"][1]["argumentBindings"][0].update({"sourceRef": "value#val-1"})
    response = {"status": "fail", "finding": "The authenticated context source does not establish the required request details DTO.", "mappings": []}

    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: response)
    findings, evidence = subject.review_public_contract_closure(model, build_scenario_index(scenario))

    assert findings
    assert "does not establish" in evidence["verdicts"][0]["finding"]


def test_review_payload_lists_authenticated_subject_identifier_value() -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    scenario = _scenario("authenticated_actor_context")
    identify_ref = _authenticated_identify_obligations(scenario)
    payload = subject._review_payload(build_scenario_index(scenario), _model(), build_scenario_index(scenario).use_cases[0])

    assert payload["requiredValueCatalog"][0]["sourceRef"] == "value#val-1"
    assert payload["requiredValueCatalog"][0]["identityObligationRef"] == identify_ref
    assert payload["requiredValueCatalog"][0]["availability"] == "server_context"


def _identity_case(source_kind: str, source_ref: str | None = None) -> tuple[dict, dict]:
    scenario = _scenario()
    contract = scenario["use_case_specs"][0]["public_contract"]
    contract["required_values"] = []
    identify = {
        "obligation_ref": "identify-1", "obligation": "identify",
        "identity_source_kind": source_kind, "subject": "student", "subject_ref": "ACT1",
        "requirement_ids": ["R-ID"],
    }
    if source_kind == "authenticated_context":
        identify["source_authenticate_obligation_ref"] = "auth-1"
        contract["identity_obligations"] = [
            {"obligation": "authenticate", "obligation_ref": "auth-1", "subject_ref": "ACT1", "requirement_ids": ["R-AUTH"]},
            identify,
        ]
        contract["required_values"] = [{
            "value_ref": "val-identity", "name": "student id",
            "source": "authenticated_actor_context", "value_type": "identifier",
            "usage": "control", "requirement_ids": ["R-ID"],
            "identity_obligation_ref": "identify-1",
        }]
    else:
        contract["identity_obligations"] = [identify]
    model = _model()
    model["Collaborations"][0]["calls"][0]["stepRefs"] = ["UC1:main:1"]
    model["Collaborations"][0]["calls"][1]["argumentBindings"][0].update({
        "sourceRef": source_ref or "UC1:main:1#param-details",
        "sourceKind": {"caller_input": "use_case_input", "authenticated_context": "authenticated_context", "system_result": "earlier_step_result"}.get(source_kind, ""),
    })
    if source_kind == "authenticated_context":
        model["Classes"][1]["operations"][0]["parameters"][0].update({
            "type": "String", "requiredValueRef": "val-identity",
        })
        model["Classes"][1]["operations"][0]["operationId"] = "SubmitControl::submit(details:String)"
        model["Collaborations"][0]["calls"][1]["receiverOperationId"] = "SubmitControl::submit(details:String)"
    mapping = _pass_response()["mappings"][0]
    if source_kind == "authenticated_context":
        identity_mapping = dict(mapping, obligationId="identity:2")
        mapping.update({"obligationId": "value:1"})
        mappings = [mapping, identity_mapping]
    else:
        mapping.update({"obligationId": "identity:1"})
        mappings = [mapping]
    if source_kind == "authenticated_context":
        for item in mappings:
            item["operationId"] = "SubmitControl::submit(details:String)"
    return scenario, {"status": "pass", "finding": "", "mappings": mappings}


def test_identify_caller_input_requires_canonical_actor_binding(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject
    scenario, payload = _identity_case("caller_input")
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: payload)
    assert subject.review_public_contract_closure(_identity_model("caller_input", "UC1:main:1#param-details"), build_scenario_index(scenario))[0] == []


def _model_with_root_step() -> dict:
    model = _model()
    model["Collaborations"][0]["calls"][0]["stepRefs"] = ["UC1:main:1"]
    return model


def test_actor_input_sources_allow_only_declared_root_dto_fields() -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    model = _model_with_root_step()
    model["DataTypes"] = [{
        "name": "RequestData", "kind": "valueObject",
        "fields": ["memberId : UUID"], "fieldRefs": ["field-member-id"],
    }]
    use_case = build_scenario_index(_scenario()).use_case("UC1")

    sources = subject._actor_input_sources(model, use_case)

    assert "UC1:main:1#param-details.field-member-id" in sources
    assert "call-boundary#param-details.field-member-id" in sources
    assert "call-control#param-details-control.field-member-id" not in sources
    assert "UC1:main:1#param-details.invented-field" not in sources


def _waitlist_source_field_model(source_ref: str) -> dict:
    model = _model_with_root_step()
    boundary = model["Classes"][0]["operations"][0]
    boundary.update({
        "operationId": "WaitlistBoundary::cancel(request:CancelWaitlistRequest)",
        "name": "cancel",
        "parameters": [{
            "name": "request", "type": "CancelWaitlistRequest",
            "stableRef": "param-cancel-request",
        }],
    })
    control = model["Classes"][1]["operations"][0]
    control.update({
        "operationId": "WaitlistControl::cancel(waitlistEntryId:UUID)",
        "name": "cancel",
        "parameters": [{
            "name": "waitlistEntryId", "type": "UUID",
            "stableRef": "param-waitlist-entry", "requiredValueRef": "val-1",
        }],
    })
    calls = model["Collaborations"][0]["calls"]
    calls[0]["receiverOperationId"] = boundary["operationId"]
    calls[1]["receiverOperationId"] = control["operationId"]
    calls[1]["argumentBindings"] = [{
        "parameter": "waitlistEntryId", "sourceRef": source_ref,
    }]
    model["DataTypes"] = [{
        "name": "CancelWaitlistRequest", "kind": "valueObject",
        "fields": ["waitlistEntryId : UUID"], "fieldRefs": ["field-waitlist-entry"],
    }]
    return model


@pytest.mark.parametrize(
    ("source_ref", "field_ref", "valid"),
    [
        ("call-boundary#param-cancel-request.field-waitlist-entry", "field-waitlist-entry", True),
        ("call-control#param-cancel-request.field-waitlist-entry", "field-waitlist-entry", False),
        ("call-boundary#param-cancel-request.unrelated-field", "unrelated-field", False),
    ],
)
def test_verifier_accepts_only_exact_root_dto_source_field(
    source_ref: str, field_ref: str, valid: bool,
) -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    model = _waitlist_source_field_model(source_ref)
    response = _pass_response()
    response["mappings"][0].update({
        "operationId": "WaitlistControl::cancel(waitlistEntryId:UUID)",
        "parameterRef": "param-waitlist-entry", "fieldRef": field_ref,
    })
    problem = subject._verify_response(
        model, build_scenario_index(_scenario()).use_case("UC1"),
        subject._ReviewResponse.model_validate(response),
    )

    assert (not problem) is valid


def _identity_model(source_kind: str, source_ref: str) -> dict:
    model = _model_with_root_step()
    model["Collaborations"][0]["calls"][1]["argumentBindings"][0].update({
        "sourceRef": source_ref,
        "sourceKind": {"caller_input": "use_case_input", "authenticated_context": "authenticated_context", "system_result": "earlier_step_result"}.get(source_kind, ""),
    })
    if source_kind == "authenticated_context":
        model["Classes"][1]["operations"][0]["parameters"][0].update({
        "type": "String", "requiredValueRef": "val-identity",
        })
        model["Classes"][1]["operations"][0]["operationId"] = "SubmitControl::submit(details:String)"
        model["Collaborations"][0]["calls"][1]["receiverOperationId"] = "SubmitControl::submit(details:String)"
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
    scenario, payload = _identity_case("authenticated_context", "value#wrong-auth")
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: payload)
    findings, evidence = subject.review_public_contract_closure(_identity_model("authenticated_context", "value#wrong-auth"), build_scenario_index(scenario))
    assert findings and "linked to this identity obligation" in evidence["verdicts"][0]["finding"]

    scenario, payload = _identity_case("authenticated_context", "value#val-identity")
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: payload)
    assert subject.review_public_contract_closure(_identity_model("authenticated_context", "value#val-identity"), build_scenario_index(scenario))[0] == []


def test_identify_authenticated_context_accepts_exact_source_ref_without_sidecar_kind(monkeypatch) -> None:
    from app.design.services.class_diagram import public_contract_review as subject

    scenario, payload = _identity_case("authenticated_context", "value#val-identity")
    model = _identity_model("authenticated_context", "value#val-identity")
    payload["mappings"][0].update({"operationId": "SubmitControl::submit(details:String)"})
    binding = model["Collaborations"][0]["calls"][1]["argumentBindings"][0]
    binding.pop("sourceKind")
    monkeypatch.setattr(subject, "parse_structured", lambda *_args, **_kwargs: payload)
    assert subject.review_public_contract_closure(model, build_scenario_index(scenario))[0] == []


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


def test_class_gate_reuses_deterministic_validation_for_unchanged_digest(monkeypatch) -> None:
    from dataclasses import replace

    from app.design.graphs import subgraphs
    from app.design.nodes.artifact import check_node
    from app.validation import Finding, ValidationReport

    calls = 0

    def validate(*_args):
        nonlocal calls
        calls += 1
        return ValidationReport(
            status="findings",
            findings=(Finding("class.test", "unchanged finding", "UC1"),),
        )

    monkeypatch.setattr(subgraphs, "validate_class_model", validate)
    monkeypatch.setattr(subgraphs, "_stored_class_model", lambda value: value)
    monkeypatch.setattr(
        subgraphs,
        "review_public_contract_closure",
        lambda *_args, **_kwargs: ([], {"status": "pass"}),
    )
    spec = replace(subgraphs.CLASS_DIAGRAM_SPEC, repair=None)
    model = _model()
    model["Collaborations"][0]["useCaseIds"] = ["UC1"]
    result = check_node(spec)({
        "extracted_bce_classes": model,
        "usecase_spec": _scenario(),
    })

    assert calls == 1
    assert "unchanged finding" in result["class_diagram_check"]["findings"][0]
    assert result["class_diagram_check"]["finding_details"][0]["rule_id"] == "class.test"
    assert result["class_diagram_check"]["semanticEvidence"] == {"status": "skipped_static_findings"}


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
