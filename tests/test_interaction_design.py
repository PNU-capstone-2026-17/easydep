from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.validation import run_checks
from app.design.services.class_diagram.plantuml import (
    generate_plantuml_from_bce_json,
)
from app.design.services.interaction_design import pipeline
from app.design.services.interaction_design.checks import (
    OPERATION_CHECKS,
    OperationContext,
    final_model_findings,
)
from app.design.services.interaction_design.contracts import (
    CallPlanProposal,
    InventoryProposal,
    OperationFragment,
)
from app.design.services.interaction_design.scenario import build_scenario_index
from app.design.services.interaction_design.sequence import (
    project_sequence_model,
    sequence_findings,
)
from app.design.services.sequence_diagram.methods import is_return_value_label


def _scenario() -> dict:
    return {
        "use_cases": [
            {"id": "UC1", "name": "Manage order", "primary_actor": "Customer"},
            {"id": "UC2", "name": "Validate order", "primary_actor": ""},
        ],
        "use_case_specs": [
            {
                "use_case_id": "UC1",
                "main_scenario": [
                    {"step_number": 1, "subject_ref": "Customer", "sentence": "Customer submits an order."},
                    {"step_number": 2, "subject_ref": "System", "sentence": "System validates the order."},
                    {"step_number": 3, "subject_ref": "System", "sentence": "System returns the result."},
                    {"step_number": 4, "subject_ref": "Customer", "sentence": "Customer requests the receipt."},
                    {"step_number": 5, "subject_ref": "System", "sentence": "System returns the receipt."},
                ],
                "extensions": [{
                    "label": "2a",
                    "branch_step": 2,
                    "condition": "The order is invalid",
                    "handling_steps": [{
                        "sub_step": "2a1",
                        "subject_ref": "System",
                        "sentence": "System returns the rejection.",
                    }],
                }],
            },
            {
                "use_case_id": "UC2",
                "main_scenario": [{
                    "step_number": 1,
                    "subject_ref": "System",
                    "sentence": "System checks order constraints.",
                }],
                "extensions": [],
            },
        ],
        "relationships": {
            "includes": [{
                "base_use_case_id": "UC1",
                "included_use_case_id": "UC2",
                "step_refs": [{"use_case_id": "UC1", "step_ref": "main:2"}],
            }],
            "extends": [],
        },
    }


def test_scenario_index_splits_actor_entries_and_attaches_extension_steps():
    index = build_scenario_index(_scenario())

    assert [group.id for group in index.groups] == ["UC1:main:1", "UC1:main:4"]
    first = index.groups[0]
    assert "UC1:extension:2a:2a1" in first.step_ids
    assert "UC2:main:1" in first.required_step_ids
    assert first.trace_use_case_ids == ("UC1", "UC2")


def test_internal_include_has_no_synthetic_standalone_group():
    index = build_scenario_index(_scenario())

    assert all(group.use_case_id != "UC2" for group in index.groups)


def test_inventory_payload_keeps_execution_evidence_without_full_spec_documents():
    payload = pipeline._inventory_payload(build_scenario_index(_scenario()))

    assert set(payload) == {"useCases", "relationships"}
    assert [item["id"] for item in payload["useCases"]] == ["UC1", "UC2"]
    assert payload["useCases"][0]["steps"][0]["stepRef"] == "UC1:main:1"
    assert payload["relationships"] == [{
        "kind": "include",
        "baseUseCaseId": "UC1",
        "relatedUseCaseId": "UC2",
        "anchorStepRefs": ["UC1:main:2"],
    }]


def test_inventory_contract_does_not_silently_default_structural_decisions():
    with pytest.raises(ValidationError):
        InventoryProposal.model_validate({
            "items": [{"name": "Order", "kind": "Entity"}],
            "Relationships": [],
        })


