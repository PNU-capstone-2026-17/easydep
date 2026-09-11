from __future__ import annotations

import pytest

from app.design.services.executable_behavior import (
    AdjudicatedSemanticReview,
    FindingLedgerSnapshot,
    FindingLedgerStatus,
    ReviewCategory,
    ReviewDecision,
    ReviewEvidenceKind,
    ReviewEvidenceRecord,
    ReviewEvidenceRef,
    ReviewFindingDisposition,
    ReviewOwnerStage,
    ReviewSeverity,
    SemanticReviewAdjudicationProposal,
    SemanticReviewFinding,
    SemanticReviewProposal,
    SemanticReviewStage,
    ValidatedSemanticReview,
    advance_finding_ledger,
    blocking_finding_digest,
    semantic_finding_id,
    validated_review_adjudication,
    validated_semantic_review,
)
from app.design.services.executable_behavior.reviews import (
    BehaviorSemanticReviewProposal,
    FindingAdjudication,
    InventorySemanticReviewProposal,
)
from scripts import run_executable_behavior_llm_experiment as experiment


def _ref(kind: ReviewEvidenceKind, reference: str) -> ReviewEvidenceRef:
    return ReviewEvidenceRef(kind=kind, ref=reference)


def _record(
    kind: ReviewEvidenceKind,
    reference: str,
    *owners: str,
) -> ReviewEvidenceRecord:
    return ReviewEvidenceRecord(kind=kind, ref=reference, ownerIds=owners)


def _inventory_index(*owners: str) -> tuple[ReviewEvidenceRecord, ...]:
    first = owners[0]
    last = owners[-1]
    return (
        _record(ReviewEvidenceKind.CLASS, "class:Instructor", first),
        _record(ReviewEvidenceKind.CLASS, "class:Professor", last),
        _record(ReviewEvidenceKind.CLASS, "class:Registration", last),
        _record(
            ReviewEvidenceKind.RELATIONSHIP,
            "relationship:Registration|Course",
            last,
        ),
    )


def _behavior_index(owner: str = "UC1") -> tuple[ReviewEvidenceRecord, ...]:
    return (
        _record(
            ReviewEvidenceKind.OPERATION,
            "Registration::drop(registrationId:RegistrationId)",
            owner,
        ),
        _record(ReviewEvidenceKind.STEP, f"{owner}:main:3", owner),
    )


def _finding(
    *,
    category: ReviewCategory = ReviewCategory.SEMANTIC_DUPLICATE,
    severity: ReviewSeverity = ReviewSeverity.HIGH,
    owner_stage: ReviewOwnerStage = ReviewOwnerStage.INVENTORY_FRAGMENT,
    owner_ids: tuple[str, ...] = ("UC1", "UC2"),
    predicate_key: str = "SAME_DURABLE_CONCEPT",
) -> SemanticReviewFinding:
    if category is ReviewCategory.SEMANTIC_DUPLICATE:
        targets = (
            _ref(ReviewEvidenceKind.CLASS, "class:Instructor"),
            _ref(ReviewEvidenceKind.CLASS, "class:Professor"),
        )
    elif category is ReviewCategory.RELATIONSHIP_SEMANTICS:
        targets = (
            _ref(
                ReviewEvidenceKind.RELATIONSHIP,
                "relationship:Registration|Course",
            ),
        )
    elif category in {
        ReviewCategory.BCE_OWNER,
        ReviewCategory.SEMANTIC_DUPLICATE_OPERATION,
        ReviewCategory.MIXED_RESPONSIBILITY,
            ReviewCategory.PARAMETER_SEMANTICS,
            ReviewCategory.ENTITY_STATE_OWNERSHIP,
            ReviewCategory.SHARED_CONTRACT_REGRESSION,
        }:
        targets = (
            _ref(
                ReviewEvidenceKind.OPERATION,
                "Registration::drop(registrationId:RegistrationId)",
            ),
        )
    elif category is ReviewCategory.SCENARIO_PATH_OMISSION:
        targets = (_ref(ReviewEvidenceKind.STEP, f"{owner_ids[0]}:main:3"),)
    else:
        class_reference = (
            "class:Registration"
            if owner_ids == ("class:Registration",)
            else "class:Professor"
            if owner_ids == ("UC2",)
            else "class:Instructor"
        )
        targets = (_ref(ReviewEvidenceKind.CLASS, class_reference),)
    return SemanticReviewFinding(
        ruleId="DOMAIN.SEMANTIC.IDENTITY",
        predicateKey=predicate_key,
        category=category,
        severity=severity,
        ownerStage=owner_stage,
        ownerIds=owner_ids,
        targetRefs=targets,
        evidence=targets,
        expected="One durable concept has one canonical representation.",
        observed="The same durable concept has two representations.",
        message="The two names describe the same durable concept.",
    )


