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
        "phase": "diagram", "classified": [req],
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
    interrupt_obj = SimpleNamespace(value={
        "stage": "specs", "status": "need_feedback", "prompt": "p", "summary": ["UC1"],
        "edit_stage": "specs", "edit_targets": ["UC1", "UC2"],
    })
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

    assert seen["answer"] is edit        # 문자열로 뭉개지 않고 그대로 넘어간다
    assert seen["thread_id"] == "t-9"
    assert seen["persist"] is True       # 서빙 경로는 세션을 DB에 남긴다
