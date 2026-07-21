"""STEP 3 — 유스케이스별 명세 생성 (Cockburn 풀 템플릿, 병렬).

step2의 각 유스케이스에 대해 주 시나리오 + 확장(예외/대안) + 사전/사후조건을 생성한다.
유스케이스마다 LLM 호출 1건이 독립적이라 ThreadPoolExecutor로 동시 실행해 속도를 높인다
(동시 상한은 settings.spec_concurrency). invoke_structured가 호출마다 자체 ChatOpenAI를
만들므로 스레드 안전하다.

LLM 출력을 그대로 믿지 않고 검증·반성한다:
  - _clean: 문장의 마크다운/특수문자 정리(모델이 **굵게** 등을 섞어도 방어).
  - _validate_spec: 정적(결정론) 체크 — 분기/복귀 참조, 무분기, 제어토큰, black-box UI 용어,
    계약 완결성.
  - _semantic_findings: LLM 의미 검증(hidden branching·scope creep 등, 정적이 못 잡는 것).
  - _spec_for의 reflection 루프: 검증 실패 시 지시를 붙여 재생성(최대 max_repair_iters),
    회귀하면 직전본 유지.

RAG("Writing Effective Use Cases" PDF)는 향후 SPEC 프롬프트에 few-shot으로 주입 예정.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import cast

from langchain_core.messages import HumanMessage, SystemMessage

from app.requirements import prompts
from app.requirements.agent.llm import invoke_structured
from app.requirements.agent.state import AgentState, RequirementItem, UseCaseItem, UseCaseSpecItem
from app.requirements.config import settings
from app.requirements.schemas import SpecCritique, UseCaseSpec

# 마크다운/특수문자 → plain 정규화 매핑.
_REPLACEMENTS = {
    " ": " ", " ": " ",                    # narrow/no-break space
    "‑": "-", "–": "-", "—": "-",       # non-breaking/en/em dash
    "‘": "'", "’": "'", "“": '"', "”": '"',  # smart quotes
    "**": "", "__": "", "`": "",                       # bold/code 마크업
}


def _clean(text: str) -> str:
    """문장에서 마크다운 마크업과 특수 공백/따옴표를 제거해 plain 텍스트로 만든다."""
    for src, dst in _REPLACEMENTS.items():
        text = text.replace(src, dst)
    return text.strip()


# Cockburn: MSS/확장은 무분기(Ch.7) → 명시적 if/else만 빠르게 걸러낸다(미묘한 분기는 LLM 몫).
_BRANCH = re.compile(r"\b(if|else)\b", re.IGNORECASE)
# 'Success!'/'Fail!'는 Cockburn의 시나리오 종결 토큰 → 프로즈가 아니라 outcome 필드로 표현.
_CONTROL_TOKEN = re.compile(r"(success!|fail!)", re.IGNORECASE)
# black-box lint: Cockburn Reminder 7(p.209)의 "나쁜 예"에 실제 등장하는 UI 용어만.
# ⚠ Cockburn은 금지 단어목록을 명문화하지 않았다(docs/research/cockburn-grounding.md C1).
# 따라서 이 목록은 "예시일 뿐 완전목록 아님"이며, 그가 예로 든 단어(screen/field/button/click/
# tab, p.209·p.91-92)로만 한정한다. page/menu/form 등은 그의 예시에 없어(오히려 form은 p.177에서
# 긍정적으로 등장) 제외한다. 나머지 내부컴포넌트 누출 판단은 임의 사전이 아니라 LLM Validator에 위임.
_UI_TERMS = ["screen", "field", "fields", "button", "click", "clicks", "clicked", "tab"]
_UI_PATTERNS = {w: re.compile(rf"\b{re.escape(w)}\b", re.IGNORECASE) for w in _UI_TERMS}


def _black_box_lint(text: str) -> list[str]:
    """문장에서 Cockburn 예시 UI 용어를 찾아 반환한다(단어 경계 매칭)."""
    return sorted(w for w, pat in _UI_PATTERNS.items() if pat.search(text))


def _resolve(ids: list[str], by_id: dict[str, RequirementItem]) -> str:
    """요구 id 목록을 'id: text' 나열로 해석한다(없는 id는 조용히 건너뜀)."""
    lines = [f"- {i}: {by_id[i]['text']}" for i in ids if i in by_id]
    return "\n".join(lines) or "- (none)"


def _validate_spec(spec: dict) -> list[str]:
    """명세를 결정론적으로 점검한다(생성은 LLM 휴리스틱, 이 점검은 확정적).

    (1) 확장 분기/복귀 참조 무결성, (2) 문장 정적 체크(무분기·제어토큰·black-box UI 용어),
    (3) 계약 완결성(precondition/success_guarantee). 위반은 issues 문자열로 반환.
    """
    main = spec.get("main_scenario", [])
    exts = spec.get("extensions", [])
    step_nums = {s["step_number"] for s in main}
    issues: list[str] = []

    # (1) 확장 분기/복귀 참조 무결성
    for ext in exts:
        label = ext.get("label") or "?"
        branch = ext.get("branch_step")
        if branch is not None and branch not in step_nums:
            issues.append(f"{label}: branch_step {branch}가 주 시나리오에 없음")
        outcome = ext.get("outcome")
        resume = ext.get("resume_at_step")
        if outcome == "resume":
            if resume is None:
                issues.append(f"{label}: outcome=resume인데 resume_at_step 없음")
            elif resume not in step_nums:
                issues.append(f"{label}: resume_at_step {resume}가 주 시나리오에 없음")
        elif resume is not None:
            issues.append(f"{label}: outcome={outcome}인데 resume_at_step이 설정됨")

    # (2) 문장 정적 체크 — trigger + MSS 스텝 + 확장 handling
    def _locations():
        yield "trigger", spec.get("trigger", "")
        for s in main:
            yield f"step {s['step_number']}", s["sentence"]
        for e in exts:
            for h in e.get("handling_steps", []):
                yield h["sub_step"], h["sentence"]

    for loc, sent in _locations():
        if _BRANCH.search(sent):
            issues.append(f"{loc}: 분기어(if/else) — 무분기여야 함(별도 확장으로 분리)")
        if _CONTROL_TOKEN.search(sent):
            issues.append(f"{loc}: 제어토큰(Success!/Fail!) — outcome 필드로 표현할 것")
        ui = _black_box_lint(sent)
        if ui:
            issues.append(f"{loc}: UI 용어 {ui} — black-box 위반")

    # (3) 계약 완결성
    if not spec.get("preconditions"):
        issues.append("preconditions 없음")
    if not spec.get("success_guarantee"):
        issues.append("success_guarantee 없음")
    return issues


def _spec_human(uc: UseCaseItem, by_id: dict[str, RequirementItem], actors: list, feedback: str = "") -> str:
    """명세 생성용 user 프롬프트(유스케이스 + FR/NFR). feedback 시 재생성 지시를 얹는다."""
    actor = next((a for a in actors if a.get("name") == uc.get("primary_actor")), None)
    actor_desc = f"{actor['name']} — {actor['description']}" if actor else uc.get("primary_actor", "")
    base = (
        f"Use case: {uc['name']}\n"
        f"Primary actor: {actor_desc}\n"
        f"Goal: {uc.get('goal', '')}\n\n"
        f"Functional requirements it covers:\n{_resolve(uc.get('requirement_ids', []), by_id)}\n\n"
        f"Non-functional constraints:\n{_resolve(uc.get('nfr_ids', []), by_id)}"
    )
    return prompts.apply_user_feedback(base, feedback)


def _assemble(spec: UseCaseSpec, uc: UseCaseItem) -> UseCaseSpecItem:
    """구조화 출력을 정리(_clean)해 상태 dict로 조립한다(issues는 이후 계산)."""
    return {
        "use_case_id": uc["id"],
        "name": uc["name"],
        "preconditions": [_clean(p) for p in spec.preconditions],
        "trigger": _clean(spec.trigger),
        "main_scenario": [
            {"step_number": s.step_number, "sentence": _clean(s.sentence),
             "covered_req_ids": s.covered_req_ids}
            for s in spec.main_scenario
        ],
        "extensions": [
            {"label": _clean(e.label), "branch_step": e.branch_step,
             "condition": _clean(e.condition),
             "handling_steps": [{"sub_step": h.sub_step, "sentence": _clean(h.sentence)}
                                for h in e.handling_steps],
             "outcome": e.outcome, "resume_at_step": e.resume_at_step}
            for e in spec.extensions
        ],
        "success_guarantee": [_clean(g) for g in spec.success_guarantee],
        "minimal_guarantee": [_clean(g) for g in spec.minimal_guarantee],
        "issues": [],
        "repair_iters": 0,
    }


def _semantic_findings(item: UseCaseSpecItem) -> list[str]:
    """정적 체크가 못 잡는 의미 결함을 LLM으로 검증(비활성/실패 시 빈 리스트)."""
    if not settings.enable_semantic_validator:
        return []
    payload = {k: item[k] for k in ("trigger", "preconditions", "main_scenario",
                                    "extensions", "success_guarantee")}
    try:
        critique: SpecCritique = invoke_structured(
            SpecCritique,
            [SystemMessage(content=prompts.SPEC_VALIDATOR_SYSTEM),
             HumanMessage(content=f"[USE CASE SPEC UNDER REVIEW]\n{json.dumps(payload, ensure_ascii=False)}")],
        )
    except Exception as exc:  # noqa: BLE001 - 검증 실패는 치명적이지 않음
        print(f"[agent] semantic validator 실패(무시): {exc}")
        return []
    return [] if critique.is_valid else [f"[semantic] {f}" for f in critique.findings]


def _check(item: UseCaseSpecItem) -> list[str]:
    """정적(결정론) + 의미(LLM) 검증을 병합한 issues."""
    return _validate_spec(cast(dict, item)) + _semantic_findings(item)


def _spec_for(
    uc: UseCaseItem,
    by_id: dict[str, RequirementItem],
    actors: list,
    feedback: str = "",
) -> UseCaseSpecItem:
    """명세를 생성하고, 검증 실패 시 수술적 지시로 재생성하는 반성 루프(스레드에서 호출).

    각 반복은 결정론 static + LLM semantic 검증을 병합해 issues를 만들고, 남으면 그 issues를
    지시로 붙여 재생성한다(최대 settings.max_repair_iters). 재생성이 더 나빠지거나 실패하면
    직전 정상본을 유지한다. feedback이 있으면 최초 생성에 사용자 지시를 반영한다.
    """
    base_user = _spec_human(uc, by_id, actors, feedback)

    def _generate(messages) -> UseCaseSpecItem:
        spec: UseCaseSpec = invoke_structured(UseCaseSpec, messages)
        item = _assemble(spec, uc)
        item["issues"] = _check(item)
        return item

    item = _generate([SystemMessage(content=prompts.SPEC_SYSTEM), HumanMessage(content=base_user)])

    for _ in range(settings.max_repair_iters):
        if not item["issues"]:
            break
        repair_user = prompts.spec_repair_user(base_user, item["issues"])
        try:
            candidate = _generate(
                [SystemMessage(content=prompts.SPEC_SYSTEM), HumanMessage(content=repair_user)]
            )
        except Exception as exc:  # noqa: BLE001 - 재생성 실패 시 직전본 유지
            print(f"[agent] spec 재생성 실패(직전본 유지): {exc}")
            break
        candidate["repair_iters"] = item["repair_iters"] + 1
        # 회귀 방지: 재생성이 이슈를 더 늘리면 채택하지 않고 중단.
        if len(candidate["issues"]) > len(item["issues"]):
            break
        item = candidate

    return item


def generate_specs(
    state: AgentState, feedback: str = "", target_ids: list[str] | None = None
) -> dict:
    """모든 유스케이스의 명세를 UC별 병렬로 생성한다(입력 순서로 취합).

    feedback: 재생성 지시(대상 UC 생성에 반영).
    target_ids: 주어지면 그 UC만 재생성하고 나머지는 기존 use_case_specs를 그대로 둔다(local 피드백).
    """
    use_cases = state.get("use_cases") or []
    if not use_cases:
        return {"use_case_specs": [], "phase": "specs"}

    classified = state.get("classified") or []
    by_id: dict[str, RequirementItem] = {r["id"]: r for r in classified}
    actors = state.get("actors") or []

    existing = {s["use_case_id"]: s for s in (state.get("use_case_specs") or [])}
    target_set = set(target_ids) if target_ids else None
    to_gen = [uc for uc in use_cases if target_set is None or uc["id"] in target_set]

    workers = max(1, min(len(to_gen), settings.spec_concurrency)) if to_gen else 1
    results: dict[str, UseCaseSpecItem] = {}
    if to_gen:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_spec_for, uc, by_id, actors, feedback): uc["id"] for uc in to_gen}
            for fut in as_completed(futures):
                results[futures[fut]] = fut.result()

    # use_cases 입력 순서 유지: 재생성분 우선, 아니면 기존 spec 유지(local 피드백 시 형제 보존).
    specs = [results.get(uc["id"]) or existing.get(uc["id"]) for uc in use_cases]
    specs = [s for s in specs if s is not None]
    return {"use_case_specs": specs, "phase": "specs"}


def check_specs(state: AgentState) -> dict:
    """생성된 명세의 검증 결과를 집계한다(결정론 요약 노드).

    generate_specs가 반성 루프로 이미 정적+의미 검증·수리했고, 이 노드는 그 결과(잔여 issues·
    repair 횟수)를 그래프에서 보이는 별도 단계로 집계·표면화한다(step2의 check_coverage와 대칭).
    """
    specs = state.get("use_case_specs") or []
    report = {
        "n_specs": len(specs),
        "total_issues": sum(len(s.get("issues", [])) for s in specs),
        "issues_by_uc": {s["use_case_id"]: s["issues"] for s in specs if s.get("issues")},
        "total_repair_iters": sum(s.get("repair_iters", 0) for s in specs),
    }
    return {"spec_report": report, "phase": "check_specs"}
