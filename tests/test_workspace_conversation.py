from __future__ import annotations

from typing import Any

from app.llm_schema import strict_json_schema
from app.workspace import service as workspace_module
from app.workspace.conversation.agent import ConversationAgent
from app.workspace.conversation.context import ConversationContext
from app.workspace.conversation.contracts import (
    Clarification,
    CommandIntent,
    Reply,
    RevisionInterpretation,
    RevisionPatchIntent,
)
from app.workspace.service import WorkspaceService


def context(*, actions: list[dict[str, Any]] | None = None) -> ConversationContext:
    return ConversationContext(
        app_id="app-1",
        workspace={"stage": "design", "status": "AWAITING_INPUT"},
        actions=actions or [],
    )


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.matches = [
            {
                "ref": "class_diagram:OrderService",
                "label": "OrderService",
                "owner": "design",
                "editable": True,
            }
        ]

    def read_workspace(self):
        self.calls.append(("read_workspace", None))
        return {"stage": "design", "status": "AWAITING_INPUT"}

    def search_elements(self, query: str):
        self.calls.append(("search_elements", query))
        return list(self.matches)

    def artifact_candidates(self, artifact_stage: str):
        self.calls.append(("artifact_candidates", artifact_stage))
        return list(self.matches)

    def validate_targets(self, refs):
        refs = list(refs)
        self.calls.append(("validate_targets", refs))
        return {
            "valid": bool(refs),
            "valid_refs": refs,
            "existing_refs": refs,
        }

    validate_revision_selections = validate_targets

    def read_element(self, ref: str):
        self.calls.append(("read_element", ref))
        return {"ref": ref, "content": {"operations": ["placeOrder"]}}


def test_general_reply_does_not_read_project_state() -> None:
    def propose(schema, _messages):
        assert schema.__name__ == "_ConversationPlan"
        return schema(kind="reply", reply="Hello. What would you like to review?")

    tools = FakeTools()
    result = ConversationAgent(propose).respond(
        "app-1", "Hello", context(), tools=tools
    )

    assert isinstance(result, Reply)
    assert result.text.startswith("Hello")
    assert tools.calls == []


def test_selected_artifact_still_classifies_ordinary_chat_before_reading() -> None:
    def propose(schema, _messages):
        assert schema.__name__ == "_ConversationPlan"
        return schema(kind="reply", reply="Hello. How can I help?")

    tools = FakeTools()
    result = ConversationAgent(propose).respond(
        "app-1",
        "Hello",
        ConversationContext(
            app_id="app-1",
            workspace={
                "stage": "design",
                "selection": {"artifact_stage": "class_diagram"},
            },
        ),
        tools=tools,
    )

    assert isinstance(result, Reply)
    assert tools.calls == []


def test_selected_artifact_question_reads_the_exact_selected_element() -> None:
    def propose(schema, _messages):
        if schema.__name__ == "_ConversationPlan":
            return schema(kind="project_question", query="operations")
        return schema(text="OrderService has a placeOrder operation.")

    tools = FakeTools()
    result = ConversationAgent(propose).respond(
        "app-1",
        "What operations does this have?",
        ConversationContext(
            app_id="app-1",
            workspace={
                "stage": "design",
                "selection": {
                    "artifact_stage": "class_diagram",
                    "element_ref": "class_diagram:OrderService",
                },
            },
        ),
        tools=tools,
    )

    assert isinstance(result, Reply)
    assert [name for name, _ in tools.calls] == [
        "read_workspace",
        "artifact_candidates",
        "search_elements",
        "validate_targets",
        "read_element",
    ]


def test_project_question_is_answered_from_read_only_tool_evidence() -> None:
    def propose(schema, _messages):
        if schema.__name__ == "_ConversationPlan":
            return schema(kind="project_question", query="OrderService operations")
        return schema(text="OrderService has a placeOrder operation.")

    tools = FakeTools()
    result = ConversationAgent(propose).respond(
        "app-1", "Which operations does OrderService have?", context(), tools=tools
    )

    assert isinstance(result, Reply)
    assert "placeOrder" in result.text
    assert [name for name, _ in tools.calls] == [
        "read_workspace",
        "search_elements",
        "validate_targets",
        "read_element",
    ]


