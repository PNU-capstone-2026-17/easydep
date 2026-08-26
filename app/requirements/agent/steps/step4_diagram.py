"""Step 4: project canonical relations and render the use-case diagram.

This is the single relationship-identification stage. It bounds ``include``
candidates with Step 3 RTM coverage and bounds ``extend`` to existing use cases
and existing base-scenario steps; the model only makes semantic choices inside
those spaces. Actor relations are canonical upstream facts.
"""
from __future__ import annotations

import hashlib
import json
import re
import textwrap
from collections import defaultdict
from typing import Any, cast

from langchain_core.messages import HumanMessage, SystemMessage

from app.requirements import prompts
from app.requirements.agent import supervisor, validator
from app.requirements.agent.llm import invoke_structured
from app.requirements.agent.state import AgentState
from app.requirements.common.state_contract import contract
from app.requirements.knowledge import rules
from app.requirements.schemas import RelationshipModel


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def _prompt_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _accepted_specs_by_id(
    state: AgentState, accepted_ids: set[str]
) -> tuple[dict[str, dict], list[dict]]:
    """Index accepted specifications by stable use-case ID."""
    indexed: dict[str, dict] = {}
    rejections: list[dict] = []
    for raw_spec in state.get("use_case_specs") or []:
        spec = dict(raw_spec)
        use_case_id = str(spec.get("use_case_id") or "")
        if use_case_id not in accepted_ids:
            continue
        if (
            spec.get("generated") is False
            or spec.get("issues")
            or spec.get("semantic_status") != validator.OK
        ):
            rejections.append(
                {
                    "kind": "specification",
                    "use_case_id": use_case_id,
                    "step_ref": "specification",
                    "reason": "unaccepted use-case specification",
                }
            )
            continue
        indexed.setdefault(use_case_id, spec)
    return indexed, rejections


def _include_candidates(
    state: AgentState,
    use_cases: list[dict],
    specs_by_id: dict[str, dict],
) -> list[dict]:
    """Bound include discovery by RTM-backed shared behavior; semantics choose reuse."""
    accepted_fr_ids = {
        str(item["id"])
        for item in state.get("classified") or []
        if item.get("id") and item.get("type") == "FR"
    }
    refs_by_requirement: dict[str, list[dict]] = defaultdict(list)
    for use_case in use_cases:
        if use_case.get("level") == "subfunction":
            continue
        use_case_id = str(use_case["id"])
        spec = specs_by_id.get(use_case_id)
        if spec is None:
            continue
        mapped_requirements = {
            str(value) for value in use_case.get("requirement_ids") or []
        }
        for step in spec.get("main_scenario") or []:
            number = step.get("step_number")
            if not isinstance(number, int):
                continue
            for requirement_id in step.get("covered_req_ids") or []:
                requirement_id = str(requirement_id)
                if (
                    requirement_id not in accepted_fr_ids
                    or requirement_id not in mapped_requirements
                ):
                    continue
                refs_by_requirement[requirement_id].append(
                    {
                        "use_case_id": use_case_id,
                        "step_ref": f"main:{number}",
                        "sentence": _clean_text(step.get("sentence")),
                    }
                )

    candidates: list[dict] = []
    for requirement_id, refs in refs_by_requirement.items():
        participant_ids = list(dict.fromkeys(str(ref["use_case_id"]) for ref in refs))
        if len(participant_ids) < 2:
            continue
        identity = {
            "kind": "include",
            "requirement_id": requirement_id,
            "step_refs": [
                {"use_case_id": ref["use_case_id"], "step_ref": ref["step_ref"]}
                for ref in refs
            ],
        }
        candidate_id = _stable_id("rel-include", identity)
        candidates.append(
            {
                "candidate_id": candidate_id,
                "requirement_id": requirement_id,
                "participating_use_case_ids": participant_ids,
                "step_refs": refs,
                "derived_use_case_id": (
                    "UC_INC_" + candidate_id.rsplit("-", 1)[1].upper()
                ),
            }
        )
    return candidates


