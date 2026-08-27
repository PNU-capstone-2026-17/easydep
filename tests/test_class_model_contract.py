import json

import pytest
from pydantic import ValidationError

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram import extractor
from app.design.services.class_diagram.extractor import DomainStructureProposal
from app.design.services.class_diagram.type_system import (
    GENERIC_CONTAINERS,
    PRIMITIVES,
    structure_type_inventory,
)


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


def test_trace_references_are_ordered_sets_at_the_class_contract_boundary():
    model = BCEModel.model_validate({
        "Classes": [{
            "className": "OrderControl", "stereotype": "Control",
            "use_case_ids": ["UC1"],
            "operations": [{
                "operationId": "ignored", "name": "place", "parameters": [],
                "returnType": "void",
                "stepRefs": ["UC1:main:1", "UC1:main:2", "UC1:main:1"],
            }],
        }],
        "Collaborations": [{
            "collaborationId": "UC1:main:1", "useCaseIds": ["UC1"],
            "calls": [{
                "callId": "ignored", "receiverOperationId": "OrderControl::place()",
                "stepRefs": ["UC1:main:1", "UC1:main:1"],
            }],
        }],
    }).model_dump(by_alias=True)

    assert model["Classes"][0]["operations"][0]["stepRefs"] == [
        "UC1:main:1", "UC1:main:2",
    ]
    assert model["Collaborations"][0]["calls"][0]["stepRefs"] == [
        "UC1:main:1",
    ]


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


def test_structure_type_inventory_is_derived_from_the_shared_type_system():
    inventory = structure_type_inventory()

    assert inventory == {
        "primitives": tuple(sorted(PRIMITIVES)),
        "genericContainers": tuple(sorted(GENERIC_CONTAINERS)),
        "arraySyntax": "byte[]",
    }


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
        ({
            "Classes": [_domain_class()],
            "DataTypes": [{
                "name": "OrderControl",
                "kind": "valueObject",
                "fields": ["value : String"],
            }],
        }, "Class and DataType names must not overlap"),
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


def test_structure_omits_relation_without_independently_grounded_entities():
    proposal = {
        "Classes": [
            {
                **_domain_class(
                    stereotype="Entity",
                    fields=["orderId : String"],
                ),
                "className": "Order",
            },
            {
                **_domain_class(stereotype="Entity", operations=[]),
                "className": "Buyer",
            },
        ],
        "Relationships": [{
            "source": "Order", "target": "Buyer", "type": "Association",
        }],
    }

    model = DomainStructureProposal.model_validate(proposal)

    assert model.Relationships == []


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


def test_structure_projection_keeps_only_transitively_reachable_data_types(monkeypatch):
    proposal = {
        "Classes": [_domain_class(operations=[{
            "operationId": "ignored",
            "name": "place",
            "parameters": [{"name": "request", "type": "OrderRequest"}],
            "returnType": "void",
            "stepRefs": ["UC1:main:1"],
        }])],
        "DataTypes": [
            {
                "name": "OrderRequest", "kind": "valueObject",
                "fields": ["address : Address"],
            },
            {
                "name": "Address", "kind": "valueObject",
                "fields": ["line1 : String"],
            },
            {
                "name": "UnusedRole", "kind": "enumeration",
                "values": ["ADMIN"],
            },
        ],
    }
    monkeypatch.setattr(extractor, "parse_structured", lambda *_args, **_kwargs: proposal)

    model = extractor.run_domain_structure_parse([], operation="test")

    assert [item["name"] for item in model["DataTypes"]] == [
        "OrderRequest", "Address",
    ]


def test_structure_projection_finds_data_types_in_python_named_return_fields(monkeypatch):
    proposal = {
        "Classes": [_domain_class(operations=[{
            "operation_id": "ignored",
            "name": "place",
            "parameters": [],
            "return_type": "Receipt",
            "step_refs": ["UC1:main:1"],
        }])],
        "DataTypes": [{
            "name": "Receipt", "kind": "valueObject",
            "fields": ["accepted : boolean"],
        }],
    }
    monkeypatch.setattr(extractor, "parse_structured", lambda *_args, **_kwargs: proposal)

    model = extractor.run_domain_structure_parse([], operation="test")

    assert [item["name"] for item in model["DataTypes"]] == ["Receipt"]


def test_scenario_structure_contract_catches_missing_fields_and_step_coverage():
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
    assert "Entity Order has no state-bearing operation for declared use cases: ['UC1']" in issues
    assert "actor-driven use case UC1 has no Control class" in issues
    assert any("UC1:main:2" in issue for issue in issues)


def test_class_operation_step_refs_stay_within_declared_use_case_scope():
    scenario = {
        "use_case_specs": [
            {"use_case_id": "UC1", "main_scenario": [{"step_number": 1}]},
            {"use_case_id": "UC2", "main_scenario": [{"step_number": 1}]},
        ],
    }
    model = {
        "Classes": [{
            "className": "Order", "stereotype": "Entity",
            "fields": ["orderId : uuid"], "identifier": ["orderId"],
            "use_case_ids": ["UC1"],
            "operations": [{
                "name": "load", "stepRefs": ["UC2:main:1"],
            }],
        }],
    }

    issues = extractor._scenario_structure_issues(model, scenario)

    assert "Order.load traces use cases outside its class scope: ['UC2']" in issues
    assert "Entity Order has no state-bearing operation for declared use cases: ['UC1']" in issues