def _adjudicate(
    review: ValidatedSemanticReview,
    *,
    subject: object,
    evidence_index: tuple[ReviewEvidenceRecord, ...],
    disposition: ReviewFindingDisposition = ReviewFindingDisposition.CONFIRMED,
) -> AdjudicatedSemanticReview:
    return validated_review_adjudication(
        SemanticReviewAdjudicationProposal(
            findings=tuple(
                FindingAdjudication(
                    findingId=semantic_finding_id(finding),
                    disposition=disposition,
                    evidence=(finding.evidence[0],),
                    rationale="The exact indexed evidence decides the predicate.",
                )
                for finding in review.reported_high_findings
            )
        ),
        review=review,
        subject=subject,
        evidence_index=evidence_index,
    )


def test_inventory_fragment_prompt_reuses_names_by_domain_identity() -> None:
    prompt = experiment._INVENTORY_FRAGMENT_PROMPT

    assert "same canonical name for the same real-world concept" in prompt
    assert "merge stage reconciles compatible per-use-case structural views" in prompt
    assert "full stereotype, fields, identifiers" not in prompt


def test_local_duplicate_review_rejects_cross_bce_role_targets() -> None:
    control_ref = "ExportScheduleControl::assembleScheduleData(studentId:String)"
    entity_ref = "Schedule::getSchedule(studentId:String)"
    finding = _finding(
        category=ReviewCategory.SEMANTIC_DUPLICATE_OPERATION,
        owner_stage=ReviewOwnerStage.OPERATION_FRAGMENT,
        owner_ids=("UC6",),
    ).model_copy(
        update={
            "target_refs": (
                _ref(ReviewEvidenceKind.OPERATION, control_ref),
                _ref(ReviewEvidenceKind.OPERATION, entity_ref),
            ),
            "evidence": (
                _ref(ReviewEvidenceKind.OPERATION, control_ref),
                _ref(ReviewEvidenceKind.OPERATION, entity_ref),
            ),
        }
    )
    proposal = SemanticReviewProposal(
        decision=ReviewDecision.REVISE,
        findings=(finding,),
    )
    subject = {
        "reviewRubricVersion": "local-behavior-v4-bce-role-duplicate",
        "ownerRoles": {
            "ExportScheduleControl": "Control",
            "Schedule": "Entity",
        },
    }

    with pytest.raises(ValueError, match="same BCE role"):
        experiment._validate_local_review_contract(proposal, subject=subject)


def test_local_duplicate_review_rejects_different_owners_in_same_bce_role() -> None:
    first_ref = "ExportScheduleControl::assembleScheduleData(studentId:String)"
    second_ref = "ScheduleQueryControl::loadSchedule(studentId:String)"
    finding = _finding(
        category=ReviewCategory.SEMANTIC_DUPLICATE_OPERATION,
        owner_stage=ReviewOwnerStage.OPERATION_FRAGMENT,
        owner_ids=("UC6",),
    ).model_copy(
        update={
            "target_refs": (
                _ref(ReviewEvidenceKind.OPERATION, first_ref),
                _ref(ReviewEvidenceKind.OPERATION, second_ref),
            ),
            "evidence": (
                _ref(ReviewEvidenceKind.OPERATION, first_ref),
                _ref(ReviewEvidenceKind.OPERATION, second_ref),
            ),
        }
    )
    proposal = SemanticReviewProposal(
        decision=ReviewDecision.REVISE,
        findings=(finding,),
    )
    subject = {
        "reviewRubricVersion": "local-behavior-v4-bce-role-duplicate",
        "ownerRoles": {
            "ExportScheduleControl": "Control",
            "ScheduleQueryControl": "Control",
        },
    }

    with pytest.raises(ValueError, match="same class owner"):
        experiment._validate_local_review_contract(proposal, subject=subject)


