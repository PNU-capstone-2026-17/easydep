"""클래스 호출 관계와 실제 값의 출처를 검사한다."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram import collaboration, generation, service
from app.design.services.class_diagram.identity import materialize_pre_collaboration_refs
from app.design.services.class_diagram.validation.collaboration import (
    CollaborationContext,
    _collaboration_bindings,
)
from app.design.services.class_diagram.proposals import (
    CallPlanProposal,
    CombinedUnitProposal,
    InventoryProposal,
    OperationFragment,
)
from app.design.services.class_diagram.scenario import build_scenario_index
from tests.class_design_fixtures import (
    call_plan,
    combined_unit_proposal,
    inventory_proposal,
    multiple_entry_use_case,
    operation_fragment,
    patch_class_design_parser,
    single_use_case,
)


def _first_binding_choice(messages, schema):
    """Return the first offered source for deterministic service fixtures."""
    payload = json.loads(messages[-1]["content"])
    return {
        choice["choice"]: choice["candidates"][0]
        for choice in payload["choices"]
    }


def _reject_semantic_binding_choice(messages, schema):
    payload = json.loads(messages[-1]["content"])
    return {
        choice["choice"]: (
            "NO_MATCH" if "NO_MATCH" in choice["candidates"]
            else choice["candidates"][0]
        )
        for choice in payload["choices"]
    }


def _select_repaired_request_or_no_match(messages, schema):
    payload = json.loads(messages[-1]["content"])
    selected = {}
    for choice in payload["choices"]:
        candidates = choice["candidates"]
        target_parameter = choice["target"]["parameter"]
        request_source = next((
            value
            for value, detail in zip(candidates, choice.get("candidateDetails", []))
            if detail.get("sourceParameter") == "request"
        ), None)
        selected[choice["choice"]] = (
            request_source if target_parameter == "request" and request_source
            else "NO_MATCH" if "NO_MATCH" in candidates
            else candidates[0]
        )
    return selected


def test_call_plan_schema_rejects_cross_actor_entry_operation(monkeypatch):
    """A plan repair may not move a valid operation into another actor entry."""

    index = build_scenario_index(multiple_entry_use_case())
    use_case = index.use_case("UC1")
    model = BCEModel.model_validate({
        "Classes": [
            {
                "className": "RequestBoundary",
                "stereotype": "Boundary",
                "use_case_ids": ["UC1"],
                "operations": [
                    {
                        "operationId": "ignored",
                        "name": "submit",
                        "parameters": [],
                        "returnType": "void",
                        "stepRefs": ["UC1:main:1"],
                    },
                    {
                        "operationId": "ignored",
                        "name": "requestReceipt",
                        "parameters": [],
                        "returnType": "void",
                        "stepRefs": ["UC1:main:3"],
                    },
                ],
            },
            {
                "className": "RequestControl",
                "stereotype": "Control",
                "use_case_ids": ["UC1"],
                "operations": [
                    {
                        "operationId": "ignored",
                        "name": "process",
                        "parameters": [],
                        "returnType": "void",
                        "stepRefs": ["UC1:main:2"],
                    },
                    {
                        "operationId": "ignored",
                        "name": "deliverReceipt",
                        "parameters": [],
                        "returnType": "void",
                        "stepRefs": ["UC1:main:4"],
                    },
                ],
            },
        ],
        "DataTypes": [],
        "Relationships": [],
        "Collaborations": [],
    })
    valid_plan = {
        "calls": [
            {"receiverOperationId": "RequestBoundary::submit()", "parentCallIndex": None},
            {"receiverOperationId": "RequestControl::process()", "parentCallIndex": 1},
            {"receiverOperationId": "RequestBoundary::requestReceipt()", "parentCallIndex": None},
            {"receiverOperationId": "RequestControl::deliverReceipt()", "parentCallIndex": 3},
        ],
    }

    def fake_parse(messages, schema, **_kwargs):
        payload = json.loads(messages[-1]["content"])
        assert payload["actorEntries"] == [
            {
                "actorStepRef": "UC1:main:1",
                "actor": "Member",
                "requiredStepRefs": ["UC1:main:1", "UC1:main:2"],
                "eligibleReceiverOperationIds": [
                    "RequestBoundary::submit()",
                    "RequestControl::process()",
                ],
            },
            {
                "actorStepRef": "UC1:main:3",
                "actor": "Member",
                "requiredStepRefs": ["UC1:main:3", "UC1:main:4"],
                "eligibleReceiverOperationIds": [
                    "RequestBoundary::requestReceipt()",
                    "RequestControl::deliverReceipt()",
                ],
            },
        ]
        schema.model_validate(valid_plan)
        invalid_plan = {
            "calls": [
                *valid_plan["calls"][:3],
                {
                    "receiverOperationId": "RequestControl::process()",
                    "parentCallIndex": 3,
                },
            ],
        }
        with pytest.raises(ValidationError, match="actor entry resolved"):
            schema.model_validate(invalid_plan)
        return valid_plan

    monkeypatch.setattr(collaboration, "parse_structured", fake_parse)
    assert collaboration.propose_call_plan(index, model, use_case).model_dump(
        by_alias=True,
    ) == valid_plan


def test_vertical_service_persists_calls_and_derives_parameter_provenance(monkeypatch):
    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            return combined_unit_proposal()
        if schema is OperationFragment:
            return operation_fragment()
        if issubclass(schema, CallPlanProposal):
            return call_plan()
        if schema.__name__ == "BindingChoices":
            return _first_binding_choice(_messages, schema)
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(single_use_case()))
    model = model.model_dump(by_alias=True)

    assert len(model["Collaborations"]) == 1
    calls = model["Collaborations"][0]["calls"]
    boundary_operation = next(
        operation
        for owner in model["Classes"]
        for operation in owner["operations"]
        if operation["name"] == "submit"
    )
    assert calls[1]["argumentBindings"] == [{
        "parameter": "request",
        "sourceRef": f"{calls[0]['stableId']}#{boundary_operation['parameters'][0]['stableRef']}",
    }]
    assert all(item.get("type") != "Dependency" for item in model["Relationships"])


def test_missing_source_repairs_owning_unit_without_call_plan_retry(monkeypatch):
    """A finite-source gap is repaired at the operation/call unit seam once."""

    combined_calls = 0

    def fake_parse(messages, schema, **_kwargs):
        nonlocal combined_calls
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            combined_calls += 1
            if combined_calls == 2:
                payload = json.loads(messages[-1]["content"])
                assert payload["repairContext"]["code"] == "BINDING_SOURCE_UNAVAILABLE"
                assert payload["repairContext"]["callIndex"] == 1
                return combined_unit_proposal()
            proposal = combined_unit_proposal()
            proposal["fragment"] = operation_fragment(unsourceable=True)
            return proposal
        if issubclass(schema, CallPlanProposal):
            raise TypeError("a missing source must not trigger a call-plan retry")
        if schema.__name__ == "BindingChoices":
            return _select_repaired_request_or_no_match(messages, schema)
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(single_use_case()))

    assert combined_calls == 2
    assert model.Collaborations[0].calls[1].argument_bindings[0].parameter == "request"


def test_binding_retry_guidance_reaches_owning_operation_repair(monkeypatch):
    combined_calls = 0

    def fake_parse(messages, schema, **_kwargs):
        nonlocal combined_calls
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            combined_calls += 1
            payload = json.loads(messages[-1]["content"])
            assert payload["repairGuidance"] == "Use the captured request as the source."
            if combined_calls == 1:
                proposal = combined_unit_proposal()
                proposal["fragment"] = operation_fragment(unsourceable=True)
                return proposal
            assert payload["repairContext"]["code"] == "BINDING_SOURCE_UNAVAILABLE"
            return combined_unit_proposal()
        if issubclass(schema, CallPlanProposal):
            return call_plan()
        if schema.__name__ == "BindingChoices":
            return _select_repaired_request_or_no_match(messages, schema)
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    service.generate_class_model(
        build_scenario_index(single_use_case()),
        repair_guidance="  Use the captured request as the source.  ",
    )
    assert combined_calls == 2


def test_unchanged_missing_source_stalls_after_one_owning_unit_repair(monkeypatch):
    """The same structural source slot cannot regenerate indefinitely."""

    combined_calls = 0

    def fake_parse(_messages, schema, **_kwargs):
        nonlocal combined_calls
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            combined_calls += 1
            proposal = combined_unit_proposal()
            proposal["fragment"] = operation_fragment(unsourceable=True)
            return proposal
        if issubclass(schema, CallPlanProposal):
            raise TypeError("a missing source must not trigger a call-plan retry")
        if schema.__name__ == "BindingChoices":
            return _reject_semantic_binding_choice(_messages, schema)
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    with pytest.raises(
        generation.ClassBindingStalled, match="BINDING_SOURCE_UNAVAILABLE",
    ) as caught:
        service.generate_class_model(build_scenario_index(single_use_case()))

    assert combined_calls == 2
    assert caught.value.unit_id == "UC1"
    assert caught.value.repair_context["code"] == "BINDING_SOURCE_UNAVAILABLE"
    assert caught.value.repair_context["useCaseId"] == "UC1"
    assert caught.value.repair_context["parameter"]["type"]
    assert caught.value.repair_context["availableSources"]
    assert caught.value.repair_context["callIndex"] == 1


def test_temporal_parameter_uses_explicit_runtime_clock_when_no_upstream_value(monkeypatch):
    inventory_candidate = inventory_proposal()
    inventory_candidate["items"].append({
        "name": "Registration",
        "kind": "Entity",
        "description": "Accepted registration",
        "fields": [
            {"name": "registrationId", "type": "uuid"},
            {"name": "registeredAt", "type": "localdatetime"},
        ],
        "identifier": ["registrationId"],
        "values": [],
        "useCaseIds": ["UC1"],
    })
    fragment = operation_fragment()
    fragment["Classes"].append({
        "className": "Registration",
        "operations": [{
            "name": "create",
            "parameters": [{"name": "registeredAt", "type": "localdatetime"}],
            "returnType": "Registration",
            "stepRefs": ["UC1:main:2"],
        }],
    })
    plan = call_plan()
    plan["calls"].append({
        "receiverOperationId": "Registration::create(registeredAt:localdatetime)",
        "parentCallIndex": 2,
    })

    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_candidate
        if schema is CombinedUnitProposal:
            proposal = combined_unit_proposal()
            proposal["fragment"] = fragment
            proposal["calls"].append({
                "operationRef": "Registration.create",
                "parentCallIndex": 2,
            })
            return proposal
        if schema is OperationFragment:
            return fragment
        if issubclass(schema, CallPlanProposal):
            return plan
        if schema.__name__ == "BindingChoices":
            return _first_binding_choice(_messages, schema)
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(single_use_case()))
    model = model.model_dump(by_alias=True)

    runtime_call = model["Collaborations"][0]["calls"][2]
    assert runtime_call["argumentBindings"] == [{
        "parameter": "registeredAt",
        "sourceRef": "runtime#currentDateTime",
    }]


def test_structured_parameter_is_not_inferred_from_matching_field_names(monkeypatch):
    inventory_candidate = inventory_proposal()
    inventory_candidate["items"].append({
        "name": "Registration",
        "kind": "Entity",
        "description": "Accepted registration",
        "fields": [{"name": "registrationId", "type": "uuid"}],
        "identifier": ["registrationId"],
        "values": [],
        "useCaseIds": ["UC1"],
    })
    fragment = operation_fragment()
    fragment["DataTypes"].append({
        "name": "RegistrationDetails",
        "kind": "valueObject",
        "fields": [{"name": "value", "type": "String"}],
        "values": [],
    })
    fragment["Classes"].append({
        "className": "Registration",
        "operations": [{
            "name": "create",
            "parameters": [{"name": "details", "type": "RegistrationDetails"}],
            "returnType": "Registration",
            "stepRefs": ["UC1:main:2"],
        }],
    })
    plan = call_plan()
    plan["calls"].append({
        "receiverOperationId": "Registration::create(details:RegistrationDetails)",
        "parentCallIndex": 2,
    })

    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_candidate
        if schema is CombinedUnitProposal:
            proposal = combined_unit_proposal()
            proposal["fragment"] = fragment
            proposal["calls"].append({
                "operationRef": "Registration.create",
                "parentCallIndex": 2,
            })
            return proposal
        if schema is OperationFragment:
            return fragment
        if issubclass(schema, CallPlanProposal):
            return plan
        if schema.__name__ == "BindingChoices":
            return _first_binding_choice(_messages, schema)
        raise AssertionError(schema)

    index = build_scenario_index(single_use_case())
    calls = [
        {"callId": "UC1::call:1", "parentCallId": None,
         "receiverOperationId": "RegistrationBoundary::submit(value:String)"},
        {"callId": "UC1::call:2", "parentCallId": "UC1::call:1",
         "receiverOperationId": "RegistrationControl::register(details:RegistrationDetails)"},
    ]
    operations = {
        "RegistrationBoundary::submit(value:String)": {
            "parameters": [{"name": "value", "type": "String"}],
        },
        "RegistrationControl::register(details:RegistrationDetails)": {
            "parameters": [{"name": "details", "type": "RegistrationDetails"}],
        },
    }
    candidates = collaboration._binding_candidates(
        {"Classes": [], "DataTypes": [{
            "name": "RegistrationDetails", "kind": "valueObject",
            "fields": ["value : String"],
        }]}, index.use_case("UC1"), None, False, calls, 1,
        {"name": "details", "type": "RegistrationDetails"}, operations,
    )
    assert candidates == []


def test_optional_results_use_explicit_unwrap_sources(monkeypatch):
    model = {
        "Classes": [
            {
                "className": "Student",
                "stereotype": "Entity",
                "use_case_ids": ["UC1"],
                "fields": ["id : uuid"],
                "identifier": ["id"],
                "operations": [],
            },
            {
                "className": "RequestBoundary",
                "stereotype": "Boundary",
                "use_case_ids": ["UC1"],
                "fields": [],
                "identifier": [],
                "operations": [{
                    "operationId": "RequestBoundary::start()",
                    "name": "start",
                    "parameters": [],
                    "returnType": "void",
                    "stepRefs": ["UC1:main:1"],
                }],
            },
            {
                "className": "StudentLookup",
                "stereotype": "Control",
                "use_case_ids": ["UC1"],
                "fields": [],
                "identifier": [],
                "operations": [{
                    "operationId": "StudentLookup::find()",
                    "name": "find",
                    "parameters": [],
                    "returnType": "optional<Student>",
                    "stepRefs": ["UC1:main:2"],
                }],
            },
            {
                "className": "Registration",
                "stereotype": "Entity",
                "use_case_ids": ["UC1"],
                "fields": [],
                "identifier": [],
                "operations": [{
                    "operationId": (
                        "Registration::create("
                        "student:Student,failureCode:ValidationFailureCode)"
                    ),
                    "name": "create",
                    "parameters": [
                        {"name": "student", "type": "Student"},
                        {
                            "name": "failureCode",
                            "type": "ValidationFailureCode",
                        },
                    ],
                    "returnType": "Registration",
                    "stepRefs": ["UC1:main:2"],
                }],
            },
            {
                "className": "RegistrationPolicy",
                "stereotype": "Entity",
                "use_case_ids": ["UC1"],
                "fields": [],
                "identifier": [],
                "operations": [{
                    "operationId": "RegistrationPolicy::validate()",
                    "name": "validate",
                    "parameters": [],
                    "returnType": "ValidationResult",
                    "stepRefs": ["UC1:main:2"],
                }],
            },
            {
                "className": "Session",
                "stereotype": "Entity",
                "use_case_ids": ["UC1"],
                "fields": ["id : uuid", "studentId : uuid"],
                "identifier": ["id"],
                "operations": [{
                    "operationId": "Session::create(studentId:uuid)",
                    "name": "create",
                    "parameters": [{"name": "studentId", "type": "uuid"}],
                    "returnType": "Session",
                    "stepRefs": ["UC1:main:2"],
                }],
            },
        ],
        "DataTypes": [
            {
                "name": "ValidationResult",
                "kind": "valueObject",
                "fields": [
                    "failureCode : optional<ValidationFailureCode>",
                ],
            },
            {
                "name": "ValidationFailureCode",
                "kind": "enumeration",
                "values": ["NOT_ELIGIBLE"],
            },
        ],
        "Relationships": [],
        "Collaborations": [],
    }
    accepted_model = materialize_pre_collaboration_refs(None, BCEModel.model_validate(model))
    model = accepted_model.model_dump(by_alias=True)
    plan = CallPlanProposal.model_validate({
        "calls": [
            {"receiverOperationId": "RequestBoundary::start()", "parentCallIndex": None},
            {"receiverOperationId": "StudentLookup::find()", "parentCallIndex": 1},
            {
                "receiverOperationId": "RegistrationPolicy::validate()",
                "parentCallIndex": 2,
            },
            {
                "receiverOperationId": (
                    "Registration::create("
                    "student:Student,failureCode:ValidationFailureCode)"
                ),
                "parentCallIndex": 3,
            },
            {
                "receiverOperationId": "Session::create(studentId:UUID)",
                "parentCallIndex": 2,
            },
        ],
    })

    monkeypatch.setattr(
        collaboration, "parse_structured",
        lambda messages, schema, **_kwargs: _first_binding_choice(messages, schema),
    )
    collaboration_model = collaboration.materialize(
        build_scenario_index(single_use_case()),
        accepted_model,
        build_scenario_index(single_use_case()).use_case("UC1"),
        plan,
    ).model_dump(by_alias=True)

    calls = collaboration_model["calls"]
    data_type_refs = {
        item.name: dict(zip(
            [field.split(":", 1)[0].strip() for field in item.fields], item.field_refs,
        ))
        for item in accepted_model.DataTypes
    }
    class_field_refs = {
        item.class_name: dict(zip(
            [field.split(":", 1)[0].strip() for field in item.fields], item.field_refs,
        ))
        for item in accepted_model.Classes
    }
    assert calls[3]["argumentBindings"] == [
        {
            "parameter": "student",
            "sourceRef": f"{calls[1]['stableId']}#result.unwrap",
        },
        {
            "parameter": "failureCode",
            "sourceRef": (
                f"{calls[2]['stableId']}#result."
                f"{data_type_refs['ValidationResult']['failureCode']}.unwrap"
            ),
        },
    ]
    assert calls[4]["argumentBindings"] == [{
        "parameter": "studentId",
        "sourceRef": (
            f"{calls[1]['stableId']}#result.unwrap."
            f"{class_field_refs['Student']['id']}"
        ),
    }]

    missing_source_model = json.loads(json.dumps(model))
    registration = missing_source_model["Classes"][3]["operations"][0]
    registration["parameters"].append({"name": "instructorId", "type": "String"})
    registration["operationId"] = (
        "Registration::create(student:Student,"
        "failureCode:ValidationFailureCode,instructorId:String)"
    )
    missing_source_plan = plan.model_copy(deep=True)
    missing_source_plan.calls[3].receiver_operation_id = registration["operationId"]

    with pytest.raises(collaboration.BindingSourceViolation) as source_error:
        collaboration.materialize(
            build_scenario_index(single_use_case()),
            BCEModel.model_validate(missing_source_model),
            build_scenario_index(single_use_case()).use_case("UC1"),
            missing_source_plan,
        )
    assert source_error.value.repair_context == {
        "code": "BINDING_SOURCE_UNAVAILABLE",
        "useCaseId": "UC1",
        "location": "UC1::call:4#instructorId",
        "receiverOperationId": registration["operationId"],
        "parameter": {"name": "instructorId", "type": "String"},
        "searchedSourceScopes": [
            "ancestor-call-parameter",
            "earlier-root-input",
            "previous-call-result",
            "derived-structured-value",
            "runtime-value",
        ],
        "availableSources": [],
        "instruction": (
            "No finite compatible source exists. Change this operation so every "
            "parameter is supplied by an entry input, evidence-backed trusted "
            "context, an earlier result, a supported runtime value, or a "
            "derivable structured value."
        ),
    }

    entity_to_control = CallPlanProposal.model_validate({
        "calls": [
            {"receiverOperationId": "RequestBoundary::start()", "parentCallIndex": None},
            {"receiverOperationId": "StudentLookup::find()", "parentCallIndex": 1},
            {
                "receiverOperationId": "RegistrationPolicy::validate()",
                "parentCallIndex": 2,
            },
            {"receiverOperationId": "StudentLookup::find()", "parentCallIndex": 3},
        ],
    })
    with pytest.raises(collaboration.CallPlanViolation, match="entity -> control") as caught:
        collaboration.materialize(
            build_scenario_index(single_use_case()),
            BCEModel.model_validate(model),
            build_scenario_index(single_use_case()).use_case("UC1"),
            entity_to_control,
        )
    assert caught.value.repair_context["location"] == "calls[3].parentCallIndex"
    assert caught.value.repair_context["allowedParentCallIndexes"] == [2, 1]

    def select_control_parent(messages, _schema, **_kwargs):
        payload = json.loads(messages[-1]["content"])
        assert [item["selection"] for item in payload["alternatives"]] == [
            "parent:2",
            "parent:1",
        ]
        return {"selection": "parent:2"}

    monkeypatch.setattr(collaboration, "parse_structured", select_control_parent)
    repaired = collaboration.repair_communication_parent(
        build_scenario_index(single_use_case()),
        BCEModel.model_validate(model),
        build_scenario_index(single_use_case()).use_case("UC1"),
        entity_to_control,
        caught.value,
    )
    assert repaired is not None
    expected = entity_to_control.model_dump(by_alias=True)
    expected["calls"][3]["parentCallIndex"] = 2
    assert repaired.model_dump(by_alias=True) == expected
    collaboration.materialize(
        build_scenario_index(single_use_case()),
        BCEModel.model_validate(model),
        build_scenario_index(single_use_case()).use_case("UC1"),
        repaired,
    )

    unique_parent_plan = CallPlanProposal.model_validate({
        "calls": [
            {"receiverOperationId": "RequestBoundary::start()", "parentCallIndex": None},
            {"receiverOperationId": "StudentLookup::find()", "parentCallIndex": 1},
            {
                "receiverOperationId": "RegistrationPolicy::validate()",
                "parentCallIndex": 1,
            },
        ],
    })
    monkeypatch.setattr(
        collaboration, "propose_call_plan", lambda *_args, **_kwargs: unique_parent_plan
    )
    monkeypatch.setattr(
        collaboration, "parse_structured", lambda *_args, **_kwargs: {"selection": "parent:2"}
    )
    automatically_repaired = collaboration.process_use_case(
        build_scenario_index(single_use_case()),
        BCEModel.model_validate(model),
        build_scenario_index(single_use_case()).use_case("UC1"),
    )
    assert automatically_repaired.calls[2].parent_call_id == "UC1::call:2"

    same_boundary_response = CallPlanProposal.model_validate({
        "calls": [
            {"receiverOperationId": "RequestBoundary::start()", "parentCallIndex": None},
            {"receiverOperationId": "StudentLookup::find()", "parentCallIndex": 1},
            {"receiverOperationId": "RequestBoundary::start()", "parentCallIndex": 2},
        ],
    })
    with pytest.raises(ValueError, match="control -> boundary"):
        collaboration.materialize(
            build_scenario_index(single_use_case()),
            BCEModel.model_validate(model),
            build_scenario_index(single_use_case()).use_case("UC1"),
            same_boundary_response,
        )


def test_actor_flow_requires_control_handoff():
    """BCE 산출물이므로 actor 요청을 Boundary 안에서 끝내지는 않는다."""

    model = BCEModel.model_validate({
        "Classes": [{
            "className": "RequestBoundary",
            "stereotype": "Boundary",
            "use_case_ids": ["UC1"],
            "operations": [{
                "operationId": "RequestBoundary::submit(request:RequestData)",
                "name": "submit",
                "parameters": [{"name": "request", "type": "RequestData"}],
                "returnType": "RequestResult",
                "stepRefs": ["UC1:main:1", "UC1:main:2"],
            }],
        }],
        "DataTypes": [
            {
                "name": "RequestData",
                "kind": "valueObject",
                "fields": ["value : String"],
            },
            {
                "name": "RequestResult",
                "kind": "valueObject",
                "fields": ["accepted : Boolean"],
            },
        ],
        "Relationships": [],
        "Collaborations": [],
    })
    plan = CallPlanProposal.model_validate({
        "calls": [{
            "receiverOperationId": "RequestBoundary::submit(request:RequestData)",
            "parentCallIndex": None,
        }],
    })

    with pytest.raises(ValueError):
        collaboration.materialize(
            build_scenario_index(single_use_case()),
            model,
            build_scenario_index(single_use_case()).use_case("UC1"),
            plan,
        )


def test_scalar_parameter_can_use_same_typed_request_fields_with_different_names(monkeypatch):
    """매개변수 이름이 달라도 타입이 맞는 요청 필드를 실제 후보로 제공한다."""
    model = BCEModel.model_validate({
        "Classes": [
            {
                "className": "ConversionBoundary",
                "stereotype": "Boundary",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": (
                        "ConversionBoundary::convert(request:ConversionRequest)"
                    ),
                    "name": "convert",
                    "parameters": [{"name": "request", "type": "ConversionRequest"}],
                    "returnType": "ConversionResult",
                    "stepRefs": ["UC1:main:1"],
                }],
            },
            {
                "className": "ConversionControl",
                "stereotype": "Control",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ConversionControl::findUnit(code:String)",
                    "name": "findUnit",
                    "parameters": [{"name": "code", "type": "String"}],
                    "returnType": "ConversionResult",
                    "stepRefs": ["UC1:main:2"],
                }],
            },
        ],
        "DataTypes": [
            {
                "name": "ConversionRequest",
                "kind": "valueObject",
                "fields": [
                    "sourceUnitCode : String",
                    "targetUnitCode : String",
                ],
            },
            {
                "name": "ConversionResult",
                "kind": "valueObject",
                "fields": ["value : Decimal"],
            },
        ],
        "Relationships": [],
        "Collaborations": [],
    })
    model = materialize_pre_collaboration_refs(None, model)
    plan = CallPlanProposal.model_validate({
        "calls": [
            {
                "receiverOperationId": (
                    "ConversionBoundary::convert(request:ConversionRequest)"
                ),
                "parentCallIndex": None,
            },
            {
                "receiverOperationId": "ConversionControl::findUnit(code:String)",
                "parentCallIndex": 1,
            },
        ],
    })
    request_parameter = model.Classes[0].operations[0].parameters[0]
    request_fields = model.DataTypes[0].field_refs

    def select_source(_group, ambiguous, parameter_types, **_kwargs):
        location = "UC1::call:2#code"
        expected_candidates = [
            f"{_kwargs['calls'][0]['stableId']}#{request_parameter.stable_ref}.{field_ref}"
            for field_ref in request_fields
        ]
        assert ambiguous == {location: expected_candidates}
        assert parameter_types == {location: "String"}
        return {location: expected_candidates[0]}

    monkeypatch.setattr(collaboration, "select_ambiguous_bindings", select_source)
    result = collaboration.materialize(
        build_scenario_index(single_use_case()),
        model,
        build_scenario_index(single_use_case()).use_case("UC1"),
        plan,
    )

    assert result.calls[1].argument_bindings[0].source_ref == (
        f"{result.calls[0].stable_id}#{request_parameter.stable_ref}.{request_fields[0]}"
    )


def test_boundary_handoff_can_select_renamed_compatible_inputs(monkeypatch):
    """Actor-facing values remain finite candidates after a Control rename."""

    index = build_scenario_index(single_use_case())
    model = BCEModel.model_validate({
        "Classes": [
            {
                "className": "CalculatorBoundary",
                "stereotype": "Boundary",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ignored",
                    "name": "calculate",
                    "parameters": [
                        {"name": "firstValue", "type": "BigDecimal"},
                        {"name": "secondValue", "type": "BigDecimal"},
                        {"name": "operation", "type": "OperationType"},
                    ],
                    "returnType": "CalculationResult",
                    "stepRefs": ["UC1:main:1"],
                }],
            },
            {
                "className": "CalculationControl",
                "stereotype": "Control",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ignored",
                    "name": "executeOperation",
                    "parameters": [
                        {"name": "operand1", "type": "BigDecimal"},
                        {"name": "operand2", "type": "BigDecimal"},
                        {"name": "operationType", "type": "OperationType"},
                    ],
                    "returnType": "CalculationResult",
                    "stepRefs": ["UC1:main:2"],
                }],
            },
        ],
        "DataTypes": [
            {
                "name": "OperationType",
                "kind": "enumeration",
                "fields": [],
                "values": ["ADD", "SUBTRACT", "MULTIPLY", "DIVIDE"],
            },
            {
                "name": "CalculationResult",
                "kind": "valueObject",
                "fields": ["value : BigDecimal"],
                "values": [],
            },
        ],
        "Relationships": [],
        "Collaborations": [],
    })
    model = materialize_pre_collaboration_refs(None, model)
    plan = CallPlanProposal.model_validate({
        "calls": [
            {
                "receiverOperationId": (
                    "CalculatorBoundary::calculate(firstValue:BigDecimal,"
                    "secondValue:BigDecimal,operation:OperationType)"
                ),
                "parentCallIndex": None,
            },
            {
                "receiverOperationId": (
                    "CalculationControl::executeOperation(operand1:BigDecimal,"
                    "operand2:BigDecimal,operationType:OperationType)"
                ),
                "parentCallIndex": 1,
            },
        ],
    })
    boundary_parameters = model.Classes[0].operations[0].parameters
    result_field = model.DataTypes[1].field_refs[0]

    def select_source(_use_case, ambiguous, parameter_types, **_kwargs):
        call_ref = _kwargs["calls"][0]["stableId"]
        first = f"{call_ref}#{boundary_parameters[0].stable_ref}"
        second = f"{call_ref}#{boundary_parameters[1].stable_ref}"
        operation_type = f"{call_ref}#{boundary_parameters[2].stable_ref}"
        result_value = f"{call_ref}#result.{result_field}"
        assert ambiguous == {
            "UC1::call:2#operand1": [first, second, result_value],
            "UC1::call:2#operand2": [first, second, result_value],
            "UC1::call:2#operationType": [operation_type],
        }
        assert parameter_types == {
            "UC1::call:2#operand1": "BigDecimal",
            "UC1::call:2#operand2": "BigDecimal",
            "UC1::call:2#operationType": "OperationType",
        }
        return {
            "UC1::call:2#operand1": first,
            "UC1::call:2#operand2": second,
            "UC1::call:2#operationType": operation_type,
        }

    monkeypatch.setattr(collaboration, "select_ambiguous_bindings", select_source)
    result = collaboration.materialize(index, model, index.use_case("UC1"), plan)

    assert {
        binding.parameter: binding.source_ref
        for binding in result.calls[1].argument_bindings
    } == {
        "operand1": f"{result.calls[0].stable_id}#{boundary_parameters[0].stable_ref}",
        "operand2": f"{result.calls[0].stable_id}#{boundary_parameters[1].stable_ref}",
        "operationType": f"{result.calls[0].stable_id}#{boundary_parameters[2].stable_ref}",
    }


def test_binding_selection_allows_reusing_symmetric_source(monkeypatch):
    """A finite source can legitimately supply multiple parameters."""

    first = "UC1::call:1#firstValue"
    second = "UC1::call:1#secondValue"
    ambiguous = {
        "UC1::call:2#operand1": [first, second],
        "UC1::call:2#operand2": [second, first],
    }

    def fake_parse(_messages, schema, **_kwargs):
        parsed = schema.model_validate({"choice1": first, "choice2": first})
        assert parsed.choice1 == parsed.choice2 == first
        with pytest.raises(ValidationError):
            schema.model_validate({"choice1": first, "choice2": "unoffered"})
        return {"choice1": first, "choice2": first}

    monkeypatch.setattr(collaboration, "parse_structured", fake_parse)
    index = build_scenario_index(single_use_case())

    assert collaboration.select_ambiguous_bindings(
        index.use_case("UC1"),
        ambiguous,
        {
            "UC1::call:2#operand1": "BigDecimal",
            "UC1::call:2#operand2": "BigDecimal",
        },
    ) == {
        "UC1::call:2#operand1": first,
        "UC1::call:2#operand2": first,
    }


def test_binding_candidates_include_finite_type_compatible_ancestor_values():
    specification = single_use_case()
    specification["use_case_specs"][0]["preconditions"] = [
        "The student is authenticated."
    ]
    index = build_scenario_index(specification)
    target = {"name": "studentId", "type": "UUID"}
    calls = [
        {
            "callId": "UC1::call:1",
            "stableId": "call-source",
            "parentCallId": None,
            "receiverOperationId": "RegistrationControl::swap()",
        },
        {
            "callId": "UC1::call:2",
            "stableId": "call-target",
            "parentCallId": "UC1::call:1",
            "receiverOperationId": "Registration::find(studentId:UUID)",
        },
    ]
    operations = {
        "RegistrationControl::swap()": {
            "parameters": [
                {"name": "offeringId", "type": "UUID", "stableRef": "param-offering"},
                {"name": "studentId", "type": "UUID", "stableRef": "param-student"},
            ],
            "returnType": "SwapResult",
        },
        "Registration::find(studentId:UUID)": {
            "parameters": [{**target, "stableRef": "param-target"}],
            "returnType": "void",
        },
    }
    candidates = collaboration._binding_candidates(
        {
            "Classes": [],
            "DataTypes": [
                {
                    "name": "SwapResult",
                    "kind": "valueObject",
                    "fields": ["registrationId : UUID", "studentId : UUID"],
                    "fieldRefs": ["field-registration", "field-student"],
                }
            ],
        },
        index.use_case("UC1"),
        actor_step=None,
        is_root=False,
        calls=calls,
        call_index=1,
        parameter=target,
        operations=operations,
    )

    assert "call-source#param-offering" in candidates
    assert "call-source#result.field-registration" in candidates
    assert "call-source#param-student" in candidates
    assert "call-source#result.field-student" in candidates
    assert "UC1:precondition:1#studentId" not in candidates

    no_value_model = {"Classes": [], "DataTypes": []}
    uuid_handoff = [
        {
            "callId": "UC1::call:root",
            "parentCallId": None,
            "receiverOperationId": "RegistrationBoundary::submit()",
        },
        {
            "callId": "UC1::call:handoff",
            "parentCallId": "UC1::call:root",
            "receiverOperationId": "RegistrationControl::register(studentId:UUID)",
        },
    ]
    handoff_operations = {
        "RegistrationBoundary::submit()": {"stereotype": "boundary", "parameters": []},
        "RegistrationControl::register(studentId:UUID)": {
            "stereotype": "control", "parameters": [target]
        },
    }
    assert collaboration._binding_candidates(
        no_value_model, index.use_case("UC1"), None, False, uuid_handoff, 1, target,
        handoff_operations,
    ) == []

    nested_calls = [
        *uuid_handoff[:1],
        {
            "callId": "UC1::call:handoff",
            "parentCallId": "UC1::call:root",
            "receiverOperationId": "RegistrationControl::register()",
        },
        {
            "callId": "UC1::call:outbound",
            "parentCallId": "UC1::call:handoff",
            "receiverOperationId": "NotificationBoundary::notify()",
        },
        {
            "callId": "UC1::call:second-handoff",
            "parentCallId": "UC1::call:outbound",
            "receiverOperationId": "RegistrationControl::continueWith(studentContext:String)",
        },
    ]
    nested_target = {"name": "studentContext", "type": "String"}
    nested_operations = {
        "RegistrationBoundary::submit()": {"stereotype": "boundary", "parameters": []},
        "RegistrationControl::register()": {"stereotype": "control", "parameters": []},
        "NotificationBoundary::notify()": {"stereotype": "boundary", "parameters": []},
        "RegistrationControl::continueWith(studentContext:String)": {
            "stereotype": "control", "parameters": [nested_target]
        },
    }
    assert collaboration._binding_candidates(
        no_value_model, index.use_case("UC1"), None, False, nested_calls, 3,
        nested_target, nested_operations,
    ) == []


def test_control_to_entity_renamed_singleton_uses_finite_selection(monkeypatch):
    """A renamed Control value reaches Entity only after a bounded choice."""

    specification = single_use_case()
    specification["use_case_specs"][0]["preconditions"] = [
        "Authenticated StudentIdentity is available in trusted request context."
    ]
    specification["use_case_specs"][0]["public_contract"] = {
        "identity_obligations": [{
            "obligation_ref": "ob-student", "subject_ref": "sub-student",
            "subject": "display-only", "obligation": "authenticate",
            "requirement_ids": ["REQ-1"],
        }],
        "required_values": [],
    }
    index = build_scenario_index(specification)
    model = BCEModel.model_validate({
        "Classes": [
            {
                "className": "RegistrationBoundary",
                "stereotype": "Boundary",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ignored", "name": "submit", "parameters": [],
                    "returnType": "void", "stepRefs": ["UC1:main:1"],
                }],
            },
            {
                "className": "RegistrationControl",
                "stereotype": "Control",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ignored", "name": "swap",
                        "parameters": [{
                            "name": "currentStudent", "type": "StudentIdentity",
                            "obligationRef": "ob-student",
                        }],
                    "returnType": "void", "stepRefs": ["UC1:main:2"],
                }],
            },
            {
                "className": "Registration",
                "stereotype": "Entity",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ignored", "name": "swapOffering",
                    "parameters": [{"name": "student", "type": "StudentIdentity"}],
                    "returnType": "void", "stepRefs": ["UC1:main:2"],
                }],
            },
        ],
        "DataTypes": [{
            "name": "StudentIdentity", "kind": "valueObject", "fields": ["token : String"],
        }], "Relationships": [], "Collaborations": [],
    })
    model = materialize_pre_collaboration_refs(None, model)
    plan = CallPlanProposal.model_validate({"calls": [
        {"receiverOperationId": "RegistrationBoundary::submit()", "parentCallIndex": None},
        {
            "receiverOperationId": "RegistrationControl::swap(currentStudent:StudentIdentity)",
            "parentCallIndex": 1,
        },
        {
            "receiverOperationId": "Registration::swapOffering(student:StudentIdentity)",
            "parentCallIndex": 2,
        },
    ]})

    def select_source(_use_case, ambiguous, _parameter_types, **kwargs):
        location = "UC1::call:3#student"
        source_call = kwargs["calls"][1]
        source_parameter = model.Classes[1].operations[0].parameters[0]
        source_ref = f"{source_call['stableId']}#{source_parameter.stable_ref}"
        assert ambiguous[location] == [source_ref]
        assert source_ref in ambiguous[location]
        assert location in kwargs["semantic_locations"]
        return {key: values[0] for key, values in ambiguous.items()}

    monkeypatch.setattr(collaboration, "select_ambiguous_bindings", select_source)
    result = collaboration.materialize(index, model, index.use_case("UC1"), plan)

    assert result.calls[2].argument_bindings[0].source_ref == (
        f"{result.calls[1].stable_id}#{model.Classes[1].operations[0].parameters[0].stable_ref}"
    )


def test_binding_candidates_ignore_stale_ancestor_operation_during_validation():
    """A replaced operation is an invalid collaboration, not a KeyError."""

    index = build_scenario_index(single_use_case())
    calls = [
        {
            "callId": "UC1::call:1", "parentCallId": None,
            "receiverOperationId": "OldControl::swap(oldId:String)",
        },
        {
            "callId": "UC1::call:2", "parentCallId": "UC1::call:1",
            "receiverOperationId": "Registration::swap(registrationId:String)",
        },
    ]
    assert collaboration._binding_candidates(
        {"Classes": [], "DataTypes": []},
        index.use_case("UC1"),
        actor_step=None,
        is_root=False,
        calls=calls,
        call_index=1,
        parameter={"name": "registrationId", "type": "String"},
        operations={
            "Registration::swap(registrationId:String)": {
                "parameters": [{"name": "registrationId", "type": "String"}],
                "returnType": "void",
            },
        },
    ) == []


def test_stable_source_refs_do_not_change_when_parameter_names_change():
    index = build_scenario_index(single_use_case())
    calls = [{
        "callId": "UC1::call:1", "stableId": "call-stable-1",
        "receiverOperationId": "Boundary::send(value:String)",
        "parentCallId": None,
    }, {
        "callId": "UC1::call:2", "stableId": "call-stable-2",
        "receiverOperationId": "Control::receive(other:String)",
        "parentCallId": "UC1::call:1",
    }]
    source_parameter = {"name": "value", "type": "String", "stableRef": "param-stable-1"}
    target_parameter = {"name": "other", "type": "String", "stableRef": "param-stable-2"}
    operations = {
        "Boundary::send(value:String)": {
            "stereotype": "boundary", "parameters": [source_parameter],
        },
        "Control::receive(other:String)": {
            "stereotype": "control", "parameters": [target_parameter],
        },
    }
    candidates = lambda: collaboration._binding_candidates(
        {"Classes": [], "DataTypes": []}, index.use_case("UC1"), None, False,
        calls, 1, target_parameter, operations,
    )
    before = candidates()
    source_parameter["name"] = "renamedValue"
    target_parameter["name"] = "renamedTarget"

    assert before == candidates() == ["call-stable-1#param-stable-1"]


def test_collaboration_validator_rejects_wrong_stable_field_ref():
    index = build_scenario_index(single_use_case())
    model = BCEModel.model_validate({
        "Classes": [{
            "className": "RequestBoundary", "stereotype": "Boundary",
            "stableId": "class-boundary", "use_case_ids": ["UC1"],
            "operations": [{
                "operationId": "ignored", "stableId": "operation-boundary",
                "name": "send", "parameters": [{
                    "name": "request", "type": "RequestData", "stableRef": "param-request",
                }], "returnType": "void", "stepRefs": ["UC1:main:1"],
            }],
        }, {
            "className": "RequestControl", "stereotype": "Control",
            "stableId": "class-control", "use_case_ids": ["UC1"],
            "operations": [{
                "operationId": "ignored", "stableId": "operation-control",
                "name": "consume", "parameters": [{
                    "name": "payload", "type": "String", "stableRef": "param-payload",
                }], "returnType": "void", "stepRefs": ["UC1:main:2"],
            }],
        }],
        "DataTypes": [{
            "name": "RequestData", "stableId": "dtype-request",
            "kind": "valueObject", "fields": ["payload : String"],
            "fieldRefs": ["field-payload"],
        }],
        "Relationships": [], "Collaborations": [],
    })
    boundary_op, control_op = [
        operation for owner in model.Classes for operation in owner.operations
    ]
    collaboration_model = {
        "collaborationId": "UC1", "useCaseIds": ["UC1"], "entryActor": "Member",
        "calls": [{
            "callId": "UC1::call:1", "stableId": "call-stable-boundary",
            "receiverOperationId": boundary_op.operation_id,
            "stepRefs": ["UC1:main:1"],
            "argumentBindings": [{
                "parameter": "request", "sourceRef": "UC1:main:1#param-request",
            }],
        }, {
            "callId": "UC1::call:2", "stableId": "call-stable-control",
            "parentCallId": "UC1::call:1",
            "receiverOperationId": control_op.operation_id,
            "stepRefs": ["UC1:main:2"],
            "argumentBindings": [{
                "parameter": "payload",
                "sourceRef": "call-stable-boundary#param-request.field-wrong",
            }],
        }],
    }
    findings = _collaboration_bindings(
        collaboration_model,
        CollaborationContext(index, model.model_dump(by_alias=True), index.use_case("UC1")),
    )

    assert len(findings) == 1
    assert findings[0].location == "UC1::call:2#payload"
