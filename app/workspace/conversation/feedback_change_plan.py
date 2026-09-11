"""Pure, fail-closed planning for a feedback decision revision.

This module deliberately owns neither persistence nor execution.  It turns a
normalized feedback envelope and a frozen typed trace into an immutable
``ChangeSet`` that a later repository/service layer may checkpoint and publish.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from enum import StrEnum
from hashlib import sha256
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.artifact_trace import ArtifactTrace, TraceRef

from .contracts import RevisionTarget
from .feedback_envelope import BaseRevision, Decision, Question


class ChangePlanError(ValueError):
    """The evidence does not support a safe deterministic plan."""


class ExecutionAction(StrEnum):
    REBUILD = "rebuild"
    REPROJECT = "reproject"
    STALE = "stale"


class _PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)


def _digest(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _revision_key(value: BaseRevision) -> tuple[str, str, str]:
    return (value.artifact_type, str(value.version_id or ""), value.digest or "")


def _target_key(value: RevisionTarget) -> tuple[str, str, str]:
    return (value.owner, value.artifact_type, f"{value.kind}:{value.element_id}")


def _trace_ref(target: RevisionTarget) -> TraceRef:
    """ProjectTools' display/canonical ref is not necessarily its RTM identity."""
    return TraceRef(target.kind, target.element_id)


def _target_fingerprint(value: RevisionTarget) -> tuple[str, str, str, str, str, int | None]:
    return (
        value.ref,
        value.kind,
        value.element_id,
        value.owner,
        value.artifact_type,
        value.artifact_version_id,
    )


def _decision_digest(value: Decision) -> str:
    meaning = value.normalized_meaning
    return _digest(
        {
            "origin": value.origin,
            "answerMode": value.answer_mode,
            "selectedOptionId": value.selected_option_id,
            "rawAnswer": value.raw_answer,
            "meaning": meaning.model_dump() if meaning is not None else None,
            "targets": sorted(_target_fingerprint(item) for item in value.authoritative_targets),
            "constraints": sorted(value.preserved_constraints),
            "baseRevisions": sorted(_revision_key(item) for item in value.base_revisions),
        }
    )


class RtmSnapshot(_PlanModel):
    """A normalized read-only RTM graph expressed with stable ``TraceRef`` values."""

    trace: ArtifactTrace
    projection_contracts: tuple[ProjectionContract, ...] = ()

    @model_validator(mode="after")
    def normalize_contracts(self) -> RtmSnapshot:
        contracts = tuple(
            sorted(
                set(self.projection_contracts),
                key=lambda item: (
                    item.consumer,
                    item.producer_refs,
                    item.adapter,
                    item.version,
                ),
            )
        )
        object.__setattr__(self, "projection_contracts", contracts)
        return self

    @property
    def digest(self) -> str:
        return _digest(
            {
                "nodes": [
                    {
                        "ref": node.ref.format(),
                        "sources": [item.format() for item in node.direct_sources],
                    }
                    for node in self.trace.nodes
                ],
                "projectionContracts": [
                    (
                        item.consumer.format(),
                        tuple(ref.format() for ref in item.producer_refs),
                        item.adapter,
                        item.version,
                    )
                    for item in self.projection_contracts
                ],
            }
        )


class ProjectionContract(_PlanModel):
    """Registered authority for a deterministic projection, never inferred from RTM."""

    consumer: TraceRef
    producer_refs: tuple[TraceRef, ...] = Field(min_length=1)
    adapter: str = Field(min_length=1)
    version: str = Field(min_length=1)

    @model_validator(mode="after")
    def normalize(self) -> ProjectionContract:
        object.__setattr__(self, "producer_refs", tuple(sorted(set(self.producer_refs))))
        object.__setattr__(self, "adapter", self.adapter.strip())
        object.__setattr__(self, "version", self.version.strip())
        if not self.adapter or not self.version:
            raise ValueError("projection adapter and version must not be blank")
        return self

    def __str__(self) -> str:
        return f"{self.consumer.format()}<-{','.join(item.format() for item in self.producer_refs)}:{self.adapter}:{self.version}"


