"""클래스 설계 서비스의 결합 생성, 수리와 cache 경계를 검증한다."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram import feedback as feedback_stage
from app.design.services.class_diagram import generation, service
from app.design.services.class_diagram.cache import ProcessLocalAcceptedUnitCache
from app.design.services.class_diagram.proposals import (
    CallPlanProposal,
    CombinedUnitCall,
    CombinedUnitProposal,
    FeedbackScope,
    InventoryProposal,
    OperationFragment,
    ProposedCall,
)
from app.design.services.class_diagram.scenario import build_scenario_index
from tests.class_design_fixtures import (
    combined_unit_proposal,
    inventory_proposal,
    multiple_entry_use_case,
    multiple_root_call_plan,
    multiple_root_combined_proposal,
    patch_class_design_parser,
    single_use_case,
)


def test_exact_operation_and_call_targets_resolve_without_scope_llm(monkeypatch):
    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            return combined_unit_proposal()
        raise AssertionError(f"unexpected scope LLM call: {schema}")

    patch_class_design_parser(monkeypatch, fake_parse)
    index = build_scenario_index(single_use_case())
    model = service.generate_class_model(index)

    operation_id = model.Classes[0].operations[0].stable_id
    call_id = model.Collaborations[0].calls[0].stable_id
    legacy_operation_id = model.Classes[0].operations[0].operation_id
    legacy_call_id = model.Collaborations[0].calls[0].call_id
    assert operation_id is not None
    assert call_id is not None

    operation_scope = feedback_stage.feedback_scope(
        index, model, "Rename only this operation.", {operation_id},
    )
    call_scope = feedback_stage.feedback_scope(
        index, model, "Change only this call.", {call_id},
    )
    legacy_operation_scope = feedback_stage.feedback_scope(
        index, model, "Rename only this operation.", {legacy_operation_id},
    )
    legacy_call_scope = feedback_stage.feedback_scope(
        index, model, "Change only this call.", {legacy_call_id},
    )

    assert operation_scope == FeedbackScope(kind="operation", ids=["UC1"])
    assert call_scope == FeedbackScope(kind="collaboration", ids=["UC1"])
    assert legacy_operation_scope == operation_scope
    assert legacy_call_scope == call_scope


def test_selected_legacy_collaboration_keeps_its_id_and_rebinds_parent_ids(
    monkeypatch,
) -> None:
    index = build_scenario_index(single_use_case())
    current = BCEModel.model_validate({
        "Classes": [
            {
                "className": "RequestBoundary",
                "stereotype": "Boundary",
                "use_case_ids": ["UC1"],
                "operations": [{"name": "submit", "operationId": "ignored"}],
            },
            {
                "className": "RequestControl",
                "stereotype": "Control",
                "use_case_ids": ["UC1"],
                "operations": [{"name": "process", "operationId": "ignored"}],
            },
            {
                "className": "Request",
                "stereotype": "Entity",
                "use_case_ids": ["UC1"],
                "operations": [{"name": "save", "operationId": "ignored"}],
            },
        ],
        "Collaborations": [
            {
                "collaborationId": "UC1:main:1",
                "useCaseIds": ["UC1"],
                "calls": [
                    {
                        "callId": "ignored",
                        "receiverOperationId": "RequestBoundary::submit()",
                        "parentCallId": None,
                    },
                    {
                        "callId": "ignored",
                        "receiverOperationId": "RequestControl::process()",
                        "parentCallId": "UC1:main:1::call:1",
                    },
                ],
            }
        ],
    })
    replacement = BCEModel.model_validate({
        **current.model_dump(by_alias=True),
        "Collaborations": [
            {
                "collaborationId": "UC1",
                "useCaseIds": ["UC1"],
                "calls": [
                    {
                        "callId": "ignored",
                        "receiverOperationId": "RequestBoundary::submit()",
                        "parentCallId": None,
                    },
                    {
                        "callId": "ignored",
                        "receiverOperationId": "RequestControl::process()",
                        "parentCallId": "UC1::call:1",
                    },
                    {
                        "callId": "ignored",
                        "receiverOperationId": "Request::save()",
                        "parentCallId": "UC1::call:2",
                    },
                ],
            }
        ],
    }).Collaborations[0]
    monkeypatch.setattr(
        service,
        "_replace_use_cases",
        lambda *_args, **_kwargs: ({"UC1": replacement}, []),
    )
    skeleton = BCEModel.model_validate({
        **current.model_dump(by_alias=True),
        "Collaborations": [],
    })

    revised = service._replace_selected_collaborations(
        index,
        skeleton,
        current,
        [index.use_case("UC1")],
        feedback="Add the missing call.",
    )

    collaboration = revised.Collaborations[0]
    call_ids = {call.call_id for call in collaboration.calls}
    assert collaboration.collaboration_id == "UC1:main:1"
    assert [call.parent_call_id for call in collaboration.calls] == [
        None,
        "UC1:main:1::call:1",
        "UC1:main:1::call:2",
    ]
    assert all(
        call.parent_call_id is None or call.parent_call_id in call_ids
        for call in collaboration.calls
    )


def test_targeted_inventory_revision_allows_baseline_but_rejects_regression(monkeypatch):
    proposal = inventory_proposal()
    for name, scope in (("LegacyRecord", []), ("OtherRecord", ["UC1"])):
        proposal["items"].append({
            "name": name, "kind": "Entity", "description": "Legacy state",
            "fields": [{"name": "recordId", "type": "String"}],
            "identifier": ["recordId"], "values": [], "useCaseIds": scope,
        })
    relationship = {
        "source": "LegacyRecord", "target": "OtherRecord", "type": "Association",
        "sourceMultiplicity": "1", "targetMultiplicity": "*", "description": "legacy",
    }
    proposal["Relationships"] = [relationship, deepcopy(relationship)]
    current = feedback_stage.inventory._normalize_inventory(
        InventoryProposal.model_validate(proposal)
    )
    proposal = deepcopy(proposal)
    next(
        item for item in proposal["items"] if item["name"] == "RequestControl"
    )["description"] = "New coordination"
    monkeypatch.setattr(feedback_stage, "parse_structured", lambda *_args, **_kwargs: proposal)

    accepted = feedback_stage.AcceptedInventory.from_payload(current)
    revised = feedback_stage.propose_inventory_revision(
        build_scenario_index(single_use_case()),
        accepted,
        "Update only the control description.",
        {"RequestControl"},
    ).as_payload()

    control = next(item for item in revised["Classes"] if item["className"] == "RequestControl")
    assert control["description"] == "New coordination"
    legacy = next(item for item in revised["Classes"] if item["className"] == "LegacyRecord")
    assert legacy["useCaseIds"] == []

    proposal["Relationships"].append(deepcopy(relationship))
    with pytest.raises(ValueError, match="both directions"):
        feedback_stage.propose_inventory_revision(
            build_scenario_index(single_use_case()),
            accepted,
            "Change relation.",
            {"LegacyRecord"},
        )


def test_inventory_revision_preserves_valid_operations_and_calls(monkeypatch):
    combined_calls = 0

    def fake_parse(messages, schema, **_kwargs):
        nonlocal combined_calls
        if schema is InventoryProposal:
            proposal = inventory_proposal()
            if "currentInventory" in json.loads(messages[-1]["content"]):
                proposal["items"][1]["description"] = "Revised coordination"
            return proposal
        if schema is CombinedUnitProposal:
            combined_calls += 1
            return combined_unit_proposal()
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    index = build_scenario_index(single_use_case())
    current = service.generate_class_model(index)
    operations_before = [
        operation.model_dump(by_alias=True)
        for item in current.Classes for operation in item.operations
    ]
    calls_before = current.Collaborations[0].model_dump(by_alias=True)

    revised = service.revise_class_model(
        current, index, "Update the control description.", {"RequestControl"}
    )

    assert combined_calls == 1
    assert [
        operation.model_dump(by_alias=True)
        for item in revised.Classes for operation in item.operations
    ] == operations_before
    assert revised.Collaborations[0].model_dump(by_alias=True) == calls_before

    expanded_payload = feedback_stage.inventory_from_model(current).as_payload()
    expanded_payload["Classes"].append({
        "className": "AuditRecord", "stereotype": "Entity",
        "description": "Durable audit state", "fields": ["recordId : String"],
        "identifier": ["recordId"], "values": [], "useCaseIds": ["UC1"],
    })
    expanded = feedback_stage.AcceptedInventory.from_payload(expanded_payload)
    monkeypatch.setattr(
        feedback_stage, "propose_inventory_revision", lambda *_args, **_kwargs: expanded
    )
    monkeypatch.setattr(
        feedback_stage,
        "feedback_scope",
        lambda *_args, **_kwargs: FeedbackScope(kind="inventory", ids=[]),
    )
    rebuilds = []
    def rebuild(*_args, **_kwargs):
        rebuilds.append(True)
        return current
    monkeypatch.setattr(service.generation, "build_model", rebuild)

    service.revise_class_model(current, index, "Add durable audit state.", set())

    assert rebuilds == [True]

    type_payload = feedback_stage.inventory_from_model(current).as_payload()
    type_payload["DataTypes"].append({
        "name": "AuditId", "kind": "valueObject", "fields": ["value : String"],
        "values": [], "identifier": [], "useCaseIds": ["UC1"],
    })
    type_expanded = feedback_stage.AcceptedInventory.from_payload(type_payload)
    monkeypatch.setattr(
        feedback_stage, "propose_inventory_revision", lambda *_args, **_kwargs: type_expanded
    )

    service.revise_class_model(current, index, "Add a structural identifier type.", set())

    assert rebuilds == [True, True]


def test_generate_uses_one_combined_call_and_keeps_the_public_model(monkeypatch):
    schemas: list[type] = []

    def fake_parse(messages, schema, **_kwargs):
        schemas.append(schema)
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            payload = json.loads(messages[-1]["content"])
            assert payload["actorEntries"] == [
                {
                    "actorStepRef": "UC1:main:1",
                    "actor": "Member",
                    "requiredStepRefs": ["UC1:main:1", "UC1:main:2"],
                },
                {
                    "actorStepRef": "UC1:main:3",
                    "actor": "Member",
                    "requiredStepRefs": ["UC1:main:3", "UC1:main:4"],
                },
            ]
            proposal = multiple_root_combined_proposal()
            # 두 번째 사용자 입력 뒤의 Control이 첫 번째 입력값도 다시 쓰는 경우를
            # 포함한다. 같은 유스케이스의 앞 root 입력은 별도 LLM 수리 없이 연결된다.
            proposal["fragment"]["Classes"][1]["operations"][1]["parameters"].append({
                "name": "request", "type": "RequestData",
            })
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
    assert [binding.source_ref for binding in collaboration.calls[3].argument_bindings] == [
        "UC1::call:2#result",
        "UC1::call:1#request",
    ]
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
    combined_payloads: list[dict] = []
    call_plan_payloads: list[dict] = []

    # 루트와 자식을 구분하는 값은 null일 수 있지만 생략할 수는 없다. 이 계약을
    # 구조화 출력 단계에서 강제해야 모든 호출이 루트로 해석되는 일을 막을 수 있다.
    for schema, payload in (
        (ProposedCall, {"receiverOperationId": "RequestBoundary::submit()"}),
        (CombinedUnitCall, {"operationRef": "RequestBoundary.submit"}),
    ):
        with pytest.raises(ValidationError, match="parentCallIndex"):
            schema.model_validate(payload)

    def fake_parse(messages, schema, **_kwargs):
        nonlocal combined_calls
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            combined_calls += 1
            combined_payloads.append(json.loads(messages[-1]["content"]))
            proposal = multiple_root_combined_proposal()
            if combined_calls == 1:
                proposal["calls"][2]["parentCallIndex"] = 2
            return proposal
        if issubclass(schema, CallPlanProposal):
            call_plan_payloads.append(json.loads(messages[-1]["content"]))
            plan = multiple_root_call_plan()
            plan["calls"][2]["parentCallIndex"] = 2
            return plan
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(multiple_entry_use_case()))

    # 각 call plan은 한 번만 교체한다. 그 결과도 실패하면 오류 문구를 바꿔가며 같은
    # 범위에 머물지 않고 operation과 calls를 함께 고치는 결합 수리로 올라간다.
    assert combined_calls > 1
    assert combined_payloads[-1]["repairHistory"]
    assert call_plan_payloads[0]["previousPlan"]["calls"][2]["parentCallIndex"] == 2
    assert [item.collaboration_id for item in model.Collaborations] == ["UC1"]
    assert sum(call.parent_call_id is None for call in model.Collaborations[0].calls) == 2


def test_repeated_invalid_call_plan_stops_as_generation_stalled(monkeypatch):
    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            proposal = multiple_root_combined_proposal()
            proposal["calls"][2]["parentCallIndex"] = 2
            return proposal
        if issubclass(schema, CallPlanProposal):
            plan = multiple_root_call_plan()
            plan["calls"][2]["parentCallIndex"] = 2
            return plan
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)

    with pytest.raises(generation.GenerationStalled) as caught:
        service.generate_class_model(build_scenario_index(multiple_entry_use_case()))

    assert caught.value.unit_id == "UC1"
    assert "last finding: ValueError:" in str(caught.value)


def test_invalid_operation_repairs_stop_as_generation_stalled(monkeypatch):
    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is CombinedUnitProposal:
            proposal = combined_unit_proposal()
            proposal["fragment"]["DataTypes"][0]["fields"][0]["type"] = "MissingType"
            return proposal
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)

    with pytest.raises(generation.GenerationStalled) as caught:
        service.generate_class_model(build_scenario_index(single_use_case()))

    assert caught.value.unit_id == "UC1"
    assert "cached operation fragment UC1" in caught.value.finding


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
    assert (combined_calls - before_repair[0], call_plan_calls - before_repair[1]) == (1, 1)
    assert {item[1] for item in previews} == {"operations", "collaborations"}

    failure = ""
    before_revision_call_plans = call_plan_calls
    parents_before = [
        call.parent_call_id for call in resumed.Collaborations[0].calls
    ]
    result = service.revise_class_model(
        resumed, index, "Rename the actor-facing operation.", {"UC1"},
    )

    boundary = next(item for item in result.Classes if item.class_name == "RequestBoundary")
    assert boundary.operations[0].name == "send"
    assert [item.collaboration_id for item in result.Collaborations] == ["UC1"]
    assert call_plan_calls == before_revision_call_plans
    assert [
        call.parent_call_id for call in result.Collaborations[0].calls
    ] == parents_before
