"""Interaction-design collaborations and value provenance."""
from __future__ import annotations

from app.design.services.class_diagram import collaboration, service
from app.design.services.class_diagram.proposals import (
    CallPlanProposal,
    InventoryProposal,
    OperationFragment,
)
from app.design.services.class_diagram.scenario import build_scenario_index
from tests.class_design_fixtures import (
    call_plan,
    inventory_proposal,
    operation_fragment,
    patch_class_design_parser,
    single_use_case,
)


def test_vertical_service_persists_calls_and_derives_parameter_provenance(monkeypatch):
    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            return operation_fragment()
        if issubclass(schema, CallPlanProposal):
            return call_plan()
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(single_use_case())

    assert len(model["Collaborations"]) == 1
    calls = model["Collaborations"][0]["calls"]
    assert calls[1]["argumentBindings"] == [{
        "parameter": "request",
        "sourceRef": "UC1:main:1::call:1#request",
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
        if schema is OperationFragment:
            return fragment
        if issubclass(schema, CallPlanProposal):
            return plan
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(single_use_case())

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
        if schema is OperationFragment:
            return fragment
        if issubclass(schema, CallPlanProposal):
            return plan
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(single_use_case())
    binding = model["Collaborations"][0]["calls"][2]["argumentBindings"][0]

    assert binding == {
        "parameter": "details",
        "sourceRef": (
            "derived#RegistrationDetails("
            "value=UC1:main:1::call:2#request.value)"
        ),
    }


def test_earlier_optional_result_has_explicit_unwrap_source():
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
                "stereotype": "Control",
                "use_case_ids": ["UC1"],
                "fields": [],
                "identifier": [],
                "operations": [{
                    "operationId": "Registration::create(student:Student)",
                    "name": "create",
                    "parameters": [{"name": "student", "type": "Student"}],
                    "returnType": "Registration",
                    "stepRefs": ["UC1:main:2"],
                }],
            },
        ],
        "DataTypes": [],
        "Relationships": [],
        "Collaborations": [],
    }
    plan = CallPlanProposal.model_validate({
        "calls": [
            {"receiverOperationId": "RequestBoundary::start()", "parentCallIndex": None},
            {"receiverOperationId": "StudentLookup::find()", "parentCallIndex": 1},
            {"receiverOperationId": "Registration::create(student:Student)", "parentCallIndex": 1},
        ],
    })

    collaboration_model = collaboration.materialize(
        build_scenario_index(single_use_case()),
        model,
        build_scenario_index(single_use_case()).groups[0],
        plan,
    )

    assert collaboration_model["calls"][2]["argumentBindings"] == [{
        "parameter": "student",
        "sourceRef": "UC1:main:1::call:2#result.unwrap",
    }]


def test_ambiguous_binding_selection_is_limited_to_finite_candidates(monkeypatch):
    candidates = [
        "UC1:main:1::call:1#request.studentId",
        "UC1:main:1::call:2#result.studentId",
    ]

    def fake_parse(_messages, schema, **_kwargs):
        properties = schema.model_json_schema()["properties"]
        assert properties["choice1"]["enum"] == candidates
        return {"choice1": candidates[1]}

    patch_class_design_parser(monkeypatch, fake_parse)
    selected = collaboration.select_ambiguous_bindings(
        build_scenario_index(single_use_case()).groups[0],
        {"UC1:main:1::call:3#studentId": candidates},
    )

    assert selected == {
        "UC1:main:1::call:3#studentId": candidates[1],
    }
