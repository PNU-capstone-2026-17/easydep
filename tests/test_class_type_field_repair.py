"""Focused coverage for the one-shot type-field repair inside combined proposals."""
from __future__ import annotations

from copy import deepcopy

from app.design.services.class_diagram import generation
from app.design.services.class_diagram.models import AcceptedInventory, RepairBudget
from app.design.services.class_diagram.proposals import CombinedUnitProposal
from app.design.services.class_diagram.scenario import build_scenario_index
from tests.class_design_fixtures import combined_unit_proposal, single_use_case


def _inventory() -> AcceptedInventory:
    return AcceptedInventory.from_payload({
        "Classes": [
            {"className": "RequestBoundary", "stereotype": "Boundary", "fields": []},
            {"className": "RequestControl", "stereotype": "Control", "fields": []},
        ],
        "DataTypes": [],
        "Relationships": [],
    })


def _propose(monkeypatch, replies):
    calls = []

    def fake_parse(_messages, schema, **_kwargs):
        calls.append(schema)
        return replies.pop(0)

    monkeypatch.setattr(generation, "parse_structured", fake_parse)
    index = build_scenario_index(single_use_case())
    fragment, raw = generation._propose_unit(
        index, _inventory(), next(item for item in index.use_cases if item.id == "UC1"),
        reserved=[], reserved_types=[],
        budget=RepairBudget("UC1"),
    )
    return fragment, raw, calls


def test_targeted_repair_changes_only_invalid_type_fields_and_keeps_calls(monkeypatch):
    proposal = combined_unit_proposal()
    proposal["fragment"]["DataTypes"][0]["fields"][0]["type"] = "List<String"
    proposal["fragment"]["Classes"][0]["operations"][0]["parameters"][0]["type"] = "Missing"
    original_calls = deepcopy(proposal["calls"])
    original_names = [
        operation["name"]
        for class_set in proposal["fragment"]["Classes"]
        for operation in class_set["operations"]
    ]

    _, raw, schemas = _propose(monkeypatch, [
        proposal,
        {"corrections": [
            {"path": "/fragment/DataTypes/0/fields/0/type", "correctedType": "List<String>"},
            {"path": "/fragment/Classes/0/operations/0/parameters/0/type", "correctedType": "RequestData"},
        ]},
    ])

    assert schemas == [CombinedUnitProposal, generation.TypeFieldRepairProposal]
    assert raw["calls"] == original_calls
    assert raw["fragment"]["DataTypes"][0]["fields"][0]["type"] == "List<String>"
    assert raw["fragment"]["Classes"][0]["operations"][0]["parameters"][0]["type"] == "RequestData"
    assert [
        operation["name"]
        for class_set in raw["fragment"]["Classes"]
        for operation in class_set["operations"]
    ] == original_names


def test_invalid_targeted_answer_falls_through_to_existing_full_unit_repair(monkeypatch):
    invalid = combined_unit_proposal()
    invalid["fragment"]["DataTypes"][0]["fields"][0]["type"] = "UnknownThing"

    _, raw, schemas = _propose(monkeypatch, [
        invalid,
        {"corrections": [{
            "path": "/fragment/DataTypes/0/fields/0/type", "correctedType": "StillUnknown",
        }]},
        combined_unit_proposal(),
    ])

    assert schemas == [
        CombinedUnitProposal,
        generation.TypeFieldRepairProposal,
        CombinedUnitProposal,
    ]
    assert raw["fragment"]["DataTypes"][0]["fields"][0]["type"] == "String"


def test_valid_candidate_does_not_make_type_repair_call(monkeypatch):
    _, _, schemas = _propose(monkeypatch, [combined_unit_proposal()])

    assert schemas == [CombinedUnitProposal]


def test_generic_whitespace_repair_preserves_calls_and_non_type_fields(monkeypatch):
    proposal = combined_unit_proposal()
    operation = proposal["fragment"]["Classes"][0]["operations"][0]
    operation["parameters"][0]["type"] = "optional list<RequestData>"
    original_calls = deepcopy(proposal["calls"])
    original_operation = deepcopy(operation)

    _, raw, schemas = _propose(monkeypatch, [
        proposal,
        {"corrections": [{
            "path": "/fragment/Classes/0/operations/0/parameters/0/type",
            # parse_structured returns Pydantic's field-name dump, not alias JSON.
            "corrected_type": "Optional<List<RequestData>>",
        }]},
    ])

    repaired_operation = raw["fragment"]["Classes"][0]["operations"][0]
    assert schemas == [CombinedUnitProposal, generation.TypeFieldRepairProposal]
    assert raw["calls"] == original_calls
    assert {
        key: value for key, value in repaired_operation.items() if key != "parameters"
    } == {
        key: value for key, value in original_operation.items() if key != "parameters"
    }
    assert repaired_operation["parameters"][0] == {
        "name": "request", "type": "Optional<List<RequestData>>",
    }


def test_type_field_scan_rejects_bad_grammar_and_unknown_references():
    proposal = combined_unit_proposal()
    proposal["fragment"]["DataTypes"][0]["fields"][0]["type"] = "List<String"
    proposal["fragment"]["Classes"][0]["operations"][0]["returnType"] = "UnknownResult"

    findings = generation._invalid_type_fields(proposal, _inventory(), [])

    assert [item["reason"] for item in findings] == [
        "type does not match the canonical grammar",
        "type references a name outside declaredNames",
    ]
