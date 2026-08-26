import json

import pytest
from pydantic import ValidationError

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram import extractor
from app.design.services.class_diagram.extractor import DomainStructureProposal


def test_operations_are_reusable_signatures_without_execution_metadata():
    model = BCEModel.model_validate({
        "Classes": [{
            "className": "OrderControl", "stereotype": "Control",
            "use_case_ids": ["UC1"],
            "operations": [{
                "operationId": "ignored", "name": "placeOrder",
                "parameters": [{"name": "request", "type": "OrderRequest"}],
                "returnType": "Order", "stepRefs": ["UC1:main:2"],
            }],
        }],
    })

    operation = model.model_dump(by_alias=True)["Classes"][0]["operations"][0]
    assert operation == {
        "operationId": "OrderControl::placeOrder(request:OrderRequest)",
        "name": "placeOrder",
        "parameters": [{"name": "request", "type": "OrderRequest"}],
        "returnType": "Order",
        "stepRefs": ["UC1:main:2"],
    }


@pytest.mark.parametrize("legacy_key", ["methods", "actorEntry", "inputBindings"])
def test_legacy_execution_fields_are_not_persistable(legacy_key: str):
    operation = {
        "operationId": "ignored", "name": "placeOrder", "parameters": [],
        "returnType": "void", "stepRefs": ["UC1:main:2"],
    }
    payload = {
        "Classes": [{
            "className": "OrderControl", "stereotype": "Control",
            "use_case_ids": ["UC1"], "operations": [operation],
        }],
    }
    if legacy_key == "methods":
        payload["Classes"][0][legacy_key] = ["placeOrder(): void"]
    else:
        operation[legacy_key] = False if legacy_key == "actorEntry" else []

    with pytest.raises(ValidationError, match=legacy_key):
        BCEModel.model_validate(payload)


def test_data_types_are_minimal_and_concrete():
    model = BCEModel.model_validate({
        "DataTypes": [
            {"name": "Address", "kind": "valueObject", "fields": ["line1 : String"]},
            {"name": "OrderStatus", "kind": "enumeration", "values": ["PENDING", "PAID"]},
        ],
    })

    assert [item.kind for item in model.DataTypes] == ["valueObject", "enumeration"]
    with pytest.raises(ValidationError, match="enumeration needs values"):
        BCEModel.model_validate({"DataTypes": [{"name": "Empty", "kind": "enumeration"}]})


def _domain_class(*, stereotype: str = "Control", fields=None, identifier=None, operations=None) -> dict:
    return {
        "className": "OrderControl", "stereotype": stereotype,
        "use_case_ids": ["UC1"], "fields": fields or [], "identifier": identifier or [],
        "operations": operations if operations is not None else [{
            "operationId": "OrderControl::place()", "name": "place", "parameters": [],
            "returnType": "void", "stepRefs": ["UC1:main:1"],
        }],
    }


@pytest.mark.parametrize(
    ("proposal", "message"),
    [
        ({"Classes": []}, "at least 1 item"),
        ({"Classes": [_domain_class(operations=[])]}, "need at least one operation"),
        ({"Classes": [_domain_class(operations=[{
            "operationId": "OrderControl::place()", "name": "place", "parameters": [],
            "returnType": "void", "stepRefs": [],
        }])]}, "at least 1 item"),
        ({"Classes": [_domain_class(operations=[{
            "operationId": "OrderControl::place()", "name": "place", "parameters": [],
            "returnType": "void", "stepRefs": ["UC1:1"],
        }])]}, "canonical main or extension"),
        ({"Classes": [_domain_class(operations=[{
            "operationId": "OrderControl::place()", "name": "place", "parameters": [],
            "returnType": "MissingResult", "stepRefs": ["UC1:main:1"],
        }])]}, "all field, parameter, and return types must resolve"),
        ({"Classes": [_domain_class(fields=["orderId : String {identifier}"])]}, "inline {identifier}"),
        ({"Classes": [_domain_class()], "Relationships": [{"source": "A", "target": "B", "type": "Dependency"}]}, "Input should be"),
    ],
)
def test_domain_structure_proposal_rejects_empty_or_behavioral_structure(proposal, message):
    with pytest.raises(ValidationError, match=message):
        DomainStructureProposal.model_validate(proposal)


