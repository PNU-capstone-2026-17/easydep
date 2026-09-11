"""Typed, in-memory contracts for the executable-behavior prototype.

These objects deliberately are not persistence schemas.  ``VALIDATED`` means
only the local contract passed; a behavior becomes ``ACCEPTED`` only when its
catalog and every use-case witness are committed together.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

VALIDATOR_VERSION = "executable-behavior/v1"


class StageStatus(StrEnum):
    PROVISIONAL = "PROVISIONAL"
    VALIDATED = "VALIDATED"
    SEALED_UNDER_CONTRACT = "SEALED_UNDER_CONTRACT"
    ACCEPTED = "ACCEPTED"


def canonical_digest(value: Any) -> str:
    """Return a content digest stable across mapping insertion order."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class Contract(BaseModel):
    """Strict base class for prototype boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class Provenance(Contract):
    """Inputs and validator identity that make a validated result reproducible."""

    revision: int = Field(ge=1)
    input_digests: dict[str, str] = Field(alias="inputDigests", min_length=1)
    validator_version: str = Field(alias="validatorVersion", min_length=1)
    digest: str = Field(min_length=1)


def make_provenance(
    *, revision: int, inputs: Mapping[str, Any], subject: Any,
    validator_version: str = VALIDATOR_VERSION,
) -> Provenance:
    """Freeze a subject's exact input set and normalized content identity."""
    input_digests = {name: canonical_digest(value) for name, value in sorted(inputs.items())}
    return Provenance(
        revision=revision,
        inputDigests=input_digests,
        validatorVersion=validator_version,
        digest=canonical_digest({
            "inputs": input_digests,
            "revision": revision,
            "subject": subject,
            "validator": validator_version,
        }),
    )


def provenance_matches_subject(provenance: Provenance, subject: Any) -> bool:
    """Check that a validated wrapper still contains the exact validated content."""
    expected = canonical_digest({
        "inputs": provenance.input_digests,
        "revision": provenance.revision,
        "subject": subject,
        "validator": provenance.validator_version,
    })
    return provenance.digest == expected


class RevisionedDraft(Contract):
    status: StageStatus
    provenance: Provenance


class ValidatedOperationFragment(RevisionedDraft):
    status: Literal[StageStatus.VALIDATED] = StageStatus.VALIDATED
    use_case_id: str = Field(alias="useCaseId", min_length=1)
    payload: dict[str, Any]


class ValidatedCatalogDraft(RevisionedDraft):
    status: Literal[StageStatus.VALIDATED] = StageStatus.VALIDATED
    payload: dict[str, Any]


class ValidatedCallStructure(RevisionedDraft):
    status: Literal[StageStatus.VALIDATED] = StageStatus.VALIDATED
    use_case_id: str = Field(alias="useCaseId", min_length=1)
    payload: dict[str, Any]


class ValidatedBindingPlan(RevisionedDraft):
    status: Literal[StageStatus.VALIDATED] = StageStatus.VALIDATED
    use_case_id: str = Field(alias="useCaseId", min_length=1)
    payload: dict[str, Any]


class ValidatedExecutionWitness(RevisionedDraft):
    """A concrete UC call-and-binding execution checked against one catalog."""

    status: Literal[StageStatus.VALIDATED] = StageStatus.VALIDATED
    use_case_id: str = Field(alias="useCaseId", min_length=1)
    catalog_digest: str = Field(alias="catalogDigest", min_length=1)
    scenario_digest: str = Field(alias="scenarioDigest", min_length=1)
    call_structure_digest: str = Field(alias="callStructureDigest", min_length=1)
    binding_plan_digest: str = Field(alias="bindingPlanDigest", min_length=1)
    actor_entries: tuple[dict[str, Any], ...] = Field(alias="actorEntries")
    calls: tuple[dict[str, Any], ...]
    bindings: tuple[dict[str, Any], ...]


class AcceptedBehaviorModel(RevisionedDraft):
    """The sole in-memory commit boundary for this prototype."""

    status: Literal[StageStatus.ACCEPTED] = StageStatus.ACCEPTED
    catalog: ValidatedCatalogDraft
    witnesses: tuple[ValidatedExecutionWitness, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def witnesses_are_unique_and_match_catalog(self) -> AcceptedBehaviorModel:
        if not provenance_matches_subject(self.catalog.provenance, self.catalog.payload):
            raise ValueError("accepted catalog content changed after validation")
        use_case_ids = [item.use_case_id for item in self.witnesses]
        if len(use_case_ids) != len(set(use_case_ids)):
            raise ValueError("an accepted behavior has one witness per use case")
        catalog_digest = canonical_digest(self.catalog.payload)
        if any(item.catalog_digest != catalog_digest for item in self.witnesses):
            raise ValueError("all witnesses must use the accepted catalog digest")
        catalog_snapshot_digest = canonical_digest(
            self.catalog.model_dump(by_alias=True)
        )
        if any(
            item.provenance.input_digests.get("catalog") != catalog_snapshot_digest
            for item in self.witnesses
        ):
            raise ValueError(
                "all witnesses must use the accepted catalog validation snapshot"
            )
        scenario_digest = self.catalog.provenance.input_digests.get("scenario")
        if not scenario_digest or any(item.scenario_digest != scenario_digest for item in self.witnesses):
            raise ValueError("all witnesses must use the catalog scenario snapshot")
        for item in self.witnesses:
            witness_subject = {
                "useCaseId": item.use_case_id,
                "actorEntries": list(item.actor_entries),
                "calls": list(item.calls),
                "bindings": list(item.bindings),
            }
            if not provenance_matches_subject(item.provenance, witness_subject):
                raise ValueError(
                    f"accepted witness content changed after validation: {item.use_case_id}"
                )
        accepted_subject = {
            "catalog": catalog_digest,
            "witnesses": [item.provenance.digest for item in self.witnesses],
        }
        if not provenance_matches_subject(self.provenance, accepted_subject):
            raise ValueError("accepted behavior provenance does not match its content")
        return self


__all__ = [
    "VALIDATOR_VERSION",
    "AcceptedBehaviorModel",
    "Contract",
    "Provenance",
    "StageStatus",
    "ValidatedBindingPlan",
    "ValidatedCallStructure",
    "ValidatedCatalogDraft",
    "ValidatedExecutionWitness",
    "ValidatedOperationFragment",
    "canonical_digest",
    "make_provenance",
    "provenance_matches_subject",
]
