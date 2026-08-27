"""Focused contracts for collaboration-based class behavior."""
from __future__ import annotations

from copy import deepcopy

import pytest

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


def test_persistent_state_steps_require_an_entity_delegation():
    scenario = _actor_scenario()
    model = _actor_model()
    model["Collaborations"] = []
    model["Classes"].append({
        "className": "Order",
        "stereotype": "Entity",
        "fields": ["orderId : UUID"],
        "use_case_ids": ["UC1"],
        "operations": [{
            "operationId": "ignored",
            "name": "place",
            "parameters": [{"name": "request", "type": "OrderRequest"}],
            "returnType": "void",
            "stepRefs": ["UC1:main:2"],
        }],
    })
    model = BCEModel.model_validate(model).model_dump(by_alias=True)
    group = behavior.execution_groups(scenario)[0]
    proposal = behavior.CollaborationProposal.model_validate({"calls": [
        {
            "receiverOperationId": "OrderForm::submit(request:OrderRequest)",
            "stepRefs": ["UC1:main:1"],
        },
        {
            "receiverOperationId": "OrderControl::place(request:OrderRequest)",
            "stepRefs": ["UC1:main:2"],
        },
    ]})

    with pytest.raises(ValueError, match="persistent-state steps"):
        behavior._materialize_calls(proposal, model, group, scenario)

    proposal.calls.append(behavior.ProposedCall.model_validate({
        "receiverOperationId": "Order::place(request:OrderRequest)",
        "parentCallIndex": 2,
        "stepRefs": ["UC1:main:2"],
    }))
    calls = behavior._materialize_calls(
        proposal, model, group, scenario,
    )
    assert calls[-1]["receiverOperationId"] == "Order::place(request:OrderRequest)"


def test_collaboration_proposal_rejects_blank_step_refs_at_schema_boundary():
    with pytest.raises(ValueError, match="stepRefs cannot contain blank values"):
        behavior.CollaborationProposal.model_validate({"calls": [{
            "receiverOperationId": "OrderForm::submit(request:OrderRequest)",
            "stepRefs": ["  "],
        }]})


def test_group_repair_receives_the_valid_proposal_that_failed_materialization(monkeypatch):
    scenario = _actor_scenario()
    group = behavior.execution_groups(scenario)[0]
    initial = {"calls": [
        {"receiverOperationId": "OrderForm::submit(request:OrderRequest)", "stepRefs": ["UC1:main:1"]},
        {"receiverOperationId": "OrderControl::place(request:OrderRequest)", "stepRefs": ["UC1:main:2"]},
        {"receiverOperationId": "OrderControl::place(request:OrderRequest)", "stepRefs": ["UC1:main:2"]},
    ]}
    repaired = {"calls": initial["calls"][:2]}
    payloads = []

    def structured(messages, schema, **_kwargs):
        assert schema is behavior.CollaborationProposal
        payloads.append(__import__("json").loads(messages[1]["content"]))
        return initial if len(payloads) == 1 else repaired

    monkeypatch.setattr(behavior, "parse_structured", structured)

    collaboration, outcome = behavior._process_group(_actor_model(), scenario, group)

    assert collaboration is not None
    assert outcome.repaired is True
    assert payloads[1]["previousProposal"] == behavior.CollaborationProposal.model_validate(
        initial
    ).model_dump(by_alias=True)
    assert payloads[1]["repairFindings"]


def test_bounded_proposal_failures_do_not_guess_an_ambiguous_tree(monkeypatch):
    scenario = {
        "use_cases": [{"id": "UC1", "primary_actor": "Buyer"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "primary_actor": "Buyer",
            "main_scenario": [
                {"step_number": 1, "sentence": "Buyer selects an offering."},
                {"step_number": 2, "sentence": "Buyer submits registration."},
                {"step_number": 3, "sentence": "System confirms registration."},
            ],
        }],
    }
    model = BCEModel.model_validate({
        "Classes": [
            {
                "className": "RegistrationBoundary",
                "stereotype": "Boundary",
                "use_case_ids": ["UC1"],
                "operations": [
                    {
                            "operationId": "ignored",
                            "name": "select",
                            "parameters": [
                                {"name": "offeringId", "type": "uuid"},
                                {"name": "buyerId", "type": "uuid"},
                            ],
                        "returnType": "void",
                        "stepRefs": ["UC1:main:2"],
                    },
                    {
                        "operationId": "ignored",
                        "name": "register",
                        "parameters": [
                            {"name": "offeringId", "type": "uuid"},
                            {"name": "buyerId", "type": "uuid"},
                        ],
                        "returnType": "RegistrationResult",
                        "stepRefs": ["UC1:main:3"],
                    },
                ],
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
                    "returnType": "RegistrationResult",
                    "stepRefs": ["UC1:main:3"],
                }],
            },
        ],
        "DataTypes": [{
            "name": "RegistrationResult",
            "kind": "valueObject",
            "fields": ["accepted : boolean"],
        }],
    }).model_dump(by_alias=True)
    group = behavior.execution_groups(scenario)[1]
    monkeypatch.setattr(
        behavior,
        "_propose_group",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("model failed")),
    )

    collaboration, outcome = behavior._process_group(model, scenario, group)

    assert outcome.status == "failed"
    assert collaboration is None
    assert "unique" in outcome.issues[-1]


