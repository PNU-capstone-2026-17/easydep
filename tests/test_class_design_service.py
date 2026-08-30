"""클래스 설계 서비스의 결합 생성, 수리와 cache 경계를 검증한다."""
from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.design.services.class_diagram import service
from app.design.services.class_diagram.cache import ProcessLocalAcceptedUnitCache
from app.design.services.class_diagram.proposals import (
    CallPlanProposal,
    CombinedUnitProposal,
    FeedbackScope,
    InventoryProposal,
    OperationFragment,
)
from app.design.services.class_diagram.scenario import build_scenario_index
from tests.class_design_fixtures import (
    inventory_proposal,
    multiple_entry_use_case,
    multiple_root_call_plan,
    multiple_root_combined_proposal,
    patch_class_design_parser,
)


def test_generate_uses_one_combined_call_and_keeps_the_public_model(monkeypatch):
    schemas: list[type] = []

    def fake_parse(_messages, schema, **_kwargs):
        schemas.append(schema)
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            proposal = multiple_root_combined_proposal()
            proposal["fragment"]["Classes"][0]["operations"].append({
                "name": "showResult",
                "parameters": [{"name": "result", "type": "RequestResult"}],
                "returnType": "void",
                "stepRefs": ["UC1:main:2"],
            })
            proposal["calls"].insert(2, {
                "operationRef": "RequestBoundary.showResult",
                "parentCallIndex": 2,
            })
            proposal["calls"][3]["parentCallIndex"] = None
            proposal["calls"][4]["parentCallIndex"] = 4
            return proposal
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(multiple_entry_use_case()))

    assert schemas == [InventoryProposal, CombinedUnitProposal]
    collaboration = model.Collaborations[0]
    assert collaboration.collaboration_id == "UC1"
    assert collaboration.use_case_ids == ["UC1"]
    roots = [call for call in collaboration.calls if call.parent_call_id is None]
    assert len(roots) == 2
    assert collaboration.calls[3].argument_bindings[0].source_ref == "UC1::call:2#result"
    boundary = next(item for item in model.Classes if item.class_name == "RequestBoundary")
    assert [operation.name for operation in boundary.operations] == [
        "submit", "requestReceipt",
    ]
    assert boundary.operations[0].step_refs == ["UC1:main:1", "UC1:main:2"]


def test_combined_cache_skips_warm_calls_and_revalidates_the_hit(monkeypatch):
    schemas: list[type] = []

    def fake_parse(_messages, schema, **_kwargs):
        schemas.append(schema)
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            return multiple_root_combined_proposal()
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    cache = ProcessLocalAcceptedUnitCache(capacity=32)
    index = build_scenario_index(multiple_entry_use_case())

    cold = service.generate_class_model(index, cache=cache)

    schemas.clear()
    warm = service.generate_class_model(index, cache=cache)

    assert schemas == []
    assert warm.model_dump(by_alias=True) == cold.model_dump(by_alias=True)


def test_repeated_call_plan_regenerates_the_use_case_combined_unit(monkeypatch):
    combined_calls = 0
    call_plan_calls = 0

    def fake_parse(_messages, schema, **_kwargs):
        nonlocal call_plan_calls, combined_calls
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            combined_calls += 1
            proposal = multiple_root_combined_proposal()
            if combined_calls == 1:
                proposal["calls"][2]["parentCallIndex"] = 2
            return proposal
        if issubclass(schema, CallPlanProposal):
            call_plan_calls += 1
            plan = multiple_root_call_plan()
            plan["calls"][2]["parentCallIndex"] = 2
            return plan
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(multiple_entry_use_case()))

    assert (combined_calls, call_plan_calls) == (2, 2)
    assert [item.collaboration_id for item in model.Collaborations] == ["UC1"]
    assert sum(call.parent_call_id is None for call in model.Collaborations[0].calls) == 2


def test_resume_and_revision_keep_errors_and_use_case_ownership(monkeypatch):
    revised = False
    failure = ""
    combined_calls = 0
    call_plan_calls = 0
    previews: list[tuple] = []

    def fake_parse(_messages, schema, **_kwargs):
        nonlocal call_plan_calls, combined_calls, revised
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            combined_calls += 1
            return multiple_root_combined_proposal()
        if schema is FeedbackScope:
            return {"kind": "operation", "ids": ["UC1"]}
        if schema is OperationFragment:
            revised = True
            fragment = multiple_root_combined_proposal()["fragment"]
            fragment["Classes"][0]["operations"][0]["name"] = "send"
            return fragment
        if issubclass(schema, CallPlanProposal):
            call_plan_calls += 1
            if failure == "provider":
                raise RuntimeError("provider unavailable")
            if failure == "schema":
                return {"calls": []}
            plan = deepcopy(multiple_root_call_plan())
            if failure == "repeat":
                plan["calls"][2]["parentCallIndex"] = 2
            if revised:
                plan["calls"][0]["receiverOperationId"] = "RequestBoundary::send(request:RequestData)"
            return plan
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    monkeypatch.setattr(service.operations, "emit_preview", lambda *args: previews.append(args))
    index = build_scenario_index(multiple_entry_use_case())
    current = service.generate_class_model(index)
    current.Collaborations = []

    for failure, error_type in (
        ("provider", RuntimeError),
        ("schema", ValidationError),
    ):
        with pytest.raises(error_type):
            service.resume_class_model(index, current)

    failure = "repeat"
    before_repair = combined_calls, call_plan_calls
    previews.clear()
    resumed = service.resume_class_model(index, current)
    assert [item.collaboration_id for item in resumed.Collaborations] == ["UC1"]
    assert (combined_calls - before_repair[0], call_plan_calls - before_repair[1]) == (1, 2)
    assert {item[1] for item in previews} == {"operations", "collaborations"}

    failure = ""
    result = service.revise_class_model(
        resumed, index, "Rename the actor-facing operation.", {"UC1"},
    )

    boundary = next(item for item in result.Classes if item.class_name == "RequestBoundary")
    assert boundary.operations[0].name == "send"
    assert [item.collaboration_id for item in result.Collaborations] == ["UC1"]
