"""Interaction-design inventory and scenario evidence contracts."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.design.services.interaction_design import inventory
from app.design.services.interaction_design.proposals import InventoryProposal
from app.design.services.interaction_design.scenario import build_scenario_index
from tests.interaction_design_fixtures import scenario


def test_scenario_index_splits_actor_entries_and_attaches_extension_steps():
    index = build_scenario_index(scenario())

    assert [group.id for group in index.groups] == ["UC1:main:1", "UC1:main:4"]
    first = index.groups[0]
    assert "UC1:extension:2a:2a1" in first.step_ids
    assert "UC2:main:1" in first.required_step_ids
    assert first.trace_use_case_ids == ("UC1", "UC2")


def test_internal_include_has_no_synthetic_standalone_group():
    index = build_scenario_index(scenario())

    assert all(group.use_case_id != "UC2" for group in index.groups)


def test_inventory_payload_keeps_execution_evidence_without_full_spec_documents():
    payload = inventory.inventory_payload(build_scenario_index(scenario()))

    assert set(payload) == {"useCases", "relationships"}
    assert [item["id"] for item in payload["useCases"]] == ["UC1", "UC2"]
    assert payload["useCases"][0]["steps"][0]["stepRef"] == "UC1:main:1"
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
        "Relationships": [],
    })

    normalized = inventory.normalize_inventory(proposal)
    details = next(item for item in normalized["DataTypes"] if item["name"] == "OrderDetails")
    assert details["useCaseIds"] == ["UC1"]
