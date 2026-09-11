from __future__ import annotations

from copy import deepcopy

import pytest

from app.design.services.class_diagram.scenario import Step, UseCase
from app.design.services.executable_behavior import (
    ActorEntry,
    CallProposal,
    LocalSemanticContract,
    LocalSemanticValidationError,
    ObligationKind,
    OperationContext,
    OperationResponsibility,
    OperationSemantics,
    ReviewDecision,
    ScenarioObligation,
    SemanticReviewAdjudicationProposal,
    SemanticReviewProposal,
    SemanticReviewStage,
    StateEffect,
    StateEffectOperation,
    assemble_sealed_catalog,
    local_contract_escapes,
    local_review_subject,
    seal_local_operation_fragment,
    validate_effect_call_links,
    validated_call_structure,
    validated_local_semantic_contract,
    validated_operation_fragment,
    validated_review_adjudication,
    validated_semantic_review,
)
from scripts import run_executable_behavior_llm_experiment as experiment

SCENARIO = {
    "useCases": [
        {
            "id": "UC-SUBMIT",
            "steps": [
                {"id": "submit:1"},
                {"id": "submit:2"},
                {"id": "submit:3"},
            ],
        }
    ]
}

INVENTORY = {
    "Classes": [
        {
            "className": "SubmitBoundary",
            "stereotype": "Boundary",
            "fields": [],
        },
        {
            "className": "SubmitControl",
            "stereotype": "Control",
            "fields": [],
        },
        {
            "className": "RequestRecord",
            "stereotype": "Entity",
            "fields": ["id : UUID", "status : String"],
        },
        {
            "className": "Course",
            "stereotype": "Entity",
            "fields": ["capacity : Integer"],
        },
    ],
    "DataTypes": [],
    "Relationships": [],
}

FRAGMENT = {
    "Classes": [
        {
            "className": "SubmitBoundary",
            "operations": [
                {
                    "name": "submit",
                    "parameters": [],
                    "returnType": "void",
                    "stepRefs": ["submit:1"],
                }
            ],
        },
        {
            "className": "SubmitControl",
            "operations": [
                {
                    "name": "process",
                    "parameters": [],
                    "returnType": "void",
                    "stepRefs": ["submit:2"],
                }
            ],
        },
        {
            "className": "RequestRecord",
            "operations": [
                {
                    "name": "store",
                    "parameters": [],
                    "returnType": "void",
                    "stepRefs": ["submit:3"],
                }
            ],
        },
    ],
    "DataTypes": [],
}


def _fragment(payload=FRAGMENT):
    return validated_operation_fragment(
        payload,
        OperationContext.from_payload(
            "UC-SUBMIT",
            INVENTORY,
            scenario=SCENARIO,
            allowed_step_ids=("submit:1", "submit:2", "submit:3"),
            durable_entity_names=("RequestRecord",),
        ),
    )


def _contract(
    *, state_ref: str = "RequestRecord", execution_owner: str = "RequestRecord"
) -> LocalSemanticContract:
    return LocalSemanticContract(
        useCaseId="UC-SUBMIT",
        obligations=(
            ScenarioObligation(
                obligationId="obligation:submit-input",
                sourceRefs=("submit:1",),
                kind=ObligationKind.INTERACTION,
                expected="Accept the submission request.",
            ),
            ScenarioObligation(
                obligationId="obligation:process",
                sourceRefs=("submit:2",),
                kind=ObligationKind.OUTCOME,
                expected="Coordinate submission processing.",
            ),
            ScenarioObligation(
                obligationId="obligation:persist",
                sourceRefs=("submit:3",),
                kind=ObligationKind.STATE_TRANSITION,
                expected="Create the durable request record.",
                stateRef=state_ref,
            ),
        ),
        operations=(
            OperationSemantics(
                operationRef="SubmitBoundary::submit()",
                responsibility=OperationResponsibility.COORDINATE,
                realizes=("obligation:submit-input",),
                delegates=("SubmitControl::process()",),
                outcomes=("Submission entered the application.",),
            ),
            OperationSemantics(
                operationRef="SubmitControl::process()",
                responsibility=OperationResponsibility.COORDINATE,
                realizes=("obligation:process",),
                delegates=("RequestRecord::store()",),
                outcomes=("Persistence is delegated to the Entity.",),
            ),
            OperationSemantics(
                operationRef="RequestRecord::store()",
                responsibility=OperationResponsibility.MUTATE,
                realizes=("obligation:persist",),
                effects=(
                    StateEffect(
                        effectId="effect:create-request",
                        stateRef=state_ref,
                        operation=StateEffectOperation.CREATE,
                        executionOwner=execution_owner,
                    ),
                ),
                outcomes=("The request record exists.",),
            ),
        ),
    )


