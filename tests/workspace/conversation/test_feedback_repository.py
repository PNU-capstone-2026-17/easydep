from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.artifact_trace import ArtifactTrace, TraceNode, TraceRef
from app.db.models import App, Base, WorkspaceCommand
from app.workspace.conversation.contracts import RevisionTarget
from app.workspace.conversation.feedback_change_plan import (
    ArtifactSnapshotEntry,
    ExecutionAction,
    ProjectionContract,
    RtmSnapshot,
    plan_change_set,
)
from app.workspace.conversation.feedback_envelope import (
    DecisionMeaning,
    DecisionPayload,
    DecisionPolicy,
    Question,
    QuestionOption,
    answer_option,
)
from app.workspace.conversation.feedback_repository import (
    AcceptedArtifactEntry,
    ArtifactValidity,
    AttemptStatus,
    CheckpointStatus,
    CheckpointVersions,
    ExecutionCheckpoint,
    ExecutionVersionSpec,
    FeedbackRepository,
    FeedbackRepositoryError,
    RemoteAttempt,
    checkpoint_reusable,
    expected_input_fingerprints,
)


def _target(ref: str, kind: str, owner: str, artifact_type: str) -> RevisionTarget:
    return RevisionTarget(
        ref=ref,
        kind=kind,
        element_id=ref.split(":", 1)[1],
        owner=owner,
        artifact_type=artifact_type,
        artifact_version_id=3,
        display_label=ref,
    )


def _bundle(suffix=""):
    root = _target("use_case_spec:UC1", "use_case_spec", "requirements", "usecase")
    cls = _target("class:Enrollment", "class", "design", "class_diagram")
    seq = _target("sequence:Flow", "sequence", "design", "sequence_diagram")
    q = Question(
        question_id="q1" + suffix,
        question_version=1,
        app_id="app",
        source_execution_id="run",
        detected_at={"stage": "design", "artifact_ref": "class:Enrollment"},
        base_revisions=[{"artifact_type": "usecase", "version_id": 3}],
        trigger={"category": "gap"},
        authority_candidates=[root],
        prompt="Clarify",
        options=(
            QuestionOption(
                option_id="apply",
                label="Apply",
                decision_payload=DecisionPayload(
                    normalized_meaning=DecisionMeaning(
                        semantic_scope="contract", requested_effect="retain"
                    ),
                    authoritative_target_refs=(root.ref,),
                ),
            ),
        ),
        decision_policy=DecisionPolicy(
            allowed_semantic_scopes=("contract",),
            allowed_change_types=("modify",),
        ),
    )
    d = answer_option(q, option_id="apply", decision_id="d1" + suffix, source_user_message_id="m1")
    trace = RtmSnapshot(
        trace=ArtifactTrace(
            (
                TraceNode(TraceRef("use_case_spec", "UC1")),
                TraceNode(TraceRef("class", "Enrollment"), (TraceRef("use_case_spec", "UC1"),)),
                TraceNode(TraceRef("sequence", "Flow"), (TraceRef("class", "Enrollment"),)),
            )
        ),
        projection_contracts=(
            ProjectionContract(
                consumer=TraceRef("sequence", "Flow"),
                producer_refs=(TraceRef("class", "Enrollment"),),
                adapter="class_to_sequence",
                version="v1",
            ),
        ),
    )
    catalog = (root, cls, seq)
    snap = tuple(
        ArtifactSnapshotEntry(target=t, digest=str(i) * 64) for i, t in enumerate(catalog, 1)
    )
    cs = plan_change_set(
        change_set_id="cs1" + suffix,
        question=q,
        decision=d,
        artifact_snapshot=snap,
        pre_change_trace=trace,
    )
    versions = tuple(
        ExecutionVersionSpec(
            execution_unit_id=u.execution_unit_id,
            versions=CheckpointVersions(
                schema_version="s1",
                validator_version="v1",
                prompt_version="p1",
                projection_version="x1",
            ),
        )
        for u in cs.execution_units
    )
    return d, cs, versions


@pytest.fixture
def repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'feedback.db'}", future=True)
    Base.metadata.create_all(engine, tables=[App.__table__, WorkspaceCommand.__table__])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(App(app_id="app"))
    try:
        yield FeedbackRepository(factory)
    finally:
        engine.dispose()


def _created(repository, key="k1"):
    decision, cs, versions = _bundle()
    value = repository.create(
        app_id="app", idempotency_key=key, source_identity="source", decision=decision
    )
    attached = repository.attach_change_set(
        "app", key, cs, versions, expected_revision=value.revision
    )
    return decision, cs, versions, attached


def test_create_is_idempotent_and_conflicts_on_payload(repository):
    decision, _, _, attached = _created(repository)
    assert "stage" not in attached.model_dump()
    assert repository.create(
        app_id="app", idempotency_key="k1", source_identity="source", decision=decision
    ) == repository.load("app", "k1")
    with pytest.raises(FeedbackRepositoryError, match="idempotency conflict"):
        repository.create(
            app_id="app", idempotency_key="k1", source_identity="other", decision=decision
        )


def test_attach_replay_is_idempotent_and_versions_cover_units(repository):
    _, cs, versions = _bundle()
    first = repository.create(
        app_id="app",
        idempotency_key="k1",
        source_identity="source",
        decision=cs.decision_snapshot,
    )
    attached = repository.attach_change_set(
        "app", "k1", cs, versions, expected_revision=first.revision
    )
    assert (
        repository.attach_change_set("app", "k1", cs, versions, expected_revision=first.revision)
        == attached
    )
    assert {x.execution_unit_id for x in attached.execution_versions} == {
        x.execution_unit_id for x in cs.execution_units
    }


