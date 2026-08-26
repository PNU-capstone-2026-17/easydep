"""Derive source-grounded actors and user-goal use cases."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from app.core import traceability
from app.requirements import prompts
from app.requirements.agent import supervisor, validator
from app.requirements.agent.llm import invoke_structured
from app.requirements.agent.state import ActorItem, AgentState, RequirementItem, UseCaseItem
from app.requirements.common import telemetry
from app.requirements.common.state_contract import contract
from app.requirements.config import settings  # re-exported test/configuration seam
from app.requirements.knowledge import rules
from app.requirements.schemas import ActorResult, UseCase, UseCaseResult


class _MissingUseCaseCandidate(BaseModel):
    """One independently initiated goal absent from the fixed proposal."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    primary_actor: str = Field(min_length=1)
    supporting_actors: list[str] = Field(default_factory=list)
    goal: str = Field(min_length=1)


class _RequirementTraceSlice(BaseModel):
    """The complete RTM decision for exactly one accepted requirement."""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str = Field(min_length=1)
    use_case_names: list[str] = Field(default_factory=list)
    missing_use_case: _MissingUseCaseCandidate | None = None


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


def _trace_slice(
    requirement: RequirementItem,
    accepted_requirements: list[RequirementItem],
    raw_use_cases: list[UseCase],
) -> _RequirementTraceSlice:
    proposed = "\n".join(
        f"- {use_case.name} [primary: {use_case.primary_actor}; goal: {use_case.goal}]"
        for use_case in raw_use_cases
    ) or "- (none)"
    context = "\n".join(
        f"- {item['id']}: {item['text']}"
        for item in accepted_requirements
        if item["id"] != requirement["id"]
    ) or "- (none)"
    functional = requirement.get("type") == "FR"
    result = invoke_structured(
        _RequirementTraceSlice,
        [
            SystemMessage(content=(
                prompts.FUNCTIONAL_TRACE_SLICE_SYSTEM
                if functional
                else prompts.CONSTRAINT_TRACE_SLICE_SYSTEM
            )),
            HumanMessage(content=(
                f"[FIXED PROPOSED USE CASES]\n{proposed}\n\n"
                f"[{'FUNCTIONAL REQUIREMENT' if functional else 'NON-FUNCTIONAL CONSTRAINT'} "
                f"UNDER AUDIT]\n"
                f"- {requirement['id']}: {requirement['text']}\n\n"
                f"[OTHER ACCEPTED REQUIREMENTS — CONTEXT ONLY]\n{context}"
            )),
        ],
    )
    if not isinstance(result, _RequirementTraceSlice):
        raise TypeError(f"unexpected trace slice result {type(result).__name__}")
    if result.requirement_id != requirement["id"]:
        raise ValueError(
            f"trace slice returned {result.requirement_id!r} for {requirement['id']!r}"
        )
    return result


def _audit_requirement_traceability(
    functional_requirements: list[RequirementItem],
    constraints: list[RequirementItem],
    raw_use_cases: list[UseCase],
    functional_audit_ids: list[str],
) -> list[UseCase]:
    """Review each ambiguous FR and each NFR independently, then merge RTM edges."""
    accepted_requirements = functional_requirements + constraints
    by_id = {requirement["id"]: requirement for requirement in accepted_requirements}
    constraint_ids = [constraint["id"] for constraint in constraints]
    task_ids = functional_audit_ids + constraint_ids
    workers = max(1, min(4, len(task_ids)))
    decisions: dict[str, _RequirementTraceSlice] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                telemetry.bind_context(_trace_slice),
                by_id[requirement_id],
                accepted_requirements,
                raw_use_cases,
            ): requirement_id
            for requirement_id in task_ids
        }
        for future in as_completed(futures):
            requirement_id = futures[future]
            try:
                decisions[requirement_id] = future.result()
            except Exception as exc:  # noqa: BLE001 - preserve only this requirement's mapping
                telemetry.record_degradation(
                    "use_cases.traceability_slice",
                    f"{type(exc).__name__}: {exc}",
                    subject=requirement_id,
                )

    known_names = {_actor_key(use_case.name) for use_case in raw_use_cases}
    accepted: dict[str, _RequirementTraceSlice] = {}
    for requirement_id, decision in decisions.items():
        if requirement_id in constraint_ids and decision.missing_use_case is not None:
            telemetry.record_degradation(
                "use_cases.traceability_slice",
                "a non-functional constraint proposed a missing use case",
                subject=requirement_id,
            )
            continue
        target_keys = {_actor_key(name) for name in decision.use_case_names}
        unknown = sorted(target_keys - known_names)
        if unknown:
            telemetry.record_degradation(
                "use_cases.traceability_slice",
                f"unknown use-case names: {unknown}",
                subject=requirement_id,
            )
            continue
        accepted[requirement_id] = decision

    functional_ids = set(functional_audit_ids)
    nfr_ids_to_audit = set(constraint_ids)
    target_keys = {
        requirement_id: {_actor_key(name) for name in decision.use_case_names}
        for requirement_id, decision in accepted.items()
    }
    updated: list[UseCase] = []
    for use_case in raw_use_cases:
        requirement_ids = [
            requirement_id
            for requirement_id in use_case.requirement_ids
            if requirement_id not in accepted or requirement_id not in functional_ids
        ]
        nfr_ids = [
            requirement_id
            for requirement_id in use_case.nfr_ids
            if requirement_id not in accepted or requirement_id not in nfr_ids_to_audit
        ]
        use_case_key = _actor_key(use_case.name)
        for requirement_id in functional_audit_ids:
            if use_case_key in target_keys.get(requirement_id, set()):
                requirement_ids.append(requirement_id)
        for requirement_id in constraint_ids:
            if use_case_key in target_keys.get(requirement_id, set()):
                nfr_ids.append(requirement_id)
        updated.append(use_case.model_copy(update={
            "requirement_ids": list(dict.fromkeys(requirement_ids)),
            "nfr_ids": list(dict.fromkeys(nfr_ids)),
        }))

    existing_names = {_actor_key(use_case.name) for use_case in updated}
    for requirement_id in functional_audit_ids:
        decision = accepted.get(requirement_id)
        candidate = decision.missing_use_case if decision else None
        if candidate is None or _actor_key(candidate.name) in existing_names:
            continue
        updated.append(UseCase(
            name=candidate.name,
            primary_actor=candidate.primary_actor,
            supporting_actors=candidate.supporting_actors,
            goal=candidate.goal,
            requirement_ids=[requirement_id],
            nfr_ids=[],
        ))
        existing_names.add(_actor_key(candidate.name))
    return updated


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
            SystemMessage(content=prompts.USECASE_GOAL_SKELETON_SYSTEM),
            HumanMessage(content=prompts.apply_user_feedback(human, feedback)),
        ],
    )
    raw_use_cases = result.use_cases
    claim_counts = Counter(
        requirement_id
        for use_case in raw_use_cases
        for requirement_id in set(use_case.requirement_ids)
    )
    audit_ids = sorted(
        requirement["id"]
        for requirement in fr
        if claim_counts[requirement["id"]] != 1
    )
    if audit_ids or nfr:
        raw_use_cases = _audit_requirement_traceability(
            fr, nfr, raw_use_cases, audit_ids
        )
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