def test_operation_generation_advances_in_two_item_waves_with_reserved_catalog(monkeypatch):
    index = build_scenario_index(_scenario())
    inventory = pipeline._normalize_inventory(InventoryProposal.model_validate({
        "items": [{
            "name": "RequestBoundary",
            "kind": "Boundary",
            "description": "Request interface",
            "fields": [],
            "identifier": [],
            "values": [],
            "useCaseIds": ["UC1", "UC2"],
        }],
        "Relationships": [],
    }))
    observed = {}

    def fragment(_index, _inventory, use_case, **kwargs):
        group_id = kwargs.get("execution_group_id") or use_case.id
        observed[group_id] = len(kwargs.get("reserved") or [])
        return {
            "DataTypes": [],
            "Classes": [{
                "className": "RequestBoundary",
                "operations": [{
                    "name": "handle" + group_id.replace(":", "").upper(),
                    "parameters": [],
                    "returnType": "void",
                    "stepRefs": list(kwargs.get("allowed_step_ids") or []),
                }],
            }],
        }

    monkeypatch.setattr(pipeline, "_checked_fragment", fragment)
    monkeypatch.setattr(pipeline, "_preview", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline.settings, "design_class_behavior_parallelism", 2)

    pipeline._build_fragments(index, inventory)

    assert observed["UC1:main:1"] == 0
    assert observed["UC1:main:4"] == 0
    assert observed["UC2"] == 1


def _single_use_case() -> dict:
    return {
        "use_cases": [{
            "id": "UC1", "name": "Submit request", "primary_actor": "Member",
        }],
        "use_case_specs": [{
            "use_case_id": "UC1",
            "main_scenario": [
                {"step_number": 1, "subject_ref": "Member", "sentence": "Member submits a request."},
                {"step_number": 2, "subject_ref": "System", "sentence": "System returns the result."},
            ],
            "extensions": [],
        }],
        "relationships": {"includes": [], "extends": []},
    }


def _inventory_proposal() -> dict:
    return {
        "items": [
            {"name": "RequestBoundary", "kind": "Boundary", "description": "Member interface", "fields": [], "identifier": [], "values": [], "useCaseIds": ["UC1"]},
            {"name": "RequestControl", "kind": "Control", "description": "Request coordination", "fields": [], "identifier": [], "values": [], "useCaseIds": ["UC1"]},
        ],
        "Relationships": [],
    }


def _operation_fragment(*, unsourceable: bool = False) -> dict:
    control_parameter = (
        {"name": "other", "type": "Boolean"}
        if unsourceable
        else {"name": "request", "type": "RequestData"}
    )
    return {
        "DataTypes": [
            {
                "name": "RequestData",
                "kind": "valueObject",
                "fields": [{"name": "value", "type": "String"}],
                "values": [],
            },
            {
                "name": "RequestResult",
                "kind": "valueObject",
                "fields": [{"name": "accepted", "type": "Boolean"}],
                "values": [],
            },
        ],
        "Classes": [
            {"className": "RequestBoundary", "operations": [{
                "name": "submit",
                "parameters": [{"name": "request", "type": "RequestData"}],
                "returnType": "RequestResult",
                "stepRefs": ["UC1:main:1", "UC1:main:2"],
            }]},
            {"className": "RequestControl", "operations": [{
                "name": "process",
                "parameters": [control_parameter],
                "returnType": "RequestResult",
                "stepRefs": ["UC1:main:2"],
            }]},
        ],
    }


def _call_plan() -> dict:
    return {
        "calls": [
            {
                "receiverOperationId": "RequestBoundary::submit(request:RequestData)",
                "parentCallIndex": None,
            },
            {
                "receiverOperationId": "RequestControl::process(request:RequestData)",
                "parentCallIndex": 1,
            },
        ],
    }


def test_vertical_pipeline_persists_calls_and_derives_parameter_provenance(monkeypatch):
    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return _inventory_proposal()
        if schema is OperationFragment:
            return _operation_fragment()
        if issubclass(schema, CallPlanProposal):
            call_schema = schema.model_json_schema()["$defs"]["FiniteProposedCall"]
            assert call_schema["properties"]["receiverOperationId"]["enum"] == [
                "RequestBoundary::submit(request:RequestData)",
                "RequestControl::process(request:RequestData)",
            ]
            return _call_plan()
        raise AssertionError(schema)

    monkeypatch.setattr(pipeline, "parse_structured", fake_parse)

    model = pipeline.generate_class_model(_single_use_case())

    assert len(model["Collaborations"]) == 1
    calls = model["Collaborations"][0]["calls"]
    assert calls[1]["argumentBindings"] == [{
        "parameter": "request",
        "sourceRef": "UC1:main:1::call:1#request",
    }]
    assert all(item.get("type") != "Dependency" for item in model["Relationships"])
    assert pipeline.project_call_dependencies(model) == [{
        "source": "RequestBoundary",
        "target": "RequestControl",
        "type": "Dependency",
    }]
    assert final_model_findings(model, build_scenario_index(_single_use_case())) == []


