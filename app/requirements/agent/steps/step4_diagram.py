"""Step 4: evidence-bound relationship projection and deterministic PlantUML rendering.

The model never creates a relationship. This module derives relation candidates from
accepted use-case specifications, asks the model to approve or reject their stable IDs,
then materializes the accepted candidates with stable ``use_case_id`` joins.
"""
from __future__ import annotations

import hashlib
import json
import re
import textwrap
from collections import defaultdict
from collections.abc import Iterable
from itertools import pairwise
from typing import Any, cast

from langchain_core.messages import HumanMessage, SystemMessage

from app.requirements import prompts
from app.requirements.agent import supervisor, validator
from app.requirements.agent.llm import invoke_structured
from app.requirements.agent.state import AgentState
from app.requirements.common.state_contract import contract
from app.requirements.schemas import RelationshipModel

_OPTIONAL_WORDS = re.compile(
    r"\b(?:optional|optionally|choose(?:s|n)?|select(?:s|ed|ing)?|opt(?:s|ed|ing)?|"
    r"request(?:s|ed|ing)?|want(?:s|ed|ing)?|may|can)\b",
    re.IGNORECASE,
)
_MANDATORY_WORDS = re.compile(
    r"\b(?:must|required|mandatory|shall|always|every|each)\b", re.IGNORECASE
)
_LEXICAL_STOP_WORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "for", "from", "if", "in",
        "is", "it", "its", "may", "of", "on", "only", "or", "provided", "the", "their", "then",
        "this", "to", "unless", "when", "where", "which", "who", "with", "would",
    }
)


def _norm_step(sentence: str) -> str:
    """Normalize a source step for deterministic shared-subfunction matching."""
    tokens = re.sub(r"[^\w\s]", " ", str(sentence).casefold()).split()
    return " ".join(token for token in tokens if token not in {"a", "an", "the"})


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"


def _step_number(step: dict) -> int | None:
    value = step.get("step_number")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _display_name(sentence: str, actor_names: Iterable[str]) -> str:
    """Derive a deterministic short display name without changing the evidence itself."""
    value = _clean_text(sentence).rstrip(".?!:;")
    prefixes = ["System", *sorted(actor_names, key=len, reverse=True)]
    for prefix in prefixes:
        if value.casefold().startswith(f"{prefix.casefold()} "):
            value = value[len(prefix):].lstrip()
            break
    return value[:1].upper() + value[1:] if value else "Projected behavior"


def _step_ref(
    use_case_id: str, step_number: int, sentence: str, covered_req_ids: Iterable[str] = ()
) -> dict[str, object]:
    return {
        "use_case_id": use_case_id,
        "step_ref": f"main:{step_number}",
        "sentence": _clean_text(sentence),
        "covered_req_ids": sorted({str(requirement_id) for requirement_id in covered_req_ids}),
    }


def _accepted_specs_by_id(state: AgentState, accepted_ids: set[str]) -> tuple[dict[str, dict], list[dict]]:
    """Index specs only by their stable use-case ID; display names are never a join key."""
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
        previous = indexed.get(use_case_id)
        if previous is None or _spec_sort_key(spec) < _spec_sort_key(previous):
            indexed[use_case_id] = spec
    return indexed, sorted(rejections, key=lambda item: item["use_case_id"])


def _spec_sort_key(spec: dict) -> str:
    """Choose deterministically if an invalid input carries duplicate spec IDs."""
    return json.dumps(spec, ensure_ascii=False, sort_keys=True, default=str)


