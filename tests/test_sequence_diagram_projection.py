"""Deterministic sequence projections from accepted class designs."""
from __future__ import annotations

from copy import deepcopy

from app.design.services.class_diagram import projections, service
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.proposals import (
    CallPlanProposal,
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
    sequence_flow_order,
    validate_sequence_model,
)
from tests.class_design_fixtures import (
    call_plan,
    inventory_proposal,
    operation_fragment,
    patch_class_design_parser,
    single_use_case,
)


def _accepted_model(monkeypatch):
    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            return operation_fragment()
        if issubclass(schema, CallPlanProposal):
            return call_plan()
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    return service.generate_class_model(build_scenario_index(single_use_case()))


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


def test_sequence_projection_adds_one_return_for_every_call_including_void(monkeypatch):
    class_model = _accepted_model(monkeypatch)
    control = next(
        item for item in class_model.Classes
        if item.class_name == "RequestControl"
    )
    control.operations[0].return_type = "void"

    sequence = project_sequence_model(
        build_scenario_index(single_use_case()), class_model, "@startuml\n@enduml",
    )

    messages = sequence.Diagrams[0].Messages
    calls = [message for message in messages if message.call_id]
    returns = [message for message in messages if message.type == "return"]
    assert len(calls) == len(returns) == 2
    assert {message.reply_to for message in returns} == {
        message.call_id for message in calls
    }
    assert any(message.label == "void" for message in returns)
    assert sequence_findings(sequence) == []


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


def test_collection_validation_rejects_bce_handoff_and_main_flow_reordering(
    monkeypatch,
):
    sequence, state = _projected_contract(monkeypatch)
    messages = sequence["Diagrams"][0]["Messages"]
    calls = [message for message in messages if message["call_id"]]
    calls[1]["source"] = calls[0]["source"]
    calls[0]["step_ids"], calls[1]["step_ids"] = (
        calls[1]["step_ids"], calls[0]["step_ids"],
    )

    report = validate_sequence_model(sequence, state)
    rules = {finding.rule_id for finding in report.findings}

    assert "sequence.message-bce-flow" in rules
    assert "sequence.flow-order" in rules


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


def test_flow_order_accepts_extension_result_shared_with_branch_call():
    """분기 단계와 extension을 함께 추적한 한 호출은 늦은 재실행이 아니다."""

    state = {
        "usecase_spec": {
            "use_case_specs": [{
                "use_case_id": "UC1",
                "main_scenario": [
                    {"step_number": 1},
                    {"step_number": 2},
                    {"step_number": 3},
                ],
                "extensions": [{
                    "label": "2a",
                    "branch_step": 2,
                    "handling_steps": [{"sub_step": "2a1"}],
                }],
            }],
        },
    }
    model = {
        "use_case_id": "UC1",
        "Messages": [
            {
                "source": "Actor",
                "target": "Boundary",
                "type": "sync",
                "label": "submit()",
                "step_ids": ["UC1:main:1", "UC1:main:2", "UC1:extension:2a:2a1"],
            },
            {
                "source": "Boundary",
                "target": "Control",
                "type": "sync",
                "label": "continueFlow()",
                "step_ids": ["UC1:main:3"],
            },
        ],
    }

    assert sequence_flow_order(model, state) == []