def test_revision_plan_reserves_queries_for_distinct_registration_flows() -> None:
    class SearchTools(FakeTools):
        def __init__(self) -> None:
            super().__init__()
            self.matches = [
                {
                    "ref": "class_diagram:CourseOffering",
                    "label": "CourseOffering",
                    "owner": "design",
                    "editable": True,
                },
                {
                    "ref": "class_diagram:UC3:main:1",
                    "label": "UC3",
                    "owner": "design",
                    "editable": True,
                },
                {
                    "ref": "class_diagram:UC5:main:1",
                    "label": "UC5",
                    "owner": "design",
                    "editable": True,
                },
            ]
            self.revision_queries: list[str] = []

        def search_revision_context(self, queries, *, anchor_refs):
            self.revision_queries = list(queries)
            return {"candidates": list(self.matches), "evidence": []}

    def propose(schema, messages):
        if schema.__name__ == "_ConversationPlan":
            prompt = str(messages[0].content)
            assert '"drop registration"' in prompt
            assert '"swap registration"' in prompt
            return schema(
                kind="command",
                intent="revise",
                query="CourseOffering",
                search_queries=[
                    "CourseOffering",
                    "registration removal",
                    "drop registration",
                    "swap registration",
                ],
            )
        return schema(
            targets=[
                "class_diagram:CourseOffering",
                "class_diagram:UC3:main:1",
                "class_diagram:UC5:main:1",
            ],
            semantic_scope="behavior",
            requested_effect="Add the update to every relevant removal flow.",
        )

    tools = SearchTools()
    result = ConversationAgent(propose).respond(
        "app-1",
        "Add the update to every relevant registration removal flow.",
        context(),
        tools=tools,
    )

    assert isinstance(result, CommandIntent)
    assert tools.revision_queries == [
        "CourseOffering",
        "registration removal",
        "drop registration",
        "swap registration",
    ]
    assert result.targets == [
        "class_diagram:CourseOffering",
        "class_diagram:UC3:main:1",
        "class_diagram:UC5:main:1",
    ]


def test_cloud_question_adds_local_guidance_to_the_grounded_answer() -> None:
    guidance_calls: list[tuple[str, str, str]] = []

    def guidance(app_id: str, topic: str, query: str):
        guidance_calls.append((app_id, topic, query))
        return {
            "topic": "free_tier",
            "current": {"provider": "aws", "region": "ap-northeast-2"},
            "requestedSkus": [
                {
                    "sku": "t3.micro",
                    "status": "found",
                    "matches": [{"freeTier": {"status": "conditional"}}],
                }
            ],
        }

    def propose(schema, messages):
        if schema.__name__ == "_ConversationPlan":
            return schema(
                kind="project_question",
                query="t3.micro Free Tier",
                cloud_topic="free_tier",
            )
        prompt = str(messages[-1].content)
        assert '"cloud"' in prompt
        assert '"status": "conditional"' in prompt
        return schema(text="t3.micro eligibility is account-dependent in this evidence.")

    tools = FakeTools()
    result = ConversationAgent(
        propose, cloud_guidance_call=guidance
    ).respond(
        "app-1",
        "Is t3.micro in the Free Tier for this app?",
        context(),
        tools=tools,
    )

    assert isinstance(result, Reply)
    assert "account-dependent" in result.text
    assert guidance_calls == [
        (
            "app-1",
            "free_tier",
            "t3.micro Free Tier\nIs t3.micro in the Free Tier for this app?",
        )
    ]


