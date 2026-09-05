"""요구사항 그래프 결과가 Workspace에 전달되는 공개 dict 계약을 검증한다."""

from types import SimpleNamespace

import pytest

from app.requirements.orchestration.graph import result_payload


def test_payload_completed():
    result = {"phase": "reconcile", "classified": [{"id": "R1"}]}
    out = result_payload(result, "tid-1")
    assert out["status"] == "completed"
    assert out["thread_id"] == "tid-1"
    assert out["requirements"] == [{"id": "R1"}]


def test_payload_omits_pipeline_when_stubs_off():
    # 파이프라인 미실행(step2~4 키 없음) → 응답에도 없어야 함.
    result = {"phase": "reconcile", "classified": [{"id": "R1"}]}
    out = result_payload(result, "tid")
    for key in ("actors", "use_cases", "coverage", "use_case_specs", "relationships", "diagram"):
        assert key not in out


def test_payload_includes_pipeline_artifacts_when_present():
    result = {
        "phase": "diagram",
        "classified": [{"id": "R1"}],
        "actors": [{"name": "User", "kind": "primary", "description": "d"}],
        "use_cases": [{"id": "UC1", "name": "Log in"}],
        "coverage": {"coverage_ratio": 1.0},
        "use_case_specs": [{"use_case_id": "UC1", "main_scenario": []}],
        "relationships": {"associations": [{"actor": "User", "use_case": "Log in"}]},
        "diagram": "@startuml\n@enduml",
    }
    out = result_payload(result, "tid")
    assert out["actors"][0]["name"] == "User"
    assert out["use_cases"][0]["id"] == "UC1"
    assert out["coverage"]["coverage_ratio"] == 1.0
    assert out["use_case_specs"][0]["use_case_id"] == "UC1"
    assert out["relationships"]["associations"][0]["actor"] == "User"
    assert out["diagram"] == "@startuml\n@enduml"


def test_payload_omits_empty_pipeline_values():
    # 빈 값(예: use_cases=[])은 싣지 않는다.
    result = {"phase": "reconcile", "classified": [], "use_cases": [], "diagram": ""}
    out = result_payload(result, "tid")
    assert "use_cases" not in out and "diagram" not in out


def test_payload_need_clarification():
    interrupt_obj = SimpleNamespace(value={"questions": ["q1", "q2"]})
    result = {"__interrupt__": [interrupt_obj]}
    out = result_payload(result, "tid-2")
    assert out["status"] == "need_clarification"
    assert out["questions"] == ["q1", "q2"]


# ---------------------------------------------------------------------------
# 구조화 편집(F) — 게이트가 준 재료가 응답까지 흘러가는가, 라우팅이 갈리는가.
# ---------------------------------------------------------------------------
def test_feedback_payload_carries_the_edit_material():
    interrupt_obj = SimpleNamespace(
        value={
            "stage": "specs",
            "status": "need_feedback",
            "prompt": "p",
            "summary": ["UC1"],
            "edit_stage": "specs",
            "edit_targets": ["UC1", "UC2"],
        }
    )
    out = result_payload({"__interrupt__": [interrupt_obj]}, "tid")
    assert out["status"] == "need_feedback"
    assert out["edit_stage"] == "specs"
    assert out["edit_targets"] == ["UC1", "UC2"]


def test_analyze_rejects_answer_and_edit_together():
    """둘 다 오면 무엇을 따를지 모호하다 — 조용히 하나를 고르지 않는다."""
    from app.requirements.orchestration.service import analyze_requirements
    from app.requirements.schemas import AnalyzeRequest, FeedbackEdit

    req = AnalyzeRequest(
        answer="자연어",
        edit=FeedbackEdit(stage="specs", instruction="구조화"),
        thread_id="t",
    )
    with pytest.raises(ValueError, match="Send only one resume input"):
        analyze_requirements(req)


def test_initial_cloud_constraints_are_structured_and_normalized():
    from app.requirements.schemas import AnalyzeRequest

    request = AnalyzeRequest(
        requirements=["Users can sign in."],
        cloud_constraints={
            "provider": "aws",
            "region": " Seoul ",
            "monthly_budget_amount": 300,
            "monthly_budget_currency": "usd",
        },
    )

    assert request.cloud_constraints is not None
    assert request.cloud_constraints.region == "Seoul"
    assert request.cloud_constraints.monthly_budget_currency == "USD"