def _pass_review(fragment, semantics):
    subject = local_review_subject(fragment, semantics)
    raw = validated_semantic_review(
        SemanticReviewProposal(decision=ReviewDecision.PASS, findings=()),
        stage=SemanticReviewStage.BEHAVIOR,
        subject=subject,
        allowed_owner_ids=("UC-SUBMIT",),
        evidence_index=(),
    )
    return validated_review_adjudication(
        SemanticReviewAdjudicationProposal(findings=()),
        review=raw,
        subject=subject,
        evidence_index=(),
    )


def _seal(payload=FRAGMENT):
    fragment = _fragment(payload)
    semantics = validated_local_semantic_contract(
        _contract(), fragment=fragment, scenario=SCENARIO, inventory=INVENTORY
    )
    return seal_local_operation_fragment(
        fragment, semantics, _pass_review(fragment, semantics)
    )


def _split_inputs() -> experiment.UseCaseInputs:
    steps = (
        Step("submit:1", "UC-SUBMIT", "Student", "Submit request", 0, "main"),
        Step("submit:2", "UC-SUBMIT", "System", "Process request", 1, "main"),
        Step("submit:3", "UC-SUBMIT", "System", "Store request", 2, "main"),
    )
    return experiment.UseCaseInputs(
        use_case=UseCase(
            id="UC-SUBMIT",
            name="Submit",
            primary_actor="Student",
            specification={"preconditions": []},
            steps=steps,
            precondition_refs=(),
        ),
        groups=(),
        allowed_steps=tuple(step.id for step in steps),
        allowed_owners=("SubmitBoundary", "SubmitControl", "RequestRecord"),
        durable_entities=("RequestRecord",),
    )


def test_split_obligation_plan_is_exact_and_locally_closed() -> None:
    inputs = _split_inputs()
    sources = experiment._scenario_source_descriptors(inputs)
    entries = experiment._fragment_operation_entries(_fragment(), INVENTORY)
    plan = experiment.ScenarioObligationPlan(
        decisions=[
            experiment.ScenarioObligationDecision(
                sourceRef="submit:1",
                kind=ObligationKind.INTERACTION,
                stateRefs=[],
            ),
            experiment.ScenarioObligationDecision(
                sourceRef="submit:2",
                kind=ObligationKind.OUTCOME,
                stateRefs=[],
            ),
            experiment.ScenarioObligationDecision(
                sourceRef="submit:3",
                kind=ObligationKind.STATE_TRANSITION,
                stateRefs=["RequestRecord"],
            ),
        ]
    )

    obligations, findings = experiment._obligations_from_plan(
        plan,
        sources=sources,
        available_states=experiment._available_state_refs(INVENTORY),
        operation_entries=entries,
    )

    assert findings == []
    assert obligations is not None
    assert [item.obligation_id for item in obligations] == ["O1", "O2", "O3"]
    assert [item.expected for item in obligations] == [
        "Submit request",
        "Process request",
        "Store request",
    ]


def test_split_obligation_plan_expands_composite_transition_atomically() -> None:
    inputs = _split_inputs()
    sources = experiment._scenario_source_descriptors(inputs)
    sources[-1]["sentence"] = "Store the request and set its status."
    plan = experiment.ScenarioObligationPlan(
        decisions=[
            experiment.ScenarioObligationDecision(
                sourceRef="submit:1",
                kind=ObligationKind.INTERACTION,
                stateRefs=[],
            ),
            experiment.ScenarioObligationDecision(
                sourceRef="submit:2",
                kind=ObligationKind.OUTCOME,
                stateRefs=[],
            ),
            experiment.ScenarioObligationDecision(
                sourceRef="submit:3",
                kind=ObligationKind.STATE_TRANSITION,
                stateRefs=["RequestRecord", "RequestRecord.status"],
            ),
        ]
    )

    obligations, findings = experiment._obligations_from_plan(
        plan,
        sources=sources,
        available_states=experiment._available_state_refs(INVENTORY),
        operation_entries=experiment._fragment_operation_entries(
            _fragment(), INVENTORY
        ),
    )

    assert findings == []
    assert obligations is not None
    assert [item.obligation_id for item in obligations] == [
        "O1",
        "O2",
        "O3.S1",
        "O3.S2",
    ]
    assert [item.state_ref for item in obligations[-2:]] == [
        "RequestRecord",
        "RequestRecord.status",
    ]