class ArtifactSnapshotEntry(_PlanModel):
    """Current artifact identity/fingerprint pinned for an impact input or output."""

    target: RevisionTarget
    digest: str | None = None

    @model_validator(mode="after")
    def validate_fingerprint(self) -> ArtifactSnapshotEntry:
        digest = self.digest.strip() if self.digest is not None else None
        if self.digest is not None and not digest:
            raise ValueError("artifact snapshot digest must not be blank")
        if digest is not None:
            object.__setattr__(self, "digest", digest)
        if self.target.artifact_version_id is None and digest is None:
            raise ValueError("artifact snapshot requires target version or digest")
        return self

    @property
    def trace_ref(self) -> TraceRef:
        return _trace_ref(self.target)

    @property
    def fingerprint(self) -> str:
        return (
            f"digest:{self.digest}"
            if self.digest is not None
            else f"version:{self.target.artifact_version_id}"
        )


class RtmEvidence(_PlanModel):
    pre_trace_digest: str = Field(min_length=64, max_length=64)
    impacted_refs: tuple[TraceRef, ...] = Field(min_length=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def normalize(self) -> RtmEvidence:
        object.__setattr__(self, "impacted_refs", tuple(sorted(set(self.impacted_refs))))
        object.__setattr__(self, "reason", self.reason.strip())
        if not self.reason:
            raise ValueError("RTM evidence reason must not be blank")
        return self


class DependencyRecord(_PlanModel):
    producer_ref: TraceRef
    producer_revision_or_digest: str = Field(min_length=1)
    relation: Literal["derives_from", "uses_contract", "projects"]


class ExecutionUnit(_PlanModel):
    execution_unit_id: str = Field(min_length=1)
    artifact: RevisionTarget
    owner: Literal["requirements", "design", "implementation", "testing"]
    action: ExecutionAction
    reason: str = Field(min_length=1)
    rtm_evidence: RtmEvidence
    dependencies: tuple[DependencyRecord, ...] = ()
    depends_on_unit_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid_unit(self) -> ExecutionUnit:
        if self.owner != self.artifact.owner:
            raise ValueError("execution unit owner must match its artifact owner")
        identifier = self.execution_unit_id.strip()
        reason = self.reason.strip()
        if not identifier or not reason:
            raise ValueError("execution unit ID and reason must not be blank")
        dependencies = tuple(sorted(set(self.depends_on_unit_ids)))
        if identifier in dependencies:
            raise ValueError("execution unit cannot depend on itself")
        object.__setattr__(self, "execution_unit_id", identifier)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "depends_on_unit_ids", dependencies)
        object.__setattr__(
            self,
            "dependencies",
            tuple(
                sorted(
                    set(self.dependencies),
                    key=lambda item: (
                        item.producer_ref,
                        item.producer_revision_or_digest,
                        item.relation,
                    ),
                )
            ),
        )
        return self


def _topological_units(units: Iterable[ExecutionUnit]) -> tuple[ExecutionUnit, ...]:
    values = tuple(units)
    by_id = {item.execution_unit_id: item for item in values}
    if len(by_id) != len(values):
        raise ValueError("execution unit IDs must be unique")
    pending = {item.execution_unit_id: set(item.depends_on_unit_ids) for item in values}
    if any(not dependencies <= set(by_id) for dependencies in pending.values()):
        raise ValueError("execution unit dependency is not in this ChangeSet")
    ordered: list[ExecutionUnit] = []
    while pending:
        ready = sorted(
            identifier for identifier, dependencies in pending.items() if not dependencies
        )
        if not ready:
            raise ValueError("execution unit dependencies contain a cycle")
        for identifier in ready:
            ordered.append(by_id[identifier])
            pending.pop(identifier)
        completed = set(ready)
        for dependencies in pending.values():
            dependencies.difference_update(completed)
    return tuple(ordered)


