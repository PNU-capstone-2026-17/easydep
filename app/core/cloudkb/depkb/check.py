"""Evaluate a concrete plan without interpreting human-readable prose."""

from __future__ import annotations

from dataclasses import dataclass

from .infra_intent import Constraint, InfraIntent


@dataclass(frozen=True)
class Violation:
    kind: str
    subject: str
    object: str
    detail: str


@dataclass(frozen=True)
class Report:
    evaluationComplete: bool
    systemPassed: bool
    violations: tuple[Violation, ...]
    unevaluatedConstraints: tuple[str, ...]
    missingMandatoryResources: tuple[str, ...]


def _instances(plan: dict, resource_id: str) -> list[dict]:
    for resource in plan.get("resources", []):
        if resource.get("id") == resource_id:
            return list(resource.get("instances") or [])
    return []


def _check_machine(constraint: Constraint, plan: dict) -> tuple[Violation | None, bool]:
    machine = constraint.machine
    objects = _instances(plan, constraint.object)
    supported = False
    minimum = machine.get("minCount")
    if isinstance(minimum, int):
        supported = True
        if len(objects) < minimum:
            return Violation(
                constraint.kind, constraint.subject, constraint.object,
                f"observed {len(objects)} instance(s), expected at least {minimum}",
            ), True
    distinct_over = machine.get("distinctOver")
    if isinstance(distinct_over, str):
        supported = True
        values = {item.get(distinct_over) for item in objects} - {None}
        needed = minimum if isinstance(minimum, int) else 2
        if len(values) < needed:
            return Violation(
                constraint.kind, constraint.subject, constraint.object,
                f"observed {len(values)} distinct {distinct_over} value(s), expected {needed}",
            ), True
    name_equals = machine.get("nameEquals")
    if isinstance(name_equals, str):
        supported = True
        if objects and not any(item.get("name") == name_equals for item in objects):
            return Violation(
                constraint.kind, constraint.subject, constraint.object,
                f"no instance has name {name_equals!r}",
            ), True
    # appliesWhen/otherwise/exclusive require a typed concrete-plan context that
    # this v2 checker does not yet receive.  Report them; never silently pass.
    unsupported = set(machine) - {"minCount", "distinctOver", "nameEquals"}
    return None, supported and not unsupported


def check(intent: InfraIntent, plan: dict) -> Report:
    violations: list[Violation] = []
    unevaluated: list[str] = []
    for constraint in intent.constraints:
        violation, complete = _check_machine(constraint, plan)
        if violation:
            violations.append(violation)
        if not complete:
            unevaluated.append(f"{constraint.kind}: {constraint.subject}→{constraint.object}")

    missing = tuple(
        resource.id for resource in intent.resources
        if resource.provisioningStatus in {
            "selectedStartResource", "mandatoryForProvisioning"
        }
        and not _instances(plan, resource.id)
        and not any(
            item.id == resource.id and item.behavior in {"providerDefaulted", "providerCreated"}
            for item in intent.providerRealizations
        )
    )
    complete = not unevaluated
    passed = complete and not violations and not missing
    return Report(
        evaluationComplete=complete, systemPassed=passed,
        violations=tuple(violations), unevaluatedConstraints=tuple(unevaluated),
        missingMandatoryResources=missing,
    )