def _ancestor_names(actor: str, parent_by_child: dict[str, str]) -> set[str]:
    ancestors: set[str] = set()
    current = parent_by_child.get(actor)
    while current and current not in ancestors:
        ancestors.add(current)
        current = parent_by_child.get(current)
    return ancestors


def _actor_projection(
    use_cases: list[dict], actors: list[dict]
) -> tuple[list[dict], list[dict], list[str], list[str]]:
    """Project canonical actor participation and specialization facts."""
    actor_names = {str(actor.get("name") or "") for actor in actors}
    parent_by_child = {
        str(actor["name"]): str(actor["parent_actor"])
        for actor in actors
        if actor.get("name") and actor.get("parent_actor") in actor_names
    }
    generalizations = [
        {"parent": parent, "child": child, "kind": "actor"}
        for child, parent in parent_by_child.items()
    ]

    declared: list[tuple[str, str]] = []
    dropped: list[str] = []
    for use_case in use_cases:
        use_case_id = str(use_case["id"])
        participants = [
            use_case.get("primary_actor"),
            *(use_case.get("supporting_actors") or []),
        ]
        for actor_value in participants:
            actor_name = str(actor_value or "")
            if not actor_name:
                continue
            if actor_name not in actor_names:
                dropped.append(f"unknown actor {actor_name} for use case {use_case_id}")
                continue
            pair = (actor_name, use_case_id)
            if pair not in declared:
                declared.append(pair)

    declared_set = set(declared)
    associations = [
        {"actor": actor_name, "use_case_id": use_case_id}
        for actor_name, use_case_id in declared
        if not any(
            (ancestor, use_case_id) in declared_set
            for ancestor in _ancestor_names(actor_name, parent_by_child)
        )
    ]
    associated = {actor for actor, _ in declared}
    for actor_name, _ in declared:
        associated.update(
            child
            for child in parent_by_child
            if actor_name in _ancestor_names(child, parent_by_child)
        )
    orphan_actors = [
        str(actor["name"])
        for actor in actors
        if str(actor["name"]) not in associated
    ]
    return associations, generalizations, dropped, orphan_actors


def _relationship_prompt(
    state: AgentState,
    use_cases: list[dict],
    specs_by_id: dict[str, dict],
    include_candidates: list[dict],
    feedback: str,
) -> str:
    """Serialize compact evidence; relationship semantics live in the system prompt."""
    requirement_text = {
        str(item.get("id")): _clean_text(item.get("text"))
        for item in state.get("classified") or []
        if item.get("id")
    }
    bounded_artifact = []
    for use_case in use_cases:
        use_case_id = str(use_case["id"])
        spec = specs_by_id.get(use_case_id)
        if spec is None:
            continue
        bounded_artifact.append(
            {
                "use_case_id": use_case_id,
                "name": str(use_case.get("name") or ""),
                "goal": str(use_case.get("goal") or ""),
                "primary_actor": str(use_case.get("primary_actor") or ""),
                "requirement_ids": [
                    str(requirement_id)
                    for requirement_id in use_case.get("requirement_ids") or []
                ],
                "preconditions": spec.get("preconditions") or [],
                "trigger": spec.get("trigger") or "",
                "success_guarantee": spec.get("success_guarantee") or [],
                "main_scenario_steps": [
                    {
                        "step_ref": f"main:{step.get('step_number')}",
                        "sentence": _clean_text(step.get("sentence")),
                    }
                    for step in spec.get("main_scenario") or []
                    if isinstance(step.get("step_number"), int)
                ],
            }
        )
    prompt_candidates = [
        {
            "candidate_id": candidate["candidate_id"],
            "requirement_id": candidate["requirement_id"],
            "step_refs": candidate["step_refs"],
        }
        for candidate in include_candidates
    ]
    prompt = (
        f"Requirements by ID:\n{_prompt_json(requirement_text)}\n\n"
        f"Include candidates:\n{_prompt_json(prompt_candidates)}\n\n"
        f"Use cases:\n{_prompt_json(bounded_artifact)}"
    )
    return prompts.apply_user_feedback(prompt, feedback)