def test_local_duplicate_review_accepts_two_operations_on_same_owner() -> None:
    first_ref = "Schedule::findByStudent(studentId:String)"
    second_ref = "Schedule::loadForStudent(studentId:String)"
    finding = _finding(
        category=ReviewCategory.SEMANTIC_DUPLICATE_OPERATION,
        owner_stage=ReviewOwnerStage.OPERATION_FRAGMENT,
        owner_ids=("UC6",),
    ).model_copy(
        update={
            "target_refs": (
                _ref(ReviewEvidenceKind.OPERATION, first_ref),
                _ref(ReviewEvidenceKind.OPERATION, second_ref),
            ),
            "evidence": (
                _ref(ReviewEvidenceKind.OPERATION, first_ref),
                _ref(ReviewEvidenceKind.OPERATION, second_ref),
            ),
        }
    )
    proposal = SemanticReviewProposal(
        decision=ReviewDecision.REVISE,
        findings=(finding,),
    )
    subject = {
        "reviewRubricVersion": "local-behavior-v4-bce-role-duplicate",
        "ownerRoles": {"Schedule": "Entity"},
    }

    experiment._validate_local_review_contract(proposal, subject=subject)


def test_resolved_inventory_defect_must_target_resolution_owner() -> None:
    class_ref = _ref(ReviewEvidenceKind.CLASS, "class:CourseOffering")
    finding = _finding(
        category=ReviewCategory.ENTITY_LIFECYCLE,
        owner_stage=ReviewOwnerStage.INVENTORY_FRAGMENT,
        owner_ids=("UC1",),
    ).model_copy(
        update={"target_refs": (class_ref,), "evidence": (class_ref,)}
    )
    proposal = SemanticReviewProposal(
        decision=ReviewDecision.REVISE,
        findings=(finding,),
    )

    with pytest.raises(ValueError, match="inventoryResolution owner"):
        experiment._validate_review_owner_contract(
            proposal,
            allowed_owner_groups={
                "inventoryFragment": ["UC1"],
                "inventoryResolution": ["class:CourseOffering"],
            },
            evidence_index=(
                _record(
                    ReviewEvidenceKind.CLASS,
                    "class:CourseOffering",
                    "UC1",
                    "class:CourseOffering",
                ),
            ),
        )


def test_resolved_inventory_owner_is_routed_deterministically() -> None:
    relationship_ref = _ref(
        ReviewEvidenceKind.RELATIONSHIP,
        "relationship:Registration|Course",
    )
    finding = _finding(
        category=ReviewCategory.RELATIONSHIP_SEMANTICS,
        owner_stage=ReviewOwnerStage.INVENTORY_FRAGMENT,
        owner_ids=("UC1",),
    ).model_copy(
        update={"target_refs": (relationship_ref,), "evidence": (relationship_ref,)}
    )
    proposal = InventorySemanticReviewProposal.model_validate(
        {
            "decision": ReviewDecision.REVISE,
            "findings": [finding.model_dump(by_alias=True)],
        }
    )
    evidence_index = (
        _record(
            ReviewEvidenceKind.RELATIONSHIP,
            "relationship:Registration|Course",
            "UC1",
            "relationship:Registration|Course",
        ),
    )

    normalized = InventorySemanticReviewProposal.model_validate(
        experiment._normalize_review_owner_routing(
            proposal,
            allowed_owner_groups={
                "inventoryFragment": ["UC1"],
                "inventoryResolution": ["relationship:Registration|Course"],
            },
            evidence_index=evidence_index,
        )
    )

    assert normalized.findings[0].owner_stage is ReviewOwnerStage.INVENTORY_RESOLUTION
    assert normalized.findings[0].owner_ids == (
        "relationship:Registration|Course",
    )


def test_control_overlap_cannot_be_blocking_inventory_duplicate() -> None:
    targets = (
        _ref(ReviewEvidenceKind.CLASS, "class:WaitlistControl"),
        _ref(ReviewEvidenceKind.CLASS, "class:ManageWaitlistControl"),
    )
    finding = _finding(
        category=ReviewCategory.SEMANTIC_DUPLICATE,
        owner_stage=ReviewOwnerStage.INVENTORY_FRAGMENT,
        owner_ids=("UC8",),
    ).model_copy(update={"target_refs": targets, "evidence": targets})
    proposal = SemanticReviewProposal(
        decision=ReviewDecision.REVISE,
        findings=(finding,),
    )
    subject = {
        "inventory": {
            "Classes": [
                {"className": "WaitlistControl", "stereotype": "Control"},
                {
                    "className": "ManageWaitlistControl",
                    "stereotype": "Control",
                },
            ]
        }
    }

    with pytest.raises(ValueError, match="OVER_FRAGMENTED_ROLE"):
        experiment._validate_inventory_review_contract(
            proposal,
            subject=subject,
        )