def _base_revision_problem(
    revisions: Iterable[BaseRevision], snapshots: Iterable[ArtifactSnapshotEntry]
) -> str | None:
    values = tuple(snapshots)
    for base in revisions:
        matches = [item for item in values if item.target.artifact_type == base.artifact_type]
        fingerprints = {(item.target.artifact_version_id, item.digest) for item in matches}
        if not matches or len(fingerprints) != 1:
            return "base revision has no unique current artifact fingerprint"
        version, digest = next(iter(fingerprints))
        if base.version_id is not None and base.version_id != version:
            return "base revision version is stale against current artifact snapshot"
        if base.digest is not None and base.digest != digest:
            return "base revision digest is stale against current artifact snapshot"
    return None


def _change_set_digest(
    *,
    app_id: str,
    question_id: str,
    question_version: int,
    decision_id: str,
    decision_digest: str,
    revisions: Iterable[BaseRevision],
    owner: RevisionTarget,
    trace: RtmSnapshot,
    impact: Iterable[TraceRef],
    units: Iterable[ExecutionUnit],
    artifact_snapshot: Iterable[ArtifactSnapshotEntry],
) -> str:
    return _digest(
        {
            "app": app_id,
            "question": [question_id, question_version],
            "decision": decision_id,
            "decisionDigest": decision_digest,
            "revisions": sorted(_revision_key(item) for item in revisions),
            "owner": _target_fingerprint(owner),
            "trace": trace.digest,
            "impact": sorted(item.format() for item in impact),
            "artifacts": [
                (_target_fingerprint(item.target), item.digest)
                for item in sorted(artifact_snapshot, key=lambda value: value.trace_ref)
            ],
            "units": [
                (
                    item.execution_unit_id,
                    _target_fingerprint(item.artifact),
                    item.action.value,
                    item.reason,
                    item.rtm_evidence.pre_trace_digest,
                    tuple(ref.format() for ref in item.rtm_evidence.impacted_refs),
                    item.rtm_evidence.reason,
                    item.depends_on_unit_ids,
                    tuple(
                        (
                            dependency.producer_ref.format(),
                            dependency.producer_revision_or_digest,
                            dependency.relation,
                        )
                        for dependency in item.dependencies
                    ),
                )
                for item in units
            ],
        }
    )


