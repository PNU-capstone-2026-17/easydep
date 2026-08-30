"""클래스 연산 생성의 저장 계약과 실행을 막는 오류를 검사한다."""
from __future__ import annotations

import pytest

from app.design.services.class_diagram import service
from app.design.services.class_diagram.proposals import (
    CallPlanProposal,
    InventoryProposal,
    OperationFragment,
)
from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.class_diagram.validation import (
    OperationContext,
    validate_operations,
)
from tests.class_design_fixtures import (
    call_plan,
    inventory_proposal,
    operation_fragment,
    patch_class_design_parser,
    single_use_case,
)


def test_operation_generation_keeps_signature_data_types_in_the_persisted_model(monkeypatch):
    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            return operation_fragment()
        if issubclass(schema, CallPlanProposal):
            return call_plan()
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(single_use_case()))
    model = model.model_dump(by_alias=True)

    assert [item["name"] for item in model["DataTypes"]] == [
        "RequestData", "RequestResult",
    ]


def test_vertical_service_does_not_fabricate_an_unsourceable_parameter(monkeypatch):
    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            return operation_fragment(unsourceable=True)
        if issubclass(schema, CallPlanProposal):
            plan = call_plan()
            plan["calls"][1]["receiverOperationId"] = "RequestControl::process(other:Boolean)"
            return plan
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    with pytest.raises(ValueError, match="parameter source must be"):
        service.generate_class_model(build_scenario_index(single_use_case()))


def test_operation_repair_continues_past_one_replacement(monkeypatch):
    invalid_one = operation_fragment()
    invalid_one["Classes"][0]["operations"][0]["stepRefs"] = ["UC9:main:1"]
    invalid_two = operation_fragment()
    invalid_two["Classes"][0]["operations"][0]["stepRefs"] = ["UC8:main:1"]
    candidates = [invalid_one, invalid_two, operation_fragment()]
    operation_calls = 0

    def fake_parse(_messages, schema, **_kwargs):
        nonlocal operation_calls
        if schema is InventoryProposal:
            return inventory_proposal()
        if schema is OperationFragment:
            candidate = candidates[operation_calls]
            operation_calls += 1
            return candidate
        if issubclass(schema, CallPlanProposal):
            return call_plan()
        raise AssertionError(schema)

    patch_class_design_parser(monkeypatch, fake_parse)
    model = service.generate_class_model(build_scenario_index(single_use_case()))

    assert operation_calls == 3
    assert len(model.Collaborations) == 1


def test_operation_validation_rejects_step_ref_outside_execution_slice():
    index = build_scenario_index(single_use_case())
    fragment = operation_fragment()
    fragment["Classes"][0]["operations"][0]["stepRefs"] = ["UC1:main:2"]

    report = validate_operations(
        fragment,
        OperationContext(
            index,
            {
                "Classes": [
                    {
                        "className": "RequestBoundary",
                        "stereotype": "Boundary",
                        "useCaseIds": ["UC1"],
                    },
                    {
                        "className": "RequestControl",
                        "stereotype": "Control",
                        "useCaseIds": ["UC1"],
                    },
                ],
                "DataTypes": [],
                "Relationships": [],
            },
            index.use_case("UC1"),
            ("UC1:main:1",),
            ("UC1:main:1",),
        ),
    )

    assert any(
        finding.rule_id == "class.operation.references"
        for finding in report.findings
    )


def test_operation_contract_rejects_duplicate_parameter_names():
    fragment = operation_fragment()
    parameters = fragment["Classes"][0]["operations"][0]["parameters"]
    parameters.append({"name": parameters[0]["name"], "type": "String"})

    with pytest.raises(ValueError, match="parameter names"):
        OperationFragment.model_validate(fragment)


def test_operation_contract_rejects_duplicate_class_declarations():
    fragment = operation_fragment()
    fragment["Classes"].append({
        "className": fragment["Classes"][0]["className"],
        "operations": [fragment["Classes"][0]["operations"][0]],
    })

    with pytest.raises(ValueError, match="each class once"):
        OperationFragment.model_validate(fragment)