def test_shared_contract_review_rejects_distinct_boundary_operations() -> None:
    first_ref = "AcademicAdministratorBoundary::createOrUpdateTerm(termData:TermInput)"
    second_ref = (
        "AcademicAdministratorBoundary::manageCourseOffering"
        "(details:CourseOfferingDetails)"
    )
    targets = (
        _ref(ReviewEvidenceKind.OPERATION, first_ref),
        _ref(ReviewEvidenceKind.OPERATION, second_ref),
    )
    finding = _finding(
        category=ReviewCategory.SHARED_CONTRACT_REGRESSION,
        owner_stage=ReviewOwnerStage.OPERATION_FRAGMENT,
        owner_ids=("UC10",),
    ).model_copy(update={"target_refs": targets, "evidence": targets})
    proposal = SemanticReviewProposal(
        decision=ReviewDecision.REVISE,
        findings=(finding,),
    )
    subject = {
        "ownerScopes": {
            "operationFragment:UC10": [first_ref],
            "operationFragment:UC11": [second_ref],
        }
    }

    with pytest.raises(ValueError, match="not distinct operations"):
        experiment._validate_behavior_review_contract(proposal, subject=subject)


def test_shared_contract_review_accepts_one_ref_owned_by_multiple_fragments() -> None:
    operation_ref = "StudentBoundary::submit(request:RequestData)"
    target = _ref(ReviewEvidenceKind.OPERATION, operation_ref)
    finding = _finding(
        category=ReviewCategory.SHARED_CONTRACT_REGRESSION,
        owner_stage=ReviewOwnerStage.OPERATION_FRAGMENT,
        owner_ids=("UC1",),
    ).model_copy(update={"target_refs": (target,), "evidence": (target,)})
    proposal = SemanticReviewProposal(
        decision=ReviewDecision.REVISE,
        findings=(finding,),
    )
    subject = {
        "ownerScopes": {
            "operationFragment:UC1": [operation_ref],
            "operationFragment:UC2": [operation_ref],
        }
    }

    experiment._validate_behavior_review_contract(proposal, subject=subject)


def _validated_inventory_review(
    subject: object,
    finding: SemanticReviewFinding,
    *,
    owners: tuple[str, ...],
) -> AdjudicatedSemanticReview:
    evidence_index = _inventory_index(*owners)
    review = validated_semantic_review(
        SemanticReviewProposal(
            decision=ReviewDecision.REVISE,
            findings=(finding,),
        ),
        stage=SemanticReviewStage.INVENTORY,
        subject=subject,
        allowed_owner_ids=owners,
        evidence_index=evidence_index,
    )
    return _adjudicate(review, subject=subject, evidence_index=evidence_index)


def test_review_json_schemas_restrict_categories_and_owner_stages() -> None:
    inventory_schema = str(InventorySemanticReviewProposal.model_json_schema())
    behavior_schema = str(BehaviorSemanticReviewProposal.model_json_schema())

    assert "ENTITY_LIFECYCLE" in inventory_schema
    assert "SCENARIO_INTENT" not in inventory_schema
    assert "inventoryFragment" in inventory_schema
    assert "operationFragment" not in inventory_schema
    assert "SCENARIO_INTENT" in behavior_schema
    assert "ENTITY_LIFECYCLE" not in behavior_schema
    assert "operationFragment" in behavior_schema
    assert "inventoryFragment" not in behavior_schema


def test_semantic_duplicate_requires_two_distinct_target_refs() -> None:
    payload = _finding().model_dump(by_alias=True)
    payload["targetRefs"] = [
        {"kind": "CLASS", "ref": "class:CourseOffering"},
        {"kind": "CLASS", "ref": "class:CourseOffering"},
    ]

    with pytest.raises(ValueError, match="two distinct targets"):
        SemanticReviewFinding.model_validate(payload)


def test_only_high_findings_are_reported_as_blocking_claims() -> None:
    advisory = SemanticReviewProposal(
        decision=ReviewDecision.PASS,
        findings=(_finding(severity=ReviewSeverity.MEDIUM),),
    )
    validated = validated_semantic_review(
        advisory,
        stage=SemanticReviewStage.INVENTORY,
        subject={"inventory": ["Instructor", "Professor"]},
        allowed_owner_ids=("UC1", "UC2"),
        evidence_index=_inventory_index("UC1", "UC2"),
    )
    assert validated.blocking_findings == ()

    with pytest.raises(ValueError, match="if and only if"):
        SemanticReviewProposal(
            decision=ReviewDecision.PASS,
            findings=(_finding(),),
        )


