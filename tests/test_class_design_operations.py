"""클래스 연산 생성의 저장 계약과 실행을 막는 오류를 검사한다."""
from __future__ import annotations

import pytest

from app.design.services.class_diagram import operations, service
from app.design.services.class_diagram.models import AcceptedFragment, AcceptedInventory
from app.design.services.class_diagram.proposals import (
    OperationFragment,
)
from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.class_diagram.validation import (
    OperationContext,
    validate_operations,
)
from tests.class_design_fixtures import (
    operation_fragment,
    patch_class_design_parser,
    single_use_case,
    valid_parse_response,
)


def test_operation_generation_keeps_signature_data_types_in_the_persisted_model(monkeypatch):
    patch_class_design_parser(monkeypatch, valid_parse_response)
    model = service.generate_class_model(build_scenario_index(single_use_case()))
    model = model.model_dump(by_alias=True)

    assert [item["name"] for item in model["DataTypes"]] == [
        "RequestData", "RequestResult",
    ]

    # 요청과 시스템 처리가 한 문장에 합쳐진 유스케이스는 Boundary와 Control이 같은
    # 단계 근거를 공유해야 실제 Boundary -> Control 호출을 만들 수 있다.
    one_step = single_use_case()
    one_step["use_case_specs"][0]["main_scenario"] = [
        one_step["use_case_specs"][0]["main_scenario"][0]
    ]
    index = build_scenario_index(one_step)
    proposal = operation_fragment()
    for class_set in proposal["Classes"]:
        for operation in class_set["operations"]:
            operation["stepRefs"] = ["UC1:main:1"]
    inventory = AcceptedInventory.from_payload({
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
    })
    accepted = operations.normalize_operation_fragment(
        proposal,
        index,
        inventory,
        index.use_case("UC1"),
        reserved=[{
            "className": "RequestControl",
            "operations": [{
                "name": "process",
                "parameters": [{"name": "acceptedRequest", "type": "RequestData"}],
                "returnType": "RequestResult",
            }],
        }],
        allowed_step_ids=("UC1:main:1",),
    ).as_payload()
    control = next(
        item for item in accepted["Classes"]
        if item["className"] == "RequestControl"
    )
    assert control["operations"][0]["stepRefs"] == ["UC1:main:1"]
    assert control["operations"][0]["parameters"] == [
        {"name": "acceptedRequest", "type": "RequestData"},
    ]

def test_operation_validation_rejects_step_ref_outside_use_case_scope():
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
        ),
    )

    assert any(
        finding.rule_id == "class.operation.references"
        for finding in report.findings
    )


def test_state_backed_use_case_requires_an_entity_operation_only_when_scoped():
    index = build_scenario_index(single_use_case())
    inventory = {
        "Classes": [
            {"className": "RequestBoundary", "stereotype": "Boundary", "useCaseIds": ["UC1"]},
            {"className": "RequestControl", "stereotype": "Control", "useCaseIds": ["UC1"]},
            {
                "className": "Request",
                "stereotype": "Entity",
                "fields": ["id : uuid"],
                "identifier": ["id"],
                "useCaseIds": ["UC1"],
            },
        ],
        "DataTypes": [],
        "Relationships": [],
    }

    report = validate_operations(
        operation_fragment(),
        OperationContext(index, inventory, index.use_case("UC1")),
    )

    assert [
        finding.rule_id for finding in report.findings
        if finding.rule_id == "class.operation.state-ownership"
    ] == ["class.operation.state-ownership"]


def test_final_compose_keeps_operationless_class_referenced_by_an_entity_field():
    inventory = AcceptedInventory.from_payload({
        "Classes": [
            {
                "className": "RegistrationPeriod",
                "stereotype": "Entity",
                "fields": ["term : AcademicTerm"],
                "identifier": [],
                "useCaseIds": ["UC1"],
            },
            {
                "className": "AcademicTerm",
                "stereotype": "Entity",
                "fields": ["id : uuid"],
                "identifier": ["id"],
                "useCaseIds": ["UC1"],
            },
        ],
        "DataTypes": [],
        "Relationships": [],
    })
    fragment = AcceptedFragment("UC1", {
        "Classes": [{
            "className": "RegistrationPeriod",
            "operations": [{
                "name": "save",
                "parameters": [],
                "returnType": "void",
                "stepRefs": ["UC1:main:1"],
            }],
        }],
        "DataTypes": [],
    })

    model = operations.compose_operation_units(inventory, [fragment], final=True)

    assert {item.class_name for item in model.Classes} == {
        "AcademicTerm", "RegistrationPeriod",
    }


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
