"""Class-design inventory and scenario evidence contracts."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import settings
from app.design.services.class_diagram import collaboration, inventory, operations
from app.design.services.class_diagram.proposals import InventoryProposal
from app.design.services.class_diagram.scenario import build_scenario_index
from app.llm_schema import strict_json_schema
from tests.class_design_fixtures import (
    call_plan,
    inventory_proposal,
    operation_fragment,
    patch_class_design_parser,
    scenario,
    single_use_case,
)


def test_scenario_index_splits_actor_entries_and_attaches_extension_steps():
    index = build_scenario_index(scenario())

    assert [group.id for group in index.groups] == ["UC1:main:1", "UC1:main:4"]
    first = index.groups[0]
    assert "UC1:extension:2a:2a1" in first.step_ids
    assert "UC2:main:1" in first.required_step_ids
    assert first.trace_use_case_ids == ("UC1", "UC2")

    consecutive = scenario()
    consecutive["use_case_specs"][0]["main_scenario"][1].update({
        "subject_ref": "Customer",
        "sentence": "Customer also supplies the order details.",
    })
    consecutive_groups = build_scenario_index(consecutive).groups
    assert [group.id for group in consecutive_groups] == ["UC1:main:1", "UC1:main:4"]
    assert consecutive_groups[0].step_ids[:3] == (
        "UC1:main:1", "UC1:main:2", "UC1:main:3",
    )


def test_scenario_index_keeps_system_steps_before_first_actor_entry():
    value = single_use_case()
    value["use_case_specs"][0]["main_scenario"].insert(0, {
        "step_number": 0,
        "subject_ref": "System",
        "sentence": "System shows the request form.",
    })

    group = build_scenario_index(value).groups[0]

    assert group.id == "UC1:main:1"
    assert group.required_step_ids[:2] == ("UC1:main:0", "UC1:main:1")


def test_internal_include_has_no_synthetic_standalone_group():
    index = build_scenario_index(scenario())

    assert all(group.use_case_id != "UC2" for group in index.groups)


def test_inventory_payload_keeps_execution_evidence_without_full_spec_documents():
    payload = inventory.inventory_payload(build_scenario_index(scenario()))

    assert set(payload) == {"useCases", "relationships"}
    assert [item["id"] for item in payload["useCases"]] == ["UC1", "UC2"]
    assert payload["useCases"][0]["steps"][0]["stepRef"] == "UC1:main:1"
    assert payload["useCases"][0]["context"] == {
        "trigger": "Customer starts an order request.",
        "preconditions": ["The catalog is available."],
        "successGuarantee": [{
            "sentence": "The accepted order remains available after this request.",
            "covered_req_ids": ["RR1"],
        }],
        "minimalGuarantee": [],
    }
    assert payload["relationships"] == [{
        "kind": "include",
        "baseUseCaseId": "UC1",
        "relatedUseCaseId": "UC2",
        "anchorStepRefs": ["UC1:main:2"],
    }]
    assert all("use_case_specs" not in item for item in payload["useCases"])


def test_inventory_contract_does_not_silently_default_structural_decisions():
    with pytest.raises(ValidationError):
        InventoryProposal.model_validate({
            "items": [{"name": "Order", "kind": "Entity"}],
            "Relationships": [],
        })


def test_inventory_json_schema_is_strict_and_english_only():
    schema = strict_json_schema(InventoryProposal)

    def assert_object_properties_are_required(node):
        if node.get("type") == "object" and "properties" in node:
            assert set(node["properties"]).issubset(node.get("required", []))
        for value in node.values():
            if isinstance(value, dict):
                assert_object_properties_are_required(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        assert_object_properties_are_required(item)

    def assert_descriptions_are_ascii_english(node):
        if isinstance(node, dict):
            description = node.get("description")
            if isinstance(description, str):
                assert description.isascii(), description
            for value in node.values():
                assert_descriptions_are_ascii_english(value)
        elif isinstance(node, list):
            for item in node:
                assert_descriptions_are_ascii_english(item)

    assert_object_properties_are_required(schema)
    assert_descriptions_are_ascii_english(schema)


def test_normalized_inventory_propagates_entity_scope_through_structural_types():
    proposal = InventoryProposal.model_validate({
        "items": [
            {
                "name": "Order",
                "kind": "Entity",
                "description": "Persistent order",
                "fields": [{"name": "details", "type": "OrderDetails"}],
                "identifier": ["orderId"],
                "values": [],
                "useCaseIds": ["UC1"],
            },
            {
                "name": "OrderDetails",
                "kind": "valueObject",
                "description": "Order data",
                "fields": [{"name": "label", "type": "String"}],
                "identifier": [],
                "values": [],
                "useCaseIds": [],
            },
        ],
        "Relationships": [
            {
                "source": "Order",
                "target": "OrderDetails",
                "type": "Composition",
                "sourceMultiplicity": "1",
                "targetMultiplicity": "1",
                "description": "Order owns its details.",
            }
        ],
    })

    normalized = inventory.normalize_inventory(proposal)
    details = next(item for item in normalized.data_types if item["name"] == "OrderDetails")
    assert details["useCaseIds"] == ["UC1"]
    assert normalized.relationships == ()


def test_class_design_reasoning_effort_is_independent_per_owned_stage(monkeypatch):
    """E1 can lower one stage without changing the others or repair scope."""

    monkeypatch.setattr(settings, "design_class_inventory_reasoning_effort", "low")
    monkeypatch.setattr(settings, "design_class_operation_reasoning_effort", "high")
    monkeypatch.setattr(settings, "design_class_call_plan_reasoning_effort", "medium")

    assert inventory.inventory_reasoning_effort() == "low"
    assert operations.operation_reasoning_effort() == "high"
    assert collaboration.call_plan_reasoning_effort() == "medium"

    # 단계별 reasoning은 모델 설정일 뿐 수리 횟수나 종료 조건을 바꾸지 않는다.
    assert not hasattr(settings, "design_max_repair_iters")


def test_inventory_repair_continues_past_one_replacement(monkeypatch):
    candidates = []
    missing_control = inventory_proposal()
    missing_control["items"] = [missing_control["items"][0]]
    candidates.append(missing_control)
    candidates.append(missing_control)
    candidates.append(inventory_proposal())
    inventory_calls = 0

    def fake_parse(messages, schema, **_kwargs):
        nonlocal inventory_calls
        if schema.__name__ == "InventoryProposal":
            candidate = candidates[inventory_calls]
            inventory_calls += 1
            if inventory_calls == 3:
                assert "repairHistory" in messages[-1]["content"]
            return candidate
        if schema.__name__ == "OperationFragment":
            return operation_fragment()
        return call_plan()

    patch_class_design_parser(monkeypatch, fake_parse)
    accepted = inventory.inventory_proposal(build_scenario_index(single_use_case()))

    assert inventory_calls == 3
    assert {item["className"] for item in accepted.classes} == {
        "RequestBoundary",
        "RequestControl",
    }
