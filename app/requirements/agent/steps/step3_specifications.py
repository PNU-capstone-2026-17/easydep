"""STEP 3 — 유스케이스별 명세 생성 (Cockburn 풀 템플릿, 병렬).

step2의 각 유스케이스에 대해 주 시나리오 + 확장(예외/대안) + 사전/사후조건을 생성한다.
유스케이스마다 LLM 호출 1건이 독립적이라 ThreadPoolExecutor로 동시 실행해 속도를 높인다
(동시 상한은 settings.spec_concurrency). invoke_structured가 호출마다 자체 ChatOpenAI를
만들므로 스레드 안전하다.

LLM 출력을 그대로 믿지 않고 검증·반성한다:
  - _clean: 문장의 마크다운/특수문자 정리(모델이 **굵게** 등을 섞어도 방어).
  - _validate_spec: 정적(결정론) 체크. 판정은 `knowledge/detectors.py`가 하고 여기서는
    지적을 문자열로 바꾼다 — 규칙과 검출기가 지식베이스에 함께 있어야 지적이 인용을 들고 나간다.
  - _semantic_findings: LLM 의미 검증(hidden branching·scope creep 등, 정적이 못 잡는 것).
    검증자가 댄 규칙 id를 지식베이스와 대조해, **없는 규칙을 인용한 지적은 버린다.**
  - _spec_for의 reflection 루프: 검증 실패 시 지시를 붙여 재생성(최대 max_repair_iters),
    회귀하면 직전본 유지.

규칙의 출처(책이 적었나 / 우리가 정했나)는 `knowledge/rules.py`에 있다. 책 본문은 저장소에
없다 — 저작물이라 지웠고(`d1a7ec5`), 담는 것은 우리 표현의 규범 문장과 인용 좌표다.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import cast

from langchain_core.messages import HumanMessage, SystemMessage

from app.requirements import prompts
from app.requirements.agent import supervisor, validator
from app.requirements.agent.llm import invoke_structured
from app.requirements.agent.state import AgentState, RequirementItem, UseCaseItem, UseCaseSpecItem
from app.requirements.common import telemetry
from app.requirements.common.state_contract import contract
from app.requirements.config import settings
from app.requirements.knowledge import detectors, rules
from app.requirements.schemas import UseCaseSpec

# 마크다운/특수문자 → plain 정규화 매핑.
_REPLACEMENTS = {
    " ": " ", " ": " ",                    # narrow/no-break space
    "‑": "-", "–": "-", "—": "-",       # non-breaking/en/em dash
    "‘": "'", "’": "'", "“": '"', "”": '"',  # smart quotes
    "**": "", "__": "", "`": "",                       # bold/code 마크업
}

_LOCAL_REPAIR_LIMIT = 2
_RULE_TAG = re.compile(r"\[([a-z][a-z0-9_.-]*)\s")


def _clean(text: str) -> str:
    """문장에서 마크다운 마크업과 특수 공백/따옴표를 제거해 plain 텍스트로 만든다."""
    for src, dst in _REPLACEMENTS.items():
        text = text.replace(src, dst)
    return text.strip()


def _resolve(ids: list[str], by_id: dict[str, RequirementItem]) -> str:
    """요구 id 목록을 'id: text' 나열로 해석한다(없는 id는 조용히 건너뜀)."""
    lines = [f"- {i}: {by_id[i]['text']}" for i in ids if i in by_id]
    return "\n".join(lines) or "- (none)"


def _validate_spec(spec: dict) -> list[str]:
    """명세를 결정론적으로 점검한다(생성은 LLM 휴리스틱, 이 점검은 확정적).

    판정은 `knowledge/detectors.py`가 한다 — 규칙과 검출기가 지식베이스에 함께 있어야
    지적이 근거(규칙 id + 인용)를 들고 나간다. 여기서는 상태·리포트에 실릴 문자열로만 바꾼다.

    예전에는 정규식과 UI 단어 목록이 이 파일 상단에 있었고, "그 목록은 완전목록이 아니다"는
    사실이 **주석에만** 있었다. 그래서 지적을 받는 사람은 그 한계를 알 수 없었다.
    """
    return [f.as_issue() for f in detectors.spec_findings(spec)]


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
        "requirement_ids": list(uc["requirement_ids"]),
        "nfr_ids": list(uc["nfr_ids"]),
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
        "semantic_status": validator.PENDING,
    }


#: 검증자에게 보여줄 명세의 칸들. 여기 없는 것은 검증자가 못 본다.
_REVIEWED_FIELDS = ("trigger", "preconditions", "main_scenario", "extensions",
                    "success_guarantee", "minimal_guarantee")


def spec_review_payload(item: dict, requirements: list[dict] | None = None) -> dict:
    """검증자가 받는 모양. **공개 함수인 이유는 평가가 같은 모양을 써야 하기 때문**이다.

    평가(`evaluation/`)가 이 모양을 따로 알고 있으면, 파이프라인이 보여주는 것과 눈금이
    재는 것이 조용히 달라진다 — 그러면 눈금 수치가 파이프라인에 대한 말이 아니게 된다.
    """
    payload: dict = {k: item[k] for k in _REVIEWED_FIELDS}
    payload["requirements_it_must_cover"] = requirements or []
    return payload


def _semantic_findings(
    item: UseCaseSpecItem, requirements: list[dict] | None = None
) -> tuple[list[str], str]:
    """정적 체크가 못 잡는 의미 결함을 독립 검증자에게 묻는다.

    판정은 `agent/validator.py`가 한다. 여기서 만드는 payload가 **black-box 경계**다:

      - 넣는다: 산출물(명세)과 그 명세가 다뤄야 할 **요구사항**. 요구사항은 판정의 대상이
        아니라 잣대다 — `spec.no-scope-creep`("주어진 요구에 없는 기능을 만들지 말라")은
        요구사항을 못 보면 **판정 자체가 불가능하다.** 2026-07-26까지 실제로 그랬다:
        규칙 목록에는 있는데 근거가 payload에 없어서, 검증자는 짐작으로 답할 수밖에 없었다.
        평가 세트의 의미 규칙 눈금(`evaluation/seeded.py`)을 만들다 드러났다.
      - 넣지 않는다: 생성 프롬프트·사용자 피드백·재생성 이력. 그걸 보여주면 검증자가
        "규칙을 지켰나" 대신 "지시를 따랐나"를 보게 된다.

    `(결함 목록, 검증 상태)`를 돌려준다. 상태를 함께 내는 이유는 **"결함 없음"과
    "확인하지 못함"이 같은 값이 되면 안 되기 때문**이다. 예전에는 검증기가 예외로
    죽어도 빈 리스트를 돌려줬고, 그러면 NIM이 내려간 동안 생성된 모든 명세가 조용히
    '깨끗함'으로 통과했다.
    """
    payload = spec_review_payload(item, requirements)
    result = validator.review(
        rules.WRITE_SPECIFICATIONS,
        payload,
        prefix="semantic",
        source="spec.semantic_validator",
        subject=item.get("use_case_id"),
    )
    return result.findings, result.status


def requirement_view(uc: UseCaseItem, by_id: dict[str, RequirementItem]) -> list[dict]:
    """이 UC가 다뤄야 할 요구사항(id + 문장). 검증자가 scope creep을 판정할 잣대다."""
    ids = list(uc.get("requirement_ids", [])) + list(uc.get("nfr_ids", []))
    return [{"id": rid, "text": by_id[rid]["text"]} for rid in ids if rid in by_id]


def _check(
    item: UseCaseSpecItem, requirements: list[dict] | None = None
) -> tuple[list[str], str]:
    """정적(결정론) + 의미(LLM) 검증을 병합한 (issues, 의미검증 상태)."""
    static_findings = _validate_spec(cast(dict, item))
    if static_findings:
        return static_findings, validator.PENDING
    findings, status = _semantic_findings(item, requirements)
    return findings, status


def _issue_keys(issues: list[str]) -> set[str]:
    """Return stable keys for deterministic findings and semantic rule verdicts."""
    keys: set[str] = set()
    known_rule_ids = rules.known_ids()
    for issue in issues:
        rule_id = next(
            (
                match.group(1)
                for match in reversed(list(_RULE_TAG.finditer(issue)))
                if match.group(1) in known_rule_ids
            ),
            None,
        )
        if rule_id is None:
            keys.add(f"raw:{issue}")
        else:
            finding = " ".join(issue.rsplit("[", 1)[0].split()).casefold()
            origin = "semantic" if issue.startswith("[semantic]") else "deterministic"
            keys.add(f"{origin}:{rule_id}:{finding}")
    return keys


def _issue_fingerprint(issue_keys: set[str]) -> str:
    payload = "\n".join(sorted(issue_keys)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
    A repair is accepted only when its stable issue-key set is a strict subset of
    the current set.  The loop stops when a previously seen key fingerprint recurs.
    """
    base_user = _spec_human(uc, by_id, actors, feedback)
    # 검증자에게 줄 잣대. 생성 프롬프트와 달리 **요구사항만** 담는다(지시는 담지 않는다).
    requirements = requirement_view(uc, by_id)

    def _generate(messages) -> UseCaseSpecItem:
        spec: UseCaseSpec = invoke_structured(UseCaseSpec, messages)
        item = _assemble(spec, uc)
        item["issues"], item["semantic_status"] = _check(item, requirements)
        return item

    # 명세 하나마다 **한 번** 조립한다(재생성에도 같은 것을 쓴다). 프롬프트가 재생성마다
    # 달라지면 반성 루프가 무엇을 고쳤는지 알 수 없다.
    system = prompts.generation_system_for(rules.WRITE_SPECIFICATIONS)

    item = _generate([SystemMessage(content=system), HumanMessage(content=base_user)])

    unresolved_keys = _issue_keys(item["issues"])
    fingerprints = [_issue_fingerprint(unresolved_keys)]
    attempts = 0
    stopped = "budget"
    for _ in range(min(_LOCAL_REPAIR_LIMIT, max(0, settings.max_repair_iters))):
        if not item["issues"]:
            stopped = "clean"
            break
        previous_spec = {
            key: item.get(key)
            for key in (
                "preconditions", "trigger", "main_scenario", "extensions",
                "success_guarantee", "minimal_guarantee",
            )
        }
        repair_user = prompts.spec_repair_user(
            base_user,
            json.dumps(previous_spec, ensure_ascii=False, indent=2),
            item["issues"],
        )
        attempts += 1
        try:
            candidate = _generate(
                [SystemMessage(content=system), HumanMessage(content=repair_user)]
            )
        except Exception as exc:  # noqa: BLE001 - 재생성 실패 시 직전본 유지
            # 수리를 못 했으므로 이 명세에는 검증이 지적한 결함이 그대로 남아 있다.
            telemetry.record_degradation(
                "spec.repair", f"{type(exc).__name__}: {exc}", subject=uc["id"]
            )
            stopped = "error"
            break
        candidate_keys = _issue_keys(candidate["issues"])
        candidate_fingerprint = _issue_fingerprint(candidate_keys)
        if candidate_fingerprint in fingerprints:
            stopped = "repeated_fingerprint"
            break
        if not candidate_keys < unresolved_keys:
            # A repair must remove keys without adding or replacing any finding.
            stopped = "no_improvement"
            break
        item = candidate
        unresolved_keys = candidate_keys
        fingerprints.append(candidate_fingerprint)
    else:
        # 예산을 다 쓰고 나왔다. 마지막 반복이 결함을 없앴을 수도 있다.
        stopped = "clean" if not item["issues"] else "budget"

    # 채택 횟수가 아니라 **시도 횟수**다. 채택 수를 세면 헛돈 재생성이 기록에서
    # 사라져서, 반성 루프가 비용을 얼마나 쓰는지 알 수 없게 된다.
    item["repair_iters"] = attempts
    item["repair_stopped"] = stopped
    return item


