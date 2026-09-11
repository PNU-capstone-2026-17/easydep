"""Isolated, in-memory runner for validating the proposed behavior methodology."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.design.schemas.class_model import BCEModel
from app.design.services.executable_behavior.bindings import (
    BindingSelector,
    Source,
    validated_binding_plan,
)
from app.design.services.executable_behavior.calls import (
    ActorEntry,
    CallProposal,
    validated_call_structure,
)
from app.design.services.executable_behavior.catalog import (
    CatalogResult,
)
from app.design.services.executable_behavior.contracts import (
    AcceptedBehaviorModel,
    ValidatedBindingPlan,
    ValidatedCallStructure,
    ValidatedExecutionWitness,
    ValidatedOperationFragment,
)
from app.design.services.executable_behavior.materialize import materialize_bce_model
from app.design.services.executable_behavior.operations import (
    OperationContext,
    validated_operation_fragment,
)
from app.design.services.executable_behavior.orchestration import accept_behavior
from app.design.services.executable_behavior.reviews import (
    AdjudicatedSemanticReview,
    SemanticReviewAdjudicationProposal,
    SemanticReviewProposal,
    SemanticReviewStage,
    validated_review_adjudication,
    validated_semantic_review,
)
from app.design.services.executable_behavior.semantics import (
    LocallySealedOperationFragment,
    LocalSemanticContract,
    ValidatedLocalSemanticContract,
    assemble_sealed_catalog,
    local_review_evidence_index,
    local_review_subject,
    seal_local_operation_fragment,
    validate_effect_call_links,
    validated_local_semantic_contract,
)
from app.design.services.executable_behavior.witness import validate_execution_witness


@dataclass(frozen=True)
class UseCaseDraft:
    use_case_id: str
    allowed_step_ids: tuple[str, ...]
    actor_entries: tuple[ActorEntry | Mapping[str, Any], ...]
    operation_fragment: Mapping[str, Any]
    local_semantic_contract: LocalSemanticContract | Mapping[str, Any]
    local_review: SemanticReviewProposal | Mapping[str, Any]
    local_adjudication: SemanticReviewAdjudicationProposal | Mapping[str, Any]
    call_proposals: tuple[CallProposal | Mapping[str, Any], ...]
    sources: tuple[Source | Mapping[str, Any], ...]
    durable_entity_names: tuple[str, ...] = ()
    allowed_owner_names: tuple[str, ...] = ()
    binding_selections: Mapping[tuple[str, str], str] | None = None


@dataclass(frozen=True)
class PrototypeResult:
    operation_fragments: tuple[ValidatedOperationFragment, ...]
    local_semantic_contracts: tuple[ValidatedLocalSemanticContract, ...]
    local_reviews: tuple[AdjudicatedSemanticReview, ...]
    local_seals: tuple[LocallySealedOperationFragment, ...]
    catalog: CatalogResult
    call_structures: tuple[ValidatedCallStructure, ...]
    binding_plans: tuple[ValidatedBindingPlan, ...]
    witnesses: tuple[ValidatedExecutionWitness, ...]
    accepted: AcceptedBehaviorModel
    bce_model: BCEModel


def _scenario_use_cases(scenario: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    values = scenario.get("useCases") or scenario.get("use_cases") or ()
    result: dict[str, Mapping[str, Any]] = {}
    for item in values:
        if not isinstance(item, Mapping):
            raise TypeError("scenario use cases must be objects")
        use_case_id = str(item.get("useCaseId") or item.get("id") or "")
        if not use_case_id or use_case_id in result:
            raise ValueError("scenario use-case identities must be non-empty and unique")
        result[use_case_id] = item
    if not result:
        raise ValueError("prototype scenario requires explicit useCases")
    return result


def _scenario_steps(use_case: Mapping[str, Any]) -> set[str]:
    values = use_case.get("steps") or ()
    return {
        str(item.get("stepId") or item.get("id") or "")
        for item in values
        if isinstance(item, Mapping)
        and str(item.get("stepId") or item.get("id") or "")
    }


def _entry_steps(entry: ActorEntry | Mapping[str, Any]) -> tuple[str, ...]:
    if isinstance(entry, ActorEntry):
        return entry.required_step_refs
    return tuple(
        str(item)
        for item in (
            entry.get("requiredStepRefs") or entry.get("required_step_refs") or ()
        )
    )


def validate_behavior_prototype(
    *,
    scenario: Mapping[str, Any],
    inventory: Mapping[str, Any],
    drafts: tuple[UseCaseDraft, ...],
    binding_selector: BindingSelector | None = None,
    revision: int = 1,
) -> PrototypeResult:
    """Run every contract boundary without graph, persistence, or provider calls."""
    use_case_ids = [draft.use_case_id for draft in drafts]
    if not drafts or len(use_case_ids) != len(set(use_case_ids)):
        raise ValueError("prototype requires unique use-case drafts")
    scenario_use_cases = _scenario_use_cases(scenario)
    if set(use_case_ids) != set(scenario_use_cases):
        raise ValueError("drafts must exactly cover the fixed scenario use cases")
    for draft in drafts:
        allowed_steps = set(draft.allowed_step_ids)
        scenario_steps = _scenario_steps(scenario_use_cases[draft.use_case_id])
        if not allowed_steps or (scenario_steps and allowed_steps != scenario_steps):
            raise ValueError(
                f"allowed steps do not match the scenario: {draft.use_case_id}"
            )
        entry_steps = {
            step
            for entry in draft.actor_entries
            for step in _entry_steps(entry)
        }
        if entry_steps != allowed_steps:
            raise ValueError(
                f"actor entries must exactly cover allowed steps: {draft.use_case_id}"
            )

    operation_fragments = tuple(
        validated_operation_fragment(
            draft.operation_fragment,
            OperationContext.from_payload(
                draft.use_case_id,
                inventory,
                scenario=scenario,
                allowed_step_ids=draft.allowed_step_ids,
                durable_entity_names=draft.durable_entity_names,
                allowed_owner_names=draft.allowed_owner_names,
                revision=revision,
            ),
        )
        for draft in drafts
    )
    local_semantic_contracts: list[ValidatedLocalSemanticContract] = []
    local_reviews: list[AdjudicatedSemanticReview] = []
    local_seals: list[LocallySealedOperationFragment] = []
    for draft, fragment in zip(drafts, operation_fragments, strict=True):
        semantics = validated_local_semantic_contract(
            draft.local_semantic_contract,
            fragment=fragment,
            scenario=scenario,
            inventory=inventory,
            revision=revision,
        )
        review_subject = local_review_subject(fragment, semantics)
        evidence_index = local_review_evidence_index(fragment, semantics)
        raw_review = validated_semantic_review(
            draft.local_review,
            stage=SemanticReviewStage.BEHAVIOR,
            subject=review_subject,
            allowed_owner_ids=(draft.use_case_id,),
            evidence_index=evidence_index,
            revision=revision,
        )
        review = validated_review_adjudication(
            draft.local_adjudication,
            review=raw_review,
            subject=review_subject,
            evidence_index=evidence_index,
            revision=revision,
        )
        seal = seal_local_operation_fragment(
            fragment, semantics, review, revision=revision
        )
        local_semantic_contracts.append(semantics)
        local_reviews.append(review)
        local_seals.append(seal)

    catalog = assemble_sealed_catalog(
        local_seals,
        inventory=inventory,
        scenario=scenario,
        revision=revision,
    )

    call_structures: list[ValidatedCallStructure] = []
    binding_plans: list[ValidatedBindingPlan] = []
    witnesses: list[ValidatedExecutionWitness] = []
    for draft in drafts:
        calls = validated_call_structure(
            catalog.validated,
            scenario=scenario,
            use_case_id=draft.use_case_id,
            proposals=draft.call_proposals,
            actor_entries=draft.actor_entries,
            revision=revision,
        )
        seal = local_seals[use_case_ids.index(draft.use_case_id)]
        validate_effect_call_links(seal, calls)
        bindings = validated_binding_plan(
            catalog.validated,
            calls,
            scenario=scenario,
            sources=draft.sources,
            selector=binding_selector,
            selections=draft.binding_selections,
            revision=revision,
        )
        witness = validate_execution_witness(
            catalog.validated, calls, bindings, revision=revision
        )
        call_structures.append(calls)
        binding_plans.append(bindings)
        witnesses.append(witness)

    accepted = accept_behavior(
        catalog.validated,
        witnesses,
        required_use_case_ids=use_case_ids,
        revision=revision,
    )
    return PrototypeResult(
        operation_fragments,
        tuple(local_semantic_contracts),
        tuple(local_reviews),
        tuple(local_seals),
        catalog,
        tuple(call_structures),
        tuple(binding_plans),
        tuple(witnesses),
        accepted,
        materialize_bce_model(accepted),
    )


__all__ = ["PrototypeResult", "UseCaseDraft", "validate_behavior_prototype"]
