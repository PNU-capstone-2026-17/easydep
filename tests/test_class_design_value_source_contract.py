"""Domain-neutral regression coverage for deterministic call-value sources.

These cases deliberately vary UC IDs and domain terminology.  They protect the
source contract, not a particular course-registration diagram.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram import collaboration, operations
from app.design.services.class_diagram.proposals import OperationFragment
from app.design.services.class_diagram.scenario import build_scenario_index
from app.design.services.class_diagram.validation import validate_class_model


def _scenario(
    use_case_id: str,
    actor: str,
    preconditions: list[str],
    actors: list[dict] | None = None,
) -> dict:
    result = {
        "use_cases": [{
            "id": use_case_id,
            "name": f"Manage {actor.lower()} request",
            "primary_actor": actor,
        }],
        "use_case_specs": [{
            "use_case_id": use_case_id,
            "preconditions": preconditions,
            "main_scenario": [
                {
                    "step_number": 1,
                    "subject_ref": actor,
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
    if actors is not None:
        result["actors"] = actors
    return result


def _identity_handoff_model(
    use_case_id: str,
    prefix: str,
    identity_name: str,
) -> BCEModel:
    request_type = f"{prefix}Request"
    result_type = f"{prefix}Result"
    boundary = f"{prefix}Boundary"
    control = f"{prefix}Control"
    identity_parameter = identity_name.removesuffix("Identity").lower()
    return BCEModel.model_validate({
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
                        {"name": identity_parameter, "type": identity_name},
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
    })


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


@pytest.mark.parametrize(
    ("use_case_id", "actor", "prefix", "identity_name"),
    [
        ("UC17", "Customer", "Booking", "CustomerIdentity"),
        ("UC42", "Patient", "Admission", "PatientIdentity"),
    ],
)
def test_authenticated_context_is_a_typed_handoff_source_not_a_domain_special_case(
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
    )
    index = build_scenario_index(specification)
    result = collaboration.materialize(
        index,
        _identity_handoff_model(use_case_id, prefix, identity_name),
        index.use_case(use_case_id),
        _identity_plan(prefix, identity_name),
    )

    assert [binding.source_ref for binding in result.calls[1].argument_bindings] == [
        f"{use_case_id}::call:1#request",
        f"context#{use_case_id}:precondition:1:{identity_name.removesuffix('Identity').lower()}",
    ]


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


def test_anonymous_search_has_no_identity_or_context_binding():
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
    plan = collaboration.CallPlanProposal.model_validate({"calls": [
        {"receiverOperationId": "CatalogBoundary::search(criteria:SearchCriteria)", "parentCallIndex": None},
        {"receiverOperationId": "CatalogControl::find(criteria:SearchCriteria)", "parentCallIndex": 1},
    ]})

    result = collaboration.materialize(index, model, index.use_case(use_case_id), plan)

    assert result.calls[1].argument_bindings[0].source_ref == "UC33::call:1#criteria"
    assert all(
        not binding.source_ref.startswith("context#")
        for call in result.calls for binding in call.argument_bindings
    )


def test_authenticated_parent_actor_evidence_supplies_child_identity_without_precondition():
    """Actor hierarchy is another explicit evidence path, not role-name guessing."""

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
    result = collaboration.materialize(
        index,
        _identity_handoff_model(use_case_id, "Stay", "PremiumGuestIdentity"),
        index.use_case(use_case_id),
        _identity_plan("Stay", "PremiumGuestIdentity"),
    )

    trusted_binding = result.calls[1].argument_bindings[1]
    assert trusted_binding.parameter == "premiumguest"
    assert trusted_binding.source_ref.startswith("context#")


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


def test_operation_payload_exposes_only_explicit_inherited_actor_context():
    """The operation proposer sees bounded evidence before it invents signatures."""

    trusted = build_scenario_index(_scenario(
        "UC71",
        "VerifiedBuyer",
        [],
        actors=[
            {
                "name": "VerifiedBuyer",
                "description": "A buyer with a verified account.",
                "parent_actor": "AccountHolder",
                "source_refs": ["RR20"],
            },
            {
                "name": "AccountHolder",
                "description": "Authenticated account identity is trusted by the platform.",
                "parent_actor": None,
                "source_refs": ["RR7"],
            },
        ],
    ))
    payload = operations._operation_payload(
        trusted, {"Classes": [], "DataTypes": []}, trusted.use_case("UC71"),
    )

    assert payload["availableTrustedContext"] == [{
        "sourceRef": "context#actor:VerifiedBuyer",
        "kind": "trusted_context",
        "subject": "VerifiedBuyer",
        "evidenceRefs": ["actor:AccountHolder", "RR7"],
        "evidence": "Authenticated account identity is trusted by the platform.",
        "eligibility": "typed parameter must match the primary actor; Control handoff only",
    }]
    assert set(payload["valueSourcePolicy"]) == {
        "requestInputs", "trustedContext", "previousResults", "runtimeValues", "derivedValues",
    }

    bare = build_scenario_index(_scenario("UC72", "Browser", []))
    bare_payload = operations._operation_payload(
        bare, {"Classes": [], "DataTypes": []}, bare.use_case("UC72"),
    )
    assert bare_payload["availableTrustedContext"] == []


def test_operation_prompt_distinguishes_public_contract_value_origins():
    prompt = operations.operation_prompt()

    assert "public_contract.required_values" in prompt
    assert "system_result values are produced" in prompt
    assert "caller-supplied identifier" in prompt
    assert "invent authentication or delegation" in prompt


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
