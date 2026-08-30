"""Deterministic sequence projections from accepted class designs."""
from __future__ import annotations

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
from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.sequence_diagram.methods import is_return_value_label
from app.design.services.sequence_diagram.projection import (
    project_sequence_model,
    sequence_findings,
)
from app.design.services.sequence_diagram.validation import (
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
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    return service.generate_class_model(build_scenario_index(single_use_case()))


def _accepted_multiple_root_model(monkeypatch):
    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            return multiple_root_combined_proposal()
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
            "subject_ref": "System",
            "sentence": "System prepares the alternate receipt.",
        }],
    }]
    payload = class_model.model_dump(by_alias=True)
    calls = payload["Collaborations"][0]["calls"]
    calls[2]["stepRefs"] = ["UC1:extension:3a:3a1"]
    calls[3]["stepRefs"] = ["UC1:main:4", "UC1:extension:3a:3a1"]
    conditional = project_sequence_model(
        build_scenario_index(scenario), BCEModel.model_validate(payload),
    )
    inherited = next(
        message for message in conditional.Diagrams[0].Messages
        if message.call_id == "UC1::call:4"
    )
    assert [fragment.id for fragment in inherited.fragments] == [
        "UC1:extension:3a"
    ]


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


def test_collection_validation_rejects_invalid_bce_handoff(monkeypatch):
    sequence, state = _projected_contract(monkeypatch)
    messages = sequence["Diagrams"][0]["Messages"]
    calls = [message for message in messages if message["call_id"]]
    calls[1]["source"] = calls[0]["source"]

    report = validate_sequence_model(sequence, state)
    rules = {finding.rule_id for finding in report.findings}

    assert "sequence.message-bce-flow" in rules


def test_collection_validation_requires_boundary_to_control_handoff(monkeypatch):
    sequence, state = _projected_contract(monkeypatch)
    messages = sequence["Diagrams"][0]["Messages"]
    outer_call = messages[0]
    outer_return = messages[-1]
    outer_call["step_ids"] = ["UC1:main:1", "UC1:main:2"]
    outer_return["step_ids"] = ["UC1:main:1", "UC1:main:2"]
    sequence["Diagrams"][0]["Messages"] = [outer_call, outer_return]

    report = validate_sequence_model(sequence, state)

    assert any(
        finding.rule_id == "sequence.message-bce-flow"
        and "hand off to a Control" in finding.message
        for finding in report.findings
    )


def test_collection_validation_rejects_return_before_its_call(monkeypatch):
    sequence, state = _projected_contract(monkeypatch)
    messages = sequence["Diagrams"][0]["Messages"]
    nested_return = next(
        message for message in messages
        if message["type"] == "return" and message["reply_to"].endswith("call:2")
    )
    messages.remove(nested_return)
    messages.insert(0, nested_return)

    report = validate_sequence_model(sequence, state)

    assert "sequence.call-return-links" in {
        finding.rule_id for finding in report.findings
    }
