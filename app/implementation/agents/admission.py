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
from app.llm_connection import build_admission_llm_connection

from .upstream_gap_tool import UpstreamGap, UpstreamGapOption

ADMISSION_CHECKPOINT_SCHEMA = "implementation-admission/v1alpha1"
ADMISSION_VALIDATOR_VERSION = "behavior-admission/v3"
INTEGRATION_ADMISSION_VALIDATOR_VERSION = "integration-admission/v1"


class AdmissionOption(BaseModel):
    """The small choice payload returned with a semantic admission gap."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    label: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)
    requested_effect: str = Field(min_length=1, max_length=500)


class BehaviorAdmission(BaseModel):
    """The structured semantic admission decision returned by the proposer."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["IMPLEMENT", "NEEDS_INPUT"]
    summary: str = Field(min_length=1)
    source_ref: str
    options: list[AdmissionOption] = Field(default_factory=list, max_length=3)


_SYSTEM_PROMPT = """You are the semantic preflight for a bounded implementation subtask.
Judge whether the behavior contract is implementable, not source-code quality. Return only
the requested structured decision.

Choose NEEDS_INPUT only when implementation still requires a semantic choice:
1. A required policy, public outcome, authorization rule, or caller-visible mapping is
   unspecified, so implementation would invent behavior; or
2. The capsule does not decide the semantic source or trust boundary of a needed value -- for
   example, whether it is caller-controlled input, trusted platform context, persisted state,
   a prior-call result, or an internal operation result; or
3. The persistence behavior or caller-visible API contract itself still needs a choice.

A condition label is not a decision source by itself. A step that says to validate,
check, or verify a named rule only names the required rule; it does not declare what
data or operation decides it. Likewise, an outcome label is not a public mapping
without a return value or exception selector. Two reasonable implementations that
would produce different user-visible behavior are evidence of such a missing link.
A status or outcome list declares possible outputs, not the operands or decision source
that selects one. Never infer a semantic source, trust boundary, or policy from a framework,
default, naming convention, or "standard" context. A prose declaration counts only when it
explicitly states the source and trust semantics; a condition or precondition label alone
does not. An upstream contract statement that a named value or state is authenticated,
trusted, platform-provided, or persisted is an explicit semantic source/trust declaration;
it does not also need a method parameter or context accessor.
For calibration, an authenticated current principal is trusted context, while a principal ID
supplied by the caller is caller-controlled input. A bare actor or condition establishes
neither source.

Choose IMPLEMENT when the required policy and outcomes are explicit and the capsule identifies
the semantic source and trust boundary, even if the exact framework/runtime transport is not
specified. Selecting a parameter, context accessor, dependency-injection binding, or repository
plumbing is implementation work when that choice does not alter caller control, public behavior,
authorization, persistence semantics, or the API contract. Uncertainty about source files,
constructor wiring, or repository implementation is not evidence of a behavior gap. Do not
infer a business rule from a type's existence or defer a missing decision criterion to source
discovery.
The capsule's designEvidence is natural-language evidence, not a completeness proof. Choose
NEEDS_INPUT when a required domain state, relationship, or semantic source is absent from the
linked design and implementation would have to choose an upstream business rule, identifier
mapping, external supplier, or immutable declaration. Do not require an ontology, exhaustive
state list, or proof that every runtime detail is declared; private accessors, DI, repository
plumbing, and role-token mapping remain implementation choices when they preserve the declared
meaning. A trusted contextual value declares where that value comes from; it does not by itself
declare how domain records are assigned, owned, visible, eligible, or otherwise related to it.
When the required result is relative to a contextual value, choose NEEDS_INPUT unless the linked
contracts and design evidence declare a relation, match key, or semantic source from which that
selection can be implemented. An operation name alone is not such a declaration when the linked
data and dependencies cannot evaluate the relation. If designEvidence is absent entirely, treat
the capsule as a legacy context and do not infer a gap merely from that absence. Any
preflightFindings are deterministic evidence to resolve in this same decision, not a separate
decision. When NEEDS_INPUT is appropriate, provide two or three mutually exclusive options when
the missing choice can be presented naturally. Each option needs a stable id, short label,
description, and requested_effect describing the upstream change it would cause.
Report at most one root ambiguity concisely, using exactly one allowed source reference.
Every option must be resolvable by revising that same source reference. Options for an API,
operation, or class reference must preserve the already-declared use-case behavior and policy;
do not offer a choice that instead changes a requirement or another upstream target.
Each option must also be independently executable within that source element and the elements
already declared in designEvidence. Do not make an option depend on an undeclared class,
relationship endpoint, API field, or policy. If an alternative needs another authority target,
do not present it as a local option for the current source reference.
The source reference identifies the first contract that must change:
- Use a use_case_spec reference when the required behavior, policy, value-source meaning,
  trust boundary, or domain matching rule itself is not specified.
- When that meaning is already stated but the API, class operation, sequence call, or state
  contract lacks only a carrier for it, use the closest api or operation reference instead.
  Do not route mere runtime wiring to the use-case specification.
- When linked BCE design evidence lacks a required state or relationship, use the closest
  exact class reference available as the source reference.
- Use an api reference only when the unresolved choice belongs to the caller-visible HTTP
  request or response. Use an operation reference when trusted server context or an internal
  call is required but its semantic source or trust boundary is not declared.
For IMPLEMENT, source_ref must be empty.
"""

