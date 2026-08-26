"""Focused contracts for collaboration-based class behavior."""
from __future__ import annotations

from copy import deepcopy

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram import behavior
from app.design.services.class_diagram.behavior import project_call_dependencies
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.validation import operation_contract_issues


def _actor_scenario() -> dict:
    return {
        "use_cases": [{"id": "UC1", "primary_actor": "Buyer"}],
        "use_case_specs": [{
            "use_case_id": "UC1", "primary_actor": "Buyer",
            "main_scenario": [
                {"step_number": 1, "sentence": "Buyer submits an order."},
                {"step_number": 2, "sentence": "System places the order."},
            ],
        }],
    }


def _actor_model() -> dict:
    model = BCEModel.model_validate({
        "Classes": [
            {
                "className": "OrderForm", "stereotype": "Boundary", "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ignored", "name": "submit",
                    "parameters": [{"name": "request", "type": "OrderRequest"}],
                    "returnType": "void", "stepRefs": ["UC1:main:1"],
                }],
            },
            {
                "className": "OrderControl", "stereotype": "Control", "use_case_ids": ["UC1"],
                "operations": [{
                    "operationId": "ignored", "name": "place",
                    "parameters": [{"name": "request", "type": "OrderRequest"}],
                    "returnType": "void", "stepRefs": ["UC1:main:2"],
                }],
            },
        ],
        "DataTypes": [{"name": "OrderRequest", "kind": "valueObject", "fields": ["sku : String"]}],
        "Collaborations": [{
            "collaborationId": "UC1:main:1", "useCaseIds": ["UC1"], "entryActor": "Buyer",
            "calls": [
                {
                    "callId": "ignored", "receiverOperationId": "OrderForm::submit(request:OrderRequest)",
                    "stepRefs": ["UC1:main:1"],
                    "argumentBindings": [{"parameter": "request", "sourceRef": "UC1:main:1#request"}],
                },
                {
                    "callId": "ignored", "parentCallId": "UC1:main:1::call:1",
                    "receiverOperationId": "OrderControl::place(request:OrderRequest)",
                    "stepRefs": ["UC1:main:2"],
                    "argumentBindings": [{"parameter": "request", "sourceRef": "UC1:main:1::call:1#request"}],
                },
            ],
        }],
    }).model_dump(by_alias=True)
    model["Relationships"] = project_call_dependencies(model)
    return model


def test_actor_input_lives_on_the_entry_call_and_parent_parameter_is_per_call():
    model = _actor_model()
    calls = model["Collaborations"][0]["calls"]

    assert calls[0]["argumentBindings"] == [{"parameter": "request", "sourceRef": "UC1:main:1#request"}]
    assert calls[1]["argumentBindings"] == [{
        "parameter": "request", "sourceRef": "UC1:main:1::call:1#request",
    }]
    assert operation_contract_issues(model, {"usecase_spec": _actor_scenario()}) == []


def test_unique_untyped_operation_reference_resolves_within_finite_candidates():
    model = _actor_model()
    model["Collaborations"] = []
    group = behavior.execution_groups(_actor_scenario())[0]
    proposal = behavior.CollaborationProposal.model_validate({"calls": [
        {
            "receiverOperationId": "OrderForm::submit",
            "stepRefs": ["UC1:main:1"],
        },
        {
            "receiverOperationId": "OrderControl::place",
            "stepRefs": ["UC1:main:2"],
        },
    ]})

    calls = behavior._materialize_calls(proposal, model, group, _actor_scenario())

    assert [call["receiverOperationId"] for call in calls] == [
        "OrderForm::submit(request:OrderRequest)",
        "OrderControl::place(request:OrderRequest)",
    ]


