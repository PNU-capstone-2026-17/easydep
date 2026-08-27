"""Deterministic sequence projections from accepted class designs."""
from __future__ import annotations

from app.design.services.class_diagram import projections, service
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.proposals import (
    CallPlanProposal,
    InventoryProposal,
    OperationFragment,
)
from app.design.services.sequence_diagram.methods import is_return_value_label
from app.design.services.sequence_diagram.projection import (
    project_sequence_model,
    sequence_findings,
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
    return service.generate_class_model(single_use_case())


def test_class_render_keeps_structure_and_projects_call_dependencies(monkeypatch):
    model = _accepted_model(monkeypatch)
    model["Relationships"] = [{
        "source": "RequestControl",
        "target": "RequestBoundary",
        "type": "Association",
        "sourceMultiplicity": "1",
        "targetMultiplicity": "1",
        "description": "uses interface contract",
    }]
    puml = generate_plantuml_from_bce_json(model)

    assert 'RequestControl "1" --> "1" RequestBoundary' in puml
    assert "RequestBoundary ..> RequestControl" in puml
    assert projections.project_call_dependencies(model) == [{
        "source": "RequestBoundary",
        "target": "RequestControl",
        "type": "Dependency",
    }]


def test_sequence_projection_adds_one_return_for_every_call_including_void(monkeypatch):
    class_model = _accepted_model(monkeypatch)
    control = next(
        item for item in class_model["Classes"]
        if item["className"] == "RequestControl"
    )
    control["operations"][0]["returnType"] = "void"

    sequence = project_sequence_model(
        single_use_case(), class_model, "@startuml\n@enduml",
    )

    messages = sequence["Diagrams"][0]["Messages"]
    calls = [message for message in messages if message["call_id"]]
    returns = [message for message in messages if message["type"] == "return"]
    assert len(calls) == len(returns) == 2
    assert {message["reply_to"] for message in returns} == {
        message["call_id"] for message in calls
    }
    assert any(message["label"] == "void" for message in returns)
    assert sequence_findings(sequence) == []


def test_nested_generic_is_a_valid_return_label():
    assert is_return_value_label("optional<list<CourseOfferingSummary>>")
    assert not is_return_value_label("optional<list<CourseOfferingSummary>")