def test_revision_can_only_select_a_finite_validated_ref() -> None:
    def propose(schema, _messages):
        if schema.__name__ == "_ConversationPlan":
            return schema(kind="command", intent="revise", query="OrderService")
        return schema(
            targets=["invented:ref", "class_diagram:OrderService"],
            semantic_scope="behavior",
            requested_effect="Change the order method.",
        )

    tools = FakeTools()
    result = ConversationAgent(propose).respond(
        "app-1",
        "Change the order method in OrderService.",
        context(),
        tools=tools,
    )

    assert isinstance(result, CommandIntent)
    assert result.intent == "revise"
    assert result.targets == ["class_diagram:OrderService"]
    assert ("validate_targets", ["class_diagram:OrderService"]) in tools.calls


def test_selected_diagram_can_resolve_an_explicitly_named_nested_operation() -> None:
    operation_ref = "class_diagram:OrderControl::cancelReservation()"

    def propose(schema, messages):
        assert schema.__name__ == "RevisionInterpretation"
        prompt = str(messages[-1].content)
        assert "sequence_diagram:UC4" in prompt
        assert operation_ref in prompt
        return schema(
            targets=[
                operation_ref,
                "class_diagram:UC4::call:1",
                "sequence_diagram:UC4",
            ],
            semantic_scope="contract",
            requested_effect="Rename cancelReservation to submitCancellation.",
            change_type="rename",
        )

    tools = FakeTools()
    tools.matches = [
        {
            "ref": "sequence_diagram:UC4",
            "label": "UC4",
            "owner": "design",
            "editable": True,
        },
        {
            "ref": operation_ref,
            "label": "OrderControl::cancelReservation()",
            "owner": "design",
            "editable": True,
        },
        {
            "ref": "class_diagram:UC4::call:1",
            "label": "UC4::call:1",
            "owner": "design",
            "editable": True,
        },
    ]
    selected_context = ConversationContext(
        app_id="app-1",
        workspace={
            "stage": "design",
            "status": "AWAITING_INPUT",
            "selection": {
                "artifact_stage": "sequence_diagram",
                "element_ref": "sequence_diagram:UC4",
            },
        },
    )

    result = ConversationAgent(propose).interpret_revision(
        "Rename OrderControl.cancelReservation to submitCancellation.",
        ["sequence_diagram:UC4"],
        tools=tools,
        context=selected_context,
    )

    assert isinstance(result, CommandIntent)
    assert result.targets == [operation_ref]


def test_llm_decomposed_class_feedback_keeps_all_grounded_subchanges() -> None:
    class_ref = "class_diagram:CourseOffering"
    uc3_ref = "class_diagram:UC3:main:1"
    uc5_ref = "class_diagram:UC5:main:1"

    def propose(schema, _messages):
        if schema.__name__ == "_ConversationPlan":
            return schema(kind="command", intent="revise", query="enrollment count")
        return schema(
            targets=[class_ref, uc3_ref, uc5_ref],
            semantic_scope="contract",
            requested_effect=(
                "Add an enrollment decrement operation and invoke it when "
                "registrations are removed in UC3 and UC5."
            ),
            change_type="add",
            target_instructions=[
                {
                    "target": class_ref,
                    "instruction": "Add a parameterless decrement operation.",
                },
                {
                    "target": uc3_ref,
                    "instruction": "Invoke it when the original registration is removed.",
                },
                {
                    "target": uc5_ref,
                    "instruction": "Invoke it when the dropped registration is removed.",
                },
            ],
        )

    tools = FakeTools()
    tools.matches = [
        {
            "ref": class_ref,
            "label": "CourseOffering",
            "owner": "design",
            "editable": True,
        },
        {
            "ref": uc3_ref,
            "label": "UC3 collaboration",
            "owner": "design",
            "editable": True,
        },
        {
            "ref": uc5_ref,
            "label": "UC5 collaboration",
            "owner": "design",
            "editable": True,
        },
    ]
    selected_context = ConversationContext(
        app_id="app-1",
        workspace={
            "stage": "design",
            "status": "AWAITING_INPUT",
            "selection": {"artifact_stage": "class_diagram"},
        },
    )

    result = ConversationAgent(propose).respond(
        "app-1",
        "Add a decrement operation to CourseOffering and use it when "
        "registrations are removed in UC3 step 4 and UC5 step 3.",
        selected_context,
        tools=tools,
    )

    assert isinstance(result, CommandIntent)
    assert result.targets == [class_ref, uc3_ref, uc5_ref]
    assert [item.target for item in result.revision.target_instructions] == [
        class_ref,
        uc3_ref,
        uc5_ref,
    ]


