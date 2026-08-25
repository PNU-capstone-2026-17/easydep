"""Derive source-grounded actors and user-goal use cases."""
from __future__ import annotations

from collections.abc import Callable

from langchain_core.messages import HumanMessage, SystemMessage

from app.core import traceability
from app.requirements import prompts
from app.requirements.agent import supervisor, validator
from app.requirements.agent.llm import invoke_structured
from app.requirements.agent.state import ActorItem, AgentState, RequirementItem, UseCaseItem
from app.requirements.common import telemetry
from app.requirements.common.state_contract import contract
from app.requirements.config import settings  # re-exported test/configuration seam
from app.requirements.knowledge import rules
from app.requirements.schemas import ActorResult, UseCaseResult


def _split_fr_nfr(
    classified: list[RequirementItem],
) -> tuple[list[RequirementItem], list[RequirementItem]]:
    fr = [requirement for requirement in classified if requirement.get("type") == "FR"]
    nfr = [requirement for requirement in classified if requirement.get("type") == "NFR"]
    return fr, nfr


def _listing(items: list[RequirementItem]) -> str:
    return "\n".join(f"- {item['id']}: {item['text']}" for item in items)


def _accepted_source_refs(values: list[str], accepted_ids: set[str]) -> list[str]:
    """Normalize actor provenance without allowing an unknown requirement reference."""
    return sorted({str(value).strip() for value in values if str(value).strip() in accepted_ids})


def _actor_key(value: str | None) -> str:
    return " ".join(str(value or "").split()).casefold()


def _canonical_actors(raw_actors, accepted_ids: set[str]) -> tuple[list[ActorItem], list[str]]:
    """Use one stable spelling for actor identities and parent references."""
    records: dict[str, dict] = {}
    dangling: list[str] = []
    for actor in raw_actors:
        name = " ".join(actor.name.split())
        key = _actor_key(name)
        if not key:
            dangling.append("blank actor name")
            continue
        if key not in records:
            source_refs = _accepted_source_refs(actor.source_refs, accepted_ids)
            records[key] = {
                "name": name,
                "description": actor.description,
                "parent_actor": actor.parent_actor,
                "source_refs": source_refs,
            }
            if not source_refs:
                dangling.append(name)
        else:
            records[key]["source_refs"] = sorted(
                set(records[key]["source_refs"])
                | set(_accepted_source_refs(actor.source_refs, accepted_ids))
            )

    actors: list[ActorItem] = []
    for key, record in records.items():
        if not record["source_refs"]:
            continue
        parent_key = _actor_key(record["parent_actor"])
        if parent_key == key:
            dangling.append(record["name"])
            parent = None
        elif parent_key and parent_key not in records:
            dangling.append(str(record["parent_actor"]))
            parent = None
        else:
            parent = records[parent_key]["name"] if parent_key else None
        actors.append({
            "name": record["name"],
            "description": record["description"],
            "parent_actor": parent,
            "source_refs": record["source_refs"],
        })
    return actors, dangling


def _canonical_use_cases(
    raw_use_cases,
    actors: list[ActorItem],
    functional_ids: set[str],
    constraint_ids: set[str],
):
    """Resolve use-case actor references without silently inventing an actor."""
    known = {_actor_key(actor["name"]): actor["name"] for actor in actors}
    use_cases = []
    dangling: list[str] = []
    for use_case in raw_use_cases:
        primary = known.get(_actor_key(use_case.primary_actor))
        if primary is None:
            dangling.append(use_case.primary_actor)
            continue
        supporting: list[str] = []
        for actor in use_case.supporting_actors:
            canonical = known.get(_actor_key(actor))
            if canonical is None:
                dangling.append(actor)
            elif canonical not in supporting:
                supporting.append(canonical)
        use_cases.append(use_case.model_copy(update={
            "primary_actor": primary,
            "supporting_actors": supporting,
            "requirement_ids": [
                ref for ref in dict.fromkeys(use_case.requirement_ids) if ref in functional_ids
            ],
            "nfr_ids": [ref for ref in dict.fromkeys(use_case.nfr_ids) if ref in constraint_ids],
        }))
    return use_cases, dangling