class ChangeSet(_PlanModel):
    change_set_id: str = Field(min_length=1)
    app_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    question_version: int = Field(ge=1)
    decision_id: str = Field(min_length=1)
    decision_snapshot: Decision
    decision_digest: str = Field(min_length=64, max_length=64)
    base_revisions: tuple[BaseRevision, ...] = Field(min_length=1)
    authoritative_owner: RevisionTarget
    execution_units: tuple[ExecutionUnit, ...] = Field(min_length=1)
    artifact_snapshot: tuple[ArtifactSnapshotEntry, ...] = Field(min_length=1)
    pre_change_trace: RtmSnapshot
    pre_change_impact: tuple[TraceRef, ...] = Field(min_length=1)
    plan_digest: str = Field(min_length=64, max_length=64)
    status: Literal["PLANNED"] = "PLANNED"

    @model_validator(mode="after")
    def validate_change_set(self) -> ChangeSet:
        revisions = tuple(sorted(set(self.base_revisions), key=_revision_key))
        impact = tuple(sorted(set(self.pre_change_impact)))
        units = _topological_units(self.execution_units)
        if self.decision_snapshot.status != "NORMALIZED":
            raise ValueError("ChangeSet decision snapshot must be NORMALIZED")
        if set(map(_revision_key, self.decision_snapshot.base_revisions)) != set(
            map(_revision_key, revisions)
        ):
            raise ValueError("ChangeSet base revisions must match decision snapshot")
        if self.authoritative_owner not in self.decision_snapshot.authoritative_targets:
            raise ValueError("authoritative owner must be a decision target")
        root_ref = _trace_ref(self.authoritative_owner)
        try:
            expected_impact, _required_refs = _planning_impact(
                self.pre_change_trace.trace, root_ref
            )
        except ChangePlanError as error:
            raise ValueError(str(error)) from error
        if set(impact) != set(expected_impact):
            raise ValueError("pre-change impact must equal root and all RTM downstream refs")
        object.__setattr__(self, "base_revisions", revisions)
        object.__setattr__(self, "pre_change_impact", impact)
        snapshots = tuple(sorted(self.artifact_snapshot, key=lambda item: item.trace_ref))
        if len({item.trace_ref for item in snapshots}) != len(snapshots) or not {
            _trace_ref(item.artifact) for item in units
        } <= {item.trace_ref for item in snapshots}:
            raise ValueError("artifact snapshot must cover execution units without duplicates")
        snapshot_by_ref = {item.trace_ref: item for item in snapshots}
        if snapshot_by_ref.get(_trace_ref(self.authoritative_owner)) is None or (
            snapshot_by_ref[_trace_ref(self.authoritative_owner)].target != self.authoritative_owner
        ):
            raise ValueError("authoritative owner must match artifact snapshot")
        if problem := _base_revision_problem(revisions, snapshots):
            raise ValueError(problem)
        unit_refs = tuple(_trace_ref(item.artifact) for item in units)
        if len(set(unit_refs)) != len(unit_refs):
            raise ValueError("execution unit artifacts must be unique")
        if set(unit_refs) != set(impact):
            raise ValueError("execution units must exactly cover pre-change impact")
        unit_id_by_ref = {_trace_ref(item.artifact): item.execution_unit_id for item in units}
        for unit in units:
            unit_ref = _trace_ref(unit.artifact)
            if snapshot_by_ref[unit_ref].target != unit.artifact:
                raise ValueError("execution unit artifact must match artifact snapshot")
            if unit.rtm_evidence.pre_trace_digest != self.pre_change_trace.digest or set(
                unit.rtm_evidence.impacted_refs
            ) != set(impact):
                raise ValueError("execution unit RTM evidence does not match ChangeSet")
            expected_producers = set(self.pre_change_trace.trace.sources(unit_ref))
            actual_producers = {item.producer_ref for item in unit.dependencies}
            if len(actual_producers) != len(unit.dependencies) or (
                actual_producers != expected_producers
            ):
                raise ValueError("execution unit dependencies must match RTM direct sources")
            expected_unit_ids = {
                unit_id_by_ref[producer] for producer in expected_producers & set(impact)
            }
            if set(unit.depends_on_unit_ids) != expected_unit_ids:
                raise ValueError("execution unit dependency IDs must match impacted producers")
            for dependency in unit.dependencies:
                producer = snapshot_by_ref.get(dependency.producer_ref)
                if (
                    producer is None
                    or dependency.producer_revision_or_digest != producer.fingerprint
                ):
                    raise ValueError("dependency producer does not match artifact snapshot")
            if unit.action is ExecutionAction.REPROJECT:
                contracts = tuple(
                    contract
                    for contract in self.pre_change_trace.projection_contracts
                    if contract.consumer == unit_ref
                    and set(contract.producer_refs) == actual_producers
                )
                if len(contracts) != 1 or any(
                    dependency.relation != "projects" for dependency in unit.dependencies
                ):
                    raise ValueError("reproject unit requires one exact projection contract")
        meaning = self.decision_snapshot.normalized_meaning
        if meaning is None:
            raise ValueError("ChangeSet decision snapshot has no normalized meaning")
        try:
            root_action = OwnershipRouter().direct_action(
                self.authoritative_owner, meaning.semantic_scope
            )
        except ChangePlanError as error:
            raise ValueError(str(error)) from error
        expected_actions = _planned_actions(
            trace=self.pre_change_trace,
            impact=impact,
            catalog=snapshot_by_ref,
            root_ref=root_ref,
            root_action=root_action,
        )
        if any(unit.action is not expected_actions[_trace_ref(unit.artifact)] for unit in units):
            raise ValueError("execution unit action violates the registered planning policy")
        if self.decision_digest != _decision_digest(self.decision_snapshot):
            raise ValueError("ChangeSet decision digest does not match decision snapshot")
        if (
            self.decision_snapshot.decision_id != self.decision_id
            or self.decision_snapshot.app_id != self.app_id
            or self.decision_snapshot.question_id != self.question_id
            or self.decision_snapshot.question_version != self.question_version
        ):
            raise ValueError("ChangeSet decision snapshot is not pinned to its identity")
        object.__setattr__(self, "execution_units", units)
        object.__setattr__(self, "artifact_snapshot", snapshots)
        if self.plan_digest != _change_set_digest(
            app_id=self.app_id,
            question_id=self.question_id,
            question_version=self.question_version,
            decision_id=self.decision_id,
            decision_digest=self.decision_digest,
            revisions=revisions,
            owner=self.authoritative_owner,
            trace=self.pre_change_trace,
            impact=impact,
            units=units,
            artifact_snapshot=snapshots,
        ):
            raise ValueError("ChangeSet plan digest does not match its frozen content")
        return self