def test_initial_cloud_constraints_do_not_require_an_optional_budget():
    from app.requirements.schemas import AnalyzeRequest

    request = AnalyzeRequest(
        requirements=["Users can register for a course."],
        cloud_constraints={"provider": "aws", "region": "ap-northeast-2"},
    )

    assert request.cloud_constraints is not None
    assert request.cloud_constraints.provider == "aws"
    assert request.cloud_constraints.region == "ap-northeast-2"
    assert request.cloud_constraints.monthly_budget_amount is None


def test_deployment_preferences_preserve_selected_zones_without_deciding_topology():
    from app.requirements.schemas import DeploymentPreferences

    intake = DeploymentPreferences.model_validate(
        {
            "targets": [
                {
                    "provider": "aws",
                    "region": "ap-northeast-2",
                    "zones": ["ap-northeast-2a", "ap-northeast-2b"],
                }
            ]
        }
    )

    assert intake.targets[0].zones == ["ap-northeast-2a", "ap-northeast-2b"]
    assert "compute_profile" not in intake.model_dump()

    preferences = DeploymentPreferences.model_validate(
        {
            "targets": [
                {
                    "provider": "aws",
                    "region": "ap-northeast-2",
                    "zones": ["ap-northeast-2a", "ap-northeast-2b"],
                },
                {
                    "provider": "azure",
                    "region": "koreacentral",
                    "zones": ["1", "2"],
                },
            ],
            "compute_profile": "managedGroupManyMultiZone",
            "replica_count": 2,
            "public_ingress": "loadBalanced",
            "monthly_budget_currency": "krw",
        }
    )

    assert preferences.monthly_budget_currency == "KRW"
    assert len(preferences.targets) == 2


def test_minimal_deployment_intake_does_not_invent_topology_choices():
    from app.requirements.schemas import DeploymentPreferences

    preferences = DeploymentPreferences.model_validate(
        {
            "targets": [{"provider": "aws", "region": "ap-northeast-2"}],
            "monthly_budget_amount": 200,
            "monthly_budget_currency": "usd",
        }
    )

    assert preferences.model_dump(mode="json", exclude_unset=True) == {
        "targets": [{"provider": "aws", "region": "ap-northeast-2"}],
        "monthly_budget_amount": 200.0,
        "monthly_budget_currency": "USD",
    }


def test_deployment_preferences_ignore_removed_topology_fields():
    from app.requirements.schemas import DeploymentPreferences

    preferences = DeploymentPreferences.model_validate(
        {
            "targets": [{"provider": "aws", "region": "ap-northeast-2"}],
            "compute_profile": "managedGroupOne",
        }
    )

    assert "compute_profile" not in preferences.model_dump()
    assert "public_ingress" not in preferences.model_dump()


def test_deployment_preferences_do_not_store_removed_placement_fields():
    from app.requirements.schemas import DeploymentPreferences

    preferences = DeploymentPreferences.model_validate(
        {
            "targets": [{"provider": "aws", "region": "ap-northeast-2"}],
            "compute_profile": "managedGroupOne",
            "persistent_workload_placement": "colocate",
        }
    )

    dumped = preferences.model_dump()
    assert "compute_profile" not in dumped
    assert "persistent_workload_placement" not in dumped


def test_deployment_preferences_reject_two_regions_for_one_provider():
    from pydantic import ValidationError

    from app.requirements.schemas import DeploymentPreferences

    with pytest.raises(ValidationError, match="at most one region"):
        DeploymentPreferences.model_validate(
            {
                "targets": [
                    {"provider": "gcp", "region": "asia-northeast3"},
                    {"provider": "gcp", "region": "us-central1"},
                ]
            }
        )


