"""Class-design operation fragments and deterministic operation checks."""
from __future__ import annotations

import pytest

from app.validation import run_checks
from app.design.services.class_diagram import service
from app.design.services.class_diagram.proposals import (
    CallPlanProposal,
    InventoryProposal,
    OperationFragment,
)
from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.class_diagram.validation.operations import (
    OPERATION_CHECKS,
    OperationContext,
)
from tests.class_design_fixtures import (
    call_plan,
    inventory_proposal,
    operation_fragment,
    patch_class_design_parser,
    single_use_case,
)


def test_operation_generation_keeps_signature_data_types_in_the_persisted_model(monkeypatch):
    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            return operation_fragment()
        if issubclass(schema, CallPlanProposal):
            return call_plan()
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(single_use_case()))
    model = model.model_dump(by_alias=True)

    assert [item["name"] for item in model["DataTypes"]] == [
        "RequestData", "RequestResult",
    ]


def test_vertical_service_does_not_fabricate_an_unsourceable_parameter(monkeypatch):
    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            return operation_fragment(unsourceable=True)
        if issubclass(schema, CallPlanProposal):
            plan = call_plan()
            plan["calls"][1]["receiverOperationId"] = "RequestControl::process(other:Boolean)"
            return plan
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(single_use_case()))
    model = model.model_dump(by_alias=True)

    assert model["Collaborations"] == []


def test_operation_generation_reuses_one_grounded_upstream_value_type(monkeypatch):
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
        "name": "AddRegistrationDetails",
        "kind": "valueObject",
        "fields": [{"name": "value", "type": "String"}, {"name": "generatedId", "type": "uuid"}],
        "values": [],
    })
    fragment["Classes"].append({
        "className": "Registration",
        "operations": [{
            "name": "add",
            "parameters": [{"name": "details", "type": "AddRegistrationDetails"}],
            "returnType": "Registration",
            "stepRefs": ["UC1:main:2"],
        }],
    })
    plan = call_plan()
    plan["calls"].append({
        "receiverOperationId": "Registration::add(details:AddRegistrationDetails)",
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
    model = service.generate_class_model(build_scenario_index(single_use_case()))
    model = model.model_dump(by_alias=True)

    registration = next(item for item in model["Classes"] if item["className"] == "Registration")
    assert registration["operations"][0]["parameters"][0]["type"] == "RequestData"
    assert {item["name"] for item in model["DataTypes"]} == {
        "RequestData", "RequestResult",
    }


def test_placeholder_operations_are_removed_before_the_fragment_is_accepted(monkeypatch):
    fragment = operation_fragment()
    fragment["Classes"][1]["operations"].append({
        "name": "none",
        "parameters": [],
        "returnType": "void",
        "stepRefs": ["UC1:main:2"],
    })

    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            return fragment
        if issubclass(schema, CallPlanProposal):
            return call_plan()
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(single_use_case()))
    model = model.model_dump(by_alias=True)

    assert all(
        operation["name"] != "none"
        for class_set in model["Classes"]
        for operation in class_set["operations"]
    )


def test_repaired_fragment_drops_actor_entry_refs_from_delegated_operations(monkeypatch):
    candidate = operation_fragment()
    candidate["Classes"][1]["operations"][0]["stepRefs"] = [
        "UC1:main:1", "UC1:main:2",
    ]
    operation_calls = 0

    def fake_parse(_messages, schema, **_kwargs):
        nonlocal operation_calls
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            operation_calls += 1
            return candidate if operation_calls == 1 else operation_fragment()
        if issubclass(schema, CallPlanProposal):
            return call_plan()
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(single_use_case()))
    model = model.model_dump(by_alias=True)

    control = next(item for item in model["Classes"] if item["className"] == "RequestControl")
    assert operation_calls == 1
    assert control["operations"][0]["stepRefs"] == ["UC1:main:2"]


def test_entity_receiver_cannot_accept_its_own_complete_entity_type():
    index = build_scenario_index(single_use_case())
    inventory = {
        "Classes": [
            {"className": "RequestBoundary", "stereotype": "Boundary", "useCaseIds": ["UC1"]},
            {"className": "RequestControl", "stereotype": "Control", "useCaseIds": ["UC1"]},
            {"className": "Registration", "stereotype": "Entity", "useCaseIds": ["UC1"], "fields": ["registrationId : uuid"], "identifier": ["registrationId"]},
        ],
        "DataTypes": [],
        "Relationships": [],
    }
    fragment = operation_fragment()
    fragment["Classes"].append({
        "className": "Registration",
        "operations": [{
            "name": "add",
            "parameters": [{"name": "entry", "type": "Registration"}],
            "returnType": "Registration",
            "stepRefs": ["UC1:main:2"],
        }],
    })

    report = run_checks(
        OPERATION_CHECKS,
        fragment,
        OperationContext(index, inventory, index.use_case("UC1")),
        parallel=True,
    )

    assert any("own complete Entity type" in finding.message for finding in report.findings)


def test_only_boundary_operation_may_trace_an_actor_entry_step():
    index = build_scenario_index(single_use_case())
    inventory = {
        "Classes": [
            {"className": "RequestBoundary", "stereotype": "Boundary", "useCaseIds": ["UC1"]},
            {"className": "RequestControl", "stereotype": "Control", "useCaseIds": ["UC1"]},
        ],
        "DataTypes": [],
        "Relationships": [],
    }
    fragment = operation_fragment()
    fragment["Classes"][1]["operations"][0]["stepRefs"] = [
        "UC1:main:1", "UC1:main:2",
    ]

    report = run_checks(
        OPERATION_CHECKS,
        fragment,
        OperationContext(index, inventory, index.use_case("UC1")),
        parallel=True,
    )

    assert any("only Boundary may own an actor entry step" in finding.message for finding in report.findings)


def test_operation_contract_rejects_duplicate_parameter_names():
    fragment = operation_fragment()
    parameters = fragment["Classes"][0]["operations"][0]["parameters"]
    parameters.append({"name": parameters[0]["name"], "type": "String"})

    with pytest.raises(ValueError, match="parameter names"):
        OperationFragment.model_validate(fragment)


def test_operation_contract_rejects_duplicate_class_declarations():
    fragment = operation_fragment()
    fragment["Classes"].append({
        "className": fragment["Classes"][0]["className"],
        "operations": [fragment["Classes"][0]["operations"][0]],
    })

    with pytest.raises(ValueError, match="each class once"):
        OperationFragment.model_validate(fragment)