def _relationship_review_payload(
    requirements: list[dict],
    actors: list[dict],
    use_cases: list[dict],
    specs_by_id: dict[str, dict],
    relationships: dict[str, Any],
) -> dict[str, Any]:
    """Expose only the accepted evidence and the materialized relationship result."""
    return {
        "requirements": [
            {
                "id": str(requirement.get("id") or ""),
                "text": str(requirement.get("text") or ""),
                "type": str(requirement.get("type") or ""),
            }
            for requirement in requirements
            if requirement.get("id")
        ],
        "actors": [
            {
                "name": str(actor.get("name") or ""),
                "description": str(actor.get("description") or ""),
                "parent_actor": actor.get("parent_actor"),
            }
            for actor in actors
            if actor.get("name")
        ],
        "use_cases": [
            {
                "id": str(use_case["id"]),
                "name": str(use_case.get("name") or ""),
                "goal": str(use_case.get("goal") or ""),
                "primary_actor": str(use_case.get("primary_actor") or ""),
                "supporting_actors": list(use_case.get("supporting_actors") or []),
                "requirement_ids": list(use_case.get("requirement_ids") or []),
                "preconditions": list(
                    (specs_by_id.get(str(use_case["id"])) or {}).get("preconditions") or []
                ),
                "trigger": str(
                    (specs_by_id.get(str(use_case["id"])) or {}).get("trigger") or ""
                ),
                "main_scenario": list(
                    (specs_by_id.get(str(use_case["id"])) or {}).get("main_scenario") or []
                ),
                "extensions": list(
                    (specs_by_id.get(str(use_case["id"])) or {}).get("extensions") or []
                ),
                "success_guarantee": list(
                    (specs_by_id.get(str(use_case["id"])) or {}).get("success_guarantee")
                    or []
                ),
            }
            for use_case in use_cases
            if str(use_case["id"]) in specs_by_id
        ],
        "relationships": {
            key: relationships.get(key, [])
            for key in (
                "associations",
                "includes",
                "extends",
                "generalizations",
                "derived_use_cases",
            )
        },
    }


def _normalize_step_ref(value: object) -> str | None:
    match = re.fullmatch(r"(?:main:|step\s*)?(\d+)", _clean_text(value), re.IGNORECASE)
    if not match:
        return None
    return f"main:{int(match.group(1))}"


def _materialize_includes(
    model: RelationshipModel,
    candidates: list[dict],
    use_cases: list[dict],
) -> tuple[list[dict], list[dict], list[str]]:
    """Materialize only one unambiguous model decision per bounded candidate."""
    candidates_by_id = {str(item["candidate_id"]): item for item in candidates}
    names_by_id = {str(item["id"]): str(item.get("name") or "") for item in use_cases}
    decisions_by_id: dict[str, list] = defaultdict(list)
    dropped: list[str] = []
    for selection in model.includes:
        if selection.candidate_id not in candidates_by_id:
            dropped.append(f"unknown relationship candidate {selection.candidate_id}")
            continue
        decisions_by_id[selection.candidate_id].append(selection)

    includes: list[dict] = []
    derived: list[dict] = []
    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        selections = decisions_by_id.get(candidate_id, [])
        if len(selections) != 1:
            if len(selections) > 1:
                dropped.append(f"ambiguous relationship candidate decision {candidate_id}")
            continue
        selection = selections[0]
        name = _clean_text(selection.included_use_case_name)
        if selection.decision != "approve":
            continue
        if not name:
            dropped.append(f"approved include candidate {candidate_id} has no use-case name")
            continue
        derived_id = str(candidate["derived_use_case_id"])
        derived.append(
            {
                "use_case_id": derived_id,
                "name": name,
                "origin": "factored_include",
                "step_refs": candidate["step_refs"],
                "requirement_ids": [candidate["requirement_id"]],
            }
        )
        for base_id in candidate["participating_use_case_ids"]:
            includes.append(
                {
                    "base_use_case_id": base_id,
                    "included_use_case_id": derived_id,
                    "base_use_case": names_by_id[base_id],
                    "included_use_case": name,
                    "step_refs": candidate["step_refs"],
                    "requirement_ids": [candidate["requirement_id"]],
                    "requirement_refs": [
                        {
                            "use_case_id": ref["use_case_id"],
                            "step_ref": ref["step_ref"],
                            "requirement_id": candidate["requirement_id"],
                        }
                        for ref in candidate["step_refs"]
                    ],
                }
            )
    return includes, derived, dropped