def test_extraction_repairs_only_scenario_dependent_structure_once(monkeypatch):
    scenario = {
        "use_cases": [{"id": "UC1", "primary_actor": "Buyer"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [
                {"step_number": 1, "sentence": "Buyer submits an order."},
                {"step_number": 2, "sentence": "System records the order."},
            ],
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


def test_extraction_uses_one_global_then_bounded_focused_repairs(monkeypatch):
    scenario = {
        "use_cases": [{"id": "UC1", "primary_actor": "Buyer"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [
                {"step_number": 1, "sentence": "Buyer submits an order."},
                {"step_number": 2, "sentence": "System records the order."},
            ],
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
    control = {
        "className": "OrderControl",
        "stereotype": "Control",
        "fields": [],
        "identifier": [],
        "use_case_ids": ["UC1"],
        "operations": [{"name": "place", "stepRefs": ["UC1:main:1"]}],
    }
    initial = {"Classes": [boundary, control], "DataTypes": [], "Relationships": []}
    still_invalid = {**initial, "Classes": [boundary, {
        **control,
        "operations": [{"name": "place", "stepRefs": ["UC1:main:1", "UC1:main:missing"]}],
    }]}
    valid = {**initial, "Classes": [boundary, {
        **control,
        "operations": [{"name": "place", "stepRefs": ["UC1:main:2"]}],
    }]}
    calls: list[str] = []
    focused_findings: list[list[str]] = []
    monkeypatch.setattr(extractor, "run_bce_skeleton_parse", lambda _messages: initial)
    monkeypatch.setattr(extractor.settings, "design_max_repair_iters", 3)

    def repair(messages, *, operation, **_kwargs):
        calls.append(operation)
        assert "UC1:main:2" in messages[-1]["content"]
        return still_invalid

    def focused(candidate, _scenario, findings):
        focused_findings.append(findings)
        return still_invalid if len(focused_findings) == 1 else valid

    monkeypatch.setattr(extractor, "run_domain_structure_parse", repair)
    monkeypatch.setattr(extractor, "_repair_operations_by_use_case", focused)

    result = extractor.extract_bce_classes_from_scenario(json.dumps(scenario))

    assert result is valid
    assert calls == ["DomainStructureContractRepair"]
    assert len(focused_findings) == 2
    assert any("UC1:main:missing" in issue for issue in focused_findings[1])


def test_focused_operation_repair_cannot_rewrite_other_use_cases_or_structure():
    candidate = BCEModel.model_validate({
        "Classes": [{
            "className": "OrderBoundary",
            "stereotype": "Boundary",
            "fields": [],
            "use_case_ids": ["UC1", "UC2"],
            "operations": [
                {
                    "operationId": "ignored", "name": "submit",
                    "parameters": [], "returnType": "void",
                    "stepRefs": ["UC1:main:1"],
                },
                {
                    "operationId": "ignored", "name": "inspect",
                    "parameters": [], "returnType": "String",
                    "stepRefs": ["UC2:main:1"],
                },
            ],
        }],
        "DataTypes": [],
        "Relationships": [],
    }).model_dump(by_alias=True)
    repair = extractor.DomainOperationRepair.model_validate({
        "Classes": [{
            "className": "OrderBoundary",
            "operations": [{
                "operationId": "ignored", "name": "submitOrder",
                "parameters": [{"name": "orderId", "type": "uuid"}],
                "returnType": "boolean", "stepRefs": ["UC1:main:1"],
            }],
        }],
    })

    merged = extractor._merge_operation_repair(candidate, repair, "UC1")

    order = merged["Classes"][0]
    assert [item["name"] for item in order["operations"]] == [
        "inspect", "submitOrder",
    ]
    assert order["operations"][0] == candidate["Classes"][0]["operations"][1]
    assert merged["DataTypes"] == candidate["DataTypes"]
    assert merged["Relationships"] == candidate["Relationships"]


def test_focused_operation_repair_reuses_an_identical_reserved_signature():
    candidate = BCEModel.model_validate({
        "Classes": [{
            "className": "Registration",
            "stereotype": "Entity",
            "fields": ["id : uuid"],
            "use_case_ids": ["UC1", "UC2"],
            "operations": [{
                "operationId": "ignored", "name": "save",
                "parameters": [{"name": "studentId", "type": "uuid"}],
                "returnType": "boolean", "stepRefs": ["UC1:main:2"],
            }],
        }],
    }).model_dump(by_alias=True)
    repair = extractor.DomainOperationRepair.model_validate({
        "Classes": [{
            "className": "Registration",
            "operations": [{
                "operationId": "ignored", "name": "save",
                "parameters": [{"name": "studentId", "type": "uuid"}],
                "returnType": "boolean", "stepRefs": ["UC2:main:3"],
            }],
        }],
    })

    merged = extractor._merge_operation_repair(candidate, repair, "UC2")

    operations = merged["Classes"][0]["operations"]
    assert len(operations) == 1
    assert operations[0]["stepRefs"] == ["UC1:main:2", "UC2:main:3"]


def test_scenario_signature_contract_requires_boundary_to_supply_control_inputs():
    scenario = {
        "use_cases": [{"id": "UC1", "primary_actor": "Buyer"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "primary_actor": "Buyer",
            "main_scenario": [
                {"step_number": 1, "sentence": "Buyer selects an offering."},
                {"step_number": 2, "sentence": "System registers the buyer."},
            ],
        }],
    }
    model = BCEModel.model_validate({
        "Classes": [
            {
                "className": "RegistrationBoundary",
                "stereotype": "Boundary",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ignored",
                    "name": "register",
                    "parameters": [{"name": "offeringId", "type": "uuid"}],
                    "returnType": "void",
                    "stepRefs": ["UC1:main:1"],
                }],
            },
            {
                "className": "RegistrationControl",
                "stereotype": "Control",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ignored",
                    "name": "register",
                    "parameters": [
                        {"name": "offeringId", "type": "uuid"},
                        {"name": "buyerId", "type": "uuid"},
                    ],
                    "returnType": "void",
                    "stepRefs": ["UC1:main:2"],
                }],
            },
        ],
    }).model_dump(by_alias=True)

    issues = extractor._scenario_signature_issues(model, scenario)

    assert len(issues) == 1
    assert "cannot form a sourceable BCE call tree" in issues[0]

    model["Classes"][0]["operations"][0]["parameters"].append({
        "name": "buyerId", "type": "uuid",
    })
    assert extractor._scenario_signature_issues(model, scenario) == []


def test_boundary_signature_separates_actor_entries_after_system_behavior():
    scenario = {
        "use_cases": [{"id": "UC1", "primary_actor": "Buyer"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [
                {"step_number": 1, "sentence": "Buyer views an item."},
                {"step_number": 2, "sentence": "System presents details."},
                {"step_number": 3, "sentence": "Buyer selects the item."},
                {"step_number": 4, "sentence": "System confirms selection."},
            ],
        }],
    }
    model = BCEModel.model_validate({
        "Classes": [
            {
                "className": "CatalogBoundary", "stereotype": "Boundary",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ignored", "name": "select", "parameters": [],
                    "returnType": "void",
                    "stepRefs": ["UC1:main:1", "UC1:main:3"],
                }],
            },
            {
                "className": "CatalogControl", "stereotype": "Control",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ignored", "name": "select", "parameters": [],
                    "returnType": "void",
                    "stepRefs": ["UC1:main:2", "UC1:main:4"],
                }],
            },
        ],
    }).model_dump(by_alias=True)

    assert any(
        "merges actor entries separated by completed system behavior" in issue
        for issue in extractor._scenario_signature_issues(model, scenario)
    )

    scenario["use_case_specs"][0]["main_scenario"] = [
        {"step_number": 1, "sentence": "Buyer enters a quantity."},
        {"step_number": 3, "sentence": "Buyer selects the item."},
        {"step_number": 4, "sentence": "System confirms selection."},
    ]
    assert not any(
        "merges actor entries" in issue
        for issue in extractor._scenario_signature_issues(model, scenario)
    )


