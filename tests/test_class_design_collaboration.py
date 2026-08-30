"""클래스 호출 관계와 실제 값의 출처를 검사한다."""
from __future__ import annotations

import pytest

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram import collaboration, service
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
    operation_fragment,
    patch_class_design_parser,
    single_use_case,
)


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
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(single_use_case()))
    model = model.model_dump(by_alias=True)

    assert len(model["Collaborations"]) == 1
    calls = model["Collaborations"][0]["calls"]
    assert calls[1]["argumentBindings"] == [{
        "parameter": "request",
        "sourceRef": "UC1::call:1#request",
    }]
    assert all(item.get("type") != "Dependency" for item in model["Relationships"])


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
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(single_use_case()))
    model = model.model_dump(by_alias=True)

    runtime_call = model["Collaborations"][0]["calls"][2]
    assert runtime_call["argumentBindings"] == [{
        "parameter": "registeredAt",
        "sourceRef": "runtime#currentDateTime",
    }]


def test_structured_parameter_is_derived_from_upstream_fields(monkeypatch):
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
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(single_use_case()))
    model = model.model_dump(by_alias=True)
    binding = model["Collaborations"][0]["calls"][2]["argumentBindings"][0]

    assert binding == {
        "parameter": "details",
            "sourceRef": (
                "derived#RegistrationDetails("
                "value=UC1::call:2#request.value)"
            ),
    }


def test_optional_results_use_explicit_unwrap_sources():
    model = {
        "Classes": [
            {
                "className": "Student",
                "stereotype": "Entity",
                "use_case_ids": ["UC1"],
                "fields": ["studentId : uuid"],
                "identifier": ["studentId"],
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
        ],
    })

    collaboration_model = collaboration.materialize(
        build_scenario_index(single_use_case()),
        BCEModel.model_validate(model),
        build_scenario_index(single_use_case()).use_case("UC1"),
        plan,
    ).model_dump(by_alias=True)

    assert collaboration_model["calls"][3]["argumentBindings"] == [
        {
            "parameter": "student",
            "sourceRef": "UC1::call:2#result.unwrap",
        },
        {
            "parameter": "failureCode",
            "sourceRef": "UC1::call:3#result.failureCode.unwrap",
        },
    ]

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
    with pytest.raises(ValueError, match="entity -> control"):
        collaboration.materialize(
            build_scenario_index(single_use_case()),
            BCEModel.model_validate(model),
            build_scenario_index(single_use_case()).use_case("UC1"),
            entity_to_control,
        )

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
    expected_candidates = [
        "UC1::call:1#request.sourceUnitCode",
        "UC1::call:1#request.targetUnitCode",
    ]

    def select_source(_group, ambiguous):
        location = "UC1::call:2#code"
        assert ambiguous == {location: expected_candidates}
        return {location: expected_candidates[0]}

    monkeypatch.setattr(collaboration, "select_ambiguous_bindings", select_source)
    result = collaboration.materialize(
        build_scenario_index(single_use_case()),
        model,
        build_scenario_index(single_use_case()).use_case("UC1"),
        plan,
    )

    assert result.calls[1].argument_bindings[0].source_ref == expected_candidates[0]