def test_external_precondition_is_representable_without_inventing_entity_state() -> None:
    sources = [
        {
            "sourceRef": "UC10:precondition:1",
            "subject": "precondition",
            "sentence": "Academic Administrator is authenticated",
            "branch": "precondition",
            "condition": "Academic Administrator is authenticated",
        }
    ]
    plan = experiment.ScenarioObligationPlan(
        decisions=[
            experiment.ScenarioObligationDecision(
                sourceRef="UC10:precondition:1",
                kind=ObligationKind.PRECONDITION,
                stateRefs=[],
            )
        ]
    )

    obligations, findings = experiment._obligations_from_plan(
        plan,
        sources=sources,
        available_states=experiment._available_state_refs(INVENTORY),
        operation_entries=experiment._fragment_operation_entries(
            _fragment(), INVENTORY
        ),
    )

    assert findings == []
    assert obligations is not None
    assert obligations[0].kind is ObligationKind.PRECONDITION
    assert obligations[0].state_ref is None


def test_source_contract_rejects_state_from_unrelated_entity_operation() -> None:
    inputs = _split_inputs()
    plan = experiment.ScenarioObligationPlan(
        decisions=[
            experiment.ScenarioObligationDecision(
                sourceRef="submit:1",
                kind=ObligationKind.OBSERVATION,
                stateRefs=["Course.capacity"],
            ),
            experiment.ScenarioObligationDecision(
                sourceRef="submit:2",
                kind=ObligationKind.OUTCOME,
                stateRefs=[],
            ),
            experiment.ScenarioObligationDecision(
                sourceRef="submit:3",
                kind=ObligationKind.STATE_TRANSITION,
                stateRefs=["RequestRecord"],
            ),
        ]
    )

    obligations, findings = experiment._obligations_from_plan(
        plan,
        sources=experiment._scenario_source_descriptors(inputs),
        available_states=experiment._available_state_refs(INVENTORY),
        operation_entries=experiment._fragment_operation_entries(
            _fragment(), INVENTORY
        ),
    )

    assert obligations is None
    assert any("allowedStateRefs" in finding for finding in findings)


def test_source_contract_narrows_state_choices_from_operation_and_scenario() -> None:
    inputs = _split_inputs()
    contracts = experiment._obligation_source_contracts(
        sources=experiment._scenario_source_descriptors(inputs),
        available_states=experiment._available_state_refs(INVENTORY),
        operation_entries=experiment._fragment_operation_entries(
            _fragment(), INVENTORY
        ),
    )

    transition = next(
        item for item in contracts if item["sourceRef"] == "submit:3"
    )
    unrelated = next(
        item for item in contracts if item["sourceRef"] == "submit:1"
    )
    assert transition["allowedStateRefs"] == ["RequestRecord"]
    assert "Course.capacity" not in unrelated["allowedStateRefs"]


def test_source_contract_uses_operation_concepts_not_incidental_sentence_words() -> None:
    contracts = experiment._obligation_source_contracts(
        sources=[
            {
                "sourceRef": "UC5:main:3",
                "sentence": (
                    "Remove the registration from the student's schedule and update "
                    "enrollment capacity"
                ),
                "branch": "main",
                "condition": "",
            }
        ],
        available_states=[
            {"stateRef": "CourseOffering", "executionOwner": "CourseOffering"},
            {
                "stateRef": "CourseOffering.capacity",
                "executionOwner": "CourseOffering",
            },
            {
                "stateRef": "CourseOffering.meetingSchedule",
                "executionOwner": "CourseOffering",
            },
            {
                "stateRef": "CourseOffering.remainingSeats",
                "executionOwner": "CourseOffering",
            },
            {
                "stateRef": "CourseOffering.schedule",
                "executionOwner": "CourseOffering",
            },
            {
                "stateRef": "CourseOffering.students",
                "executionOwner": "CourseOffering",
            },
        ],
        operation_entries=[
            {
                "operationRef": "CourseOffering::adjustSeats(offeringId:String)",
                "owner": "CourseOffering",
                "ownerRole": "Entity",
                "stepRefs": ["UC5:main:3"],
            }
        ],
    )

    assert contracts[0]["allowedStateRefs"] == ["CourseOffering.capacity"]


def test_update_effect_needs_no_fabricated_replacement_value() -> None:
    effect = StateEffect(
        effectId="E-update",
        stateRef="RequestRecord.status",
        operation=StateEffectOperation.UPDATE,
        executionOwner="RequestRecord",
    )

    assert effect.value is None
    assert effect.delta is None