def _mine_include_candidates(
    use_cases: list[dict], specs_by_id: dict[str, dict], actor_names: Iterable[str]
) -> list[dict[str, object]]:
    """Build deduplicated include candidates from shared step or requirement evidence."""
    shared_steps: dict[str, list[dict[str, object]]] = defaultdict(list)
    shared_requirements: dict[str, list[dict[str, object]]] = defaultdict(list)
    for use_case in sorted(use_cases, key=lambda item: str(item["id"])):
        use_case_id = str(use_case["id"])
        spec = specs_by_id.get(use_case_id)
        if not spec:
            continue
        accepted_requirement_ids = {str(item) for item in use_case.get("requirement_ids") or []}
        for step in spec.get("main_scenario") or []:
            number = _step_number(step)
            sentence = _clean_text(step.get("sentence"))
            normalized = _norm_step(sentence)
            if number is None or len(normalized.split()) < 2:
                continue
            covered = [
                str(requirement_id)
                for requirement_id in step.get("covered_req_ids") or []
                if str(requirement_id) in accepted_requirement_ids
            ]
            ref = _step_ref(use_case_id, number, sentence, covered)
            shared_steps[normalized].append(ref)
            for requirement_id in covered:
                shared_requirements[requirement_id].append(ref)

    grouped: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}

    def add_evidence(refs: list[dict[str, object]], *, normalized: str | None, requirement_id: str | None) -> None:
        ordered_refs = sorted(refs, key=lambda ref: (str(ref["use_case_id"]), str(ref["step_ref"])))
        participant_ids = {str(ref["use_case_id"]) for ref in ordered_refs}
        if len(participant_ids) < 2:
            return
        key = tuple((str(ref["use_case_id"]), str(ref["step_ref"])) for ref in ordered_refs)
        group = grouped.setdefault(
            key,
            {
                "step_refs": ordered_refs,
                "normalized_subfunctions": set(),
                "requirement_ids": set(),
            },
        )
        if normalized:
            group["normalized_subfunctions"].add(normalized)
        if requirement_id:
            group["requirement_ids"].add(requirement_id)

    for normalized, refs in shared_steps.items():
        add_evidence(refs, normalized=normalized, requirement_id=None)
    for requirement_id, refs in shared_requirements.items():
        add_evidence(refs, normalized=None, requirement_id=requirement_id)

    candidates: list[dict[str, object]] = []
    for group in grouped.values():
        refs = group["step_refs"]
        participant_ids = sorted({str(ref["use_case_id"]) for ref in refs})
        normalized_subfunctions = sorted(group["normalized_subfunctions"])
        requirement_ids = sorted(group["requirement_ids"])
        requirement_refs = [
            {
                "use_case_id": ref["use_case_id"],
                "step_ref": ref["step_ref"],
                "requirement_id": requirement_id,
            }
            for ref in refs
            for requirement_id in requirement_ids
            if requirement_id in ref["covered_req_ids"]
        ]
        identity = {
            "kind": "include",
            "participating_use_case_ids": participant_ids,
            "step_refs": [
                {"use_case_id": ref["use_case_id"], "step_ref": ref["step_ref"]} for ref in refs
            ],
            "normalized_subfunctions": normalized_subfunctions,
            "requirement_refs": requirement_refs,
        }
        candidate_id = _stable_id("rel-include", identity)
        digest = candidate_id.rsplit("-", 1)[1].upper()
        candidates.append(
            {
                "candidate_id": candidate_id,
                "kind": "include",
                "participating_use_case_ids": participant_ids,
                "step_refs": refs,
                "derived_use_case_id": f"UC_INC_{digest}",
                "derived_use_case_name": _display_name(str(refs[0]["sentence"]), actor_names),
                "normalized_subfunction": normalized_subfunctions[0] if normalized_subfunctions else None,
                "requirement_ids": requirement_ids,
                "requirement_refs": requirement_refs,
                "condition": None,
                "extension_point": None,
            }
        )
    return candidates


def _actor_compatible(left: str, right: str, parent_by_child: dict[str, str]) -> bool:
    left_lineage = {left, *_ancestor_names(left, parent_by_child)}
    right_lineage = {right, *_ancestor_names(right, parent_by_child)}
    return bool(left_lineage & right_lineage)