def _tracked_spec_for(
    uc: UseCaseItem,
    by_id: dict[str, RequirementItem],
    actors: list,
    feedback: str = "",
) -> UseCaseSpecItem:
    """Generate one specification while exposing only its live task boundary."""
    fields = {"useCaseId": uc["id"], "useCaseName": uc.get("name", "")}
    telemetry.emit_progress("specTaskStarted", **fields)
    status = "completed"
    try:
        return _spec_for(uc, by_id, actors, feedback)
    except BaseException:
        status = "failed"
        raise
    finally:
        telemetry.emit_progress("specTaskFinished", status=status, **fields)


def _failed_spec(uc: UseCaseItem, exc: BaseException) -> UseCaseSpecItem:
    """생성이 끝내 실패한 UC 자리를 채우는 빈 명세.

    이 UC를 목록에서 빼 버리면 산출물에서 조용히 사라진다 — 형제 명세가 멀쩡한 실행과
    구별되지 않는다. 자리는 남기되 "만들지 못했다"고 적어, 리포트와 저장된 아티팩트
    양쪽에서 보이게 한다.
    """
    return {
        "use_case_id": uc["id"],
        "name": uc.get("name", ""),
        "requirement_ids": list(uc["requirement_ids"]),
        "nfr_ids": list(uc["nfr_ids"]),
        "preconditions": [],
        "trigger": "",
        "main_scenario": [],
        "extensions": [],
        "success_guarantee": [],
        "minimal_guarantee": [],
        "issues": [f"[generation] Could not generate the specification: {type(exc).__name__}: {exc}"],
        "repair_iters": 0,
        "semantic_status": validator.FAILED,
        "repair_stopped": "not_generated",
        "generated": False,
    }


@contract("generate_specs", requires=("use_cases", "classified"),
          produces=("use_case_specs",))
def generate_specs(
    state: AgentState, feedback: str = "", target_ids: list[str] | None = None
) -> dict:
    """모든 유스케이스의 명세를 UC별 병렬로 생성한다(입력 순서로 취합).

    feedback: 재생성 지시(대상 UC 생성에 반영).
    target_ids: 주어지면 그 UC만 재생성하고 나머지는 기존 use_case_specs를 그대로 둔다(local 피드백).
    """
    feedback = supervisor.feedback_for(state, "specs", feedback)
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
                    telemetry.bind_context(_tracked_spec_for),
                    uc,
                    by_id,
                    actors,
                    feedback,
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


@contract("check_specs", requires=("use_case_specs",), produces=("spec_report",))
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
        # 원인이 달라도 결과는 같다 — 이 명세는 의미 검증을 **거치지 못했다.**
        # 어느 상태가 그에 해당하는지는 validator가 정한다(같은 목록을 두 번 적지 않는다).
        "unvalidated_ucs": [
            s["use_case_id"] for s in specs
            if s.get("semantic_status") in validator.UNVALIDATED
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