def test_revision_patch_intents_are_structured_and_kept_for_selected_targets() -> None:
    class_ref = "class_diagram:Enrollment"

    def propose(schema, _messages):
        if schema.__name__ == "_ConversationPlan":
            return schema(kind="command", intent="revise", query="Enrollment")
        return schema(
            targets=[class_ref],
            semantic_scope="contract",
            requested_effect="Add an operation while preserving the existing order.",
            patch_intents=[
                {
                    "operation": "add_operation",
                    "target": class_ref,
                    "name": "decrement",
                    "parameters": [{"name": "id", "type": "UUID"}],
                    "returnType": "int",
                    "stepRefs": ["UC1:main:2"],
                },
                {
                    "operation": "preserve_existing_order",
                    "target": class_ref,
                },
            ],
        )

    tools = FakeTools()
    tools.matches = [
        {"ref": class_ref, "label": "Enrollment", "owner": "design", "editable": True}
    ]
    result = ConversationAgent(propose).respond(
        "app-1", "Add decrement to Enrollment and preserve order.", context(), tools=tools
    )

    assert isinstance(result, CommandIntent)
    assert result.revision is not None
    patches = result.revision.patch_intents
    assert [patch.operation for patch in patches] == [
        "add_operation",
        "preserve_existing_order",
    ]
    assert patches[0].parameters[0].type == "UUID"
    assert patches[0].return_type == "int"
    assert patches[0].step_refs == ["UC1:main:2"]
    assert patches[0].model_dump(mode="json", by_alias=True)["returnType"] == "int"
    assert patches[0].model_dump(mode="json", by_alias=True)["stepRefs"] == ["UC1:main:2"]


def test_revision_patch_intent_requires_operation_specific_details() -> None:
    import pytest

    with pytest.raises(ValueError, match="missing its required details"):
        RevisionPatchIntent(operation="rename_operation", target="class:Enrollment")


def test_revision_interpretation_patch_schema_is_strict_and_binding_aliases_round_trip() -> None:
    schema = strict_json_schema(RevisionInterpretation)
    binding_schema = schema["$defs"]["RevisionPatchArgumentBinding"]
    assert binding_schema["additionalProperties"] is False
    assert set(binding_schema["properties"]) == set(binding_schema["required"])

    patch = RevisionPatchIntent(
        operation="insert_call_after",
        target="class_diagram:UC1:main:1",
        anchor="Existing::remove()",
        receiverOperationId="Counter::decrement()",
        stepRefs=["UC1:main:3"],
        argumentBindings=[{"parameter": "id", "sourceRef": "UC1:main:2#id"}],
    )
    dumped = patch.model_dump(mode="json", by_alias=True)
    assert dumped["argumentBindings"] == [
        {"parameter": "id", "sourceRef": "UC1:main:2#id"}
    ]


