"""Cross-stage validation of one concrete UC call-and-binding execution."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.design.services.executable_behavior.bindings import (
    BindingSelectionError,
    validate_binding_plan,
)
from app.design.services.executable_behavior.calls import (
    CallStructureBuilder,
    CallValidationError,
    catalog_operations,
)
from app.design.services.executable_behavior.contracts import (
    ValidatedBindingPlan,
    ValidatedCallStructure,
    ValidatedCatalogDraft,
    ValidatedExecutionWitness,
    canonical_digest,
    make_provenance,
    provenance_matches_subject,
)


class WitnessValidationError(ValueError):
    """A concrete execution does not satisfy every lower-stage contract."""


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def validate_execution_witness(
    catalog: ValidatedCatalogDraft,
    call_structure: ValidatedCallStructure,
    binding_plan: ValidatedBindingPlan,
    *,
    revision: int | None = None,
) -> ValidatedExecutionWitness:
    """Revalidate the complete execution instead of trusting `VALIDATED` labels."""
    if not provenance_matches_subject(catalog.provenance, catalog.payload):
        raise WitnessValidationError("catalog content changed after validation")
    if not provenance_matches_subject(
        call_structure.provenance, call_structure.payload
    ):
        raise WitnessValidationError("call structure changed after validation")
    if not provenance_matches_subject(binding_plan.provenance, binding_plan.payload):
        raise WitnessValidationError("binding plan changed after validation")
    if call_structure.use_case_id != binding_plan.use_case_id:
        raise WitnessValidationError(
            "call structure and binding plan own different use cases"
        )
    scenario_digest = catalog.provenance.input_digests.get("scenario")
    if not scenario_digest:
        raise WitnessValidationError("catalog provenance must pin a scenario snapshot")
    if call_structure.provenance.input_digests.get("scenario") != scenario_digest:
        raise WitnessValidationError("call structure uses a different scenario snapshot")
    if binding_plan.provenance.input_digests.get("scenario") != scenario_digest:
        raise WitnessValidationError("binding plan uses a different scenario snapshot")
    if call_structure.provenance.input_digests.get("catalog") != canonical_digest(
        catalog.model_dump(by_alias=True)
    ):
        raise WitnessValidationError("call structure uses a different catalog snapshot")

    raw_calls = _mapping_list(call_structure.payload.get("calls"))
    actor_entries = _mapping_list(call_structure.payload.get("actorEntries"))
    try:
        rebuilt = CallStructureBuilder(catalog_operations(catalog.payload)).build(
            call_structure.use_case_id,
            raw_calls,
            actor_entries=actor_entries,
        )
    except CallValidationError as error:
        raise WitnessValidationError(str(error)) from error
    if canonical_digest(rebuilt.as_payload()) != canonical_digest(call_structure.payload):
        raise WitnessValidationError("call structure is not its canonical validated form")

    try:
        normalized_bindings = validate_binding_plan(
            catalog, call_structure, binding_plan
        )
    except BindingSelectionError as error:
        raise WitnessValidationError(str(error)) from error

    calls = tuple(_mapping_list(call_structure.payload.get("calls")))
    witness_revision = revision or max(
        catalog.provenance.revision,
        call_structure.provenance.revision,
        binding_plan.provenance.revision,
    )
    subject = {
        "useCaseId": call_structure.use_case_id,
        "actorEntries": actor_entries,
        "calls": calls,
        "bindings": normalized_bindings,
    }
    return ValidatedExecutionWitness(
        useCaseId=call_structure.use_case_id,
        catalogDigest=canonical_digest(catalog.payload),
        scenarioDigest=scenario_digest,
        callStructureDigest=canonical_digest(call_structure.payload),
        bindingPlanDigest=canonical_digest(binding_plan.payload),
        actorEntries=tuple(actor_entries),
        calls=calls,
        bindings=normalized_bindings,
        provenance=make_provenance(
            revision=witness_revision,
            inputs={
                "catalog": catalog.model_dump(by_alias=True),
                "calls": call_structure.model_dump(by_alias=True),
                "bindings": binding_plan.model_dump(by_alias=True),
            },
            subject=subject,
        ),
    )


__all__ = ["WitnessValidationError", "validate_execution_witness"]
