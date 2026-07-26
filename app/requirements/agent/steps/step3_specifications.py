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
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import cast

from langchain_core.messages import HumanMessage, SystemMessage

from app.requirements import prompts
from app.requirements.agent.llm import invoke_structured
from app.requirements.agent.state import AgentState, RequirementItem, UseCaseItem, UseCaseSpecItem
from app.requirements.common import telemetry
from app.requirements.common.state_contract import contract
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
        # _check가 곧 덮어쓴다. 조립 시점에는 아직 아무 검증도 안 했다.
        "semantic_status": "pending",
    }


def _semantic_findings(item: UseCaseSpecItem) -> tuple[list[str], str]:
    """정적 체크가 못 잡는 의미 결함을 LLM으로 검증한다.

    `(결함 목록, 검증 상태)`를 돌려준다. 상태를 함께 내는 이유는 **"결함 없음"과
    "확인하지 못함"이 같은 값이 되면 안 되기 때문**이다. 예전에는 검증기가 예외로
    죽어도 빈 리스트를 돌려줬고, 그러면 NIM이 내려간 동안 생성된 모든 명세가 조용히
    '깨끗함'으로 통과했다.

    상태: "ok"(검증함) | "disabled"(설정으로 끔) | "failed"(부르지 못함).
    """
    if not settings.enable_semantic_validator:
        return [], "disabled"
    payload = {k: item[k] for k in ("trigger", "preconditions", "main_scenario",
                                    "extensions", "success_guarantee")}
    try:
        critique: SpecCritique = invoke_structured(
            SpecCritique,
            [SystemMessage(content=prompts.SPEC_VALIDATOR_SYSTEM),
             HumanMessage(content=f"[USE CASE SPEC UNDER REVIEW]\n{json.dumps(payload, ensure_ascii=False)}")],
        )
    except Exception as exc:  # noqa: BLE001 - 검증 실패는 치명적이지 않음
        telemetry.record_degradation(
            "spec.semantic_validator",
            f"{type(exc).__name__}: {exc}",
            subject=item.get("use_case_id"),
        )
        return [], "failed"
    findings = [] if critique.is_valid else [f"[semantic] {f}" for f in critique.findings]
    return findings, "ok"


def _check(item: UseCaseSpecItem) -> tuple[list[str], str]:
    """정적(결정론) + 의미(LLM) 검증을 병합한 (issues, 의미검증 상태)."""
    findings, status = _semantic_findings(item)
    return _validate_spec(cast(dict, item)) + findings, status


def _spec_for(
    uc: UseCaseItem,
    by_id: dict[str, RequirementItem],
    actors: list,
    feedback: str = "",
) -> UseCaseSpecItem:
    """명세를 생성하고, 검증 실패 시 지시를 붙여 재생성하는 반성 루프(스레드에서 호출).

    각 반복은 결정론 static + LLM semantic 검증을 병합해 issues를 만들고, 남으면 그 issues를
    지시로 붙여 재생성한다(최대 settings.max_repair_iters). feedback이 있으면 최초 생성에
    사용자 지시를 반영한다.

    **채택 규칙은 "결함이 줄었는가"다.** 예전에는 개수가 늘어날 때만 거절했기 때문에,
    결함 3개가 다른 결함 3개로 바뀌어도 채택하고 다음 반복까지 돌았다 — 나아진 게 없는데
    예산만 태우고, 마지막에 남는 것이 최선본이라는 보장도 없었다. 이제 줄지 않으면
    직전본을 최선으로 보고 멈춘다.

    멈춘 이유는 `repair_stopped`에 남긴다. 수술적(부분) 수정으로 바꿀 값어치가 있는지는
    이 값의 분포를 봐야 알 수 있고, 지금은 그 근거가 없다.
    """
    base_user = _spec_human(uc, by_id, actors, feedback)

    def _generate(messages) -> UseCaseSpecItem:
        spec: UseCaseSpec = invoke_structured(UseCaseSpec, messages)
        item = _assemble(spec, uc)
        item["issues"], item["semantic_status"] = _check(item)
        return item

    item = _generate([SystemMessage(content=prompts.SPEC_SYSTEM), HumanMessage(content=base_user)])

    attempts = 0
    stopped = "budget"
    for _ in range(settings.max_repair_iters):
        if not item["issues"]:
            stopped = "clean"
            break
        repair_user = prompts.spec_repair_user(base_user, item["issues"])
        attempts += 1
        try:
            candidate = _generate(
                [SystemMessage(content=prompts.SPEC_SYSTEM), HumanMessage(content=repair_user)]
            )
        except Exception as exc:  # noqa: BLE001 - 재생성 실패 시 직전본 유지
            # 수리를 못 했으므로 이 명세에는 검증이 지적한 결함이 그대로 남아 있다.
            telemetry.record_degradation(
                "spec.repair", f"{type(exc).__name__}: {exc}", subject=uc["id"]
            )
            stopped = "error"
            break
        if len(candidate["issues"]) >= len(item["issues"]):
            # 줄지 않았다 — 같은 개수의 다른 결함으로 바뀐 것도 개선이 아니다.
            stopped = "no_improvement"
            break
        item = candidate
    else:
        # 예산을 다 쓰고 나왔다. 마지막 반복이 결함을 없앴을 수도 있다.
        stopped = "clean" if not item["issues"] else "budget"

    # 채택 횟수가 아니라 **시도 횟수**다. 채택 수를 세면 헛돈 재생성이 기록에서
    # 사라져서, 반성 루프가 비용을 얼마나 쓰는지 알 수 없게 된다.
    item["repair_iters"] = attempts
    item["repair_stopped"] = stopped
    return item