def _lexical_terms(value: object) -> list[str]:
    """Return ordered content-bearing English terms for deterministic evidence ranking."""
    return [
        token
        for token in _norm_step(_clean_text(value)).split()
        if len(token) > 1 and token not in _LEXICAL_STOP_WORDS
    ]


def _lexical_tokens(value: object) -> set[str]:
    return set(_lexical_terms(value))


def _lexical_bigrams(value: object) -> set[tuple[str, str]]:
    terms = _lexical_terms(value)
    return set(pairwise(terms))


def _condition_clause(value: str) -> str:
    """Prefer the condition clause when an optional requirement states one."""
    match = re.search(
        r"\b(?:only\s+if|if|when|unless|provided(?:\s+that)?)\b\s*(.+)",
        value,
        re.IGNORECASE,
    )
    return match.group(1) if match else value


def _display_condition(value: str) -> str:
    """Keep an explicit leading condition clause short enough for the diagram."""
    match = re.match(
        r"\s*((?:only\s+if|if|when|while|after|before|unless|provided(?:\s+that)?)\b[^,.;]*)",
        value,
        re.IGNORECASE,
    )
    return _clean_text(match.group(1) if match else value)


def _base_context_evidence(
    base: dict, spec: dict, requirement_text: dict[str, str]
) -> tuple[set[str], set[tuple[str, str]]]:
    """Collect only accepted source text that can support a top-level extension anchor."""
    texts = [base.get("name"), base.get("goal")]
    texts.extend(step.get("sentence") for step in spec.get("main_scenario") or [])
    texts.extend(requirement_text.get(str(requirement_id), "") for requirement_id in base.get("requirement_ids") or [])
    return (
        set().union(*(_lexical_tokens(text) for text in texts)),
        set().union(*(_lexical_bigrams(text) for text in texts)),
    )


def _anchor_score(
    condition_tokens: set[str],
    full_tokens: set[str],
    condition_bigrams: set[tuple[str, str]],
    context_tokens: set[str],
    context_bigrams: set[tuple[str, str]],
    step: dict,
) -> int:
    """Weight direct condition matches, then preserve small ordered-phrase evidence."""
    step_tokens = _lexical_tokens(step.get("sentence"))
    ordered_matches = condition_bigrams & (context_bigrams | _lexical_bigrams(step.get("sentence")))
    return (
        3 * len(condition_tokens & step_tokens)
        + 2 * len(condition_tokens & context_tokens)
        + len(full_tokens & step_tokens)
        + len(full_tokens & context_tokens)
        + len(ordered_matches)
    )


