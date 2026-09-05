from __future__ import annotations

from typing import Any

from app.workspace import service as workspace_module
from app.workspace.conversation.contracts import (
    CommandIntent,
    RevisionInterpretation,
    RevisionPlan,
    RevisionTarget,
)
from app.workspace.service import WorkspaceService


def _target(
    ref: str,
    *,
    kind: str = "class",
    owner: str = "design",
    artifact_type: str = "CLASS_DIAGRAM",
) -> RevisionTarget:
    return RevisionTarget(
        ref=ref,
        kind=kind,
        element_id=ref.split(":", 1)[-1],
        owner=owner,
        artifact_type=artifact_type,
        artifact_version_id=7,
        display_label=ref.split(":", 1)[-1],
    )


def _plan(
    status: str,
    *,
    requested: list[RevisionTarget],
    authority: list[RevisionTarget] | None = None,
    downstream: list[RevisionTarget] | None = None,
    execution_mode: str = "targeted_revision",
) -> RevisionPlan:
    return RevisionPlan(
        plan_digest="a" * 64,
        status=status,
        requested_targets=requested,
        authority_targets=authority if authority is not None else requested,
        upstream_candidates=authority or [],
        downstream_targets=downstream or [],
        execution_mode=execution_mode,  # type: ignore[arg-type]
        reason_codes=["test"],
        explanation="Review the bounded revision scope.",
        artifact_versions={"CLASS_DIAGRAM": 7},
        trace_digest="b" * 64,
    )


class _Tools:
    def __init__(self, _app_id: str) -> None:
        pass

    def trace_impact(self, refs, *, view: str):
        return {"refs": list(refs), "view": view}

    def revision_snapshot(self):
        return {"artifact_versions": {"CLASS_DIAGRAM": 8}}

    def current_revision_target(self, target: RevisionTarget):
        return target


def _latest() -> dict[str, Any]:
    return {
        "command_id": "design-command",
        "app_id": "app-1",
        "action": "message",
        "stage": "design",
        "status": "COMPLETED",
        "payload": {},
        "result": {},
    }


def _intent(ref: str) -> CommandIntent:
    interpretation = RevisionInterpretation(
        targets=[ref],
        semantic_scope="contract",
        requested_effect="Change the selected contract.",
    )
    return CommandIntent(
        intent="revise",
        targets=[ref],
        instruction="Change the selected contract.",
        revision=interpretation,
    )


def test_ready_local_plan_is_attached_to_the_bounded_design_message(monkeypatch) -> None:
    target = _target("class_diagram:OrderControl")
    downstream = _target("api_spec:createOrder", kind="api", artifact_type="API_SPEC")
    plan = _plan("ready_local", requested=[target], downstream=[downstream])
    monkeypatch.setattr(workspace_module, "ProjectTools", _Tools)
    monkeypatch.setattr(workspace_module, "plan_revision", lambda *_args: plan)
    monkeypatch.setattr(
        workspace_module.repository,
        "latest_command",
        lambda *_args, **_kwargs: _latest(),
    )

    service = WorkspaceService()
    try:
        action, payload, stage = service._route_conversation_intent(
            "app-1", {"text": "Change it."}, _intent(target.ref), _latest()
        )
    finally:
        service.shutdown()

    assert (action, stage) == ("message", "design")
    assert payload["revision_plan"]["status"] == "ready_local"
    assert payload["context"]["approved_authority_targets"] == [target.ref]
    assert payload["context"]["approved_downstream_targets"] == [downstream.ref]


def test_confirmation_plan_dispatches_without_running_a_stage_service() -> None:
    requested = _target("sequence_diagram:UC1", kind="sequence")
    authority = _target("class_diagram:OrderControl")
    plan = _plan(
        "needs_confirmation", requested=[requested], authority=[authority]
    )
    command = {
        **_latest(),
        "command_id": "plan-command",
        "payload": {
            "_conversation_outcome": {"kind": "revision_plan"},
            "revision_plan": plan.model_dump(mode="json"),
        },
    }

    service = WorkspaceService()
    try:
        result = service._dispatch(command)
    finally:
        service.shutdown()

    assert result["awaiting_input"] is True
    assert result["action"] == "confirm_change"
    assert result["requested_targets"][0]["ref"] == requested.ref
    assert result["authority_targets"][0]["ref"] == authority.ref