def test_class_render_keeps_structure_and_projects_call_dependencies(monkeypatch):
    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return _inventory_proposal()
        if schema is OperationFragment:
            return _operation_fragment()
        if issubclass(schema, CallPlanProposal):
            return _call_plan()
        raise AssertionError(schema)

    monkeypatch.setattr(pipeline, "parse_structured", fake_parse)
    model = pipeline.generate_class_model(_single_use_case())
    model["Relationships"] = [{
        "source": "RequestControl",
        "target": "RequestBoundary",
        "type": "Association",
        "sourceMultiplicity": "1",
        "targetMultiplicity": "1",
        "description": "uses interface contract",
    }]
    puml = generate_plantuml_from_bce_json(model)

    assert 'RequestControl "1" --> "1" RequestBoundary' in puml
    assert "RequestBoundary ..> RequestControl" in puml


def test_operation_fragment_owns_signature_only_data_types(monkeypatch):
    inventory = _inventory_proposal()
    fragment = _operation_fragment()

    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory
        if schema is OperationFragment:
            return fragment
        if issubclass(schema, CallPlanProposal):
            return _call_plan()
        raise AssertionError(schema)

    monkeypatch.setattr(pipeline, "parse_structured", fake_parse)

    model = pipeline.generate_class_model(_single_use_case())

    assert [item["name"] for item in model["DataTypes"]] == [
        "RequestData", "RequestResult",
    ]
    assert final_model_findings(model, build_scenario_index(_single_use_case())) == []


def test_collision_repair_can_reuse_a_committed_local_data_type(monkeypatch):
    fragment = _operation_fragment()
    fragment["DataTypes"] = []

    def fake_parse(_messages, schema, **_kwargs):
        assert schema is OperationFragment
        return fragment

    monkeypatch.setattr(pipeline, "parse_structured", fake_parse)
    index = build_scenario_index(_single_use_case())
    inventory = pipeline._normalize_inventory(
        InventoryProposal.model_validate(_inventory_proposal())
    )

    accepted = pipeline._checked_fragment(
        index,
        inventory,
        index.use_case("UC1"),
        reserved_types=_operation_fragment()["DataTypes"],
        operation="InteractionOperationCollisionRepair",
    )

    assert accepted["DataTypes"] == []


def test_vertical_pipeline_does_not_fabricate_an_unsourceable_parameter(monkeypatch):
    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return _inventory_proposal()
        if schema is OperationFragment:
            return _operation_fragment(unsourceable=True)
        if issubclass(schema, CallPlanProposal):
            plan = _call_plan()
            plan["calls"][1]["receiverOperationId"] = (
                "RequestControl::process(other:Boolean)"
            )
            return plan
        raise AssertionError(schema)

    monkeypatch.setattr(pipeline, "parse_structured", fake_parse)

    model = pipeline.generate_class_model(_single_use_case())

    assert model["Collaborations"] == []
    assert any(
        finding.rule_id == "class.model.collaboration-coverage"
        for finding in final_model_findings(
            model, build_scenario_index(_single_use_case()),
        )
    )


def test_temporal_parameter_uses_explicit_runtime_clock_when_no_upstream_value(monkeypatch):
    inventory = _inventory_proposal()
    inventory["items"].append({
        "name": "Registration",
        "kind": "Entity",
        "description": "Accepted registration",
        "fields": [
            {"name": "registrationId", "type": "uuid"},
            {"name": "registeredAt", "type": "localdatetime"},
        ],
        "identifier": ["registrationId"],
        "values": [],
        "useCaseIds": ["UC1"],
    })
    fragment = _operation_fragment()
    fragment["Classes"].append({
        "className": "Registration",
        "operations": [{
            "name": "create",
            "parameters": [{"name": "registeredAt", "type": "localdatetime"}],
            "returnType": "Registration",
            "stepRefs": ["UC1:main:2"],
        }],
    })
    plan = _call_plan()
    plan["calls"].append({
        "receiverOperationId": "Registration::create(registeredAt:localdatetime)",
        "parentCallIndex": 2,
    })

    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory
        if schema is OperationFragment:
            return fragment
        if issubclass(schema, CallPlanProposal):
            return plan
        raise AssertionError(schema)

    monkeypatch.setattr(pipeline, "parse_structured", fake_parse)

    model = pipeline.generate_class_model(_single_use_case())
    runtime_call = model["Collaborations"][0]["calls"][2]
    assert runtime_call["argumentBindings"] == [{
        "parameter": "registeredAt",
        "sourceRef": "runtime#currentDateTime",
    }]
    sequence = project_sequence_model(_single_use_case(), model, "")
    runtime_argument = sequence["Diagrams"][0]["Messages"][2]["arguments"][0]
    assert runtime_argument["source_kind"] == "state"
    assert runtime_argument["source_ref"] == "runtime#currentDateTime"