def _materialize_extends(
    model: RelationshipModel,
    use_cases: list[dict],
    specs_by_id: dict[str, dict],
) -> tuple[list[dict], list[str]]:
    names_by_id = {str(item["id"]): str(item.get("name") or "") for item in use_cases}
    requirements_by_id = {
        str(item["id"]): [str(value) for value in item.get("requirement_ids") or []]
        for item in use_cases
    }
    valid_steps = {
        use_case_id: {
            f"main:{step.get('step_number')}": _clean_text(step.get("sentence"))
            for step in spec.get("main_scenario") or []
            if isinstance(step.get("step_number"), int)
        }
        for use_case_id, spec in specs_by_id.items()
    }
    relations: list[dict] = []
    dropped: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for selection in model.extends:
        base_id = selection.base_use_case_id
        extending_id = selection.extending_use_case_id
        step_ref = _normalize_step_ref(selection.base_step_ref)
        label = f"extend {base_id} / {extending_id}"
        if base_id not in names_by_id or extending_id not in names_by_id:
            dropped.append(f"{label}: relationship stage cannot create use cases")
            continue
        if base_id == extending_id:
            dropped.append(f"{label}: endpoints must be distinct")
            continue
        if step_ref is None or step_ref not in valid_steps.get(base_id, {}):
            dropped.append(
                f"{label}: base step ref {selection.base_step_ref!r} is not one of "
                f"{sorted(valid_steps.get(base_id, {}))}"
            )
            continue
        identity = (base_id, extending_id, step_ref)
        if identity in seen:
            continue
        seen.add(identity)
        requirement_ids = requirements_by_id.get(extending_id, [])
        relations.append(
            {
                "base_use_case_id": base_id,
                "extending_use_case_id": extending_id,
                "base_use_case": names_by_id[base_id],
                "extending_use_case": names_by_id[extending_id],
                "condition": _clean_text(selection.condition),
                "extension_point": step_ref,
                "extension_point_name": _clean_text(selection.extension_point_name),
                "step_refs": [
                    {
                        "use_case_id": base_id,
                        "step_ref": step_ref,
                        "sentence": valid_steps[base_id][step_ref],
                    }
                ],
                "requirement_ids": requirement_ids,
                "requirement_refs": [
                    {"use_case_id": extending_id, "requirement_id": requirement_id}
                    for requirement_id in requirement_ids
                ],
            }
        )
    return relations, dropped


