"""STEP 3 — 유스케이스별 명세 proposal·검증·bounded repair stage다.

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

import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import NotRequired, TypedDict, cast

from langchain_core.messages import HumanMessage, SystemMessage

from app.requirements import prompts
from app.requirements.common.state_contract import contract
from app.requirements.config import settings
from app.requirements.contracts.state import (
    ActorItem,
    AgentState,
    RequirementItem,
    UseCaseItem,
    UseCaseSpecItem,
)
from app.requirements.knowledge import detectors, rules
from app.requirements.modeling import validation as validator
from app.requirements.modeling.contracts import (
    ModelingStagePatch,
    SemanticReviewCall,
    StructuredProposalCall,
)
from app.requirements.modeling.feedback import feedback_for
from app.requirements.runtime import telemetry
from app.requirements.runtime.structured_llm import invoke_structured
from app.requirements.schemas import UseCaseSpec
from app.requirements.traceability import constraints_for_use_case

# 마크다운/특수문자 → plain 정규화 매핑.
_REPLACEMENTS = {
    " ": " ", " ": " ",                    # narrow/no-break space
    "‑": "-", "–": "-", "—": "-",       # non-breaking/en/em dash
    "‘": "'", "’": "'", "“": '"', "”": '"',  # smart quotes
    "**": "", "__": "", "`": "",                       # bold/code 마크업
}

_LOCAL_REPAIR_LIMIT = 2
_RULE_TAG = re.compile(r"\[([a-z][a-z0-9_.-]*)\s")


class _NeighbourGoal(TypedDict):
    """같은 요구사항을 공유하지만 현재 명세 범위 밖인 목표다."""

    id: str
    name: str
    goal: str


class _SpecificationInput(UseCaseItem):
    """명세 생성 동안만 붙는 결정론적 문맥을 포함한 유스케이스다."""

    _neighboring_goals: NotRequired[list[_NeighbourGoal]]
    _constraint_requirements: NotRequired[list[dict[str, object]]]


def normalize_text(text: str) -> str:
    """문장에서 마크다운 마크업과 특수 공백/따옴표를 제거해 plain 텍스트로 만든다."""
    for src, dst in _REPLACEMENTS.items():
        text = text.replace(src, dst)
    return text.strip()


def _resolve(ids: list[str], by_id: dict[str, RequirementItem]) -> str:
    """요구 id 목록을 'id: text' 나열로 해석한다(없는 id는 조용히 건너뜀)."""
    lines = [f"- {i}: {by_id[i]['text']}" for i in ids if i in by_id]
    return "\n".join(lines) or "- (none)"


def validate_specification(spec: dict[str, object]) -> list[str]:
    """명세를 결정론적으로 점검한다(생성은 LLM 휴리스틱, 이 점검은 확정적).

    판정은 `knowledge/detectors.py`가 한다 — 규칙과 검출기가 지식베이스에 함께 있어야
    지적이 근거(규칙 id + 인용)를 들고 나간다. 여기서는 상태·리포트에 실릴 문자열로만 바꾼다.

    예전에는 정규식과 UI 단어 목록이 이 파일 상단에 있었고, "그 목록은 완전목록이 아니다"는
    사실이 **주석에만** 있었다. 그래서 지적을 받는 사람은 그 한계를 알 수 없었다.
    """
    return [f.as_issue() for f in detectors.spec_findings(spec)]


def _spec_human(
    uc: _SpecificationInput,
    by_id: dict[str, RequirementItem],
    actors: list[ActorItem],
    feedback: str = "",
) -> str:
    """명세 생성용 user 프롬프트(유스케이스 + FR/NFR). feedback 시 재생성 지시를 얹는다."""
    actor = next((a for a in actors if a.get("name") == uc.get("primary_actor")), None)
    actor_desc = f"{actor['name']} — {actor['description']}" if actor else uc.get("primary_actor", "")
    neighbouring_goals = uc.get("_neighboring_goals") or []
    scope = (
        f"Current goal boundary: implement ONLY {uc['name']} — {uc.get('goal', '')}."
    )
    if neighbouring_goals:
        listing = "\n".join(
            f"- {item['id']}: {item['name']} — {item.get('goal', '')}"
            for item in neighbouring_goals
        )
        scope += (
            "\nNeighbouring goals share source requirements but are OUT OF SCOPE. Do not "
            "include them as steps or extensions, and do not infer ordering, lifecycle state, "
            "or preconditions between them unless a requirement states it explicitly:\n"
            f"{listing}"
        )
    applicable_constraints = list(uc.get("_constraint_requirements") or [])
    constraint_listing = "\n".join(
        f"- {item.get('id')}: {item.get('text', '')}"
        for item in applicable_constraints
        if item.get("id")
    ) or "- (none)"
    base = (
        f"Use case: {uc['name']}\n"
        f"{scope}\n"
        f"Primary actor: {actor_desc}\n"
        f"Goal: {uc.get('goal', '')}\n\n"
        f"Functional requirements it covers:\n{_resolve(uc.get('requirement_ids', []), by_id)}\n\n"
        f"Non-functional constraints:\n{_resolve(uc.get('nfr_ids', []), by_id)}\n\n"
        "Applicable RTM constraints (refine this use case; they are not new goals or "
        f"scenario coverage):\n{constraint_listing}"
    )
    return prompts.apply_user_feedback(base, feedback)


def normalize_specification(spec: UseCaseSpec, uc: UseCaseItem) -> UseCaseSpecItem:
    """구조화 출력을 정리(_clean)해 상태 dict로 조립한다(issues는 이후 계산)."""
    return {
        "use_case_id": uc["id"],
        "name": uc["name"],
        "requirement_ids": list(uc["requirement_ids"]),
        "nfr_ids": list(uc["nfr_ids"]),
        "preconditions": [normalize_text(p) for p in spec.preconditions],
        "trigger": normalize_text(spec.trigger),
        "main_scenario": [
            {"step_number": s.step_number, "sentence": normalize_text(s.sentence),
             "covered_req_ids": s.covered_req_ids}
            for s in spec.main_scenario
        ],
        "extensions": [
            {"label": normalize_text(e.label), "branch_step": e.branch_step,
             "condition": normalize_text(e.condition),
             "handling_steps": [{"sub_step": h.sub_step, "sentence": normalize_text(h.sentence)}
                                for h in e.handling_steps],
             "outcome": e.outcome, "resume_at_step": e.resume_at_step}
            for e in spec.extensions
        ],
        "success_guarantee": [normalize_text(g) for g in spec.success_guarantee],
        "minimal_guarantee": [normalize_text(g) for g in spec.minimal_guarantee],
        "issues": [],
        "repair_iters": 0,
        # _check가 곧 덮어쓴다. 조립 시점에는 아직 아무 검증도 안 했다.
        "semantic_status": validator.PENDING,
    }


#: 검증자에게 보여줄 명세의 공개 필드 계약. 여기 없는 것은 검증자가 못 본다.
SPECIFICATION_REVIEW_FIELDS = (
    "trigger",
    "preconditions",
    "main_scenario",
    "extensions",
    "success_guarantee",
    "minimal_guarantee",
)

def spec_review_payload(
    item: dict[str, object],
    requirements: list[dict[str, object]] | None = None,
    goal_context: dict[str, object] | None = None,
    constraints: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """검증자가 받는 모양. **공개 함수인 이유는 평가가 같은 모양을 써야 하기 때문**이다.

    평가(`evaluation/`)가 이 모양을 따로 알고 있으면, 파이프라인이 보여주는 것과 눈금이
    재는 것이 조용히 달라진다 — 그러면 눈금 수치가 파이프라인에 대한 말이 아니게 된다.
    """
    payload: dict[str, object] = {
        key: item[key] for key in SPECIFICATION_REVIEW_FIELDS
    }
    if item.get("name"):
        payload["use_case_name"] = item["name"]
    if goal_context:
        payload.update(goal_context)
    payload["requirements_it_must_cover"] = requirements or []
    if constraints:
        payload["constraints_it_must_respect"] = constraints
    return payload


def _semantic_findings(
    item: UseCaseSpecItem,
    requirements: list[dict[str, object]] | None = None,
    goal_context: dict[str, object] | None = None,
    constraints: list[dict[str, object]] | None = None,
    review_call: SemanticReviewCall | None = None,
) -> tuple[list[str], str]:
    """정적 체크가 못 잡는 의미 결함을 독립 검증자에게 묻는다.

    판정은 `modeling/validation.py`가 한다. 여기서 만드는 payload가 **black-box 경계**다:

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
    payload = spec_review_payload(cast(dict[str, object], item), requirements, goal_context, constraints)
    reviewer = review_call or validator.review
    review = reviewer(
        rules.WRITE_SPECIFICATIONS,
        payload,
        prefix="semantic",
        source="spec.semantic_validator",
        subject=item.get("use_case_id"),
        confirm_violations=True,
    )
    # A partial verdict is not a clean semantic review. Keep the persisted shape small by
    # representing that existing condition with the existing unvalidated status instead of
    # adding another per-spec audit field.
    status = validator.UNGROUNDED if review.unexamined else review.status
    return review.findings, status


