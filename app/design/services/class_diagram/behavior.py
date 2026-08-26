"""Operation-first enrichment for an immutable BCE structural skeleton.

The first class call owns names, BCE stereotypes, fields, trace tags, and
relationships.  This module is deliberately unable to add or edit those facts:
it accepts only operations for classes already present in that skeleton, then
binds every parameter from a finite, ordered set of sources.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.design.schemas.class_model import BCEModel, canonical_operation_id
from app.design.services.common.structured import parse_structured


class BehaviorParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: str = Field(min_length=1)


class BehaviorOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    parameters: list[BehaviorParameter] = Field(default_factory=list)
    return_type: str = Field(default="void", alias="returnType", min_length=1)
    step_refs: list[str] = Field(alias="stepRefs", min_length=1)
    actor_entry: bool = Field(default=False, alias="actorEntry")


class BehaviorClass(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    class_name: str = Field(alias="className", min_length=1)
    operations: list[BehaviorOperation] = Field(default_factory=list)


class BehaviorSlice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    Classes: list[BehaviorClass] = Field(default_factory=list)


class EntityBehaviorClass(BaseModel):
    """A non-empty, transient completion for one or more existing Entities."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    class_name: str = Field(alias="className", min_length=1)
    operations: list[BehaviorOperation] = Field(min_length=1)


class EntityBehaviorSlice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    Classes: list[EntityBehaviorClass] = Field(min_length=1)


class BindingChoice(BaseModel):
    """The only narrow LLM decision: choose one already-enumerated source."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_ref: str = Field(alias="sourceRef", min_length=1)


_BEHAVIOR_SYSTEM = """
You add executable behaviour to an existing BCE skeleton.  Return only the
given schema.  The skeleton is fixed: use only supplied className values and
never create, delete, rename, or edit a class, field, identifier, trace tag, or
relationship.  Propose operations only; do not provide input bindings.

Work on one execution group.  For an actor-associated group, emit exactly one
actorEntry operation on a supplied Boundary and at least one supplied Control
operation.  Set actorEntry=true on that Boundary operation and false on every
other operation; the entry must cite only the supplied actor step.  For an internal
group, emit no actorEntry and start with a supplied Control.  Keep operation
stepRefs within this group.

Model a cohesive BCE collaboration, not one method per scenario sentence.
- The Boundary entry captures the actor-supplied request values with concrete,
  typed parameters and a goal-specific operation name; never call it actorEntry.
  It is the application interface, so when the goal exposes data, confirmation,
  or a decision its return type must describe that observable result rather than
  void.
- A Control operation coordinates the goal or makes a domain decision.  It may
  cover several consecutive steps and must return a concrete result when the
  actor receives data, confirmation, or a decision.
- Use only supplied Entity classes for domain queries and state changes that
  the group steps actually evidence and existing Dependency paths support.
  When completionContract.domainStateInteractionEvidenced is true and at least
  one controlEntityPath is supplied, emit an Entity operation on one such path.
  Do not emit an Entity operation merely because an Entity is in the wider
  use-case scope; a request segment that only captures, validates, or presents
  input can remain Boundary/Control-only.  Do not force every available Entity
  into a group when one cohesive domain path already realizes the stated behavior.
- Presentation, notification, and error sentences describe returned outcomes or
  sequence branches.  Do not create separate inform*, notify*, present*, or
  handle-error Control methods for each sentence.
- Reuse the same parameter name and type as a value moves from Boundary to
  Control to Entity.  Do not omit an input merely because it was described in
  prose rather than named as a programming identifier.
- Give every non-entry parameter a finite earlier source: either an earlier
  reachable operation parameter with the exact same name and type, or an
  earlier non-scalar result whose type exactly matches the parameter type.
  If one value must be transformed into another, model that transformation as
  an operation with the needed typed result; do not leave an implied mapping.
- An internal group's first Control is a reusable formal contract.  Its
  parameters are supplied at each caller, so preserve their exact names and
  types; downstream sequence behavior must resolve each caller's formal input.
- Query/read/search/find/list/fetch/view operations return the requested typed
  data.  Check/validate operations return a typed decision when later behavior
  depends on it.  A command returns a typed outcome when the actor receives a
  confirmation or rejection.  Use void only when no caller observes data,
  confirmation, or a decision.

Before returning, audit that actorEntry flags, Entity participation, parameter
propagation, and observable return types satisfy every rule above.

If the supplied dependency topology cannot support the collaboration, return no
invented class or relationship.
""".strip()

_BINDING_SYSTEM = """
Choose exactly one sourceRef from the supplied candidates for the given
operation parameter.  Do not invent, edit, or explain a sourceRef.  Return
only the schema.
""".strip()

_ENTITY_BEHAVIOR_SYSTEM = """
Complete only the missing Entity behavior for one execution group. Return only
the given schema and at least one operation. Use only an entity class named in
eligibleEntityClassNames. Do not repeat Boundary or Control operations and do
not set actorEntry=true. Keep stepRefs inside the supplied group.
Collectively cover every requiredEntityStepRefs value. One cohesive operation
may cover several consecutive required steps; do not create one method merely
for each sentence.

The current operations are fixed callers. Choose concrete parameters that have
an exact-name, exact-type finite source in an earlier Boundary or Control
parameter. A read operation returns the requested domain type; a state-changing
operation may return void when its caller observes the final outcome. Do not
invent a class, relationship, or unrelated domain operation.
""".strip()


# These are evidence terms in the scenario, not operation-name conventions.
# A group that says it retrieves, presents, or reports information has an
# observable contract even when its proposed method names are arbitrary.
_OBSERVABLE_OUTPUT = re.compile(
    r"\b(?:display|present|show|provide|return|send|notify|inform|confirm|report|reveal)(?:s|ed|ing)?\b",
    re.IGNORECASE,
)
_OBSERVABLE_QUERY = re.compile(
    r"\b(?:retrieve|fetch|find|search|query|list|browse)(?:s|ed|ing)?\b",
    re.IGNORECASE,
)
_OBSERVABLE_REQUEST = re.compile(
    r"\b(?:request|ask|view|look\s+up|check)(?:s|ed|ing)?\b.*\b(?:detail|information|data|result|status|list)\b",
    re.IGNORECASE,
)
_OBSERVABLE_DECISION = re.compile(
    r"\b(?:whether|decision|outcome|result|status|confirmation|approval|rejection|"
    r"eligible|ineligible|available|unavailable|valid|invalid|succeed(?:s|ed)?|fail(?:s|ed)?)\b",
    re.IGNORECASE,
)
_DOMAIN_STATE_INTERACTION = re.compile(
    r"\b(?:retrieve|load|fetch|find|search|query|list|browse|create|add|save|"
    r"store|persist|update|modify|change|delete|remove|cancel|reserve|release)"
    r"(?:s|d|ed|ing)?\b",
    re.IGNORECASE,
)
_SCALAR_VALUE_TYPES = frozenset({
    "bool", "boolean", "byte", "char", "character", "date", "datetime",
    "decimal", "double", "float", "guid", "instant", "int", "integer",
    "long", "number", "short", "string", "str", "time", "timestamp", "uuid",
})


@dataclass(frozen=True)
class _Step:
    id: str
    subject: str
    sentence: str
    order: int


@dataclass(frozen=True)
class _Group:
    use_case_id: str
    id: str
    step_ids: tuple[str, ...]
    actor_step: str | None
    internal: bool


@dataclass(frozen=True)
class _GroupOutcome:
    """One non-persisted enrichment result for an execution group.

    The BCE artifact stays a plain mapping at its serialization boundary.  This
    sidecar is deliberately attached only to the in-memory result so callers
    can distinguish a partial group from a group that was never attempted
    without teaching downstream artifacts a diagnostic schema.
    """

    group_id: str
    accepted_operation_ids: tuple[str, ...]
    rejected_operation_ids: tuple[str, ...]
    issues: tuple[str, ...]
    repaired: bool = False

    @property
    def status(self) -> str:
        if self.accepted_operation_ids and (self.rejected_operation_ids or self.issues):
            return "partial"
        if self.accepted_operation_ids and not self.issues:
            return "accepted"
        return "failed"


class _BehaviorArtifact(dict):
    """A dict-compatible BCE result with an intentionally transient sidecar."""


def group_outcomes(model: dict[str, Any]) -> tuple[_GroupOutcome, ...]:
    """Return in-memory group results without adding fields to the BCE artifact."""

    return tuple(getattr(model, "_behavior_group_outcomes", ()))


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _is_scalar_value_type(type_name: str) -> bool:
    """Whether a result type lacks an identity that can safely name a value."""
    return _text(type_name).casefold().replace(" ", "") in _SCALAR_VALUE_TYPES


def _class_name(item: dict[str, Any]) -> str:
    return _text(item.get("className") or item.get("class_name"))


def _stereotype(item: dict[str, Any]) -> str:
    return _text(item.get("stereotype")).replace("<", "").replace(">", "").casefold()


def _use_case_id(item: dict[str, Any]) -> str:
    return _text(item.get("use_case_id") or item.get("id"))


def _source_specification_map(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the accepted source specifications, before relationship projection."""
    return {
        _use_case_id(item): item
        for item in scenario.get("use_case_specs") or []
        if isinstance(item, dict) and _use_case_id(item)
    }