def _top_level_extend_candidates(
    state: AgentState, use_cases: list[dict], specs_by_id: dict[str, dict]
) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    """Offer one evidence-supported compatible base anchor per optional requirement group."""
    requirement_text = {
        str(requirement.get("id") or ""): _clean_text(requirement.get("text"))
        for requirement in state.get("classified") or []
        if requirement.get("type") == "FR"
    }
    actor_names = {str(actor.get("name") or "") for actor in state.get("actors") or []}
    parent_by_child = {
        str(actor["name"]): str(actor["parent_actor"])
        for actor in state.get("actors") or []
        if actor.get("name") and actor.get("parent_actor") in actor_names
    }
    candidates: list[dict[str, object]] = []
    rejections: list[dict[str, str]] = []
    accepted = [
        use_case
        for use_case in use_cases
        if use_case.get("level") != "subfunction" and str(use_case["id"]) in specs_by_id
    ]
    for extending in sorted(accepted, key=lambda item: str(item["id"])):
        extending_id = str(extending["id"])
        optional_requirements: dict[str, list[str]] = defaultdict(list)
        for requirement_id in extending.get("requirement_ids") or []:
            requirement_id = str(requirement_id)
            text = requirement_text.get(requirement_id, "")
            if not text or not _OPTIONAL_WORDS.search(text):
                continue
            if _MANDATORY_WORDS.search(text):
                continue
            optional_requirements[_norm_step(text)].append(requirement_id)
        if not optional_requirements:
            continue
        extending_refs = [
            _step_ref(
                extending_id,
                number,
                _clean_text(step.get("sentence")),
                step.get("covered_req_ids") or [],
            )
            for step in specs_by_id[extending_id].get("main_scenario") or []
            if (number := _step_number(step)) is not None
        ]
        if not extending_refs:
            continue
        base_anchors: list[tuple[str, dict, dict, set[str], set[tuple[str, str]]]] = []
        for base in sorted(accepted, key=lambda item: str(item["id"])):
            base_id = str(base["id"])
            if base_id == extending_id or not _actor_compatible(
                str(extending.get("primary_actor") or ""),
                str(base.get("primary_actor") or ""),
                parent_by_child,
            ):
                continue
            base_anchors.append(
                (
                    base_id,
                    base,
                    specs_by_id[base_id],
                    *_base_context_evidence(base, specs_by_id[base_id], requirement_text),
                )
            )

        for normalized_condition, requirement_ids in sorted(optional_requirements.items()):
            condition = requirement_text[requirement_ids[0]]
            condition_tokens = _lexical_tokens(_condition_clause(condition))
            full_tokens = _lexical_tokens(condition)
            condition_bigrams = _lexical_bigrams(_condition_clause(condition))
            ranked: list[tuple[int, str, int, dict[str, object]]] = []
            for base_id, _base, spec, context_tokens, context_bigrams in base_anchors:
                for step in spec.get("main_scenario") or []:
                    number = _step_number(step)
                    if number is None:
                        continue
                    score = _anchor_score(
                        condition_tokens,
                        full_tokens,
                        condition_bigrams,
                        context_tokens,
                        context_bigrams,
                        step,
                    )
                    if score:
                        ranked.append(
                            (
                                score,
                                base_id,
                                number,
                                _step_ref(base_id, number, _clean_text(step.get("sentence"))),
                            )
                        )
            evidence_ref = f"requirement:{','.join(sorted(requirement_ids))}"
            if not ranked:
                rejections.append(
                    {
                        "kind": "extend",
                        "use_case_id": extending_id,
                        "step_ref": evidence_ref,
                        "reason": "no positively supported base anchor",
                    }
                )
                continue
            best_score = max(item[0] for item in ranked)
            best = [item for item in ranked if item[0] == best_score]
            if len({item[1] for item in best}) != 1:
                rejections.append(
                    {
                        "kind": "extend",
                        "use_case_id": extending_id,
                        "step_ref": evidence_ref,
                        "reason": "ambiguous supported base anchors",
                    }
                )
                continue
            # When several steps in the same proven base are equally supported,
            # attach after the latest one: that is the first point where all
            # preceding base effects are available to optional behaviour.
            _score, base_id, number, anchor = max(
                best, key=lambda item: (item[2], item[3]["step_ref"])
            )
            requirement_refs = [
                {
                    "use_case_id": extending_id,
                    "requirement_id": requirement_id,
                    "source_text": requirement_text[requirement_id],
                }
                for requirement_id in sorted(requirement_ids)
            ]
            identity = {
                "kind": "extend_existing",
                "participating_use_case_ids": [base_id, extending_id],
                "step_refs": [
                    {"use_case_id": ref["use_case_id"], "step_ref": ref["step_ref"]}
                    for ref in [anchor, *extending_refs]
                ],
                "requirement_refs": requirement_refs,
                "condition": normalized_condition,
            }
            candidate_id = _stable_id("rel-extend", identity)
            candidates.append(
                {
                    "candidate_id": candidate_id,
                    "kind": "extend",
                    "participating_use_case_ids": [base_id, extending_id],
                    "step_refs": [anchor, *extending_refs],
                    "derived_use_case_id": extending_id,
                    "derived_use_case_name": str(extending.get("name") or ""),
                    "existing_use_case": True,
                    "normalized_subfunction": None,
                    "requirement_ids": sorted(requirement_ids),
                    "requirement_refs": requirement_refs,
                    "condition": _display_condition(condition),
                    "extension_point": f"main:{number}",
                    "extension_point_name": (
                        f"main step {number}: "
                        f"{_display_name(str(anchor['sentence']), actor_names)}"
                    ),
                }
            )
    return candidates, sorted(rejections, key=lambda item: (item["use_case_id"], item["step_ref"]))


