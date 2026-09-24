"""Deterministic sequence projections from accepted class designs."""
from __future__ import annotations

import json
from copy import deepcopy

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram import projections, service
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.proposals import (
    CallPlanProposal,
    CombinedUnitProposal,
    InventoryProposal,
    OperationFragment,
)


def _first_binding_choice(messages, _schema):
    payload = json.loads(messages[-1]["content"])
    return {
        choice["choice"]: choice["candidates"][0]
        for choice in payload["choices"]
    }
from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.sequence_diagram.methods import is_return_value_label
from app.design.services.sequence_diagram.projection import (
    project_sequence_model,
    sequence_findings,
)
from app.design.services.sequence_diagram.validation import (
    sequence_argument_data_flow,
    sequence_fragment_condition_consistency,
    validate_sequence_model,
)
from tests.class_design_fixtures import (
    call_plan,
    combined_unit_proposal,
    inventory_proposal,
    multiple_entry_use_case,
    multiple_root_combined_proposal,
    operation_fragment,
    patch_class_design_parser,
    single_use_case,
)


def _accepted_model(monkeypatch):
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
    return service.generate_class_model(build_scenario_index(single_use_case()))


def _accepted_multiple_root_model(monkeypatch):
    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            return multiple_root_combined_proposal()
        if schema.__name__ == "BindingChoices":
            return _first_binding_choice(_messages, schema)
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    return service.generate_class_model(
        build_scenario_index(multiple_entry_use_case())
    )


def test_class_render_keeps_structure_and_projects_call_dependencies(monkeypatch):
    model = _accepted_model(monkeypatch)
    payload = model.model_dump(by_alias=True)
    payload["Relationships"] = [{
        "source": "RequestControl",
        "target": "RequestBoundary",
        "type": "Association",
        "sourceMultiplicity": "1",
        "targetMultiplicity": "1",
        "description": "uses interface contract",
    }]
    puml = generate_plantuml_from_bce_json(payload)

    assert 'RequestControl "1" --> "1" RequestBoundary' in puml
    assert "RequestBoundary ..> RequestControl" in puml
    assert [item.as_payload() for item in projections.project_call_dependencies(model)] == [{
        "source": "RequestBoundary",
        "target": "RequestControl",
        "type": "Dependency",
    }]


def test_multiple_roots_project_in_order_to_one_use_case_diagram(monkeypatch):
    class_model = _accepted_multiple_root_model(monkeypatch)
    scenario = multiple_entry_use_case()

    sequence = project_sequence_model(
        build_scenario_index(scenario),
        class_model,
        "@startuml\n@enduml",
    )

    assert len(sequence.Diagrams) == 1
    messages = sequence.Diagrams[0].Messages
    calls = [message for message in messages if message.call_id]
    returns = [message for message in messages if message.type == "return"]
    assert [message.call_id for message in calls] == [
        f"UC1::call:{position}" for position in range(1, 5)
    ]
    assert [message.call_id for message in calls if message.source == "Member"] == [
        "UC1::call:1",
        "UC1::call:3",
    ]
    assert len(calls) == len(returns) == 4
    assert {message.reply_to for message in returns} == {
        message.call_id for message in calls
    }
    assert any(message.label == "void" for message in returns)
    assert sequence_findings(sequence) == []

    scenario["use_case_specs"][0]["extensions"] = [{
        "label": "3a",
        "branch_step": 3,
        "condition": "The member requests an alternate receipt",
        "handling_steps": [{
            "sub_step": "3a1",
            "subject_ref": "system",
            "sentence": "System prepares the alternate receipt.",
        }],
    }]
    payload = class_model.model_dump(by_alias=True)
    calls = payload["Collaborations"][0]["calls"]
    calls[2]["stepRefs"] = ["UC1:extension:3:1:3a1"]
    calls[3]["stepRefs"] = ["UC1:main:4", "UC1:extension:3:1:3a1"]
    conditional = project_sequence_model(
        build_scenario_index(scenario), BCEModel.model_validate(payload),
    )
    inherited = next(
        message for message in conditional.Diagrams[0].Messages
        if message.call_id == "UC1::call:4"
    )
    assert [fragment.id for fragment in inherited.fragments] == [
        "UC1:extension:3:1"
    ]
    assert inherited.fragments[0].condition_ref == "UC1:extension:3:1"


