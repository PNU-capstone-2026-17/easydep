"""Interaction-design service orchestration and owned-unit resume behavior."""
from __future__ import annotations

from app.design.services.interaction_design import service
from app.design.services.interaction_design.proposals import (
    CallPlanProposal,
    InventoryProposal,
    OperationFragment,
)
from tests.interaction_design_fixtures import (
    call_plan,
    inventory_proposal,
    operation_fragment,
    patch_interaction_parser,
    single_use_case,
)


def test_resume_only_plans_missing_collaborations(monkeypatch):
    calls: list[type] = []

    def fake_parse(_messages, schema, **_kwargs):
        calls.append(schema)
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            return operation_fragment()
        if issubclass(schema, CallPlanProposal):
            return call_plan()
        raise AssertionError(schema)

    patch_interaction_parser(monkeypatch, fake_parse)
    current = service.generate_class_model(single_use_case())
    current["Collaborations"] = []
    calls.clear()

    resumed = service.resume_class_model(single_use_case(), current)

    assert len(resumed["Collaborations"]) == 1
    assert sum(issubclass(schema, CallPlanProposal) for schema in calls) == 1
    assert all(schema is not OperationFragment for schema in calls)


def test_operation_feedback_rebuilds_only_the_owned_contract(monkeypatch):
    operation_fragment_calls = 0
    revised = False

    def fake_parse(_messages, schema, **_kwargs):
        nonlocal operation_fragment_calls, revised
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            operation_fragment_calls += 1
            candidate = operation_fragment()
            if operation_fragment_calls > 1:
                revised = True
                candidate["Classes"][0]["operations"][0]["name"] = "send"
            return candidate
        if issubclass(schema, CallPlanProposal):
            plan = call_plan()
            if revised:
                plan["calls"][0]["receiverOperationId"] = (
                    "RequestBoundary::send(request:RequestData)"
                )
            return plan
        raise AssertionError(schema)

    patch_interaction_parser(monkeypatch, fake_parse)
    current = service.generate_class_model(single_use_case())
    inventory_before = [
        (item["className"], item["fields"], item["identifier"])
        for item in current["Classes"]
    ]

    result = service.revise_class_model(
        current,
        single_use_case(),
        "Rename the actor-facing operation to send.",
        {"UC1"},
    )

    boundary = next(
        item for item in result["Classes"] if item["className"] == "RequestBoundary"
    )
    assert boundary["operations"][0]["name"] == "send"
    assert [
        (item["className"], item["fields"], item["identifier"])
        for item in result["Classes"]
    ] == inventory_before
    assert result["Collaborations"][0]["calls"][0]["receiverOperationId"].startswith(
        "RequestBoundary::send("
    )
