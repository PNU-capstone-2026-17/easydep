"""Class-design operation fragments and deterministic operation checks."""
from __future__ import annotations

import json

import pytest

from app.config import settings
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
from app.design.services.class_diagram.operations import _canonicalize_step_ownership
from app.validation import run_checks
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


def test_operation_public_payload_keeps_context_without_repeating_full_scenario(monkeypatch):
    monkeypatch.setattr(settings, "design_class_compact_operation_payload", True)
    payloads: list[dict] = []

    def fake_parse(messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            payloads.append(json.loads(messages[-1]["content"]))
            return operation_fragment()
        if issubclass(schema, CallPlanProposal):
            return call_plan()
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    raw = single_use_case()
    raw["use_cases"][0]["goal"] = "Submit a request and receive a result."
    raw["use_cases"][0]["supporting_actors"] = ["Request Gateway"]
    raw["use_case_specs"][0]["preconditions"] = ["The member is authenticated."]
    raw["use_case_specs"][0]["postconditions"] = ["The result is recorded."]
    raw["use_case_specs"][0]["business_rules"] = ["A request is accepted once."]
    raw["use_cases"][0]["main_scenario"] = [{"step_number": 99}]
    raw["use_cases"][0]["extensions"] = [{"label": "legacy"}]
    service.generate_class_model(build_scenario_index(raw))

    assert len(payloads) == 1
    payload = payloads[0]
    use_case = payload["useCase"]
    assert use_case["id"] == "UC1"
    assert use_case["name"] == "Submit request"
    assert use_case["goal"] == "Submit a request and receive a result."
    assert use_case["supporting_actors"] == ["Request Gateway"]
    assert "main_scenario" not in use_case
    assert "extensions" not in use_case
    specification = use_case["specification"]
    assert specification["preconditions"] == ["The member is authenticated."]
    assert specification["postconditions"] == ["The result is recorded."]
    assert specification["business_rules"] == ["A request is accepted once."]
    assert "main_scenario" not in specification
    assert "extensions" not in specification
    encoded = json.dumps(use_case)
    assert encoded.count('"goal"') == 1
    assert encoded.count('"primary_actor"') == 1
    assert payload["allowedStepRefs"] == ["UC1:main:1", "UC1:main:2"]
    assert [step["stepRef"] for step in payload["executionSlice"]["steps"]] == [
        "UC1:main:1", "UC1:main:2",
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
    with pytest.raises(ValueError, match="parameter source must be"):
        service.generate_class_model(build_scenario_index(single_use_case()))


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
        # operation 정규화가 재사용 가능한 RequestData로 바꾼 뒤의 실제 유한 후보다.
        "receiverOperationId": "Registration::add(details:RequestData)",
        "parentCallIndex": 2,
    })

    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_candidate
        if schema is OperationFragment:
            return fragment
        if issubclass(schema, CallPlanProposal):
            return plan
        if schema.__name__ == "FiniteBindingChoices":
            # 같은 RequestData가 두 호출에 있어 선택만 LLM 몫이다. 테스트는 동적
            # Literal schema가 허용한 첫 실제 후보를 골라 생성 흐름을 끝까지 확인한다.
            return {
                name: field_schema["enum"][0]
                for name, field_schema in schema.model_json_schema()["properties"].items()
            }
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


def test_operation_repair_continues_past_one_replacement(monkeypatch):
    invalid_one = operation_fragment()
    invalid_one["Classes"][0]["operations"][0]["stepRefs"] = ["UC9:main:1"]
    invalid_two = operation_fragment()
    invalid_two["Classes"][0]["operations"][0]["stepRefs"] = ["UC8:main:1"]
    candidates = [invalid_one, invalid_two, operation_fragment()]
    operation_calls = 0

    def fake_parse(messages, schema, **_kwargs):
        nonlocal operation_calls
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            candidate = candidates[operation_calls]
            operation_calls += 1
            if operation_calls == 3:
                assert "Accumulated repair history" in messages[-1]["content"]
            return candidate
        if issubclass(schema, CallPlanProposal):
            return call_plan()
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(single_use_case()))

    assert operation_calls == 3
    assert len(model.Collaborations) == 1


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


def test_actor_only_execution_slice_does_not_require_control_or_entity():
    raw = {
        "use_cases": [{"id": "UC1", "name": "Review", "primary_actor": "Member"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [{
                "step_number": 1,
                "subject_ref": "Member",
                "sentence": "Member reviews the displayed result.",
            }],
            "extensions": [],
        }],
        "relationships": {"includes": [], "extends": []},
    }
    index = build_scenario_index(raw)
    inventory = {
        "Classes": [{
            "className": "ReviewBoundary",
            "stereotype": "Boundary",
            "useCaseIds": ["UC1"],
        }],
        "DataTypes": [],
        "Relationships": [],
    }
    fragment = {
        "DataTypes": [],
        "Classes": [{
            "className": "ReviewBoundary",
            "operations": [{
                "name": "review",
                "parameters": [],
                "returnType": "void",
                "stepRefs": ["UC1:main:1"],
            }],
        }],
    }
    report = run_checks(
        OPERATION_CHECKS,
        fragment,
        OperationContext(index, inventory, index.use_case("UC1"), ("UC1:main:1",), ("UC1:main:1",)),
        parallel=True,
    )
    assert not report.findings


def test_operation_normalization_discards_refs_outside_execution_slice():
    candidate = {
        "DataTypes": [],
        "Classes": [{
            "className": "RequestBoundary",
            "operations": [{
                "name": "review",
                "parameters": [],
                "returnType": "void",
                "stepRefs": ["UC1:main:1", "UC1:main:2"],
            }],
        }],
    }
    inventory = {"Classes": [{"className": "RequestBoundary", "stereotype": "Boundary"}]}
    normalized = _canonicalize_step_ownership(
        candidate, inventory, set(), ("UC1:main:2",),
    )
    assert normalized["Classes"][0]["operations"][0]["stepRefs"] == ["UC1:main:2"]


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
