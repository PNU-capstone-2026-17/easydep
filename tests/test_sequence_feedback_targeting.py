"""Explicit sequence-UC feedback must bypass whole-stage regeneration."""

from __future__ import annotations

import pytest

from app.design import service as design_service
from app.workspace import service as workspace_module
from app.workspace.service import WorkspaceService


def test_selected_sequence_ucs_use_one_targeted_batch_path(monkeypatch) -> None:
    calls: list[tuple[str, list[tuple[str, str]]]] = []

    monkeypatch.setattr(
        workspace_module,
        "session_status",
        lambda _app_id: {"active": True, "retryable": False, "stage": "sequence_diagram"},
    )

    def revise(app_id, request):
        calls.append(
            (
                app_id,
                [(revision.target, revision.feedback) for revision in request.revisions],
            )
        )
        return {
            "changed": ["sequence_diagram"],
            "touched": {"sequence_diagram": ["UC5", "UC6"]},
            "related": {},
        }

    monkeypatch.setattr(workspace_module, "revise_design_elements", revise)
    monkeypatch.setattr(
        workspace_module,
        "resume_design_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("selected UC feedback must not resume the whole stage")
        ),
    )

    service = WorkspaceService()
    try:
        result = service._stage_message(
            {
                "command_id": "command-1",
                "app_id": "app-1",
                "stage": "design",
                "payload": {
                    "text": "Targeted sequence feedback for UC5, UC6",
                    "context": {
                        "stage": "design",
                        "artifact_stage": "sequence_diagram",
                        "target_feedbacks": [
                            {
                                "target": "sequence_diagram:UC5",
                                "feedback": "Show duplicate-email handling.",
                            },
                            {
                                "target": "sequence_diagram:UC6",
                                "feedback": "Display a confirmation after cancellation.",
                            },
                        ],
                    },
                },
            },
            advance=False,
        )
    finally:
        service.shutdown()

    assert calls == [
        (
            "app-1",
            [
                ("sequence_diagram:UC5", "Show duplicate-email handling."),
                ("sequence_diagram:UC6", "Display a confirmation after cancellation."),
            ],
        )
    ]
    assert result["awaiting_input"] is True
    assert result["touched"] == {"sequence_diagram": ["UC5", "UC6"]}


def test_active_sequence_feedback_without_a_target_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_module,
        "session_status",
        lambda _app_id: {"active": True, "retryable": False, "stage": "sequence_diagram"},
    )
    service = WorkspaceService()
    try:
        with pytest.raises(ValueError, match="Select one or more use-case targets"):
            service._stage_message(
                {
                    "command_id": "command-1",
                    "app_id": "app-1",
                    "action": "message",
                    "stage": "design",
                    "payload": {"text": "Make this clearer.", "context": {}},
                },
                advance=False,
            )
    finally:
        service.shutdown()


def test_targeted_sequence_batch_persists_once_after_every_revision_succeeds(monkeypatch) -> None:
    persisted: list[dict] = []
    synced: list[dict] = []

    monkeypatch.setattr(
        design_service.artifact_repository,
        "load_state",
        lambda _app_id: {"revision_count": 0},
    )
    monkeypatch.setattr(
        design_service, "to_web_response", lambda state: {"artifacts": state}
    )
    monkeypatch.setattr(
        design_service,
        "persist_cascade",
        lambda _app_id, result: persisted.append(result),
    )
    monkeypatch.setattr(
        design_service,
        "sync_design_state",
        lambda _app_id, state: synced.append(state),
    )

    def cascade(state, target, feedback, **_scope):
        return {
            "state": {**state, "revision_count": state["revision_count"] + 1, target: feedback},
            "changed": ["sequence_diagram"],
            "touched": {"sequence_diagram": [target.partition(":")[2]]},
            "related": [],
        }

    monkeypatch.setattr(design_service, "revise_and_cascade", cascade)
    response = design_service.revise_design_elements(
        "00000000-0000-0000-0000-000000000001",
        design_service.BatchReviseRequest(
            revisions=[
                design_service.ReviseRequest(
                    target="sequence_diagram:UC5", feedback="first"
                ),
                design_service.ReviseRequest(
                    target="sequence_diagram:UC6", feedback="second"
                ),
            ]
        ),
    )

    assert response["artifacts"]["revision_count"] == 2
    assert len(persisted) == 1
    assert persisted[0]["touched"] == {"sequence_diagram": ["UC5", "UC6"]}
    assert synced == [persisted[0]["state"]]


def test_failed_targeted_batch_does_not_persist_an_earlier_target(monkeypatch) -> None:
    persisted: list[dict] = []

    monkeypatch.setattr(
        design_service.artifact_repository,
        "load_state",
        lambda _app_id: {"revision_count": 0},
    )
    monkeypatch.setattr(
        design_service,
        "persist_cascade",
        lambda _app_id, result: persisted.append(result),
    )

    def cascade(state, target, feedback, **_scope):
        if target.endswith("UC6"):
            raise RuntimeError("second target failed")
        return {
            "state": {**state, "revision_count": 1},
            "changed": ["sequence_diagram"],
            "touched": {"sequence_diagram": ["UC5"]},
            "related": [],
        }

    monkeypatch.setattr(design_service, "revise_and_cascade", cascade)
    with pytest.raises(RuntimeError, match="no batch changes were saved"):
        design_service.revise_design_elements(
            "00000000-0000-0000-0000-000000000001",
            design_service.BatchReviseRequest(
                revisions=[
                    design_service.ReviseRequest(
                        target="sequence_diagram:UC5", feedback="first"
                    ),
                    design_service.ReviseRequest(
                        target="sequence_diagram:UC6", feedback="second"
                    ),
                ]
            ),
        )

    assert persisted == []


def test_artifact_persistence_failure_restores_the_previous_design_checkpoint(
    monkeypatch,
) -> None:
    original = {"revision_count": 0}
    synced: list[dict] = []
    monkeypatch.setattr(
        design_service.artifact_repository,
        "load_state",
        lambda _app_id: original,
    )
    monkeypatch.setattr(
        design_service,
        "sync_design_state",
        lambda _app_id, state: synced.append(state),
    )
    monkeypatch.setattr(
        design_service,
        "revise_and_cascade",
        lambda state, _target, _feedback, **_scope: {
            "state": {**state, "revision_count": 1},
            "changed": ["sequence_diagram"],
            "touched": {"sequence_diagram": ["UC5"]},
            "related": [],
        },
    )

    def fail_persistence(_app_id, _result):
        raise RuntimeError("database write failed")

    monkeypatch.setattr(design_service, "persist_cascade", fail_persistence)

    with pytest.raises(RuntimeError, match="database write failed"):
        design_service.revise_design_elements(
            "00000000-0000-0000-0000-000000000001",
            design_service.BatchReviseRequest(
                revisions=[
                    design_service.ReviseRequest(
                        target="sequence_diagram:UC5",
                        feedback="first",
                    )
                ]
            ),
        )

    assert synced == [{"revision_count": 1}, original]


def test_design_review_result_exposes_pending_method_proposals_for_manual_approval() -> None:
    service = WorkspaceService()
    try:
        result = service._design_result(
            {
                "validation": {
                    "sequence_diagram": {
                        "method_proposals": [
                            {"id": "method:Account:register(): void"}
                        ]
                    }
                },
                "session": {"stage": "sequence_diagram"},
            }
        )
    finally:
        service.shutdown()

    assert result["method_proposals"] == [{"id": "method:Account:register(): void"}]