class OwnershipRouter:
    """Small explicit registry. Missing or multiple routes are planning errors."""

    _DIRECT: ClassVar[dict[tuple[str, str], ExecutionAction]] = {
        **{
            (kind, scope): ExecutionAction.REBUILD
            for kind in ("use_case", "use_case_spec", "actor", "relationship")
            for scope in ("contract", "behavior")
        },
        **{
            (kind, scope): ExecutionAction.REBUILD
            for kind in ("class", "operation", "collaboration", "call")
            for scope in ("presentation", "contract", "behavior")
        },
        **{(kind, "implementation"): ExecutionAction.REBUILD for kind in ("file", "task")},
    }

    def direct_action(self, target: RevisionTarget, semantic_scope: str) -> ExecutionAction:
        action = self._DIRECT.get((target.kind, semantic_scope))
        if action is None:
            raise ChangePlanError("ownership route is unsupported or unregistered")
        return action

    def route(
        self, *, question: Question, decision: Decision
    ) -> tuple[RevisionTarget, ExecutionAction]:
        meaning = decision.normalized_meaning
        if meaning is None:
            raise ChangePlanError("decision has no normalized meaning")
        targets = tuple(sorted(decision.authoritative_targets, key=_target_key))
        if question.trigger.category == "specification_gap":
            if meaning.semantic_scope not in {"contract", "behavior"}:
                raise ChangePlanError("specification_gap scope is unsupported for UC specification")
            candidates = tuple(
                item
                for item in targets
                if item.kind == "use_case_spec" and item.owner == "requirements"
            )
            if len(targets) != 1 or len(candidates) != 1:
                raise ChangePlanError(
                    "specification_gap requires exactly one UC specification owner"
                )
            return candidates[0], ExecutionAction.REBUILD
        routes = [
            (target, self.direct_action(target, meaning.semantic_scope)) for target in targets
        ]
        owners = {(target.ref, target.owner, action) for target, action in routes}
        if len(owners) != 1:
            raise ChangePlanError("ownership route is ambiguous")
        return routes[0]


def _planned_actions(
    *,
    trace: RtmSnapshot,
    impact: Iterable[TraceRef],
    catalog: dict[TraceRef, ArtifactSnapshotEntry],
    root_ref: TraceRef,
    root_action: ExecutionAction,
) -> dict[TraceRef, ExecutionAction]:
    impact_refs = tuple(sorted(set(impact)))
    actions: dict[TraceRef, ExecutionAction] = {root_ref: root_action}
    for ref in impact_refs:
        if ref == root_ref:
            continue
        artifact = catalog[ref].target
        if artifact.kind == "class" or artifact.artifact_type == "class_diagram":
            actions[ref] = ExecutionAction.REBUILD
        elif artifact.kind == "sequence" or artifact.artifact_type == "sequence_diagram":
            direct = tuple(sorted(set(trace.trace.sources(ref))))
            contracts = tuple(
                contract
                for contract in trace.projection_contracts
                if contract.consumer == ref and contract.producer_refs == direct
            )
            actions[ref] = (
                ExecutionAction.REPROJECT
                if len(contracts) == 1
                and all(catalog[producer].target.kind == "class" for producer in direct)
                else ExecutionAction.STALE
            )
        else:
            actions[ref] = ExecutionAction.STALE
    changed = True
    while changed:
        changed = False
        for ref in impact_refs:
            if ref == root_ref or actions[ref] not in {
                ExecutionAction.REBUILD,
                ExecutionAction.REPROJECT,
            }:
                continue
            producers = set(trace.trace.sources(ref)) & set(impact_refs)
            if any(actions[producer] is ExecutionAction.STALE for producer in producers):
                actions[ref] = ExecutionAction.STALE
                changed = True
    return actions