def _relationship_candidates(
    state: AgentState, use_cases: list[dict], actors: list[dict]
) -> tuple[list[dict], list[dict]]:
    accepted_ids = {str(use_case["id"]) for use_case in use_cases}
    specs_by_id, rejected_specs = _accepted_specs_by_id(state, accepted_ids)
    actor_names = [str(actor.get("name") or "") for actor in actors]
    includes = _mine_include_candidates(use_cases, specs_by_id, actor_names)
    existing_extends, existing_rejections = _top_level_extend_candidates(state, use_cases, specs_by_id)
    candidates = {
        str(candidate["candidate_id"]): candidate
        for candidate in [*includes, *existing_extends]
    }
    return (
        [candidates[candidate_id] for candidate_id in sorted(candidates)],
        sorted(
            [*rejected_specs, *existing_rejections],
            key=lambda item: (item["use_case_id"], item["step_ref"]),
        ),
    )


def _ancestor_names(actor: str, parent_by_child: dict[str, str]) -> set[str]:
    ancestors: set[str] = set()
    current = parent_by_child.get(actor)
    while current and current not in ancestors:
        ancestors.add(current)
        current = parent_by_child.get(current)
    return ancestors


def _actor_projection(use_cases: list[dict], actors: list[dict]) -> tuple[list[dict], list[dict], list[str], list[str]]:
    """Derive actor relations and inherited-link suppression solely from accepted input."""
    actor_names = {str(actor.get("name") or "") for actor in actors}
    parent_by_child = {
        str(actor["name"]): str(actor["parent_actor"])
        for actor in actors
        if actor.get("name") and actor.get("parent_actor") in actor_names
    }
    generalizations = [
        {
            "parent": parent,
            "child": child,
            "kind": "actor",
            "rationale": "declared actor specialization",
        }
        for child, parent in sorted(parent_by_child.items())
    ]

    declared: set[tuple[str, str]] = set()
    dropped: list[str] = []
    for use_case in use_cases:
        use_case_id = str(use_case["id"])
        for actor in [use_case.get("primary_actor"), *(use_case.get("supporting_actors") or [])]:
            actor_name = str(actor or "")
            if not actor_name:
                continue
            if actor_name not in actor_names:
                dropped.append(f"unknown actor {actor_name} for use case {use_case_id}")
                continue
            declared.add((actor_name, use_case_id))

    associations: list[dict] = []
    for actor_name, use_case_id in sorted(declared, key=lambda pair: (pair[1], pair[0])):
        if any((ancestor, use_case_id) in declared for ancestor in _ancestor_names(actor_name, parent_by_child)):
            continue
        associations.append({"actor": actor_name, "use_case_id": use_case_id})

    associated_actors = {actor for actor, _ in declared}
    for actor_name, _ in declared:
        associated_actors.update(
            child for child in parent_by_child if actor_name in _ancestor_names(child, parent_by_child)
        )
    orphan_actors = sorted(actor_name for actor_name in actor_names if actor_name not in associated_actors)
    return associations, generalizations, sorted(dropped), orphan_actors