def test_source_condition_ref_is_structural_and_condition_text_is_display_only(monkeypatch):
    class_model = _accepted_multiple_root_model(monkeypatch)
    scenario = single_use_case()
    scenario["use_case_specs"][0]["extensions"] = [{
        "label": "3a",
        "branch_step": 3,
        "condition": "The member requests an alternate receipt",
        "handling_steps": [{
            "sub_step": "3a1",
            "subject_ref": "system",
            "sentence": "System prepares the alternate receipt.",
        }],
    }]
    payload = class_model.model_dump(by_alias=True)
    calls = payload["Collaborations"][0]["calls"]
    calls[2]["stepRefs"] = ["UC1:extension:3:1:3a1"]
    calls[3]["stepRefs"] = ["UC1:main:4", "UC1:extension:3:1:3a1"]
    sequence = project_sequence_model(
        build_scenario_index(scenario), BCEModel.model_validate(payload),
    ).model_dump()
    state = {"usecase_spec": scenario}
    call = next(
        message for message in sequence["Diagrams"][0]["Messages"]
        if message.get("call_id") == "UC1::call:4"
    )
    fragment = call["fragments"][0]
    assert fragment["condition_ref"] == "UC1:extension:3:1"

    fragment["condition"] = "The member asks for another kind of receipt"
    assert sequence_fragment_condition_consistency(sequence["Diagrams"][0], state) == []

    fragment["condition_ref"] = "UC1:extension:3:2"
    assert sequence_fragment_condition_consistency(sequence["Diagrams"][0], state)
    fragment.pop("condition_ref")
    assert sequence_fragment_condition_consistency(sequence["Diagrams"][0], state)


def test_nested_generic_is_a_valid_return_label():
    assert is_return_value_label("optional<list<CourseOfferingSummary>>")
    assert not is_return_value_label("optional<list<CourseOfferingSummary>")


def _projected_contract(monkeypatch):
    class_model = _accepted_model(monkeypatch)
    scenario = single_use_case()
    class_puml = generate_plantuml_from_bce_json(
        class_model.model_dump(by_alias=True)
    )
    sequence = project_sequence_model(
        build_scenario_index(scenario), class_model, class_puml,
    )
    state = {
        "usecase_spec": scenario,
        "extracted_bce_classes": class_model.model_dump(by_alias=True),
        "class_diagram_puml": class_puml,
    }
    return sequence.model_dump(), state


def test_collection_validation_rejects_duplicate_call_ids(monkeypatch):
    sequence, state = _projected_contract(monkeypatch)
    messages = sequence["Diagrams"][0]["Messages"]
    messages.insert(1, deepcopy(messages[0]))

    report = validate_sequence_model(sequence, state)

    assert "sequence.call-return-links" in {
        finding.rule_id for finding in report.findings
    }


def test_projection_preserves_stable_source_refs_and_rejects_wrong_ref(monkeypatch):
    sequence, state = _projected_contract(monkeypatch)
    arguments = [
        argument
        for message in sequence["Diagrams"][0]["Messages"]
        for argument in message.get("arguments", [])
    ]
    assert arguments
    source_ref = arguments[0]["source_ref"]
    source_id, _, _ = source_ref.partition("#")
    stable_ids = {
        call["stableId"]
        for collaboration in state["extracted_bce_classes"]["Collaborations"]
        for call in collaboration["calls"]
    }
    assert source_id in stable_ids or source_id.startswith("UC1:")

    arguments[0]["source_ref"] = f"{source_id}#unknown-stable-ref"
    findings = sequence_argument_data_flow(sequence["Diagrams"][0], state)
    assert any(finding.rule_id == "sequence.argument-data-flow" for finding in findings)