def test_quantified_revision_does_not_add_targets_outside_llm_selection() -> None:
    class_ref = "class_diagram:CourseOffering"
    uc3_ref = "class_diagram:UC3:main:1"
    uc5_ref = "class_diagram:UC5:main:1"
    candidates = [
        {"ref": class_ref, "label": "CourseOffering", "owner": "design", "editable": True},
        {"ref": uc3_ref, "label": "UC3", "owner": "design", "editable": True},
        {"ref": uc5_ref, "label": "UC5", "owner": "design", "editable": True},
    ]

    def propose(schema, messages):
        assert "do not stop after selecting the first named example" in str(
            messages[0].content
        )
        return schema(
            targets=[class_ref, uc3_ref],
            semantic_scope="behavior",
            requested_effect="Apply this to every relevant removal flow.",
            patch_intents=[
                {
                    "operation": "add_operation",
                    "target": class_ref,
                    "name": "decrement",
                },
                {
                    "operation": "insert_call_after",
                    "target": uc3_ref,
                    "anchor": "Registration::deleteById(id:UUID)",
                    "receiverOperationId": "CourseOffering::decrement()",
                    "stepRefs": ["UC3:main:4"],
                },
            ],
        )

    tools = FakeTools()
    tools.matches = candidates
    evidence = [
        {
            "ref": ref,
            "content": content,
        }
        for ref, content in (
            (class_ref, {"className": "CourseOffering", "operations": []}),
            (
                uc3_ref,
                {
                    "collaborationId": "UC3:main:1",
                    "calls": [{"receiverOperationId": "Registration::deleteById(id:UUID)"}],
                },
            ),
            (
                uc5_ref,
                {
                    "collaborationId": "UC5:main:1",
                    "calls": [{"receiverOperationId": "Registration::deleteById(id:UUID)"}],
                },
            ),
        )
    ]

    result = ConversationAgent(propose)._select_revision(
        "Apply this to every relevant removal flow.",
        candidates,
        tools,
        evidence=evidence,
    )

    assert isinstance(result, CommandIntent)
    # Evidence is supplied to the model, but target selection remains an LLM
    # decision. Code must not manufacture UC5 merely because it shares an
    # anchor with the model-selected UC3.
    assert result.targets == [class_ref, uc3_ref]
    assert [patch.target for patch in result.revision.patch_intents] == [
        class_ref,
        uc3_ref,
    ]


def test_shared_operation_step_refs_are_union_of_selected_insertions() -> None:
    interpretation = RevisionInterpretation(
        targets=["class_diagram:CourseOffering", "class_diagram:UC3", "class_diagram:UC5"],
        semantic_scope="behavior",
        patch_intents=[
            {
                "operation": "add_operation",
                "target": "class_diagram:CourseOffering",
                "name": "decrement",
            },
            {
                "operation": "insert_call_after",
                "target": "class_diagram:UC3",
                "anchor": "Registration::deleteById(id:UUID)",
                "receiverOperationId": "CourseOffering::decrement()",
                "stepRefs": ["UC3:main:4"],
            },
            {
                "operation": "insert_call_after",
                "target": "class_diagram:UC5",
                "anchor": "Registration::deleteById(id:UUID)",
                "receiverOperationId": "CourseOffering::decrement()",
                "stepRefs": ["UC5:main:3"],
            },
        ],
    )

    assert interpretation.patch_intents[0].step_refs == ["UC3:main:4", "UC5:main:3"]


def test_exact_identifier_resolution_is_not_limited_to_class_operations() -> None:
    schema_ref = "api_spec:IncidentAcknowledgement"

    def propose(schema, _messages):
        assert schema.__name__ == "RevisionInterpretation"
        return schema(
            targets=["sequence_diagram:UC-INCIDENT", schema_ref],
            semantic_scope="contract",
            requested_effect="Rename IncidentAcknowledgement to IncidentReceipt.",
            change_type="rename",
        )

    tools = FakeTools()
    tools.matches = [
        {
            "ref": "sequence_diagram:UC-INCIDENT",
            "label": "UC-INCIDENT",
            "owner": "design",
            "editable": True,
        },
        {
            "ref": schema_ref,
            "label": "IncidentAcknowledgement",
            "owner": "design",
            "editable": True,
        },
    ]

    result = ConversationAgent(propose).interpret_revision(
        "Rename IncidentAcknowledgement to IncidentReceipt.",
        ["sequence_diagram:UC-INCIDENT"],
        tools=tools,
    )

    assert isinstance(result, CommandIntent)
    assert result.targets == [schema_ref]