def _suppress_redundant_associations(
    associations: list[dict], relations: dict[str, list[dict]], actors: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Hide a relation-owned duplicate entry point for the same actor lineage."""
    actor_names = {str(actor.get("name") or "") for actor in actors}
    parent_by_child = {
        str(actor["name"]): str(actor["parent_actor"])
        for actor in actors
        if actor.get("name") and actor.get("parent_actor") in actor_names
    }
    pairs = {(str(item["actor"]), str(item["use_case_id"])) for item in associations}
    bases_by_target: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for relation in relations["includes"]:
        bases_by_target[str(relation["included_use_case_id"])].append(
            (str(relation["base_use_case_id"]), "include")
        )
    for relation in relations["extends"]:
        bases_by_target[str(relation["extending_use_case_id"])].append(
            (str(relation["base_use_case_id"]), "extend")
        )

    kept: list[dict] = []
    suppressed: list[dict] = []
    for association in associations:
        actor = str(association["actor"])
        target = str(association["use_case_id"])
        lineage = {actor, *_ancestor_names(actor, parent_by_child)}
        inherited = next(
            (
                (base_id, kind)
                for base_id, kind in bases_by_target.get(target, [])
                if any((candidate_actor, base_id) in pairs for candidate_actor in lineage)
            ),
            None,
        )
        if inherited is None:
            kept.append(association)
            continue
        base_id, kind = inherited
        suppressed.append(
            {
                "actor": actor,
                "use_case_id": target,
                "via_use_case_id": base_id,
                "relation_kind": kind,
            }
        )
    return kept, suppressed


@contract("identify_relationships", requires=("use_cases", "actors"), produces=("relationships",))
def identify_relationships(state: AgentState, feedback: str = "") -> dict:
    """Project include/actor facts and semantically select bounded extend relations."""
    feedback = supervisor.feedback_for(cast(dict, state), "relationships", feedback)
    use_cases = cast(list[dict], state.get("use_cases") or [])
    empty: dict[str, Any] = {
        "associations": [],
        "includes": [],
        "extends": [],
        "generalizations": [],
        "derived_use_cases": [],
        "suppressed_associations": [],
        "orphan_actors": [],
        "dropped_refs": [],
        "relationship_issues": [],
    }
    if not use_cases:
        return {"relationships": empty, "phase": "relationships"}

    actors = cast(list[dict], state.get("actors") or [])
    associations, generalizations, actor_drops, orphan_actors = _actor_projection(
        use_cases, actors
    )
    accepted_ids = {str(use_case["id"]) for use_case in use_cases}
    specs_by_id, spec_rejections = _accepted_specs_by_id(state, accepted_ids)
    candidates = _include_candidates(state, use_cases, specs_by_id)
    if specs_by_id:
        model: RelationshipModel = invoke_structured(
            RelationshipModel,
            [
                SystemMessage(content=prompts.generation_system_for(rules.DRAW_DIAGRAM)),
                HumanMessage(
                    content=_relationship_prompt(
                        state, use_cases, specs_by_id, candidates, feedback
                    )
                ),
            ],
        )
    else:
        model = RelationshipModel()
    includes, derived, include_drops = _materialize_includes(
        model, candidates, use_cases
    )
    extends, extend_drops = _materialize_extends(model, use_cases, specs_by_id)
    relation_parts = {"includes": includes, "extends": extends}
    projected_associations, suppressed = _suppress_redundant_associations(
        associations, relation_parts, actors
    )
    relations = {
        "associations": projected_associations,
        "includes": includes,
        "extends": extends,
        "generalizations": generalizations,
        "derived_use_cases": derived,
        "suppressed_associations": suppressed,
        "orphan_actors": orphan_actors,
        "dropped_refs": [
            *actor_drops,
            *(f"{item['use_case_id']}: {item['reason']}" for item in spec_rejections),
            *include_drops,
            *extend_drops,
        ],
        "relationship_issues": [],
        "semantic_status": "single_pass" if specs_by_id else "not_run",
        "repair_iters": 0,
        "repair_stopped": "single_pass" if specs_by_id else "not_applicable",
    }
    if specs_by_id:
        review = validator.review(
            rules.DRAW_DIAGRAM,
            _relationship_review_payload(
                cast(list[dict], state.get("classified") or []),
                actors,
                use_cases,
                specs_by_id,
                relations,
            ),
            prefix="rel",
            source="relationships.semantic_validator",
            confirm_violations=True,
        )
        relations["relationship_issues"] = review.findings
        relations["semantic_status"] = review.status
        relations["unexamined_rules"] = list(review.unexamined)
        relations["repair_stopped"] = (
            "clean" if review.status == validator.OK and not review.findings else "unresolved"
        )
    return {"relationships": relations, "phase": "relationships"}


@contract("check_relationships", requires=("relationships",), produces=("relationship_report",))
def check_relationships(state: AgentState) -> dict:
    """Aggregate deterministic relationship integrity diagnostics."""
    rel = state.get("relationships") or {}
    use_cases = cast(list[dict], state.get("use_cases") or [])
    declared_supporting = {
        (str(actor), str(use_case["id"]))
        for use_case in use_cases
        for actor in use_case.get("supporting_actors", []) or []
    }
    associations = {
        (str(item.get("actor") or ""), str(item.get("use_case_id") or ""))
        for item in rel.get("associations", [])
    }
    actor_names = {str(actor.get("name") or "") for actor in state.get("actors") or []}
    parent_by_child = {
        str(actor["name"]): str(actor["parent_actor"])
        for actor in state.get("actors") or []
        if actor.get("name") and actor.get("parent_actor") in actor_names
    }
    bases_by_target: dict[str, set[str]] = defaultdict(set)
    for relation in [*rel.get("includes", []), *rel.get("extends", [])]:
        target_key = (
            "included_use_case_id"
            if "included_use_case_id" in relation
            else "extending_use_case_id"
        )
        bases_by_target[str(relation.get(target_key) or "")].add(
            str(relation.get("base_use_case_id") or "")
        )

    def is_associated(actor: str, use_case_id: str, seen: set[str] | None = None) -> bool:
        if (actor, use_case_id) in associations or any(
            (ancestor, use_case_id) in associations
            for ancestor in _ancestor_names(actor, parent_by_child)
        ):
            return True
        visited = seen or set()
        return use_case_id not in visited and any(
            is_associated(actor, base_id, visited | {use_case_id})
            for base_id in bases_by_target.get(use_case_id, set())
        )

    report = {
        "counts": {
            key: len(rel.get(key, []))
            for key in (
                "associations",
                "includes",
                "extends",
                "generalizations",
                "derived_use_cases",
            )
        },
        "orphan_actors": rel.get("orphan_actors", []),
        "declared_supporting_associations": len(declared_supporting),
        "missing_supporting_associations": sorted(
            f"{actor} -> {use_case_id}"
            for actor, use_case_id in declared_supporting
            if not is_associated(actor, use_case_id)
        ),
        "dropped_refs": rel.get("dropped_refs", []),
        "relationship_issues": rel.get("relationship_issues", []),
        "semantic_status": rel.get("semantic_status", "unknown"),
        "unexamined_rules": rel.get("unexamined_rules", []),
        "repair_iters": rel.get("repair_iters", 0),
        "repair_stopped": rel.get("repair_stopped", "unknown"),
    }
    return {"relationship_report": report, "phase": "check_relationships"}


def _san(name: str) -> str:
    """Make a collision-proof PlantUML alias while retaining its stable source key."""
    alias = re.sub(r"\W+", "_", name).strip("_") or "n"
    safe = alias if alias[0].isalpha() else f"n_{alias}"
    return f"{safe}_{hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]}"


def _plantuml_label(value: object) -> str:
    return _clean_text(value).replace("\\", "/").replace('"', "'")


def _label_lines(value: object, width: int) -> list[str]:
    compact = _plantuml_label(value)
    if not compact:
        return []
    return textwrap.wrap(
        compact,
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [compact]


def _extend_label(relation: dict) -> str:
    condition = "\\n".join(_label_lines(relation.get("condition"), width=32))
    return f"<<extend>>\\n[{condition}]" if condition else "<<extend>>"


@contract("render_diagram", requires=("relationships", "use_cases", "actors"), produces=("diagram",))
def render_diagram(state: AgentState) -> dict:
    """Render stable-ID relations while preserving the reviewed use-case order."""
    actors = cast(list[dict], state.get("actors") or [])
    use_cases = cast(list[dict], state.get("use_cases") or [])
    rel = state.get("relationships") or {}
    if not use_cases:
        return {"diagram": "@startuml\n@enduml", "phase": "diagram"}

    actor_alias = {str(actor["name"]): _san(str(actor["name"])) for actor in actors}
    use_cases_by_id = {str(use_case["id"]): use_case for use_case in use_cases}
    derived = cast(list[dict], rel.get("derived_use_cases") or [])
    derived_by_id = {str(item["use_case_id"]): item for item in derived}
    uc_alias = {
        use_case_id: _san(use_case_id)
        for use_case_id in [*use_cases_by_id, *derived_by_id]
    }
    extension_points: dict[str, list[str]] = defaultdict(list)
    for extension in rel.get("extends", []):
        base_id = str(extension.get("base_use_case_id") or "")
        point = _plantuml_label(
            extension.get("extension_point_name") or extension.get("extension_point")
        )
        if base_id in use_cases_by_id and point and point not in extension_points[base_id]:
            extension_points[base_id].append(point)

    primary_names = {str(use_case.get("primary_actor") or "") for use_case in use_cases}
    supporting_names = {
        str(actor)
        for use_case in use_cases
        for actor in use_case.get("supporting_actors", []) or []
    }
    primary = [
        actor
        for actor in actors
        if str(actor["name"]) in primary_names or str(actor["name"]) not in supporting_names
    ]
    supporting = [
        actor
        for actor in actors
        if str(actor["name"]) in supporting_names and str(actor["name"]) not in primary_names
    ]

    lines = ["@startuml", "left to right direction"]
    for actor in primary:
        name = str(actor["name"])
        lines.append(f'actor "{_plantuml_label(name)}" as {actor_alias[name]}')
    lines.append("rectangle System {")
    for use_case in use_cases:
        use_case_id = str(use_case["id"])
        label = _plantuml_label(use_case.get("name"))
        point_lines = [
            line
            for point in extension_points.get(use_case_id, [])
            for line in _label_lines(point, width=40)
        ]
        if point_lines:
            label += "\\n-- extension points --\\n" + "\\n".join(point_lines)
        lines.append(f'  usecase "{label}" as {uc_alias[use_case_id]}')
    for item in derived:
        use_case_id = str(item["use_case_id"])
        lines.append(
            f'  usecase "{_plantuml_label(item.get("name"))}" as {uc_alias[use_case_id]}'
        )
    lines.append("}")
    for actor in supporting:
        name = str(actor["name"])
        lines.append(f'actor "{_plantuml_label(name)}" as {actor_alias[name]}')

    for association in rel.get("associations", []):
        actor_name = str(association.get("actor") or "")
        use_case_id = str(association.get("use_case_id") or "")
        associated_use_case = use_cases_by_id.get(use_case_id)
        if actor_name not in actor_alias or associated_use_case is None:
            continue
        if actor_name in (associated_use_case.get("supporting_actors") or []):
            lines.append(f"{uc_alias[use_case_id]} --- {actor_alias[actor_name]}")
        else:
            lines.append(f"{actor_alias[actor_name]} --- {uc_alias[use_case_id]}")
    for include in rel.get("includes", []):
        base_id = str(include.get("base_use_case_id") or "")
        included_id = str(include.get("included_use_case_id") or "")
        if base_id in uc_alias and included_id in uc_alias:
            lines.append(f"{uc_alias[base_id]} ..> {uc_alias[included_id]} : <<include>>")
    for extend in rel.get("extends", []):
        base_id = str(extend.get("base_use_case_id") or "")
        extending_id = str(extend.get("extending_use_case_id") or "")
        if base_id in uc_alias and extending_id in uc_alias:
            lines.append(
                f"{uc_alias[base_id]} <.. {uc_alias[extending_id]} : {_extend_label(extend)}"
            )
    for generalization in rel.get("generalizations", []):
        if generalization.get("kind") != "actor":
            continue
        parent = str(generalization.get("parent") or "")
        child = str(generalization.get("child") or "")
        if parent in actor_alias and child in actor_alias:
            lines.append(f"{actor_alias[parent]} <|-- {actor_alias[child]}")
    lines.append("@enduml")
    return {"diagram": "\n".join(lines), "phase": "diagram"}
