from __future__ import annotations

from copy import deepcopy

import pytest

from app.design.services.class_diagram.scenario import ExecutionGroup, Step, UseCase
from app.design.services.executable_behavior import (
    ActorEntry,
    BindingResolver,
    BindingSelectionError,
    CallProposal,
    CallValidationError,
    CatalogValidationError,
    LocalSemanticContract,
    MaterializationError,
    ObligationKind,
    OperationContext,
    OperationResponsibility,
    OperationSemantics,
    OperationValidationError,
    ReviewDecision,
    ScenarioObligation,
    SemanticReviewAdjudicationProposal,
    SemanticReviewProposal,
    Source,
    StageStatus,
    StateEffect,
    StateEffectOperation,
    UseCaseDraft,
    assemble_catalog,
    binding_candidate_sets,
    materialize_bce_model,
    validate_behavior_prototype,
    validated_binding_plan,
    validated_call_structure,
    validated_operation_fragment,
)
from app.design.services.executable_behavior.witness import (
    WitnessValidationError,
    validate_execution_witness,
)
from scripts import run_executable_behavior_llm_experiment as experiment

SCENARIO = {
    "useCases": [
        {
            "id": "UC-SUBMIT",
            "steps": [
                {"id": "submit:1", "text": "actor submits a request"},
                {"nub": "ignored"},
                {"id": "submit:2", "text": "system processes the request"},
                {"id": "submit:3", "text": "system stores the request"},
            ],
        }
    ]
}

INVENTORY = {
    "Classes": [
        {
            "className": "SubmitBoundary",
            "stereotype": "Boundary",
            "description": "submission entry",
            "fields": [],
            "use_case_ids": ["UC-SUBMIT"],
            "identifier": [],
        },
        {
            "className": "SubmitControl",
            "stereotype": "Control",
            "description": "submission workflow",
            "fields": [],
            "use_case_ids": ["UC-SUBMIT"],
            "identifier": [],
        },
        {
            "className": "RequestRecord",
            "stereotype": "Entity",
            "description": "durable request",
            "fields": ["id : UUID"],
            "use_case_ids": ["UC-SUBMIT"],
            "identifier": ["id"],
        },
    ],
    "DataTypes": [],
    "Relationships": [],
}

OPERATION_FRAGMENT = {
    "Classes": [
        {
            "className": "SubmitBoundary",
            "operations": [
                {
                    "name": "submit",
                    "parameters": [{"name": "request", "type": "Request"}],
                    "returnType": "Result",
                    "stepRefs": ["submit:1"],
                }
            ],
        },
        {
            "className": "SubmitControl",
            "operations": [
                {
                    "name": "process",
                    "parameters": [{"name": "request", "type": "Request"}],
                    "returnType": "Result",
                    "stepRefs": ["submit:2"],
                }
            ],
        },
        {
            "className": "RequestRecord",
            "operations": [
                {
                    "name": "store",
                    "parameters": [{"name": "request", "type": "Request"}],
                    "returnType": "void",
                    "stepRefs": ["submit:3"],
                }
            ],
        },
    ],
    "DataTypes": [
        {
            "name": "Request",
            "kind": "valueObject",
            "fields": [{"name": "content", "type": "String"}],
            "values": [],
        },
        {
            "name": "Result",
            "kind": "valueObject",
            "fields": [{"name": "accepted", "type": "Boolean"}],
            "values": [],
        },
    ],
}