def test_composite_parameter_needs_an_ancestor_object_or_earlier_result():
    scenario = _actor_scenario()
    group = behavior.execution_groups(scenario)[0]
    root_id = "InputBoundary::submit(periodId:String,recordId:String)"
    child_id = "ProcessControl::apply(period:Period)"
    calls = [
        {"callId": "UC1:main:1::call:1", "parentCallId": None, "receiverOperationId": root_id},
        {"callId": "UC1:main:1::call:2", "parentCallId": "UC1:main:1::call:1", "receiverOperationId": child_id},
    ]
    operations = {
        root_id: {
            "parameters": [
                {"name": "periodId", "type": "String"},
                {"name": "recordId", "type": "String"},
            ],
            "returnType": "void",
        },
        child_id: {"parameters": [{"name": "period", "type": "Period"}], "returnType": "void"},
    }

    assert behavior._binding_candidates(
        calls, 1, {"name": "period", "type": "Period"}, operations, group, scenario
    ) == []

    operations[root_id]["returnType"] = "Period"
    assert behavior._binding_candidates(
        calls, 1, {"name": "period", "type": "Period"}, operations, group, scenario
    ) == ["UC1:main:1::call:1#result"]


def test_declared_structured_field_is_a_finite_ancestor_value_source():
    scenario = _actor_scenario()
    group = behavior.execution_groups(scenario)[0]
    root_id = "InputBoundary::submit(details:OrderDetails)"
    child_id = "ProcessControl::apply(orderId:string)"
    calls = [
        {"callId": "UC1:main:1::call:1", "parentCallId": None, "receiverOperationId": root_id},
        {"callId": "UC1:main:1::call:2", "parentCallId": "UC1:main:1::call:1", "receiverOperationId": child_id},
    ]
    operations = {
        root_id: {
            "parameters": [{"name": "details", "type": "OrderDetails"}],
            "returnType": "void",
        },
        child_id: {
            "parameters": [{"name": "orderId", "type": "string"}],
            "returnType": "void",
        },
    }
    model = {
        "DataTypes": [{
            "name": "OrderDetails", "kind": "valueObject",
            "fields": ["orderId : String"],
        }],
    }

    assert behavior._binding_candidates(
        calls, 1, {"name": "orderId", "type": "string"},
        operations, group, scenario, model,
    ) == ["UC1:main:1::call:1#details.orderId"]


def test_collaboration_validation_accepts_only_declared_structured_projection():
    scenario = _actor_scenario()
    model = _actor_model()
    control = model["Classes"][1]["operations"][0]
    control["parameters"] = [{"name": "sku", "type": "String"}]
    model = BCEModel.model_validate(model).model_dump(by_alias=True)
    calls = model["Collaborations"][0]["calls"]
    calls[1]["receiverOperationId"] = "OrderControl::place(sku:String)"
    calls[1]["argumentBindings"] = [{
        "parameter": "sku",
        "sourceRef": "UC1:main:1::call:1#request.sku",
    }]
    model["Relationships"] = project_call_dependencies(model)

    assert operation_contract_issues(model, {"usecase_spec": scenario}) == []

    calls[1]["argumentBindings"][0]["sourceRef"] = (
        "UC1:main:1::call:1#request.missing"
    )
    assert any(
        kind == "producer" and location.endswith("#sku")
        for kind, _message, location in operation_contract_issues(
            model, {"usecase_spec": scenario},
        )
    )


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


def test_consecutive_actor_inputs_start_distinct_execution_groups():
    scenario = {
        "use_cases": [{"id": "UC1", "primary_actor": "Buyer"}],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "primary_actor": "Buyer",
            "main_scenario": [
                {"step_number": 1, "sentence": "Buyer inspects an item."},
                {"step_number": 2, "sentence": "Buyer selects the item."},
                {"step_number": 3, "sentence": "System confirms the selection."},
            ],
        }],
    }

    groups = behavior.execution_groups(scenario)

    assert [(group.id, group.step_ids) for group in groups] == [
        ("UC1:main:1", ("UC1:main:1",)),
        ("UC1:main:2", ("UC1:main:2", "UC1:main:3")),
    ]