def test_review_must_target_the_owning_stage_and_known_owner() -> None:
    proposal = SemanticReviewProposal(
        decision=ReviewDecision.REVISE,
        findings=(_finding(owner_ids=("UC9",)),),
    )
    with pytest.raises(ValueError, match="unknown owners"):
        validated_semantic_review(
            proposal,
            stage=SemanticReviewStage.INVENTORY,
            subject={},
            allowed_owner_ids=("UC1",),
            evidence_index=_inventory_index("UC1"),
        )

    wrong_stage = SemanticReviewProposal(
        decision=ReviewDecision.REVISE,
        findings=(
            _finding(
                category=ReviewCategory.BCE_OWNER,
                owner_stage=ReviewOwnerStage.OPERATION_FRAGMENT,
                owner_ids=("UC1",),
            ),
        ),
    )
    with pytest.raises(ValueError, match="inventoryFragment"):
        validated_semantic_review(
            wrong_stage,
            stage=SemanticReviewStage.INVENTORY,
            subject={},
            allowed_owner_ids=("UC1",),
            evidence_index=_behavior_index(),
        )


def test_review_checkpoint_is_bound_to_subject_and_evidence_index() -> None:
    subject = {"inventory": ["Instructor", "Professor"]}
    first = validated_semantic_review(
        SemanticReviewProposal(
            decision=ReviewDecision.REVISE,
            findings=(_finding(),),
        ),
        stage=SemanticReviewStage.INVENTORY,
        subject=subject,
        allowed_owner_ids=("UC1", "UC2"),
        evidence_index=_inventory_index("UC1", "UC2"),
    )
    restored = ValidatedSemanticReview.model_validate(first.model_dump(by_alias=True))
    assert restored.subject_digest == first.subject_digest
    assert restored.evidence_index_digest == first.evidence_index_digest
    assert blocking_finding_digest(restored) == blocking_finding_digest(first)


def test_review_rejects_unknown_or_wrongly_typed_evidence() -> None:
    proposal = SemanticReviewProposal(
        decision=ReviewDecision.REVISE,
        findings=(_finding(owner_ids=("UC1",)),),
    )
    with pytest.raises(ValueError, match="unknown typed evidence"):
        validated_semantic_review(
            proposal,
            stage=SemanticReviewStage.INVENTORY,
            subject={"inventory": ["Course"]},
            allowed_owner_ids=("UC1",),
            evidence_index=(
                _record(ReviewEvidenceKind.OUTCOME, "class:Instructor", "UC1"),
                _record(ReviewEvidenceKind.CLASS, "class:Professor", "UC1"),
            ),
        )


def test_relationship_finding_requires_exact_relationship_target() -> None:
    finding = _finding(
        category=ReviewCategory.RELATIONSHIP_SEMANTICS,
        owner_stage=ReviewOwnerStage.INVENTORY_RESOLUTION,
        owner_ids=("class:Registration",),
    )
    review = validated_semantic_review(
        SemanticReviewProposal(
            decision=ReviewDecision.REVISE,
            findings=(finding,),
        ),
        stage=SemanticReviewStage.INVENTORY,
        subject={"inventory": ["Registration", "Course"]},
        allowed_owner_ids=("class:Registration",),
        evidence_index=_inventory_index("class:Registration"),
    )

    assert review.blocking_findings == (finding,)


def test_behavior_review_rejects_inventory_categories() -> None:
    proposal = SemanticReviewProposal(
        decision=ReviewDecision.REVISE,
        findings=(
            _finding(
                owner_stage=ReviewOwnerStage.OPERATION_FRAGMENT,
                owner_ids=("UC1",),
            ),
        ),
    )
    with pytest.raises(ValueError, match="not an allowed BEHAVIOR"):
        validated_semantic_review(
            proposal,
            stage=SemanticReviewStage.BEHAVIOR,
            subject={"catalog": 1},
            allowed_owner_ids=("UC1",),
            evidence_index=(
                _record(ReviewEvidenceKind.CLASS, "class:Instructor", "UC1"),
                _record(ReviewEvidenceKind.CLASS, "class:Professor", "UC1"),
            ),
        )


def test_inventory_review_can_target_a_merged_resolution() -> None:
    subject = {"inventory": ["Instructor", "Professor", "Registration"]}
    review = _validated_inventory_review(
        subject,
        _finding(
            category=ReviewCategory.ENTITY_STRUCTURE,
            owner_stage=ReviewOwnerStage.INVENTORY_RESOLUTION,
            owner_ids=("class:Registration",),
        ),
        owners=("UC1", "class:Registration"),
    )

    assert experiment._blocking_findings_by_owner(
        review,
        owner_stage=ReviewOwnerStage.INVENTORY_RESOLUTION,
    ) == {"class:Registration": [review.blocking_findings[0]]}