def test_scenario_signature_contract_rejects_unsourceable_entity_inputs():
    scenario = {
        "use_cases": [{"id": "UC1", "primary_actor": "Buyer"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [
                {"step_number": 1, "sentence": "Buyer selects an offering."},
                {"step_number": 2, "sentence": "System records the registration."},
            ],
        }],
    }
    model = BCEModel.model_validate({
        "Classes": [
            {
                "className": "RegistrationBoundary", "stereotype": "Boundary",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ignored", "name": "register",
                    "parameters": [{"name": "offeringId", "type": "string"}],
                    "returnType": "void", "stepRefs": ["UC1:main:1"],
                }],
            },
            {
                "className": "RegistrationControl", "stereotype": "Control",
                "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ignored", "name": "register",
                    "parameters": [{"name": "offeringId", "type": "string"}],
                    "returnType": "void", "stepRefs": ["UC1:main:2"],
                }],
            },
            {
                "className": "Registration", "stereotype": "Entity",
                "fields": ["registrationId : string", "offeringId : string"],
                "identifier": ["registrationId"], "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ignored", "name": "create",
                    "parameters": [
                        {"name": "registrationId", "type": "string"},
                        {"name": "offeringId", "type": "string"},
                    ],
                    "returnType": "void", "stepRefs": ["UC1:main:2"],
                }],
            },
        ],
    }).model_dump(by_alias=True)

    assert any(
        "cannot form a sourceable BCE call tree" in issue
        for issue in extractor._scenario_signature_issues(model, scenario)
    )

    model["Classes"][2]["operations"][0]["parameters"] = [
        {"name": "offeringId", "type": "string"},
    ]
    model = BCEModel.model_validate(model).model_dump(by_alias=True)
    assert extractor._scenario_signature_issues(model, scenario) == []
