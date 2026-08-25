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
from app.requirements.common.state_contract import contract
from app.requirements.config import settings
from app.requirements.legacy.auto_clarify import *
from app.requirements.schemas import *

_EXPAND_REQUIREMENTS_SYSTEM = """You expand an initial software-product description into a
practical first set of concrete English requirement statements.

- Expand a short or broad product idea into the smallest coherent, testable first scope: the
  primary end-to-end user goals and only the basic administration needed to operate them.
- Do not add requirements to reach a quota.
- Do not add optional enhancements, localization, analytics, observability, legal content,
  customer-support tooling, or external integrations unless the user explicitly requests them.
- EasyDep supports English only. Never introduce multilingual or localization requirements.
- Keep an already concrete requirement within its stated scope; split it only when it contains
  independently testable needs.
- Set requirement granularity to one independently completed actor or business goal, not one UI
  action or CRUD verb. Keep a cohesive query-and-inspect flow together; keep the add, view,
  update, and remove actions for one temporary collection together; and keep a create, update,
  and delete lifecycle under one management responsibility.
- Keep an outcome or confirmation with the goal that causes it unless it has its own independent
  trigger. Every extra statement expands scope and is allowed only when it has a distinct trigger
  and acceptance outcome.
- Write one need per sentence using clear shall-style English.
- Do not invent numeric targets, implementation technology, cloud topology, protocols, or named
  external integrations.
- Final grounding audit before returning: every actor or role, delivery channel, named
  integration, technology choice, and quantitative constraint must be stated in the source idea.
  A necessary ordinary primary or operator role may be inferred solely to express the minimal
  product scope. Remove or rewrite every other unsupported detail. When the source requires a
  result or notification but gives no delivery mechanism, express only a provider-neutral, local
  system outcome rather than inventing a channel or integration.
- Do not classify the requirements as functional or non-functional.
- Return only the structured result.
"""


@contract(
    "expand_requirements",
    requires=("raw_requirements",),
    produces=("expanded_requirements", "expanded_source_refs"),
)
def expand_requirements(state: AgentState) -> dict:
    """Expand one terse product idea before traceable RR refinement.

    A multi-item input is already a requirement set.  Rewriting it here would
    silently change its scope before the RAW-to-RR provenance stage can inspect
    it, so it deliberately bypasses semantic expansion.
    """
    source = list(state.get("raw_requirements") or [])
    if len(source) != 1:
        return {
            "expanded_requirements": source,
            "expanded_source_refs": [[f"RAW{index}"] for index in range(1, len(source) + 1)],
            "phase": "expand_requirements",
        }

    listing = "\n".join(f"- {item}" for item in source)
    result: ExpandedRequirementsResult = invoke_structured(
        ExpandedRequirementsResult,
        [
            SystemMessage(content=_EXPAND_REQUIREMENTS_SYSTEM),
            HumanMessage(content=f"Initial requirements:\n{listing}"),
        ],
    )
    expanded = [item.strip() for item in result.requirements if item.strip()]
    working_set = expanded or source
    return {
        "expanded_requirements": working_set,
        # Expansion elaborates one user statement, so all derived requirements
        # retain the original source instead of inventing new RAW identities.
        "expanded_source_refs": [["RAW1"] for _ in working_set],
        "phase": "expand_requirements",
    }


@contract("intake", requires=("raw_requirements",), produces=("messages",))
def intake(state: AgentState) -> dict:
    """입력 요구사항 배열을 첫 사용자 메시지로 그래프에 넣는다."""
    reqs = state.get("expanded_requirements") or state.get("raw_requirements") or []
    listing = "\n".join(f"- {r}" for r in reqs)
    human = HumanMessage(content=f"Here are my requirements:\n{listing}")
    return {"messages": [human], "phase": "intake"}


# 구체화본이 없으면 원문으로 분류한다. 둘 다 없을 때만 상류가 안 돈 것이다.
@contract("classify", requires_any=(
    "requirement_drafts", "refined_requirements", "expanded_requirements", "raw_requirements",
),
          produces=("classified",))
