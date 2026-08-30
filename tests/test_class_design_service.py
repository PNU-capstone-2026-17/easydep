"""클래스 설계 서비스의 결합 생성, 수리와 cache 경계를 검증한다."""
from __future__ import annotations

from copy import deepcopy

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
            return multiple_root_combined_proposal()
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(multiple_entry_use_case()))

    assert schemas == [InventoryProposal, CombinedUnitProposal]
    assert [item.class_name for item in model.Classes] == [
        "RequestBoundary", "RequestControl",
    ]
    assert {item.name for item in model.DataTypes} == {
        "ReceiptResult", "RequestData", "RequestResult",
    }
    collaboration = model.Collaborations[0]
    assert collaboration.collaboration_id == "UC1"
    assert collaboration.use_case_ids == ["UC1"]
    roots = [call for call in collaboration.calls if call.parent_call_id is None]
    assert [call.receiver_operation_id for call in roots] == [
        "RequestBoundary::submit(request:RequestData)",
        "RequestBoundary::requestReceipt()",
    ]
    assert collaboration.calls[3].argument_bindings[0].source_ref == (
        "UC1::call:2#result"
    )


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
    assert schemas == [InventoryProposal, CombinedUnitProposal]

    schemas.clear()
    warm = service.generate_class_model(index, cache=cache)

    assert schemas == []
    assert warm.model_dump(by_alias=True) == cold.model_dump(by_alias=True)


def test_resume_uses_only_the_existing_call_plan_schema(monkeypatch):
    schemas: list[type] = []

    def fake_parse(_messages, schema, **_kwargs):
        schemas.append(schema)
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            return multiple_root_combined_proposal()
        if issubclass(schema, CallPlanProposal):
            return multiple_root_call_plan()
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    index = build_scenario_index(multiple_entry_use_case())
    current = service.generate_class_model(index)
    current.Collaborations = []
    schemas.clear()

    resumed = service.resume_class_model(index, current)

    assert len(schemas) == 1 and issubclass(schemas[0], CallPlanProposal)
    assert [item.collaboration_id for item in resumed.Collaborations] == ["UC1"]
    assert sum(call.parent_call_id is None for call in resumed.Collaborations[0].calls) == 2


def test_revision_keeps_separate_operation_and_call_plan_schemas(monkeypatch):
    schemas: list[type] = []
    revised = False

    def fake_parse(_messages, schema, **_kwargs):
        nonlocal revised
        schemas.append(schema)
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            return multiple_root_combined_proposal()
        if schema is FeedbackScope:
            return {"kind": "operation", "ids": ["UC1"]}
        if schema is OperationFragment:
            revised = True
            fragment = multiple_root_combined_proposal()["fragment"]
            fragment["Classes"][0]["operations"][0]["name"] = "send"
            return fragment
        if issubclass(schema, CallPlanProposal):
            plan = deepcopy(multiple_root_call_plan())
            if revised:
                plan["calls"][0]["receiverOperationId"] = (
                    "RequestBoundary::send(request:RequestData)"
                )
            return plan
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    index = build_scenario_index(multiple_entry_use_case())
    current = service.generate_class_model(index)
    schemas.clear()

    result = service.revise_class_model(
        current,
        index,
        "Rename the actor-facing operation.",
        {"UC1"},
    )

    assert OperationFragment in schemas
    assert any(issubclass(schema, CallPlanProposal) for schema in schemas)
    assert CombinedUnitProposal not in schemas
    boundary = next(item for item in result.Classes if item.class_name == "RequestBoundary")
    assert boundary.operations[0].name == "send"
    assert [item.collaboration_id for item in result.Collaborations] == ["UC1"]