def test_include_scope_is_limited_to_its_declared_base_step_group():
    scenario = {
        "use_cases": [
            {"id": "UC1", "primary_actor": "Buyer"},
            {"id": "UC-S"},
        ],
        "use_case_specs": [
            {
                "use_case_id": "UC1",
                "primary_actor": "Buyer",
                "main_scenario": [
                    {"step_number": 1, "sentence": "Buyer inspects an item."},
                    {"step_number": 2, "sentence": "System presents details."},
                    {"step_number": 3, "sentence": "Buyer selects the item."},
                    {"step_number": 4, "sentence": "System confirms the selection."},
                ],
            },
            {
                "use_case_id": "UC-S",
                "main_scenario": [
                    {"step_number": 1, "sentence": "System obtains shared details."},
                ],
            },
        ],
        "relationships": {"includes": [{
            "base_use_case_id": "UC1",
            "included_use_case_id": "UC-S",
            "step_refs": [{"use_case_id": "UC1", "step_ref": "main:1"}],
        }]},
    }
    first, second = behavior.execution_groups(scenario)

    assert behavior._trace_scope_ids(first, scenario) == ["UC1", "UC-S"]
    assert "UC-S:main:1" in behavior._required_trace_steps(first, scenario)
    assert behavior._trace_scope_ids(second, scenario) == ["UC1"]
    assert "UC-S:main:1" not in behavior._required_trace_steps(second, scenario)


def test_included_use_case_with_its_own_actor_keeps_a_standalone_group():
    scenario = {
        "use_cases": [
            {"id": "UC1", "primary_actor": "Buyer"},
            {"id": "UC-S", "primary_actor": "Buyer"},
        ],
        "use_case_specs": [
            {
                "use_case_id": "UC1",
                "main_scenario": [
                    {"step_number": 1, "sentence": "Buyer submits an order."},
                    {"step_number": 2, "sentence": "System confirms it."},
                ],
            },
            {
                "use_case_id": "UC-S",
                "main_scenario": [
                    {"step_number": 1, "sentence": "Buyer signs in."},
                    {"step_number": 2, "sentence": "System confirms access."},
                ],
            },
        ],
        "relationships": {"includes": [{
            "base_use_case_id": "UC1",
            "included_use_case_id": "UC-S",
            "step_refs": [{"use_case_id": "UC1", "step_ref": "main:1"}],
        }]},
    }

    groups = behavior.execution_groups(scenario)
    base = next(group for group in groups if group.use_case_id == "UC1")

    assert any(group.use_case_id == "UC-S" for group in groups)
    assert "UC-S:main:1" in behavior._required_trace_steps(base, scenario)


def test_extend_keeps_independent_actor_collaborations_out_of_base_call_scope():
    scenario = {
        "use_cases": [
            {"id": "UC1", "primary_actor": "Buyer"},
            {"id": "UC2", "primary_actor": "Buyer"},
        ],
        "use_case_specs": [
            {
                "use_case_id": "UC1",
                "main_scenario": [
                    {"step_number": 1, "sentence": "Buyer views an order."},
                    {"step_number": 2, "sentence": "System presents it."},
                ],
            },
            {
                "use_case_id": "UC2",
                "main_scenario": [
                    {"step_number": 1, "sentence": "Buyer exports the order."},
                    {"step_number": 2, "sentence": "System creates a file."},
                ],
            },
        ],
        "relationships": {"extends": [{
            "base_use_case_id": "UC1",
            "extending_use_case_id": "UC2",
            "extension_point": "main:2",
        }]},
    }
    groups = behavior.execution_groups(scenario)
    base = next(group for group in groups if group.use_case_id == "UC1")
    extending = next(group for group in groups if group.use_case_id == "UC2")

    assert behavior._trace_scope_ids(base, scenario) == ["UC1"]
    assert behavior._available_trace_steps(base, scenario) == {
        "UC1:main:1", "UC1:main:2",
    }
    assert behavior._trace_scope_ids(extending, scenario) == ["UC2"]


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
    assert behavior.group_outcomes(enriched)[0].status == "accepted"


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
    model["Classes"][0]["description"] = "Verbose responsibility prose"
    model["DataTypes"].append({"name": "OrderStatus", "kind": "enumeration", "fields": [], "values": ["PENDING", "PLACED"]})
    model["Classes"][1]["fields"] = ["status : OrderStatus"]

    puml = generate_plantuml_from_bce_json(model)

    assert 'package "Data Types" {' in puml
    assert "class OrderRequest <<ValueObject>>" in puml
    assert "enum OrderStatus" in puml
    assert "UC1:main:1::call:1" not in puml
    assert "Verbose responsibility prose" not in puml
    assert "OrderForm ..> OrderControl" in puml