def test_ambiguous_exact_labels_remain_for_structured_disambiguation() -> None:
    first_ref = "api_spec:Incident"
    second_ref = "use_case:Incident"

    def propose(schema, _messages):
        assert schema.__name__ == "RevisionInterpretation"
        return schema(
            targets=[second_ref],
            semantic_scope="behavior",
            requested_effect="Change the incident use case.",
        )

    tools = FakeTools()
    tools.matches = [
        {"ref": first_ref, "label": "Incident", "owner": "design", "editable": True},
        {"ref": second_ref, "label": "Incident", "owner": "requirements", "editable": True},
    ]

    result = ConversationAgent(propose).interpret_revision(
        "Change Incident.", [first_ref], tools=tools
    )

    assert isinstance(result, CommandIntent)
    assert result.targets == [second_ref]


def test_exact_identifier_does_not_match_inside_a_longer_word() -> None:
    incident_ref = "api_spec:Incident"
    selected_ref = "use_case:UC-TRIAGE"

    def propose(schema, _messages):
        assert schema.__name__ == "RevisionInterpretation"
        return schema(
            targets=[selected_ref],
            semantic_scope="behavior",
            requested_effect="Change incidental behavior.",
        )

    tools = FakeTools()
    tools.matches = [
        {"ref": incident_ref, "label": "Incident", "owner": "design", "editable": True},
        {"ref": selected_ref, "label": "Workflow", "owner": "requirements", "editable": True},
    ]

    result = ConversationAgent(propose).interpret_revision(
        "Change incidental behavior.", [selected_ref], tools=tools
    )

    assert isinstance(result, CommandIntent)
    assert result.targets == [selected_ref]


def test_mentioned_exact_dependency_does_not_override_the_selected_authority() -> None:
    schema_ref = "api_spec:IncidentAcknowledgement"
    sequence_ref = "sequence_diagram:UC-TRIAGE"

    def propose(schema, _messages):
        assert schema.__name__ == "RevisionInterpretation"
        return schema(
            targets=[sequence_ref],
            semantic_scope="behavior",
            requested_effect="Return IncidentAcknowledgement after triage succeeds.",
        )

    tools = FakeTools()
    tools.matches = [
        {
            "ref": schema_ref,
            "label": "IncidentAcknowledgement",
            "owner": "design",
            "editable": True,
        },
        {
            "ref": sequence_ref,
            "label": "Triage scenario",
            "owner": "design",
            "editable": True,
        },
    ]

    result = ConversationAgent(propose).interpret_revision(
        "Return IncidentAcknowledgement after triage succeeds.",
        [sequence_ref],
        tools=tools,
    )

    assert isinstance(result, CommandIntent)
    assert result.targets == [sequence_ref]


def test_revision_candidates_include_recent_stable_target_remaps() -> None:
    def propose(schema, messages):
        if schema.__name__ == "_ConversationPlan":
            return schema(kind="command", intent="revise", query="that method")
        prompt = str(messages[-1].content)
        assert "class_diagram:OrderControl:operation:stable-1" in prompt
        return schema(
            targets=["class_diagram:OrderControl:operation:stable-1"],
            semantic_scope="behavior",
            requested_effect="Change that method.",
        )

    tools = FakeTools()
    result = ConversationAgent(propose).respond(
        "app-1",
        "Change that method.",
        ConversationContext(
            app_id="app-1",
            workspace={"stage": "design", "status": "AWAITING_INPUT"},
            target_remap={
                "class_diagram:OrderControl:operation:old": (
                    "class_diagram:OrderControl:operation:stable-1"
                )
            },
        ),
        tools=tools,
    )

    assert isinstance(result, CommandIntent)
    assert result.targets == ["class_diagram:OrderControl:operation:stable-1"]