def test_structured_parameter_is_derived_from_upstream_fields(monkeypatch):
    inventory = _inventory_proposal()
    inventory["items"].append({
        "name": "Registration",
        "kind": "Entity",
        "description": "Accepted registration",
        "fields": [{"name": "registrationId", "type": "uuid"}],
        "identifier": ["registrationId"],
        "values": [],
        "useCaseIds": ["UC1"],
    })
    fragment = _operation_fragment()
    fragment["DataTypes"].append({
        "name": "RegistrationDetails",
        "kind": "valueObject",
        "fields": [{"name": "value", "type": "String"}],
        "values": [],
    })
    fragment["Classes"].append({
        "className": "Registration",
        "operations": [{
            "name": "create",
            "parameters": [{"name": "details", "type": "RegistrationDetails"}],
            "returnType": "Registration",
            "stepRefs": ["UC1:main:2"],
        }],
    })
    plan = _call_plan()
    plan["calls"].append({
        "receiverOperationId": "Registration::create(details:RegistrationDetails)",
        "parentCallIndex": 2,
    })

    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return inventory
        if schema is OperationFragment:
            return fragment
        if issubclass(schema, CallPlanProposal):
            return plan
        raise AssertionError(schema)

    monkeypatch.setattr(pipeline, "parse_structured", fake_parse)

    model = pipeline.generate_class_model(_single_use_case())
    binding = model["Collaborations"][0]["calls"][2]["argumentBindings"][0]
    assert binding == {
        "parameter": "details",
        "sourceRef": (
            "derived#RegistrationDetails("
            "value=UC1:main:1::call:2#request.value)"
        ),
    }
    assert final_model_findings(
        model, build_scenario_index(_single_use_case()),
    ) == []


def test_earlier_optional_result_has_explicit_unwrap_source():
    model = {
        "Classes": [
            {
                "className": "Student",
                "stereotype": "Entity",
                "use_case_ids": ["UC1"],
                "fields": ["studentId : uuid"],
                "identifier": ["studentId"],
                "operations": [{
                    "operationId": "Student::find(id:uuid)",
                    "name": "find",
                    "parameters": [{"name": "id", "type": "uuid"}],
                    "returnType": "optional<Student>",
                    "stepRefs": ["UC1:main:2"],
                }],
            },
            {
                "className": "Registration",
                "stereotype": "Entity",
                "use_case_ids": ["UC1"],
                "fields": ["registrationId : uuid"],
                "identifier": ["registrationId"],
                "operations": [{
                    "operationId": "Registration::create(student:Student)",
                    "name": "create",
                    "parameters": [{"name": "student", "type": "Student"}],
                    "returnType": "Registration",
                    "stepRefs": ["UC1:main:2"],
                }],
            },
        ],
        "DataTypes": [],
        "Relationships": [],
        "Collaborations": [],
    }
    calls = [
        {
            "callId": "lookup",
            "parentCallId": None,
            "receiverOperationId": "Student::find(id:uuid)",
        },
        {
            "callId": "create",
            "parentCallId": None,
            "receiverOperationId": "Registration::create(student:Student)",
        },
    ]
    index = build_scenario_index(_single_use_case())

    candidates = pipeline._binding_candidates(
        model,
        index,
        index.groups[0],
        calls,
        1,
        {"name": "student", "type": "Student"},
        pipeline.operation_catalog(model),
    )

    assert candidates == ["lookup#result.unwrap"]


def test_ambiguous_binding_schema_is_limited_to_finite_candidates(monkeypatch):
    candidates = [
        "UC1:main:1::call:1#request.studentId",
        "UC1:main:1::call:2#result.studentId",
    ]

    def fake_parse(_messages, schema, **kwargs):
        properties = schema.model_json_schema()["properties"]
        assert properties["choice1"]["enum"] == candidates
        assert kwargs["max_completion_tokens"] == 2048
        return {"choice1": candidates[1]}

    monkeypatch.setattr(pipeline, "parse_structured", fake_parse)
    selected = pipeline._select_ambiguous_bindings(
        build_scenario_index(_single_use_case()).groups[0],
        {"UC1:main:1::call:3#studentId": candidates},
    )

    assert selected == {
        "UC1:main:1::call:3#studentId": candidates[1],
    }


