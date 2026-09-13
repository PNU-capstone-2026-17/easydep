"""Workspace 대화형 project tool의 읽기·버전·owner 경계를 검증한다."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.db.models import (
    TYPE_API_SPEC,
    TYPE_CLASS,
    TYPE_REFINE_REQ,
    TYPE_SEQUENCE,
    TYPE_SOURCE_CODE,
    TYPE_USECASE_SPEC,
)
from app.design.services.class_diagram.identity import stable_operation_id
from app.workspace.conversation import project_tools as project_tools_module
from app.workspace.conversation.context import build_conversation_context
from app.workspace.conversation.contracts import (
    Clarification,
    CommandIntent,
    Reply,
    RevisionInterpretation,
)
from app.workspace.conversation.project_tools import ProjectTools
from app.workspace.conversation.revision_planner import RevisionPlanner

APP_ID = "11111111-1111-4111-8111-111111111111"


def _state() -> dict[str, Any]:
    return {
        "artifact_versions": {
            TYPE_REFINE_REQ: {"version_id": 11, "version_no": 1},
            TYPE_USECASE_SPEC: {"version_id": 12, "version_no": 2},
            TYPE_CLASS: {"version_id": 21, "version_no": 3},
            TYPE_SEQUENCE: {"version_id": 22, "version_no": 3},
            TYPE_API_SPEC: {"version_id": 23, "version_no": 4},
        },
        "refined_requirements": {
            "requirements": [
                {"id": "REQ-ORDER", "text": "The member can place an order."}
            ]
        },
        "usecase_spec": {
            "actors": [{"name": "Member", "description": "Places orders."}],
            "use_cases": [
                {
                    "id": "UC-ORDER",
                    "name": "Place order",
                    "primary_actor": "Member",
                    "requirement_ids": ["REQ-ORDER"],
                }
            ],
            "use_case_specs": [
                {
                    "use_case_id": "UC-ORDER",
                    "name": "Place order",
                    "main_scenario": [
                        {"step_number": 1, "sentence": "Member submits an order."}
                    ],
                }
            ],
            "relationships": {
                "associations": [
                    {"actor": "Member", "use_case_id": "UC-ORDER"}
                ]
            },
        },
        "extracted_bce_classes": {
            "Classes": [
                {
                    "className": "OrderControl",
                    "stereotype": "Control",
                    "use_case_ids": ["UC-ORDER"],
                    "operations": [
                        {
                            "operationId": "OrderControl::placeOrder()",
                            "name": "placeOrder",
                        }
                    ],
                }
            ],
            "Collaborations": [],
        },
        "sequence_diagram_model": {
            "Diagrams": [
                {
                    "use_case_id": "UC-ORDER",
                    "Participants": [],
                    "Messages": [],
                }
            ]
        },
        "api_spec_model": {
            "Endpoints": [
                {
                    "operation_id": "placeOrder",
                    "method": "post",
                    "path": "/orders",
                    "source_classes": ["OrderControl"],
                    "use_case_ids": ["UC-ORDER"],
                }
            ],
            "Schemas": [],
        },
        "erd_bce_classes": {
            "Classes": [
                {"className": "Order", "stereotype": "Entity", "fields": []}
            ]
        },
    }


def _snapshot() -> dict[str, Any]:
    return {
        "version_id": 31,
        "version_no": 5,
        "snapshot_digest": "source-digest",
        "metadata": {
            "implementation_traceability": {
                "mappings": [
                    {
                        "taskId": "implement-order",
                        "target_file": "application/src/OrderService.java",
                        "requirementIds": ["REQ-ORDER"],
                        "useCaseIds": ["UC-ORDER"],
                        "sourceRefs": ["api:placeOrder"],
                    }
                ]
            }
        },
        "files": {
            "application/src/OrderService.java": {
                "content": "class OrderService {}",
                "sha256": "digest",
            }
        },
    }


@pytest.fixture
def tools(monkeypatch: pytest.MonkeyPatch) -> ProjectTools:
    monkeypatch.setattr(
        "app.workspace.conversation.project_tools.artifact_repository.load_state",
        lambda app_id: _state() if app_id == APP_ID else {},
    )
    monkeypatch.setattr(
        "app.workspace.conversation.project_tools.artifact_repository.load_file_snapshot",
        lambda app_id, artifact_type: (
            _snapshot() if app_id == APP_ID and artifact_type == TYPE_SOURCE_CODE else None
        ),
    )
    monkeypatch.setattr(
        "app.workspace.conversation.project_tools.workspace_repository.latest_command",
        lambda _app_id, **_kwargs: None,
    )
    return ProjectTools(APP_ID)


def test_conversation_contracts_reject_execution_fields_and_invalid_revision() -> None:
    assert Reply(text="안녕하세요").text == "안녕하세요"
    assert Clarification(question="어느 주문인가요?").candidates == []
    with pytest.raises(ValidationError):
        Reply(text="ok", stage="design")
    with pytest.raises(ValidationError):
        CommandIntent(intent="revise", targets=[], instruction="바꿔줘")
    command = CommandIntent(
        intent="revise",
        targets=["class_diagram:OrderControl"],
        instruction="이름을 바꿔줘",
    )
    assert command.model_dump(mode="json") == {
        "intent": "revise",
        "targets": ["class_diagram:OrderControl"],
        "instruction": "이름을 바꿔줘",
    }


def test_search_uses_latest_editing_catalog_and_returns_owner_and_version(
    tools: ProjectTools,
) -> None:
    matches = tools.search_elements("OrderControl")

    target = next(item for item in matches if item["ref"] == "class_diagram:OrderControl")
    assert target["owner"] == "design"
    assert target["editable"] is True
    assert target["artifact_type"] == TYPE_CLASS
    assert target["artifact_version_id"] == 21

    implementation = tools.search_elements("OrderService")
    source = next(item for item in implementation if item["ref"].startswith("file:"))
    assert source["owner"] == "implementation"
    assert source["artifact_version_id"] == 31


def test_search_ranks_evidence_spread_across_multiple_elements(
    tools: ProjectTools,
) -> None:
    matches = tools.search_elements(
        "How does the UC-ORDER sequence call OrderControl placeOrder?"
    )
    refs = {item["ref"] for item in matches[:5]}

    assert "sequence_diagram:UC-ORDER" in refs
    assert "class_diagram:OrderControl::placeOrder()" in refs


def test_revision_search_maps_use_case_evidence_to_current_collaboration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    state["extracted_bce_classes"]["Collaborations"] = [
        {
            "collaborationId": "UC-ORDER:main:1",
            "useCaseIds": ["UC-ORDER"],
            "calls": [],
        }
    ]
    monkeypatch.setattr(
        "app.workspace.conversation.project_tools.artifact_repository.load_state",
        lambda app_id: state if app_id == APP_ID else {},
    )
    monkeypatch.setattr(
        "app.workspace.conversation.project_tools.artifact_repository.load_file_snapshot",
        lambda *_args: None,
    )

    result = ProjectTools(APP_ID).search_revision_context(
        ["OrderControl", "place order"],
        anchor_refs=["class_diagram:OrderControl"],
    )

    candidate_refs = [item["ref"] for item in result["candidates"]]
    evidence_refs = [item["ref"] for item in result["evidence"]]
    assert candidate_refs[:2] == [
        "class_diagram:OrderControl",
        "class_diagram:UC-ORDER:main:1",
    ]
    assert "use_case_spec:UC-ORDER" in evidence_refs
    specification = next(
        item for item in result["evidence"]
        if item["ref"] == "use_case_spec:UC-ORDER"
    )
    assert specification["behavior"]["main_scenario"] == [
        {"step_number": 1, "sentence": "Member submits an order."}
    ]
    collaboration = next(
        item for item in result["evidence"]
        if item["ref"] == "class_diagram:UC-ORDER:main:1"
    )
    assert collaboration["content"]["collaborationId"] == "UC-ORDER:main:1"
    assert collaboration["content"]["useCaseIds"] == ["UC-ORDER"]
    assert collaboration["content"]["calls"] == []
    assert collaboration["rtm_sources"] == {"use_case": ["UC-ORDER"]}


def test_change_context_uses_the_global_trace_with_api_scope(
    tools: ProjectTools,
) -> None:
    result = tools.search_change_context(
        ["OrderControl"],
        artifact_stage="api_spec",
    )

    assert [item["ref"] for item in result["candidates"]] == [
        "api_spec:placeOrder"
    ]
    class_evidence = next(
        item for item in result["evidence"]
        if item["ref"] == "class_diagram:OrderControl"
    )
    assert "api_spec:placeOrder" in class_evidence["trace"]["direct_consumers"]


def test_selected_stage_filters_exact_same_name_hits_from_other_artifacts(
    tools: ProjectTools,
) -> None:
    result = tools.search_change_context(
        [
            "The requirements should state that members receive an email "
            "after order confirmation."
        ],
        anchor_refs=["entity:Order"],
        artifact_stage="refined_requirements",
    )

    assert [item["ref"] for item in result["candidates"]] == [
        "requirement:REQ-ORDER"
    ]
    assert "entity:Order" in {
        item["ref"] for item in result["evidence"]
    }


def test_change_context_can_cross_design_to_the_implementation_scope(
    tools: ProjectTools,
) -> None:
    result = tools.search_change_context(
        ["OrderControl"],
        artifact_stage=TYPE_SOURCE_CODE,
    )

    candidate_refs = {item["ref"] for item in result["candidates"]}
    assert candidate_refs == {
        "task:implement-order",
        "file:application/src/OrderService.java",
    }
    assert all(item["owner"] == "implementation" for item in result["candidates"])
    assert "task:implement-order" in result["relations"][
        "class_diagram:OrderControl"
    ]["downstream"]


def test_sequence_scope_includes_its_exact_class_collaboration_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    state["extracted_bce_classes"]["Collaborations"] = [
        {
            "collaborationId": "UC-ORDER:main:1",
            "useCaseIds": ["UC-ORDER"],
            "calls": [],
        }
    ]
    monkeypatch.setattr(
        project_tools_module.artifact_repository,
        "load_state",
        lambda _app_id: state,
    )
    monkeypatch.setattr(
        project_tools_module.artifact_repository,
        "load_file_snapshot",
        lambda *_args: _snapshot(),
    )
    monkeypatch.setattr(
        project_tools_module.workspace_repository,
        "latest_command",
        lambda *_args, **_kwargs: None,
    )

    result = ProjectTools(APP_ID).search_change_context(
        ["add a validation call"],
        anchor_refs=["sequence_diagram:UC-ORDER"],
        artifact_stage="sequence_diagram",
    )

    assert [item["ref"] for item in result["candidates"]][:2] == [
        "sequence_diagram:UC-ORDER",
        "class_diagram:UC-ORDER:main:1",
    ]


def test_erd_feedback_finds_and_routes_to_its_exact_class_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    state["extracted_bce_classes"]["Classes"].append(
        {
            "className": "Order",
            "stereotype": "Entity",
            "use_case_ids": ["UC-ORDER"],
            "fields": [],
            "operations": [],
        }
    )
    monkeypatch.setattr(
        project_tools_module.artifact_repository,
        "load_state",
        lambda _app_id: state,
    )
    monkeypatch.setattr(
        project_tools_module.artifact_repository,
        "load_file_snapshot",
        lambda *_args: _snapshot(),
    )
    monkeypatch.setattr(
        project_tools_module.workspace_repository,
        "latest_command",
        lambda *_args, **_kwargs: None,
    )
    tools = ProjectTools(APP_ID)

    context = tools.search_change_context(
        ["Order should store a delivery address."],
        anchor_refs=["entity:Order"],
        artifact_stage="erd",
    )
    plan = RevisionPlanner(tools).plan(
        RevisionInterpretation(
            targets=["entity:Order"],
            semantic_scope="contract",
            requested_effect="Add a delivery address.",
            change_type="add",
        )
    )

    candidate_refs = [item["ref"] for item in context["candidates"]]
    assert candidate_refs[:2] == [
        "entity:Order",
        "class_diagram:Order",
    ]
    assert "class_diagram:OrderControl" not in candidate_refs
    assert plan.status == "needs_confirmation"
    assert [target.ref for target in plan.authority_targets] == [
        "class_diagram:Order"
    ]


def test_testing_finding_context_uses_exact_file_hints_for_repair_scope(
    tools: ProjectTools,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def latest_command(_app_id: str, *, stage: str) -> dict[str, Any] | None:
        if stage != "testing":
            return None
        return {
            "command_id": "testing-1",
            "stage": "testing",
            "status": "AWAITING_INPUT",
            "result": {
                "blocking_findings": [
                    {
                        "code": "testing.static",
                        "repair_owner": "implementation",
                        "file_hints": ["application/src/OrderService.java"],
                    }
                ]
            },
        }

    monkeypatch.setattr(
        project_tools_module.workspace_repository,
        "latest_command",
        latest_command,
    )

    result = tools.search_change_context(
        ["testing.static"],
        artifact_stage="TESTING_RESULTS",
    )

    candidate_refs = {item["ref"] for item in result["candidates"]}
    assert {
        "finding:testing.static",
        "file:application/src/OrderService.java",
        "task:implement-order",
    } <= candidate_refs


def test_exact_identifier_and_path_search_work_across_registration_and_incident_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    state["refined_requirements"]["requirements"].append(
        {"id": "REQ-REGISTER", "text": "A student can register for a course."}
    )
    state["usecase_spec"]["use_cases"].append(
        {"id": "UC-REGISTER", "name": "Course registration"}
    )
    state["api_spec_model"]["Endpoints"].append(
        {
            "operation_id": "acknowledgeIncident",
            "method": "post",
            "path": "/incidents/{incidentId}/acknowledgement",
        }
    )
    state["api_spec_model"]["Schemas"].append(
        {"name": "IncidentAcknowledgement", "fields": ["incidentId"]}
    )
    monkeypatch.setattr(
        project_tools_module.artifact_repository,
        "load_state",
        lambda _app_id: state,
    )
    monkeypatch.setattr(
        project_tools_module.artifact_repository,
        "load_file_snapshot",
        lambda _app_id, _artifact_type: _snapshot(),
    )
    monkeypatch.setattr(
        project_tools_module.workspace_repository,
        "latest_command",
        lambda *_args, **_kwargs: None,
    )
    cross_domain_tools = ProjectTools(APP_ID)

    registration = cross_domain_tools.resolve_exact_elements(
        "Revise requirement:REQ-REGISTER."
    )
    incident_schema = cross_domain_tools.resolve_exact_elements(
        "Rename schema:IncidentAcknowledgement."
    )
    incident_path = cross_domain_tools.search_elements("incident path")

    assert [item["ref"] for item in registration] == ["requirement:REQ-REGISTER"]
    assert [item["ref"] for item in incident_schema] == [
        "api_spec:IncidentAcknowledgement"
    ]
    assert incident_path[0]["ref"] == "api_spec:acknowledgeIncident"


def test_implementation_catalog_matches_workspace_rtm_to_application_snapshot_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RTM의 application/ 경로와 snapshot의 application-root 경로를 같은 파일로 본다."""

    snapshot = _snapshot()
    snapshot["files"] = {
        "src/OrderService.java": {
            "content": "class OrderService {}",
            "sha256": "digest",
        }
    }
    monkeypatch.setattr(
        project_tools_module.artifact_repository,
        "load_state",
        lambda _app_id: _state(),
    )
    monkeypatch.setattr(
        project_tools_module.artifact_repository,
        "load_file_snapshot",
        lambda _app_id, _artifact_type: snapshot,
    )
    monkeypatch.setattr(
        project_tools_module.workspace_repository,
        "latest_command",
        lambda *_args, **_kwargs: None,
    )

    validation = ProjectTools(APP_ID).validate_targets(
        ["file:application/src/OrderService.java"]
    )

    assert validation["valid_refs"] == ["file:application/src/OrderService.java"]