def test_adjudication_controls_which_reported_high_claims_are_repairable(
    tmp_path,
) -> None:
    subject = {"catalog": ["Registration::drop"]}
    evidence_index = _behavior_index()
    raw = validated_semantic_review(
        SemanticReviewProposal(
            decision=ReviewDecision.REVISE,
            findings=(
                _finding(
                    category=ReviewCategory.BCE_OWNER,
                    owner_stage=ReviewOwnerStage.OPERATION_FRAGMENT,
                    owner_ids=("UC1",),
                ),
            ),
        ),
        stage=SemanticReviewStage.BEHAVIOR,
        subject=subject,
        allowed_owner_ids=("UC1",),
        evidence_index=evidence_index,
    )

    refuted = _adjudicate(
        raw,
        subject=subject,
        evidence_index=evidence_index,
        disposition=ReviewFindingDisposition.REFUTED,
    )
    unresolved = _adjudicate(
        raw,
        subject=subject,
        evidence_index=evidence_index,
        disposition=ReviewFindingDisposition.UNRESOLVED,
    )
    confirmed = _adjudicate(raw, subject=subject, evidence_index=evidence_index)

    assert refuted.blocking_findings == ()
    assert refuted.unresolved_findings == ()
    assert unresolved.blocking_findings == ()
    assert unresolved.unresolved_findings == raw.reported_high_findings
    assert confirmed.blocking_findings == raw.reported_high_findings
    assert experiment._review_repair_owners(
        run_dir=tmp_path, review=refuted
    ) == set()


def test_adjudication_must_exactly_cover_reported_high_findings() -> None:
    subject = {"inventory": ["Instructor", "Professor"]}
    evidence_index = _inventory_index("UC1", "UC2")
    raw = validated_semantic_review(
        SemanticReviewProposal(
            decision=ReviewDecision.REVISE,
            findings=(_finding(),),
        ),
        stage=SemanticReviewStage.INVENTORY,
        subject=subject,
        allowed_owner_ids=("UC1", "UC2"),
        evidence_index=evidence_index,
    )

    with pytest.raises(ValueError, match="exactly cover"):
        validated_review_adjudication(
            SemanticReviewAdjudicationProposal(findings=()),
            review=raw,
            subject=subject,
            evidence_index=evidence_index,
        )


def test_finding_identity_distinguishes_different_predicates() -> None:
    first = _finding(predicate_key="CAPACITY_RESTORATION_MISSING")
    second = _finding(predicate_key="STATE_OWNER_IS_CONTROL")
    assert semantic_finding_id(first) != semantic_finding_id(second)

    subject = {"inventory": ["Instructor", "Professor"]}
    first_review = _validated_inventory_review(
        subject, first, owners=("UC1", "UC2")
    )
    second_review = _validated_inventory_review(
        subject, second, owners=("UC1", "UC2")
    )
    assert blocking_finding_digest(first_review) != blocking_finding_digest(second_review)


def test_finding_ledger_allows_only_explicit_state_transitions() -> None:
    ledger = FindingLedgerSnapshot()
    ledger = advance_finding_ledger(
        ledger,
        finding_id="finding-1",
        to_status=FindingLedgerStatus.REPORTED,
        subject_digest="subject-1",
        candidate_digest=None,
        rationale="Reviewer reported the claim.",
    )
    ledger = advance_finding_ledger(
        ledger,
        finding_id="finding-1",
        to_status=FindingLedgerStatus.CONFIRMED,
        subject_digest="subject-1",
        candidate_digest=None,
        rationale="Adjudication confirmed the claim.",
    )
    assert ledger.latest_statuses == {"finding-1": FindingLedgerStatus.CONFIRMED}

    with pytest.raises(ValueError, match="not allowed"):
        advance_finding_ledger(
            ledger,
            finding_id="finding-1",
            to_status=FindingLedgerStatus.VERIFIED_RESOLVED,
            subject_digest="subject-2",
            candidate_digest="candidate-2",
            rationale="A patch was never recorded.",
        )