def draft(*, sources: tuple[Source, ...] | None = None) -> UseCaseDraft:
    return UseCaseDraft(
        use_case_id="UC-SUBMIT",
        allowed_step_ids=("submit:1", "submit:2", "submit:3"),
        actor_entries=(
            ActorEntry(
                "submit-main",
                "Requester",
                "SubmitBoundary",
                ("submit:1", "submit:2", "submit:3"),
            ),
        ),
        operation_fragment=OPERATION_FRAGMENT,
        local_semantic_contract=LocalSemanticContract(
            useCaseId="UC-SUBMIT",
            obligations=(
                ScenarioObligation(
                    obligationId="obligation:submit-input",
                    sourceRefs=("submit:1",),
                    kind=ObligationKind.INTERACTION,
                    expected="Accept the request.",
                ),
                ScenarioObligation(
                    obligationId="obligation:process",
                    sourceRefs=("submit:2",),
                    kind=ObligationKind.OUTCOME,
                    expected="Coordinate processing.",
                ),
                ScenarioObligation(
                    obligationId="obligation:store",
                    sourceRefs=("submit:3",),
                    kind=ObligationKind.STATE_TRANSITION,
                    expected="Create a durable request record.",
                    stateRef="RequestRecord",
                ),
            ),
            operations=(
                OperationSemantics(
                    operationRef="SubmitBoundary::submit(request:Request)",
                    responsibility=OperationResponsibility.COORDINATE,
                    realizes=("obligation:submit-input",),
                    delegates=("SubmitControl::process(request:Request)",),
                    outcomes=("The request enters the system.",),
                ),
                OperationSemantics(
                    operationRef="SubmitControl::process(request:Request)",
                    responsibility=OperationResponsibility.COORDINATE,
                    realizes=("obligation:process",),
                    delegates=("RequestRecord::store(request:Request)",),
                    outcomes=("Persistence is delegated.",),
                ),
                OperationSemantics(
                    operationRef="RequestRecord::store(request:Request)",
                    responsibility=OperationResponsibility.MUTATE,
                    realizes=("obligation:store",),
                    effects=(
                        StateEffect(
                            effectId="effect:create-request-record",
                            stateRef="RequestRecord",
                            operation=StateEffectOperation.CREATE,
                            executionOwner="RequestRecord",
                        ),
                    ),
                    outcomes=("The durable request exists.",),
                ),
            ),
        ),
        local_review=SemanticReviewProposal(
            decision=ReviewDecision.PASS,
            findings=(),
        ),
        local_adjudication=SemanticReviewAdjudicationProposal(findings=()),
        call_proposals=(
            CallProposal(
                "SubmitBoundary::submit(request:Request)",
                "submit-root",
                group_key="submit-main",
            ),
            CallProposal(
                "SubmitControl::process(request:Request)",
                "submit-control",
                "submit-root",
                group_key="submit-main",
            ),
            CallProposal(
                "RequestRecord::store(request:Request)",
                "submit-store",
                "submit-control",
                group_key="submit-main",
            ),
        ),
        sources=sources
        if sources is not None
        else (Source("actor:request", "Request"),),
        durable_entity_names=("RequestRecord",),
    )


def test_complete_witness_is_the_only_acceptance_path() -> None:
    result = validate_behavior_prototype(
        scenario=SCENARIO,
        inventory=INVENTORY,
        drafts=(draft(),),
    )

    assert result.accepted.status is StageStatus.ACCEPTED
    assert len(result.witnesses) == 1
    assert len(result.witnesses[0].calls) == 3
    assert len(result.witnesses[0].bindings) == 3
    assert len(result.bce_model.Collaborations) == 1
    assert [call.stable_id for call in result.bce_model.Collaborations[0].calls] == [
        "UC-SUBMIT::submit-root",
        "UC-SUBMIT::submit-control",
        "UC-SUBMIT::submit-store",
    ]


def test_missing_binding_source_rejects_the_whole_run_without_mutating_input() -> None:
    original = deepcopy(OPERATION_FRAGMENT)

    with pytest.raises(BindingSelectionError, match="no source"):
        validate_behavior_prototype(
            scenario=SCENARIO,
            inventory=INVENTORY,
            drafts=(draft(sources=()),),
        )

    assert original == OPERATION_FRAGMENT


