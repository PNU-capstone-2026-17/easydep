"""Bounded trusted-context provenance derived from explicit use-case evidence.

The class-design input has prose preconditions rather than a separate identity
schema.  This adapter deliberately recognises only explicit trust declarations
and only when their named subject structurally matches a typed Control parameter.  It
does not know application roles or domain class names.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.design.services.class_diagram.scenario import UseCase, text

# These are source/trust semantics, not application-domain vocabulary.  Keep
# the list here so a future structured precondition marker can replace it in one
# place without changing collaboration validation.
_TRUST_DECLARATION = re.compile(
    r"(?:\bauthenticated\b|\bauthori[sz]ed\b|\btrusted\b|"
    r"\b(?:platform|server)[ -]provided\b|\b(?:signed|logged)[ -]?in\b|"
    r"\bverified identity\b)",
    re.IGNORECASE,
)
_WORD = re.compile(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+")


@dataclass(frozen=True)
class TrustedContextSource:
    """One finite server-owned value available at a Boundary→Control handoff."""

    source_ref: str
    source_type: str
    evidence_refs: tuple[str, ...]


def _tokens(value: str) -> set[str]:
    return {
        token.casefold()
        for token in _WORD.findall(text(value))
        if token.casefold() not in {"id", "identity", "context"}
    }


def _precondition_values(use_case: UseCase) -> list[str]:
    raw = use_case.specification.get("preconditions") or []
    values = list(raw.values()) if isinstance(raw, dict) else list(raw)
    return [text(value) for value in values]


def trusted_context_sources(
    use_case: UseCase,
    parameter_name: str,
    parameter_type: str,
    *,
    actors: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> tuple[TrustedContextSource, ...]:
    """Return evidence-backed context candidates for one operation parameter.

    A precondition such as ``The customer is authenticated`` can supply a
    server-owned ``customer: CustomerIdentity`` value.  A bare actor, an
    availability condition, or a precondition that merely mentions a role does
    not create a context candidate.  Matching the declared type lets neutral
    parameter names (for example ``actor``) use an explicitly named subject
    type without recognising any domain-specific role.  Scalar IDs do not
    become trusted values merely because their name mentions that subject.
    """

    # A trusted principal is a typed subject, never an inferred scalar ID.  A
    # parameter named ``customerId`` with type UUID remains caller input unless
    # the upstream contract models a typed current-principal value.  This keeps
    # authentication evidence from silently changing caller control.
    subjects = _tokens(parameter_type)
    if not subjects:
        return ()
    result: list[TrustedContextSource] = []
    for evidence_ref, precondition in zip(use_case.precondition_refs, _precondition_values(use_case)):
        if not _TRUST_DECLARATION.search(precondition):
            continue
        if not subjects.intersection(_tokens(precondition)):
            continue
        result.append(TrustedContextSource(
            source_ref=f"context#{evidence_ref}:{parameter_name}",
            source_type=parameter_type,
            evidence_refs=(evidence_ref,),
        ))
    if result:
        return tuple(result)
    return _actor_context_sources(use_case, parameter_type, actors)


def trusted_context_evidence(
    use_case: UseCase,
    actors: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> list[dict[str, Any]]:
    """Project only declared context evidence for the operation proposer.

    No parameter is known while operations are being proposed, so this exposes
    evidence and eligibility rather than fabricating a type.  Materialization
    later turns it into a finite typed candidate only when the parameter type
    structurally matches the named subject.
    """

    result: list[dict[str, Any]] = []
    for evidence_ref, precondition in zip(use_case.precondition_refs, _precondition_values(use_case)):
        if _TRUST_DECLARATION.search(precondition):
            result.append({
                "sourceRefTemplate": f"context#{evidence_ref}:<parameterName>",
                "kind": "trusted_context",
                "evidenceRefs": [evidence_ref],
                "evidence": precondition,
                "eligibility": "typed parameter subject must match this evidence; Control handoff only",
            })
    actor_contract = _trusted_actor_contract(use_case, actors)
    if actor_contract is not None:
        primary, trusted_actor, refs, description = actor_contract
        result.append({
            "sourceRef": f"context#actor:{primary}",
            "kind": "trusted_context",
            "subject": primary,
            "evidenceRefs": [f"actor:{trusted_actor}", *refs],
            "evidence": description,
            "eligibility": "typed parameter must match the primary actor; Control handoff only",
        })
    return result


def _actor_context_sources(
    use_case: UseCase,
    parameter_type: str,
    actors: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> tuple[TrustedContextSource, ...]:
    """Resolve an explicit trusted actor contract through its parent hierarchy."""

    contract = _trusted_actor_contract(use_case, actors)
    if contract is None:
        return ()
    primary, trusted_actor, refs, _description = contract
    if not _tokens(parameter_type).intersection(_tokens(primary)):
        return ()
    return (TrustedContextSource(
        source_ref=f"context#actor:{primary}",
        source_type=parameter_type,
        evidence_refs=(f"actor:{trusted_actor}", *refs),
    ),)


def _trusted_actor_contract(
    use_case: UseCase,
    actors: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> tuple[str, str, tuple[str, ...], str] | None:
    """Find the first explicit trust declaration in the primary actor lineage."""

    by_name = {
        text(actor.get("name")).casefold(): actor
        for actor in actors
        if isinstance(actor, dict) and text(actor.get("name"))
    }
    primary = text(use_case.primary_actor)
    current = by_name.get(primary.casefold())
    if not current:
        return None
    visited: set[str] = set()
    while current:
        current_name = text(current.get("name"))
        key = current_name.casefold()
        if not key or key in visited:
            return None
        visited.add(key)
        description = text(current.get("description"))
        if _TRUST_DECLARATION.search(description):
            refs = tuple(
                str(value).strip()
                for value in current.get("source_refs", current.get("sourceRefs", [])) or []
                if str(value).strip()
            )
            return primary, current_name, refs, description
        parent = text(current.get("parent_actor", current.get("parentActor")))
        current = by_name.get(parent.casefold()) if parent else None
    return None


def is_trusted_context_ref(source_ref: str) -> bool:
    return source_ref.startswith("context#")


__all__ = [
    "TrustedContextSource",
    "is_trusted_context_ref",
    "trusted_context_evidence",
    "trusted_context_sources",
]