_INTEGRATION_SYSTEM_PROMPT = """You are the semantic preflight for one bounded integration implementation task.
The supplied evidence boundary is complete. Trace required runtime meanings to backend
preconditions across that evidence. Choose NEEDS_INPUT when evidence contradicts the frozen
contract or omits or ambiguously defines required upstream product or runtime meaning, so
implementation or validation would require guessing. Never assume unseen configuration,
framework defaults, or naming conventions. Choose IMPLEMENT only when the path is semantically
coherent and any remaining work is connector mechanics. Before IMPLEMENT, for each backend
precondition on the representative traced path whose operand comes from runtime context or
configuration, identify an explicit compatible supplier in the supplied evidence: either an
effective compatible value/default or a runtime/deployment binding that supplies or forwards it.
Merely declaring a configurable property is insufficient when its effective value is absent or
incompatible and the supplied deployment neither supplies nor exposes a compatible value. If no
compatible supplier exists and satisfying the precondition would require guessing or an upstream
change, choose NEEDS_INPUT. A build or test pass is not semantic evidence. Return only the
requested structured decision. For NEEDS_INPUT, give one concise root gap using exactly one
allowed source reference and return an empty options list; downstream RTM authority is not part of
this evidence. For IMPLEMENT, source_ref must be empty.
The source_ref is an RTM routing key, not the path where evidence was observed. Copy exactly one
complete string verbatim from payload.sourceRefs; never return an evidenceFiles[].path. For a
runtime or deployment mapping ambiguity or contradiction, prefer an available workload:* source
reference.
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
    payload: dict[str, object],
    *,
    task_id: str,
    system_prompt: str,
    validator_version: str,
) -> str:
    """Fingerprint exactly the values that can change the admission decision."""

    return _sha256_json(
        {
            "taskId": task_id,
            "admissionModel": build_admission_llm_connection().model,
            "admissionPrompt": system_prompt,
            "validatorSchemaVersion": validator_version,
            "payload": payload,
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
    raw_options = raw_gap.get("options", [])
    if not isinstance(raw_options, list):
        return False, None
    options: list[UpstreamGapOption] = []
    for raw_option in raw_options:
        if not isinstance(raw_option, dict):
            return False, None
        try:
            option = AdmissionOption.model_validate(
                {
                    "id": raw_option.get("id"),
                    "label": raw_option.get("label"),
                    "description": raw_option.get("description"),
                    "requested_effect": raw_option.get(
                        "requested_effect", raw_option.get("requestedEffect")
                    ),
                }
            )
        except ValueError:
            return False, None
        options.append(
            UpstreamGapOption(
                id=option.id,
                label=option.label,
                description=option.description,
                requested_effect=option.requested_effect,
            )
        )
    if len(options) < 2 or len(options) > 3 or len({option.id for option in options}) != len(options):
        options = []
    return True, UpstreamGap(
        summary=summary,
        source_ref=source_ref,
        options=tuple(options),
    )


def _clear_stale_need_input(run_root: Path, task_id: str) -> None:
    """Remove only the latest blocker superseded by a new admission decision."""

    path = run_root / "reports" / "agent-executions" / f"{task_id}.result.json"
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if result.get("status") == "NEEDS_INPUT":
        path.unlink()


def _preflight_admission(
    run_root: Path,
    task: dict[str, object],
    source_refs: list[str],
    *,
    payload: dict[str, object],
    system_prompt: str,
    validator_version: str,
    admission_call: Callable[[], UpstreamGap | None],
) -> UpstreamGap | None:
    """Reuse the existing checkpoint contract for one exact admission input."""

    task_id = str(task.get("task_id") or "")
    if not task_id:
        return None
    execution_dir = run_root / "reports" / "agent-executions"
    target = execution_dir / f"{task_id}.admission.json"
    input_sha256 = _admission_input_sha256(
        payload,
        task_id=task_id,
        system_prompt=system_prompt,
        validator_version=validator_version,
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

    gap = admission_call()
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


def _admit_payload(
    payload: dict[str, object],
    source_refs: list[str],
    *,
    system_prompt: str,
    operation: str,
    proposal_call: Callable[..., dict[str, Any]] = parse_structured,
) -> UpstreamGap | None:
    semantic_source_refs = _semantic_source_refs(source_refs)
    connection = build_admission_llm_connection()
    parsed = proposal_call(
        [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            },
        ],
        BehaviorAdmission,
        reasoning_effort="low",
        max_completion_tokens=2048,
        operation=operation,
        connection=connection,
    )
    admission = BehaviorAdmission.model_validate(parsed)
    if admission.decision == "IMPLEMENT":
        if admission.source_ref:
            raise ValueError("IMPLEMENT admission must have an empty source_ref")
        return None

    if admission.source_ref not in semantic_source_refs:
        raise ValueError(
            "NEEDS_INPUT source_ref must match exactly one allowed source reference: "
            f"received {admission.source_ref!r}; allowed {semantic_source_refs!r}"
        )
    summary = shorten(admission.summary.strip(), width=500, placeholder="…")
    if not summary:
        raise ValueError("NEEDS_INPUT summary must not be blank")
    options = [
        UpstreamGapOption(
            id=option.id,
            label=option.label,
            description=option.description,
            requested_effect=option.requested_effect,
        )
        for option in admission.options
    ]
    if len(options) < 2 or len(options) > 3 or len({option.id for option in options}) != len(options):
        options = []
    return UpstreamGap(
        summary=summary,
        source_ref=admission.source_ref,
        options=tuple(options),
    )


def admit_behavior_capsule(
    context: dict[str, object],
    source_refs: list[str],
    *,
    proposal_call: Callable[..., dict[str, Any]] = parse_structured,
) -> UpstreamGap | None:
    """Admit a behavior capsule or return its single bounded upstream gap."""

    payload = {
        "behaviorCapsule": context.get("behaviorCapsule"),
        "sourceRefs": _semantic_source_refs(source_refs),
    }
    return _admit_payload(
        payload,
        source_refs,
        system_prompt=_SYSTEM_PROMPT,
        operation="implementation-admission",
        proposal_call=proposal_call,
    )


def preflight_semantic_behavior(
    run_root: Path,
    task: dict[str, object],
    context: dict[str, object],
    source_refs: list[str],
) -> UpstreamGap | None:
    """Return a cached or new behavior gap before starting OpenHands."""

    if not any(ref.startswith("use_case_spec:") for ref in source_refs):
        return None
    payload = {
        "behaviorCapsule": context.get("behaviorCapsule"),
        "sourceRefs": _semantic_source_refs(source_refs),
    }
    return _preflight_admission(
        run_root,
        task,
        source_refs,
        payload=payload,
        system_prompt=_SYSTEM_PROMPT,
        validator_version=ADMISSION_VALIDATOR_VERSION,
        admission_call=lambda: admit_behavior_capsule(context, source_refs),
    )


def integration_evidence_paths(
    run_root: Path,
    task: dict[str, object],
    context: dict[str, object],
) -> list[str]:
    """Return the safe evidence boundary, including not-yet-created planned files."""

    raw_paths = context.get("readSourcePaths")
    if not isinstance(raw_paths, list):
        raise TypeError("Integration admission requires readSourcePaths")
    root = run_root.resolve()
    evidence_paths: set[str] = set()
    for value in raw_paths:
        if not isinstance(value, str) or not value:
            raise ValueError("Integration admission paths must be non-empty strings")
        relative = Path(value.replace("\\", "/"))
        target = (root / relative).resolve()
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not target.is_relative_to(root)
        ):
            raise ValueError(f"Unsafe integration evidence path: {value}")
        evidence_paths.add(target.relative_to(root).as_posix())

    execution_dir = (root / "reports" / "agent-executions").resolve()
    application_root = (root / "application").resolve()
    for task_id in task.get("depends_on", []):
        result_path = (execution_dir / f"{task_id}.result.json").resolve()
        if not result_path.is_relative_to(execution_dir) or not result_path.is_file():
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        for value in result.get("changedFiles", []):
            if not isinstance(value, str):
                continue
            relative = Path(value.replace("\\", "/"))
            target = (root / relative).resolve()
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not relative.parts
                or relative.parts[0] != "application"
                or "generated" in relative.parts
                or not target.is_relative_to(application_root)
            ):
                continue
            evidence_paths.add(target.relative_to(root).as_posix())

    return sorted(evidence_paths)


def _integration_admission_payload(
    run_root: Path,
    task: dict[str, object],
    context: dict[str, object],
    source_refs: list[str],
) -> dict[str, object]:
    """Read every file in the admitted evidence boundary for the live decision."""

    root = run_root.resolve()
    paths = integration_evidence_paths(run_root, task, context)
    for path in paths:
        if not (root / path).is_file():
            raise ValueError(f"Missing integration admission evidence: {path}")
    return {
        "traceEvidence": context.get("traceEvidence"),
        "deployment": context.get("deployment"),
        "evidenceFiles": [
            {
                "path": path,
                "content": (root / path).read_bytes().decode("utf-8"),
            }
            for path in paths
        ],
        "sourceRefs": _semantic_source_refs(source_refs),
    }


def admit_integration_evidence(
    payload: dict[str, object],
    source_refs: list[str],
    *,
    proposal_call: Callable[..., dict[str, Any]] = parse_structured,
) -> UpstreamGap | None:
    """Admit the complete vertical-integration evidence boundary."""

    gap = _admit_payload(
        payload,
        source_refs,
        system_prompt=_INTEGRATION_SYSTEM_PROMPT,
        operation="implementation-integration-admission",
        proposal_call=proposal_call,
    )
    if gap is None:
        return None
    return UpstreamGap(summary=gap.summary, source_ref=gap.source_ref)


def preflight_semantic_integration(
    run_root: Path,
    task: dict[str, object],
    context: dict[str, object],
    source_refs: list[str],
) -> UpstreamGap | None:
    """Return a cached or new integration gap before starting OpenHands."""

    payload = _integration_admission_payload(run_root, task, context, source_refs)
    return _preflight_admission(
        run_root,
        task,
        source_refs,
        payload=payload,
        system_prompt=_INTEGRATION_SYSTEM_PROMPT,
        validator_version=INTEGRATION_ADMISSION_VALIDATOR_VERSION,
        admission_call=lambda: admit_integration_evidence(payload, source_refs),
    )
