from __future__ import annotations

from typing import Any

import pytest

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


def test_design_execution_passes_the_frozen_downstream_scope(monkeypatch) -> None:
    observed: dict[str, Any] = {}
    monkeypatch.setattr(
        workspace_module,
        "session_status",
        lambda _app_id: {"stage": "class_diagram", "retryable": False},
    )

    def revise(_app_id, _request, **kwargs):
        observed.update(kwargs)
        return {"changed": [], "touched": {}, "related": {}}

    monkeypatch.setattr(workspace_module, "revise_design_elements", revise)
    service = WorkspaceService()
    try:
        service._stage_message(
            {
                **_latest(),
                "payload": {
                    "text": "Change the selected operation.",
                    "context": {
                        "validated_target_feedbacks": [
                            {
                                "target": "class_diagram:OrderControl::create()",
                                "feedback": "Change the selected operation.",
                            }
                        ],
                        "approved_authority_targets": [
                            "class_diagram:OrderControl::create()"
                        ],
                        "approved_downstream_targets": ["api_spec:createOrder"],
                    },
                },
            },
            advance=False,
        )
    finally:
        service.shutdown()

    assert observed["approved_authority_targets"] == {
        "class_diagram:OrderControl::create()"
    }
    assert observed["approved_downstream_targets"] == {"api_spec:createOrder"}


def test_revision_after_a_reply_and_clarification_uses_the_stage_action_anchor(monkeypatch) -> None:
    target = _target("class_diagram:OrderControl")
    plan = _plan("ready_local", requested=[target])
    stage_gate = {
        **_latest(),
        "command_id": "stage-gate",
        "status": "AWAITING_INPUT",
        "result": {"message": "Review the class diagram."},
    }
    reply = {
        **_latest(),
        "command_id": "reply-command",
        "payload": {
            "_conversation_actions": [
                {
                    "action": "message",
                    "label": "Send revision feedback",
                    "payload": {"action_id": "stage-gate"},
                }
            ]
        },
    }
    clarification = {
        **_latest(),
        "command_id": "clarification-command",
        "status": "AWAITING_INPUT",
        "payload": {"action_id": "reply-command"},
        "result": {
            "conversation": {"clarification": {"question": "Which behavior?"}}
        },
    }
    commands = {
        item["command_id"]: item for item in (stage_gate, reply, clarification)
    }

    def get_command(command_id):
        return commands.get(command_id)

    monkeypatch.setattr(workspace_module, "ProjectTools", _Tools)
    monkeypatch.setattr(workspace_module, "plan_revision", lambda *_args: plan)
    monkeypatch.setattr(
        workspace_module.repository,
        "latest_command",
        lambda *_args, **_kwargs: clarification,
    )
    monkeypatch.setattr(
        workspace_module.repository,
        "get_command",
        get_command,
    )

    service = WorkspaceService()
    try:
        action, payload, stage = service._route_conversation_intent(
            "app-1", {"text": "Change it."}, _intent(target.ref), clarification
        )
    finally:
        service.shutdown()

    assert (action, stage) == ("message", "design")
    assert payload["action_id"] == "stage-gate"


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


@pytest.mark.parametrize("exact_entry", [True, False])
def test_reviewed_spec_revision_builds_and_persists_a_fresh_design_plan(
    monkeypatch,
    exact_entry: bool,
) -> None:
    entry = _target(
        "class_diagram:OrderBoundary::placeOrder()",
        kind="operation",
    )
    broad = _target(
        "design_stage:class_diagram",
        kind="design_stage",
    )
    expected = entry if exact_entry else broad
    plan = _plan(
        "needs_confirmation",
        requested=[expected],
        execution_mode="targeted_revision" if exact_entry else "stage_rewind",
    )
    source = {
        **_latest(),
        "command_id": "requirements-review",
        "stage": "requirements",
        "status": "AWAITING_INPUT",
        "result": {
            "downstream_revision_handoff": {
                "source_targets": [
                    {
                        "ref": "use_case_spec:UC-ORDER",
                        "artifact_version_id": 8,
                    }
                ],
                "semantic_scope": "behavior",
                "requested_effect": "Add the accepted exception.",
                "change_type": "modify",
            }
        },
    }
    persisted: dict[str, Any] = {}
    observed: dict[str, Any] = {}

    class DownstreamTools(_Tools):
        def design_entry_targets_for_requirements(self, refs):
            observed["source_targets"] = list(refs)
            return [entry] if exact_entry else []

    def make_plan(_tools, interpretation):
        observed["interpretation"] = interpretation
        return plan

    monkeypatch.setattr(workspace_module, "ProjectTools", DownstreamTools)
    monkeypatch.setattr(workspace_module, "plan_revision", make_plan)
    monkeypatch.setattr(
        workspace_module.repository,
        "get_command",
        lambda command_id: source if command_id == "requirements-review" else None,
    )
    monkeypatch.setattr(
        workspace_module.repository,
        "update_command",
        lambda command_id, **changes: persisted.update(
            command_id=command_id, **changes
        ),
    )
    command = {
        **_latest(),
        "command_id": "downstream-plan",
        "action": "plan_downstream_revision",
        "stage": "design",
        "payload": {"action_id": "requirements-review"},
    }

    service = WorkspaceService()
    try:
        result = service._dispatch(command)
    finally:
        service.shutdown()

    assert observed["source_targets"] == [
        {"ref": "use_case_spec:UC-ORDER", "artifact_version_id": 8}
    ]
    assert observed["interpretation"].targets == [expected.ref]
    assert result["action"] == "confirm_change"
    assert persisted["command_id"] == "downstream-plan"
    assert persisted["payload"]["revision_plan"]["plan_digest"] == plan.plan_digest
    assert "_conversation_outcome" not in persisted["payload"]


def test_exact_spec_revision_review_is_marked_for_fresh_downstream_planning() -> None:
    target = _target(
        "use_case_spec:UC-ORDER",
        kind="use_case_spec",
        owner="requirements",
        artifact_type="USECASE_SPEC",
    )
    plan = _plan("ready_local", requested=[target])
    interpretation = RevisionInterpretation(
        targets=[target.ref],
        semantic_scope="behavior",
        requested_effect="Add the accepted exception.",
    )

    result = WorkspaceService._attach_downstream_revision_handoff(
        {
            "stage": "requirements",
            "payload": {"text": interpretation.requested_effect},
        },
        plan,
        interpretation,
        {
            "awaiting_input": True,
            "phase": "specs",
            "revision_execution": {
                "artifact_versions": {"USECASE_SPEC": 8}
            },
        },
    )

    assert result["downstream_revision_handoff"]["source_targets"] == [
        {"ref": target.ref, "artifact_version_id": 8}
    ]


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


def test_approved_plan_keeps_authority_bounded_and_downstream_as_hints(monkeypatch) -> None:
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
            "approved_downstream_targets": None,
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
