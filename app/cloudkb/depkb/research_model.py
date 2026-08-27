"""Evidence-first resource model contracts independent of the legacy claim vocabulary."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NativeObservation:
    """Raw control-plane observation; no resource type is required at collection time."""

    provider: str
    native_id: str
    source_identity: str
    source_version: str
    source_locator: str
    identity_fields: tuple[str, ...] = ()
    crud_operations: tuple[str, ...] = ()
    parent_path: str | None = None
    independently_readable: bool | None = None
    survives_parent_update: bool | None = None
    detachable: bool | None = None
    independently_deletable: bool | None = None
    lifecycle_owner: str | None = None
    embedded_in: str | None = None
    provider_created: bool | None = None
    connection_manager: str | None = None


@dataclass(frozen=True)
class ResourceTypeAssignment:
    """A derived type assignment tied to a codebook version and reviewer decision."""

    provider: str
    native_id: str
    derived_type: str
    codebook_version: str
    reviewer_a: str
    reviewer_b: str
    adjudication: str | None = None


@dataclass(frozen=True)
class Realization:
    id: str
    provider: str
    capabilities: frozenset[str]
    elements: frozenset[str]
    constraints: dict[str, Any] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()
    status: str = "confirmed"


def _constraints_match(realization: Realization, requested: dict[str, Any]) -> bool:
    return all(
        key not in realization.constraints or realization.constraints[key] == value
        for key, value in requested.items()
    )


def minimum_realizations(
    capability_set: Iterable[str], provider: str, constraints: dict[str, Any],
    candidates: Iterable[Realization],
) -> tuple[Realization, ...]:
    """Return every inclusion-minimal confirmed alternative, never a count-minimum."""
    required = frozenset(capability_set)
    eligible = [
        item for item in candidates
        if item.provider == provider
        and item.status == "confirmed"
        and required <= item.capabilities
        and _constraints_match(item, constraints)
    ]
    minimal = [
        item for item in eligible
        if not any(other.elements < item.elements for other in eligible)
    ]
    return tuple(sorted(minimal, key=lambda item: item.id))


def validate_observation(value: dict[str, Any]) -> None:
    """Validate raw evidence without imposing a predefined resource-form taxonomy."""
    required = ("provider", "nativeId", "sourceIdentity", "sourceVersion", "sourceLocator")
    missing = [name for name in required if not str(value.get(name) or "").strip()]
    if missing:
        raise ValueError("missing native observation fields: " + ", ".join(missing))
    forbidden = {"nativeForm", "neutralId", "capability", "derivedType"} & set(value)
    if forbidden:
        raise ValueError(
            "raw observation contains premature classification: " + ", ".join(sorted(forbidden))
        )