def test_call_candidate_is_rejected_before_checkpoint_when_producer_is_omitted() -> None:
    scenario = {
        "useCases": [
            {
                "id": "UC-LOOKUP",
                "steps": [
                    {"id": "lookup:1"},
                    {"id": "lookup:2"},
                    {"id": "lookup:3"},
                ],
            }
        ]
    }
    inventory = {
        "Classes": [
            {"className": "LookupBoundary", "stereotype": "Boundary"},
            {"className": "LookupControl", "stereotype": "Control"},
            {"className": "Student", "stereotype": "Entity"},
        ],
        "DataTypes": [],
        "Relationships": [],
    }
    payload = {
        "Classes": [
            {
                "className": "LookupBoundary",
                "operations": [
                    {
                        "name": "submit",
                        "parameters": [{"name": "request", "type": "LookupRequest"}],
                        "returnType": "void",
                        "stepRefs": ["lookup:1"],
                    }
                ],
            },
            {
                "className": "LookupControl",
                "operations": [
                    {
                        "name": "coordinate",
                        "parameters": [],
                        "returnType": "void",
                        "stepRefs": ["lookup:2"],
                    },
                    {
                        "name": "verify",
                        "parameters": [
                            {"name": "student", "type": "Student"},
                            {"name": "backupStudent", "type": "Student"},
                        ],
                        "returnType": "boolean",
                        "stepRefs": ["lookup:3"],
                    },
                ],
            },
            {
                "className": "Student",
                "operations": [
                    {
                        "name": "getInfo",
                        "parameters": [{"name": "studentId", "type": "String"}],
                        "returnType": "Student",
                        "stepRefs": ["lookup:2"],
                    },
                    {
                        "name": "getBackupInfo",
                        "parameters": [{"name": "studentId", "type": "String"}],
                        "returnType": "Student",
                        "stepRefs": ["lookup:2"],
                    }
                ],
            },
        ],
        "DataTypes": [
            {
                "name": "LookupRequest",
                "kind": "valueObject",
                "fields": [{"name": "studentId", "type": "String"}],
                "values": [],
            }
        ],
    }
    fragment = validated_operation_fragment(
        payload,
        OperationContext.from_payload(
            "UC-LOOKUP",
            inventory,
            scenario=scenario,
            allowed_step_ids=("lookup:1", "lookup:2", "lookup:3"),
            durable_entity_names=("Student",),
        ),
    )
    catalog = assemble_catalog((fragment,), inventory=inventory, scenario=scenario)
    calls = validated_call_structure(
        catalog.validated,
        scenario=scenario,
        use_case_id="UC-LOOKUP",
        proposals=(
            CallProposal(
                "LookupBoundary::submit(request:LookupRequest)",
                "root",
                group_key="lookup-main",
            ),
            CallProposal(
                "LookupControl::coordinate()",
                "coordinate",
                "root",
                group_key="lookup-main",
            ),
            CallProposal(
                "LookupControl::verify(student:Student,backupStudent:Student)",
                "verify",
                "coordinate",
                group_key="lookup-main",
            ),
        ),
        actor_entries=(
            ActorEntry(
                "lookup-main",
                "Requester",
                "LookupBoundary",
                ("lookup:1", "lookup:2", "lookup:3"),
            ),
        ),
    )

    with pytest.raises(CallValidationError, match="no finite typed source"):
        experiment._validate_call_binding_feasibility(
            calls,
            scenario=scenario,
            catalog=catalog.validated,
        )

    use_case_steps = tuple(
        Step(step_id, "UC-LOOKUP", "", "", index, "main")
        for index, step_id in enumerate(("lookup:1", "lookup:2", "lookup:3"))
    )
    inputs = experiment.UseCaseInputs(
        UseCase("UC-LOOKUP", "Lookup", "Requester", {}, use_case_steps, ()),
        (
            ExecutionGroup(
                "lookup-main",
                "UC-LOOKUP",
                tuple(step.id for step in use_case_steps),
                "lookup:1",
                "Requester",
                ("UC-LOOKUP",),
                tuple(step.id for step in use_case_steps),
            ),
        ),
        tuple(step.id for step in use_case_steps),
        ("LookupBoundary", "LookupControl", "Student"),
        ("Student",),
    )
    raw_plan = experiment.CallChoicePlan(
        calls=[
            experiment.CallChoice(
                callId="coordinate",
                receiverOperationId="LookupControl::coordinate",
                parentCallId=None,
                groupKey="lookup-main",
                guardRefs=[],
                exportResult=False,
            ),
            experiment.CallChoice(
                callId="producer",
                receiverOperationId="Student::getInfo(studentId:String)",
                parentCallId="root",
                groupKey="lookup-main",
                guardRefs=[],
                exportResult=False,
            ),
            experiment.CallChoice(
                callId="backup",
                receiverOperationId="Student::getBackupInfo(studentId:String)",
                parentCallId="root",
                groupKey="lookup-main",
                guardRefs=[],
                exportResult=False,
            ),
            experiment.CallChoice(
                callId="verify",
                receiverOperationId=(
                    "LookupControl::verify(student:Student,backupStudent:Student)"
                ),
                parentCallId="root",
                groupKey="lookup-main",
                guardRefs=[],
                exportResult=False,
            ),
        ]
    )

    compiled = experiment._compile_call_plan(
        raw_plan,
        inputs=inputs,
        catalog=catalog.validated,
    )

    assert [item.call_id for item in compiled.calls] == [
        "auto_root_1",
        "coordinate",
        "producer",
        "backup",
        "verify",
    ]
    assert [item.parent_call_id for item in compiled.calls] == [
        None,
        "auto_root_1",
        "coordinate",
        "coordinate",
        "coordinate",
    ]
    assert compiled.calls[1].receiver_operation_id == "LookupControl::coordinate()"

    compiled_calls = validated_call_structure(
        catalog.validated,
        scenario=scenario,
        use_case_id="UC-LOOKUP",
        proposals=tuple(
            CallProposal(
                item.receiver_operation_id,
                item.call_id,
                item.parent_call_id,
                item.group_key,
            )
            for item in compiled.calls
        ),
        actor_entries=(
            ActorEntry(
                "lookup-main",
                "Requester",
                "LookupBoundary",
                ("lookup:1", "lookup:2", "lookup:3"),
            ),
        ),
    )
    with pytest.raises(BindingSelectionError, match="reuse source"):
        validated_binding_plan(
            catalog.validated,
            compiled_calls,
            scenario=scenario,
            sources=(
                Source("actor:request", "LookupRequest"),
                Source("actor:studentId", "String", kind="derived"),
            ),
            selections={
                ("verify", "student"): "call:producer.return",
                ("verify", "backupStudent"): "call:producer.return",
            },
        )


