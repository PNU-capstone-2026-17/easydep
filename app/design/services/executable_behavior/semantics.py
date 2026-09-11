"""Use-case obligations, explicit effects, and local semantic seals.

This boundary makes state-changing behavior decidable before global assembly.
An operation name or step reference is never treated as proof of an effect.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from app.design.schemas.class_model import canonical_operation_id

from .calls import catalog_operations
from .catalog import CatalogResult, assemble_catalog
from .contracts import (
    Contract,
    RevisionedDraft,
    StageStatus,
    ValidatedCallStructure,
    ValidatedCatalogDraft,
    ValidatedOperationFragment,
    canonical_digest,
    make_provenance,
    provenance_matches_subject,
)
from .reviews import (
    AdjudicatedSemanticReview,
    ReviewEvidenceKind,
    ReviewEvidenceRecord,
)

LOCAL_SEMANTICS_VALIDATOR_VERSION = "executable-behavior.local-semantics.v3"
LOCAL_SEAL_VALIDATOR_VERSION = "executable-behavior.local-seal.v1"


class ObligationKind(StrEnum):
    PRECONDITION = "PRECONDITION"
    INTERACTION = "INTERACTION"
    OBSERVATION = "OBSERVATION"
    STATE_TRANSITION = "STATE_TRANSITION"
    OUTCOME = "OUTCOME"


class OperationResponsibility(StrEnum):
    COORDINATE = "COORDINATE"
    QUERY = "QUERY"
    MUTATE = "MUTATE"


class StateEffectOperation(StrEnum):
    SET = "SET"
    UPDATE = "UPDATE"
    INCREMENT = "INCREMENT"
    DECREMENT = "DECREMENT"
    CREATE = "CREATE"
    DELETE = "DELETE"
    DERIVE = "DERIVE"


class ScenarioObligation(Contract):
    obligation_id: str = Field(alias="obligationId", min_length=1)
    source_refs: tuple[str, ...] = Field(alias="sourceRefs", min_length=1)
    kind: ObligationKind
    expected: str = Field(min_length=1)
    state_ref: str | None = Field(default=None, alias="stateRef", min_length=1)
    condition: str | None = None

    @model_validator(mode="after")
    def state_obligations_name_state(self) -> ScenarioObligation:
        if self.kind in {
            ObligationKind.OBSERVATION,
            ObligationKind.STATE_TRANSITION,
        } and not self.state_ref:
            raise ValueError("state observation/transition requires stateRef")
        return self


class StateEffect(Contract):
    effect_id: str = Field(alias="effectId", min_length=1)
    state_ref: str = Field(alias="stateRef", min_length=1)
    operation: StateEffectOperation
    execution_owner: str = Field(alias="executionOwner", min_length=1)
    value: str | int | float | bool | None = None
    delta: int | float | None = None
    condition: str | None = None

    @model_validator(mode="after")
    def operands_match_operation(self) -> StateEffect:
        if self.operation in {
            StateEffectOperation.INCREMENT,
            StateEffectOperation.DECREMENT,
        }:
            if self.delta is None or self.delta <= 0:
                raise ValueError("increment/decrement effect requires a positive delta")
        elif self.delta is not None:
            raise ValueError("delta is only valid for increment/decrement")
        if self.operation is StateEffectOperation.SET and self.value is None:
            raise ValueError("SET effect requires value")
        if self.operation is not StateEffectOperation.SET and self.value is not None:
            raise ValueError("value is only valid for SET")
        return self


class OperationSemantics(Contract):
    operation_ref: str = Field(alias="operationRef", min_length=1)
    responsibility: OperationResponsibility
    realizes: tuple[str, ...] = Field(min_length=1)
    observes: tuple[str, ...] = ()
    effects: tuple[StateEffect, ...] = ()
    delegates: tuple[str, ...] = ()
    outcomes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def responsibility_has_a_single_effect_style(self) -> OperationSemantics:
        if self.responsibility is OperationResponsibility.COORDINATE:
            if self.effects or self.observes:
                raise ValueError("COORDINATE cannot directly observe or mutate state")
        elif self.responsibility is OperationResponsibility.QUERY:
            if (
                self.delegates
                or not self.observes
                or any(
                    effect.operation is not StateEffectOperation.DERIVE
                    for effect in self.effects
                )
            ):
                raise ValueError(
                    "QUERY requires observations, optional DERIVE effects, and no delegates"
                )
        elif (
            not self.effects
            or self.delegates
            or all(
                effect.operation is StateEffectOperation.DERIVE
                for effect in self.effects
            )
        ):
            raise ValueError(
                "MUTATE requires a state-changing effect and cannot delegate"
            )
        return self

    @property
    def reads(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.observes))

    @property
    def writes(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                effect.state_ref
                for effect in self.effects
                if effect.operation is not StateEffectOperation.DERIVE
            )
        )


class LocalSemanticContract(Contract):
    use_case_id: str = Field(alias="useCaseId", min_length=1)
    obligations: tuple[ScenarioObligation, ...] = Field(min_length=1)
    operations: tuple[OperationSemantics, ...] = Field(min_length=1)
    assumptions: tuple[str, ...] = ()


class ValidatedLocalSemanticContract(RevisionedDraft):
    status: Literal[StageStatus.VALIDATED] = StageStatus.VALIDATED
    use_case_id: str = Field(alias="useCaseId", min_length=1)
    fragment_digest: str = Field(alias="fragmentDigest", min_length=1)
    scenario_digest: str = Field(alias="scenarioDigest", min_length=1)
    inventory_digest: str = Field(alias="inventoryDigest", min_length=1)
    owner_roles: dict[str, Literal["Boundary", "Control", "Entity"]] = Field(
        alias="ownerRoles"
    )
    contract: LocalSemanticContract

    @model_validator(mode="after")
    def provenance_matches_contract(self) -> ValidatedLocalSemanticContract:
        if not provenance_matches_subject(self.provenance, _semantics_subject(self)):
            raise ValueError("local semantic provenance does not match its content")
        return self


class LocallySealedOperationFragment(RevisionedDraft):
    status: Literal[StageStatus.SEALED_UNDER_CONTRACT] = (
        StageStatus.SEALED_UNDER_CONTRACT
    )
    use_case_id: str = Field(alias="useCaseId", min_length=1)
    fragment: ValidatedOperationFragment
    semantics: ValidatedLocalSemanticContract
    review: AdjudicatedSemanticReview

    @model_validator(mode="after")
    def sealed_inputs_remain_intact(self) -> LocallySealedOperationFragment:
        if self.use_case_id != self.fragment.use_case_id:
            raise ValueError("seal and fragment useCaseId differ")
        if self.use_case_id != self.semantics.use_case_id:
            raise ValueError("seal and semantic contract useCaseId differ")
        if not provenance_matches_subject(
            self.fragment.provenance, self.fragment.payload
        ):
            raise ValueError("sealed fragment changed after validation")
        if self.semantics.fragment_digest != canonical_digest(self.fragment.payload):
            raise ValueError("sealed semantics refer to another fragment")
        if not provenance_matches_subject(
            self.semantics.provenance, _semantics_subject(self.semantics)
        ):
            raise ValueError("sealed semantic contract changed after validation")
        if self.review.subject_digest != canonical_digest(local_review_subject(
            self.fragment, self.semantics
        )):
            raise ValueError("local review does not cover the sealed contract")
        if self.review.blocking_findings or self.review.unresolved_findings:
            raise ValueError("local seal requires no confirmed or unresolved findings")
        if not provenance_matches_subject(self.provenance, _seal_subject(self)):
            raise ValueError("local seal provenance does not match its content")
        return self


class LocalContractEscape(Contract):
    use_case_id: str = Field(alias="useCaseId", min_length=1)
    target_ref: str = Field(alias="targetRef", min_length=1)
    reason: str = Field(min_length=1)


class LocalSemanticValidationError(ValueError):
    def __init__(self, findings: Iterable[str]):
        self.findings = tuple(findings)
        super().__init__("local semantic validation failed: " + "; ".join(self.findings))


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _fragment_operations(
    fragment: ValidatedOperationFragment,
) -> dict[str, tuple[str, Mapping[str, Any]]]:
    result: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for class_set in fragment.payload.get("Classes", []) or []:
        if not isinstance(class_set, Mapping):
            continue
        owner = _text(class_set.get("className") or class_set.get("name"))
        for operation in class_set.get("operations", []) or []:
            if not isinstance(operation, Mapping):
                continue
            reference = canonical_operation_id(
                owner,
                _text(operation.get("name")),
                operation.get("parameters", []) or [],
            )
            result[reference] = owner, operation
    return result


def _scenario_source_refs(
    scenario: Mapping[str, Any], use_case_id: str
) -> set[str]:
    result: set[str] = set()
    use_cases = scenario.get("useCases") or scenario.get("use_cases") or ()
    for use_case in use_cases:
        if not isinstance(use_case, Mapping):
            continue
        owner = _text(use_case.get("id") or use_case.get("useCaseId"))
        if owner != use_case_id:
            continue
        for step in use_case.get("steps") or ():
            if isinstance(step, Mapping):
                result.add(_text(step.get("id") or step.get("stepId")))
        for key in (
            "preconditions",
            "successGuarantees",
            "minimalGuarantees",
            "outcomes",
        ):
            for item in use_case.get(key) or ():
                result.add(
                    _text(item.get("id") or item.get("ref"))
                    if isinstance(item, Mapping)
                    else _text(item)
                )
    return {item for item in result if item}


def _entity_state_refs(
    inventory: Mapping[str, Any],
) -> tuple[set[str], dict[str, str]]:
    entities: set[str] = set()
    owner_by_state: dict[str, str] = {}
    for class_item in inventory.get("Classes", []) or []:
        if not isinstance(class_item, Mapping):
            continue
        owner = _text(class_item.get("className") or class_item.get("name"))
        role = _text(class_item.get("stereotype") or class_item.get("kind"))
        if role.casefold() != "entity":
            continue
        entities.add(owner)
        owner_by_state[owner] = owner
        for field in class_item.get("fields", []) or []:
            if isinstance(field, Mapping):
                field_name = _text(field.get("name"))
            else:
                field_name = _text(field).partition(":")[0].strip()
            if field_name:
                owner_by_state[f"{owner}.{field_name}"] = owner
    return entities, owner_by_state


def _semantics_subject(value: ValidatedLocalSemanticContract) -> dict[str, Any]:
    return {
        "useCaseId": value.use_case_id,
        "fragmentDigest": value.fragment_digest,
        "scenarioDigest": value.scenario_digest,
        "inventoryDigest": value.inventory_digest,
        "ownerRoles": value.owner_roles,
        "contract": value.contract.model_dump(by_alias=True),
    }


def local_review_subject(
    fragment: ValidatedOperationFragment,
    semantics: ValidatedLocalSemanticContract,
) -> dict[str, Any]:
    return {
        "reviewRubricVersion": "local-behavior-v4-bce-role-duplicate",
        "ownerRoles": semantics.owner_roles,
        "responsibilityPolicy": {
            "Boundary": (
                "COORDINATE actor entry/exit and may return a result DTO; "
                "it does not own durable state"
            ),
            "Control": (
                "COORDINATE workflow and may return a result DTO; it does not "
                "own durable state"
            ),
            "EntityQuery": "QUERY observes Entity-owned durable state",
            "EntityMutation": (
                "MUTATE owns explicit non-DERIVE effects on the same Entity"
            ),
        },
        "preconditionPolicy": (
            "A precondition is assumed or checked within an existing operation; "
            "it does not require a standalone operation unless a scenario step "
            "explicitly performs it."
        ),
        "coordinationHandoffPolicy": (
            "Realizing an obligation means contributing to its execution, not "
            "directly owning its state. Boundary and Control COORDINATE operations "
            "may realize the same state obligation as the Entity MUTATE operation. "
            "Distinct operations in different BCE roles are expected layering even "
            "when they contribute to the same intent or return the same type; this "
            "includes Boundary-to-Control and Control-to-Entity handoffs. They are "
            "not semantic duplicates. A semantic duplicate finding must compare at "
            "least two operations in the same BCE role whose responsibilities and "
            "contracts are substitutable. Direct state ownership is evidenced only "
            "by observes or effects."
        ),
        "fragment": fragment.model_dump(by_alias=True),
        "localContract": semantics.model_dump(by_alias=True),
    }


def local_review_evidence_index(
    fragment: ValidatedOperationFragment,
    semantics: ValidatedLocalSemanticContract,
) -> tuple[ReviewEvidenceRecord, ...]:
    """Expose only stable local contract coordinates to the local reviewer."""
    owner = fragment.use_case_id
    records: list[ReviewEvidenceRecord] = [
        ReviewEvidenceRecord(
            kind=ReviewEvidenceKind.OPERATION,
            ref=operation_ref,
            ownerIds=(owner,),
        )
        for operation_ref in sorted(_fragment_operations(fragment))
    ]
    records.extend(
        ReviewEvidenceRecord(
            kind=ReviewEvidenceKind.OBLIGATION,
            ref=obligation.obligation_id,
            ownerIds=(owner,),
        )
        for obligation in semantics.contract.obligations
    )
    records.extend(
        ReviewEvidenceRecord(
            kind=ReviewEvidenceKind.STATE_EFFECT,
            ref=effect.effect_id,
            ownerIds=(owner,),
        )
        for operation in semantics.contract.operations
        for effect in operation.effects
    )
    return tuple(records)


def _seal_subject(value: LocallySealedOperationFragment) -> dict[str, Any]:
    return {
        "useCaseId": value.use_case_id,
        "fragment": value.fragment.provenance.digest,
        "semantics": value.semantics.provenance.digest,
        "review": value.review.adjudication_provenance.digest,
    }


def validated_local_semantic_contract(
    proposal: LocalSemanticContract | Mapping[str, Any],
    *,
    fragment: ValidatedOperationFragment,
    scenario: Mapping[str, Any],
    inventory: Mapping[str, Any],
    revision: int = 1,
) -> ValidatedLocalSemanticContract:
    """Prove local obligation/effect closure against exact validated inputs."""
    contract = (
        proposal
        if isinstance(proposal, LocalSemanticContract)
        else LocalSemanticContract.model_validate(proposal)
    )
    findings: list[str] = []
    if not provenance_matches_subject(fragment.provenance, fragment.payload):
        findings.append("operation fragment changed after validation")
    if contract.use_case_id != fragment.use_case_id:
        findings.append("semantic contract and fragment useCaseId differ")
    if fragment.provenance.input_digests.get("scenario") != canonical_digest(scenario):
        findings.append("operation fragment uses a different scenario")
    if fragment.provenance.input_digests.get("inventory") != canonical_digest(inventory):
        findings.append("operation fragment uses a different inventory")

    operations = _fragment_operations(fragment)
    inventory_roles = {
        _text(item.get("className") or item.get("name")): _text(
            item.get("stereotype") or item.get("kind")
        )
        for item in inventory.get("Classes", []) or []
        if isinstance(item, Mapping)
    }
    owner_roles: dict[str, Literal["Boundary", "Control", "Entity"]] = {}
    for owner, _ in operations.values():
        role = inventory_roles.get(owner)
        if role == "Boundary":
            owner_roles[owner] = "Boundary"
        elif role == "Control":
            owner_roles[owner] = "Control"
        elif role == "Entity":
            owner_roles[owner] = "Entity"
        else:
            findings.append(f"operation owner has no BCE role in inventory: {owner}")
    operation_step_refs = {
        _text(step_ref)
        for _, operation in operations.values()
        for step_ref in operation.get("stepRefs", []) or []
        if _text(step_ref)
    }
    operation_semantics = {item.operation_ref: item for item in contract.operations}
    if len(operation_semantics) != len(contract.operations):
        findings.append("operation semantics references must be unique")
    missing_operations = set(operations) - set(operation_semantics)
    unknown_operations = set(operation_semantics) - set(operations)
    if missing_operations:
        findings.append(
            "operations lack semantic contracts: " + ", ".join(sorted(missing_operations))
        )
    if unknown_operations:
        findings.append(
            "semantic contracts name unknown operations: "
            + ", ".join(sorted(unknown_operations))
        )

    obligations = {item.obligation_id: item for item in contract.obligations}
    if len(obligations) != len(contract.obligations):
        findings.append("obligation IDs must be unique")
    scenario_refs = _scenario_source_refs(scenario, contract.use_case_id)
    obligated_sources: set[str] = set()
    for obligation in contract.obligations:
        obligated_sources.update(obligation.source_refs)
        unknown_sources = set(obligation.source_refs) - scenario_refs
        if unknown_sources:
            findings.append(
                f"{obligation.obligation_id}: unknown scenario sources: "
                + ", ".join(sorted(unknown_sources))
            )
    missing_sources = scenario_refs - obligated_sources
    if missing_sources:
        findings.append(
            "scenario sources lack obligations: " + ", ".join(sorted(missing_sources))
        )

    entities, owner_by_state = _entity_state_refs(inventory)
    realized: set[str] = set()
    seen_effects: set[str] = set()
    for semantic in contract.operations:
        operation_entry = operations.get(semantic.operation_ref)
        unknown_obligations = set(semantic.realizes) - set(obligations)
        if unknown_obligations:
            findings.append(
                f"{semantic.operation_ref}: unknown obligations: "
                + ", ".join(sorted(unknown_obligations))
            )
        realized.update(set(semantic.realizes) & set(obligations))
        unknown_delegates = set(semantic.delegates) - set(operations)
        if unknown_delegates:
            findings.append(
                f"{semantic.operation_ref}: unknown delegates: "
                + ", ".join(sorted(unknown_delegates))
            )
        step_refs = (
            {
                _text(value)
                for value in operation_entry[1].get("stepRefs", []) or []
                if _text(value)
            }
            if operation_entry
            else set()
        )
        for obligation_id in semantic.realizes:
            realized_obligation = obligations.get(obligation_id)
            required_steps = (
                set(realized_obligation.source_refs) & operation_step_refs
                if realized_obligation
                else set()
            )
            if realized_obligation and required_steps and not (required_steps & step_refs):
                findings.append(
                    f"{semantic.operation_ref}: {obligation_id} is not linked to an "
                    "operation stepRef"
                )
        for observed in semantic.observes:
            if observed not in owner_by_state:
                findings.append(f"{semantic.operation_ref}: unknown observed state {observed}")
        for effect in semantic.effects:
            if effect.effect_id in seen_effects:
                findings.append(f"duplicate effect ID: {effect.effect_id}")
            seen_effects.add(effect.effect_id)
            state_owner = owner_by_state.get(effect.state_ref)
            if state_owner is None:
                findings.append(
                    f"{semantic.operation_ref}: unknown effect state {effect.state_ref}"
                )
            if effect.execution_owner not in entities:
                findings.append(
                    f"{semantic.operation_ref}: effect owner is not an Entity: "
                    f"{effect.execution_owner}"
                )
            if state_owner and state_owner != effect.execution_owner:
                findings.append(
                    f"{semantic.operation_ref}: effect owner does not own "
                    f"{effect.state_ref}"
                )
            if operation_entry and operation_entry[0] != effect.execution_owner:
                findings.append(
                    f"{semantic.operation_ref}: operation owner does not execute its effect"
                )
            matching_obligation = any(
                (
                    obligations[obligation_id].kind
                    is (
                        ObligationKind.OBSERVATION
                        if effect.operation is StateEffectOperation.DERIVE
                        else ObligationKind.STATE_TRANSITION
                    )
                    and obligations[obligation_id].state_ref == effect.state_ref
                )
                for obligation_id in semantic.realizes
                if obligation_id in obligations
            )
            if not matching_obligation:
                findings.append(
                    f"{semantic.operation_ref}: effect {effect.effect_id} is not linked "
                    "to a matching state obligation"
                )

    missing_obligations = set(obligations) - realized
    if missing_obligations:
        findings.append(
            "unrealized obligations: " + ", ".join(sorted(missing_obligations))
        )
    for obligation in contract.obligations:
        if obligation.kind is not ObligationKind.STATE_TRANSITION:
            continue
        matching_mutations = [
            semantic
            for semantic in contract.operations
            if obligation.obligation_id in semantic.realizes
            and semantic.responsibility is OperationResponsibility.MUTATE
            and any(
                effect.state_ref == obligation.state_ref for effect in semantic.effects
            )
        ]
        if not matching_mutations:
            findings.append(
                f"{obligation.obligation_id}: state transition has no matching Entity effect"
            )

    if findings:
        raise LocalSemanticValidationError(findings)

    subject = {
        "useCaseId": contract.use_case_id,
        "fragmentDigest": canonical_digest(fragment.payload),
        "scenarioDigest": canonical_digest(scenario),
        "inventoryDigest": canonical_digest(inventory),
        "ownerRoles": owner_roles,
        "contract": contract.model_dump(by_alias=True),
    }
    return ValidatedLocalSemanticContract(
        useCaseId=contract.use_case_id,
        fragmentDigest=canonical_digest(fragment.payload),
        scenarioDigest=canonical_digest(scenario),
        inventoryDigest=canonical_digest(inventory),
        ownerRoles=owner_roles,
        contract=contract,
        provenance=make_provenance(
            revision=revision,
            inputs={
                "fragment": fragment.model_dump(by_alias=True),
                "scenario": scenario,
                "inventory": inventory,
            },
            subject=subject,
            validator_version=LOCAL_SEMANTICS_VALIDATOR_VERSION,
        ),
    )


def seal_local_operation_fragment(
    fragment: ValidatedOperationFragment,
    semantics: ValidatedLocalSemanticContract,
    review: AdjudicatedSemanticReview,
    *,
    revision: int = 1,
) -> LocallySealedOperationFragment:
    """Seal only a locally valid contract with no live semantic finding."""
    use_case_id = fragment.use_case_id
    subject = {
        "useCaseId": use_case_id,
        "fragment": fragment.provenance.digest,
        "semantics": semantics.provenance.digest,
        "review": review.adjudication_provenance.digest,
    }
    return LocallySealedOperationFragment(
        useCaseId=use_case_id,
        fragment=fragment,
        semantics=semantics,
        review=review,
        provenance=make_provenance(
            revision=revision,
            inputs={
                "fragment": fragment.model_dump(by_alias=True),
                "semantics": semantics.model_dump(by_alias=True),
                "review": review.model_dump(by_alias=True),
            },
            subject=subject,
            validator_version=LOCAL_SEAL_VALIDATOR_VERSION,
        ),
    )


def local_contract_escapes(
    seals: Iterable[LocallySealedOperationFragment],
    catalog: ValidatedCatalogDraft,
) -> tuple[LocalContractEscape, ...]:
    """Return every local operation contract lost during global integration."""
    catalog_index = catalog_operations(catalog.payload)
    escapes: list[LocalContractEscape] = []
    for seal in seals:
        for operation_ref in _fragment_operations(seal.fragment):
            if operation_ref not in catalog_index:
                escapes.append(
                    LocalContractEscape(
                        useCaseId=seal.use_case_id,
                        targetRef=operation_ref,
                        reason="LOCAL_CONTRACT_ESCAPE: operation absent after integration",
                    )
                )
    return tuple(escapes)


def assemble_sealed_catalog(
    seals: Iterable[LocallySealedOperationFragment],
    *,
    inventory: Mapping[str, Any],
    scenario: Mapping[str, Any],
    revision: int = 1,
) -> CatalogResult:
    """Assemble only locally sealed fragments and retain every seal in provenance."""
    values = tuple(seals)
    if not values:
        raise LocalSemanticValidationError(["sealed catalog requires local seals"])
    for seal in values:
        if not isinstance(seal, LocallySealedOperationFragment):
            raise LocalSemanticValidationError(
                ["sealed catalog accepts only LocallySealedOperationFragment"]
            )
        if not provenance_matches_subject(seal.provenance, _seal_subject(seal)):
            raise LocalSemanticValidationError(
                [f"local seal changed after validation: {seal.use_case_id}"]
            )
    assembled = assemble_catalog(
        (seal.fragment for seal in values),
        inventory=inventory,
        scenario=scenario,
        revision=revision,
    )
    escapes = local_contract_escapes(values, assembled.validated)
    if escapes:
        raise LocalSemanticValidationError(item.reason for item in escapes)
    provenance = make_provenance(
        revision=revision,
        inputs={
            "scenario": scenario,
            "inventory": inventory,
            **{
                f"seal:{seal.use_case_id}": seal.model_dump(by_alias=True)
                for seal in values
            },
        },
        subject=assembled.payload,
        validator_version="executable-behavior.sealed-catalog.v1",
    )
    validated = ValidatedCatalogDraft(payload=assembled.payload, provenance=provenance)
    return CatalogResult(assembled.payload, assembled.digest, validated)


def validate_effect_call_links(
    seal: LocallySealedOperationFragment,
    calls: ValidatedCallStructure,
) -> None:
    """Require every declared mutation and delegation to appear in the call tree."""
    if not provenance_matches_subject(seal.provenance, _seal_subject(seal)):
        raise LocalSemanticValidationError(["local seal changed after validation"])
    if not provenance_matches_subject(calls.provenance, calls.payload):
        raise LocalSemanticValidationError(["call structure changed after validation"])
    if seal.use_case_id != calls.use_case_id:
        raise LocalSemanticValidationError(["seal and calls useCaseId differ"])
    call_values = [
        item for item in calls.payload.get("calls", []) or [] if isinstance(item, Mapping)
    ]
    called_operations = {
        _text(item.get("receiverOperationId")) for item in call_values
    }
    call_by_id = {_text(item.get("callId")): item for item in call_values}
    findings: list[str] = []
    for semantic in seal.semantics.contract.operations:
        if (
            semantic.responsibility is OperationResponsibility.MUTATE
            and semantic.operation_ref not in called_operations
        ):
            findings.append(
                f"effect operation is not executed by a call: {semantic.operation_ref}"
            )
        for delegate in semantic.delegates:
            linked = any(
                _text(parent.get("receiverOperationId")) == semantic.operation_ref
                and _text(child.get("receiverOperationId")) == delegate
                and _text(child.get("parentCallId")) == parent_id
                for parent_id, parent in call_by_id.items()
                for child in call_values
            )
            if not linked:
                findings.append(
                    f"declared delegation is absent from calls: "
                    f"{semantic.operation_ref} -> {delegate}"
                )
    if findings:
        raise LocalSemanticValidationError(findings)


__all__ = [
    "LOCAL_SEAL_VALIDATOR_VERSION",
    "LOCAL_SEMANTICS_VALIDATOR_VERSION",
    "LocalContractEscape",
    "LocalSemanticContract",
    "LocalSemanticValidationError",
    "LocallySealedOperationFragment",
    "ObligationKind",
    "OperationResponsibility",
    "OperationSemantics",
    "ScenarioObligation",
    "StateEffect",
    "StateEffectOperation",
    "ValidatedLocalSemanticContract",
    "assemble_sealed_catalog",
    "local_contract_escapes",
    "local_review_evidence_index",
    "local_review_subject",
    "seal_local_operation_fragment",
    "validate_effect_call_links",
    "validated_local_semantic_contract",
]
