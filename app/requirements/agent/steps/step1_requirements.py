"""STEP 1 — 사용자 요구사항 구체화 및 분류.

노드 흐름:
  intake → clarify(few-shot 구체화) → classify(BERT 단독, id=FR1/NFR2)

- 분류: classify가 파인튜닝 BERT 단독으로 FR/NFR을 판정한다(LLM 분류 없음).
"""
from __future__ import annotations

from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage

from app.requirements.agent.llm import invoke_structured
from app.requirements.agent.state import AgentState, RequirementItem
from app.requirements.classifier import bert_available, classify_bert
from app.requirements.config import settings
from app.requirements.schemas import *
from app.requirements.legacy.auto_clarify import *


def intake(state: AgentState) -> dict:
    """입력 요구사항 배열을 첫 사용자 메시지로 그래프에 넣는다."""
    reqs = state.get("raw_requirements") or []
    listing = "\n".join(f"- {r}" for r in reqs)
    human = HumanMessage(content=f"Here are my requirements:\n{listing}")
    return {"messages": [human], "phase": "intake"}


def classify(state: AgentState, feedback: str = "") -> dict:
    """refined 요구사항을 파인튜닝 BERT로 FR/NFR 분류한다(BERT 단독).

    각 요구사항 문장을 BERT로 분류하고, 유형별 순번으로 id를 FR1/NFR2 형식으로 부여한다.
    분류는 결정론적이므로 feedback은 (게이트 시그니처 유지용으로만 받고) 결과에 영향 없다.
    BERT 사용 불가(enable_bert_verify=False/로드 실패) 시 기본값 FR로 강등한다.
    """
    refined = state.get("refined_requirements") or state.get("raw_requirements") or []
    available = bert_available()
    counters = {"FR": 0, "NFR": 0}
    classified: list[RequirementItem] = []
    for text in refined:
        req_type: Literal["FR", "NFR"] = "FR"
        if available:
            verified = classify_bert(text)
            if verified is not None and verified[0] == "NFR":
                req_type = "NFR"
        counters[req_type] += 1
        item: RequirementItem = {"id": f"{req_type}{counters[req_type]}", "text": text, "type": req_type}
        classified.append(item)

    _apply_constraint_links(classified, state.get("constraint_links") or [])
    return {"classified": classified, "phase": "classify"}


def _norm(s: str) -> str:
    """문장 비교용 정규화(공백 축약·소문자). clarify가 두 곳에 낸 같은 문장을 매칭하기 위함."""
    return " ".join((s or "").split()).lower()


def _apply_constraint_links(classified: list[RequirementItem], links: list[dict]) -> None:
    """clarify의 (제약↔기능) 문장쌍을 id로 해소해 NFR 항목의 qualifies에 부모 FR id를 채운다.

    BERT 분류를 존중한다 — child가 NFR이고 parent가 FR로 분류됐을 때만 링크한다(불일치 시 조용히 스킵).
    """
    if not links:
        return
    by_norm = {_norm(it["text"]): it for it in classified}
    for link in links:
        child = by_norm.get(_norm(link.get("constraint", "")))
        parent = by_norm.get(_norm(link.get("qualifies", "")))
        if child and parent and child["type"] == "NFR" and parent["type"] == "FR":
            child.setdefault("qualifies", [])
            if parent["id"] not in child["qualifies"]:
                child["qualifies"].append(parent["id"])



def clarify(state: AgentState) -> dict:
    """구체화 Few-Shot 예시가 포함된 프롬프트"""
    # few-shot 예시 선별 방법은 settings 로 전환(random | mmr+nim). mmr+nim 은
    # 원본 요구사항을 쿼리로 써야 관련 예시가 나오므로 raw_requirements 를 넘긴다.
    query = "\n".join(state.get("raw_requirements") or [])
    system = refine_requirements_prompt(query, method=settings.example_sampling_method)
    messages = [SystemMessage(content=system), *state["messages"]]
    result: ClarifyOnlyResult = invoke_structured(ClarifyOnlyResult, messages)

    # Phase 2(RTM): 분리된 제약↔기능 링크를 문장쌍으로 실어 보낸다(classify가 id로 해소).
    links = [{"constraint": c.constraint, "qualifies": c.qualifies}
             for c in result.constraint_links]
    return {"refined_requirements": result.refined_requirements,
            "constraint_links": links, "phase": "clarify"}