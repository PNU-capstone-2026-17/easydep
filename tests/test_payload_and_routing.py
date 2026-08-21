"""_result_payload 응답 변환 테스트."""

from types import SimpleNamespace

import pytest

from app.requirements.agent.graph import _result_payload
from app.requirements.schemas import AnalyzeResponse


def test_payload_completed():
    result = {"phase": "reconcile", "classified": [{"id": "R1"}]}
    out = _result_payload(result, "tid-1")
    assert out["status"] == "completed"
    assert out["thread_id"] == "tid-1"
    assert out["requirements"] == [{"id": "R1"}]


def test_payload_omits_pipeline_when_stubs_off():
    # 파이프라인 미실행(step2~4 키 없음) → 응답에도 없어야 함.
    result = {"phase": "reconcile", "classified": [{"id": "R1"}]}
    out = _result_payload(result, "tid")
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
    out = _result_payload(result, "tid")
    assert out["actors"][0]["name"] == "User"
    assert out["use_cases"][0]["id"] == "UC1"
    assert out["coverage"]["coverage_ratio"] == 1.0
    assert out["use_case_specs"][0]["use_case_id"] == "UC1"
    assert out["relationships"]["associations"][0]["actor"] == "User"
    assert out["diagram"] == "@startuml\n@enduml"


def test_payload_omits_empty_pipeline_values():
    # 빈 값(예: use_cases=[])은 싣지 않는다.
    result = {"phase": "reconcile", "classified": [], "use_cases": [], "diagram": ""}
    out = _result_payload(result, "tid")
    assert "use_cases" not in out and "diagram" not in out


def test_payload_need_clarification():
    interrupt_obj = SimpleNamespace(value={"questions": ["q1", "q2"]})
    result = {"__interrupt__": [interrupt_obj]}
    out = _result_payload(result, "tid-2")
    assert out["status"] == "need_clarification"
    assert out["questions"] == ["q1", "q2"]


def test_analyze_response_accepts_and_omits_pipeline_fields():
    req = {"id": "FR1", "text": "log in", "type": "FR"}
    # 파이프라인 산출물이 있는 payload → AnalyzeResponse가 검증·직렬화한다.
    result = {
        "phase": "diagram",
        "classified": [req],
        "use_cases": [{"id": "UC1", "name": "Log in"}],
        "diagram": "@startuml\n@enduml",
    }
    resp = AnalyzeResponse(**_result_payload(result, "tid"))
    dumped = resp.model_dump()
    assert dumped["use_cases"][0]["id"] == "UC1"
    assert dumped["diagram"] == "@startuml\n@enduml"

    # 파이프라인 미실행 → 해당 필드는 None.
    resp2 = AnalyzeResponse(**_result_payload({"classified": [req]}, "t"))
    assert resp2.diagram is None and resp2.use_cases is None and resp2.actors is None


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
    out = _result_payload({"__interrupt__": [interrupt_obj]}, "tid")
    assert out["status"] == "need_feedback"
    assert out["edit_stage"] == "specs"
    assert out["edit_targets"] == ["UC1", "UC2"]
    # 스키마도 통과해야 화면까지 간다.
    assert AnalyzeResponse(**out).edit_targets == ["UC1", "UC2"]


def test_analyze_rejects_answer_and_edit_together():
    """둘 다 오면 무엇을 따를지 모호하다 — 조용히 하나를 고르지 않는다."""
    from fastapi import HTTPException

    from app.requirements.api import analyze_endpoint
    from app.requirements.schemas import AnalyzeRequest, FeedbackEdit

    req = AnalyzeRequest(
        answer="자연어",
        edit=FeedbackEdit(stage="specs", instruction="구조화"),
        thread_id="t",
    )
    with pytest.raises(HTTPException) as excinfo:
        analyze_endpoint(req)
    assert excinfo.value.status_code == 400


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
    from app.requirements import api
    from app.requirements.schemas import AnalyzeRequest, FeedbackEdit

    seen = {}

    def fake_resume(answer, thread_id, *, persist=False):
        seen.update(answer=answer, thread_id=thread_id, persist=persist)
        return {"thread_id": thread_id, "phase": "specs", "status": "completed"}

    monkeypatch.setattr(api, "resume_analysis", fake_resume)
    monkeypatch.setattr(api.settings, "enable_session_persistence", True)
    edit = FeedbackEdit(stage="specs", scope="local", target_ids=["UC1"], instruction="고쳐")
    api.analyze_endpoint(AnalyzeRequest(edit=edit, thread_id="t-9"))

    assert seen["answer"] is edit  # 문자열로 뭉개지 않고 그대로 넘어간다
    assert seen["thread_id"] == "t-9"
    assert seen["persist"] is True  # 서빙 경로는 세션을 DB에 남긴다


def test_every_artifact_key_survives_the_response_schema():
    """응답 스키마에 없는 산출물 키는 **조용히 사라진다** — pydantic이 모르는 키를 버린다.

    실제로 `cloud_concerns`가 그 상태였다: 파이프라인이 만들고 `_result_payload`가 싣는데
    화면은 못 받았다. 키를 하나 더 만들 때마다 같은 사고가 나므로 목록끼리 대조한다.
    """
    from app.requirements.agent.graph import _ARTIFACT_KEYS

    missing = [k for k in _ARTIFACT_KEYS if k not in AnalyzeResponse.model_fields]
    assert not missing, f"응답 스키마에 없는 산출물 키: {missing}"


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
    out = _result_payload(result, "tid-r")

    assert out["resource_questions"] == questions
    assert AnalyzeResponse(**out).resource_questions == questions