def _decision_state(candidates: list[dict], model: RelationshipModel) -> tuple[set[str], list[dict], list[str]]:
    """Return safely approved candidate IDs; unknown or ambiguous decisions are rejected."""
    candidate_ids = {str(candidate["candidate_id"]) for candidate in candidates}
    values_by_id: dict[str, set[str]] = defaultdict(set)
    dropped: list[str] = []
    for decision in model.candidate_decisions:
        if decision.candidate_id not in candidate_ids:
            dropped.append(f"unknown relationship candidate {decision.candidate_id}")
            continue
        values_by_id[decision.candidate_id].add(decision.decision)

    normalized: list[dict] = []
    approved: set[str] = set()
    for candidate_id in sorted(values_by_id):
        values = values_by_id[candidate_id]
        if len(values) != 1:
            dropped.append(f"ambiguous relationship candidate decision {candidate_id}")
            normalized.append({"candidate_id": candidate_id, "decision": "reject"})
            continue
        decision_value = next(iter(values))
        normalized.append({"candidate_id": candidate_id, "decision": decision_value})
        if decision_value == "approve":
            approved.add(candidate_id)
    return approved, normalized, sorted(dropped)


def _materialize_candidates(candidates: list[dict], approved_ids: set[str], use_cases: list[dict]) -> dict[str, list[dict]]:
    """Project only approved evidence-bound candidates into public relationships."""
    names_by_id = {str(use_case["id"]): str(use_case.get("name") or "") for use_case in use_cases}
    includes: list[dict] = []
    extends: list[dict] = []
    derived: list[dict] = []
    for candidate in candidates:
        if candidate["candidate_id"] not in approved_ids:
            continue
        if candidate["kind"] == "include":
            derived.append(
                {
                    "use_case_id": candidate["derived_use_case_id"],
                    "name": candidate["derived_use_case_name"],
                    "origin": "factored_include",
                    "candidate_id": candidate["candidate_id"],
                }
            )
            for base_use_case_id in candidate["participating_use_case_ids"]:
                includes.append(
                    {
                        "candidate_id": candidate["candidate_id"],
                        "base_use_case_id": base_use_case_id,
                        "included_use_case_id": candidate["derived_use_case_id"],
                        "base_use_case": names_by_id.get(str(base_use_case_id), ""),
                        "included_use_case": candidate["derived_use_case_name"],
                        "step_refs": candidate["step_refs"],
                        "requirement_ids": candidate.get("requirement_ids", []),
                        "requirement_refs": candidate.get("requirement_refs", []),
                    }
                )
            continue
        base_use_case_id = candidate["participating_use_case_ids"][0]
        extends.append(
            {
                "candidate_id": candidate["candidate_id"],
                "base_use_case_id": base_use_case_id,
                "extending_use_case_id": candidate["derived_use_case_id"],
                "base_use_case": names_by_id.get(str(base_use_case_id), ""),
                "extending_use_case": candidate["derived_use_case_name"],
                "condition": candidate["condition"],
                "extension_point": candidate["extension_point"],
                "extension_point_name": candidate["extension_point_name"],
                "step_refs": candidate["step_refs"],
                "requirement_ids": candidate.get("requirement_ids", []),
                "requirement_refs": candidate.get("requirement_refs", []),
            }
        )
    return {
        "includes": sorted(includes, key=lambda item: (item["base_use_case_id"], item["candidate_id"])),
        "extends": sorted(extends, key=lambda item: (item["base_use_case_id"], item["candidate_id"])),
        "derived_use_cases": sorted(derived, key=lambda item: item["use_case_id"]),
    }


def _suppress_redundant_associations(
    associations: list[dict], relations: dict[str, list[dict]], actors: list[dict]
) -> tuple[list[dict], list[dict]]:
    """Hide a direct child link when the same actor can already reach it through its base."""
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
        reachable = {actor, *_ancestor_names(actor, parent_by_child)}
        inherited = next(
            (
                (base_use_case_id, kind)
                for base_use_case_id, kind in bases_by_target.get(target, [])
                if any((candidate_actor, base_use_case_id) in pairs for candidate_actor in reachable)
            ),
            None,
        )
        if inherited is None:
            kept.append(association)
            continue
        base_use_case_id, kind = inherited
        suppressed.append(
            {
                "actor": actor,
                "use_case_id": target,
                "via_use_case_id": base_use_case_id,
                "relation_kind": kind,
            }
        )
    return kept, suppressed