def test_operation_boundary_rejects_call_fields_instead_of_ignoring_them() -> None:
    combined = {**OPERATION_FRAGMENT, "calls": [{"operationKey": "invented"}]}
    context = OperationContext.from_payload(
        "UC-SUBMIT",
        INVENTORY,
        scenario=SCENARIO,
        allowed_step_ids=("submit:1", "submit:2", "submit:3"),
        durable_entity_names=("RequestRecord",),
    )

    with pytest.raises(OperationValidationError, match="unsupported fields: calls"):
        validated_operation_fragment(combined, context)


def test_invalid_bce_edge_fails_at_call_boundary() -> None:
    invalid = draft()
    invalid = UseCaseDraft(
        **{
            **invalid.__dict__,
            "call_proposals": (
                invalid.call_proposals[0],
                CallProposal(
                    "RequestRecord::store(request:Request)",
                    "submit-store",
                    "submit-root",
                    group_key="submit-main",
                ),
            ),
        }
    )

    with pytest.raises(CallValidationError, match="Boundary may delegate only to Control"):
        validate_behavior_prototype(
            scenario=SCENARIO,
            inventory=INVENTORY,
            drafts=(invalid,),
        )


def test_scenario_and_actor_slice_must_be_complete_before_generation() -> None:
    incomplete = draft()
    incomplete = UseCaseDraft(
        **{
            **incomplete.__dict__,
            "allowed_step_ids": ("submit:1", "submit:2"),
            "actor_entries": (
                ActorEntry(
                    "submit-main",
                    "Requester",
                    "SubmitBoundary",
                    ("submit:1", "submit:2"),
                ),
            ),
        }
    )

    with pytest.raises(ValueError, match="allowed steps do not match"):
        validate_behavior_prototype(
            scenario=SCENARIO,
            inventory=INVENTORY,
            drafts=(incomplete,),
        )


def test_witness_rejects_payload_changed_after_validation() -> None:
    result = validate_behavior_prototype(
        scenario=SCENARIO,
        inventory=INVENTORY,
        drafts=(draft(),),
    )
    calls = result.call_structures[0]
    tampered_payload = deepcopy(calls.payload)
    tampered_payload["calls"][1]["parentCallId"] = None
    tampered = calls.model_copy(update={"payload": tampered_payload})

    with pytest.raises(WitnessValidationError, match="changed after validation"):
        validate_execution_witness(
            result.catalog.validated,
            tampered,
            result.binding_plans[0],
        )


def test_materializer_rechecks_nested_accepted_content() -> None:
    result = validate_behavior_prototype(
        scenario=SCENARIO,
        inventory=INVENTORY,
        drafts=(draft(),),
    )
    tampered = result.accepted.model_copy(deep=True)
    tampered.catalog.payload["Classes"][0]["description"] = "changed later"

    with pytest.raises(MaterializationError, match="integrity"):
        materialize_bce_model(tampered)