def test_ungrounded_downstream_dto_reuses_one_unique_upstream_dto():
    inventory_proposal = _inventory_proposal()
    inventory_proposal["items"].append({
        "name": "Registration",
        "kind": "Entity",
        "description": "Accepted registration",
        "fields": [{"name": "registrationId", "type": "uuid"}],
        "identifier": ["registrationId"],
        "values": [],
        "useCaseIds": ["UC1"],
    })
    inventory = pipeline._normalize_inventory(
        InventoryProposal.model_validate(inventory_proposal)
    )
    fragment = _operation_fragment()
    for item in fragment["DataTypes"]:
        item["fields"] = [
            f"{field['name']} : {field['type']}" for field in item["fields"]
        ]
    fragment["DataTypes"].append({
        "name": "AddRegistrationDetails",
        "kind": "valueObject",
        "fields": [
            "value : String",
            "generatedId : uuid",
        ],
        "values": [],
    })
    fragment["Classes"].append({
        "className": "Registration",
        "operations": [{
            "name": "add",
            "parameters": [{
                "name": "details", "type": "AddRegistrationDetails",
            }],
            "returnType": "Registration",
            "stepRefs": ["UC1:main:2"],
        }],
    })

    normalized = pipeline._canonicalize_downstream_input_types(fragment, inventory)

    entity = normalized["Classes"][-1]
    assert entity["operations"][0]["parameters"][0]["type"] == "RequestData"
    assert {item["name"] for item in normalized["DataTypes"]} == {
        "RequestData", "RequestResult",
    }


def test_placeholder_operation_is_removed_before_contract_validation():
    inventory = pipeline._normalize_inventory(
        InventoryProposal.model_validate(_inventory_proposal())
    )
    fragment = _operation_fragment()
    fragment["Classes"].append({
        "className": "RequestControl",
        "operations": [{
            "name": "none",
            "parameters": [],
            "returnType": "void",
            "stepRefs": ["UC1:main:2"],
        }],
    })

    normalized = pipeline._canonicalize_downstream_input_types(fragment, inventory)

    assert all(
        operation["name"] != "none"
        for class_set in normalized["Classes"]
        for operation in class_set["operations"]
    )


def test_entity_receiver_cannot_accept_its_own_complete_entity_type():
    index = build_scenario_index(_single_use_case())
    inventory_proposal = _inventory_proposal()
    inventory_proposal["items"].append({
        "name": "Registration",
        "kind": "Entity",
        "description": "Accepted registration",
        "fields": [{"name": "registrationId", "type": "uuid"}],
        "identifier": ["registrationId"],
        "values": [],
        "useCaseIds": ["UC1"],
    })
    inventory = pipeline._normalize_inventory(
        InventoryProposal.model_validate(inventory_proposal)
    )
    fragment = _operation_fragment()
    fragment["Classes"].append({
        "className": "Registration",
        "operations": [{
            "name": "add",
            "parameters": [{"name": "entry", "type": "Registration"}],
            "returnType": "Registration",
            "stepRefs": ["UC1:main:2"],
        }],
    })

    report = run_checks(
        OPERATION_CHECKS,
        fragment,
        OperationContext(index, inventory, index.use_case("UC1")),
        parallel=True,
    )

    assert any(
        "must not accept its own complete Entity type" in finding.message
        for finding in report.findings
    )


def test_only_boundary_operation_may_trace_an_actor_entry_step():
    index = build_scenario_index(_single_use_case())
    inventory = pipeline._normalize_inventory(
        InventoryProposal.model_validate(_inventory_proposal())
    )
    fragment = _operation_fragment()
    fragment["Classes"][1]["operations"][0]["stepRefs"] = [
        "UC1:main:1", "UC1:main:2",
    ]

    report = run_checks(
        OPERATION_CHECKS,
        fragment,
        OperationContext(index, inventory, index.use_case("UC1")),
        parallel=True,
    )

    assert any(
        "only Boundary may own an actor entry step" in finding.message
        for finding in report.findings
    )