def _candidate_prompt(candidates: list[dict], feedback: str) -> str:
    candidate_view = [
        {
            "candidate_id": candidate["candidate_id"],
            "kind": candidate["kind"],
            "participating_use_case_ids": candidate["participating_use_case_ids"],
            "step_refs": candidate["step_refs"],
            "requirement_refs": candidate.get("requirement_refs", []),
            "existing_use_case": candidate.get("existing_use_case", False),
            "derived_use_case_name": candidate["derived_use_case_name"],
            "condition": candidate["condition"],
            "extension_point": candidate["extension_point"],
            "extension_point_name": candidate.get("extension_point_name"),
        }
        for candidate in candidates
    ]
    base = (
        "Relationship candidates were deterministically derived from accepted use-case evidence.\n"
        "Return only candidate_decisions. Each decision must copy one candidate_id and set decision to "
        "approve or reject. Do not create, rename, or alter any relationship.\n\n"
        f"Candidates:\n{json.dumps(candidate_view, ensure_ascii=False, indent=2)}"
    )
    return prompts.apply_user_feedback(base, feedback)


@contract("identify_relationships", requires=("use_cases", "actors"), produces=("relationships",))
def identify_relationships(state: AgentState, feedback: str = "") -> dict:
    """Project deterministic actor links and model-approved evidence candidates."""
    feedback = supervisor.feedback_for(cast(dict, state), "relationships", feedback)
    use_cases = cast(list[dict], state.get("use_cases") or [])
    empty: dict[str, Any] = {
        "associations": [],
        "includes": [],
        "extends": [],
        "generalizations": [],
        "derived_use_cases": [],
        "candidates": [],
        "candidate_decisions": [],
        "candidate_rejections": [],
        "suppressed_associations": [],
        "orphan_actors": [],
        "dropped_refs": [],
        "relationship_issues": [],
    }
    if not use_cases:
        return {"relationships": empty, "phase": "relationships"}

    actors = cast(list[dict], state.get("actors") or [])
    associations, generalizations, association_drops, orphan_actors = _actor_projection(use_cases, actors)
    candidates, candidate_rejections = _relationship_candidates(state, use_cases, actors)

    def materialize(model: RelationshipModel) -> dict:
        approved_ids, decisions, decision_drops = _decision_state(candidates, model)
        rel: dict[str, Any] = _materialize_candidates(candidates, approved_ids, use_cases)
        projected_associations, suppressed_associations = _suppress_redundant_associations(
            associations, rel, actors
        )
        rel.update(
            {
                "associations": projected_associations,
                "generalizations": generalizations,
                "candidates": candidates,
                "candidate_decisions": decisions,
                "candidate_rejections": candidate_rejections,
                "suppressed_associations": suppressed_associations,
                "orphan_actors": orphan_actors,
                "dropped_refs": sorted([*association_drops, *decision_drops]),
            }
        )
        return rel

    if candidates:
        decision_model: RelationshipModel = invoke_structured(
            RelationshipModel,
            [
                SystemMessage(content=prompts.RELATIONSHIPS_SYSTEM),
                HumanMessage(content=_candidate_prompt(candidates, feedback)),
            ],
        )
    else:
        decision_model = RelationshipModel()
    rel = materialize(decision_model)
    rel["relationship_issues"] = []
    rel["semantic_status"] = "not_run"
    rel["repair_iters"] = 0
    rel["repair_stopped"] = "not_applicable"
    return {"relationships": rel, "phase": "relationships"}