def _retry_dangling_actor_refs(
    *,
    schema,
    raw_items,
    canonicalize: Callable,
    repair_prompt: Callable[[list[str]], str],
    extract: Callable,
) -> list:
    """Allow exactly one identity-only correction before discarding invalid links."""
    canonical, dangling = canonicalize(raw_items)
    if not dangling:
        return canonical
    repaired = invoke_structured(
        schema,
        [
            SystemMessage(content=prompts.ACTORS_SYSTEM if schema is ActorResult else prompts.USECASES_SYSTEM),
            HumanMessage(content=repair_prompt(sorted(set(dangling)))),
        ],
    )
    return canonicalize(extract(repaired))[0]


def _actor_repair_prompt(base_human: str, raw_actors) -> Callable[[list[str]], str]:
    proposed = "\n".join(
        f"- {actor.name} [parent: {actor.parent_actor or 'none'}; sourceRefs: {actor.source_refs}]"
        for actor in raw_actors
    ) or "- (none)"

    def build(dangling: list[str]) -> str:
        return (
            f"{base_human}\n\n[ACTOR IDENTITY REPAIR]\n"
            "Return the same actor proposal, correcting only blank, duplicate, or dangling "
            "actor identities and parentActor references. Do not derive additional roles. "
            "Every sourceRefs entry must be an accepted requirement ID.\n\n"
            f"[CURRENT ACTORS]\n{proposed}\n\n[IDENTITIES TO CORRECT]\n{', '.join(dangling)}"
        )

    return build


def _use_case_repair_prompt(base_human: str, raw_use_cases, actors: list[ActorItem]) -> Callable[[list[str]], str]:
    proposed = "\n".join(
        f"- {use_case.name} [primary: {use_case.primary_actor}; "
        f"supporting: {', '.join(use_case.supporting_actors) or 'none'}]"
        for use_case in raw_use_cases
    ) or "- (none)"
    actor_names = ", ".join(actor["name"] for actor in actors) or "(none)"

    def build(dangling: list[str]) -> str:
        return (
            f"{base_human}\n\n[USE-CASE ACTOR IDENTITY REPAIR]\n"
            "Return the same use-case proposal, correcting only primaryActor and "
            "supportingActors references that do not name a listed actor. Do not derive, "
            "remove, or regroup use cases.\n\n"
            f"[CANONICAL ACTORS]\n{actor_names}\n\n[CURRENT USE CASES]\n{proposed}\n\n"
            f"[IDENTITIES TO CORRECT]\n{', '.join(dangling)}"
        )

    return build


def _audit_omitted_actor_goal(base_human: str, raw_use_cases, orphan_ids: list[str]):
    """One bounded semantic audit replaces coverage-driven regeneration."""
    proposed = "\n".join(
        f"- {use_case.name}: {use_case.goal} [FR: {use_case.requirement_ids}; "
        f"actors: {use_case.primary_actor}, {', '.join(use_case.supporting_actors) or 'none'}]"
        for use_case in raw_use_cases
    ) or "- (none)"
    audit = (
        f"{base_human}\n\n[OMITTED ACTOR-GOAL AUDIT]\n"
        "Review the proposal once for an explicitly stated, independently initiated actor "
        "goal that is absent from the proposed use cases. Return it unchanged unless such a "
        "goal is actually omitted; then add or correct only the use case needed for that goal. "
        "Do not add or alter a use case merely to improve numeric requirement coverage. Do not "
        "create include or derived use cases. Authentication stated only as a precondition "
        "remains a precondition, not fabricated behavior.\n\n"
        f"[PROPOSED USE CASES]\n{proposed}\n\n"
        f"[UNCLAIMED FUNCTIONAL REQUIREMENT IDS]\n{', '.join(orphan_ids)}"
    )
    try:
        result: UseCaseResult = invoke_structured(
            UseCaseResult,
            [SystemMessage(content=prompts.USECASES_SYSTEM), HumanMessage(content=audit)],
        )
    except Exception as exc:  # noqa: BLE001 - preserve a usable proposal when an audit fails
        telemetry.record_degradation(
            "use_cases.actor_goal_audit", f"{type(exc).__name__}: {exc}",
            subject=",".join(orphan_ids),
        )
        return raw_use_cases
    return result.use_cases


