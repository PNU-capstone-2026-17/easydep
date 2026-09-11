"""Grounded semantic-review and finding-ledger contracts.

Reviewers only report claims. Exact typed evidence is resolved against an index
constructed by code, and a separate adjudication decides whether a HIGH claim is
confirmed, refuted, or unresolved. Only confirmed findings are repairable.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .contracts import (
    Contract,
    Provenance,
    RevisionedDraft,
    StageStatus,
    canonical_digest,
    make_provenance,
    provenance_matches_subject,
)

REVIEW_VALIDATOR_VERSION = "executable-behavior.semantic-review.v3"


class SemanticReviewStage(StrEnum):
    INVENTORY = "INVENTORY"
    BEHAVIOR = "BEHAVIOR"


class ReviewOwnerStage(StrEnum):
    INVENTORY_FRAGMENT = "inventoryFragment"
    INVENTORY_RESOLUTION = "inventoryResolution"
    OPERATION_FRAGMENT = "operationFragment"
    INTEGRATION_RESOLUTION = "integrationResolution"


class ReviewDecision(StrEnum):
    PASS = "PASS"  # noqa: S105 - review verdict, not a credential
    REVISE = "REVISE"


class ReviewSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ReviewCategory(StrEnum):
    SEMANTIC_DUPLICATE = "SEMANTIC_DUPLICATE"
    OVER_FRAGMENTED_ROLE = "OVER_FRAGMENTED_ROLE"
    ENTITY_LIFECYCLE = "ENTITY_LIFECYCLE"
    ENTITY_STRUCTURE = "ENTITY_STRUCTURE"
    RELATIONSHIP_SEMANTICS = "RELATIONSHIP_SEMANTICS"
    MISSED_SHARED_CONCEPT = "MISSED_SHARED_CONCEPT"
    SCENARIO_INTENT = "SCENARIO_INTENT"
    BCE_OWNER = "BCE_OWNER"
    SEMANTIC_DUPLICATE_OPERATION = "SEMANTIC_DUPLICATE_OPERATION"
    MIXED_RESPONSIBILITY = "MIXED_RESPONSIBILITY"
    PARAMETER_SEMANTICS = "PARAMETER_SEMANTICS"
    SCENARIO_PATH_OMISSION = "SCENARIO_PATH_OMISSION"
    ENTITY_STATE_OWNERSHIP = "ENTITY_STATE_OWNERSHIP"
    SHARED_CONTRACT_REGRESSION = "SHARED_CONTRACT_REGRESSION"


INVENTORY_CATEGORIES = frozenset(
    {
        ReviewCategory.SEMANTIC_DUPLICATE,
        ReviewCategory.OVER_FRAGMENTED_ROLE,
        ReviewCategory.ENTITY_LIFECYCLE,
        ReviewCategory.ENTITY_STRUCTURE,
        ReviewCategory.RELATIONSHIP_SEMANTICS,
        ReviewCategory.MISSED_SHARED_CONCEPT,
    }
)
BEHAVIOR_CATEGORIES = frozenset(
    {
        ReviewCategory.SCENARIO_INTENT,
        ReviewCategory.BCE_OWNER,
        ReviewCategory.SEMANTIC_DUPLICATE_OPERATION,
        ReviewCategory.MIXED_RESPONSIBILITY,
        ReviewCategory.PARAMETER_SEMANTICS,
        ReviewCategory.SCENARIO_PATH_OMISSION,
        ReviewCategory.ENTITY_STATE_OWNERSHIP,
        ReviewCategory.SHARED_CONTRACT_REGRESSION,
    }
)


class ReviewEvidenceKind(StrEnum):
    STEP = "STEP"
    OUTCOME = "OUTCOME"
    CLASS = "CLASS"
    DATA_TYPE = "DATA_TYPE"
    RELATIONSHIP = "RELATIONSHIP"
    OPERATION = "OPERATION"
    OBLIGATION = "OBLIGATION"
    STATE_EFFECT = "STATE_EFFECT"


class ReviewEvidenceRef(Contract):
    kind: ReviewEvidenceKind
    ref: str = Field(min_length=1)

    @field_validator("ref")
    @classmethod
    def normalized_ref(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("evidence ref must be non-empty")
        return normalized


class ReviewEvidenceRecord(ReviewEvidenceRef):
    owner_ids: tuple[str, ...] = Field(alias="ownerIds", min_length=1)

    @field_validator("owner_ids")
    @classmethod
    def normalized_owners(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(value.strip() for value in values if value.strip()))
        if not normalized:
            raise ValueError("evidence record requires a non-empty owner")
        return normalized


class SemanticReviewFinding(Contract):
    rule_id: str = Field(alias="ruleId", pattern=r"^[A-Z][A-Z0-9_.-]*$")
    predicate_key: str = Field(alias="predicateKey", pattern=r"^[A-Z][A-Z0-9_.-]*$")
    category: ReviewCategory
    severity: ReviewSeverity
    owner_stage: ReviewOwnerStage = Field(alias="ownerStage")
    owner_ids: tuple[str, ...] = Field(alias="ownerIds", min_length=1)
    obligation_ids: tuple[str, ...] = Field(alias="obligationIds", default=())
    target_refs: tuple[ReviewEvidenceRef, ...] = Field(alias="targetRefs", min_length=1)
    evidence: tuple[ReviewEvidenceRef, ...] = Field(min_length=1)
    expected: str = Field(min_length=1)
    observed: str = Field(min_length=1)
    message: str = Field(min_length=1)

    @field_validator("owner_ids", "obligation_ids")
    @classmethod
    def normalized_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode="after")
    def category_requires_concrete_targets(self) -> SemanticReviewFinding:
        kinds = {item.kind for item in self.target_refs}
        if self.category is ReviewCategory.RELATIONSHIP_SEMANTICS and (
            ReviewEvidenceKind.RELATIONSHIP not in kinds
        ):
            raise ValueError("relationship finding requires a relationship target")
        operation_categories = {
            ReviewCategory.BCE_OWNER,
            ReviewCategory.SEMANTIC_DUPLICATE_OPERATION,
            ReviewCategory.MIXED_RESPONSIBILITY,
            ReviewCategory.PARAMETER_SEMANTICS,
            ReviewCategory.ENTITY_STATE_OWNERSHIP,
        }
        if self.category in operation_categories and ReviewEvidenceKind.OPERATION not in kinds:
            raise ValueError("operation semantic finding requires an operation target")
        if self.category is ReviewCategory.SCENARIO_PATH_OMISSION and not (
            kinds
            & {
                ReviewEvidenceKind.STEP,
                ReviewEvidenceKind.OBLIGATION,
                ReviewEvidenceKind.OUTCOME,
            }
        ):
            raise ValueError("scenario omission requires a step, outcome, or obligation target")
        if self.category is ReviewCategory.SHARED_CONTRACT_REGRESSION and not (
            kinds
            & {
                ReviewEvidenceKind.OPERATION,
                ReviewEvidenceKind.OBLIGATION,
                ReviewEvidenceKind.STATE_EFFECT,
            }
        ):
            raise ValueError(
                "shared contract regression requires an operation, obligation, or effect target"
            )
        if self.category is ReviewCategory.SEMANTIC_DUPLICATE and len(
            {(item.kind, item.ref) for item in self.target_refs}
        ) < 2:
            raise ValueError("semantic duplicate requires at least two distinct targets")
        return self


class SemanticReviewProposal(Contract):
    decision: ReviewDecision
    findings: tuple[SemanticReviewFinding, ...] = Field(max_length=12)

    @model_validator(mode="after")
    def decision_matches_blocking_findings(self) -> SemanticReviewProposal:
        has_high = any(item.severity is ReviewSeverity.HIGH for item in self.findings)
        expected = ReviewDecision.REVISE if has_high else ReviewDecision.PASS
        if self.decision is not expected:
            raise ValueError("decision must be REVISE if and only if a HIGH finding exists")
        return self


class InventorySemanticReviewFinding(SemanticReviewFinding):
    category: Literal[
        ReviewCategory.SEMANTIC_DUPLICATE,
        ReviewCategory.OVER_FRAGMENTED_ROLE,
        ReviewCategory.ENTITY_LIFECYCLE,
        ReviewCategory.ENTITY_STRUCTURE,
        ReviewCategory.RELATIONSHIP_SEMANTICS,
        ReviewCategory.MISSED_SHARED_CONCEPT,
    ]
    owner_stage: Literal[
        ReviewOwnerStage.INVENTORY_FRAGMENT,
        ReviewOwnerStage.INVENTORY_RESOLUTION,
    ] = Field(alias="ownerStage")


class InventorySemanticReviewProposal(SemanticReviewProposal):
    findings: tuple[InventorySemanticReviewFinding, ...] = Field(max_length=12)


class BehaviorSemanticReviewFinding(SemanticReviewFinding):
    category: Literal[
        ReviewCategory.SCENARIO_INTENT,
        ReviewCategory.BCE_OWNER,
        ReviewCategory.SEMANTIC_DUPLICATE_OPERATION,
        ReviewCategory.MIXED_RESPONSIBILITY,
        ReviewCategory.PARAMETER_SEMANTICS,
        ReviewCategory.SCENARIO_PATH_OMISSION,
        ReviewCategory.ENTITY_STATE_OWNERSHIP,
        ReviewCategory.SHARED_CONTRACT_REGRESSION,
    ]
    owner_stage: Literal[
        ReviewOwnerStage.OPERATION_FRAGMENT,
        ReviewOwnerStage.INTEGRATION_RESOLUTION,
    ] = Field(alias="ownerStage")


class BehaviorSemanticReviewProposal(SemanticReviewProposal):
    findings: tuple[BehaviorSemanticReviewFinding, ...] = Field(max_length=12)


class ValidatedSemanticReview(RevisionedDraft):
    status: Literal[StageStatus.VALIDATED] = StageStatus.VALIDATED
    stage: SemanticReviewStage
    subject_digest: str = Field(alias="subjectDigest", min_length=1)
    evidence_index_digest: str = Field(alias="evidenceIndexDigest", min_length=1)
    review: SemanticReviewProposal

    @model_validator(mode="after")
    def provenance_matches_review(self) -> ValidatedSemanticReview:
        if not provenance_matches_subject(self.provenance, _review_subject(self)):
            raise ValueError("semantic review provenance does not match its content")
        return self

    @property
    def reported_high_findings(self) -> tuple[SemanticReviewFinding, ...]:
        return tuple(
            item for item in self.review.findings if item.severity is ReviewSeverity.HIGH
        )

    @property
    def blocking_findings(self) -> tuple[SemanticReviewFinding, ...]:
        """Compatibility view; repair code should use an adjudicated review."""
        return self.reported_high_findings


class ReviewFindingDisposition(StrEnum):
    CONFIRMED = "CONFIRMED"
    REFUTED = "REFUTED"
    UNRESOLVED = "UNRESOLVED"


class FindingAdjudication(Contract):
    finding_id: str = Field(alias="findingId", min_length=1)
    disposition: ReviewFindingDisposition
    evidence: tuple[ReviewEvidenceRef, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)


class SemanticReviewAdjudicationProposal(Contract):
    findings: tuple[FindingAdjudication, ...]


class AdjudicatedSemanticReview(ValidatedSemanticReview):
    adjudications: tuple[FindingAdjudication, ...]
    adjudication_provenance: Provenance = Field(alias="adjudicationProvenance")

    @model_validator(mode="after")
    def provenance_matches_adjudication(self) -> AdjudicatedSemanticReview:
        if not provenance_matches_subject(
            self.adjudication_provenance, _adjudication_subject(self)
        ):
            raise ValueError("review adjudication provenance does not match its content")
        return self

    @property
    def blocking_findings(self) -> tuple[SemanticReviewFinding, ...]:
        by_id = {semantic_finding_id(item): item for item in self.reported_high_findings}
        return tuple(
            by_id[item.finding_id]
            for item in self.adjudications
            if item.disposition is ReviewFindingDisposition.CONFIRMED
        )

    @property
    def unresolved_findings(self) -> tuple[SemanticReviewFinding, ...]:
        by_id = {semantic_finding_id(item): item for item in self.reported_high_findings}
        return tuple(
            by_id[item.finding_id]
            for item in self.adjudications
            if item.disposition is ReviewFindingDisposition.UNRESOLVED
        )


def semantic_finding_id(finding: SemanticReviewFinding) -> str:
    """Identify the violation, not its prose or the current supporting evidence."""
    return canonical_digest(
        {
            "ruleId": finding.rule_id,
            "predicateKey": finding.predicate_key,
            "category": finding.category.value,
            "ownerStage": finding.owner_stage.value,
            "ownerIds": sorted(finding.owner_ids),
            "obligationIds": sorted(finding.obligation_ids),
            "targetRefs": sorted(
                (
                    {"kind": item.kind.value, "ref": item.ref}
                    for item in finding.target_refs
                ),
                key=canonical_digest,
            ),
        }
    )


def _review_subject(review: ValidatedSemanticReview) -> dict[str, Any]:
    return {
        "stage": review.stage.value,
        "subjectDigest": review.subject_digest,
        "evidenceIndexDigest": review.evidence_index_digest,
        "review": review.review.model_dump(by_alias=True),
    }


def _adjudication_subject(review: AdjudicatedSemanticReview) -> dict[str, Any]:
    return {
        "reviewProvenance": review.provenance.digest,
        "adjudications": [item.model_dump(by_alias=True) for item in review.adjudications],
    }


def _evidence_key(value: ReviewEvidenceRef) -> tuple[ReviewEvidenceKind, str]:
    return value.kind, value.ref


def _validated_evidence_index(
    evidence_index: Iterable[ReviewEvidenceRecord | Mapping[str, Any]],
    *,
    allowed_owner_ids: set[str],
) -> tuple[ReviewEvidenceRecord, ...]:
    records = tuple(
        item
        if isinstance(item, ReviewEvidenceRecord)
        else ReviewEvidenceRecord.model_validate(item)
        for item in evidence_index
    )
    keys: set[tuple[ReviewEvidenceKind, str]] = set()
    for record in records:
        key = _evidence_key(record)
        if key in keys:
            raise ValueError(f"duplicate evidence index entry: {record.kind.value}:{record.ref}")
        keys.add(key)
        unknown = set(record.owner_ids) - allowed_owner_ids
        if unknown:
            raise ValueError("evidence index names unknown owners: " + ", ".join(sorted(unknown)))
    return records


def _validate_finding_evidence(
    finding: SemanticReviewFinding,
    records: tuple[ReviewEvidenceRecord, ...],
) -> None:
    by_key = {_evidence_key(item): item for item in records}
    references = (*finding.target_refs, *finding.evidence)
    unknown = [item for item in references if _evidence_key(item) not in by_key]
    if unknown:
        raise ValueError(
            "review cites unknown typed evidence: "
            + ", ".join(f"{item.kind.value}:{item.ref}" for item in unknown)
        )
    target_owners = {
        owner_id
        for item in finding.target_refs
        for owner_id in by_key[_evidence_key(item)].owner_ids
    }
    if not (target_owners & set(finding.owner_ids)):
        raise ValueError("review targets do not belong to a named repair owner")
    indexed_obligations = {
        item.ref for item in records if item.kind is ReviewEvidenceKind.OBLIGATION
    }
    unknown_obligations = set(finding.obligation_ids) - indexed_obligations
    if unknown_obligations:
        raise ValueError(
            "review names unknown obligations: " + ", ".join(sorted(unknown_obligations))
        )


def validated_semantic_review(
    proposal: SemanticReviewProposal | Mapping[str, Any],
    *,
    stage: SemanticReviewStage,
    subject: Any,
    allowed_owner_ids: Iterable[str],
    evidence_index: Iterable[ReviewEvidenceRecord | Mapping[str, Any]],
    revision: int = 1,
) -> ValidatedSemanticReview:
    """Ground every typed reference and bind the claim set to the exact subject."""
    review = (
        proposal
        if isinstance(proposal, SemanticReviewProposal)
        else SemanticReviewProposal.model_validate(proposal)
    )
    allowed = tuple(dict.fromkeys(str(value).strip() for value in allowed_owner_ids))
    allowed_set = {value for value in allowed if value}
    records = _validated_evidence_index(evidence_index, allowed_owner_ids=allowed_set)
    allowed_owner_stages = (
        {
            ReviewOwnerStage.INVENTORY_FRAGMENT,
            ReviewOwnerStage.INVENTORY_RESOLUTION,
        }
        if stage is SemanticReviewStage.INVENTORY
        else {
            ReviewOwnerStage.OPERATION_FRAGMENT,
            ReviewOwnerStage.INTEGRATION_RESOLUTION,
        }
    )
    allowed_categories = (
        INVENTORY_CATEGORIES
        if stage is SemanticReviewStage.INVENTORY
        else BEHAVIOR_CATEGORIES
    )
    finding_ids: set[str] = set()
    for finding in review.findings:
        if finding.owner_stage not in allowed_owner_stages:
            expected = ", ".join(sorted(value.value for value in allowed_owner_stages))
            raise ValueError(f"{stage.value} review must target one of: {expected}")
        unknown = set(finding.owner_ids) - allowed_set
        if unknown:
            raise ValueError("review targets unknown owners: " + ", ".join(sorted(unknown)))
        if finding.category not in allowed_categories:
            raise ValueError(
                f"{finding.category.value} is not an allowed {stage.value} review category"
            )
        _validate_finding_evidence(finding, records)
        finding_id = semantic_finding_id(finding)
        if finding_id in finding_ids:
            raise ValueError("semantic review contains a duplicate finding identity")
        finding_ids.add(finding_id)

    subject_digest = canonical_digest(subject)
    evidence_index_payload = [item.model_dump(by_alias=True) for item in records]
    evidence_index_digest = canonical_digest(evidence_index_payload)
    review_subject = {
        "stage": stage.value,
        "subjectDigest": subject_digest,
        "evidenceIndexDigest": evidence_index_digest,
        "review": review.model_dump(by_alias=True),
    }
    provenance = make_provenance(
        revision=revision,
        inputs={
            "subject": subject,
            "allowedOwnerIds": allowed,
            "evidenceIndex": evidence_index_payload,
        },
        subject=review_subject,
        validator_version=REVIEW_VALIDATOR_VERSION,
    )
    return ValidatedSemanticReview(
        stage=stage,
        subjectDigest=subject_digest,
        evidenceIndexDigest=evidence_index_digest,
        review=review,
        provenance=provenance,
    )


def validated_review_adjudication(
    proposal: SemanticReviewAdjudicationProposal | Mapping[str, Any],
    *,
    review: ValidatedSemanticReview,
    subject: Any,
    evidence_index: Iterable[ReviewEvidenceRecord | Mapping[str, Any]],
    revision: int = 1,
) -> AdjudicatedSemanticReview:
    """Require an explicit disposition for every reported HIGH claim."""
    value = (
        proposal
        if isinstance(proposal, SemanticReviewAdjudicationProposal)
        else SemanticReviewAdjudicationProposal.model_validate(proposal)
    )
    if review.subject_digest != canonical_digest(subject):
        raise ValueError("adjudication subject does not match the reviewed subject")
    raw_records = tuple(evidence_index)
    allowed_owners = {
        owner_id
        for record in raw_records
        for owner_id in (
            record.owner_ids
            if isinstance(record, ReviewEvidenceRecord)
            else ReviewEvidenceRecord.model_validate(record).owner_ids
        )
    }
    records = _validated_evidence_index(
        raw_records, allowed_owner_ids=allowed_owners
    )
    if canonical_digest([item.model_dump(by_alias=True) for item in records]) != (
        review.evidence_index_digest
    ):
        raise ValueError("adjudication evidence index differs from the review index")
    high_by_id = {
        semantic_finding_id(item): item for item in review.reported_high_findings
    }
    adjudication_ids = [item.finding_id for item in value.findings]
    if len(adjudication_ids) != len(set(adjudication_ids)):
        raise ValueError("adjudication contains duplicate finding IDs")
    if set(adjudication_ids) != set(high_by_id):
        raise ValueError("adjudication must exactly cover every reported HIGH finding")
    by_key = {_evidence_key(item): item for item in records}
    for item in value.findings:
        unknown = [ref for ref in item.evidence if _evidence_key(ref) not in by_key]
        if unknown:
            raise ValueError(
                "adjudication cites unknown typed evidence: "
                + ", ".join(f"{ref.kind.value}:{ref.ref}" for ref in unknown)
            )
        finding = high_by_id[item.finding_id]
        finding_evidence = {
            _evidence_key(ref) for ref in (*finding.target_refs, *finding.evidence)
        }
        if not ({_evidence_key(ref) for ref in item.evidence} & finding_evidence):
            raise ValueError("adjudication evidence does not address the finding")
        evidence_owners = {
            owner_id
            for ref in item.evidence
            for owner_id in by_key[_evidence_key(ref)].owner_ids
        }
        if not (evidence_owners & set(finding.owner_ids)):
            raise ValueError("adjudication evidence belongs to another repair owner")

    adjudications = tuple(value.findings)
    adjudication_subject = {
        "reviewProvenance": review.provenance.digest,
        "adjudications": [item.model_dump(by_alias=True) for item in adjudications],
    }
    adjudication_provenance = make_provenance(
        revision=revision,
        inputs={
            "review": review.model_dump(by_alias=True),
            "subject": subject,
            "evidenceIndex": [item.model_dump(by_alias=True) for item in records],
        },
        subject=adjudication_subject,
        validator_version=REVIEW_VALIDATOR_VERSION,
    )
    return AdjudicatedSemanticReview(
        **review.model_dump(by_alias=True),
        adjudications=adjudications,
        adjudicationProvenance=adjudication_provenance,
    )


class FindingLedgerStatus(StrEnum):
    REPORTED = "REPORTED"
    CONFIRMED = "CONFIRMED"
    REFUTED = "REFUTED"
    UNRESOLVED = "UNRESOLVED"
    PATCHED = "PATCHED"
    VERIFIED_RESOLVED = "VERIFIED_RESOLVED"
    STILL_OPEN = "STILL_OPEN"
    REGRESSION = "REGRESSION"


class FindingLedgerTransition(Contract):
    sequence: int = Field(ge=1)
    finding_id: str = Field(alias="findingId", min_length=1)
    from_status: FindingLedgerStatus | None = Field(alias="fromStatus")
    to_status: FindingLedgerStatus = Field(alias="toStatus")
    subject_digest: str = Field(alias="subjectDigest", min_length=1)
    candidate_digest: str | None = Field(alias="candidateDigest")
    rationale: str = Field(min_length=1)


class FindingLedgerSnapshot(Contract):
    transitions: tuple[FindingLedgerTransition, ...] = ()

    @model_validator(mode="after")
    def transitions_form_valid_histories(self) -> FindingLedgerSnapshot:
        latest: dict[str, FindingLedgerStatus] = {}
        for position, item in enumerate(self.transitions, start=1):
            if item.sequence != position:
                raise ValueError("finding ledger sequence must be contiguous")
            previous = latest.get(item.finding_id)
            if item.from_status is not previous:
                raise ValueError("finding ledger transition has a stale fromStatus")
            if item.to_status not in _allowed_finding_transitions(previous):
                raise ValueError("finding ledger transition is not allowed")
            latest[item.finding_id] = item.to_status
        return self

    @property
    def latest_statuses(self) -> dict[str, FindingLedgerStatus]:
        result: dict[str, FindingLedgerStatus] = {}
        for item in self.transitions:
            result[item.finding_id] = item.to_status
        return result


def _allowed_finding_transitions(
    status: FindingLedgerStatus | None,
) -> frozenset[FindingLedgerStatus]:
    return {
        None: frozenset({FindingLedgerStatus.REPORTED}),
        FindingLedgerStatus.REPORTED: frozenset(
            {
                FindingLedgerStatus.CONFIRMED,
                FindingLedgerStatus.REFUTED,
                FindingLedgerStatus.UNRESOLVED,
            }
        ),
        FindingLedgerStatus.UNRESOLVED: frozenset(
            {FindingLedgerStatus.CONFIRMED, FindingLedgerStatus.REFUTED}
        ),
        FindingLedgerStatus.CONFIRMED: frozenset({FindingLedgerStatus.PATCHED}),
        FindingLedgerStatus.PATCHED: frozenset(
            {
                FindingLedgerStatus.UNRESOLVED,
                FindingLedgerStatus.VERIFIED_RESOLVED,
                FindingLedgerStatus.STILL_OPEN,
                FindingLedgerStatus.REGRESSION,
            }
        ),
        FindingLedgerStatus.STILL_OPEN: frozenset({FindingLedgerStatus.PATCHED}),
        FindingLedgerStatus.REFUTED: frozenset(),
        FindingLedgerStatus.VERIFIED_RESOLVED: frozenset(),
        FindingLedgerStatus.REGRESSION: frozenset(),
    }[status]


def advance_finding_ledger(
    ledger: FindingLedgerSnapshot,
    *,
    finding_id: str,
    to_status: FindingLedgerStatus,
    subject_digest: str,
    candidate_digest: str | None,
    rationale: str,
) -> FindingLedgerSnapshot:
    """Append one immutable, provenance-carrying finding transition."""
    previous = ledger.latest_statuses.get(finding_id)
    transition = FindingLedgerTransition(
        sequence=len(ledger.transitions) + 1,
        findingId=finding_id,
        fromStatus=previous,
        toStatus=to_status,
        subjectDigest=subject_digest,
        candidateDigest=candidate_digest,
        rationale=rationale,
    )
    return FindingLedgerSnapshot(transitions=(*ledger.transitions, transition))


def blocking_finding_digest(
    review: ValidatedSemanticReview | AdjudicatedSemanticReview,
) -> str:
    """Hash stable violation identities, not mutable prose or incidental evidence."""
    return canonical_digest(sorted(semantic_finding_id(item) for item in review.blocking_findings))


__all__ = [
    "BEHAVIOR_CATEGORIES",
    "INVENTORY_CATEGORIES",
    "REVIEW_VALIDATOR_VERSION",
    "AdjudicatedSemanticReview",
    "BehaviorSemanticReviewProposal",
    "FindingAdjudication",
    "FindingLedgerSnapshot",
    "FindingLedgerStatus",
    "FindingLedgerTransition",
    "InventorySemanticReviewProposal",
    "ReviewCategory",
    "ReviewDecision",
    "ReviewEvidenceKind",
    "ReviewEvidenceRecord",
    "ReviewEvidenceRef",
    "ReviewFindingDisposition",
    "ReviewOwnerStage",
    "ReviewSeverity",
    "SemanticReviewAdjudicationProposal",
    "SemanticReviewFinding",
    "SemanticReviewProposal",
    "SemanticReviewStage",
    "ValidatedSemanticReview",
    "advance_finding_ledger",
    "blocking_finding_digest",
    "semantic_finding_id",
    "validated_review_adjudication",
    "validated_semantic_review",
]