def requirement_view(
    uc: UseCaseItem, by_id: dict[str, RequirementItem]
) -> list[dict[str, object]]:
    """이 UC가 다뤄야 할 요구사항(id + 문장). 검증자가 scope creep을 판정할 잣대다."""
    ids = list(uc.get("requirement_ids", [])) + list(uc.get("nfr_ids", []))
    return [{"id": rid, "text": by_id[rid]["text"]} for rid in ids if rid in by_id]


def _check(
    item: UseCaseSpecItem,
    requirements: list[dict[str, object]] | None = None,
    goal_context: dict[str, object] | None = None,
    constraints: list[dict[str, object]] | None = None,
    review_call: SemanticReviewCall | None = None,
) -> tuple[list[str], str]:
    """정적(결정론) + 의미(LLM) 검증을 병합한 (issues, 의미검증 상태)."""
    static_findings = validate_specification(cast(dict[str, object], item))
    if static_findings:
        return static_findings, validator.PENDING
    findings, status = _semantic_findings(
        item, requirements, goal_context, constraints, review_call
    )
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


def generate_specification(
    uc: UseCaseItem,
    by_id: dict[str, RequirementItem],
    actors: list[ActorItem],
    feedback: str = "",
    *,
    proposal_call: StructuredProposalCall | None = None,
    review_call: SemanticReviewCall | None = None,
) -> UseCaseSpecItem:
    """명세를 생성하고, 검증 실패 시 지시를 붙여 재생성하는 반성 루프(스레드에서 호출).

    각 반복은 결정론 static + LLM semantic 검증을 병합해 issues를 만들고, 남으면 그 issues를
    지시로 붙여 재생성한다(최대 settings.max_repair_iters). feedback이 있으면 최초 생성에
    사용자 지시를 반영한다.

    **채택 규칙은 검증 단계가 전진했거나 결함이 줄었는가다.** 정적 무결성 결함을
    해소하면 그때까지 실행하지 않았던 의미 검증이 처음으로 드러날 수 있다. 두 결함의
    개수가 같더라도 이는 회귀가 아니라 정적 계약을 통과한 진전이므로 다음 수리 기회를
    준다. 같은 검증 단계 안에서 결함 수가 줄지 않으면 직전본을 최선으로 보고 멈춘다.

    멈춘 이유는 `repair_stopped`에 남긴다. 수술적(부분) 수정으로 바꿀 값어치가 있는지는
    이 값의 분포를 봐야 알 수 있고, 지금은 그 근거가 없다.
    A repair is accepted only when its stable issue-key set is a strict subset of
    the current set. A non-improving repair is discarded, but does not cancel the remaining local
    attempt because model sampling is non-deterministic and the configured budget is already
    bounded.
    """
    specification_input = cast(_SpecificationInput, uc)
    base_user = _spec_human(specification_input, by_id, actors, feedback)
    # 검증자에게 줄 잣대. 생성 프롬프트와 달리 **요구사항만** 담는다(지시는 담지 않는다).
    requirements = requirement_view(uc, by_id)
    constraints = list(specification_input.get("_constraint_requirements") or [])
    goal_context: dict[str, object] = {
        "use_case_goal": uc.get("goal", ""),
        "neighbouring_goals_sharing_requirements": (
            specification_input.get("_neighboring_goals") or []
        ),
    }
    propose = proposal_call or invoke_structured
    reviewer = review_call or validator.review

    def _generate(messages) -> UseCaseSpecItem:
        spec: UseCaseSpec = propose(UseCaseSpec, messages)
        item = normalize_specification(spec, uc)
        item["issues"], item["semantic_status"] = _check(
            item, requirements, goal_context, constraints, reviewer
        )
        return item

    # 명세 하나마다 **한 번** 조립한다(재생성에도 같은 것을 쓴다). 프롬프트가 재생성마다
    # 달라지면 반성 루프가 무엇을 고쳤는지 알 수 없다.
    system = prompts.generation_system_for(rules.WRITE_SPECIFICATIONS)

    item = _generate([SystemMessage(content=system), HumanMessage(content=base_user)])

    unresolved_keys = _issue_keys(item["issues"])
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
        current_static = validate_specification(cast(dict[str, object], item))
        candidate_static = validate_specification(cast(dict[str, object], candidate))
        advanced_to_semantic_review = bool(current_static) and not candidate_static
        if not advanced_to_semantic_review and not candidate_keys < unresolved_keys:
            # Keep the better previous item, but use any remaining bounded attempt.
            continue
        item = candidate
        unresolved_keys = candidate_keys
    else:
        # 예산을 다 쓰고 나왔다. 마지막 반복이 결함을 없앴을 수도 있다.
        stopped = "clean" if not item["issues"] else "budget"

    # 채택 횟수가 아니라 **시도 횟수**다. 채택 수를 세면 헛돈 재생성이 기록에서
    # 사라져서, 반성 루프가 비용을 얼마나 쓰는지 알 수 없게 된다.
    item["repair_iters"] = attempts
    item["repair_stopped"] = stopped
    return item