def _failed_spec(uc: UseCaseItem, exc: BaseException) -> UseCaseSpecItem:
    """생성이 끝내 실패한 UC 자리를 채우는 빈 명세.

    이 UC를 목록에서 빼 버리면 산출물에서 조용히 사라진다 — 형제 명세가 멀쩡한 실행과
    구별되지 않는다. 자리는 남기되 "만들지 못했다"고 적어, 리포트와 저장된 아티팩트
    양쪽에서 보이게 한다.
    """
    return {
        "use_case_id": uc["id"],
        "name": uc.get("name", ""),
        "preconditions": [],
        "trigger": "",
        "main_scenario": [],
        "extensions": [],
        "success_guarantee": [],
        "minimal_guarantee": [],
        "issues": [f"[generation] 명세를 생성하지 못했다: {type(exc).__name__}: {exc}"],
        "repair_iters": 0,
        "semantic_status": "failed",
        "repair_stopped": "not_generated",
        "generated": False,
    }


@contract("generate_specs", requires=("use_cases", "classified"))
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
            # bind_context로 감싸야 워커 스레드가 같은 실행에 계측을 집계한다 —
            # ThreadPoolExecutor는 contextvars를 복사해 주지 않는다. submit 마다 새로
            # 감싼다(Context 하나는 한 번만 실행할 수 있다).
            futures = {
                pool.submit(
                    telemetry.bind_context(_spec_for), uc, by_id, actors, feedback
                ): uc["id"]
                for uc in to_gen
            }
            by_id_uc = {uc["id"]: uc for uc in to_gen}
            for fut in as_completed(futures):
                uc_id = futures[fut]
                try:
                    results[uc_id] = fut.result()
                except Exception as exc:  # noqa: BLE001 - 형제를 살리려 여기서 흡수
                    # 예전에는 여기서 예외가 올라가 노드 전체가 실패했다. UC 10개 중
                    # 9개가 이미 끝났어도 그 9개까지 함께 버려졌다.
                    telemetry.record_degradation(
                        "spec.generate", f"{type(exc).__name__}: {exc}", subject=uc_id
                    )
                    results[uc_id] = _failed_spec(by_id_uc[uc_id], exc)

    # use_cases 입력 순서 유지: 재생성분 우선, 아니면 기존 spec 유지(local 피드백 시 형제 보존).
    specs = [results.get(uc["id"]) or existing.get(uc["id"]) for uc in use_cases]
    specs = [s for s in specs if s is not None]
    return {"use_case_specs": specs, "phase": "specs"}


@contract("check_specs", requires=("use_case_specs",))
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
        # 의미 검증을 못 거친 명세. issues가 비었다는 것과 "확인했는데 깨끗하다"는 것을
        # 리포트에서 구별할 수 있어야 한다 — 이 목록이 비어 있지 않으면 total_issues는
        # 하한일 뿐이다.
        "unvalidated_ucs": [
            s["use_case_id"] for s in specs if s.get("semantic_status") == "failed"
        ],
        # 생성 자체가 실패해 빈 자리로 남은 UC. 형제는 살아 있으므로 실행은 계속되지만
        # 이 UC의 명세는 없다 — 있는 척하지 않는다.
        "failed_ucs": [
            s["use_case_id"] for s in specs if s.get("generated") is False
        ],
        # 반성 루프가 왜 멈췄는지의 분포. "no_improvement"가 많으면 재생성이 헛돌고
        # 있다는 뜻이라, 전체 재생성 대신 부분 수정으로 바꿀 근거가 된다.
        "repair_stopped": dict(
            Counter(s.get("repair_stopped", "unknown") for s in specs)
        ),
    }
    return {"spec_report": report, "phase": "check_specs"}
