"""STEP 2 — 액터 및 유스케이스 식별.

reconcile된 FR/NFR 목록에서 액터와 유스케이스를 *식별*한다(시나리오/확장은 step3 몫).
- FR만 유스케이스가 되고, NFR은 유스케이스에 제약(nfr_ids)으로 붙는다. (Cockburn)
- goal-leveling: subfunction FR은 상위 user-goal 유스케이스의 requirement_ids로 흡수하고,
  유스케이스 고도는 EBP(elementary business process) 기준으로 잡는다.
- 도출(LLM)은 휴리스틱이고, check_coverage가 FR 커버리지를 결정론적으로 점검한다.

RAG("Writing Effective Use Cases" PDF / PURE few-shot)는 _usecase_examples() seam으로
주입 예정이며, 현재 첫 구현은 프롬프트만 사용한다.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.requirements import prompts
from app.requirements.agent.llm import invoke_structured
from app.requirements.agent.state import ActorItem, AgentState, RequirementItem, UseCaseItem
from app.requirements.common import telemetry
from app.requirements.common.state_contract import contract
from app.requirements.config import settings
from app.requirements.schemas import ActorResult, UseCaseResult


def _split_fr_nfr(
    classified: list[RequirementItem],
) -> tuple[list[RequirementItem], list[RequirementItem]]:
    """분류 결과를 FR/NFR로 나눈다."""
    fr = [r for r in classified if r.get("type") == "FR"]
    nfr = [r for r in classified if r.get("type") == "NFR"]
    return fr, nfr


def _listing(items: list[RequirementItem]) -> str:
    return "\n".join(f"- {r['id']}: {r['text']}" for r in items)


def _usecase_examples() -> str:
    """few-shot 예제 주입 지점(RAG seam). 현재는 비어 있음.

    향후 use case PDF/PURE를 검색해 Cockburn 스타일 예제를 여기로 주입한다.
    """
    return ""


@contract("identify_actors", requires=("classified",))
def identify_actors(state: AgentState, feedback: str = "") -> dict:
    """FR에서 액터를 도출한다(중복 제거, primary/supporting 구분). feedback 시 재생성 지시."""
    classified = state.get("classified") or []
    fr, _ = _split_fr_nfr(classified)
    if not fr:
        return {"actors": [], "phase": "actors"}

    human = prompts.apply_user_feedback(f"Functional requirements:\n{_listing(fr)}", feedback)
    messages = [
        SystemMessage(content=prompts.ACTORS_SYSTEM),
        HumanMessage(content=human),
    ]
    result: ActorResult = invoke_structured(ActorResult, messages)

    actors: list[ActorItem] = [
        {"name": a.name, "description": a.description, "kind": a.kind,
         "parent_actor": a.parent_actor}
        for a in result.actors
    ]
    return {"actors": actors, "phase": "actors"}


def _uc_dict(uc, uid: str) -> UseCaseItem:
    """LLM UseCase → UseCaseItem(dict). id는 호출부가 지정(local 보존/broad 재부여)."""
    return {
        "id": uid,
        "name": uc.name,
        "primary_actor": uc.primary_actor,
        "level": uc.level,
        "goal": uc.goal,
        "requirement_ids": uc.requirement_ids,
        "nfr_ids": uc.nfr_ids,
    }


def _local_edit_use_cases(
    existing: list[UseCaseItem], base_human: str, target_ids: list[str], feedback: str
) -> dict:
    """대상 UC만 고치고 나머지는 보존한다. 모델엔 전체 목록을 주되 대상만 수정하라고 지시한다.

    반환 목록의 개수/순서가 기존과 같으면 기존 id를 위치로 보존한다(형제 UC 안정). 개수가 달라지면
    (모델이 UC를 추가/삭제) 안전하게 전체 UC id를 재부여한다.
    """
    target_set = {t.strip() for t in target_ids if t and t.strip()}
    current_listing = "\n".join(
        f"- {u['id']} \"{u['name']}\" [primary_actor: {u['primary_actor']}] "
        f"covers FR {u['requirement_ids']} NFR {u['nfr_ids']} — {u['goal']}"
        for u in existing
    )
    target_desc = ", ".join(
        f"{u['id']} ({u['name']})" for u in existing if u["id"] in target_set
    ) or ", ".join(sorted(target_set))
    human = prompts.usecase_local_edit(base_human, current_listing, target_desc, feedback)
    result: UseCaseResult = invoke_structured(
        UseCaseResult,
        [SystemMessage(content=prompts.USECASES_SYSTEM), HumanMessage(content=human)],
    )
    ucs = result.use_cases
    if len(ucs) == len(existing):
        # 개수 보존 → 위치로 기존 id 매핑(형제 UC id 안정).
        use_cases = [_uc_dict(uc, existing[i]["id"]) for i, uc in enumerate(ucs)]
    else:
        # 개수 변동 → 전체 재부여(회귀 방지).
        use_cases = [_uc_dict(uc, f"UC{i}") for i, uc in enumerate(ucs, start=1)]
    return {"use_cases": use_cases, "phase": "use_cases"}


@contract("identify_use_cases", requires=("classified", "actors"))
def identify_use_cases(
    state: AgentState, feedback: str = "", target_ids: list[str] | None = None
) -> dict:
    """FR+액터로 유스케이스를 user-goal(EBP) 고도로 도출한다.

    feedback: 재생성 지시(대상 UC 도출에 반영).
    target_ids: 주어지면 그 UC만 지시대로 고치고 나머지 UC(및 그 id)는 그대로 보존한다(local 피드백).
        비면 전체를 새로 도출한다(broad).
    """
    classified = state.get("classified") or []
    fr, nfr = _split_fr_nfr(classified)
    actors = state.get("actors") or []
    if not fr:
        return {"use_cases": [], "phase": "use_cases"}

    actor_listing = "\n".join(
        f"- {a['name']} ({a['kind']}): {a['description']}" for a in actors
    ) or "- (none identified)"
    human = (
        f"Actors:\n{actor_listing}\n\n"
        f"Functional requirements (candidates for use cases):\n{_listing(fr)}\n\n"
        f"Non-functional requirements (attach as constraints only):\n"
        f"{_listing(nfr) or '- (none)'}"
    )
    examples = _usecase_examples()
    if examples:
        human = f"{examples}\n\n{human}"

    existing = state.get("use_cases") or []
    if target_ids and existing:
        # local 피드백: 대상 UC만 고치고 형제 UC/그 id를 보존한다(전체 churn 방지).
        return _local_edit_use_cases(existing, human, target_ids, feedback)

    human = prompts.apply_user_feedback(human, feedback)

    result: UseCaseResult = invoke_structured(
        UseCaseResult,
        [SystemMessage(content=prompts.USECASES_SYSTEM), HumanMessage(content=human)],
    )
    ucs = result.use_cases

    # 커버리지 강제-수리: 고아 FR(어떤 UC에도 안 걸린 FR)이 있으면 그것만 담아 재프롬프트로
    # 목록을 보충한다(결정론적 집합 연산으로 판정, 최대 max_coverage_iters회). 요구 누락 차단.
    fr_ids = {r["id"] for r in fr}
    for _ in range(settings.max_coverage_iters):
        covered = {rid for uc in ucs for rid in uc.requirement_ids}
        orphans = sorted(fr_ids - covered)
        if not orphans:
            break
        orphan_listing = _listing([r for r in fr if r["id"] in set(orphans)])
        current = "\n".join(f"- {uc.name} covers {uc.requirement_ids}" for uc in ucs) or "- (none)"
        repair = prompts.usecase_coverage_repair(human, orphan_listing, current)
        try:
            result = invoke_structured(
                UseCaseResult,
                [SystemMessage(content=prompts.USECASES_SYSTEM), HumanMessage(content=repair)],
            )
        except Exception as exc:  # noqa: BLE001 - 재생성 실패 시 직전 목록 유지
            # 보충을 못 했으므로 고아 FR이 남은 채로 다음 단계로 간다. check_coverage가
            # 그 사실 자체는 결정론으로 계산하지만, 왜 못 메웠는지는 여기에만 있다.
            telemetry.record_degradation(
                "use_cases.coverage_repair",
                f"{type(exc).__name__}: {exc}",
                subject=",".join(orphans),
            )
            break
        # 보충이 오히려 커버리지를 줄이면 채택하지 않고 중단(회귀 방지).
        new_covered = {rid for uc in result.use_cases for rid in uc.requirement_ids}
        if len(new_covered & fr_ids) < len(covered & fr_ids):
            break
        ucs = result.use_cases

    use_cases: list[UseCaseItem] = [_uc_dict(uc, f"UC{i}") for i, uc in enumerate(ucs, start=1)]
    return {"use_cases": use_cases, "phase": "use_cases"}


@contract("check_coverage", requires=("classified", "use_cases"))
def check_coverage(state: AgentState) -> dict:
    """FR 커버리지를 결정론적으로 점검한다.

    유스케이스 *도출*은 LLM 휴리스틱이지만, 이 커버리지 점검은 집합 연산이라 확정적이다.
    - orphan FR: 어떤 유스케이스에도 매핑되지 않은 FR (누락 위험 표면화)
    - unattached NFR: 어떤 유스케이스에도 안 붙은 NFR (전역 제약 후보)
    - unknown refs: 제공되지 않은 id를 참조한 경우 (LLM 환각 표면화)
    """
    classified = state.get("classified") or []
    use_cases = state.get("use_cases") or []
    fr, nfr = _split_fr_nfr(classified)
    fr_ids = {r["id"] for r in fr}
    nfr_ids = {r["id"] for r in nfr}

    covered = {rid for uc in use_cases for rid in uc.get("requirement_ids", [])}
    attached_nfr = {nid for uc in use_cases for nid in uc.get("nfr_ids", [])}

    coverage = {
        "fr_total": len(fr_ids),
        "covered_fr_ids": sorted(fr_ids & covered),
        "orphan_fr_ids": sorted(fr_ids - covered),
        "unattached_nfr_ids": sorted(nfr_ids - attached_nfr),
        "unknown_requirement_refs": sorted(covered - fr_ids),
        "coverage_ratio": (
            round(len(fr_ids & covered) / len(fr_ids), 4) if fr_ids else 1.0
        ),
    }
    return {"coverage": coverage, "phase": "coverage"}
