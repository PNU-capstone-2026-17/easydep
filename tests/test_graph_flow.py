"""그래프 STEP1 흐름 테스트 (LLM 구체화 목킹, BERT 목킹).

실제 컴파일된 그래프를 gates-on으로 돌려 intake→clarify→classify(BERT 단독)까지
실행하고, step1 말미 피드백 게이트에서 interrupt로 멈추는 지점의 상태를 검증한다.
(gates-off 는 step2~4 파이프라인까지 실행하므로 여기선 다루지 않는다.)
"""
import uuid

from app.requirements.agent import graph as g
from app.requirements.schemas import ClarifyOnlyResult, ExpandedRequirementsResult


def _tid():
    return f"test-{uuid.uuid4()}"


def _gate_graph(monkeypatch):
    # 모듈 전역 그래프는 불변으로 두고, gates-on 로컬 그래프를 새로 컴파일해 쓴다.
    monkeypatch.setattr(g.settings, "enable_feedback_gates", True)
    return g.build_graph()


def test_step1_classifies_via_bert_and_gates(scripted_llm, fake_bert, monkeypatch):
    gate_graph = _gate_graph(monkeypatch)
    # clarify 가 소비하는 단일 구체화 응답.
    scripted_llm([
        ExpandedRequirementsResult(requirements=[
            "Users can log in with email and password.",
            "The system shall respond within 2 seconds.",
        ]),
        ClarifyOnlyResult.model_validate({"requirementDrafts": [
            {
                "text": "Users can log in with email and password.",
                "sourceRefs": ["RAW1"],
            },
            {
                "text": "The system shall respond within 2 seconds.",
                "sourceRefs": ["RAW1"],
            },
        ]}),
    ])
    # BERT 단독 분류: "respond"는 NFR, 나머지는 FR.
    fake_bert(lambda text: ("NFR", 0.9) if "respond" in text else ("FR", 0.95))

    result = gate_graph.invoke(
        {"raw_requirements": ["I want a login feature that is fast."]},
        {"configurable": {"thread_id": _tid()}},
    )

    reqs = result["classified"]
    assert [r["id"] for r in reqs] == ["RR1", "RR2"]
    assert [r["type"] for r in reqs] == ["FR", "NFR"]
    assert reqs[0]["source_refs"] == ["RAW1"]


def test_gates_on_interrupts_for_feedback(scripted_llm, fake_bert, monkeypatch):
    gate_graph = _gate_graph(monkeypatch)
    scripted_llm([
        ExpandedRequirementsResult(requirements=["Users can log in."]),
        ClarifyOnlyResult.model_validate({
            "requirementDrafts": [{"text": "Users can log in.", "sourceRefs": ["RAW1"]}]
        }),
    ])
    fake_bert(lambda text: ("FR", 0.99))

    result = gate_graph.invoke(
        {"raw_requirements": ["Users can log in."]},
        {"configurable": {"thread_id": _tid()}},
    )
    interrupts = result.get("__interrupt__")

    assert interrupts, "gates on이면 step1 말미에서 피드백 게이트가 멈춰야 함"
    assert interrupts[0].value.get("status") == "need_feedback"
    assert interrupts[0].value.get("stage") == "requirements"