def test_ambiguous_revision_returns_clarification_without_execution() -> None:
    def propose(schema, _messages):
        if schema.__name__ == "_ConversationPlan":
            return schema(kind="command", intent="revise", query="unknown")
        raise AssertionError("target selection must not run without candidates")

    tools = FakeTools()
    tools.matches = []
    result = ConversationAgent(propose).respond(
        "app-1", "Fix that part.", context(), tools=tools
    )

    assert isinstance(result, Clarification)
    assert result.candidates == []


def test_natural_checkpoint_request_returns_only_action_and_stage() -> None:
    def propose(schema, _messages):
        return schema(kind="command", intent="branch", stage="design")

    result = ConversationAgent(propose).respond(
        "app-1", "Create a branch after design.", context(), tools=FakeTools()
    )

    assert isinstance(result, CommandIntent)
    assert result.intent == "branch"
    assert result.stage == "design"


def _completed_command(stage: str = "requirements") -> dict[str, Any]:
    return {
        "command_id": f"{stage}-command",
        "app_id": "app-1",
        "action": "message",
        "stage": stage,
        "status": "COMPLETED",
        "payload": {},
        "result": {},
    }


def test_natural_advance_uses_the_same_published_transition(monkeypatch) -> None:
    latest = _completed_command()
    monkeypatch.setattr(workspace_module.repository, "latest_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module.repository, "get_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module, "build_conversation_context", lambda _app: context())
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "respond",
        lambda *_a, **_k: CommandIntent(intent="advance", instruction="Continue."),
    )
    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={"text": "Continue.", "action_id": latest["command_id"]},
            stage=None,
        )
    finally:
        service.shutdown()

    assert action == "start_design"
    assert payload["action_id"] == latest["command_id"]
    assert payload["conversation_intent"]["intent"] == "advance"
    assert stage is None


def test_general_reply_preserves_the_underlying_workflow_actions(monkeypatch) -> None:
    latest = _completed_command("design")
    monkeypatch.setattr(workspace_module.repository, "latest_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module.repository, "get_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module, "build_conversation_context", lambda _app: context())
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "respond",
        lambda *_a, **_k: Reply(text="I can help review the current design."),
    )
    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={"text": "Can you help?", "action_id": latest["command_id"]},
            stage=None,
        )
    finally:
        service.shutdown()

    assert action == "message"
    assert payload["_conversation_outcome"]["kind"] == "reply"
    assert [item["action"] for item in payload["_conversation_actions"]] == [
        "message",
        "start_implementation",
    ]
    assert stage == "design"


def _testing_platform_wait() -> dict[str, Any]:
    return {
        "command_id": "testing-failure",
        "app_id": "app-1",
        "action": "start_testing",
        "stage": "testing",
        "status": "AWAITING_INPUT",
        "payload": {},
        "result": {
            "requires_revision": True,
            "can_delegate_repair": False,
            "job": {"implementation_job_id": "implementation-1"},
            "blocking_findings": [
                {
                    "repairable": True,
                    "defect_class": "TEST_DEFECT",
                    "repair_owner": "testing",
                }
            ],
        },
    }


def test_testing_retry_message_uses_the_offered_retry_via_conversation(monkeypatch) -> None:
    latest = _testing_platform_wait()
    monkeypatch.setattr(workspace_module.repository, "latest_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module.repository, "get_command", lambda *_a, **_k: latest)

    monkeypatch.setattr(workspace_module, "build_conversation_context", lambda _app: context())
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "respond",
        lambda *_a, **_k: CommandIntent(intent="advance", instruction="Retry testing."),
    )
    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={"text": "test", "action_id": latest["command_id"]},
            stage=None,
        )
    finally:
        service.shutdown()

    assert action == "start_testing"
    assert payload["action_id"] == "testing-failure"
    assert payload["implementation_job_id"] == "implementation-1"
    assert payload["conversation_intent"]["intent"] == "advance"
    assert stage is None


