"""Domain-neutral regression coverage for deterministic call-value sources.

These cases deliberately vary UC IDs and domain terminology.  They protect the
source contract, not a particular course-registration diagram.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram import collaboration, operations
from app.design.services.class_diagram.identity import materialize_pre_collaboration_refs
from app.design.services.class_diagram.proposals import OperationFragment
from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.class_diagram.trusted_context import required_value_catalog
from app.design.services.class_diagram.type_system import required_value_type_compatible
from app.design.services.class_diagram.validation import validate_class_model
from app.design.services.class_diagram.validation.collaboration import (
    CollaborationContext,
    _collaboration_bindings,
)
from app.design.services.class_diagram.validation.operations import (
    OperationContext,
    validate_operations,
)


def _scenario(
    use_case_id: str,
    actor: str,
    preconditions: list[str],
    actors: list[dict] | None = None,
    identity_obligations: list[dict] | None = None,
    required_values: list[dict] | None = None,
) -> dict:
    scenario_actors = actors or [{"actor_ref": "ACT1", "name": actor}]
    for index, item in enumerate(scenario_actors, start=1):
        item.setdefault("actor_ref", f"ACT{index}")
    actor_ref = next(
        (item["actor_ref"] for item in scenario_actors if item.get("name") == actor),
        scenario_actors[0]["actor_ref"],
    )
    result = {
        "use_cases": [{
            "id": use_case_id,
            "name": f"Manage {actor.lower()} request",
            "primary_actor": actor,
            "primary_actor_ref": actor_ref,
        }],
        "use_case_specs": [{
            "use_case_id": use_case_id,
            "preconditions": preconditions,
            "public_contract": {
                "identity_obligations": identity_obligations or [],
                "required_values": required_values or [],
            },
            "main_scenario": [
                {
                    "step_number": 1,
                    "subject_ref": actor_ref,
                    "sentence": f"{actor} submits a request.",
                },
                {
                    "step_number": 2,
                    "subject_ref": "System",
                    "sentence": "System processes the request.",
                },
            ],
            "extensions": [],
        }],
        "relationships": {"includes": [], "extends": []},
    }
    result["actors"] = scenario_actors
    return result


def _identity_handoff_model(
    use_case_id: str,
    prefix: str,
    identity_name: str,
    obligation_ref: str | None = None,
) -> BCEModel:
    request_type = f"{prefix}Request"
    result_type = f"{prefix}Result"
    boundary = f"{prefix}Boundary"
    control = f"{prefix}Control"
    identity_parameter = identity_name.removesuffix("Identity").lower()
    return materialize_pre_collaboration_refs(None, BCEModel.model_validate({
        "Classes": [
            {
                "className": boundary,
                "stereotype": "Boundary",
                "use_case_ids": [use_case_id],
                "operations": [{
                    "operationId": "ignored",
                    "name": "submit",
                    "parameters": [{"name": "request", "type": request_type}],
                    "returnType": result_type,
                    "stepRefs": [f"{use_case_id}:main:1"],
                }],
            },
            {
                "className": control,
                "stereotype": "Control",
                "use_case_ids": [use_case_id],
                "operations": [{
                    "operationId": "ignored",
                    "name": "process",
                    "parameters": [
                        {"name": "request", "type": request_type},
                        {
                            "name": identity_parameter,
                            "type": identity_name,
                            **({"requiredValueRef": obligation_ref} if obligation_ref else {}),
                        },
                    ],
                    "returnType": result_type,
                    "stepRefs": [f"{use_case_id}:main:2"],
                }],
            },
        ],
        "DataTypes": [
            {
                "name": request_type,
                "kind": "valueObject",
                "fields": ["requestId : UUID"],
            },
            {
                "name": result_type,
                "kind": "valueObject",
                "fields": ["accepted : Boolean"],
            },
            {
                "name": identity_name,
                "kind": "valueObject",
                "fields": ["subjectId : UUID"],
            },
        ],
        "Relationships": [],
        "Collaborations": [],
    }))


def _identity_plan(prefix: str, identity_name: str):
    identity_parameter = identity_name.removesuffix("Identity").lower()
    return collaboration.CallPlanProposal.model_validate({
        "calls": [
            {
                "receiverOperationId": (
                    f"{prefix}Boundary::submit(request:{prefix}Request)"
                ),
                "parentCallIndex": None,
            },
            {
                "receiverOperationId": (
                    f"{prefix}Control::process(request:{prefix}Request,"
                    f"{identity_parameter}:{identity_name})"
                ),
                "parentCallIndex": 1,
            },
        ],
    })


def _select_offered_finite_source(_use_case, ambiguous, _parameter_types, **_kwargs):
    """Choose the direct matching formal, or the sole evidenced context value."""
    selected = {}
    for location, candidates in ambiguous.items():
        direct = next(
            (source for source in candidates if source.startswith("call_") and "#param_" in source),
            None,
        )
        selected[location] = direct or candidates[0]
    return selected


@pytest.mark.parametrize(
    ("use_case_id", "actor", "prefix", "identity_name"),
    [
        ("UC17", "Customer", "Booking", "UUID"),
        ("UC42", "Patient", "Admission", "UUID"),
    ],
)
def test_authenticated_required_identifier_is_a_direct_handoff_source(
    monkeypatch,
    use_case_id: str,
    actor: str,
    prefix: str,
    identity_name: str,
):
    """An evidenced identity is available only across Boundary -> Control."""

    specification = _scenario(
        use_case_id,
        actor,
        [
            f"Authenticated {identity_name} is available in trusted request context."
        ],
        identity_obligations=[{
            "obligation_ref": f"ob-{use_case_id}", "subject_ref": f"sub-{use_case_id}",
            "subject": "display-only", "obligation": "authenticate",
            "requirement_ids": [f"REQ-{use_case_id}"],
        }, {
            "obligation_ref": f"identify-{use_case_id}", "subject_ref": f"sub-{use_case_id}",
            "subject": "display-only", "obligation": "identify",
            "identity_source_kind": "authenticated_context",
            "source_authenticate_obligation_ref": f"ob-{use_case_id}",
            "requirement_ids": [f"REQ-{use_case_id}"],
        }],
        required_values=[{
            "value_ref": f"val-{use_case_id}", "name": "actor id",
            "source": "authenticated_actor_context", "value_type": "identifier",
            "usage": "control", "requirement_ids": [f"REQ-{use_case_id}"],
            "identity_obligation_ref": f"identify-{use_case_id}",
        }],
    )
    index = build_scenario_index(specification)
    monkeypatch.setattr(
        collaboration, "select_ambiguous_bindings",
        lambda _use_case, ambiguous, _parameter_types, **_kwargs: {
            location: next(source for source in candidates if source.startswith("value#"))
            if any(source.startswith("value#") for source in candidates)
            else candidates[0]
            for location, candidates in ambiguous.items()
        },
    )
    model = _identity_handoff_model(
        use_case_id, prefix, identity_name, f"val-{use_case_id}",
    )
    result = collaboration.materialize(
        index,
        model,
        index.use_case(use_case_id),
        _identity_plan(prefix, identity_name),
    )

    assert [binding.source_ref for binding in result.calls[1].argument_bindings] == [
        f"{result.calls[0].stable_id}#{model.Classes[0].operations[0].parameters[0].stable_ref}",
        f"value#val-{use_case_id}",
    ]


def test_required_value_catalog_is_exact_typed_and_keeps_availability():
    index = build_scenario_index(_scenario("UC84", "Learner", [], required_values=[
        {"value_ref": "val-a", "name": "student", "source": "authenticated_actor_context",
         "value_type": "identifier", "usage": "control", "requirement_ids": ["R1"]},
        {"value_ref": "val-b", "name": "details", "source": "caller_input",
         "value_type": "object", "usage": "control", "requirement_ids": ["R2"]},
        {"value_ref": "val-c", "name": "receipt", "source": "system_result",
         "value_type": "string", "usage": "result", "requirement_ids": ["R3"]},
    ]))
    catalog = required_value_catalog(index.use_case("UC84"))
    assert [item["sourceRef"] for item in catalog] == ["value#val-a", "value#val-b", "value#val-c"]
    assert [item["availability"] for item in catalog] == ["server_context", "actor_entry", "prior_result"]
    assert catalog[0]["identityObligationRef"] is None


def test_operation_validator_checks_exact_required_value_ref_and_type():
    index = build_scenario_index(_scenario("UC87", "Reviewer", [], required_values=[{
        "value_ref": "val-87", "name": "student id", "source": "authenticated_actor_context",
        "value_type": "identifier", "usage": "control", "requirement_ids": ["REQ-87"],
        "identity_obligation_ref": "identify-87",
    }]))
    inventory = {"Classes": [{"className": "ReviewControl", "stereotype": "Control"}], "DataTypes": []}
    fragment = {"DataTypes": [], "Classes": [{"className": "ReviewControl", "operations": [{
        "name": "process", "parameters": [
            {"name": "studentId", "type": "UUID", "requiredValueRef": "val-87"},
            {"name": "bad", "type": "ReviewRequest", "requiredValueRef": "unknown"},
        ], "returnType": "void", "stepRefs": ["UC87:main:2"],
    }]}]}
    report = validate_operations(fragment, OperationContext(index, inventory, index.use_case("UC87")))
    assert len([f for f in report.findings if f.rule_id == "class.operation.required-value"]) == 1


@pytest.mark.parametrize(
    ("parameter_type", "data_type", "expected_required_findings", "expected_allowed_findings"),
    [
        ("ReviewStatus", {"name": "ReviewStatus", "kind": "enumeration", "values": ["open", "closed"]}, 0, 0),
        ("String", None, 1, 1),
    ],
)
def test_finite_string_required_value_requires_exact_enum_in_combined_validation(
    parameter_type: str, data_type: dict | None,
    expected_required_findings: int, expected_allowed_findings: int,
):
    index = build_scenario_index(_scenario("UC90", "Reviewer", [], required_values=[{
        "value_ref": "val-status", "name": "review status", "source": "caller_input",
        "value_type": "string", "usage": "control", "requirement_ids": ["REQ-90"],
        "allowed_values": ["open", "closed"],
    }]))
    inventory = {"Classes": [{"className": "ReviewControl", "stereotype": "Control"}], "DataTypes": []}
    fragment = {
        "DataTypes": [data_type] if data_type else [],
        "Classes": [{"className": "ReviewControl", "operations": [{
            "name": "process", "parameters": [{
                "name": "status", "type": parameter_type, "requiredValueRef": "val-status",
            }], "returnType": "void", "stepRefs": ["UC90:main:2"],
        }]}],
    }

    report = validate_operations(fragment, OperationContext(index, inventory, index.use_case("UC90")))
    assert len([f for f in report.findings if f.rule_id == "class.operation.required-value"]) == expected_required_findings
    assert len([f for f in report.findings if f.rule_id == "class.operation.allowed-values"]) == expected_allowed_findings


def test_object_required_value_accepts_a_locally_declared_structured_refinement():
    index = build_scenario_index(_scenario("UC88", "Reviewer", [], required_values=[{
        "value_ref": "val-details", "name": "term details", "source": "caller_input",
        "value_type": "object", "usage": "control", "requirement_ids": ["REQ-88"],
    }]))
    inventory = {"Classes": [{"className": "ReviewControl", "stereotype": "Control"}], "DataTypes": []}
    fragment = {
        "DataTypes": [{"name": "TermDetailsDto", "kind": "valueObject", "fields": [
            "termId : UUID", "title : String",
        ]}],
        "Classes": [{"className": "ReviewControl", "operations": [{
            "name": "process", "parameters": [{
                "name": "details", "type": "TermDetailsDto", "requiredValueRef": "val-details",
            }], "returnType": "void", "stepRefs": ["UC88:main:2"],
        }]}],
    }

    report = validate_operations(fragment, OperationContext(index, inventory, index.use_case("UC88")))
    assert not [f for f in report.findings if f.rule_id == "class.operation.required-value"]


def test_unknown_required_value_refines_only_to_a_nonempty_local_dto():
    required_value = {"designType": "unknown", "valueRef": "val-search"}
    structured_dto = {
        "name": "SearchCriteria", "kind": "valueObject", "fields": ["query : String"],
    }

    assert required_value_type_compatible(
        required_value, "SearchCriteria", [structured_dto],
    )
    assert required_value_type_compatible(
        {**required_value, "designType": "Object"}, "SearchCriteria", [structured_dto],
    )
    assert not required_value_type_compatible(required_value, "String", [structured_dto])
    assert not required_value_type_compatible(required_value, "Object", [structured_dto])
    assert not required_value_type_compatible(required_value, "UndeclaredDto", [structured_dto])
    assert not required_value_type_compatible(
        required_value, "EmptyDto", [{"name": "EmptyDto", "kind": "dataType", "fields": []}],
    )
    assert not required_value_type_compatible(
        {"designType": "unknown"}, "SearchCriteria", [structured_dto],
    )


@pytest.mark.parametrize(
    ("parameter_type", "inventory_data_types", "expected_findings"),
    [
        (
            "SearchCriteria",
            [{"name": "SearchCriteria", "kind": "valueObject", "fields": ["query : String"]}],
            0,
        ),
        (
            "EmptyCriteria",
            [{"name": "EmptyCriteria", "kind": "valueObject", "fields": []}],
            1,
        ),
        ("UndeclaredCriteria", [], 1),
    ],
)
def test_unknown_required_value_refinement_uses_effective_inventory_dtos(
    parameter_type: str, inventory_data_types: list[dict], expected_findings: int,
):
    index = build_scenario_index(_scenario("UC89", "Searcher", [], required_values=[{
        "value_ref": "val-search", "name": "search criteria", "source": "caller_input",
        "value_type": "unknown", "usage": "control", "requirement_ids": ["REQ-89"],
    }]))
    inventory = {
        "Classes": [{"className": "SearchControl", "stereotype": "Control"}],
        "DataTypes": inventory_data_types,
    }
    fragment = {
        # Workspace repair omits fixed DataTypes from a replacement fragment.
        "DataTypes": [],
        "Classes": [{"className": "SearchControl", "operations": [{
            "name": "search", "parameters": [{
                "name": "criteria", "type": parameter_type, "requiredValueRef": "val-search",
            }], "returnType": "void", "stepRefs": ["UC89:main:2"],
        }]}],
    }

    report = validate_operations(fragment, OperationContext(index, inventory, index.use_case("UC89")))
    assert len([f for f in report.findings if f.rule_id == "class.operation.required-value"]) == expected_findings


@pytest.mark.parametrize(
    ("parameter_type", "declared_types"),
    [
        ("UUID", []),
        ("UndeclaredDto", []),
        ("EmptyDto", [{"name": "EmptyDto", "kind": "valueObject", "fields": []}]),
        ("EnumDto", [{"name": "EnumDto", "kind": "enumeration", "fields": ["x : String"]}]),
    ],
)
def test_object_required_value_rejects_non_structured_or_undeclared_refinements(
    parameter_type: str, declared_types: list[dict],
):
    index = build_scenario_index(_scenario("UC88", "Reviewer", [], required_values=[{
        "value_ref": "val-details", "name": "term details", "source": "caller_input",
        "value_type": "object", "usage": "control", "requirement_ids": ["REQ-88"],
    }]))
    inventory = {"Classes": [{"className": "ReviewControl", "stereotype": "Control"}], "DataTypes": []}
    fragment = {
        "DataTypes": declared_types,
        "Classes": [{"className": "ReviewControl", "operations": [{
            "name": "process", "parameters": [{
                "name": "details", "type": parameter_type, "requiredValueRef": "val-details",
            }], "returnType": "void", "stepRefs": ["UC88:main:2"],
        }]}],
    }

    report = validate_operations(fragment, OperationContext(index, inventory, index.use_case("UC88")))
    assert len([f for f in report.findings if f.rule_id == "class.operation.required-value"]) == 1


def _object_refinement_collaboration(declared_types: list[dict]) -> tuple[dict, object, dict]:
    index = build_scenario_index(_scenario("UC90", "Reviewer", [
        "Authenticated review details are available in trusted request context.",
    ], required_values=[{
        "value_ref": "val-details", "name": "review details",
        "source": "authenticated_actor_context", "value_type": "object",
        "usage": "control", "requirement_ids": ["REQ-90"],
    }]))
    use_case = index.use_case("UC90")
    model = {
        "Classes": [
            {"className": "ReviewBoundary", "stereotype": "Boundary", "operations": [{
                "operationId": "ReviewBoundary::submit()", "name": "submit",
                "parameters": [], "returnType": "void", "stepRefs": ["UC90:main:1"],
            }]},
            {"className": "ReviewControl", "stereotype": "Control", "operations": [{
                "operationId": "ReviewControl::process(details:ReviewDetailsDto)",
                "name": "process", "parameters": [{
                    "name": "details", "type": "ReviewDetailsDto",
                    "stableRef": "param-details", "requiredValueRef": "val-details",
                }], "returnType": "void", "stepRefs": ["UC90:main:2"],
            }]},
        ],
        "DataTypes": declared_types,
    }
    calls = [
        {"callId": "UC90::call:1", "stableId": "call-entry", "parentCallId": None,
         "receiverOperationId": "ReviewBoundary::submit()", "argumentBindings": []},
        {"callId": "UC90::call:2", "stableId": "call-process", "parentCallId": "UC90::call:1",
         "receiverOperationId": "ReviewControl::process(details:ReviewDetailsDto)",
         "argumentBindings": [{"parameter": "details", "sourceRef": "value#val-details"}]},
    ]
    return index, use_case, {"model": model, "calls": calls}


def test_object_required_value_refinement_is_eligible_and_validated_in_collaboration():
    data_type = {"name": "ReviewDetailsDto", "kind": "valueObject", "fields": [
        "reviewId : UUID", "decision : String",
    ]}
    index, use_case, fixture = _object_refinement_collaboration([data_type])
    model, calls = fixture["model"], fixture["calls"]
    operations = {
        "ReviewBoundary::submit()": {"stereotype": "boundary", "parameters": []},
        "ReviewControl::process(details:ReviewDetailsDto)": {
            "stereotype": "control", "parameters": model["Classes"][1]["operations"][0]["parameters"],
        },
    }

    candidates = collaboration._binding_candidates(
        model, use_case, None, False, calls, 1,
        model["Classes"][1]["operations"][0]["parameters"][0], operations,
    )
    findings = _collaboration_bindings(
        {"collaborationId": "UC90", "calls": calls},
        CollaborationContext(index, model, use_case),
    )
    assert "value#val-details" in candidates
    assert not [item for item in findings if item.rule_id == "class.collaboration.bindings"]


@pytest.mark.parametrize(
    "declared_types",
    [
        [],
        [{"name": "ReviewDetailsDto", "kind": "valueObject", "fields": []}],
        [{"name": "ReviewDetailsDto", "kind": "enumeration", "fields": ["x : String"]}],
    ],
)
def test_object_required_value_refinement_is_rejected_for_undeclared_empty_or_non_value_objects(
    declared_types: list[dict],
):
    index, use_case, fixture = _object_refinement_collaboration(declared_types)
    model, calls = fixture["model"], fixture["calls"]
    operations = {
        "ReviewBoundary::submit()": {"stereotype": "boundary", "parameters": []},
        "ReviewControl::process(details:ReviewDetailsDto)": {
            "stereotype": "control", "parameters": model["Classes"][1]["operations"][0]["parameters"],
        },
    }

    candidates = collaboration._binding_candidates(
        model, use_case, None, False, calls, 1,
        model["Classes"][1]["operations"][0]["parameters"][0], operations,
    )
    findings = _collaboration_bindings(
        {"collaborationId": "UC90", "calls": calls},
        CollaborationContext(index, model, use_case),
    )
    assert "value#val-details" not in candidates
    assert any(item.rule_id == "class.collaboration.bindings" for item in findings)


def test_result_only_required_value_mismatch_gives_directional_repair_context():
    index = build_scenario_index(_scenario("UC92", "Reviewer", [], required_values=[{
        "value_ref": "val-schedule", "name": "current schedule",
        "source": "system_result", "value_type": "object", "usage": "result",
        "requirement_ids": ["R-SCHEDULE"],
    }]))
    inventory = {
        "Classes": [{"className": "ScheduleControl", "stereotype": "Control"}],
        "DataTypes": [],
    }
    fragment = {"DataTypes": [], "Classes": [{"className": "ScheduleControl", "operations": [{
        "name": "exportSchedule",
        "parameters": [{
            "name": "schedule", "type": "ScheduleData", "requiredValueRef": "val-schedule",
        }],
        "returnType": "ExportFile",
        "stepRefs": ["UC92:main:1", "UC92:main:2"],
    }]}]}

    report = validate_operations(
        fragment, OperationContext(index, inventory, index.use_case("UC92")),
    )
    finding = next(item for item in report.findings if item.rule_id == "class.operation.required-value")
    assert "usage=result" in finding.message
    assert "designType=Object" in finding.message
    assert "parameter 'schedule':ScheduleData" in finding.message
    assert "remove the ref from the input and evidence the value via a concrete Control return" in finding.message
    assert "do not alter the type just to fit the ref" in finding.message


def test_operation_validator_keeps_server_context_out_of_boundary_signature():
    index = build_scenario_index(_scenario("UC89", "Student", [], required_values=[{
        "value_ref": "val-session", "name": "student id", "source": "authenticated_actor_context",
        "value_type": "identifier", "usage": "control", "requirement_ids": ["R-ID"],
    }]))
    inventory = {"Classes": [{"className": "StudentBoundary", "stereotype": "Boundary"}], "DataTypes": []}
    fragment = {"DataTypes": [], "Classes": [{"className": "StudentBoundary", "operations": [{
        "name": "submit", "parameters": [{"name": "studentId", "type": "UUID", "requiredValueRef": "val-session"}],
        "returnType": "void", "stepRefs": ["UC89:main:1"],
    }]}]}
    report = validate_operations(fragment, OperationContext(index, inventory, index.use_case("UC89")))
    assert any("cannot be exposed as actor-facing Boundary" in f.message for f in report.findings)


def test_direct_required_value_binding_is_not_offered_for_caller_or_system_result():
    use_case_id = "UC86"
    index = build_scenario_index(_scenario(use_case_id, "Reviewer", [], required_values=[
        {"value_ref": "val-input", "name": "criteria", "source": "caller_input",
         "value_type": "string", "usage": "control", "requirement_ids": ["R1"]},
        {"value_ref": "val-output", "name": "result", "source": "system_result",
         "value_type": "string", "usage": "result", "requirement_ids": ["R2"]},
    ]))
    refs = {item["sourceRef"] for item in required_value_catalog(index.use_case(use_case_id))}
    assert refs == {"value#val-input", "value#val-output"}
    assert not any(item["availability"] == "server_context" for item in required_value_catalog(index.use_case(use_case_id)))

    model = {"Classes": [], "DataTypes": [], "Collaborations": []}
    operations = {
        "Entry::submit(id:UUID)": {"stereotype": "boundary", "returnType": "void",
                                   "parameters": [{"name": "id", "type": "UUID", "stableRef": "entry-id"}]},
        "Work::first()": {"stereotype": "control", "returnType": "UUID", "parameters": []},
        "Work::consume(id:UUID)": {"stereotype": "control", "returnType": "void", "parameters": []},
    }
    calls = [
        {"callId": "UC86::call:1", "stableId": "call-entry", "receiverOperationId": "Entry::submit(id:UUID)", "parentCallId": None},
        {"callId": "UC86::call:2", "stableId": "call-first", "receiverOperationId": "Work::first()", "parentCallId": "UC86::call:1"},
        {"callId": "UC86::call:3", "stableId": "call-target", "receiverOperationId": "Work::consume(id:UUID)", "parentCallId": "UC86::call:2"},
    ]
    for value_ref in ("val-input", "val-output"):
        candidates = collaboration._binding_candidates(
            model, index.use_case(use_case_id), "UC86:main:1", False, calls, 2,
            {"name": "id", "type": "UUID", "requiredValueRef": value_ref}, operations,
        )
        assert f"value#{value_ref}" not in candidates
        if value_ref == "val-output":
            assert "call-first#result" in candidates


def test_boundary_control_handoff_uses_compatible_opaque_id_despite_parameter_rename(monkeypatch):
    """RTM handoff provenance survives a harmless identifier rename."""

    use_case_id = "UC73"
    index = build_scenario_index(_scenario(use_case_id, "Visitor", []))
    model = BCEModel.model_validate({
        "Classes": [
            {
                "className": "RegistrationBoundary",
                "stereotype": "Boundary",
                "use_case_ids": [use_case_id],
                "operations": [{
                    "operationId": "ignored",
                    "name": "submit",
                    "parameters": [{"name": "registrationIdentifier", "type": "UUID"}],
                    "returnType": "void",
                    "stepRefs": [f"{use_case_id}:main:1"],
                }],
            },
            {
                "className": "RegistrationControl",
                "stereotype": "Control",
                "use_case_ids": [use_case_id],
                "operations": [{
                    "operationId": "ignored",
                    "name": "register",
                    "parameters": [{"name": "registrationId", "type": "UUID"}],
                    "returnType": "void",
                    "stepRefs": [f"{use_case_id}:main:2"],
                }],
            },
        ],
        "DataTypes": [],
        "Relationships": [],
        "Collaborations": [],
    })
    model = materialize_pre_collaboration_refs(None, model)
    plan = collaboration.CallPlanProposal.model_validate({
        "calls": [
            {
                "receiverOperationId": "RegistrationBoundary::submit(registrationIdentifier:UUID)",
                "parentCallIndex": None,
            },
            {
                "receiverOperationId": "RegistrationControl::register(registrationId:UUID)",
                "parentCallIndex": 1,
            },
        ],
    })

    def select_source(_use_case, ambiguous, _parameter_types, **_kwargs):
        location = f"{use_case_id}::call:2#registrationId"
        source_call = _kwargs["calls"][0]
        source_param = model.Classes[0].operations[0].parameters[0]
        stable_source = f"{source_call['stableId']}#{source_param.stable_ref}"
        assert ambiguous == {location: [stable_source]}
        assert _kwargs["semantic_locations"] == {location}
        return {location: stable_source}

    monkeypatch.setattr(collaboration, "select_ambiguous_bindings", select_source)

    result = collaboration.materialize(
        index, model, index.use_case(use_case_id), plan,
    )

    assert result.calls[1].argument_bindings[0].source_ref == (
        f"{result.calls[0].stable_id}#{model.Classes[0].operations[0].parameters[0].stable_ref}"
    )

    def select_no_match(_use_case, ambiguous, _parameter_types, **_kwargs):
        return {next(iter(ambiguous)): collaboration.NO_BINDING_SOURCE}

    monkeypatch.setattr(collaboration, "select_ambiguous_bindings", select_no_match)
    with pytest.raises(collaboration.BindingSourceViolation) as no_match:
        collaboration.materialize(index, model, index.use_case(use_case_id), plan)
    assert no_match.value.repair_slot == {
        "actorEntryIndex": 0, "callIndex": 1, "parameterIndex": 0,
    }

    with pytest.raises(collaboration.BindingSourceViolation):
        collaboration.materialize(
            index,
            model,
            index.use_case(use_case_id),
            plan,
            binding_source_decision={
                "useCaseId": use_case_id,
                "actorEntryIndex": 0,
                "callIndex": 1,
                "parameterIndex": 0,
                "sourceKind": "authenticated_context",
            },
        )


@pytest.mark.parametrize("preconditions", [
    ["Customer accepts the booking policy before submitting the request."],
    ["The booking catalog is available and the selected slot is open."],
])
def test_identity_named_without_authenticated_context_is_not_invented(
    preconditions: list[str],
):
    """A role mention alone must not manufacture a caller identity value."""

    use_case_id = "UC18"
    specification = _scenario(
        use_case_id,
        "Customer",
        preconditions,
    )
    index = build_scenario_index(specification)

    with pytest.raises(collaboration.BindingSourceViolation) as caught:
        collaboration.materialize(
            index,
            _identity_handoff_model(use_case_id, "Booking", "CustomerIdentity"),
            index.use_case(use_case_id),
            _identity_plan("Booking", "CustomerIdentity"),
        )

    context = caught.value.repair_context
    assert context["parameter"] == {"name": "customer", "type": "CustomerIdentity"}
    assert "trusted-context" in context["searchedSourceScopes"]
    assert "context#" not in str(context)


def test_anonymous_search_has_no_identity_or_context_binding(monkeypatch):
    """Public read flows stay valid without an artificial authentication source."""

    use_case_id = "UC33"
    index = build_scenario_index(_scenario(use_case_id, "Visitor", []))
    model = BCEModel.model_validate({
        "Classes": [
            {
                "className": "CatalogBoundary",
                "stereotype": "Boundary",
                "use_case_ids": [use_case_id],
                "operations": [{
                    "operationId": "ignored",
                    "name": "search",
                    "parameters": [{"name": "criteria", "type": "SearchCriteria"}],
                    "returnType": "SearchResult",
                    "stepRefs": [f"{use_case_id}:main:1"],
                }],
            },
            {
                "className": "CatalogControl",
                "stereotype": "Control",
                "use_case_ids": [use_case_id],
                "operations": [{
                    "operationId": "ignored",
                    "name": "find",
                    "parameters": [{"name": "criteria", "type": "SearchCriteria"}],
                    "returnType": "SearchResult",
                    "stepRefs": [f"{use_case_id}:main:2"],
                }],
            },
        ],
        "DataTypes": [
            {"name": "SearchCriteria", "kind": "valueObject", "fields": ["query : String"]},
            {"name": "SearchResult", "kind": "valueObject", "fields": ["count : Integer"]},
        ],
        "Relationships": [],
        "Collaborations": [],
    })
    model = materialize_pre_collaboration_refs(None, model)
    plan = collaboration.CallPlanProposal.model_validate({"calls": [
        {"receiverOperationId": "CatalogBoundary::search(criteria:SearchCriteria)", "parentCallIndex": None},
        {"receiverOperationId": "CatalogControl::find(criteria:SearchCriteria)", "parentCallIndex": 1},
    ]})

    monkeypatch.setattr(
        collaboration, "select_ambiguous_bindings", _select_offered_finite_source,
    )
    result = collaboration.materialize(index, model, index.use_case(use_case_id), plan)

    assert result.calls[1].argument_bindings[0].source_ref == (
        f"{result.calls[0].stable_id}#{model.Classes[0].operations[0].parameters[0].stable_ref}"
    )
    assert all(
        not binding.source_ref.startswith("context#")
        for call in result.calls for binding in call.argument_bindings
    )


def test_actor_description_and_lineage_cannot_supply_identity_context():
    """Actor names and descriptions are display data, not a trust source."""

    use_case_id = "UC58"
    specification = _scenario(
        use_case_id,
        "PremiumGuest",
        [],
        actors=[
            {
                "name": "PremiumGuest",
                "description": "A registered guest with premium access.",
                "parent_actor": "Guest",
                "source_refs": ["RR12"],
            },
            {
                "name": "Guest",
                "description": "Authenticated guest identity is trusted by the platform.",
                "parent_actor": None,
                "source_refs": ["RR5"],
            },
        ],
    )
    index = build_scenario_index(specification)
    assert required_value_catalog(index.use_case(use_case_id)) == ()


def test_untrusted_parent_actor_hierarchy_does_not_supply_child_identity():
    use_case_id = "UC59"
    specification = _scenario(
        use_case_id,
        "MemberVisitor",
        [],
        actors=[
            {
                "name": "MemberVisitor",
                "description": "A visitor enrolled in the membership program.",
                "parent_actor": "Visitor",
                "source_refs": ["RR14"],
            },
            {
                "name": "Visitor",
                "description": "A person who may browse the public catalog.",
                "parent_actor": None,
                "source_refs": ["RR13"],
            },
        ],
    )
    index = build_scenario_index(specification)

    with pytest.raises(collaboration.BindingSourceViolation):
        collaboration.materialize(
            index,
            _identity_handoff_model(
                use_case_id, "Visit", "MemberVisitorIdentity"
            ),
            index.use_case(use_case_id),
            _identity_plan("Visit", "MemberVisitorIdentity"),
        )


def test_operation_payload_exposes_exact_required_value_catalog():
    """The proposer receives finite refs, never actor-description guesses."""

    trusted = build_scenario_index(_scenario(
        "UC71",
        "VerifiedBuyer",
        [],
        identity_obligations=[{
            "obligation_ref": "ob-context:71", "subject_ref": "sub-71", "subject": "display-only",
            "obligation": "authenticate", "requirement_ids": ["RR7"],
        }, {
            "obligation_ref": "identify-buyer", "subject_ref": "sub-71", "subject": "display-only",
            "obligation": "identify", "identity_source_kind": "authenticated_context",
            "source_authenticate_obligation_ref": "ob-context:71", "requirement_ids": ["RR7"],
        }],
        required_values=[{
            "value_ref": "val-student-71", "name": "student id",
            "source": "authenticated_actor_context", "value_type": "identifier",
            "usage": "control", "requirement_ids": ["RR7"],
            "identity_obligation_ref": "identify-buyer",
        }],
    ))
    payload = operations._operation_payload(
        trusted, {"Classes": [], "DataTypes": []}, trusted.use_case("UC71"),
    )

    assert payload["requiredValueSources"][0]["sourceRef"] == "value#RV1"
    assert payload["requiredValueSources"][0]["valueRef"] == "RV1"
    assert "val-student-71" not in str(payload)
    assert payload["requiredValueSources"][0]["identityObligationRef"] == "identify-buyer"
    assert payload["requiredValueSources"][0]["availability"] == "server_context"
    assert set(payload["valueSourcePolicy"]) == {
        "requestInputs", "requiredValues", "previousResults", "runtimeValues", "derivedValues",
    }

    bare = build_scenario_index(_scenario("UC72", "Browser", []))
    bare_payload = operations._operation_payload(
        bare, {"Classes": [], "DataTypes": []}, bare.use_case("UC72"),
    )
    assert bare_payload["requiredValueSources"] == []


def test_operation_prompt_distinguishes_public_contract_value_origins():
    prompt = " ".join(operations.operation_prompt().split())

    assert "public_contract.required_values" in prompt
    assert "For `control` or `both`" in prompt
    assert "A `result`-only value is evidenced by a concrete non-void Control return" in prompt
    assert "A `system_result` may also be cited on a downstream parameter" in prompt
    assert "a Boundary annotation alone is insufficient" in prompt
    assert "Never change a parameter type merely to make a ref compatible" in prompt
    assert "server_context values as actor-facing Boundary inputs" in prompt
    assert "requiredValueRef" in prompt


def test_class_prompts_keep_request_response_dataflow_within_reachable_calls():
    from app.design.services.class_diagram import collaboration
    from app.design.services.class_diagram.generation import _COMBINED_PROMPT

    operation_prompt = " ".join(operations.operation_prompt().split())
    call_plan_prompt = " ".join(collaboration.CALL_PLAN_PROMPT.split())
    combined_prompt = " ".join(_COMBINED_PROMPT.split())

    for prompt in (operation_prompt, call_plan_prompt, combined_prompt):
        assert "exactly one" in prompt
        assert "parent Control" in prompt
        assert "not available to descendants" in prompt or "not available to its child" in prompt
        assert "independently available" in prompt
    assert "parentCallIndex identifies the caller, not a data dependency" in call_plan_prompt


def _hand_authored_context_model(
    use_case_id: str,
    *,
    context_type: str,
    context_parameter: str,
) -> BCEModel:
    """A compact two-call model for validator-only persisted-artifact tests."""

    return BCEModel.model_validate({
        "Classes": [
            {
                "className": "AccessBoundary",
                "stereotype": "Boundary",
                "use_case_ids": [use_case_id],
                "operations": [{
                    "operationId": "ignored",
                    "name": "submit",
                    "parameters": [{"name": "request", "type": "AccessRequest"}],
                    "returnType": "AccessResult",
                    "stepRefs": [f"{use_case_id}:main:1"],
                }],
            },
            {
                "className": "AccessControl",
                "stereotype": "Control",
                "use_case_ids": [use_case_id],
                "operations": [{
                    "operationId": "ignored",
                    "name": "process",
                    "parameters": [
                        {"name": "request", "type": "AccessRequest"},
                        {"name": context_parameter, "type": context_type},
                    ],
                    "returnType": "AccessResult",
                    "stepRefs": [f"{use_case_id}:main:2"],
                }],
            },
        ],
        "DataTypes": [
            {"name": "AccessRequest", "kind": "valueObject", "fields": ["value : String"]},
            {"name": "AccessResult", "kind": "valueObject", "fields": ["accepted : Boolean"]},
            {
                "name": context_type,
                "kind": "valueObject",
                "fields": ["subjectId : UUID"],
            } if context_type != "String" else {
                "name": "AccessMarker",
                "kind": "valueObject",
                "fields": ["value : String"],
            },
        ],
        "Relationships": [],
        "Collaborations": [],
    })


def _persisted_two_call_collaboration(
    use_case_id: str,
    context_parameter: str,
    context_type: str,
    context_source: str,
) -> dict:
    return {
        "collaborationId": use_case_id,
        "useCaseIds": [use_case_id],
        "entryActor": "Visitor",
        "calls": [
            {
                "callId": f"{use_case_id}::call:1",
                "parentCallId": None,
                "receiverOperationId": "AccessBoundary::submit(request:AccessRequest)",
                "stepRefs": [f"{use_case_id}:main:1"],
                "argumentBindings": [{
                    "parameter": "request",
                    "sourceRef": f"{use_case_id}:main:1#request",
                }],
            },
            {
                "callId": f"{use_case_id}::call:2",
                "parentCallId": f"{use_case_id}::call:1",
                "receiverOperationId": (
                    "AccessControl::process(request:AccessRequest,"
                    f"{context_parameter}:{context_type})"
                ),
                "stepRefs": [f"{use_case_id}:main:2"],
                "argumentBindings": [
                    {
                        "parameter": "request",
                        "sourceRef": f"{use_case_id}::call:1#request",
                    },
                    {"parameter": context_parameter, "sourceRef": context_source},
                ],
            },
        ],
    }


def test_validator_rejects_fabricated_trusted_context_without_evidence():
    use_case_id = "UC60"
    index = build_scenario_index(_scenario(use_case_id, "Visitor", []))
    model = _hand_authored_context_model(
        use_case_id, context_type="VisitorIdentity", context_parameter="visitor"
    ).model_dump(by_alias=True)
    model["Collaborations"] = [_persisted_two_call_collaboration(
        use_case_id,
        "visitor",
        "VisitorIdentity",
        f"context#{use_case_id}:precondition:1:visitor",
    )]

    report = validate_class_model(model, index)

    assert any(
        finding.rule_id == "class.collaboration.bindings"
        and finding.location == f"{use_case_id}::call:2#visitor"
        for finding in report.findings
    )


def test_validator_rejects_legacy_string_precondition_binding():
    use_case_id = "UC61"
    index = build_scenario_index(_scenario(
        use_case_id,
        "Visitor",
        ["Authenticated visitor context is available to the system."],
    ))
    model = _hand_authored_context_model(
        use_case_id, context_type="String", context_parameter="visitorContext"
    ).model_dump(by_alias=True)
    model["Collaborations"] = [_persisted_two_call_collaboration(
        use_case_id,
        "visitorContext",
        "String",
        f"{use_case_id}:precondition:1#visitorContext",
    )]

    report = validate_class_model(model, index)

    assert any(
        finding.rule_id == "class.collaboration.bindings"
        and finding.location == f"{use_case_id}::call:2#visitorContext"
        for finding in report.findings
    )


@pytest.mark.parametrize("dto_name", ["BookingCommand", "AdmissionCommand"])
def test_operation_fragment_uses_structured_datatype_fields(dto_name: str):
    """The proposal boundary has one DTO representation, before persistence formatting."""

    valid = {
        "DataTypes": [{
            "name": dto_name,
            "kind": "valueObject",
            "fields": [
                {"name": "referenceId", "type": "UUID"},
                {"name": "requestedAt", "type": "LocalDateTime"},
            ],
            "values": [],
        }],
        "Classes": [{
            "className": "RequestControl",
            "operations": [{
                "name": "process",
                "parameters": [{"name": "command", "type": dto_name}],
                "returnType": "void",
                "stepRefs": ["UC1:main:1"],
            }],
        }],
    }
    assert OperationFragment.model_validate(valid).DataTypes[0].fields[0].name == "referenceId"

    invalid = {**valid, "DataTypes": [{
        **valid["DataTypes"][0],
        "fields": ["referenceId : UUID"],
    }]}
    with pytest.raises(ValidationError):
        OperationFragment.model_validate(invalid)
