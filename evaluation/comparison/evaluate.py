"""가중치 없이 개수와 분모로 한 번의 실행을 평가한다."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .models import ConstraintSpec, Manifest, RequirementSpec, SubjectResult


def fraction(numerator: int, denominator: int) -> dict[str, Any]:
    ratio = None if denominator == 0 else numerator / denominator
    return {
        "numerator": numerator,
        "denominator": denominator,
        "ratio": ratio,
        "display": f"{numerator}/{denominator}" if ratio is None else f"{numerator}/{denominator} ({ratio:.1%})",
    }


def _gate_reference_passed(
    reference: str, gate_results: dict[str, dict[str, Any]]
) -> bool:
    gate_id, separator, detail_id = reference.partition("#")
    gate = gate_results.get(gate_id, {})
    if not separator:
        return gate.get("status") == "passed"
    phases = gate.get("phases", [])
    return any(
        phase.get("id") == detail_id and phase.get("status") == "passed"
        for phase in phases
        if isinstance(phase, dict)
    )


def _gate_fraction(
    items: Iterable[RequirementSpec | ConstraintSpec],
    gate_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    entries = list(items)
    passed: list[str] = []
    failed: list[str] = []
    not_automated: list[str] = []
    for item in entries:
        if not item.verification_gates:
            not_automated.append(item.id)
            continue
        if all(
            _gate_reference_passed(reference, gate_results)
            for reference in item.verification_gates
        ):
            passed.append(item.id)
        else:
            failed.append(item.id)
    return {
        **fraction(len(passed), len(entries)),
        "passedIds": passed,
        "failedIds": failed,
        "notAutomatedIds": not_automated,
    }


def _evidence_exists(workspace: Path, raw_path: str) -> bool:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    return candidate.exists()


def _traceability(manifest: Manifest, subject: SubjectResult) -> dict[str, Any]:
    complete: list[str] = []
    incomplete: list[str] = []
    missing: dict[str, list[str]] = {}
    for requirement in manifest.requirements:
        evidence = subject.requirement_evidence.get(requirement.id, {})
        missing_stages = [
            stage
            for stage in requirement.evidence_stages
            if not any(_evidence_exists(subject.workspace, path) for path in evidence.get(stage, ()))
        ]
        if requirement.evidence_stages and not missing_stages:
            complete.append(requirement.id)
        else:
            incomplete.append(requirement.id)
            missing[requirement.id] = missing_stages or list(requirement.evidence_stages)
    return {
        **fraction(len(complete), len(manifest.requirements)),
        "completeIds": complete,
        "incompleteIds": incomplete,
        "missingStages": missing,
    }


def evaluate_run(
    manifest: Manifest,
    subject: SubjectResult,
    gate_results: list[dict[str, Any]],
    *,
    wall_seconds: float,
) -> dict[str, Any]:
    gates_by_id = {item["id"]: item for item in gate_results}
    required_gates = [gate for gate in manifest.gates if gate.required]
    required_passed = sum(gates_by_id.get(gate.id, {}).get("status") == "passed" for gate in required_gates)
    implemented = _gate_fraction(manifest.requirements, gates_by_id)
    constraints = _gate_fraction(manifest.constraints, gates_by_id)
    successful = subject.status == "completed" and required_passed == len(required_gates)
    tokens_per_implemented = None
    if subject.usage.total_tokens is not None and implemented["numerator"]:
        tokens_per_implemented = subject.usage.total_tokens / implemented["numerator"]
    return {
        "status": subject.status,
        "successful": successful,
        "implementedRequirements": implemented,
        "satisfiedConstraints": constraints,
        "passedRequiredGates": {
            **fraction(required_passed, len(required_gates)),
            "failedIds": [gate.id for gate in required_gates if gates_by_id.get(gate.id, {}).get("status") != "passed"],
        },
        "traceability": _traceability(manifest, subject),
        "usage": subject.usage.as_dict(),
        "tokensPerImplementedRequirement": tokens_per_implemented,
        "wallSeconds": round(wall_seconds, 3),
        "gates": gate_results,
    }