def test_split_entity_plan_assembles_fixed_coordinate_and_effect_ownership() -> None:
    inputs = _split_inputs()
    entries = experiment._fragment_operation_entries(_fragment(), INVENTORY)
    obligations = (
        ScenarioObligation(
            obligationId="O1",
            sourceRefs=("submit:1",),
            kind=ObligationKind.INTERACTION,
            expected="Accept the request.",
        ),
        ScenarioObligation(
            obligationId="O2",
            sourceRefs=("submit:2",),
            kind=ObligationKind.OUTCOME,
            expected="Coordinate processing.",
        ),
        ScenarioObligation(
            obligationId="O3",
            sourceRefs=("submit:3",),
            kind=ObligationKind.STATE_TRANSITION,
            expected="Create the record.",
            stateRef="RequestRecord",
        ),
    )
    plan = experiment.EntityOperationSemanticPlan(
        operations=[
            experiment.EntityOperationSemanticDecision(
                operationRef="RequestRecord::store()",
                effects=[
                    experiment.StateEffectDecision(
                        stateRef="RequestRecord",
                        operation=StateEffectOperation.CREATE,
                    ),
                    experiment.StateEffectDecision(
                        stateRef="RequestRecord.status",
                        operation=StateEffectOperation.SET,
                        value="stored",
                    )
                ],
            )
        ]
    )

    contract = experiment._assemble_local_semantic_contract(
        inputs=inputs,
        obligations=obligations,
        operation_entries=entries,
        plan=plan,
    )
    validated = validated_local_semantic_contract(
        contract,
        fragment=_fragment(),
        scenario=SCENARIO,
        inventory=INVENTORY,
    )

    semantics = {
        item.operation_ref: item for item in validated.contract.operations
    }
    assert semantics["SubmitBoundary::submit()"].responsibility is (
        OperationResponsibility.COORDINATE
    )
    assert semantics["SubmitControl::process()"].responsibility is (
        OperationResponsibility.COORDINATE
    )
    effect = semantics["RequestRecord::store()"].effects[0]
    assert effect.effect_id == "E1"
    assert effect.execution_owner == "RequestRecord"
    assert any(
        item.obligation_id == "O3.A1"
        and item.state_ref == "RequestRecord.status"
        for item in validated.contract.obligations
    )


def test_entity_participation_does_not_imply_transition_ownership() -> None:
    inputs = _split_inputs()
    obligations = (
        ScenarioObligation(
            obligationId="O3",
            sourceRefs=("submit:3",),
            kind=ObligationKind.STATE_TRANSITION,
            expected="Create the request.",
            stateRef="RequestRecord",
        ),
    )
    entries = [
        {
            "operationRef": "RequestRecord::store()",
            "owner": "RequestRecord",
            "ownerRole": "Entity",
            "stepRefs": ["submit:3"],
        },
        {
            "operationRef": "Course::find()",
            "owner": "Course",
            "ownerRole": "Entity",
            "stepRefs": ["submit:3"],
        },
    ]
    plan = experiment.EntityOperationSemanticPlan(
        operations=[
            experiment.EntityOperationSemanticDecision(
                operationRef="RequestRecord::store()",
                effects=[
                    experiment.StateEffectDecision(
                        stateRef="RequestRecord",
                        operation=StateEffectOperation.CREATE,
                    )
                ],
            ),
            experiment.EntityOperationSemanticDecision(
                operationRef="Course::find()",
                effects=[],
            ),
        ]
    )

    contract = experiment._assemble_local_semantic_contract(
        inputs=inputs,
        obligations=obligations,
        operation_entries=entries,
        plan=plan,
    )
    semantics = {item.operation_ref: item for item in contract.operations}

    assert semantics["RequestRecord::store()"].responsibility is (
        OperationResponsibility.MUTATE
    )
    assert semantics["Course::find()"].responsibility is (
        OperationResponsibility.QUERY
    )
    assert semantics["Course::find()"].observes == ("Course",)


def test_valid_local_effect_contract_can_be_sealed() -> None:
    seal = _seal()

    assert seal.status.value == "SEALED_UNDER_CONTRACT"
    entity_semantics = seal.semantics.contract.operations[-1]
    assert entity_semantics.reads == ()
    assert entity_semantics.writes == ("RequestRecord",)


