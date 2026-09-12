"""Semantic admission for bounded implementation behavior capsules."""

from __future__ import annotations

import json
from collections.abc import Callable
from textwrap import shorten
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.design.services.common.structured import parse_structured

from .upstream_gap_tool import UpstreamGap


class BehaviorAdmission(BaseModel):
    """The structured semantic admission decision returned by the proposer."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["IMPLEMENT", "NEEDS_INPUT"]
    summary: str = Field(min_length=1)
    source_ref: str


_SYSTEM_PROMPT = """You are a semantic admission judge for a bounded implementation task.
Judge meaning, not coding or wiring. Return only the requested structured decision.

Choose NEEDS_INPUT only if both conditions hold:
1. The capsule explicitly requires a user-visible decision, effect, or public outcome.
2. None of its declared inputs, prior-call results, state or trusted context, or policy
   operations can select or produce it, so implementation must invent a business rule.

A condition label is not a decision source by itself. A step that says to validate,
check, or verify a named rule only names the required rule; it does not declare what
data or operation decides it. Likewise, an outcome label is not a public mapping
without a return value or exception selector. Two reasonable implementations that
would produce different user-visible behavior are evidence of such a missing link.

Otherwise choose IMPLEMENT. Uncertainty about code, wiring, repositories, or
constructors is not evidence of a behavior gap. Do not infer a business rule from
a type's existence or defer a missing decision criterion to source discovery.
Report at most one root ambiguity concisely, using exactly one allowed source reference.
When behavior meaning is missing, prefer a use-case specification reference.
For IMPLEMENT, source_ref must be empty.
"""


def admit_behavior_capsule(
    context: dict[str, object],
    source_refs: list[str],
    *,
    proposal_call: Callable[..., dict[str, Any]] = parse_structured,
) -> UpstreamGap | None:
    """Admit a behavior capsule or return its single bounded upstream gap."""

    payload = {
        "behaviorCapsule": context.get("behaviorCapsule"),
        "sourceRefs": source_refs,
    }
    parsed = proposal_call(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            },
        ],
        BehaviorAdmission,
        reasoning_effort="low",
        max_completion_tokens=2048,
        operation="implementation-admission",
    )
    admission = BehaviorAdmission.model_validate(parsed)
    if admission.decision == "IMPLEMENT":
        if admission.source_ref:
            raise ValueError("IMPLEMENT admission must have an empty source_ref")
        return None

    if admission.source_ref not in source_refs:
        raise ValueError("NEEDS_INPUT source_ref must match exactly one allowed source reference")
    summary = shorten(admission.summary.strip(), width=500, placeholder="…")
    if not summary:
        raise ValueError("NEEDS_INPUT summary must not be blank")
    return UpstreamGap(summary=summary, source_ref=admission.source_ref)