def test_experiment_ledger_closes_a_patched_finding_only_after_rereview(
    tmp_path,
) -> None:
    subject = {"catalog": ["Registration::drop"]}
    evidence_index = _behavior_index()
    raw = validated_semantic_review(
        SemanticReviewProposal(
            decision=ReviewDecision.REVISE,
            findings=(
                _finding(
                    category=ReviewCategory.BCE_OWNER,
                    owner_stage=ReviewOwnerStage.OPERATION_FRAGMENT,
                    owner_ids=("UC1",),
                ),
            ),
        ),
        stage=SemanticReviewStage.BEHAVIOR,
        subject=subject,
        allowed_owner_ids=("UC1",),
        evidence_index=evidence_index,
    )
    confirmed = _adjudicate(raw, subject=subject, evidence_index=evidence_index)
    finding_id = semantic_finding_id(confirmed.blocking_findings[0])
    experiment._record_adjudicated_findings(
        run_dir=tmp_path,
        checkpoint_key="behavior",
        review=confirmed,
    )
    experiment._record_patched_findings(
        run_dir=tmp_path,
        checkpoint_key="behavior",
        review=confirmed,
        candidate_digest="candidate-1",
    )

    next_subject = {"catalog": ["Registration::drop-fixed"]}
    clean_raw = validated_semantic_review(
        SemanticReviewProposal(decision=ReviewDecision.PASS, findings=()),
        stage=SemanticReviewStage.BEHAVIOR,
        subject=next_subject,
        allowed_owner_ids=("UC1",),
        evidence_index=evidence_index,
    )
    clean = validated_review_adjudication(
        SemanticReviewAdjudicationProposal(findings=()),
        review=clean_raw,
        subject=next_subject,
        evidence_index=evidence_index,
    )
    experiment._record_adjudicated_findings(
        run_dir=tmp_path,
        checkpoint_key="behavior",
        review=clean,
    )

    ledger = experiment._load_finding_ledger(tmp_path, "behavior")
    assert ledger.latest_statuses[finding_id] is FindingLedgerStatus.VERIFIED_RESOLVED