@contract("identify_actors", requires=("classified",), produces=("actors",))
def identify_actors(state: AgentState, feedback: str = "") -> dict:
    """Derive external roles from accepted role/domain facts and actor goals."""
    feedback = supervisor.feedback_for(state, "actors", feedback)
    classified = state.get("classified") or []
    if not classified:
        return {"actors": [], "phase": "actors"}

    human = prompts.apply_user_feedback(
        "Accepted requirements:\n"
        f"{_listing(classified)}\n\n"
        "Use a requirement as actor evidence only when it states an external role, a role "
        "specialization/domain fact, or an actor goal. Quality and deployment constraints alone "
        "do not create actors. Return sourceRefs containing only accepted requirement IDs.",
        feedback,
    )
    system = (
        f"{prompts.ACTORS_SYSTEM}\n\n"
        "Actor discovery may also use accepted structural role/domain statements, regardless "
        "of their FR/NFR label. Do not infer actors from ordinary quality or deployment constraints."
    )
    result: ActorResult = invoke_structured(
        ActorResult, [SystemMessage(content=system), HumanMessage(content=human)]
    )
    accepted_ids = {requirement["id"] for requirement in classified}
    actors = _retry_dangling_actor_refs(
        schema=ActorResult,
        raw_items=result.actors,
        canonicalize=lambda items: _canonical_actors(items, accepted_ids),
        repair_prompt=_actor_repair_prompt(human, result.actors),
        extract=lambda repaired: repaired.actors,
    )
    return {"actors": actors, "phase": "actors"}


def _uc_dict(use_case, uid: str) -> UseCaseItem:
    return {
        "id": uid,
        "name": use_case.name,
        "primary_actor": use_case.primary_actor,
        "supporting_actors": use_case.supporting_actors,
        "level": use_case.level,
        "goal": use_case.goal,
        "requirement_ids": use_case.requirement_ids,
        "nfr_ids": use_case.nfr_ids,
    }


def _local_edit_use_cases(
    existing: list[UseCaseItem],
    base_human: str,
    target_ids: list[str],
    feedback: str,
    actors: list[ActorItem],
    functional_ids: set[str],
    constraint_ids: set[str],
) -> dict:
    target_set = {target.strip() for target in target_ids if target and target.strip()}
    current_listing = "\n".join(
        f"- {use_case['id']} {use_case['name']} [primary actor: {use_case['primary_actor']}]"
        for use_case in existing
    )
    target_desc = ", ".join(
        f"{use_case['id']} ({use_case['name']})"
        for use_case in existing if use_case["id"] in target_set
    ) or ", ".join(sorted(target_set))
    human = prompts.usecase_local_edit(base_human, current_listing, target_desc, feedback)
    result: UseCaseResult = invoke_structured(
        UseCaseResult,
        [SystemMessage(content=prompts.USECASES_SYSTEM), HumanMessage(content=human)],
    )
    use_cases = _retry_dangling_actor_refs(
        schema=UseCaseResult,
        raw_items=result.use_cases,
        canonicalize=lambda items: _canonical_use_cases(
            items, actors, functional_ids, constraint_ids
        ),
        repair_prompt=_use_case_repair_prompt(human, result.use_cases, actors),
        extract=lambda repaired: repaired.use_cases,
    )
    ids = [item["id"] for item in existing] if len(use_cases) == len(existing) else [
        f"UC{index}" for index in range(1, len(use_cases) + 1)
    ]
    return {
        "use_cases": [_uc_dict(use_case, ids[index]) for index, use_case in enumerate(use_cases)],
        "phase": "use_cases",
    }