def test_catalog_rejects_unvalidated_or_colliding_fragments() -> None:
    with pytest.raises(CatalogValidationError, match="only validated"):
        assemble_catalog(
            [OPERATION_FRAGMENT],  # type: ignore[list-item]
            inventory=INVENTORY,
            scenario=SCENARIO,
        )

    second_scenario = {
        "useCases": [
            {"id": "UC-A", "steps": [{"id": "a:1"}]},
            {"id": "UC-B", "steps": [{"id": "b:1"}]},
        ]
    }
    collision_inventory = {
        "Classes": [
            {
                "className": "SharedControl",
                "stereotype": "Control",
                "fields": [],
            }
        ],
        "DataTypes": [],
        "Relationships": [],
    }
    first = validated_operation_fragment(
        {
            "Classes": [
                {
                    "className": "SharedControl",
                    "operations": [
                        {
                            "name": "execute",
                            "parameters": [],
                            "returnType": "String",
                            "stepRefs": ["a:1"],
                        }
                    ],
                }
            ]
        },
        OperationContext.from_payload(
            "UC-A",
            collision_inventory,
            scenario=second_scenario,
            allowed_step_ids=("a:1",),
        ),
    )
    second = validated_operation_fragment(
        {
            "Classes": [
                {
                    "className": "SharedControl",
                    "operations": [
                        {
                            "name": "execute",
                            "parameters": [],
                            "returnType": "Integer",
                            "stepRefs": ["b:1"],
                        }
                    ],
                }
            ]
        },
        OperationContext.from_payload(
            "UC-B",
            collision_inventory,
            scenario=second_scenario,
            allowed_step_ids=("b:1",),
        ),
    )

    with pytest.raises(CatalogValidationError, match="contract collision"):
        assemble_catalog(
            [first, second],
            inventory=collision_inventory,
            scenario=second_scenario,
        )


def test_boundary_return_is_not_an_internal_parameter_source() -> None:
    fragment = deepcopy(OPERATION_FRAGMENT)
    boundary = fragment["Classes"][0]["operations"][0]
    boundary["parameters"] = []
    boundary["returnType"] = "Request"

    with pytest.raises(
        OperationValidationError,
        match="no finite actor-input or operation-result source for Request",
    ):
        validated_operation_fragment(
            fragment,
            OperationContext.from_payload(
                "UC-SUBMIT",
                INVENTORY,
                scenario=SCENARIO,
                allowed_step_ids=("submit:1", "submit:2", "submit:3"),
                durable_entity_names=("RequestRecord",),
            ),
        )


def test_binding_candidates_enforce_time_scope_and_synchronous_parent_rule() -> None:
    resolver = BindingResolver()
    parent_result = Source(
        "call:root.return",
        "Result",
        kind="call_result",
        scope="group-a",
        producer_call_id="root",
        exported=True,
    )
    context = {
        "call_order": {"root": 0, "child": 1, "other-root": 2},
        "parent_by_call": {
            "root": None,
            "child": "root",
            "other-root": None,
        },
    }

    child_choices = resolver.candidates(
        {"name": "result", "type": "Result"},
        (parent_result,),
        call_id="child",
        group_key="group-a",
        guard_refs=(),
        **context,
    )
    other_group_choices = resolver.candidates(
        {"name": "result", "type": "Result"},
        (parent_result,),
        call_id="other-root",
        group_key="group-b",
        guard_refs=(),
        **context,
    )

    assert child_choices == ()
    assert [item.source.source_ref for item in other_group_choices] == [
        "call:root.return"
    ]


def test_call_result_sources_are_derived_not_supplied_by_a_proposer() -> None:
    result = validate_behavior_prototype(
        scenario=SCENARIO,
        inventory=INVENTORY,
        drafts=(draft(),),
    )

    with pytest.raises(BindingSelectionError, match="derived"):
        validated_binding_plan(
            result.catalog.validated,
            result.call_structures[0],
            scenario=SCENARIO,
            sources=(
                Source("actor:request", "Request"),
                Source(
                    "call:submit-root.return",
                    "Result",
                    kind="call_result",
                    scope="submit-main",
                    producer_call_id="submit-root",
                ),
            ),
        )


def test_one_explicit_plan_resolves_all_ambiguous_finite_bindings() -> None:
    result = validate_behavior_prototype(
        scenario=SCENARIO,
        inventory=INVENTORY,
        drafts=(draft(),),
    )
    sources = (
        Source("actor:primary", "Request"),
        Source("precondition:fallback", "Request", kind="precondition"),
    )
    targets = binding_candidate_sets(
        result.catalog.validated,
        result.call_structures[0],
        scenario=SCENARIO,
        sources=sources,
    )
    selections = {
        (target.call_id, target.parameter_name): "actor:primary"
        for target in targets
    }

    plan = validated_binding_plan(
        result.catalog.validated,
        result.call_structures[0],
        scenario=SCENARIO,
        sources=sources,
        selections=selections,
    )

    assert {item["sourceRef"] for item in plan.payload["bindings"]} == {
        "actor:primary"
    }