def test_analyze_routes_a_structured_edit_to_resume(monkeypatch):
    from app.requirements.orchestration import service
    from app.requirements.schemas import AnalyzeRequest, FeedbackEdit

    seen = {}

    def fake_resume(answer, thread_id, *, persist=False):
        seen.update(answer=answer, thread_id=thread_id, persist=persist)
        return {"thread_id": thread_id, "phase": "specs", "status": "completed"}

    monkeypatch.setattr(service, "resume_analysis", fake_resume)
    monkeypatch.setattr(service.settings, "enable_session_persistence", True)
    edit = FeedbackEdit(stage="specs", scope="local", target_ids=["UC1"], instruction="고쳐")
    service.analyze_requirements(AnalyzeRequest(edit=edit, thread_id="t-9"))

    assert seen["answer"] is edit  # 문자열로 뭉개지 않고 그대로 넘어간다
    assert seen["thread_id"] == "t-9"
    assert seen["persist"] is True  # 서빙 경로는 세션을 DB에 남긴다


def test_retry_analysis_reuses_the_persistent_checkpoint_without_new_input(
    monkeypatch,
):
    from app.requirements.orchestration import graph

    captured = {}
    monkeypatch.setattr(graph, "_recall_mode", lambda _thread_id, _persist: True)
    monkeypatch.setattr(
        graph,
        "_has_checkpoint",
        lambda _gates, _thread_id, _persist: True,
    )

    def invoke(gates, thread_id, graph_input, persistent):
        captured.update(
            gates=gates,
            thread_id=thread_id,
            graph_input=graph_input,
            persistent=persistent,
        )
        return {"phase": "completed", "classified": []}

    monkeypatch.setattr(graph, "_invoke", invoke)

    result = graph.retry_analysis("app-1", persist=True)

    assert captured == {
        "gates": True,
        "thread_id": "app-1",
        "graph_input": None,
        "persistent": True,
    }
    assert result["status"] == "completed"


def test_revise_analysis_enters_the_exact_feedback_gate(monkeypatch):
    from app.requirements.orchestration import graph
    from app.requirements.schemas import FeedbackEdit

    calls: list[object] = []

    class Compiled:
        @staticmethod
        def get_state(_config):
            return SimpleNamespace(
                values={"classified": []},
                next=(),
                config={
                    "configurable": {
                        "thread_id": "app-1",
                        "checkpoint_id": "checkpoint-1",
                    }
                },
            )

        @staticmethod
        def update_state(_config, values, *, as_node):
            calls.append(("update", values, as_node))

        @staticmethod
        def invoke(graph_input, _config):
            calls.append(graph_input)
            if graph_input is None:
                return {
                    "classified": [],
                    "__interrupt__": [
                        SimpleNamespace(
                            value={
                                "status": "need_feedback",
                                "stage": "specs",
                                "prompt": "Review specifications.",
                            }
                        )
                    ],
                }
            return {"phase": "specs", "classified": []}

    monkeypatch.setattr(graph, "_recall_mode", lambda *_args: True)
    monkeypatch.setattr(graph, "_compiled", lambda *_args: Compiled())
    edit = FeedbackEdit(
        stage="specs",
        scope="local",
        target_ids=["UC1"],
        instruction="Rename the success guarantee.",
    )

    result = graph.revise_analysis(edit, "app-1", persist=True)

    assert calls[0] == ("update", {}, "write_specifications")
    assert calls[1] is None
    assert getattr(calls[2], "resume") is edit
    assert result["status"] == "completed"


def test_restore_analysis_checkpoint_branches_from_the_original_gate(monkeypatch):
    from app.requirements.orchestration import graph

    observed: dict[str, object] = {}
    original_config = {
        "configurable": {
            "thread_id": "app-1",
            "checkpoint_id": "checkpoint-before-revision",
        }
    }
    restored_config = {
        "configurable": {
            "thread_id": "app-1",
            "checkpoint_id": "checkpoint-restored",
        }
    }

    class Compiled:
        @staticmethod
        def update_state(config, values, *, as_node):
            observed.update(config=config, values=values, as_node=as_node)
            return restored_config

        @staticmethod
        def invoke(graph_input, config):
            observed.update(graph_input=graph_input, invoke_config=config)
            return {
                "__interrupt__": [
                    SimpleNamespace(value={"status": "need_feedback", "stage": "specs"})
                ]
            }

    monkeypatch.setattr(graph, "_recall_mode", lambda *_args: True)
    monkeypatch.setattr(graph, "_compiled", lambda *_args: Compiled())

    graph.restore_analysis_checkpoint(
        "app-1",
        {"config": original_config, "next": ["gate_specs"]},
        persist=True,
    )

    assert observed == {
        "config": original_config,
        "values": {},
        "as_node": "write_specifications",
        "graph_input": None,
        "invoke_config": restored_config,
    }