def _tracked_spec_for(
    uc: _SpecificationInput,
    by_id: dict[str, RequirementItem],
    actors: list[ActorItem],
    feedback: str = "",
    proposal_call: StructuredProposalCall | None = None,
    review_call: SemanticReviewCall | None = None,
) -> UseCaseSpecItem:
    """Generate one specification while exposing only its live task boundary."""
    fields = {"useCaseId": uc["id"], "useCaseName": uc.get("name", "")}
    telemetry.emit_progress("specTaskStarted", **fields)
    status = "completed"
    try:
        return generate_specification(
            uc,
            by_id,
            actors,
            feedback,
            proposal_call=proposal_call,
            review_call=review_call,
        )
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
    state: AgentState,
    feedback: str = "",
    target_ids: list[str] | None = None,
    *,
    proposal_call: StructuredProposalCall | None = None,
    review_call: SemanticReviewCall | None = None,
) -> ModelingStagePatch:
    """모든 유스케이스의 명세를 UC별 병렬로 생성한다(입력 순서로 취합).

    feedback: 재생성 지시(대상 UC 생성에 반영).
    target_ids: 주어지면 그 UC만 재생성하고 나머지는 기존 use_case_specs를 그대로 둔다(local 피드백).
    """
    feedback = feedback_for(dict(state), "specs", feedback)
    use_cases = state.get("use_cases") or []
    if not use_cases:
        return {"use_case_specs": [], "phase": "specs"}

    classified = state.get("classified") or []
    by_id: dict[str, RequirementItem] = {r["id"]: r for r in classified}
    actors = state.get("actors") or []

    existing = {s["use_case_id"]: s for s in (state.get("use_case_specs") or [])}
    target_set = set(target_ids) if target_ids else None
    to_gen: list[_SpecificationInput] = []
    for use_case in use_cases:
        if target_set is not None and use_case["id"] not in target_set:
            continue
        requirement_ids = set(use_case.get("requirement_ids") or [])
        neighbouring_goals = [
            {
                "id": other["id"],
                "name": other.get("name", ""),
                "goal": other.get("goal", ""),
            }
            for other in use_cases
            if other["id"] != use_case["id"]
            and requirement_ids.intersection(other.get("requirement_ids") or [])
        ]
        direct_ids = {
            str(value)
            for key in ("requirement_ids", "nfr_ids")
            for value in cast(list[str], use_case.get(key) or [])
        }
        applicable_constraints = [
            item
            for item in constraints_for_use_case(
                state.get("traceability") or {}, str(use_case["id"])
            )
            if str(item.get("id") or "") not in direct_ids
        ]
        to_gen.append(cast(_SpecificationInput, {
            **use_case,
            "_neighboring_goals": neighbouring_goals,
            "_constraint_requirements": applicable_constraints,
        }))

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
                    proposal_call,
                    review_call,
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
def check_specs(state: AgentState) -> ModelingStagePatch:
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