def classify(state: AgentState, feedback: str = "") -> dict:
    """refined 요구사항을 파인튜닝 BERT로 FR/NFR 분류한다(BERT 단독).

    Each requirement keeps its RR identity while BERT independently assigns the
    FR/NFR label.  Raw analysis fails closed when BERT is unavailable: assigning
    every item FR would manufacture an authoritative-looking, false result.
    """
    drafts = list(state.get("requirement_drafts") or [])
    refined = [str(item.get("text") or "") for item in drafts]
    if not refined:
        refined = list(
            state.get("refined_requirements")
            or state.get("expanded_requirements")
            or state.get("raw_requirements")
            or []
        )

    if not bert_available():
        raise RuntimeError(
            "FR/NFR classification requires the BERT classifier. "
            "For an execution without BERT, provide preclassified requirements "
            "to `python -m app.requirements.run_pipeline` instead."
        )

    classified: list[RequirementItem] = []
    for index, text in enumerate(refined):
        verified = classify_bert(text)
        if verified is None:
            raise RuntimeError(
                "BERT became unavailable during FR/NFR classification; "
                "the analysis was stopped without inventing labels."
            )
        req_type: Literal["FR", "NFR"] = verified[0]
        requirement_id = (
            str(drafts[index].get("ref") or f"RR{index + 1}")
            if drafts
            else f"RR{index + 1}"
        )
        item: RequirementItem = {"id": requirement_id, "text": text, "type": req_type}
        if drafts:
            item["draft_ref"] = str(drafts[index].get("ref") or "")
            item["source_refs"] = list(drafts[index].get("sourceRefs") or [])
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
            child_sources = set(child.get("source_refs") or [])
            parent_sources = set(parent.get("source_refs") or [])
            # A clarify-time constraint link is evidence that one compound RAW
            # statement was split.  Separate sources cannot acquire a semantic
            # qualifies edge merely because the model found them related.
            if (child_sources or parent_sources) and not (
                child_sources & parent_sources
            ):
                continue
            child.setdefault("qualifies", [])
            if parent["id"] not in child["qualifies"]:
                child["qualifies"].append(parent["id"])


def _source_mapping(result: ClarifyOnlyResult, raw: list[str]) -> tuple[list[dict], list[str]]:
    """Validate RAW references and give refined requirements stable RR ids."""
    raw_ids = {f"RAW{index}" for index in range(1, len(raw) + 1)}
    issues: list[str] = []
    drafts: list[dict] = []
    covered: set[str] = set()
    for proposal_order, proposal in enumerate(result.requirement_drafts):
        refs = sorted(
            {ref for ref in proposal.source_refs if ref in raw_ids},
            key=lambda ref: int(ref[3:]),
        )
        invalid = sorted(set(proposal.source_refs) - raw_ids)
        if invalid or not refs:
            issues.append(
                f"refined requirement has invalid source refs "
                f"{invalid or proposal.source_refs}: {proposal.text}"
            )
        covered.update(refs)
        drafts.append({
            "text": proposal.text,
            "sourceRefs": refs,
            "_proposalOrder": proposal_order,
        })

    missing = sorted(raw_ids - covered, key=lambda ref: int(ref[3:]))
    if missing:
        issues.append(f"source requirements have no refined requirement: {', '.join(missing)}")

    drafts.sort(key=lambda item: (
        min((int(ref[3:]) for ref in item["sourceRefs"]), default=len(raw) + 1),
        item["_proposalOrder"],
    ))
    for index, item in enumerate(drafts, 1):
        item.pop("_proposalOrder", None)
        item["ref"] = f"RR{index}"
    return drafts, issues



@contract("clarify", requires=("raw_requirements", "messages"),
          produces=("refined_requirements", "constraint_links", "requirement_drafts"))
def clarify(state: AgentState) -> dict:
    """구체화 Few-Shot 예시가 포함된 프롬프트"""
    # Expansion produces a working set for refinement, but RAW identifiers always
    # refer to the immutable user input kept in ``raw_requirements``.
    raw = list(state.get("raw_requirements") or [])
    working_set = list(state.get("expanded_requirements") or raw)
    source_refs = list(state.get("expanded_source_refs") or [])
    if len(source_refs) != len(working_set):
        source_refs = [[f"RAW{index}"] for index in range(1, len(working_set) + 1)]

    query = "\n".join(working_set)
    system = refine_requirements_prompt(query, method=settings.example_sampling_method)
    raw_listing = "\n".join(
        f"{', '.join(refs)}: {text}"
        for text, refs in zip(working_set, source_refs, strict=True)
    )
    messages = [
        SystemMessage(content=system),
        HumanMessage(content=f"Source requirements:\n{raw_listing}"),
    ]
    result: ClarifyOnlyResult = invoke_structured(ClarifyOnlyResult, messages)

    # Phase 2(RTM): 분리된 제약↔기능 링크를 문장쌍으로 실어 보낸다(classify가 id로 해소).
    links = [{"constraint": c.constraint, "qualifies": c.qualifies}
             for c in result.constraint_links]
    drafts, issues = _source_mapping(result, raw)
    return {
        "refined_requirements": [item["text"] for item in drafts],
        "constraint_links": links,
        "requirement_drafts": drafts,
        "requirement_source_issues": issues,
        "phase": "clarify",
    }
