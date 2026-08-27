"""Thin checkpoint acceptance adapters and E1 evaluation expectations."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from app.design.services.deployment_diagram.planner import planning_inputs_stale
from app.design.validation import design_readiness_report

_DESIGN_STAGES = {
    "class_diagram": ("class_diagram",),
    "sequence_diagram": ("class_diagram", "sequence_diagram"),
    "api_spec": ("class_diagram", "sequence_diagram", "api_spec"),
    "erd": ("class_diagram", "sequence_diagram", "api_spec", "erd"),
    "deployment_diagram": ("class_diagram", "sequence_diagram", "api_spec", "erd"),
}
_E1_ROLE_PARENT = "university user"
_E1_ROLE_CHILDREN = frozenset({"student", "professor", "academic administrator"})
_E1_ELIGIBILITY_TERMS = frozenset({
    "eligibility", "enrollment", "validation", "registration", "capacity", "conflict",
})
_E1_DATABASE_TERMS = frozenset({"database", "dbms", "postgres", "postgresql", "mysql", "mongodb", "redis", "sql"})
_E1_LOGIN_TERMS = ("login", "log in", "sign in", "authenticate")


def product_contract_issues(checkpoint: str, state: dict[str, Any]) -> list[str]:
    """Adapt the production gates; do not duplicate their design rules here."""

    stages = _DESIGN_STAGES.get(checkpoint, ())
    readiness = design_readiness_report(state, stages) if stages else {"findings": []}
    issues = [
        f"product contract [{item.get('stage')}]: {item.get('finding')}"
        for item in readiness.get("findings") or []
    ]
    if checkpoint == "deployment_diagram":
        bundle = state.get("deployment_diagram_bundle") or {}
        facts = bundle.get("planningFacts") if isinstance(bundle, dict) else None
        if isinstance(facts, dict) and facts.get("inputArtifacts"):
            stale = planning_inputs_stale(
                facts,
                refined_requirements=state.get("refined_requirements"),
                capability_contract=state.get("capability_contract"),
                resource_intake=state.get("resource_intake"),
                resource_spec=state.get("resource_spec"),
                usecase_spec=state.get("usecase_spec"),
                class_model=state.get("extracted_bce_classes"),
                sequence_model=state.get("sequence_diagram_model"),
                api_spec=state.get("api_spec"),
                erd_model=state.get("erd_bce_classes"),
                artifact_versions=state.get("artifact_versions"),
            )
            issues.extend(
                f"product contract: deployment inputs are stale for {item.get('artifact')}"
                for item in stale.get("changedArtifacts") or []
            )
    return issues


def case_expectation_issues(checkpoint: str, state: dict[str, Any]) -> list[str]:
    """Match the digest-locked E1 AWS acceptance facts against persisted models."""

    if checkpoint not in {"usecase_diagram", *_DESIGN_STAGES} or not _is_e1_aws(state):
        return []
    relationships = state.get("relationships")
    if not isinstance(relationships, dict):
        return []
    actors = _records(state.get("actors"))
    actor_by_name = {_normal(item.get("name")): item for item in actors}
    generalizations = {
        (_normal(item.get("parent")), _normal(item.get("child")))
        for item in _records(relationships.get("generalizations"))
    }
    issues: list[str] = []
    if _E1_ROLE_PARENT not in actor_by_name:
        issues.append("e1-aws expectation: specialized-role parent actor is absent")
    for child in _E1_ROLE_CHILDREN:
        actor = actor_by_name.get(child)
        if not actor or _normal(actor.get("parent_actor")) != _E1_ROLE_PARENT or (_E1_ROLE_PARENT, child) not in generalizations:
            issues.append(f"e1-aws expectation: actor generalization {child} -> {_E1_ROLE_PARENT} is absent")

    use_cases = {str(item.get("id") or ""): item for item in _records(state.get("use_cases"))}
    includes = _records(relationships.get("includes"))
    if not _shared_eligibility_include(includes, use_cases):
        issues.append("e1-aws expectation: shared eligibility behavior is not factored as an include")
    if not _optional_export_extend(_records(relationships.get("extends")), use_cases):
        issues.append("e1-aws expectation: optional export extend needs point, name, and condition")
    if not _optional_waitlist_extend(_records(relationships.get("extends")), use_cases):
        issues.append("e1-aws expectation: conditional waitlist extend needs point, name, and condition")
    if any(_has_term(_relation_text(item, use_cases), _E1_LOGIN_TERMS) for item in includes):
        issues.append("e1-aws expectation: authentication must not be modeled as an include")
    actor_references = [item.get("name") for item in actors] + [
        item.get("actor") for item in _records(relationships.get("associations"))
    ]
    if any(set(_words(name)).intersection(_E1_DATABASE_TERMS) for name in actor_references):
        issues.append("e1-aws expectation: a DBMS is infrastructure, not a use-case actor")
    return issues


def _shared_eligibility_include(includes: list[dict[str, Any]], use_cases: dict[str, dict[str, Any]]) -> bool:
    bases: dict[str, set[str]] = defaultdict(set)
    for item in includes:
        target = str(item.get("included_use_case_id") or "")
        base = str(item.get("base_use_case_id") or "")
        terms = set(_words(_relation_text(item, use_cases)))
        if target and base and len(terms.intersection(_E1_ELIGIBILITY_TERMS)) >= 2:
            bases[target].add(base)
    return any(len(value) >= 2 for value in bases.values())


def _optional_export_extend(extends: list[dict[str, Any]], use_cases: dict[str, dict[str, Any]]) -> bool:
    for item in extends:
        target = str(item.get("extending_use_case_id") or "")
        terms = _words(_relation_text(item, use_cases)) + _words(_use_case_text(use_cases.get(target)))
        if "export" in terms and all(
            str(item.get(field) or "").strip()
            for field in ("extension_point", "extension_point_name", "condition")
        ):
            return True
    return False


def _optional_waitlist_extend(extends: list[dict[str, Any]], use_cases: dict[str, dict[str, Any]]) -> bool:
    for item in extends:
        target = str(item.get("extending_use_case_id") or "")
        terms = _words(_relation_text(item, use_cases)) + _words(_use_case_text(use_cases.get(target)))
        if "waitlist" in terms and all(
            str(item.get(field) or "").strip()
            for field in ("extension_point", "extension_point_name", "condition")
        ):
            return True
    return False


def _records(value: Any) -> list[dict[str, Any]]:
    return [item for item in value or [] if isinstance(item, dict)] if isinstance(value, list) else []


def _is_e1_aws(state: dict[str, Any]) -> bool:
    marker = state.get("_case")
    if isinstance(marker, dict):
        marker = marker.get("caseId") or marker.get("case_id")
    marker = marker or state.get("caseId") or state.get("case_id")
    return {"e1", "aws"}.issubset(set(_words(marker)))


def _normal(value: Any) -> str:
    return " ".join(_words(value))


def _words(value: Any) -> list[str]:
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(value or ""))
    words = re.findall(r"[a-z0-9]+", text.casefold())
    return [word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word for word in words]


def _use_case_text(use_case: dict[str, Any] | None) -> str:
    return " ".join(str((use_case or {}).get(field) or "") for field in ("name", "goal", "description"))


def _relation_text(relation: dict[str, Any], use_cases: dict[str, dict[str, Any]]) -> str:
    target = str(relation.get("included_use_case_id") or relation.get("extending_use_case_id") or "")
    values = [str(value) for value in relation.values() if isinstance(value, str)]
    values.append(_use_case_text(use_cases.get(target)))
    for step in _records(relation.get("step_refs")):
        values.extend(str(value) for value in step.values() if isinstance(value, str))
    return " ".join(values)


def _has_term(value: str, terms: tuple[str, ...]) -> bool:
    normalized = _normal(value)
    return any(term in normalized for term in terms)
