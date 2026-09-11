"""In-memory acceptance transaction for executable behavior drafts."""
from __future__ import annotations

from collections.abc import Iterable

from app.design.services.executable_behavior.contracts import (
    AcceptedBehaviorModel,
    ValidatedCatalogDraft,
    ValidatedExecutionWitness,
    canonical_digest,
    make_provenance,
    provenance_matches_subject,
)


class AcceptanceError(ValueError):
    """No partial result is returned when an acceptance invariant fails."""


def accept_behavior(
    catalog: ValidatedCatalogDraft,
    witnesses: Iterable[ValidatedExecutionWitness],
    *, required_use_case_ids: Iterable[str], revision: int | None = None,
) -> AcceptedBehaviorModel:
    """Atomically assemble one catalog and exactly one current witness per UC."""
    if not provenance_matches_subject(catalog.provenance, catalog.payload):
        raise AcceptanceError("catalog content changed after validation")
    expected = set(required_use_case_ids)
    ordered = tuple(sorted(witnesses, key=lambda item: item.use_case_id))
    actual = {item.use_case_id for item in ordered}
    if not expected:
        raise AcceptanceError("acceptance requires at least one use case")
    if actual != expected or len(actual) != len(ordered):
        raise AcceptanceError("accepted witnesses must exactly cover the required use cases")
    catalog_digest = canonical_digest(catalog.payload)
    if any(item.catalog_digest != catalog_digest for item in ordered):
        raise AcceptanceError("witness was validated against a different catalog revision")
    catalog_snapshot_digest = canonical_digest(catalog.model_dump(by_alias=True))
    if any(
        item.provenance.input_digests.get("catalog") != catalog_snapshot_digest
        for item in ordered
    ):
        raise AcceptanceError("witness uses a different catalog validation snapshot")
    scenario_digest = catalog.provenance.input_digests.get("scenario")
    if not scenario_digest or any(item.scenario_digest != scenario_digest for item in ordered):
        raise AcceptanceError("witness was validated against a different scenario snapshot")
    for item in ordered:
        witness_subject = {
            "useCaseId": item.use_case_id,
            "actorEntries": list(item.actor_entries),
            "calls": list(item.calls),
            "bindings": list(item.bindings),
        }
        if not provenance_matches_subject(item.provenance, witness_subject):
            raise AcceptanceError(
                f"witness content changed after validation: {item.use_case_id}"
            )
    accepted_revision = revision or max(catalog.provenance.revision, *(item.provenance.revision for item in ordered))
    subject = {
        "catalog": catalog_digest,
        "witnesses": [item.provenance.digest for item in ordered],
    }
    return AcceptedBehaviorModel(
        catalog=catalog,
        witnesses=ordered,
        provenance=make_provenance(
            revision=accepted_revision,
            inputs={
                "catalog": catalog.payload,
                **{
                    f"witness:{item.use_case_id}": item.model_dump(by_alias=True)
                    for item in ordered
                },
            },
            subject=subject,
        ),
    )


__all__ = ["AcceptanceError", "accept_behavior"]