def test_capture_analysis_checkpoint_rejects_a_failed_gate_task(monkeypatch):
    from app.requirements.orchestration import graph

    class Compiled:
        @staticmethod
        def get_state(_config):
            return SimpleNamespace(
                values={"classified": []},
                config={"configurable": {"thread_id": "app-1"}},
                next=("gate_specs",),
                tasks=(
                    SimpleNamespace(
                        name="gate_specs",
                        error="gate failed",
                        interrupts=(),
                    ),
                ),
            )

    monkeypatch.setattr(graph, "_recall_mode", lambda *_args: True)
    monkeypatch.setattr(graph, "_compiled", lambda *_args: Compiled())

    with pytest.raises(ValueError, match="not at a safe feedback checkpoint"):
        graph.capture_analysis_checkpoint("app-1", persist=True)


def test_retry_analysis_rejects_a_missing_checkpoint(monkeypatch):
    from app.requirements.orchestration import graph

    monkeypatch.setattr(graph, "_recall_mode", lambda _thread_id, _persist: False)
    monkeypatch.setattr(
        graph,
        "_has_checkpoint",
        lambda _gates, _thread_id, _persist: False,
    )

    with pytest.raises(ValueError, match="No saved checkpoint was found"):
        graph.retry_analysis("missing-run", persist=True)


def test_retry_analysis_service_persists_only_new_stage_versions(monkeypatch):
    from app.requirements.orchestration import service

    monkeypatch.setattr(
        service,
        "retry_analysis",
        lambda thread_id, *, persist: {
            "thread_id": thread_id,
            "phase": "completed",
            "status": "completed",
        },
    )
    monkeypatch.setattr(service.settings, "enable_session_persistence", True)
    monkeypatch.setattr(
        service,
        "persist_analysis",
        lambda app_id, _payload: [f"saved-for-{app_id}"],
    )

    result = service.retry_requirements_analysis("app-1", app_id="app-1")

    assert result["saved_stages"] == ["saved-for-app-1"]


def test_requirement_revision_restores_checkpoint_when_artifact_save_fails(
    monkeypatch,
):
    from app.requirements.orchestration import service
    from app.requirements.schemas import FeedbackEdit

    checkpoint = {
        "config": {"configurable": {"thread_id": "app-1"}},
        "next": [],
    }
    restored: dict[str, object] = {}
    monkeypatch.setattr(
        service,
        "capture_analysis_checkpoint",
        lambda *_args, **_kwargs: checkpoint,
    )
    monkeypatch.setattr(
        service,
        "revise_analysis",
        lambda *_args, **_kwargs: {"status": "completed"},
    )
    monkeypatch.setattr(
        service,
        "persist_analysis",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("save failed")),
    )
    monkeypatch.setattr(
        service,
        "restore_analysis_checkpoint",
        lambda thread_id, snapshot, *, persist: restored.update(
            thread_id=thread_id,
            snapshot=snapshot,
            persist=persist,
        ),
    )
    monkeypatch.setattr(service.settings, "enable_session_persistence", True)
    edit = FeedbackEdit(stage="relationships", instruction="Add the association.")

    with pytest.raises(RuntimeError, match="save failed"):
        service.revise_requirements_analysis(edit, "app-1", app_id="app-1")

    assert restored == {
        "thread_id": "app-1",
        "snapshot": checkpoint,
        "persist": True,
    }


def test_feedback_payload_carries_the_resource_questions():
    """되묻기가 응답까지 못 오면 화면이 `resource_answers`를 만들 수 없다."""
    questions = [{"field": "region", "kind": "missing", "why": "w", "question": "q"}]
    result = {
        "__interrupt__": [
            SimpleNamespace(
                value={
                    "status": "need_feedback",
                    "stage": "requirements",
                    "prompt": "p",
                    "summary": [],
                    "resource_questions": questions,
                }
            )
        ]
    }
    out = result_payload(result, "tid-r")

    assert out["resource_questions"] == questions
