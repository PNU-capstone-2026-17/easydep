"""Build a cloud infrastructure intent from relation-specific DepKB findings."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .closure import Closure, _claims, closure

SCHEMA_VERSION = "easydep-infra-intent/v2"


@dataclass(frozen=True)
class Resource:
    id: str
    provisioningStatus: str
    because: tuple[str, ...] = ()
    condition: dict = field(default_factory=lambda: {"kind": "always"})


@dataclass(frozen=True)
class ProviderRealization:
    id: str
    behavior: str


@dataclass(frozen=True)
class Decision:
    about: str
    kind: str
    condition: dict


@dataclass(frozen=True)
class Constraint:
    kind: str
    subject: str
    object: str
    machine: dict
    description: str = ""


@dataclass(frozen=True)
class InfraIntent:
    schemaVersion: str
    csp: str
    region: str
    startResources: tuple[str, ...]
    resources: tuple[Resource, ...]
    createOrder: tuple[str, ...]
    deleteBlockedWhileAttached: tuple[tuple[str, str], ...]
    detachRequiredBeforeDelete: tuple[tuple[str, str], ...]
    cascadeDeletedWithOwner: tuple[tuple[str, str], ...]
    runtimeRequiredForSignal: tuple[tuple[str, str, str], ...]
    providerRealizations: tuple[ProviderRealization, ...]
    decisions: tuple[Decision, ...]
    constraints: tuple[Constraint, ...]
    provenance: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=1)


def _constraints_for(csp: str, ids: set[str]) -> tuple[Constraint, ...]:
    constraints: dict[tuple[str, str, str], Constraint] = {}
    for claim in _claims():
        if claim["csp"] != csp or claim["relationFamily"] != "provisioning":
            continue
        condition = claim["condition"]
        machine = condition.get("machine")
        if not isinstance(machine, dict):
            continue
        objects = claim["object"].split("|")
        if claim["subject"] not in ids or not any(obj in ids for obj in objects):
            continue
        item = Constraint(
            kind=condition["kind"], subject=claim["subject"], object=claim["object"],
            machine=machine, description=str(condition.get("description") or "")
        )
        constraints.setdefault((item.subject, item.object, item.kind), item)
    return tuple(constraints[key] for key in sorted(constraints))


def _merge_order(closures: list[Closure], csp: str) -> tuple[str, ...]:
    nodes = {node for item in closures for node in item.createOrder}
    edges = {
        (claim["subject"], claim["object"])
        for claim in _claims()
        if claim["csp"] == csp
        and claim["relationFamily"] == "provisioning"
        and claim["finding"] == "mandatoryForProvisioning"
        and claim["subject"] in nodes and claim["object"] in nodes
    }
    order: list[str] = []
    remaining = set(nodes)
    while remaining:
        ready = sorted(
            node for node in remaining
            if not any((node, dependency) in edges for dependency in remaining)
        )
        if not ready:
            raise ValueError(f"cycle in mandatory provisioning findings: {sorted(remaining)}")
        order.append(ready[0])
        remaining.remove(ready[0])
    return tuple(order)


def build(anchors: list[str], csp: str, region: str) -> InfraIntent:
    if not anchors:
        raise ValueError("at least one start resource is required")
    closures = [closure(anchor, csp) for anchor in anchors]
    resources: dict[str, Resource] = {
        anchor: Resource(anchor, "selectedStartResource") for anchor in anchors
    }
    realizations: dict[str, ProviderRealization] = {}
    decisions: list[Decision] = []
    for item in closures:
        for mandatory in item.mandatoryForProvisioning:
            previous = resources.get(mandatory.id)
            because = tuple(sorted(set(mandatory.because) | set(previous.because if previous else ())))
            resources[mandatory.id] = Resource(
                mandatory.id, "mandatoryForProvisioning", because, mandatory.condition
            )
        for candidate in item.nonMandatoryForProvisioning:
            resources.setdefault(
                candidate.id,
                Resource(candidate.id, "notMandatoryForProvisioning", (), candidate.condition),
            )
            if candidate.realizationBehavior:
                realizations[candidate.id] = ProviderRealization(
                    candidate.id, candidate.realizationBehavior
                )
        decisions.extend(
            Decision(decision.about, decision.kind, decision.condition)
            for decision in item.decisions
        )

    # A mandatory path overrides a non-mandatory observation from another start resource.
    realizations = {
        key: value for key, value in realizations.items()
        if resources[key].provisioningStatus == "notMandatoryForProvisioning"
    }
    ids = set(resources)
    return InfraIntent(
        schemaVersion=SCHEMA_VERSION, csp=csp, region=region,
        startResources=tuple(anchors), resources=tuple(resources[k] for k in sorted(resources)),
        createOrder=_merge_order(closures, csp),
        deleteBlockedWhileAttached=tuple(sorted({p for c in closures for p in c.deleteBlockedWhileAttached})),
        detachRequiredBeforeDelete=tuple(sorted({p for c in closures for p in c.detachRequiredBeforeDelete})),
        cascadeDeletedWithOwner=tuple(sorted({p for c in closures for p in c.cascadeDeletedWithOwner})),
        runtimeRequiredForSignal=tuple(sorted({p for c in closures for p in c.runtimeRequiredForSignal})),
        providerRealizations=tuple(realizations[k] for k in sorted(realizations)),
        decisions=tuple(decisions), constraints=_constraints_for(csp, ids),
        provenance={
            "claimsArtifact": str(Path(__file__).with_name("claims.json").name),
            "claimSchemaVersion": "easydep-dependency-claims/v2",
            "interpretation": "relation-specific findings; prose is not parsed",
        },
    )
