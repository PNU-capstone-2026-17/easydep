"""Class-design service orchestration and owned-unit resume behavior."""
from __future__ import annotations

import inspect

from app.design.services.class_diagram import service
from app.design.services.class_diagram.cache import ProcessLocalAcceptedUnitCache
from app.design.services.class_diagram.proposals import (
    CallPlanProposal,
    InventoryProposal,
    OperationFragment,
)
from app.design.services.class_diagram.scenario import build_scenario_index
from tests.class_design_fixtures import (
    call_plan,
    inventory_proposal,
    operation_fragment,
    patch_class_design_parser,
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

    patch_class_design_parser(monkeypatch, fake_parse)
    index = build_scenario_index(single_use_case())
    current = service.generate_class_model(index)
    current.Collaborations = []
    calls.clear()

    resumed = service.resume_class_model(index, current)

    assert len(resumed.Collaborations) == 1
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

    patch_class_design_parser(monkeypatch, fake_parse)
    index = build_scenario_index(single_use_case())
    backing_cache = ProcessLocalAcceptedUnitCache(capacity=32)

    class RecordingCache:
        def __init__(self):
            self.calls = 0

        def get_or_compute(self, key, compute):
            self.calls += 1
            return backing_cache.get_or_compute(key, compute)

    cache = RecordingCache()
    current = service.generate_class_model(index, cache=cache)
    inventory_before = [
        (item.class_name, item.fields, item.identifier)
        for item in current.Classes
    ]
    cache_calls_before_revision = cache.calls

    result = service.revise_class_model(
        current,
        index,
        "Rename the actor-facing operation to send.",
        {"UC1"},
        cache=cache,
    )

    assert cache.calls > cache_calls_before_revision
    boundary = next(
        item for item in result.Classes if item.class_name == "RequestBoundary"
    )
    assert boundary.operations[0].name == "send"
    assert [
        (item.class_name, item.fields, item.identifier)
        for item in result.Classes
    ] == inventory_before
    assert result.Collaborations[0].calls[0].receiver_operation_id.startswith(
        "RequestBoundary::send("
    )


def test_generate_and_resume_reuse_accepted_units_without_warm_llm_calls(monkeypatch):
    """The process cache is threaded through public services and warm hits are offline."""

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

    patch_class_design_parser(monkeypatch, fake_parse)
    cache = ProcessLocalAcceptedUnitCache(capacity=32)
    index = build_scenario_index(single_use_case())
    generated = service.generate_class_model(index, cache=cache)
    assert calls

    calls.clear()
    warm = service.generate_class_model(index, cache=cache)
    assert calls == []
    assert warm.model_dump(by_alias=True) == generated.model_dump(by_alias=True)

    # Resume has to consume the same accepted call-plan unit when the
    # checkpoint is missing its collaboration, without asking the provider.
    incomplete = generated.model_copy(deep=True)
    incomplete.Collaborations = []
    calls.clear()
    resumed = service.resume_class_model(index, incomplete, cache=cache)
    assert calls == []
    assert resumed.model_dump(by_alias=True) == generated.model_dump(by_alias=True)

    # Revision is also a first-class cache boundary, even when no feedback is
    # ultimately applied by this no-op request.
    assert "cache" in inspect.signature(service.revise_class_model).parameters