@contract("identify_use_cases", requires=("classified", "actors"), produces=("use_cases",))
def identify_use_cases(
    state: AgentState, feedback: str = "", target_ids: list[str] | None = None
) -> dict:
    """Derive FR-backed user-goal use cases and attach NFR constraints separately."""
    feedback = supervisor.feedback_for(state, "use_cases", feedback)
    classified = state.get("classified") or []
    fr, nfr = _split_fr_nfr(classified)
    actors = state.get("actors") or []
    if not fr:
        return {"use_cases": [], "phase": "use_cases"}

    actor_listing = "\n".join(
        f"- {actor['name']}: {actor['description']}" for actor in actors
    ) or "- (none identified)"
    human = (
        f"Actors:\n{actor_listing}\n\n"
        f"Functional requirements (candidates for use cases):\n{_listing(fr)}\n\n"
        f"Non-functional requirements (attach as constraints only):\n{_listing(nfr) or '- (none)'}"
    )
    existing = state.get("use_cases") or []
    if target_ids and existing:
        return _local_edit_use_cases(
            existing, human, target_ids, feedback, actors,
            {requirement["id"] for requirement in fr},
            {requirement["id"] for requirement in nfr},
        )

    result: UseCaseResult = invoke_structured(
        UseCaseResult,
        [
            SystemMessage(content=prompts.USECASES_SYSTEM),
            HumanMessage(content=prompts.apply_user_feedback(human, feedback)),
        ],
    )
    raw_use_cases = result.use_cases
    orphan_ids = sorted({requirement["id"] for requirement in fr} - {
        requirement_id for use_case in raw_use_cases for requirement_id in use_case.requirement_ids
    })
    if orphan_ids:
        raw_use_cases = _audit_omitted_actor_goal(human, raw_use_cases, orphan_ids)
    use_cases = _retry_dangling_actor_refs(
        schema=UseCaseResult,
        raw_items=raw_use_cases,
        canonicalize=lambda items: _canonical_use_cases(
            items,
            actors,
            {requirement["id"] for requirement in fr},
            {requirement["id"] for requirement in nfr},
        ),
        repair_prompt=_use_case_repair_prompt(human, raw_use_cases, actors),
        extract=lambda repaired: repaired.use_cases,
    )
    return {
        "use_cases": [_uc_dict(use_case, f"UC{index}") for index, use_case in enumerate(use_cases, 1)],
        "phase": "use_cases",
    }


@contract("review_model", requires=("actors", "use_cases"), produces=("model_review",))
def review_model(state: AgentState) -> dict:
    _ = settings.enable_semantic_validator
    payload = {
        "requirements": [
            {key: requirement.get(key) for key in ("id", "text", "type")}
            for requirement in (state.get("classified") or [])
        ],
        "deployment_needs": state.get("deployment_needs") or {},
        "actors": [
            {key: actor.get(key) for key in ("name", "description", "parent_actor", "source_refs")}
            for actor in (state.get("actors") or [])
        ],
        "use_cases": [
            {key: use_case.get(key) for key in (
                "name", "primary_actor", "supporting_actors", "level", "goal",
                "requirement_ids", "nfr_ids",
            )}
            for use_case in (state.get("use_cases") or [])
        ],
    }
    result = validator.review(
        rules.MODEL_USE_CASES, payload, prefix="model", source="use_cases.semantic_validator"
    )
    return {
        "model_review": {
            "issues": result.findings,
            "semantic_status": result.status,
            "unexamined_rules": list(result.unexamined),
        },
        "phase": "model_review",
    }


@contract("check_coverage", requires=("classified", "use_cases"), produces=("coverage",))
def check_coverage(state: AgentState) -> dict:
    """Keep the legacy claim gate while exposing step-backed effective coverage."""
    trace = traceability.index(state)
    coverage = {
        "fr_total": len(trace.fr_ids),
        "covered_fr_ids": list(trace.covered_fr_ids),
        "orphan_fr_ids": list(trace.orphan_fr_ids),
        "unattached_nfr_ids": list(trace.unattached_nfr_ids),
        "unknown_requirement_refs": list(trace.unknown_refs),
        "coverage_ratio": trace.coverage_ratio,
    }
    return {"coverage": coverage, "phase": "coverage"}