def _derived_use_case_items(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index persisted derived-use-case identities deterministically."""
    relations = scenario.get("relationships")
    if not isinstance(relations, dict):
        return {}
    values = [
        item for item in relations.get("derived_use_cases") or []
        if isinstance(item, dict) and _use_case_id(item)
    ]
    result: dict[str, dict[str, Any]] = {}
    for item in sorted(
        values,
        key=lambda value: (_use_case_id(value), json.dumps(value, ensure_ascii=False, sort_keys=True)),
    ):
        result.setdefault(_use_case_id(item), item)
    return result


def _derived_include_specifications(
    scenario: dict[str, Any], source_specs: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Create one minimal internal spec for each approved factored include.

    Relationship materialization stores the derived identity separately from the
    exact source-step and requirement evidence.  The class stage needs a finite
    internal flow without duplicating that behaviour for every include caller.
    """
    relations = scenario.get("relationships")
    if not isinstance(relations, dict):
        return {}
    derived = _derived_use_case_items(scenario)
    evidence_by_derived: dict[str, list[dict[str, Any]]] = defaultdict(list)
    requirement_ids: dict[str, set[str]] = defaultdict(set)
    for relation in relations.get("includes") or []:
        if not isinstance(relation, dict):
            continue
        derived_id = _text(relation.get("included_use_case_id"))
        item = derived.get(derived_id)
        if not item or _text(item.get("origin")).casefold() != "factored_include":
            continue
        requirement_ids[derived_id].update(
            _text(value) for value in relation.get("requirement_ids") or [] if _text(value)
        )
        evidence_by_derived[derived_id].extend(
            value for value in relation.get("step_refs") or [] if isinstance(value, dict)
        )

    result: dict[str, dict[str, Any]] = {}
    for derived_id, references in sorted(evidence_by_derived.items()):
        source_steps = {
            step.id: step
            for specification in source_specs.values()
            for step in _steps(specification)
        }
        evidence: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for reference in sorted(
            references,
            key=lambda value: (
                _text(value.get("use_case_id")),
                _text(value.get("step_ref")),
            ),
        ):
            source_id = _text(reference.get("use_case_id"))
            step_ref = _text(reference.get("step_ref"))
            source_step = source_steps.get(f"{source_id}:{step_ref}")
            key = (source_id, step_ref)
            if not source_step or key in seen:
                continue
            seen.add(key)
            evidence.append({
                "use_case_id": source_id,
                "step_ref": step_ref,
                "sentence": source_step.sentence,
                "subject_ref": source_step.subject,
                "covered_req_ids": [
                    _text(value) for value in reference.get("covered_req_ids") or [] if _text(value)
                ],
            })
        if not evidence:
            continue
        canonical = evidence[0]
        derived_item = derived[derived_id]
        result[derived_id] = {
            "use_case_id": derived_id,
            "name": _text(derived_item.get("name")),
            "requirement_ids": sorted(requirement_ids[derived_id]),
            "source_step_refs": [
                {
                    "use_case_id": item["use_case_id"],
                    "step_ref": item["step_ref"],
                    "covered_req_ids": item["covered_req_ids"],
                }
                for item in evidence
            ],
            "main_scenario": [{
                "step_number": 1,
                "subject_ref": canonical["subject_ref"],
                "sentence": canonical["sentence"],
                "covered_req_ids": canonical["covered_req_ids"],
            }],
            "extensions": [],
        }
    return result


def _specification_map(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    source_specs = _source_specification_map(scenario)
    derived_specs = _derived_include_specifications(scenario, source_specs)
    return source_specs | {
        use_case_id: specification
        for use_case_id, specification in derived_specs.items()
        if use_case_id not in source_specs
    }


def _summary_map(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _use_case_id(item): item
        for item in scenario.get("use_cases") or []
        if isinstance(item, dict) and _use_case_id(item)
    }


def _primary_actor(scenario: dict[str, Any], specification: dict[str, Any]) -> str:
    return _text(
        _summary_map(scenario).get(_use_case_id(specification), {}).get("primary_actor")
    )


def _steps(specification: dict[str, Any]) -> list[_Step]:
    use_case_id = _use_case_id(specification)
    result: list[_Step] = []
    order = 0
    for step in specification.get("main_scenario") or []:
        if not isinstance(step, dict):
            continue
        number = step.get("step_number")
        if number is None:
            continue
        result.append(_Step(
            f"{use_case_id}:main:{number}",
            _text(step.get("subject_ref")),
            _text(step.get("sentence")),
            order,
        ))
        order += 1
    for extension in specification.get("extensions") or []:
        if not isinstance(extension, dict):
            continue
        label = _text(extension.get("label"))
        for step in extension.get("handling_steps") or []:
            if not isinstance(step, dict):
                continue
            sub_step = _text(step.get("sub_step"))
            if not label or not sub_step:
                continue
            result.append(_Step(
                f"{use_case_id}:extension:{label}:{sub_step}",
                _text(step.get("subject_ref")),
                _text(step.get("sentence")),
                order,
            ))
            order += 1
    return result


def _observable_step_ids(group: _Group, scenario: dict[str, Any]) -> set[str]:
    """Return group steps whose stated effect exposes data or a decision."""
    specification = _specification_map(scenario).get(group.use_case_id, {})
    by_id = {step.id: step for step in _steps(specification)}
    return {
        step_id
        for step_id in group.step_ids
        if (step := by_id.get(step_id)) and any(
            pattern.search(step.sentence)
            for pattern in (
                _OBSERVABLE_OUTPUT,
                _OBSERVABLE_QUERY,
                _OBSERVABLE_REQUEST,
                _OBSERVABLE_DECISION,
            )
        )
    }


def _domain_state_interaction_evidenced(
    group: _Group, scenario: dict[str, Any]
) -> bool:
    """Whether this group explicitly reads or changes domain state."""

    return bool(_domain_state_step_ids(group, scenario))


def _domain_state_step_ids(group: _Group, scenario: dict[str, Any]) -> set[str]:
    """Explicit scenario steps that read or change domain state."""

    specification = _specification_map(scenario).get(group.use_case_id, {})
    by_id = {step.id: step for step in _steps(specification)}
    actor_steps = set(_actor_steps(scenario, specification))
    result: set[str] = set()
    for step_id in group.step_ids:
        step = by_id.get(step_id)
        if not step or step_id in actor_steps:
            continue
        domain_match = _DOMAIN_STATE_INTERACTION.search(step.sentence)
        if not domain_match:
            continue
        output_match = _OBSERVABLE_OUTPUT.search(step.sentence)
        # "presents the retrieved data" references an earlier read; it does
        # not perform another domain operation at the presentation step.
        if output_match and output_match.start() < domain_match.start():
            continue
        result.add(step_id)
    return result


def _actor_steps(scenario: dict[str, Any], specification: dict[str, Any]) -> list[str]:
    actor = _primary_actor(scenario, specification)
    if not actor:
        return []
    result: list[str] = []
    for step in _steps(specification):
        subject = step.subject.casefold()
        if subject and subject == actor.casefold():
            result.append(step.id)
            continue
        # Legacy use-case records have prose but no subject_ref.  Accept only a
        # leading actor mention; a later notification does not become input.
        sentence = step.sentence.casefold()
        if not subject and re.match(rf"^(?:the )?{re.escape(actor.casefold())}\b", sentence):
            result.append(step.id)
    return result


def _resolve_use_case_ids(scenario: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Return execution roots and internal relationship children.

    Every accepted specification starts as a root.  Only an include child, or
    an extend child without its own actor entry, is proven internal.  A partial
    relationship artifact must never hide an otherwise accepted specification.
    """
    specifications = _specification_map(scenario)
    pairs = relationship_pairs(scenario)
    roots = set(specifications)
    if not isinstance(scenario.get("relationships"), dict):
        return roots, set()
    internal = {
        child for kind, _base, child in pairs
        if kind == "include" or not _actor_steps(scenario, specifications.get(child, {}))
    }
    return roots - internal, internal


def _use_case_aliases(scenario: dict[str, Any]) -> dict[str, str]:
    names: dict[str, set[str]] = defaultdict(set)
    for collection in (_summary_map(scenario), _specification_map(scenario)):
        for use_case_id, item in collection.items():
            names[use_case_id.casefold()].add(use_case_id)
            name = _text(item.get("name") or item.get("use_case_name") or item.get("useCaseName"))
            if name:
                names[name.casefold()].add(use_case_id)
    for use_case_id, item in _derived_use_case_items(scenario).items():
        names[use_case_id.casefold()].add(use_case_id)
        name = _text(item.get("name") or item.get("use_case_name") or item.get("useCaseName"))
        if name:
            names[name.casefold()].add(use_case_id)
    return {
        name: next(iter(ids))
        for name, ids in names.items()
        if len(ids) == 1
    }

def relationship_pairs(scenario: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Canonical include/extend caller-to-child pairs from the requirement graph."""
    relations = scenario.get("relationships")
    if not isinstance(relations, dict):
        return []
    aliases = _use_case_aliases(scenario)
    result: list[tuple[str, str, str]] = []
    for kind, collection, stable_child_key, child_key in (
        ("include", "includes", "included_use_case_id", ("included_use_case", "includedUseCase")),
        ("extend", "extends", "extending_use_case_id", ("extending_use_case", "extendingUseCase")),
    ):
        for item in relations.get(collection) or []:
            if not isinstance(item, dict):
                continue
            stable_base = _text(item.get("base_use_case_id"))
            stable_child = _text(item.get(stable_child_key))
            base = aliases.get(stable_base.casefold()) if stable_base else aliases.get(
                _text(item.get("base_use_case") or item.get("baseUseCase")).casefold()
            )
            child = aliases.get(stable_child.casefold()) if stable_child else aliases.get(
                _text(next((item.get(key) for key in child_key if item.get(key)), None)).casefold()
            )
            if not base or not child or base == child:
                continue
            result.append((kind, base, child))
    return result


def execution_groups(scenario: dict[str, Any]) -> list[_Group]:
    """Canonical, finite request segments derived from the use-case graph."""
    roots, internal_ids = _resolve_use_case_ids(scenario)
    groups: list[_Group] = []
    for use_case_id, specification in sorted(_specification_map(scenario).items()):
        steps = _steps(specification)
        actor_steps = set(_actor_steps(scenario, specification))
        if use_case_id in internal_ids:
            groups.append(_Group(use_case_id, f"{use_case_id}:internal", tuple(step.id for step in steps), None, True))
            continue
        if use_case_id not in roots:
            continue
        active: str | None = None
        grouped: dict[str, list[str]] = {}
        previous_was_actor = False
        for step in steps:
            is_actor_step = step.id in actor_steps
            # Consecutive actor-authored sentences are one request contract.
            # A later actor sentence starts a new segment only after the system
            # has answered, which is the observable request/response boundary.
            if is_actor_step and (active is None or not previous_was_actor):
                active = step.id
                grouped.setdefault(active, [])
            if active:
                grouped[active].append(step.id)
            previous_was_actor = is_actor_step
        if not grouped:
            groups.append(_Group(use_case_id, f"{use_case_id}:root", tuple(step.id for step in steps), None, False))
        for actor_step, refs in grouped.items():
            groups.append(_Group(use_case_id, actor_step, tuple(refs), actor_step, False))
    return groups


def _class_in_scope(item: dict[str, Any], use_case_id: str) -> bool:
    """Whether a class explicitly participates in one use case."""
    return use_case_id in {_text(value) for value in item.get("use_case_ids") or []}


def _scope_classes(skeleton: dict[str, Any], use_case_id: str) -> list[dict[str, Any]]:
    """Return only classes explicitly scoped to the execution group."""
    result: list[dict[str, Any]] = []
    for item in skeleton.get("Classes") or []:
        if isinstance(item, dict) and _class_in_scope(item, use_case_id):
            result.append(item)
    return result


def _dependencies(skeleton: dict[str, Any]) -> dict[str, set[str]]:
    edges: dict[str, set[str]] = defaultdict(set)
    for relation in skeleton.get("Relationships") or []:
        if not isinstance(relation, dict) or _text(relation.get("type") or "Association").casefold() != "dependency":
            continue
        source, target = _text(relation.get("source")), _text(relation.get("target"))
        if source and target:
            edges[source].add(target)
    return edges


def _reachable(edges: dict[str, set[str]], source: str, target: str) -> bool:
    if source == target:
        return True
    queue = deque([source])
    seen = {source}
    while queue:
        node = queue.popleft()
        for child in edges.get(node, set()):
            if child == target:
                return True
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return False


def _group_payload(skeleton: dict[str, Any], group: _Group, scenario: dict[str, Any]) -> dict[str, Any]:
    specification = _specification_map(scenario).get(group.use_case_id, {})
    by_id = {step.id: step for step in _steps(specification)}
    classes = _scope_classes(skeleton, group.use_case_id)
    names = {_class_name(item) for item in classes}
    boundaries = [
        _class_name(item) for item in classes if _stereotype(item) == "boundary"
    ]
    controls = [
        _class_name(item) for item in classes if _stereotype(item) == "control"
    ]
    entities = [
        _class_name(item) for item in classes if _stereotype(item) == "entity"
    ]
    dependencies = _dependencies(skeleton)
    control_entity_paths = [
        {"control": control, "entity": entity}
        for control in controls
        for entity in entities
        if _reachable(dependencies, control, entity)
    ]
    return {
        "useCaseId": group.use_case_id,
        "groupId": group.id,
        "internalFlow": group.internal,
        "actorStep": group.actor_step,
        "steps": [
            {"id": ref, "sentence": by_id[ref].sentence}
            for ref in group.step_ids if ref in by_id
        ],
        "evidence": {
            "sourceStepRefs": specification.get("source_step_refs") or [],
            "requirementIds": specification.get("requirement_ids") or [],
        },
        "classes": [
            {"className": _class_name(item), "stereotype": item.get("stereotype"), "fields": item.get("fields") or []}
            for item in classes
        ],
        "dependencies": [
            {"source": source, "target": target}
            for source, targets in sorted(_dependencies(skeleton).items())
            for target in sorted(targets) if source in names and target in names
        ],
        "completionContract": {
            "actorEntryRequired": not group.internal,
            "actorStepRef": group.actor_step,
            "boundaryClassNames": boundaries,
            "controlClassNames": controls,
            "controlEntityPaths": control_entity_paths,
            "domainStateInteractionEvidenced": _domain_state_interaction_evidenced(
                group, scenario
            ),
        },
    }


def _propose_group(skeleton: dict[str, Any], group: _Group, scenario: dict[str, Any]) -> dict[str, Any]:
    payload = _group_payload(skeleton, group, scenario)
    return BehaviorSlice.model_validate(parse_structured([
        {"role": "system", "content": _BEHAVIOR_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ], BehaviorSlice, reasoning_effort="medium")).model_dump(by_alias=True)


def _entity_completion_required(
    attempt: _GroupAttempt,
    skeleton: dict[str, Any],
    group: _Group,
    scenario: dict[str, Any],
) -> bool:
    """Whether a grounded domain interaction lacks its Entity operation."""

    classes = {
        _class_name(item): item
        for item in _scope_classes(skeleton, group.use_case_id)
    }
    covered_steps = {
        _text(step_ref)
        for class_name, operation in attempt.operations
        if _stereotype(classes.get(class_name, {})) == "entity"
        for step_ref in operation.get("stepRefs") or []
    }
    if not (_domain_state_step_ids(group, scenario) - covered_steps):
        return False
    controls = [
        name for name, item in classes.items() if _stereotype(item) == "control"
    ]
    entities = [
        name for name, item in classes.items() if _stereotype(item) == "entity"
    ]
    edges = _dependencies(skeleton)
    return any(
        _reachable(edges, control, entity)
        for control in controls
        for entity in entities
    )


def _complete_entity_group(
    skeleton: dict[str, Any],
    group: _Group,
    scenario: dict[str, Any],
    current: _GroupAttempt,
) -> dict[str, Any]:
    payload = _group_payload(skeleton, group, scenario)
    paths = payload["completionContract"]["controlEntityPaths"]
    classes = {
        _class_name(item): item
        for item in _scope_classes(skeleton, group.use_case_id)
    }
    covered_steps = {
        _text(step_ref)
        for class_name, operation in current.operations
        if _stereotype(classes.get(class_name, {})) == "entity"
        for step_ref in operation.get("stepRefs") or []
    }
    payload |= {
        "currentOperations": _slice_from_operations(current.operations),
        "eligibleEntityClassNames": sorted({path["entity"] for path in paths}),
        "requiredEntityStepRefs": sorted(
            _domain_state_step_ids(group, scenario) - covered_steps
        ),
    }
    return EntityBehaviorSlice.model_validate(parse_structured([
        {"role": "system", "content": _ENTITY_BEHAVIOR_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ], EntityBehaviorSlice, reasoning_effort="low")).model_dump(by_alias=True)


def _repair_group(
    skeleton: dict[str, Any],
    group: _Group,
    scenario: dict[str, Any],
    proposal: dict[str, Any],
    issues: list[str],
    repair_operation_ids: set[str],
    preserve_operation_ids: set[str],
    *,
    reasoning_effort: str = "medium",
) -> dict[str, Any]:
    payload = _group_payload(skeleton, group, scenario) | {
        "currentProposal": proposal,
        "deterministicIssue": issues[0] if issues else "",
        "deterministicIssues": issues,
        "repairScope": {
            "operationIds": sorted(repair_operation_ids),
            "preserveOperationIds": sorted(preserve_operation_ids),
        },
    }
    return BehaviorSlice.model_validate(parse_structured([
        {
            "role": "system",
            "content": _BEHAVIOR_SYSTEM + (
                "\nCorrect only the listed operations or add the smallest operation needed "
                "for the stated issue. Preserve every operation outside repairScope exactly."
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ], BehaviorSlice, reasoning_effort=reasoning_effort)).model_dump(by_alias=True)


def _operation_id(class_name: str, operation: dict[str, Any]) -> str:
    parameters = [
        parameter
        for parameter in operation.get("parameters") or [] if isinstance(parameter, dict)
    ]
    return canonical_operation_id(class_name, _text(operation.get("name")), parameters)


def _operation_family(operation_id: str) -> str:
    """Class and method identity without one proposed parameter contract."""

    return _text(operation_id).partition("(")[0]


def _operation_first_step(operation: dict[str, Any], order: dict[str, int]) -> int:
    """Earliest scenario step an operation can execute at."""
    refs = [_text(ref) for ref in operation.get("stepRefs") or []]
    return min((order.get(ref, 10**9) for ref in refs), default=10**9)


def _operation_sort_key(operation: tuple[str, dict[str, Any]], order: dict[str, int]) -> tuple[int, str, str]:
    """Stable tie-breaker once data-flow edges have fixed their order."""
    class_name, item = operation
    return _operation_first_step(item, order), class_name, _text(item.get("operationId"))


def _binding_source_operation_id(source_ref: str, operation_ids: set[str]) -> str | None:
    """Return an operation producer id, excluding actor-step input sources."""
    candidate = source_ref.rsplit("#", 1)[0] if "#" in source_ref else source_ref
    return candidate if candidate in operation_ids else None


def _formal_callsite_source_ref(group_id: str, parameter_name: str) -> str:
    """Name one internal Control formal whose caller supplies the value later."""
    return f"callsite:{group_id}#{parameter_name}"


def _topological_operation_order(
    operations: dict[str, tuple[str, dict[str, Any]]],
    order: dict[str, int],
    binding_edges: set[tuple[str, str]],
) -> tuple[list[str], set[str]]:
    """Order operations by declared value flow with deterministic scenario ties."""
    successors: dict[str, set[str]] = defaultdict(set)
    indegree = dict.fromkeys(operations, 0)
    for source_id, target_id in binding_edges:
        if (
            source_id not in operations
            or target_id not in operations
            or target_id in successors[source_id]
        ):
            continue
        successors[source_id].add(target_id)
        indegree[target_id] += 1

    def key(operation_id: str) -> tuple[int, str, str]:
        return _operation_sort_key(operations[operation_id], order)

    ready = sorted((operation_id for operation_id, degree in indegree.items() if degree == 0), key=key)
    result: list[str] = []
    while ready:
        operation_id = ready.pop(0)
        result.append(operation_id)
        for successor in sorted(successors[operation_id], key=key):
            indegree[successor] -= 1
            if indegree[successor] == 0:
                ready.append(successor)
        ready.sort(key=key)
    cyclic = set(operations) - set(result)
    return result + sorted(cyclic, key=key), cyclic


def _execution_order_edges(
    group: _Group,
    operations: dict[str, tuple[str, dict[str, Any]]],
    step_order: dict[str, int],
    dependencies: dict[str, set[str]],
    stereotypes: dict[str, str],
) -> set[tuple[str, str]]:
    """Add only the BCE call-order facts that data bindings do not express."""
    entries = [
        (operation_id, class_name, operation)
        for operation_id, (class_name, operation) in operations.items()
        if operation.get("actorEntry")
    ]
    controls = [
        (operation_id, class_name, operation)
        for operation_id, (class_name, operation) in operations.items()
        if stereotypes.get(class_name) == "control"
    ]
    entities = [
        (operation_id, class_name, operation)
        for operation_id, (class_name, operation) in operations.items()
        if stereotypes.get(class_name) == "entity"
    ]
    result: set[tuple[str, str]] = set()
    for entry_id, entry_class, entry in entries:
        for control_id, control_class, control in controls:
            if (
                _operation_first_step(entry, step_order) <= _operation_first_step(control, step_order)
                and _reachable(dependencies, entry_class, control_class)
            ):
                result.add((entry_id, control_id))

    operation_ids = set(operations)

    def has_entity_result_input(operation: dict[str, Any]) -> bool:
        for binding in operation.get("inputBindings") or []:
            if not isinstance(binding, dict):
                continue
            source_ref = _text(binding.get("sourceRef"))
            source_id = _binding_source_operation_id(source_ref, operation_ids)
            if (
                source_id
                and "#" not in source_ref
                and stereotypes.get(operations[source_id][0]) == "entity"
            ):
                return True
        return False

    initial_controls = [item for item in controls if not has_entity_result_input(item[2])]
    if initial_controls:
        control_id, control_class, control = min(
            initial_controls,
            key=lambda item: _operation_sort_key((item[1], item[2]), step_order),
        )
        for entity_id, entity_class, entity in entities:
            if (
                _operation_first_step(control, step_order) <= _operation_first_step(entity, step_order)
                and _reachable(dependencies, control_class, entity_class)
            ):
                result.add((control_id, entity_id))
    return result


def _ranked_collaboration_issue(
    group: _Group,
    ordered_ids: list[str],
    operations: dict[str, tuple[str, dict[str, Any]]],
    dependencies: dict[str, set[str]],
    stereotypes: dict[str, str],
    scoped_entity_names: set[str],
) -> str | None:
    """Require the accepted rank to express the BCE collaboration, not just paths."""
    ranks = {operation_id: index for index, operation_id in enumerate(ordered_ids)}
    entries = [
        (operation_id, class_name)
        for operation_id, (class_name, operation) in operations.items()
        if operation.get("actorEntry")
    ]
    controls = [
        (operation_id, class_name)
        for operation_id, (class_name, _operation) in operations.items()
        if stereotypes.get(class_name) == "control"
    ]
    entities = [
        (operation_id, class_name)
        for operation_id, (class_name, _operation) in operations.items()
        if stereotypes.get(class_name) == "entity"
    ]
    if not group.internal and entries and not any(
        ranks[entry_id] < ranks[control_id]
        and _reachable(dependencies, entry_class, control_class)
        for entry_id, entry_class in entries
        for control_id, control_class in controls
    ):
        return f"execution order must place Boundary entry before reachable Control in {group.id}"
    if scoped_entity_names and not any(
        ranks[control_id] < ranks[entity_id]
        and _reachable(dependencies, control_class, entity_class)
        for control_id, control_class in controls
        for entity_id, entity_class in entities
    ):
        return f"execution order must place Control before reachable Entity in {group.id}"
    return None


def _observable_return_issue(
    group: _Group,
    accepted: list[tuple[str, dict[str, Any]]],
    entries: list[tuple[str, dict[str, Any]]],
    controls: list[tuple[str, dict[str, Any]]],
    scenario: dict[str, Any],
) -> str | None:
    """Reject void results where the requirement itself states an observation."""
    observable_steps = _observable_step_ids(group, scenario)
    if not observable_steps:
        return None
    invalid_ids = {
        operation["operationId"]
        for _class_name, operation in accepted
        if _text(operation.get("returnType")).casefold() == "void"
        and set(operation.get("stepRefs") or []) & observable_steps
    }
    invalid_ids.update(
        operation["operationId"]
        for _class_name, operation in entries
        if _text(operation.get("returnType")).casefold() == "void"
    )
    if controls and not any(
        _text(operation.get("returnType")).casefold() != "void"
        for _class_name, operation in controls
    ):
        invalid_ids.update(operation["operationId"] for _class_name, operation in controls)
    if not invalid_ids:
        return None
    return (
        f"observable scenario steps {sorted(observable_steps)} require concrete return "
        f"types, not void, for {sorted(invalid_ids)}"
    )


def _group_contract_issues(
    group: _Group,
    operations: list[tuple[str, dict[str, Any]]],
    ordered_ids: list[str],
    class_by_name: dict[str, dict[str, Any]],
    edges: dict[str, set[str]],
    scenario: dict[str, Any],
) -> list[str]:
    """Return the pure, shared BCE group-contract checks.

    Enrichment uses these checks to decide whether a group is safe to merge;
    persisted-model validation calls the same function below.  Keeping this
    logic in one place prevents a successful repair from being rejected later
    by a subtly different copy of the rule.
    """

    members = {
        operation["operationId"]: (class_name, operation)
        for class_name, operation in operations
    }
    entries = [
        (operation_id, class_name, operation)
        for operation_id, (class_name, operation) in members.items()
        if operation.get("actorEntry")
    ]
    controls = [
        (operation_id, class_name, operation)
        for operation_id, (class_name, operation) in members.items()
        if _stereotype(class_by_name.get(class_name, {})) == "control"
    ]
    entity_operations = [
        (operation_id, class_name, operation)
        for operation_id, (class_name, operation) in members.items()
        if _stereotype(class_by_name.get(class_name, {})) == "entity"
    ]
    scoped_controls = [
        class_name
        for class_name, item in class_by_name.items()
        if _stereotype(item) == "control"
    ]
    scoped_entities = [
        class_name
        for class_name, item in class_by_name.items()
        if _stereotype(item) == "entity"
    ]
    issues: list[str] = []
    if group.internal:
        if entries or not controls:
            issues.append("internal execution group requires Control behavior and no actorEntry")
    else:
        valid_entry = (
            len(entries) == 1
            and _stereotype(class_by_name.get(entries[0][1], {})) == "boundary"
            and set(entries[0][2].get("stepRefs") or []) == {group.actor_step}
        )
        path = valid_entry and any(
            _reachable(edges, entries[0][1], control[1])
            for control in controls
        )
        if not valid_entry or not controls or not path:
            issues.append("execution root lacks one reachable Boundary-to-Control operation path")
    domain_state_steps = _domain_state_step_ids(group, scenario)
    covered_entity_steps = {
        _text(step_ref)
        for _operation_id, _class_name, operation in entity_operations
        for step_ref in operation.get("stepRefs") or []
    }
    missing_entity_steps = sorted(domain_state_steps - covered_entity_steps)
    if (
        missing_entity_steps
        and any(
            _reachable(edges, control, entity)
            for control in scoped_controls
            for entity in scoped_entities
        )
    ):
        issues.append(
            "domain-state steps require reachable Entity operations: "
            + ", ".join(missing_entity_steps)
        )
    participating_entities = {class_name for _operation_id, class_name, _operation in entity_operations}
    if participating_entities and not any(
        _reachable(edges, control[1], entity[1])
        for control in controls for entity in entity_operations
    ):
        issues.append("execution group has domain Entities but no reachable Entity operation")
    stereotypes = {
        class_name: _stereotype(class_by_name.get(class_name, {}))
        for class_name, _operation in members.values()
    }
    if issue := _ranked_collaboration_issue(
        group, ordered_ids, members, edges, stereotypes, participating_entities
    ):
        issues.append(issue)
    if issue := _observable_return_issue(
        group,
        operations,
        [(class_name, operation) for _operation_id, class_name, operation in entries],
        [(class_name, operation) for _operation_id, class_name, operation in controls],
        scenario,
    ):
        issues.append(issue)
    return issues


def _merge_operations(
    existing: dict[str, Any], candidate: dict[str, Any], group: _Group,
) -> str | None:
    """Merge duplicate canonical IDs only when their executable contract agrees."""

    if (
        _text(existing.get("returnType")) != _text(candidate.get("returnType"))
        or bool(existing.get("actorEntry")) != bool(candidate.get("actorEntry"))
    ):
        return f"conflicting contracts for {existing['operationId']}"
    existing_bindings = {
        (
            _text(binding.get("useCaseId")),
            _text(binding.get("parameter")),
            _text(binding.get("sourceRef")),
        )
        for binding in existing.get("inputBindings") or [] if isinstance(binding, dict)
    }
    candidate_bindings = {
        (
            _text(binding.get("useCaseId")),
            _text(binding.get("parameter")),
            _text(binding.get("sourceRef")),
        )
        for binding in candidate.get("inputBindings") or [] if isinstance(binding, dict)
    }
    if existing_bindings and candidate_bindings and existing_bindings != candidate_bindings:
        return f"conflicting input bindings for {existing['operationId']}"
    if not existing_bindings and candidate_bindings:
        existing["inputBindings"] = deepcopy(candidate.get("inputBindings") or [])
    step_order = {step_id: index for index, step_id in enumerate(group.step_ids)}
    existing["stepRefs"] = sorted(
        set(existing.get("stepRefs") or []) | set(candidate.get("stepRefs") or []),
        key=step_order.get,
    )
    return None


def _validate_slice(
    skeleton: dict[str, Any], group: _Group, proposal: dict[str, Any], scenario: dict[str, Any]
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, str]]:
    """Keep independently valid operations instead of rejecting a whole slice.

    The returned rejection map is private orchestration state.  It lets repair
    focus on the bad operation while all valid siblings remain candidates for
    the final merge.
    """

    classes = {_class_name(item): item for item in _scope_classes(skeleton, group.use_case_id)}
    accepted: list[tuple[str, dict[str, Any]]] = []
    by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    rejected: dict[str, str] = {}
    for class_item in proposal.get("Classes") or []:
        if not isinstance(class_item, dict):
            rejected["<class>"] = "malformed behavior class"
            continue
        class_name = _text(class_item.get("className"))
        skeleton_class = classes.get(class_name)
        if not skeleton_class:
            rejected[class_name or "<class>"] = f"unknown or out-of-scope class {class_name}"
            continue
        stereotype = _stereotype(skeleton_class)
        for position, raw in enumerate(class_item.get("operations") or []):
            location = f"{class_name}[{position}]"
            if not isinstance(raw, dict):
                rejected[location] = f"malformed operation on {class_name}"
                continue
            refs = [_text(value) for value in raw.get("stepRefs") or []]
            candidate_id = _operation_id(class_name, raw)
            if not refs or set(refs) - set(group.step_ids):
                rejected[candidate_id] = f"operation {class_name} has stepRefs outside {group.id}"
                continue
            parameters = raw.get("parameters") or []
            if len({_text(item.get("name")) for item in parameters if isinstance(item, dict)}) != len(parameters):
                rejected[candidate_id] = f"operation {class_name} has duplicate parameters"
                continue
            operation = {
                "operationId": _operation_id(class_name, raw),
                "name": _text(raw.get("name")),
                "parameters": [
                    {"name": _text(item.get("name")), "type": _text(item.get("type"))}
                    for item in parameters if isinstance(item, dict)
                ],
                "returnType": _text(raw.get("returnType")) or "void",
                "stepRefs": sorted(set(refs), key=group.step_ids.index),
                "actorEntry": bool(raw.get("actorEntry")),
                "inputBindings": [],
                "_stereotype": stereotype,
            }
            if not operation["name"] or any(not item["name"] or not item["type"] for item in operation["parameters"]):
                rejected[operation["operationId"]] = f"invalid operation signature on {class_name}"
                continue
            if operation["actorEntry"]:
                if group.internal or stereotype != "boundary" or set(operation["stepRefs"]) != {group.actor_step}:
                    rejected[operation["operationId"]] = f"invalid actor entry {operation['operationId']}"
                    continue
            elif stereotype == "entity" and group.actor_step in set(operation["stepRefs"]):
                rejected[operation["operationId"]] = (
                    f"Entity operation cannot stand in for actor entry step {group.actor_step}"
                )
                continue
            existing = by_id.get(operation["operationId"])
            if not existing:
                by_id[operation["operationId"]] = (class_name, operation)
                accepted.append((class_name, operation))
                continue
            if issue := _merge_operations(existing[1], operation, group):
                rejected[operation["operationId"]] = issue
                by_id.pop(operation["operationId"], None)
                accepted = [
                    item for item in accepted
                    if item[1]["operationId"] != operation["operationId"]
                ]
    return accepted, rejected


def _binding_candidates(
    operation: tuple[str, dict[str, Any]],
    sources: list[tuple[str, dict[str, Any]]],
    group: _Group,
    edges: dict[str, set[str]],
    parameter: dict[str, Any],
    step_order: dict[str, int] | None = None,
) -> list[str]:
    class_name, target = operation
    name, type_name = _text(parameter.get("name")), _text(parameter.get("type"))
    if target.get("actorEntry"):
        return [f"{group.actor_step}#{name}"] if group.actor_step else []
    target_step = _operation_first_step(target, step_order or {})
    candidates: list[str] = []
    for source_class, source in sources:
        if source.get("operationId") == target.get("operationId"):
            continue
        if _operation_first_step(source, step_order or {}) > target_step:
            continue
        for source_parameter in source.get("parameters") or []:
            if (
                _reachable(edges, source_class, class_name)
                and _text(source_parameter.get("name")) == name
                and _text(source_parameter.get("type")) == type_name
            ):
                candidates.append(f"{source['operationId']}#{name}")
        if (
            _text(source.get("returnType")).casefold() != "void"
            and _text(source.get("returnType")) == type_name
            and not _is_scalar_value_type(type_name)
            and (
                _reachable(edges, source_class, class_name)
                or _reachable(edges, class_name, source_class)
            )
        ):
            candidates.append(_text(source.get("operationId")))
    return sorted(set(candidates))


def _choose_source(operation: dict[str, Any], parameter: dict[str, Any], candidates: list[str]) -> str | None:
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None
    payload = {
        "operationId": operation["operationId"],
        "parameter": {"name": parameter["name"], "type": parameter["type"]},
        "candidates": candidates,
    }
    selected = BindingChoice.model_validate(parse_structured([
        {"role": "system", "content": _BINDING_SYSTEM},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ], BindingChoice, reasoning_effort="low")).source_ref
    return selected if selected in candidates else None


def _bind_group(
    operations: list[tuple[str, dict[str, Any]]],
    group: _Group,
    scenario: dict[str, Any],
    skeleton: dict[str, Any],
) -> tuple[list[tuple[str, dict[str, Any]]], dict[str, str]]:
    """Bind the dependency-closed subset that has finite producer sources.

    A failed producer is removed together with every operation that can only
    consume it.  Unrelated operations remain eligible for persistence instead
    of inheriting the whole group's failure.
    """

    order = {
        step.id: step.order
        for step in _steps(_specification_map(scenario).get(group.use_case_id, {}))
    }
    original_order = sorted(operations, key=lambda item: _operation_sort_key(item, order))
    survivors = {
        operation["operationId"]: (class_name, operation)
        for class_name, operation in original_order
    }
    rejected: dict[str, str] = {}
    edges = _dependencies(skeleton)
    while survivors:
        ordered = [
            item for item in original_order
            if item[1]["operationId"] in survivors
        ]
        for _class_name, operation in ordered:
            operation["inputBindings"] = []
        internal_entry_id = next(
            (
                operation["operationId"]
                for _class_name, operation in ordered
                if operation.get("_stereotype") == "control"
            ),
            None,
        ) if group.internal else None
        established: set[str] = set()
        pending = {operation["operationId"] for _class_name, operation in ordered}
        for _class_name, operation in ordered:
            operation_id = operation["operationId"]
            if operation.get("actorEntry"):
                operation["inputBindings"] = [
                    {
                        "useCaseId": group.use_case_id,
                        "parameter": parameter["name"],
                        "sourceRef": f"{group.actor_step}#{parameter['name']}",
                    }
                    for parameter in operation.get("parameters") or []
                ]
            elif operation_id == internal_entry_id:
                operation["inputBindings"] = [
                    {
                        "useCaseId": group.use_case_id,
                        "parameter": parameter["name"],
                        "sourceRef": _formal_callsite_source_ref(group.id, parameter["name"]),
                    }
                    for parameter in operation.get("parameters") or []
                ]
            else:
                continue
            established.add(operation_id)
            pending.remove(operation_id)

        retry = False
        while pending:
            progressed = False
            sources = [survivors[operation_id] for operation_id in established]
            for class_name, operation in ordered:
                operation_id = operation["operationId"]
                if operation_id not in pending:
                    continue
                candidate_sets = [
                    (parameter, _binding_candidates(
                        (class_name, operation), sources, group, edges, parameter, order,
                    ))
                    for parameter in operation.get("parameters") or []
                ]
                if any(not candidates for _parameter, candidates in candidate_sets):
                    continue
                bindings: list[dict[str, str]] = []
                try:
                    for parameter, candidates in candidate_sets:
                        selected = _choose_source(operation, parameter, candidates)
                        if not selected:
                            raise ValueError(
                                f"no valid established source for {operation_id}#{parameter['name']}"
                            )
                        bindings.append({
                            "useCaseId": group.use_case_id,
                            "parameter": parameter["name"],
                            "sourceRef": selected,
                        })
                except Exception as error:  # noqa: BLE001 - converted to a local group outcome
                    rejected[operation_id] = _text(error) or "input binding source selection failed"
                    survivors.pop(operation_id, None)
                    retry = True
                    break
                operation["inputBindings"] = bindings
                established.add(operation_id)
                pending.remove(operation_id)
                progressed = True
                sources.append((class_name, operation))
            if retry:
                break
            if progressed:
                continue
            for operation_id in sorted(pending):
                operation = survivors[operation_id][1]
                parameter = next(iter(operation.get("parameters") or []), {"name": "input"})
                rejected[operation_id] = (
                    f"no finite established source for {operation_id}#{parameter['name']}"
                )
                survivors.pop(operation_id, None)
            retry = True
            break
        if retry:
            continue
        binding_edges = {
            (source_id, operation["operationId"])
            for _class_name, operation in ordered
            for binding in operation.get("inputBindings") or []
            if (source_id := _binding_source_operation_id(
                _text(binding.get("sourceRef")), set(survivors)
            ))
        }
        _binding_order, cyclic = _topological_operation_order(survivors, order, binding_edges)
        if not cyclic:
            return ordered, rejected
        dropped = set(cyclic)
        while True:
            dependents = {
                target_id for source_id, target_id in binding_edges
                if source_id in dropped
            }
            new = dependents - dropped
            if not new:
                break
            dropped.update(new)
        for operation_id in dropped:
            rejected[operation_id] = (
                f"operation input bindings form a cycle in {group.id}: {sorted(cyclic)}"
            )
            survivors.pop(operation_id, None)
    return [], rejected


@dataclass
class _GroupAttempt:
    operations: list[tuple[str, dict[str, Any]]]
    rejected: dict[str, str]
    issues: list[str]


@dataclass
class _GroupProcess:
    group: _Group
    operations: list[tuple[str, dict[str, Any]]]
    rejected: dict[str, str]
    issues: list[str]
    repaired: bool


def _slice_from_operations(
    operations: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Project accepted candidates back to the bounded LLM repair schema."""

    by_class: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for class_name, operation in operations:
        by_class[class_name].append({
            "name": _text(operation.get("name")),
            "parameters": deepcopy(operation.get("parameters") or []),
            "returnType": _text(operation.get("returnType")) or "void",
            "stepRefs": list(operation.get("stepRefs") or []),
            "actorEntry": bool(operation.get("actorEntry")),
        })
    return {
        "Classes": [
            {"className": class_name, "operations": values}
            for class_name, values in sorted(by_class.items())
        ]
    }


def _ordered_group_ids(
    operations: list[tuple[str, dict[str, Any]]],
    group: _Group,
    scenario: dict[str, Any],
    skeleton: dict[str, Any],
) -> tuple[list[str], str | None]:
    order = {
        step.id: step.order
        for step in _steps(_specification_map(scenario).get(group.use_case_id, {}))
    }
    members = {
        operation["operationId"]: (class_name, operation)
        for class_name, operation in operations
    }
    binding_edges = {
        (source_id, operation["operationId"])
        for _class_name, operation in operations
        for binding in operation.get("inputBindings") or []
        if (source_id := _binding_source_operation_id(
            _text(binding.get("sourceRef")), set(members)
        ))
    }
    stereotypes = {
        class_name: _text(operation.get("_stereotype"))
        for class_name, operation in members.values()
    }
    execution_edges = _execution_order_edges(
        group, members, order, _dependencies(skeleton), stereotypes
    )
    ordered_ids, cyclic = _topological_operation_order(
        members, order, binding_edges | execution_edges
    )
    if cyclic:
        return ordered_ids, (
            f"operation execution order forms a cycle in {group.id}: {sorted(cyclic)}"
        )
    return ordered_ids, None


def _inspect_group(
    skeleton: dict[str, Any],
    group: _Group,
    proposal: dict[str, Any],
    scenario: dict[str, Any],
) -> _GroupAttempt:
    operations, rejected = _validate_slice(skeleton, group, proposal, scenario)
    operations, binding_rejections = _bind_group(operations, group, scenario, skeleton)
    rejected.update(binding_rejections)
    ordered_ids, ordering_issue = _ordered_group_ids(operations, group, scenario, skeleton)
    class_by_name = {
        _class_name(item): item
        for item in _scope_classes(skeleton, group.use_case_id)
    }
    issues = _group_contract_issues(
        group, operations, ordered_ids, class_by_name, _dependencies(skeleton), scenario
    )
    if ordering_issue:
        issues.append(ordering_issue)
    return _GroupAttempt(operations, rejected, issues)


def _repair_scope(
    attempt: _GroupAttempt,
    group: _Group,
    scenario: dict[str, Any],
    skeleton: dict[str, Any],
) -> set[str]:
    """Identify the smallest existing contracts a repair may replace."""

    scope = {operation_id for operation_id in attempt.rejected if "::" in operation_id}
    operation_classes = {
        operation["operationId"]: class_name
        for class_name, operation in attempt.operations
    }
    class_by_name = {
        _class_name(item): item
        for item in _scope_classes(skeleton, group.use_case_id)
    }
    # A Control that can only be called with an Entity result cannot be the
    # initial Control on a BCE path.  Its signature is the smallest mutable
    # contract; the Entity producer and all unrelated operations stay frozen.
    scope.update(
        operation["operationId"]
        for class_name, operation in attempt.operations
        if _stereotype(class_by_name.get(class_name, {})) == "control"
        and any(
            (
                source_id := _binding_source_operation_id(
                    _text(binding.get("sourceRef")), set(operation_classes)
                )
            )
            and _stereotype(
                class_by_name.get(operation_classes.get(source_id, ""), {})
            ) == "entity"
            for binding in operation.get("inputBindings") or []
            if isinstance(binding, dict)
        )
    )
    observable_steps = _observable_step_ids(group, scenario)
    if observable_steps:
        scope.update(
            operation["operationId"]
            for _class_name, operation in attempt.operations
            if _text(operation.get("returnType")).casefold() == "void"
            and (
                operation.get("actorEntry")
                or set(operation.get("stepRefs") or []) & observable_steps
            )
        )
    return scope


def _combine_repair(
    original: _GroupAttempt,
    repaired_proposal: dict[str, Any],
    repair_scope: set[str],
    skeleton: dict[str, Any],
    group: _Group,
    scenario: dict[str, Any],
) -> _GroupAttempt:
    """Accept a repair only inside scope, retaining validated sibling contracts."""

    repaired, rejected = _validate_slice(skeleton, group, repaired_proposal, scenario)
    originals = {
        operation["operationId"]: (class_name, deepcopy(operation))
        for class_name, operation in original.operations
    }
    combined: dict[str, tuple[str, dict[str, Any]]] = {
        operation_id: item
        for operation_id, item in originals.items()
        if operation_id not in repair_scope
    }
    for class_name, operation in repaired:
        operation_id = operation["operationId"]
        existing = combined.get(operation_id)
        if existing:
            if issue := _merge_operations(existing[1], operation, group):
                rejected[operation_id] = issue
            continue
        if operation_id in originals and operation_id not in repair_scope:
            # A protected operation with an incompatible replacement is ignored
            # rather than letting a group repair rewrite a good sibling.
            continue
        combined[operation_id] = (class_name, operation)
    combined_operations = list(combined.values())
    bound, binding_rejections = _bind_group(combined_operations, group, scenario, skeleton)
    rejected.update(binding_rejections)
    ordered_ids, ordering_issue = _ordered_group_ids(bound, group, scenario, skeleton)
    class_by_name = {
        _class_name(item): item
        for item in _scope_classes(skeleton, group.use_case_id)
    }
    issues = _group_contract_issues(
        group, bound, ordered_ids, class_by_name, _dependencies(skeleton), scenario
    )
    if ordering_issue:
        issues.append(ordering_issue)
    return _GroupAttempt(bound, rejected, issues)


def _process_group(
    skeleton: dict[str, Any], group: _Group, scenario: dict[str, Any],
) -> _GroupProcess:
    """Run proposal plus one scoped repair without making group loss implicit."""

    try:
        proposal = _propose_group(skeleton, group, scenario)
        initial = _inspect_group(skeleton, group, proposal, scenario)
    except Exception as error:  # noqa: BLE001 - surfaced through the transient outcome
        return _GroupProcess(group, [], {}, [_text(error) or "behavior proposal failed"], False)
    if not initial.rejected and not initial.issues:
        return _GroupProcess(group, initial.operations, {}, [], False)
    current = initial
    repaired_locally = False
    if _entity_completion_required(current, skeleton, group, scenario):
        repaired_locally = True
        try:
            entity_proposal = _complete_entity_group(
                skeleton, group, scenario, current
            )
            current = _combine_repair(
                current, entity_proposal, set(), skeleton, group, scenario
            )
        except Exception as error:  # noqa: BLE001 - generic repair remains available
            current = _GroupAttempt(
                current.operations,
                current.rejected,
                [
                    *current.issues,
                    _text(error) or "entity behavior completion failed",
                ],
            )
    if not current.rejected and not current.issues:
        return _GroupProcess(group, current.operations, {}, [], repaired_locally)
    scope = _repair_scope(current, group, scenario, skeleton)
    try:
        repaired_proposal = _repair_group(
            skeleton,
            group,
            scenario,
            _slice_from_operations(current.operations),
            [*current.rejected.values(), *current.issues],
            scope,
            {
                operation["operationId"]
                for _class_name, operation in current.operations
            }
            - scope,
        )
    except Exception as error:  # noqa: BLE001 - valid siblings remain usable below
        issues = [*current.issues, _text(error) or "behavior repair failed"]
        return _GroupProcess(group, current.operations, current.rejected, issues, True)
    try:
        repaired = _combine_repair(
            current, repaired_proposal, scope, skeleton, group, scenario
        )
    except Exception as error:  # noqa: BLE001 - preserve the initial viable subset
        issues = [*current.issues, _text(error) or "behavior repair validation failed"]
        return _GroupProcess(group, current.operations, current.rejected, issues, True)
    rejected = dict(repaired.rejected)
    if not repaired.issues:
        accepted_families = {
            _operation_family(operation["operationId"])
            for _class_name, operation in repaired.operations
        }
        # A repair may offer a conflicting signature for an operation whose
        # already accepted sibling still makes the final group complete.  That
        # discarded alternative is not a defect in the persisted contract.
        rejected = {
            operation_id: issue
            for operation_id, issue in rejected.items()
            if _operation_family(operation_id) not in accepted_families
        }
    return _GroupProcess(group, repaired.operations, rejected, repaired.issues, True)


def enrich_bce_behavior(scenario: dict[str, Any], skeleton: dict[str, Any]) -> dict[str, Any]:
    """Enrich one structural model without mutating any structural decision.

    Valid operations survive a partially rejected group.  The class check
    reports the remaining deterministic topology/binding finding; no diagnostic
    blob is persisted alongside the BCE model.
    """
    if not isinstance(scenario, dict) or not skeleton:
        return deepcopy(skeleton)
    result = _BehaviorArtifact(deepcopy(skeleton))
    for class_item in result.get("Classes") or []:
        if isinstance(class_item, dict):
            class_item["operations"] = []
            class_item["methods"] = []
    groups = execution_groups(scenario)
    workers = max(1, int(getattr(settings, "design_class_behavior_parallelism", 4)))
    if len(groups) <= 1 or workers == 1:
        processed = [_process_group(result, group, scenario) for group in groups]
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(groups))) as executor:
            futures = [
                executor.submit(_process_group, result, group, scenario)
                for group in groups
            ]
            processed = [future.result() for future in futures]

    classes = {
        _class_name(item): item
        for item in result.get("Classes") or [] if isinstance(item, dict)
    }
    accepted: dict[str, tuple[str, dict[str, Any], str]] = {}
    outcomes: list[_GroupOutcome] = []
    for process in processed:
        rejected = dict(process.rejected)
        issues = list(process.issues)
        operations = process.operations
        cross_group_conflicts = {
            operation["operationId"]
            for _class_name, operation in operations
            if operation["operationId"] in accepted
            and accepted[operation["operationId"]][2] != process.group.id
        }
        if cross_group_conflicts:
            conflict_messages = [
                f"operationId conflicts with already accepted "
                f"{accepted[operation_id][2]}: {operation_id}"
                for operation_id in sorted(cross_group_conflicts)
            ]
            initial = _GroupAttempt(operations, rejected, [])
            try:
                repaired_proposal = _repair_group(
                    result,
                    process.group,
                    scenario,
                    _slice_from_operations(operations),
                    conflict_messages,
                    cross_group_conflicts,
                    {
                        operation["operationId"]
                        for _class_name, operation in operations
                    }
                    - cross_group_conflicts,
                )
                repaired = _combine_repair(
                    initial,
                    repaired_proposal,
                    cross_group_conflicts,
                    result,
                    process.group,
                    scenario,
                )
                operations = repaired.operations
                rejected.update(repaired.rejected)
                issues.extend(repaired.issues)
            except Exception as error:  # noqa: BLE001 - retain unrelated operations
                for operation_id, message in zip(
                    sorted(cross_group_conflicts), conflict_messages, strict=True
                ):
                    rejected[operation_id] = message
                issues.append(_text(error) or "cross-group operation repair failed")
        merged_ids: list[str] = []
        for class_name, operation in operations:
            operation_id = operation["operationId"]
            existing = accepted.get(operation_id)
            if existing:
                existing_class, existing_operation, existing_group_id = existing
                if existing_group_id == process.group.id and existing_class == class_name:
                    if issue := _merge_operations(existing_operation, operation, process.group):
                        rejected[operation_id] = issue
                    else:
                        merged_ids.append(operation_id)
                    continue
                rejected[operation_id] = (
                    f"operationId conflicts with already accepted {existing_group_id}: {operation_id}"
                )
                continue
            operation.pop("_stereotype", None)
            classes[class_name]["operations"].append(operation)
            accepted[operation_id] = (class_name, operation, process.group.id)
            merged_ids.append(operation_id)
        outcomes.append(_GroupOutcome(
            process.group.id,
            tuple(sorted(merged_ids)),
            tuple(sorted(rejected)),
            tuple(dict.fromkeys(issues + list(rejected.values()))),
            process.repaired,
        ))
    # Validate/canonicalize the accepted operation contract without serializing
    # the skeleton back through Pydantic.  Re-serialization would materialize
    # optional structural defaults (for example relationship descriptions),
    # which is still an observable structural rewrite for callers that supplied
    # a sparse skeleton.
    validated = BCEModel.model_validate(result).model_dump(by_alias=True)
    validated_by_name = {
        _class_name(item): item
        for item in validated.get("Classes") or []
        if isinstance(item, dict)
    }
    for class_item in result.get("Classes") or []:
        if not isinstance(class_item, dict):
            continue
        accepted = validated_by_name.get(_class_name(class_item), {})
        class_item["operations"] = accepted.get("operations") or []
        class_item["methods"] = accepted.get("methods") or []
    result._behavior_group_outcomes = tuple(outcomes)
    return result
