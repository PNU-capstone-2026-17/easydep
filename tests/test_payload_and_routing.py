"""_result_payload 응답 변환 테스트."""
from types import SimpleNamespace

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
