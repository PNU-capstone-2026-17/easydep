"""Small durable feedback prototype on the existing WorkspaceCommand ledger.

The workspace command executor owns single-flight message processing. This
repository persists that command's progress; it does not add another lock layer.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from contextlib import contextmanager
from enum import StrEnum
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import App, WorkspaceCommand
from app.db.session import get_session_factory

from .contracts import RevisionTarget
from .feedback_change_plan import ChangeSet, ExecutionAction
from .feedback_envelope import Decision

_ACTION = "feedback_revision"
_NS = uuid.UUID("f8be5c3f-43d2-5a6e-864a-3dfa1ec6ccf6")


class FeedbackRepositoryError(ValueError):
    pass


class CheckpointStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AttemptStatus(StrEnum):
    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    OUTCOME_UNKNOWN = "ATTEMPT_OUTCOME_UNKNOWN"


RemoteAttemptStatus = AttemptStatus


class ArtifactValidity(StrEnum):
    VALID = "valid"
    STALE = "stale"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CheckpointVersions(_Frozen):
    schema_version: str
    prompt_version: str | None = None
    validator_version: str
    projection_version: str | None = None


class ExecutionVersionSpec(_Frozen):
    execution_unit_id: str
    versions: CheckpointVersions


class ExecutionCheckpoint(_Frozen):
    execution_unit_id: str
    action: ExecutionAction
    input_fingerprints: tuple[str, ...]
    versions: CheckpointVersions
    output_digest: str | None = None
    validation_passed: bool = False
    attempts: int = 0
    status: CheckpointStatus = CheckpointStatus.PENDING

    @model_validator(mode="after")
    def completed_has_validated_output(self) -> ExecutionCheckpoint:
        if self.status is CheckpointStatus.COMPLETED and (
            not self.validation_passed or not self.output_digest
        ):
            raise ValueError("completed checkpoint requires validated output")
        return self


class RemoteAttempt(_Frozen):
    execution_unit_id: str
    attempt_no: int
    status: AttemptStatus
    provider: str
    request_digest: str
    outcome_digest: str | None = None


class AcceptedArtifactEntry(_Frozen):
    target: RevisionTarget
    digest: str | None = None
    validity: ArtifactValidity

    @property
    def key(self):
        return (self.target.kind, self.target.element_id)


class AcceptedManifest(_Frozen):
    head_id: str
    previous_head_id: str | None
    publish_sequence: int
    entries: tuple[AcceptedArtifactEntry, ...]


class FeedbackAggregate(_Frozen):
    idempotency_key: str
    source_identity: str
    decision: Decision
    change_set: ChangeSet | None = None
    execution_versions: tuple[ExecutionVersionSpec, ...] = ()
    checkpoints: tuple[ExecutionCheckpoint, ...] = ()
    attempts: tuple[RemoteAttempt, ...] = ()
    revision: int = 1
    manifest: AcceptedManifest | None = None


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _id(*parts: str) -> str:
    return str(uuid.uuid5(_NS, sha256(_json(parts).encode()).hexdigest()))


def expected_input_fingerprints(change_set: ChangeSet, unit) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                f"plan:{change_set.plan_digest}",
                *(
                    f"producer:{d.producer_ref.format()}:{d.producer_revision_or_digest}"
                    for d in unit.dependencies
                ),
            }
        )
    )


def checkpoint_reusable(
    checkpoint: ExecutionCheckpoint,
    *,
    action: ExecutionAction,
    input_fingerprints: Iterable[str],
    versions: CheckpointVersions,
) -> bool:
    return (
        checkpoint.status is CheckpointStatus.COMPLETED
        and checkpoint.validation_passed
        and bool(checkpoint.output_digest)
        and checkpoint.action is action
        and checkpoint.input_fingerprints == tuple(sorted(input_fingerprints))
        and checkpoint.versions == versions
    )


class FeedbackRepository:
    def __init__(self, factory: sessionmaker[Session] | None = None):
        self._factory = factory or get_session_factory()

    @contextmanager
    def _tx(self):
        s = self._factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def _row(self, s, app, key):
        row = s.get(WorkspaceCommand, _id("feedback-revision/v1", app, key))
        if row is None or row.action != _ACTION or row.app_id != app:
            raise FeedbackRepositoryError("aggregate absent")
        return row

    def _load(self, row):
        try:
            return FeedbackAggregate.model_validate(row.payload["feedback_revision"])
        except Exception as e:
            raise FeedbackRepositoryError("aggregate is corrupt") from e

    def create(
        self,
        *,
        app_id: str,
        idempotency_key: str,
        source_identity: str,
        decision: Decision,
        stage: Literal["requirements", "design", "implementation", "testing"] = "requirements",
    ):
        if (
            not idempotency_key
            or not source_identity
            or idempotency_key.strip() != idempotency_key
            or source_identity.strip() != source_identity
        ):
            raise FeedbackRepositoryError("idempotency key/source must be trimmed")
        if decision.app_id != app_id:
            raise FeedbackRepositoryError("app mismatch")
        value = FeedbackAggregate(
            idempotency_key=idempotency_key,
            source_identity=source_identity,
            decision=decision,
        )
        with self._tx() as s:
            if s.get(App, app_id) is None:
                raise FeedbackRepositoryError("app does not exist")
            row = s.get(WorkspaceCommand, _id("feedback-revision/v1", app_id, idempotency_key))
            if row is not None:
                old = self._load(row)
                if (
                    old.decision == decision
                    and old.source_identity == source_identity
                    and row.stage == stage
                ):
                    return old
                raise FeedbackRepositoryError("idempotency conflict")
            for candidate in s.scalars(
                select(WorkspaceCommand).where(
                    WorkspaceCommand.app_id == app_id,
                    WorkspaceCommand.action == _ACTION,
                )
            ):
                existing = self._load(candidate)
                if (
                    existing.source_identity == source_identity
                    or existing.decision.decision_id == decision.decision_id
                ):
                    raise FeedbackRepositoryError("decision was already submitted")
            s.add(
                WorkspaceCommand(
                    command_id=_id("feedback-revision/v1", app_id, idempotency_key),
                    app_id=app_id,
                    action=_ACTION,
                    stage=stage,
                    status="QUEUED",
                    payload={"feedback_revision": value.model_dump(mode="json")},
                )
            )
        return value

    def load(self, app_id, key):
        with self._tx() as s:
            row = s.get(WorkspaceCommand, _id("feedback-revision/v1", app_id, key))
            return self._load(row) if row else None

    def attach_change_set(
        self,
        app,
        key,
        change_set: ChangeSet,
        execution_versions: Iterable[ExecutionVersionSpec],
        *,
        expected_revision: int,
    ):
        specs = tuple(sorted(execution_versions, key=lambda x: x.execution_unit_id))
        with self._tx() as s:
            row = self._row(s, app, key)
            old = self._load(row)
            if old.change_set is not None:
                if (
                    expected_revision in {old.revision, old.revision - 1}
                    and old.change_set == change_set
                    and old.execution_versions == specs
                ):
                    return old
                raise FeedbackRepositoryError("attached plan conflict")
            if (
                old.revision != expected_revision
                or change_set.app_id != app
                or change_set.decision_snapshot != old.decision
            ):
                raise FeedbackRepositoryError("attach conflict")
            for candidate in s.scalars(
                select(WorkspaceCommand).where(
                    WorkspaceCommand.app_id == app,
                    WorkspaceCommand.action == _ACTION,
                    WorkspaceCommand.command_id != row.command_id,
                )
            ):
                attached = self._load(candidate).change_set
                if attached is not None and attached.change_set_id == change_set.change_set_id:
                    raise FeedbackRepositoryError("ChangeSet was already submitted")
            unit_ids = {x.execution_unit_id for x in change_set.execution_units}
            if len(specs) != len(unit_ids) or {x.execution_unit_id for x in specs} != unit_ids:
                raise FeedbackRepositoryError("version specs do not cover units")
            new = old.model_copy(
                update={
                    "change_set": change_set,
                    "execution_versions": specs,
                    "revision": old.revision + 1,
                }
            )
            row.payload = {"feedback_revision": new.model_dump(mode="json")}
            return new

    def _mutate(self, app, key, revision, fn):
        with self._tx() as s:
            row = self._row(s, app, key)
            old = self._load(row)
            if row.status == "COMPLETED" or old.revision != revision:
                raise FeedbackRepositoryError("revision conflict or committed")
            new = fn(old).model_copy(update={"revision": old.revision + 1})
            row.payload = {"feedback_revision": new.model_dump(mode="json")}
            row.status = "RUNNING"
            return new

    def save_checkpoint(self, app, key, checkpoint: ExecutionCheckpoint, *, expected_revision: int):
        def update(old):
            if old.change_set is None:
                raise FeedbackRepositoryError("plan required")
            unit = next(
                (
                    x
                    for x in old.change_set.execution_units
                    if x.execution_unit_id == checkpoint.execution_unit_id
                ),
                None,
            )
            specs = {x.execution_unit_id: x.versions for x in old.execution_versions}
            if (
                unit is None
                or unit.action is not checkpoint.action
                or checkpoint.versions != specs[checkpoint.execution_unit_id]
                or checkpoint.input_fingerprints
                != expected_input_fingerprints(old.change_set, unit)
            ):
                raise FeedbackRepositoryError("checkpoint mismatch")
            attempts = [
                item
                for item in old.attempts
                if item.execution_unit_id == checkpoint.execution_unit_id
            ]
            if unit.action is ExecutionAction.REBUILD and (
                checkpoint.attempts != len(attempts)
                or (
                    checkpoint.status is CheckpointStatus.COMPLETED
                    and (not attempts or attempts[-1].status is not AttemptStatus.SUCCEEDED)
                )
            ):
                raise FeedbackRepositoryError("checkpoint attempt evidence mismatch")
            if unit.action is not ExecutionAction.REBUILD and checkpoint.attempts != 0:
                raise FeedbackRepositoryError("deterministic checkpoint cannot claim LLM attempts")
            values = {x.execution_unit_id: x for x in old.checkpoints}
            prior = values.get(checkpoint.execution_unit_id)
            if prior and prior.status is CheckpointStatus.COMPLETED and prior != checkpoint:
                raise FeedbackRepositoryError("completed checkpoint immutable")
            values[checkpoint.execution_unit_id] = checkpoint
            return old.model_copy(update={"checkpoints": tuple(values.values())})

        return self._mutate(app, key, expected_revision, update)

    def start_attempt(self, app, key, attempt: RemoteAttempt, *, expected_revision: int):
        if attempt.status is not AttemptStatus.STARTED:
            raise FeedbackRepositoryError("attempt must start")

        def update(old):
            if old.change_set is None:
                raise FeedbackRepositoryError("plan required")
            unit = next(
                (
                    x
                    for x in old.change_set.execution_units
                    if x.execution_unit_id == attempt.execution_unit_id
                ),
                None,
            )
            if unit is None or unit.action is not ExecutionAction.REBUILD:
                raise FeedbackRepositoryError("remote attempt requires rebuild")
            prior = [x for x in old.attempts if x.execution_unit_id == attempt.execution_unit_id]
            if (
                any(x.status is AttemptStatus.STARTED for x in prior)
                or attempt.attempt_no != len(prior) + 1
            ):
                raise FeedbackRepositoryError("attempt overlap")
            cp = next(
                (x for x in old.checkpoints if x.execution_unit_id == attempt.execution_unit_id),
                None,
            )
            if cp and cp.status is CheckpointStatus.COMPLETED:
                raise FeedbackRepositoryError("completed checkpoint forbids attempt")
            return old.model_copy(update={"attempts": (*old.attempts, attempt)})

        return self._mutate(app, key, expected_revision, update)

    def finish_attempt(
        self,
        app,
        key,
        attempt: RemoteAttempt,
        *,
        status: AttemptStatus,
        expected_revision: int,
        outcome_digest: str | None = None,
    ):
        if status not in {
            AttemptStatus.SUCCEEDED,
            AttemptStatus.FAILED,
            AttemptStatus.OUTCOME_UNKNOWN,
        }:
            raise FeedbackRepositoryError("attempt must finish in a terminal state")
        if status is AttemptStatus.SUCCEEDED and not outcome_digest:
            raise FeedbackRepositoryError("successful attempt requires an outcome digest")
        if status is AttemptStatus.OUTCOME_UNKNOWN and outcome_digest is not None:
            raise FeedbackRepositoryError("unknown attempt cannot claim an outcome digest")

        def update(old):
            values = list(old.attempts)
            for i, item in enumerate(values):
                if (item.execution_unit_id, item.attempt_no) == (
                    attempt.execution_unit_id,
                    attempt.attempt_no,
                ) and item.status is AttemptStatus.STARTED:
                    if (
                        item.provider != attempt.provider
                        or item.request_digest != attempt.request_digest
                    ):
                        raise FeedbackRepositoryError("attempt request mismatch")
                    values[i] = item.model_copy(
                        update={"status": status, "outcome_digest": outcome_digest}
                    )
                    return old.model_copy(update={"attempts": tuple(values)})
            raise FeedbackRepositoryError("attempt terminal or absent")

        return self._mutate(app, key, expected_revision, update)

    def mark_attempt_outcome_unknown(
        self, app, key, attempt: RemoteAttempt, *, expected_revision: int
    ):
        return self.finish_attempt(
            app,
            key,
            attempt,
            status=AttemptStatus.OUTCOME_UNKNOWN,
            expected_revision=expected_revision,
        )

    def _current_head(self, s, app) -> tuple[str | None, int]:
        manifests = [
            self._load(row).manifest
            for row in s.scalars(
                select(WorkspaceCommand).where(
                    WorkspaceCommand.app_id == app, WorkspaceCommand.action == _ACTION
                )
            )
        ]
        committed = [manifest for manifest in manifests if manifest is not None]
        if not committed:
            return None, 0
        current = max(committed, key=lambda manifest: manifest.publish_sequence)
        return current.head_id, current.publish_sequence

    def current_head(self, app):
        with self._tx() as s:
            head, _ = self._current_head(s, app)
            return head

    def publish(
        self,
        app,
        key,
        *,
        expected_revision: int,
        expected_base_head: str | None,
        entries: Iterable[AcceptedArtifactEntry],
    ):
        with self._tx() as s:
            row = self._row(s, app, key)
            old = self._load(row)
            entries = tuple(sorted(entries, key=lambda entry: entry.key))
            if old.manifest:
                if (
                    expected_revision in {old.revision, old.revision - 1}
                    and old.manifest.previous_head_id == expected_base_head
                    and old.manifest.entries == entries
                ):
                    return old
                raise FeedbackRepositoryError("published request conflicts with manifest")
            head, sequence = self._current_head(s, app)
            if (
                old.revision != expected_revision
                or old.change_set is None
                or head != expected_base_head
            ):
                raise FeedbackRepositoryError("stale base or revision")
            by = {x.execution_unit_id: x for x in old.checkpoints}
            units = {x.execution_unit_id: x for x in old.change_set.execution_units}
            if len(entries) != len(units) or {e.key for e in entries} != {
                (u.artifact.kind, u.artifact.element_id) for u in units.values()
            }:
                raise FeedbackRepositoryError("manifest coverage")
            for e in entries:
                unit = next(
                    u for u in units.values() if (u.artifact.kind, u.artifact.element_id) == e.key
                )
                cp = by.get(unit.execution_unit_id)
                if unit.action is ExecutionAction.STALE:
                    if e.validity is not ArtifactValidity.STALE:
                        raise FeedbackRepositoryError("stale required")
                elif (
                    not cp
                    or not checkpoint_reusable(
                        cp,
                        action=unit.action,
                        input_fingerprints=expected_input_fingerprints(old.change_set, unit),
                        versions=next(
                            x.versions
                            for x in old.execution_versions
                            if x.execution_unit_id == unit.execution_unit_id
                        ),
                    )
                    or e.validity is not ArtifactValidity.VALID
                    or e.digest != cp.output_digest
                ):
                    raise FeedbackRepositoryError("valid checkpoint required")
            base = expected_base_head
            manifest = AcceptedManifest(
                head_id=_id(
                    "manifest",
                    app,
                    old.change_set.change_set_id,
                    str(base),
                    _json([e.model_dump(mode="json") for e in entries]),
                ),
                previous_head_id=base,
                publish_sequence=sequence + 1,
                entries=entries,
            )
            new = old.model_copy(update={"manifest": manifest, "revision": old.revision + 1})
            row.payload = {"feedback_revision": new.model_dump(mode="json")}
            row.status = "COMPLETED"
            return new


__all__ = [
    "AcceptedArtifactEntry",
    "AcceptedManifest",
    "ArtifactValidity",
    "AttemptStatus",
    "CheckpointStatus",
    "CheckpointVersions",
    "ExecutionCheckpoint",
    "ExecutionVersionSpec",
    "FeedbackAggregate",
    "FeedbackRepository",
    "FeedbackRepositoryError",
    "RemoteAttempt",
    "RemoteAttemptStatus",
    "checkpoint_reusable",
    "expected_input_fingerprints",
]