def _require_acyclic_trace(trace: ArtifactTrace, refs: Iterable[TraceRef]) -> None:
    allowed = set(refs)
    visiting: set[TraceRef] = set()
    visited: set[TraceRef] = set()

    def visit(ref: TraceRef) -> None:
        if ref in visiting:
            raise ChangePlanError("pre-change RTM impact contains a cycle")
        if ref in visited:
            return
        visiting.add(ref)
        for producer in trace.sources(ref):
            if producer in allowed:
                visit(producer)
        visiting.remove(ref)
        visited.add(ref)

    for ref in sorted(allowed):
        visit(ref)


def _planning_impact(
    trace: ArtifactTrace, root_ref: TraceRef
) -> tuple[tuple[TraceRef, ...], frozenset[TraceRef]]:
    if root_ref not in trace.refs:
        raise ChangePlanError("authoritative owner is absent from pre-change RTM")
    impact = tuple(sorted({root_ref, *trace.downstream(root_ref)}))
    _require_acyclic_trace(trace, impact)
    required_refs = set(impact)
    for ref in impact:
        required_refs.update(trace.sources(ref))
    if set(trace.unknown_source_refs) & required_refs:
        raise ChangePlanError("RTM has an unknown direct producer")
    return impact, frozenset(required_refs)