def test_testing_clarification_preserves_the_retry_action(monkeypatch) -> None:
    latest = _testing_platform_wait()
    monkeypatch.setattr(workspace_module.repository, "latest_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module.repository, "get_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module, "build_conversation_context", lambda _app: context())
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "respond",
        lambda *_a, **_k: Clarification(question="What should be retried?"),
    )
    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={"text": "What now?", "action_id": latest["command_id"]},
            stage=None,
        )
    finally:
        service.shutdown()

    assert action == "message"
    assert stage == "testing"
    assert [item["action"] for item in payload["_conversation_actions"]] == [
        "message",
        "start_testing",
    ]


def test_conversation_failure_becomes_a_retryable_persisted_clarification(
    monkeypatch,
) -> None:
    latest = _completed_command("design")
    monkeypatch.setattr(workspace_module.repository, "latest_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module.repository, "get_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module, "build_conversation_context", lambda _app: context())

    def fail(*_args, **_kwargs):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(workspace_module.conversation_agent, "respond", fail)
    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={"text": "Can you help?", "action_id": latest["command_id"]},
            stage=None,
        )
    finally:
        service.shutdown()

    assert action == "message"
    assert payload["text"] == "Can you help?"
    assert payload["_conversation_outcome"]["kind"] == "clarification"
    assert "retry" in payload["_conversation_outcome"]["question"].lower()
    assert stage == "design"


def test_natural_followup_uses_actions_preserved_by_a_reply(monkeypatch) -> None:
    latest = {
        **_completed_command("design"),
        "command_id": "reply-command",
        "payload": {
            "_conversation_actions": [
                {
                    "action": "delegate_repair",
                    "label": "Delegate repair to LLM",
                    "payload": {"action_id": "repair-command"},
                }
            ]
        },
    }
    monkeypatch.setattr(workspace_module.repository, "latest_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module.repository, "get_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(workspace_module, "build_conversation_context", lambda _app: context())
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "respond",
        lambda *_a, **_k: CommandIntent(
            intent="delegate_repair", instruction="Please repair it."
        ),
    )
    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={"text": "Please repair it.", "action_id": "reply-command"},
            stage=None,
        )
    finally:
        service.shutdown()

    assert action == "delegate_repair"
    assert payload["action_id"] == "repair-command"
    assert stage is None


def test_repeated_conversation_clarifications_route_to_original_offer(monkeypatch) -> None:
    original = {
        **_completed_command("design"),
        "command_id": "original-command",
        "payload": {
            "_conversation_actions": [
                {
                    "action": "delegate_repair",
                    "label": "Delegate repair to LLM",
                    "payload": {"action_id": "repair-command"},
                }
            ]
        },
    }
    first = {
        **_completed_command("design"),
        "command_id": "clarification-one",
        "payload": {"action_id": "original-command", "text": "Which one?"},
        "result": {"conversation": {"clarification": {"question": "Which one?"}}},
    }
    latest = {
        **_completed_command("design"),
        "command_id": "clarification-two",
        "payload": {"action_id": "clarification-one", "text": "Still unclear"},
        "result": {"conversation": {"clarification": {"question": "Please clarify."}}},
    }
    commands = {item["command_id"]: item for item in (original, first, latest)}

    def get_command(command_id):
        return commands.get(command_id)

    monkeypatch.setattr(workspace_module.repository, "latest_command", lambda *_a, **_k: latest)
    monkeypatch.setattr(
        workspace_module.repository,
        "get_command",
        get_command,
    )
    monkeypatch.setattr(workspace_module, "build_conversation_context", lambda _app: context())
    monkeypatch.setattr(
        workspace_module.conversation_agent,
        "respond",
        lambda *_a, **_k: CommandIntent(
            intent="delegate_repair", instruction="Delegate the original repair."
        ),
    )

    service = WorkspaceService()
    try:
        action, payload, stage = service._prepare_conversational_message(
            "app-1",
            action="message",
            payload={"text": "Yes, do that.", "action_id": "clarification-two"},
            stage=None,
        )
    finally:
        service.shutdown()

    assert action == "delegate_repair"
    assert payload["action_id"] == "repair-command"
    assert stage is None