def test_execution_group_payload_does_not_include_a_sibling_actor_interaction():
    scenario = {
        "use_cases": [{"id": "UC1", "primary_actor": "Buyer"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "primary_actor": "Buyer",
            "main_scenario": [
                {"step_number": 1, "sentence": "Buyer opens the catalog."},
                {"step_number": 2, "sentence": "System presents the catalog."},
                {"step_number": 3, "sentence": "Buyer selects an item."},
                {"step_number": 4, "sentence": "System presents its details."},
            ],
            "extensions": [
                {
                    "label": "2a",
                    "branch_step": 2,
                    "handling_steps": [{"sub_step": "2a1", "sentence": "System reports no catalog."}],
                },
                {
                    "label": "4a",
                    "branch_step": 4,
                    "handling_steps": [{"sub_step": "4a1", "sentence": "System reports no details."}],
                },
            ],
        }],
    }
    groups = behavior.execution_groups(scenario)

    first = behavior._group_payload(_actor_model(), groups[0], scenario)
    second = behavior._group_payload(_actor_model(), groups[1], scenario)

    assert [step["id"] for step in first["steps"]] == [
        "UC1:main:1", "UC1:main:2", "UC1:extension:2a:2a1",
    ]
    assert [step["id"] for step in second["steps"]] == [
        "UC1:main:3", "UC1:main:4", "UC1:extension:4a:4a1",
    ]


def test_enrichment_proposes_calls_then_selects_only_finite_sources(monkeypatch):
    skeleton = _actor_model()
    skeleton["Collaborations"] = []
    skeleton["Relationships"] = []

    def structured(messages, schema, **_kwargs):
        if schema is behavior.CollaborationProposal:
            return {"calls": [
                {"receiverOperationId": "OrderForm::submit(request:OrderRequest)", "stepRefs": ["UC1:main:1"]},
                {"receiverOperationId": "OrderControl::place(request:OrderRequest)", "stepRefs": ["UC1:main:2"]},
            ]}
        if schema is behavior.SourceChoice:
            payload = __import__("json").loads(messages[1]["content"])
            return {"sourceRef": payload["candidateSources"][0]["sourceRef"]}
        if schema is behavior.ClassSemanticReview:
            return {"issues": []}
        raise AssertionError(schema)

    monkeypatch.setattr(behavior, "parse_structured", structured)
    monkeypatch.setattr(behavior.settings, "design_class_behavior_parallelism", 1)

    enriched = behavior.enrich_bce_behavior(_actor_scenario(), skeleton)

    assert enriched["Collaborations"][0]["calls"][1]["argumentBindings"] == [{
        "parameter": "request", "sourceRef": "UC1:main:1::call:1#request",
    }]
    assert enriched["Relationships"] == [{
        "source": "OrderForm", "target": "OrderControl", "type": "Dependency",
        "sourceMultiplicity": "", "targetMultiplicity": "", "description": "",
    }]


def test_precondition_context_uses_stable_precondition_identity():
    scenario = {
        "use_cases": [{"id": "UC2"}],
        "use_case_specs": [{
            "use_case_id": "UC2", "preconditions": ["The member already has a session."],
            "main_scenario": [{"step_number": 1, "sentence": "System resumes the session."}],
        }],
    }
    model = BCEModel.model_validate({
        "Classes": [{
            "className": "SessionControl", "stereotype": "Control", "use_case_ids": ["UC2"],
            "operations": [{
                "operationId": "ignored", "name": "resume", "parameters": [{"name": "session", "type": "String"}],
                "returnType": "void", "stepRefs": ["UC2:main:1"],
            }],
        }],
        "Collaborations": [{
            "collaborationId": "UC2:root", "useCaseIds": ["UC2"], "calls": [{
                "callId": "ignored", "receiverOperationId": "SessionControl::resume(session:String)",
                "stepRefs": ["UC2:main:1"],
                "argumentBindings": [{"parameter": "session", "sourceRef": "UC2:precondition:1"}],
            }],
        }],
    }).model_dump(by_alias=True)

    assert operation_contract_issues(model, {"usecase_spec": scenario}) == []