def test_domain_structure_proposal_has_no_collaboration_escape_hatch():
    proposal = {"Classes": [_domain_class()], "Collaborations": []}

    with pytest.raises(ValidationError, match="Collaborations"):
        DomainStructureProposal.model_validate(proposal)


def test_structure_projection_drops_only_dangling_identifier(monkeypatch):
    proposal = {
        "Classes": [{
            **_domain_class(
                stereotype="Entity",
                fields=["orderId : String"],
                identifier=["missingId"],
                operations=[],
            ),
            "className": "Order",
        }],
    }
    monkeypatch.setattr(extractor, "parse_structured", lambda *_args, **_kwargs: proposal)

    model = extractor.run_domain_structure_parse([], operation="test")

    assert model["Classes"][0]["fields"] == ["orderId : String"]
    assert model["Classes"][0]["identifier"] == []


def test_structure_projection_honors_explicit_value_object_marker(monkeypatch):
    control = _domain_class(operations=[{
        "operationId": "OrderControl::place()",
        "name": "place",
        "parameters": [],
        "returnType": "Receipt",
        "stepRefs": ["UC1:main:1"],
    }])
    receipt = {
        **_domain_class(
            stereotype="Entity",
            fields=["accepted : boolean"],
            operations=[],
        ),
        "className": "Receipt",
        "description": "ValueObject",
    }
    monkeypatch.setattr(
        extractor,
        "parse_structured",
        lambda *_args, **_kwargs: {"Classes": [control, receipt]},
    )

    model = extractor.run_domain_structure_parse([], operation="test")

    assert [item["className"] for item in model["Classes"]] == ["OrderControl"]
    assert model["DataTypes"] == [{
        "name": "Receipt",
        "kind": "valueObject",
        "fields": ["accepted : boolean"],
        "values": [],
    }]


def test_scenario_structure_contract_catches_missing_state_and_step_coverage():
    scenario = {
        "use_cases": [{"id": "UC1", "primary_actor": "Buyer"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [
                {"step_number": 1}, {"step_number": 2},
            ],
        }],
    }
    model = {
        "Classes": [
            {
                "className": "OrderBoundary",
                "stereotype": "Boundary",
                "use_case_ids": ["UC1"],
                "operations": [{"name": "submit", "stepRefs": ["UC1:main:1"]}],
            },
            {
                "className": "Order",
                "stereotype": "Entity",
                "fields": [],
                "use_case_ids": ["UC1"],
                "operations": [],
            },
        ],
    }

    issues = extractor._scenario_structure_issues(model, scenario)

    assert "Entity Order has no persistent fields" in issues
    assert "Entity Order has no state-bearing operations" in issues
    assert "actor-driven use case UC1 has no Control class" in issues
    assert any("UC1:main:2" in issue for issue in issues)


def test_extraction_repairs_only_scenario_dependent_structure_once(monkeypatch):
    scenario = {
        "use_cases": [{"id": "UC1", "primary_actor": "Buyer"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [{"step_number": 1}, {"step_number": 2}],
        }],
    }
    boundary = {
        "className": "OrderBoundary",
        "stereotype": "Boundary",
        "fields": [],
        "identifier": [],
        "use_case_ids": ["UC1"],
        "operations": [{"name": "submit", "stepRefs": ["UC1:main:1"]}],
    }
    initial = {
        "Classes": [
            boundary,
            {
                "className": "OrderControl",
                "stereotype": "Control",
                "fields": [],
                "identifier": [],
                "use_case_ids": ["UC1"],
                "operations": [{"name": "place", "stepRefs": ["UC1:main:1"]}],
            },
        ],
        "DataTypes": [],
        "Relationships": [],
        "Collaborations": [],
    }
    repaired = {
        **initial,
        "Classes": [
            boundary,
            {
                **initial["Classes"][1],
                "operations": [{"name": "place", "stepRefs": ["UC1:main:2"]}],
            },
        ],
    }
    calls: list[str] = []
    monkeypatch.setattr(extractor, "run_bce_skeleton_parse", lambda _messages: initial)

    def repair(_messages, *, operation, **_kwargs):
        calls.append(operation)
        return repaired

    monkeypatch.setattr(extractor, "run_domain_structure_parse", repair)

    result = extractor.extract_bce_classes_from_scenario(json.dumps(scenario))

    assert result is repaired
    assert calls == ["DomainStructureContractRepair"]