def test_transition_requires_exact_entity_owned_effect() -> None:
    fragment = _fragment()

    with pytest.raises(
        LocalSemanticValidationError,
        match=r"does not own Course\.capacity",
    ):
        validated_local_semantic_contract(
            _contract(state_ref="Course.capacity"),
            fragment=fragment,
            scenario=SCENARIO,
            inventory=INVENTORY,
        )

    with pytest.raises(LocalSemanticValidationError, match="not an Entity"):
        validated_local_semantic_contract(
            _contract(execution_owner="SubmitControl"),
            fragment=fragment,
            scenario=SCENARIO,
            inventory=INVENTORY,
        )

    with pytest.raises(LocalSemanticValidationError, match="does not own"):
        validated_local_semantic_contract(
            _contract(execution_owner="Course"),
            fragment=fragment,
            scenario=SCENARIO,
            inventory=INVENTORY,
        )


def test_coordinate_operation_cannot_claim_a_direct_state_effect() -> None:
    with pytest.raises(ValueError, match="COORDINATE"):
        OperationSemantics(
            operationRef="SubmitControl::process()",
            responsibility=OperationResponsibility.COORDINATE,
            realizes=("obligation:process",),
            effects=(
                StateEffect(
                    effectId="effect:invalid-control-write",
                    stateRef="RequestRecord.status",
                    operation=StateEffectOperation.SET,
                    executionOwner="SubmitControl",
                    value="stored",
                ),
            ),
            outcomes=("Invalid direct write.",),
        )


def test_global_catalog_requires_seals_and_retains_them_in_provenance() -> None:
    seal = _seal()
    result = assemble_sealed_catalog(
        (seal,), inventory=INVENTORY, scenario=SCENARIO
    )

    assert "seal:UC-SUBMIT" in result.validated.provenance.input_digests
    with pytest.raises(LocalSemanticValidationError, match="only LocallySealed"):
        assemble_sealed_catalog(
            (_fragment(),),  # type: ignore[arg-type]
            inventory=INVENTORY,
            scenario=SCENARIO,
        )


def test_integration_loss_is_reported_as_local_contract_escape() -> None:
    seal = _seal()
    result = assemble_sealed_catalog(
        (seal,), inventory=INVENTORY, scenario=SCENARIO
    )
    payload = deepcopy(result.validated.payload)
    for class_item in payload["Classes"]:
        if class_item["className"] == "RequestRecord":
            class_item["operations"] = []
    changed = result.validated.model_copy(update={"payload": payload})

    escapes = local_contract_escapes((seal,), changed)

    assert len(escapes) == 1
    assert escapes[0].target_ref == "RequestRecord::store()"
    assert escapes[0].reason.startswith("LOCAL_CONTRACT_ESCAPE")


def test_explicit_effects_and_delegations_must_appear_in_calls() -> None:
    seal = _seal()
    catalog = assemble_sealed_catalog(
        (seal,), inventory=INVENTORY, scenario=SCENARIO
    )
    calls = validated_call_structure(
        catalog.validated,
        scenario=SCENARIO,
        use_case_id="UC-SUBMIT",
        proposals=(
            CallProposal(
                "SubmitBoundary::submit()", "root", group_key="submit-main"
            ),
            CallProposal(
                "SubmitControl::process()",
                "control",
                "root",
                group_key="submit-main",
            ),
            CallProposal(
                "RequestRecord::store()",
                "store",
                "control",
                group_key="submit-main",
            ),
        ),
        actor_entries=(
            ActorEntry(
                "submit-main",
                "Requester",
                "SubmitBoundary",
                ("submit:1", "submit:2", "submit:3"),
            ),
        ),
    )

    validate_effect_call_links(seal, calls)


def test_step_coverage_alone_does_not_prove_declared_effect_execution() -> None:
    payload = deepcopy(FRAGMENT)
    payload["Classes"][1]["operations"][0]["stepRefs"].append("submit:3")
    seal = _seal(payload)
    catalog = assemble_sealed_catalog(
        (seal,), inventory=INVENTORY, scenario=SCENARIO
    )
    calls = validated_call_structure(
        catalog.validated,
        scenario=SCENARIO,
        use_case_id="UC-SUBMIT",
        proposals=(
            CallProposal(
                "SubmitBoundary::submit()", "root", group_key="submit-main"
            ),
            CallProposal(
                "SubmitControl::process()",
                "control",
                "root",
                group_key="submit-main",
            ),
        ),
        actor_entries=(
            ActorEntry(
                "submit-main",
                "Requester",
                "SubmitBoundary",
                ("submit:1", "submit:2", "submit:3"),
            ),
        ),
    )

    required = experiment._required_effect_operations(seal)
    assert required == ("RequestRecord::store()",)
    with pytest.raises(ValueError, match="required state-effect operations"):
        experiment._validate_required_call_operations(
            calls,
            required_operation_refs=required,
        )
    with pytest.raises(LocalSemanticValidationError, match="effect operation"):
        validate_effect_call_links(seal, calls)