def plan_change_set(
    *,
    change_set_id: str,
    question: Question,
    decision: Decision,
    artifact_snapshot: Iterable[ArtifactSnapshotEntry],
    pre_change_trace: RtmSnapshot,
    router: OwnershipRouter | None = None,
) -> ChangeSet:
    """Create a deterministic draft plan after validating all envelope evidence."""
    if decision.status != "NORMALIZED":
        raise ChangePlanError("only NORMALIZED decisions can be planned")
    if question.status not in {"OPEN", "ANSWERED"}:
        raise ChangePlanError("terminal or non-open question cannot be planned")
    if decision.app_id != question.app_id or decision.question_id != question.question_id:
        raise ChangePlanError("decision does not belong to the question")
    if decision.question_version != question.question_version:
        raise ChangePlanError("decision question version is stale")
    if decision.answer_mode == "option":
        option = next(
            (item for item in question.options if item.option_id == decision.selected_option_id),
            None,
        )
        if option is None:
            raise ChangePlanError("selected option is absent from the question")
        payload = option.decision_payload
        if (
            decision.normalized_meaning != payload.normalized_meaning
            or tuple(item.ref for item in decision.authoritative_targets)
            != payload.authoritative_target_refs
            or decision.preserved_constraints != payload.preserved_constraints
        ):
            raise ChangePlanError("option decision does not match its question payload")
    elif not question.allow_free_text:
        raise ChangePlanError("question does not allow free-text decisions")
    if set(map(_revision_key, decision.base_revisions)) != set(
        map(_revision_key, question.base_revisions)
    ):
        raise ChangePlanError("decision base revisions do not match the question")
    meaning = decision.normalized_meaning
    assert meaning is not None
    if meaning.semantic_scope not in question.decision_policy.allowed_semantic_scopes:
        raise ChangePlanError("decision semantic scope is disallowed by question policy")
    if meaning.change_type not in question.decision_policy.allowed_change_types:
        raise ChangePlanError("decision change type is disallowed by question policy")
    if not set(question.decision_policy.required_preserved_constraints) <= set(
        decision.preserved_constraints
    ):
        raise ChangePlanError("decision omits required preserved constraints")
    candidates = {item.ref: item for item in question.authority_candidates}
    if any(candidates.get(item.ref) != item for item in decision.authoritative_targets):
        raise ChangePlanError("decision target does not match question authority candidate")
    owner, root_action = (router or OwnershipRouter()).route(question=question, decision=decision)
    snapshot_values = tuple(artifact_snapshot)
    catalog = {item.trace_ref: item for item in snapshot_values}
    if (
        len(catalog) != len(snapshot_values)
        or _trace_ref(owner) not in catalog
        or catalog[_trace_ref(owner)].target != owner
    ):
        raise ChangePlanError("target catalog is incomplete or ambiguous")
    root_ref = _trace_ref(owner)
    impact, required_refs = _planning_impact(pre_change_trace.trace, root_ref)
    missing = [ref.format() for ref in required_refs if ref not in catalog]
    if missing:
        raise ChangePlanError("RTM input has no current artifact snapshot: " + ", ".join(missing))
    if problem := _base_revision_problem(question.base_revisions, snapshot_values):
        raise ChangePlanError(problem)
    evidence = RtmEvidence(
        pre_trace_digest=pre_change_trace.digest,
        impacted_refs=impact,
        reason="exact pre-change RTM impact",
    )
    units: list[ExecutionUnit] = []
    actions = _planned_actions(
        trace=pre_change_trace,
        impact=impact,
        catalog=catalog,
        root_ref=root_ref,
        root_action=root_action,
    )
    for ref in impact:
        artifact = catalog[ref].target
        all_producer_refs = tuple(sorted(set(pre_change_trace.trace.sources(ref))))
        producer_refs = tuple(producer for producer in all_producer_refs if producer in impact)
        if ref == root_ref:
            action, reason, dependencies = root_action, "authoritative revision", ()
        else:
            action = actions[ref]
            dependencies = tuple(f"unit:{producer.format()}" for producer in producer_refs)
            reason = {
                ExecutionAction.REBUILD: "registered class bundle rebuild",
                ExecutionAction.REPROJECT: "registered sequence projection",
                ExecutionAction.STALE: "affected but no supported safe adapter",
            }[action]
        units.append(
            ExecutionUnit(
                execution_unit_id=f"unit:{ref.format()}",
                artifact=artifact,
                owner=artifact.owner,
                action=action,
                reason=reason,
                rtm_evidence=evidence,
                dependencies=tuple(
                    DependencyRecord(
                        producer_ref=producer,
                        producer_revision_or_digest=catalog[producer].fingerprint,
                        relation="projects"
                        if action is ExecutionAction.REPROJECT
                        else "derives_from",
                    )
                    for producer in all_producer_refs
                ),
                depends_on_unit_ids=dependencies,
            )
        )
    units = list(_topological_units(units))
    return ChangeSet(
        change_set_id=change_set_id,
        app_id=question.app_id,
        question_id=question.question_id,
        question_version=question.question_version,
        decision_id=decision.decision_id,
        decision_snapshot=decision,
        decision_digest=_decision_digest(decision),
        base_revisions=question.base_revisions,
        authoritative_owner=owner,
        execution_units=tuple(units),
        artifact_snapshot=tuple(
            sorted((catalog[ref] for ref in required_refs), key=lambda item: item.trace_ref)
        ),
        pre_change_trace=pre_change_trace,
        pre_change_impact=impact,
        plan_digest=_change_set_digest(
            app_id=question.app_id,
            question_id=question.question_id,
            question_version=question.question_version,
            decision_id=decision.decision_id,
            decision_digest=_decision_digest(decision),
            revisions=question.base_revisions,
            owner=owner,
            trace=pre_change_trace,
            impact=impact,
            units=units,
            artifact_snapshot=tuple(
                sorted((catalog[ref] for ref in required_refs), key=lambda item: item.trace_ref)
            ),
        ),
    )


__all__ = [
    "ArtifactSnapshotEntry",
    "ChangePlanError",
    "ChangeSet",
    "DependencyRecord",
    "ExecutionAction",
    "ExecutionUnit",
    "OwnershipRouter",
    "ProjectionContract",
    "RtmEvidence",
    "RtmSnapshot",
    "plan_change_set",
]