def test_stale_confirmation_never_calls_a_stage_service(monkeypatch) -> None:
    requested = _target("sequence_diagram:UC1", kind="sequence")
    authority = _target("class_diagram:OrderControl")
    plan = _plan(
        "needs_confirmation", requested=[requested], authority=[authority]
    )
    original = {
        **_latest(),
        "command_id": "plan-command",
        "status": "AWAITING_INPUT",
            "payload": {
                "text": "Change the call contract.",
                "revision_plan": plan.model_dump(mode="json"),
                "revision_interpretation": {
                    "targets": ["sequence_diagram:UC1"],
                    "semantic_scope": "contract",
                    "requested_effect": "Change the call contract.",
                    "clarification": "",
                    "change_type": "modify",
                },
            },
    }
    monkeypatch.setattr(
        workspace_module.repository,
        "get_command",
        lambda command_id: original if command_id == "plan-command" else None,
    )
    monkeypatch.setattr(workspace_module, "ProjectTools", _Tools)
    monkeypatch.setattr(workspace_module, "validate_plan", lambda *_args: False)

    service = WorkspaceService()
    called = False

    def stage_message(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    service._stage_message = stage_message  # type: ignore[method-assign]
    try:
        result = service._confirm_change(
            {
                **_latest(),
                "command_id": "confirm-command",
                "payload": {"action_id": "plan-command"},
            }
        )
    finally:
        service.shutdown()

    assert called is False
    assert result["awaiting_input"] is True
    assert result["stale_revision_plan"] == plan.plan_digest


def test_approved_plan_passes_only_frozen_targets_to_the_stage(monkeypatch) -> None:
    requested = _target("sequence_diagram:UC1", kind="sequence")
    authority = _target("class_diagram:OrderControl")
    downstream = _target("api_spec:createOrder", kind="api", artifact_type="API_SPEC")
    plan = _plan(
        "needs_confirmation",
        requested=[requested],
        authority=[authority],
        downstream=[downstream],
    )
    original = {
        **_latest(),
        "command_id": "plan-command",
        "status": "AWAITING_INPUT",
        "payload": {
            "text": "Change the call contract.",
            "revision_plan": plan.model_dump(mode="json"),
            "revision_interpretation": {
                "targets": ["sequence_diagram:UC1"],
                "semantic_scope": "contract",
                "requested_effect": "Change the call contract.",
                "clarification": "",
                "change_type": "modify",
            },
        },
    }
    monkeypatch.setattr(
        workspace_module.repository,
        "get_command",
        lambda command_id: original if command_id == "plan-command" else None,
    )
    monkeypatch.setattr(workspace_module, "ProjectTools", _Tools)
    monkeypatch.setattr(workspace_module, "validate_plan", lambda *_args: True)
    observed: dict[str, Any] = {}

    service = WorkspaceService()

    def stage_message(command, *, advance: bool):
        observed.update(command)
        assert advance is False
        return {"message": "Revised.", "changed": ["class_diagram"]}

    service._stage_message = stage_message  # type: ignore[method-assign]
    try:
        result = service._confirm_change(
            {
                **_latest(),
                "command_id": "confirm-command",
                "payload": {"action_id": "plan-command"},
            }
        )
    finally:
        service.shutdown()

    context = observed["payload"]["context"]
    assert context["validated_target_feedbacks"] == [
        {
            "target": authority.ref,
            "feedback": "Change the call contract.",
            "approved_authority_targets": [authority.ref],
            "approved_downstream_targets": [downstream.ref],
        }
    ]
    assert context["approved_authority_targets"] == [authority.ref]
    assert context["approved_downstream_targets"] == [downstream.ref]
    assert result["revision_execution"]["changed_stages"] == ["class_diagram"]


def test_approved_design_stage_plan_uses_the_single_revision_entrypoint(
    monkeypatch,
) -> None:
    target = _target(
        "design_stage:class_diagram",
        kind="design_stage",
        artifact_type="CLASS_DIAGRAM",
    )
    plan = _plan(
        "needs_confirmation",
        requested=[target],
        execution_mode="stage_rewind",
    )
    original = {
        **_latest(),
        "command_id": "plan-command",
        "status": "AWAITING_INPUT",
        "payload": {
            "text": "Regenerate the class design with the requested boundary.",
            "revision_plan": plan.model_dump(mode="json"),
            "revision_interpretation": {
                "targets": [target.ref],
                "semantic_scope": "contract",
                "requested_effect": "Regenerate the class design with the requested boundary.",
                "clarification": "",
                "change_type": "modify",
            },
        },
    }
    monkeypatch.setattr(
        workspace_module.repository,
        "get_command",
        lambda command_id: original if command_id == "plan-command" else None,
    )
    monkeypatch.setattr(workspace_module, "ProjectTools", _Tools)
    monkeypatch.setattr(workspace_module, "validate_plan", lambda *_args: True)
    observed: dict[str, str] = {}
    monkeypatch.setattr(
        workspace_module,
        "revise_design_stage_session",
        lambda app_id, stage, feedback: observed.update(
            app_id=app_id,
            stage=stage,
            feedback=feedback,
        )
        or {"status": "need_feedback"},
    )

    service = WorkspaceService()
    try:
        result = service._confirm_change(
            {
                **_latest(),
                "command_id": "confirm-command",
                "payload": {"action_id": "plan-command"},
            }
        )
    finally:
        service.shutdown()

    assert observed == {
        "app_id": "app-1",
        "stage": "class_diagram",
        "feedback": "Regenerate the class design with the requested boundary.",
    }
    assert result["design"]["status"] == "need_feedback"