def test_shared_include_reuses_one_operation_with_distinct_callsite_values():
    scenario = {
        "use_cases": [
            {"id": "UC-A", "primary_actor": "Buyer"},
            {"id": "UC-B", "primary_actor": "Buyer"},
            {"id": "UC-S"},
        ],
        "use_case_specs": [
            {
                "use_case_id": use_case_id, "primary_actor": "Buyer",
                "main_scenario": [
                    {"step_number": 1, "sentence": "Buyer submits a request."},
                    {"step_number": 2, "sentence": "System handles the request."},
                ],
            }
            for use_case_id in ("UC-A", "UC-B")
        ] + [{
            "use_case_id": "UC-S",
            "main_scenario": [{"step_number": 1, "sentence": "System validates the request."}],
        }],
        "relationships": {
            "includes": [
                {"base_use_case_id": "UC-A", "included_use_case_id": "UC-S"},
                {"base_use_case_id": "UC-B", "included_use_case_id": "UC-S"},
            ],
        },
    }
    classes = []
    collaborations = []
    for use_case_id, prefix in (("UC-A", "First"), ("UC-B", "Second")):
        collaboration_id = f"{use_case_id}:main:1"
        classes.extend([
            {"className": f"{prefix}Form", "stereotype": "Boundary", "use_case_ids": [use_case_id], "operations": [{
                "operationId": "ignored", "name": "submit", "parameters": [{"name": "request", "type": "Request"}],
                "returnType": "void", "stepRefs": [f"{use_case_id}:main:1"],
            }]},
            {"className": f"{prefix}Control", "stereotype": "Control", "use_case_ids": [use_case_id], "operations": [{
                "operationId": "ignored", "name": "handle", "parameters": [{"name": "request", "type": "Request"}],
                "returnType": "void", "stepRefs": [f"{use_case_id}:main:2"],
            }]},
        ])
        collaborations.append({
            "collaborationId": collaboration_id, "useCaseIds": [use_case_id, "UC-S"], "entryActor": "Buyer",
            "calls": [
                {"callId": "ignored", "receiverOperationId": f"{prefix}Form::submit(request:Request)", "stepRefs": [f"{use_case_id}:main:1"], "argumentBindings": [{"parameter": "request", "sourceRef": f"{use_case_id}:main:1#request"}]},
                {"callId": "ignored", "parentCallId": f"{collaboration_id}::call:1", "receiverOperationId": f"{prefix}Control::handle(request:Request)", "stepRefs": [f"{use_case_id}:main:2"], "argumentBindings": [{"parameter": "request", "sourceRef": f"{collaboration_id}::call:1#request"}]},
                {"callId": "ignored", "parentCallId": f"{collaboration_id}::call:2", "receiverOperationId": "SharedControl::validate(request:Request)", "stepRefs": ["UC-S:main:1"], "argumentBindings": [{"parameter": "request", "sourceRef": f"{collaboration_id}::call:2#request"}]},
            ],
        })
    classes.append({"className": "SharedControl", "stereotype": "Control", "use_case_ids": ["UC-S"], "operations": [{
        "operationId": "ignored", "name": "validate", "parameters": [{"name": "request", "type": "Request"}],
        "returnType": "void", "stepRefs": ["UC-S:main:1"],
    }]})
    model = BCEModel.model_validate({
        "Classes": classes, "DataTypes": [{"name": "Request", "kind": "valueObject", "fields": ["value : String"]}],
        "Collaborations": collaborations,
    }).model_dump(by_alias=True)
    model["Relationships"] = project_call_dependencies(model)

    shared_bindings = [collaboration["calls"][2]["argumentBindings"][0]["sourceRef"] for collaboration in model["Collaborations"]]
    assert shared_bindings == ["UC-A:main:1::call:2#request", "UC-B:main:1::call:2#request"]
    assert operation_contract_issues(model, {"usecase_spec": scenario}) == []


def test_invalid_future_cycle_and_unresolved_type_are_reported():
    model = _actor_model()
    broken = deepcopy(model)
    calls = broken["Collaborations"][0]["calls"]
    calls[0]["parentCallId"] = "UC1:main:1::call:2"
    calls[0]["argumentBindings"][0]["sourceRef"] = "UC1:main:1::call:2#result"
    broken["Classes"][1]["operations"][0]["returnType"] = "UnknownClassResult"

    messages = [message for _kind, message, _location in operation_contract_issues(
        broken, {"usecase_spec": _actor_scenario()}
    )]
    assert any("first call cannot" in message for message in messages)
    assert any("earlier call result" in message for message in messages)
    assert any("does not resolve" in message for message in messages)


def test_renderer_draws_data_types_but_not_collaboration_call_artifacts():
    model = _actor_model()
    model["DataTypes"].append({"name": "OrderStatus", "kind": "enumeration", "fields": [], "values": ["PENDING", "PLACED"]})
    model["Classes"][1]["fields"] = ["status : OrderStatus"]

    puml = generate_plantuml_from_bce_json(model)

    assert 'package "Data Types" {' in puml
    assert "class OrderRequest <<ValueObject>>" in puml
    assert "enum OrderStatus" in puml
    assert "UC1:main:1::call:1" not in puml
    assert "OrderForm ..> OrderControl" in puml