def test_read_element_reads_only_the_selected_current_element(tools: ProjectTools) -> None:
    item = tools.read_element("requirement:REQ-ORDER")

    assert item["app_id"] == APP_ID
    assert item["artifact_version_id"] == 11
    assert item["content"] == {
        "id": "REQ-ORDER",
        "text": "The member can place an order.",
    }

    description = tools.describe_element("requirement:REQ-ORDER")
    assert "content" not in description
    assert "member can place an order" in description["summary"]


def test_catalog_is_built_once_per_tool_instance(
    tools: ProjectTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = project_tools_module._build_catalog
    calls = 0

    def counted(app_id: str):
        nonlocal calls
        calls += 1
        return original(app_id)

    monkeypatch.setattr(project_tools_module, "_build_catalog", counted)

    tools.search_elements("Order")
    tools.read_element("requirement:REQ-ORDER")
    tools.validate_targets(["class_diagram:OrderControl"])

    assert calls == 1


def test_project_content_is_redacted_and_bounded_before_llm_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    snapshot["files"]["application/src/OrderService.java"]["content"] = (
        "api_key = supersecret\n" + "x" * 5_000
    )
    monkeypatch.setattr(
        project_tools_module.artifact_repository,
        "load_state",
        lambda _app_id: _state(),
    )
    monkeypatch.setattr(
        project_tools_module.artifact_repository,
        "load_file_snapshot",
        lambda _app_id, _artifact_type: snapshot,
    )
    monkeypatch.setattr(
        project_tools_module.workspace_repository,
        "latest_command",
        lambda *_args, **_kwargs: None,
    )

    item = ProjectTools(APP_ID).read_element(
        "file:application/src/OrderService.java"
    )
    content = item["content"]["file"]

    assert "supersecret" not in content
    assert "[REDACTED]" in content
    assert len(content) <= 4_000


def test_validate_targets_requires_canonical_edit_ref_and_matching_version(
    tools: ProjectTools,
) -> None:
    canonical = tools.validate_targets(
        [
            {
                "app_id": APP_ID,
                "ref": "class_diagram:OrderControl",
                "artifact_version_id": 21,
            }
        ]
    )
    alias = tools.validate_targets(["class:OrderControl"])
    stale = tools.validate_targets(
        [{"ref": "class_diagram:OrderControl", "artifact_version_id": 20}]
    )
    entity = tools.validate_targets(["entity:Order"])

    assert canonical["valid_refs"] == ["class_diagram:OrderControl"]
    assert alias["valid"] is False
    assert alias["targets"][0]["canonical_ref"] == "class_diagram:OrderControl"
    assert stale["targets"][0]["version_matches"] is False
    assert entity["targets"][0]["editable"] is False


def test_legacy_operation_target_freezes_the_acceptance_stable_identity(
    tools: ProjectTools,
) -> None:
    target = tools.normalize_revision_targets(
        ["class_diagram:OrderControl::placeOrder()"]
    )[0]

    assert target.kind == "operation"
    assert target.element_id == stable_operation_id(
        "OrderControl", "OrderControl::placeOrder()"
    )


def test_catalog_preserves_stable_ids_already_stored_in_accepted_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    operation = state["extracted_bce_classes"]["Classes"][0]["operations"][0]
    operation["stableId"] = "persisted-operation"
    state["extracted_bce_classes"]["Collaborations"] = [
        {
            "collaborationId": "UC-ORDER",
            "useCaseIds": ["UC-ORDER"],
            "calls": [
                {
                    "callId": "UC-ORDER::call:1",
                    "stableId": "persisted-call",
                    "receiverOperationId": "OrderControl::placeOrder()",
                    "stepRefs": ["UC-ORDER:main:1"],
                    "argumentBindings": [],
                }
            ],
        }
    ]
    monkeypatch.setattr(
        project_tools_module.artifact_repository,
        "load_state",
        lambda _app_id: state,
    )
    monkeypatch.setattr(
        project_tools_module.artifact_repository,
        "load_file_snapshot",
        lambda _app_id, _artifact_type: None,
    )
    monkeypatch.setattr(
        project_tools_module.workspace_repository,
        "latest_command",
        lambda *_args, **_kwargs: None,
    )

    accepted = ProjectTools(APP_ID)
    operation_target = accepted.normalize_revision_targets(
        ["class_diagram:OrderControl::placeOrder()"]
    )[0]
    call_target = accepted.normalize_revision_targets(
        ["class_diagram:UC-ORDER::call:1"], require_editable=False
    )[0]

    assert operation_target.element_id == "persisted-operation"
    assert call_target.element_id == "persisted-call"


def test_requirements_handoff_resolves_only_the_current_rtm_linked_boundary_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state()
    boundary = state["extracted_bce_classes"]["Classes"][0]
    boundary["className"] = "OrderBoundary"
    boundary["stereotype"] = "Boundary"
    operation = boundary["operations"][0]
    operation.update(
        operationId="OrderBoundary::placeOrder()",
        stepRefs=["UC-ORDER:main:1"],
    )
    state["extracted_bce_classes"]["Collaborations"] = [
        {
            "collaborationId": "UC-ORDER",
            "useCaseIds": ["UC-ORDER"],
            "entryActor": "Member",
            "calls": [
                {
                    "callId": "UC-ORDER::call:1",
                    "parentCallId": None,
                    "receiverOperationId": "OrderBoundary::placeOrder()",
                    "stepRefs": ["UC-ORDER:main:1"],
                    "argumentBindings": [],
                }
            ],
        }
    ]
    monkeypatch.setattr(
        project_tools_module.artifact_repository,
        "load_state",
        lambda _app_id: state,
    )
    monkeypatch.setattr(
        project_tools_module.artifact_repository,
        "load_file_snapshot",
        lambda _app_id, _artifact_type: None,
    )
    monkeypatch.setattr(
        project_tools_module.workspace_repository,
        "latest_command",
        lambda *_args, **_kwargs: None,
    )

    entries = ProjectTools(APP_ID).design_entry_targets_for_requirements(
        ["use_case_spec:UC-ORDER"]
    )

    assert [target.ref for target in entries] == [
        "class_diagram:OrderBoundary::placeOrder()"
    ]
    assert entries[0].kind == "operation"


def test_requirements_handoff_without_an_exact_boundary_root_is_not_executable(
    tools: ProjectTools,
) -> None:
    assert tools.design_entry_targets_for_requirements(
        ["use_case_spec:UC-ORDER"]
    ) == []


def test_trace_views_do_not_mix_latest_editing_and_frozen_testing_evidence(
    tools: ProjectTools, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen_calls: list[str] = []

    def frozen_trace(_app_id: str, ref: str) -> dict[str, Any]:
        frozen_calls.append(ref)
        return {"ref": ref, "trace_scope": "testing-input", "evidence": ["test:t:1"]}

    monkeypatch.setattr(
        "app.workspace.conversation.project_tools.artifact_trace_response",
        frozen_trace,
    )

    editing = tools.trace_impact(["requirement:REQ-ORDER"], view="editing")
    assert editing["view"] == "editing"
    assert editing["impacts"][0]["evidence"] == []
    assert "class:OrderControl" in editing["impacts"][0]["downstream"]
    assert frozen_calls == []

    evidence = tools.trace_impact(
        ["requirement:REQ-ORDER"], view="testing-evidence"
    )
    assert evidence["impacts"][0]["trace_scope"] == "testing-input"
    assert frozen_calls == ["requirement:REQ-ORDER"]


def test_stage_rewind_relations_include_exact_cross_delivery_downstream(
    tools: ProjectTools,
) -> None:
    requirements = tools.revision_relations(["requirements_stage:actors"])
    design = tools.revision_relations(["design_stage:class_diagram"])

    requirement_downstream = set(
        requirements["relations"]["requirements_stage:actors"]["downstream"]
    )
    design_downstream = set(
        design["relations"]["design_stage:class_diagram"]["downstream"]
    )

    assert "class_diagram:OrderControl" in requirement_downstream
    assert "file:application/src/OrderService.java" in requirement_downstream
    assert "api_spec:placeOrder" in design_downstream
    assert "file:application/src/OrderService.java" in design_downstream


def test_local_class_plan_uses_the_same_bounded_scope_as_the_design_change_plan(
    tools: ProjectTools,
) -> None:
    relations = tools.revision_relations(["class_diagram:OrderControl"])
    planned = set(
        relations["relations"]["class_diagram:OrderControl"]["downstream"]
    )
    impact = tools.trace_impact(["class_diagram:OrderControl"], view="editing")
    expected = set(impact["impacts"][0]["affected"])

    assert expected <= planned


def test_usecase_diagram_candidates_are_catalog_owned_and_cover_its_sections(
    tools: ProjectTools,
) -> None:
    refs = {item["ref"] for item in tools.artifact_candidates("usecase_diagram")}

    assert {
        "actor:Member",
        "use_case:UC-ORDER",
        "relationship:associations:Member->UC-ORDER",
        "requirements_stage:actors",
        "requirements_stage:relationships",
    } <= refs
    assert all(item["app_id"] == APP_ID for item in tools.artifact_candidates("usecase_diagram"))


def test_context_is_rebuilt_from_message_commands_and_status_not_stale_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = [
        {
            "command_id": "m1",
            "action": "message",
            "payload": {"text": "현재 상태를 알려줘"},
            "status": "COMPLETED",
            "result": {"conversation": {"reply": "설계를 검토 중입니다."}},
        }
    ]
    latest = {
        "command_id": "m1",
        "action": "message",
        "stage": "design",
        "status": "COMPLETED",
        "payload": messages[0]["payload"],
        "result": {"awaiting_input": True},
    }
    monkeypatch.setattr(
        "app.workspace.conversation.context._recent_conversation_commands",
        lambda _app_id, _limit: messages,
    )
    monkeypatch.setattr(
        "app.workspace.conversation.context.repository.latest_command",
        lambda _app_id: latest,
    )
    monkeypatch.setattr(
        "app.workspace.conversation.context.offered_actions",
        lambda _command: [],
    )

    context = build_conversation_context(APP_ID)

    assert [turn.role for turn in context.turns] == ["user", "assistant"]
    assert context.turns[1].text == "설계를 검토 중입니다."
    assert context.pending_question is None


def test_context_keeps_recent_turns_within_a_total_character_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = [
        {
            "command_id": f"m{index}",
            "action": "message",
            "payload": {"text": f"user-{index}-" + "u" * 10_000},
            "status": "COMPLETED",
            "result": {"conversation": {"reply": f"assistant-{index}-" + "a" * 10_000}},
        }
        for index in range(4)
    ]
    latest = {**messages[-1], "stage": "design"}
    monkeypatch.setattr(
        project_tools_module.workspace_repository,
        "latest_command",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.workspace.conversation.context._recent_conversation_commands",
        lambda _app_id, _limit: messages,
    )
    monkeypatch.setattr(
        "app.workspace.conversation.context.repository.latest_command",
        lambda _app_id: latest,
    )
    monkeypatch.setattr(
        "app.workspace.conversation.context.offered_actions",
        lambda _command: [],
    )

    context = build_conversation_context(APP_ID)

    assert sum(len(turn.text) for turn in context.turns) <= 24_000
    assert context.turns[-1].command_id == "m3"
    assert all(len(turn.text) <= 8_000 for turn in context.turns)


def test_context_reads_target_remap_from_revision_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = [
        {
            "command_id": "m1",
            "action": "message",
            "payload": {"text": "Rename the operation."},
            "status": "COMPLETED",
            "result": {},
        },
        {
            "command_id": "c1",
            "action": "confirm_change",
            "payload": {"action_id": "m1"},
            "status": "COMPLETED",
            "result": {
                "revision_execution": {
                    "target_remap": {
                        "class_diagram:Order::place()": "class_diagram:Order::submit()"
                    }
                }
            },
        },
    ]
    latest = {**commands[-1], "stage": "design"}
    monkeypatch.setattr(
        "app.workspace.conversation.context._recent_conversation_commands",
        lambda _app_id, _limit: commands,
    )
    monkeypatch.setattr(
        "app.workspace.conversation.context.repository.latest_command",
        lambda _app_id: latest,
    )
    monkeypatch.setattr(
        "app.workspace.conversation.context.offered_actions",
        lambda _command: [],
    )

    context = build_conversation_context(APP_ID)

    assert context.target_remap == {
        "class_diagram:Order::place()": "class_diagram:Order::submit()"
    }
    assert [turn.command_id for turn in context.turns] == ["m1"]