def test_checkpoint_reuse_requires_exact_inputs_and_versions():
    versions = CheckpointVersions(schema_version="s", validator_version="v")
    cp = ExecutionCheckpoint(
        execution_unit_id="u",
        action=ExecutionAction.REBUILD,
        input_fingerprints=("a",),
        versions=versions,
        output_digest="out",
        validation_passed=True,
        status=CheckpointStatus.COMPLETED,
    )
    assert checkpoint_reusable(
        cp, action=ExecutionAction.REBUILD, input_fingerprints=("a",), versions=versions
    )
    assert not checkpoint_reusable(
        cp, action=ExecutionAction.REBUILD, input_fingerprints=("b",), versions=versions
    )
    assert not checkpoint_reusable(
        cp,
        action=ExecutionAction.REBUILD,
        input_fingerprints=("a",),
        versions=CheckpointVersions(schema_version="s", validator_version="new"),
    )


def test_attempt_unknown_failed_retry_and_completed_blocks(repository):
    _, cs, versions, state = _created(repository)
    unit = next(u for u in cs.execution_units if u.action is ExecutionAction.REBUILD)
    version = next(v.versions for v in versions if v.execution_unit_id == unit.execution_unit_id)
    attempt = RemoteAttempt(
        execution_unit_id=unit.execution_unit_id,
        attempt_no=1,
        status=AttemptStatus.STARTED,
        provider="p",
        request_digest="r",
    )
    state = repository.start_attempt("app", "k1", attempt, expected_revision=state.revision)
    assert repository.load("app", "k1").attempts[-1] == attempt
    state = repository.mark_attempt_outcome_unknown(
        "app", "k1", attempt, expected_revision=state.revision
    )
    checkpoint = ExecutionCheckpoint(
        execution_unit_id=unit.execution_unit_id,
        action=unit.action,
        input_fingerprints=expected_input_fingerprints(cs, unit),
        versions=version,
        attempts=1,
        status=CheckpointStatus.FAILED,
    )
    state = repository.save_checkpoint("app", "k1", checkpoint, expected_revision=state.revision)
    retry = RemoteAttempt(
        execution_unit_id=unit.execution_unit_id,
        attempt_no=2,
        status=AttemptStatus.STARTED,
        provider="p",
        request_digest="r2",
    )
    state = repository.start_attempt("app", "k1", retry, expected_revision=state.revision)
    repository.finish_attempt(
        "app",
        "k1",
        retry,
        status=AttemptStatus.SUCCEEDED,
        expected_revision=state.revision,
        outcome_digest="done",
    )
    completed = checkpoint.model_copy(
        update={
            "output_digest": "done",
            "validation_passed": True,
            "attempts": 2,
            "status": CheckpointStatus.COMPLETED,
        }
    )
    state = repository.save_checkpoint("app", "k1", completed, expected_revision=state.revision + 1)
    with pytest.raises(FeedbackRepositoryError, match="completed checkpoint"):
        repository.start_attempt(
            "app",
            "k1",
            RemoteAttempt(
                execution_unit_id=unit.execution_unit_id,
                attempt_no=3,
                status=AttemptStatus.STARTED,
                provider="p",
                request_digest="r3",
            ),
            expected_revision=state.revision,
        )


def test_publish_rejects_old_base_and_accepts_correct_head(repository):
    _, cs, versions, state = _created(repository)
    entries = []
    for unit in cs.execution_units:
        version = next(
            v.versions for v in versions if v.execution_unit_id == unit.execution_unit_id
        )
        attempts = 0
        if unit.action is ExecutionAction.REBUILD:
            attempt = RemoteAttempt(
                execution_unit_id=unit.execution_unit_id,
                attempt_no=1,
                status=AttemptStatus.STARTED,
                provider="p",
                request_digest=f"request:{unit.execution_unit_id}",
            )
            state = repository.start_attempt("app", "k1", attempt, expected_revision=state.revision)
            state = repository.finish_attempt(
                "app",
                "k1",
                attempt,
                status=AttemptStatus.SUCCEEDED,
                expected_revision=state.revision,
                outcome_digest=unit.execution_unit_id,
            )
            attempts = 1
        cp = ExecutionCheckpoint(
            execution_unit_id=unit.execution_unit_id,
            action=unit.action,
            input_fingerprints=expected_input_fingerprints(cs, unit),
            versions=version,
            output_digest=unit.execution_unit_id,
            validation_passed=True,
            attempts=attempts,
            status=CheckpointStatus.COMPLETED,
        )
        state = repository.save_checkpoint("app", "k1", cp, expected_revision=state.revision)
        entries.append(
            AcceptedArtifactEntry(
                target=unit.artifact, digest=cp.output_digest, validity=ArtifactValidity.VALID
            )
        )
    published = repository.publish(
        "app",
        "k1",
        expected_revision=state.revision,
        expected_base_head=None,
        entries=entries,
    )
    assert repository.current_head("app") == published.manifest.head_id
    assert "source_change_set_id" not in published.manifest.model_dump()
    _, cs2, versions2 = _bundle("2")
    second = repository.create(
        app_id="app",
        idempotency_key="k2",
        source_identity="source2",
        decision=cs2.decision_snapshot,
    )
    second = repository.attach_change_set(
        "app", "k2", cs2, versions2, expected_revision=second.revision
    )
    with pytest.raises(FeedbackRepositoryError, match="stale base"):
        repository.publish(
            "app",
            "k2",
            expected_revision=second.revision,
            expected_base_head=None,
            entries=(),
        )
    assert repository.current_head("app") == published.manifest.head_id


def test_schema_metadata_has_no_feedback_tables():
    assert "feedback_revisions" not in Base.metadata.tables
    assert "workspace_commands" in Base.metadata.tables