@contract("check_relationships", requires=("relationships",), produces=("relationship_report",))
def check_relationships(state: AgentState) -> dict:
    """Aggregate deterministic relationship-projection diagnostics."""
    rel = state.get("relationships") or {}
    use_cases = cast(list[dict], state.get("use_cases") or [])
    declared_supporting = {
        (str(actor), str(use_case["id"]))
        for use_case in use_cases
        for actor in use_case.get("supporting_actors", []) or []
    }
    associations = {
        (str(association.get("actor") or ""), str(association.get("use_case_id") or ""))
        for association in rel.get("associations", [])
    }
    actor_names = {str(actor.get("name") or "") for actor in state.get("actors") or []}
    parent_by_child = {
        str(actor["name"]): str(actor["parent_actor"])
        for actor in state.get("actors") or []
        if actor.get("name") and actor.get("parent_actor") in actor_names
    }
    bases_by_target: dict[str, set[str]] = defaultdict(set)
    for relation in [*rel.get("includes", []), *rel.get("extends", [])]:
        target_key = "included_use_case_id" if "included_use_case_id" in relation else "extending_use_case_id"
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
            is_associated(actor, base_use_case_id, visited | {use_case_id})
            for base_use_case_id in bases_by_target.get(use_case_id, set())
        )

    report = {
        "counts": {
            key: len(rel.get(key, []))
            for key in ("associations", "includes", "extends", "generalizations", "derived_use_cases")
        },
        "candidate_count": len(rel.get("candidates", [])),
        "approved_candidate_count": sum(
            decision.get("decision") == "approve" for decision in rel.get("candidate_decisions", [])
        ),
        "candidate_rejections": rel.get("candidate_rejections", []),
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
    """Keep evidence labels literal and confined to one PlantUML declaration."""
    return _clean_text(value).replace("\\", "/").replace('"', "'")


def _label_lines(value: object, width: int = 48) -> list[str]:
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
    lines = ["<<extend>>"]
    condition_lines = _label_lines(relation.get("condition"))
    if condition_lines:
        rendered_condition = "\\n".join(condition_lines)
        lines.append(f"[{rendered_condition}]")
    return "\\n".join(lines)


@contract("render_diagram", requires=("relationships", "use_cases", "actors"), produces=("diagram",))
def render_diagram(state: AgentState) -> dict:
    """Render the stable-ID relationship projection without name-based lookups."""
    actors = cast(list[dict], state.get("actors") or [])
    use_cases = state.get("use_cases") or []
    rel = state.get("relationships") or {}
    if not use_cases:
        return {"diagram": "@startuml\n@enduml", "phase": "diagram"}

    actor_alias = {str(actor["name"]): _san(str(actor["name"])) for actor in actors}
    use_cases_by_id = {str(use_case["id"]): use_case for use_case in use_cases}
    uc_alias = {use_case_id: _san(use_case_id) for use_case_id in use_cases_by_id}
    derived_by_id = {
        str(derived["use_case_id"]): derived for derived in rel.get("derived_use_cases", [])
    }
    uc_alias.update({use_case_id: _san(use_case_id) for use_case_id in derived_by_id})
    extension_points_by_base: dict[str, list[str]] = defaultdict(list)
    for extension in rel.get("extends", []):
        base_id = str(extension.get("base_use_case_id") or "")
        point_name = _plantuml_label(
            extension.get("extension_point_name") or extension.get("extension_point")
        )
        if base_id in use_cases_by_id and point_name:
            extension_points_by_base[base_id].append(point_name)

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
    for use_case_id, use_case in sorted(use_cases_by_id.items()):
        label = _plantuml_label(use_case.get("name", ""))
        point_lines = [
            line
            for point in dict.fromkeys(extension_points_by_base.get(use_case_id, []))
            for line in _label_lines(point, width=40)
        ]
        if point_lines:
            label += "\\n-- extension points --\\n" + "\\n".join(point_lines)
        lines.append(
            f'  usecase "{label}" as {uc_alias[use_case_id]}'
        )
    for use_case_id, derived in sorted(derived_by_id.items()):
        lines.append(
            f'  usecase "{_plantuml_label(derived.get("name", ""))}" as {uc_alias[use_case_id]}'
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
        if actor_name in associated_use_case.get("supporting_actors", []):
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
            # UML dependency points from the extending UC to the extended/base UC.
            # The left-arrow form preserves that semantics and the layout used by
            # the earlier, more readable use-case renderer.
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
