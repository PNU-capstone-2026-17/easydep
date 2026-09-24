"""Trusted-context provenance from explicit accepted identity obligations.

Names, actor descriptions, prose preconditions, and parameter types have no
trust authority here. A context source exists only when a parameter explicitly
cites an accepted ``authenticate`` obligation belonging to this use case.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.design.services.class_diagram.scenario import UseCase, text


@dataclass(frozen=True)
class TrustedContextSource:
    """One finite server-owned value available at a Boundary-to-Control handoff."""

    source_ref: str
    source_type: str
    evidence_refs: tuple[str, ...]


def _authenticate_obligations(use_case: UseCase) -> tuple[dict[str, Any], ...]:
    contract = use_case.specification.get("public_contract")
    if not isinstance(contract, dict):
        return ()
    raw = contract.get("identity_obligations")
    if not isinstance(raw, list):
        return ()
    return tuple(
        item for item in raw
        if isinstance(item, dict)
        and text(item.get("obligation")) == "authenticate"
        and text(item.get("obligation_ref"))
        and text(item.get("subject_ref"))
        and isinstance(item.get("requirement_ids"), list)
    )


def trusted_context_sources(
    use_case: UseCase,
    parameter_name: str,
    parameter_type: str,
    *,
    obligation_ref: str | None = None,
    actors: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> tuple[TrustedContextSource, ...]:
    """Return the exact selected authenticate-obligation source, if valid.

    The retained parameter/actor arguments are presentation compatibility only;
    none may authorize trusted context.
    """

    selected = text(obligation_ref)
    if not selected:
        return ()
    for obligation in _authenticate_obligations(use_case):
        if text(obligation.get("obligation_ref")) != selected:
            continue
        requirement_ids = tuple(
            text(value) for value in obligation.get("requirement_ids") or [] if text(value)
        )
        return (TrustedContextSource(
            source_ref=f"context#{selected}",
            source_type=parameter_type,
            evidence_refs=(selected, *requirement_ids),
        ),)
    return ()


def trusted_context_evidence(
    use_case: UseCase,
    actors: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> list[dict[str, Any]]:
    """Project the finite obligation refs the operation LLM may select."""

    return [
        {
            "sourceRef": f"context#{text(item.get('obligation_ref'))}",
            "obligationRef": text(item.get("obligation_ref")),
            "subjectRef": text(item.get("subject_ref")),
            "kind": "trusted_context",
            "evidenceRefs": [
                text(item.get("obligation_ref")),
                *(text(value) for value in item.get("requirement_ids") or [] if text(value)),
            ],
            "eligibility": "assign this exact obligationRef only to an internal Control parameter on a Boundary-to-Control handoff",
        }
        for item in _authenticate_obligations(use_case)
    ]


def is_trusted_context_ref(source_ref: str) -> bool:
    return source_ref.startswith("context#")


__all__ = [
    "TrustedContextSource",
    "is_trusted_context_ref",
    "trusted_context_evidence",
    "trusted_context_sources",
]
