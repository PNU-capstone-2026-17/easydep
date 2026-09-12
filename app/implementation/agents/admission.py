"""Semantic admission for bounded implementation behavior capsules."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from textwrap import shorten
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.design.services.common.structured import parse_structured
from app.llm_connection import build_llm_connection

from .upstream_gap_tool import UpstreamGap

ADMISSION_CHECKPOINT_SCHEMA = "implementation-admission/v1alpha1"
ADMISSION_VALIDATOR_VERSION = "behavior-admission/v1"


class BehaviorAdmission(BaseModel):
    """The structured semantic admission decision returned by the proposer."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["IMPLEMENT", "NEEDS_INPUT"]
    summary: str = Field(min_length=1)
    source_ref: str


_SYSTEM_PROMPT = """You are a semantic admission judge for a bounded implementation task.
Judge whether the behavior contract is implementable, not source-code quality. Return only
the requested structured decision.

Choose NEEDS_INPUT only if the capsule explicitly requires a user-visible decision, effect,
or public outcome and either:
1. The required policy or outcome itself is unspecified, so implementation would invent a
   business rule; or
2. The meaning is specified, but no declared input, prior-call result, state, trusted context,
   or usable operation with its required operands can carry or produce the needed value.

A condition label is not a decision source by itself. A step that says to validate,
check, or verify a named rule only names the required rule; it does not declare what
data or operation decides it. Likewise, an outcome label is not a public mapping
without a return value or exception selector. Two reasonable implementations that
would produce different user-visible behavior are evidence of such a missing link.
A prose precondition is not by itself a trusted-context carrier. Count it only when
the capsule explicitly binds it to an API, Control, or sequence argument (for example
a `$context.*` source), or supplies a usable context operation.

Otherwise choose IMPLEMENT. Uncertainty about source files, constructor wiring, or
repository implementation is not evidence of a behavior gap. Do not infer a business
rule from a type's existence or defer a missing decision criterion to source discovery.
Report at most one root ambiguity concisely, using exactly one allowed source reference.
The source reference identifies the first contract that must change:
- Use a use_case_spec reference only when the required user-visible outcome or policy
  choice itself is not specified.
- When that meaning is already stated but the API, class operation, sequence call, or
  state contract has no carrier for an input, response, identity, scope, or decision,
  use the closest api or operation reference instead. Do not route that wiring gap to
  the use-case specification.
- Use an api reference only when the missing carrier belongs to the caller-visible HTTP
  request or response. Use an operation reference when the caller must not supply the
  value and the missing carrier belongs to trusted server context or an internal call.
For IMPLEMENT, source_ref must be empty.
"""


def _semantic_source_refs(source_refs: list[str]) -> list[str]:
    """Prefer a use-case specification when both forms identify the same case."""

    specified_use_cases = {
        ref.removeprefix("use_case_spec:")
        for ref in source_refs
        if ref.startswith("use_case_spec:")
    }
    return [
        ref
        for ref in source_refs
        if not (
            ref.startswith("use_case:")
            and ref.removeprefix("use_case:") in specified_use_cases
        )
    ]


def _sha256_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _admission_input_sha256(
    context: dict[str, object],
    source_refs: list[str],
    *,
    task_id: str,
) -> str:
    """Fingerprint exactly the values that can change the admission decision."""

    return _sha256_json(
        {
            "taskId": task_id,
            "admissionModel": build_llm_connection().model,
            "admissionPrompt": _SYSTEM_PROMPT,
            "validatorSchemaVersion": ADMISSION_VALIDATOR_VERSION,
            "behaviorCapsule": context.get("behaviorCapsule"),
            "sourceRefs": _semantic_source_refs(source_refs),
        }
    )


def _checkpoint_gap(
    checkpoint: dict[str, object], source_refs: list[str]
) -> tuple[bool, UpstreamGap | None]:
    if checkpoint.get("decision") == "IMPLEMENT":
        return True, None
    raw_gap = checkpoint.get("upstreamGap")
    if checkpoint.get("decision") != "NEEDS_INPUT" or not isinstance(raw_gap, dict):
        return False, None
    summary = raw_gap.get("summary")
    source_ref = raw_gap.get("sourceRef")
    if (
        not isinstance(summary, str)
        or not summary
        or not isinstance(source_ref, str)
        or source_ref not in _semantic_source_refs(source_refs)
    ):
        return False, None
    return True, UpstreamGap(summary=summary, source_ref=source_ref)


def _clear_stale_need_input(run_root: Path, task_id: str) -> None:
    """Remove only the latest blocker superseded by a new admission decision."""

    path = run_root / "reports" / "agent-executions" / f"{task_id}.result.json"
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if result.get("status") == "NEEDS_INPUT":
        path.unlink()


def preflight_semantic_behavior(
    run_root: Path,
    task: dict[str, object],
    context: dict[str, object],
    source_refs: list[str],
) -> UpstreamGap | None:
    """Return a cached or new semantic gap before starting OpenHands."""

    if not any(ref.startswith("use_case_spec:") for ref in source_refs):
        return None
    task_id = str(task.get("task_id") or "")
    if not task_id:
        return None
    execution_dir = run_root / "reports" / "agent-executions"
    target = execution_dir / f"{task_id}.admission.json"
    input_sha256 = _admission_input_sha256(
        context, source_refs, task_id=task_id
    )
    try:
        checkpoint = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        checkpoint = {}
    if not isinstance(checkpoint, dict):
        checkpoint = {}
    if (
        checkpoint.get("schemaVersion") == ADMISSION_CHECKPOINT_SCHEMA
        and checkpoint.get("inputSha256") == input_sha256
    ):
        reused, gap = _checkpoint_gap(checkpoint, source_refs)
        if reused:
            return gap

    gap = admit_behavior_capsule(context, source_refs)
    _clear_stale_need_input(run_root, task_id)
    execution_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "schemaVersion": ADMISSION_CHECKPOINT_SCHEMA,
        "inputSha256": input_sha256,
        "decision": "NEEDS_INPUT" if gap is not None else "IMPLEMENT",
    }
    if gap is not None:
        checkpoint["upstreamGap"] = gap.as_result()
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(target)
    return gap


def admit_behavior_capsule(
    context: dict[str, object],
    source_refs: list[str],
    *,
    proposal_call: Callable[..., dict[str, Any]] = parse_structured,
) -> UpstreamGap | None:
    """Admit a behavior capsule or return its single bounded upstream gap."""

    semantic_source_refs = _semantic_source_refs(source_refs)
    payload = {
        "behaviorCapsule": context.get("behaviorCapsule"),
        "sourceRefs": semantic_source_refs,
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

    if admission.source_ref not in semantic_source_refs:
        raise ValueError("NEEDS_INPUT source_ref must match exactly one allowed source reference")
    summary = shorten(admission.summary.strip(), width=500, placeholder="…")
    if not summary:
        raise ValueError("NEEDS_INPUT summary must not be blank")
    return UpstreamGap(summary=summary, source_ref=admission.source_ref)