def test_actor_entry_refs_are_removed_from_delegated_operations():
    inventory = pipeline._normalize_inventory(
        InventoryProposal.model_validate(_inventory_proposal())
    )
    fragment = _operation_fragment()
    fragment["Classes"][1]["operations"][0]["stepRefs"] = [
        "UC1:main:1", "UC1:main:2",
    ]
    fragment["Classes"][1]["operations"].append({
        "name": "lookupSelection",
        "parameters": [],
        "returnType": "void",
        "stepRefs": ["UC1:main:1"],
    })

    normalized = pipeline._canonicalize_step_ownership(
        fragment, inventory, {"UC1:main:1"},
    )

    control_operations = normalized["Classes"][1]["operations"]
    assert [item["name"] for item in control_operations] == ["process"]
    assert control_operations[0]["stepRefs"] == ["UC1:main:2"]


def test_resume_only_plans_missing_collaborations(monkeypatch):
    calls: list[str] = []

    def fake_parse(_messages, schema, **kwargs):
        calls.append(kwargs.get("operation", schema.__name__))
        if schema is InventoryProposal:
            return _inventory_proposal()
        if schema is OperationFragment:
            return _operation_fragment()
        if issubclass(schema, CallPlanProposal):
            return _call_plan()
        raise AssertionError(schema)

    monkeypatch.setattr(pipeline, "parse_structured", fake_parse)
    current = pipeline.generate_class_model(_single_use_case())
    current["Collaborations"] = []
    calls.clear()

    resumed = pipeline.resume_class_model(_single_use_case(), current)

    assert len(resumed["Collaborations"]) == 1
    assert calls == ["InteractionCallPlan"]


def test_sequence_projection_adds_one_return_for_every_call_including_void(monkeypatch):
    def fake_parse(_messages, schema, **_kwargs):
        if schema is InventoryProposal:
            return _inventory_proposal()
        if schema is OperationFragment:
            return _operation_fragment()
        if issubclass(schema, CallPlanProposal):
            return _call_plan()
        raise AssertionError(schema)

    monkeypatch.setattr(pipeline, "parse_structured", fake_parse)
    class_model = pipeline.generate_class_model(_single_use_case())
    control = next(
        item for item in class_model["Classes"]
        if item["className"] == "RequestControl"
    )
    control["operations"][0]["returnType"] = "void"

    sequence = project_sequence_model(
        _single_use_case(), class_model, "@startuml\n@enduml",
    )

    messages = sequence["Diagrams"][0]["Messages"]
    calls = [message for message in messages if message["call_id"]]
    returns = [message for message in messages if message["type"] == "return"]
    assert len(calls) == len(returns) == 2
    assert {message["reply_to"] for message in returns} == {
        message["call_id"] for message in calls
    }
    assert any(message["label"] == "void" for message in returns)
    assert sequence_findings(sequence) == []


def test_nested_generic_is_a_valid_return_label():
    assert is_return_value_label("optional<list<CourseOfferingSummary>>")
    assert not is_return_value_label("optional<list<CourseOfferingSummary>")


def test_operation_feedback_rebuilds_only_the_owned_contract(monkeypatch):
    revised = False

    def fake_parse(_messages, schema, **kwargs):
        nonlocal revised
        if schema is InventoryProposal:
            return _inventory_proposal()
        if schema is OperationFragment:
            candidate = _operation_fragment()
            if kwargs["operation"] == "InteractionOperationFeedback":
                revised = True
                candidate["Classes"][0]["operations"][0]["name"] = "send"
            return candidate
        if issubclass(schema, CallPlanProposal):
            plan = _call_plan()
            if revised:
                plan["calls"][0]["receiverOperationId"] = (
                    "RequestBoundary::send(request:RequestData)"
                )
            return plan
        raise AssertionError(schema)

    monkeypatch.setattr(pipeline, "parse_structured", fake_parse)
    current = pipeline.generate_class_model(_single_use_case())
    inventory_before = [
        (item["className"], item["fields"], item["identifier"])
        for item in current["Classes"]
    ]

    result = pipeline.revise_class_model(
        current,
        _single_use_case(),
        "Rename the actor-facing operation to send.",
        {"UC1"},
    )

    boundary = next(
        item for item in result["Classes"] if item["className"] == "RequestBoundary"
    )
    assert boundary["operations"][0]["name"] == "send"
    assert [
        (item["className"], item["fields"], item["identifier"])
        for item in result["Classes"]
    ] == inventory_before
    assert result["Collaborations"][0]["calls"][0]["receiverOperationId"].startswith(
        "RequestBoundary::send("
    )