def test_review_checkpoint_is_reused_only_for_the_same_subject(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def invoke(**kwargs):
        calls.append(kwargs["operation"])
        return {"decision": "PASS", "findings": []}

    monkeypatch.setattr(experiment, "_invoke", invoke)
    arguments = {
        "stage": SemanticReviewStage.INVENTORY,
        "allowed_owner_ids": ("UC1",),
        "run_dir": tmp_path,
        "budget": experiment.LogicalCallBudget(3),
    }
    experiment._semantic_review(subject={"inventory": 1}, **arguments)
    experiment._semantic_review(subject={"inventory": 1}, **arguments)
    experiment._semantic_review(subject={"inventory": 2}, **arguments)

    assert calls == [
        "ExecutableInventorySemanticReview",
        "ExecutableInventorySemanticReview",
    ]


def test_review_attempt_is_promoted_without_another_llm_call(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = {"inventory": 1}
    digest = experiment.canonical_digest(subject)
    attempt = (
        tmp_path
        / "attempts"
        / "reviews"
        / f"inventory-{digest[:12]}-proposal-existing.json"
    )
    experiment._write_json(attempt, {"decision": "PASS", "findings": []})

    def unexpected_invoke(**_kwargs):
        raise AssertionError("a valid saved review attempt must be promoted")

    monkeypatch.setattr(experiment, "_invoke", unexpected_invoke)
    result = experiment._semantic_review(
        stage=SemanticReviewStage.INVENTORY,
        subject=subject,
        allowed_owner_ids=("UC1",),
        run_dir=tmp_path,
        budget=experiment.LogicalCallBudget(1),
    )

    assert result.review.decision is ReviewDecision.PASS


def test_review_repairs_one_wrong_stage_owner_contract_before_failing_the_stage(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    responses = iter(
        (
            {
                "decision": "REVISE",
                "findings": [
                    {
                        "ruleId": "SEMANTIC.SEMANTIC_DUPLICATE",
                        "predicateKey": "DUPLICATE_OFFERING_CONCEPT",
                        "category": "SEMANTIC_DUPLICATE",
                        "severity": "HIGH",
                        "ownerStage": "inventoryResolution",
                        "ownerIds": ["UC7"],
                        "obligationIds": [],
                        "targetRefs": [
                            {"kind": "CLASS", "ref": "class:Offering"},
                            {"kind": "CLASS", "ref": "class:CourseOffering"},
                        ],
                        "evidence": [
                            {"kind": "CLASS", "ref": "class:Offering"},
                            {"kind": "CLASS", "ref": "class:CourseOffering"},
                        ],
                        "expected": "One offering concept is canonical.",
                        "observed": "Two offering concepts overlap.",
                        "message": "Offering duplicates CourseOffering.",
                    }
                ],
            },
            {
                "decision": "REVISE",
                "findings": [
                    {
                        "ruleId": "SEMANTIC.SEMANTIC_DUPLICATE",
                        "predicateKey": "DUPLICATE_OFFERING_CONCEPT",
                        "category": "SEMANTIC_DUPLICATE",
                        "severity": "HIGH",
                        "ownerStage": "inventoryFragment",
                        "ownerIds": ["UC7"],
                        "obligationIds": [],
                        "targetRefs": [
                            {"kind": "CLASS", "ref": "class:Offering"},
                            {"kind": "CLASS", "ref": "class:CourseOffering"},
                        ],
                        "evidence": [
                            {"kind": "CLASS", "ref": "class:Offering"},
                            {"kind": "CLASS", "ref": "class:CourseOffering"},
                        ],
                        "expected": "One offering concept is canonical.",
                        "observed": "Two offering concepts overlap.",
                        "message": "Offering duplicates CourseOffering.",
                    }
                ],
            },
        )
    )

    def invoke(**kwargs):
        calls.append(kwargs["operation"])
        return next(responses)

    monkeypatch.setattr(experiment, "_invoke", invoke)
    subject = {
        "ownerScopes": {
            "inventoryFragment:UC7": ["class:Offering"],
            "inventoryResolution:class:CourseOffering": [
                "class:CourseOffering"
            ],
        }
    }

    result = experiment._semantic_review(
        stage=SemanticReviewStage.INVENTORY,
        subject=subject,
        allowed_owner_ids=("UC7", "class:CourseOffering"),
        run_dir=tmp_path,
        budget=experiment.LogicalCallBudget(2),
    )

    assert result.review.decision is ReviewDecision.REVISE
    assert result.review.findings[0].owner_ids == ("UC7",)
    assert calls == [
        "ExecutableInventorySemanticReview",
        "ExecutableInventorySemanticReviewContractRepair",
    ]
    assert len(list((tmp_path / "attempts" / "reviews").glob("*.json"))) == 2


def test_owner_can_repair_two_distinct_findings_but_not_a_third(tmp_path) -> None:
    initial_subject = {"inventory": ["before", "Instructor", "Professor"]}
    initial = _validated_inventory_review(
        initial_subject,
        _finding(owner_ids=("UC1",)),
        owners=("UC1",),
    )
    repaired_subject = {"inventory": ["after", "Instructor", "Professor"]}
    experiment._record_review_repair_cycle(
        run_dir=tmp_path,
        initial_review=initial,
        output_subject=repaired_subject,
        repaired_owner_ids={"inventoryFragment:UC1"},
    )
    still_blocking = _validated_inventory_review(
        repaired_subject,
        _finding(
            category=ReviewCategory.ENTITY_STRUCTURE,
            owner_ids=("UC1",),
        ),
        owners=("UC1",),
    )

    assert experiment._review_repair_owners(
        run_dir=tmp_path,
        review=still_blocking,
    ) == {"inventoryFragment:UC1"}
    twice_repaired_subject = {
        "inventory": ["after-again", "Instructor", "Professor"]
    }
    experiment._record_review_repair_cycle(
        run_dir=tmp_path,
        initial_review=still_blocking,
        output_subject=twice_repaired_subject,
        repaired_owner_ids={"inventoryFragment:UC1"},
    )
    third_review = _validated_inventory_review(
        twice_repaired_subject,
        _finding(
            category=ReviewCategory.RELATIONSHIP_SEMANTICS,
            owner_ids=("UC1",),
        ),
        owners=("UC1",),
    )

    with pytest.raises(
        experiment.ExperimentFailure, match="owners: inventoryFragment:UC1"
    ):
        experiment._review_repair_owners(
            run_dir=tmp_path,
            review=third_review,
        )


def test_new_owner_can_be_repaired_after_a_review_round(tmp_path) -> None:
    initial_subject = {"inventory": ["before", "Instructor", "Professor"]}
    initial = _validated_inventory_review(
        initial_subject,
        _finding(owner_ids=("UC1",)),
        owners=("UC1", "UC2"),
    )
    experiment._record_review_repair_cycle(
        run_dir=tmp_path,
        initial_review=initial,
        output_subject={"inventory": ["after", "Instructor", "Professor"]},
        repaired_owner_ids={"inventoryFragment:UC1"},
    )
    next_subject = {"inventory": ["after", "Instructor", "Professor"]}
    next_review = _validated_inventory_review(
        next_subject,
        _finding(
            category=ReviewCategory.ENTITY_STRUCTURE,
            owner_ids=("UC2",),
        ),
        owners=("UC1", "UC2"),
    )

    assert experiment._review_repair_owners(
        run_dir=tmp_path,
        review=next_review,
    ) == {"inventoryFragment:UC2"}
