"""클래스 설계 서비스의 호출 수와 accepted-unit cache 경계를 검증한다."""
from __future__ import annotations

import inspect
import json

import pytest

from app.design.schemas.architecture_state import ArchitectureState
from app.design.services.class_diagram import service
from app.design.services.class_diagram.cache import ProcessLocalAcceptedUnitCache
from app.design.services.class_diagram.proposals import (
    CallPlanProposal,
    InventoryProposal,
    OperationFragment,
)
from app.design.services.class_diagram.scenario import build_scenario_index
from app.validation import Finding, ValidationReport
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


def test_operation_handoff_repair_continues_past_one_round(monkeypatch):
    operation_calls = 0
    call_plan_calls = 0

    def fake_parse(messages, schema, **_kwargs):
        nonlocal operation_calls, call_plan_calls
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            operation_calls += 1
            if operation_calls == 1:
                return operation_fragment(unsourceable=True)
            if operation_calls == 3:
                assert "Accumulated operation handoff repair history" in messages[-1][
                    "content"
                ]
            return operation_fragment()
        if issubclass(schema, CallPlanProposal):
            call_plan_calls += 1
            plan = call_plan()
            if call_plan_calls <= 2:
                plan["calls"][1]["receiverOperationId"] = (
                    "RequestControl::process(other:Boolean)"
                )
            elif call_plan_calls <= 4:
                plan["calls"][1]["parentCallIndex"] = None
            return plan
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(single_use_case()))

    assert operation_calls == 3
    assert call_plan_calls == 5
    assert len(model.Collaborations) == 1


def test_generate_raises_with_repair_history_when_collaboration_repeats(monkeypatch):
    """같은 협업 실패가 반복되면 불완전 모델 대신 누적 원인을 반환한다."""

    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            return operation_fragment()
        if issubclass(schema, CallPlanProposal):
            plan = call_plan()
            plan["calls"][1]["parentCallIndex"] = None
            return plan
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    index = build_scenario_index(single_use_case())

    with pytest.raises(ValueError) as raised:
        service.generate_class_model(index)

    message = str(raised.value)
    assert "repair stalled" in message
    assert "UC1" in message
    assert "every delegated call requires an earlier parent" in message
    assert "Accumulated repair history" in message


def test_resume_raises_with_repair_history_when_collaboration_repeats(monkeypatch):
    """재개 중 수리도 실패 협업을 누락한 채 성공으로 끝나서는 안 된다."""

    invalid_call_plan = False

    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            return operation_fragment()
        if issubclass(schema, CallPlanProposal):
            plan = call_plan()
            if invalid_call_plan:
                plan["calls"][1]["parentCallIndex"] = None
            return plan
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    index = build_scenario_index(single_use_case())
    current = service.generate_class_model(index)
    current.Collaborations = []
    invalid_call_plan = True

    with pytest.raises(ValueError) as raised:
        service.resume_class_model(index, current)

    message = str(raised.value)
    assert "repair stalled" in message
    assert "UC1" in message
    assert "every delegated call requires an earlier parent" in message
    assert "Accumulated repair history" in message


def test_generate_does_not_ignore_final_validation_findings(monkeypatch):
    """조각 검사를 통과해도 완성 모델 검증 finding이 있으면 저장하지 않는다."""

    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            return operation_fragment()
        if issubclass(schema, CallPlanProposal):
            return call_plan()
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    monkeypatch.setattr(
        service,
        "validate_class_model",
        lambda _model, _index: ValidationReport(
            status="findings",
            findings=(
                Finding(
                    "class.model.final-test",
                    "synthetic final validation finding",
                    "Collaborations",
                ),
            ),
        ),
    )

    with pytest.raises(ValueError, match="synthetic final validation finding"):
        service.generate_class_model(build_scenario_index(single_use_case()))


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
    """공개 service가 cache를 전달하고 warm hit에서는 외부 호출을 생략한다."""

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
    assert len(calls) == 3
    assert calls[0] is InventoryProposal
    assert calls[1] is OperationFragment
    assert issubclass(calls[2], CallPlanProposal)

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


def test_accepted_unit_cache_is_outside_checkpoint_and_artifact_json_contract() -> None:
    """프로세스 cache 객체와 key metadata를 저장 state에 직렬화하지 않는다."""

    cache = ProcessLocalAcceptedUnitCache(capacity=32)
    state: ArchitectureState = {
        "extracted_bce_classes": {
            "Classes": [],
            "DataTypes": [],
            "Relationships": [],
            "Collaborations": [],
        },
        "class_diagram_puml": "@startuml\n@enduml",
    }

    assert all("cache" not in key.lower() for key in ArchitectureState.__annotations__)
    assert not any(
        hasattr(cache, serializer)
        for serializer in ("model_dump", "model_dump_json", "to_json", "save")
    )

    stored_json = json.dumps(state, ensure_ascii=False, sort_keys=True)
    assert "accepted-unit" not in stored_json
    assert "cacheVersionDigest" not in stored_json
    assert "_values" not in stored_json
