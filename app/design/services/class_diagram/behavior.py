"""Build explicit, per-execution-group collaborations for BCE signatures.

The structural extraction owns classes, data types, reusable operation
signatures, and Entity relationships.  This module never edits those facts.
It asks the model only to select operation ids and an ordered call tree for a
finite execution group; call ids, argument candidates, bindings, and
Dependency relationships are derived deterministically.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.core.traceability import constraints_for_use_case
from app.design.schemas.class_model import BCEModel, canonical_call_id
from app.design.services.common.structured import parse_structured


class ProposedCall(BaseModel):
    """A deliberately id-free LLM choice for one ordered call position."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    receiver_operation_id: str = Field(alias="receiverOperationId", min_length=1)
    parent_call_index: int | None = Field(default=None, alias="parentCallIndex", ge=1)
    step_refs: list[str] = Field(alias="stepRefs", min_length=1)


class CollaborationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls: list[ProposedCall] = Field(min_length=1)


class SourceChoice(BaseModel):
    """The LLM may select exactly one source from a supplied finite list."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_ref: str = Field(alias="sourceRef", min_length=1)


class SemanticIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    collaboration_id: str = Field(alias="collaborationId", min_length=1)
    class_name: str | None = Field(default=None, alias="className")
    message: str = Field(min_length=1)
    needs_input: bool = Field(default=False, alias="needsInput")


class ClassSemanticReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issues: list[SemanticIssue] = Field(default_factory=list)


_COLLABORATION_SYSTEM = """
Build the calls for exactly one supplied execution group.  Do not create or
edit classes, data types, relationships, operations, operation ids, call ids,
or argument source references.  Choose receiverOperationId only from the
provided candidates.  Calls are in execution order.  parentCallIndex is the
one-based position of an earlier caller; omit it only for the single root
call.  Cover every supplied group step with one or more call stepRefs.
A single call may cite several main or extension steps.  Do not repeat the
same operation under the same parent merely to cover another step; repeat it
only when the scenario explicitly invokes that operation again.

For an actor group, make the root call a Boundary operation.  For an internal
group, make the root call a Control operation.  Child calls model delegation
from their parent receiver.  Reuse operation signatures; the same operation
may be called in more than one collaboration.
An actor group's Boundary root delegates to at least one Control call; a
Boundary does not replace the Control by implementing the business flow.
When a group-scoped Entity operation represents a persistent-state step, call
it as a child of the coordinating Control rather than treating the Control as
the state holder.
A non-void call may cite the system-output step fulfilled by its return.  Do
not repeat an actor-entry Boundary operation merely to represent presentation
or notification of that returned value.
Return only the supplied schema.
""".strip()

_SOURCE_SYSTEM = """
Choose exactly one sourceRef from candidateSources[].sourceRef.  Use the
candidate evidence only to distinguish the supplied finite choices.  Do not
invent, alter, or explain a sourceRef, call id, or operation id.  Return only
the schema.
""".strip()

_REPAIR_SYSTEM = """
Repair one failed collaboration proposal only.  The structural model and all
operation signatures are fixed.  Choose only supplied receiverOperationId
values; do not create identifiers or source refs.  Return a full replacement
proposal for this execution group and cover all its steps.
""".strip()

_SEMANTIC_REVIEW_SYSTEM = """
Review only unresolved collaboration decisions in the supplied, already
deterministically valid class model.  Do not review class fields, identifiers,
naming, class granularity, or implementation values, and do not propose edits.
A symbolic actor-step source such as `<step>#<parameter>` or a precondition
source is sufficient provenance; the prose need not contain that exact symbol.
Argument-source availability and type compatibility have already passed their
finite deterministic contract, so they are not review targets and must not be
reported as missing identifiers or missing values.  Emit an issue only when
the selected operation meaning or call ordering contradicts the scenario and
the scenario cannot resolve an alternative without user input.  Every issue
must name an existing collaborationId, may name an existing className, and
must set needsInput=true.  Return only the schema.

A synchronous Boundary or Control operation may trace both the request step
and later presentation/notification steps; that trace does not mean the call
is repeated or its order is ambiguous.  Do not redesign or revalidate the
upstream use-case specification, including its extension layout, in this
review.

Call order is preorder: a parent Control invocation starts before its child
Entity checks or mutations and returns only after all children return.  Do not
claim that the parent business operation completes before its prerequisite
children merely because the parent call is listed first.
""".strip()

_PRIMITIVE_TYPES = frozenset({
    "bool", "boolean", "byte", "char", "character", "date", "datetime",
    "decimal", "double", "float", "guid", "instant", "int", "integer",
    "long", "number", "short", "string", "str", "time", "timestamp", "uuid",
    "void", "object", "any",
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
    group_id: str
    accepted_call_ids: tuple[str, ...]
    issues: tuple[str, ...]
    repaired: bool = False
    needs_input: bool = False

    @property
    def status(self) -> str:
        if self.needs_input:
            return "needs_input"
        return "accepted" if self.accepted_call_ids and not self.issues else "failed"


class _BehaviorArtifact(dict):
    """A dict-compatible model with intentionally non-persisted diagnostics."""


def group_outcomes(model: dict[str, Any]) -> tuple[_GroupOutcome, ...]:
    return tuple(getattr(model, "_behavior_group_outcomes", ()))


def semantic_review_issues(model: dict[str, Any]) -> tuple[SemanticIssue, ...]:
    """Return advisory model-review observations; they never mutate or block."""

    return tuple(getattr(model, "_semantic_review_issues", ()))


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _class_name(item: dict[str, Any]) -> str:
    return _text(item.get("className") or item.get("class_name"))


def _stereotype(item: dict[str, Any]) -> str:
    return _text(item.get("stereotype")).casefold()


def _use_case_id(item: dict[str, Any]) -> str:
    return _text(item.get("use_case_id") or item.get("id"))


def _source_specification_map(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _use_case_id(item): item
        for item in scenario.get("use_case_specs") or []
        if isinstance(item, dict) and _use_case_id(item)
    }


def _summary_map(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _use_case_id(item): item
        for item in scenario.get("use_cases") or []
        if isinstance(item, dict) and _use_case_id(item)
    }


def _specification_map(scenario: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The persisted specifications are the only execution-group source.

    Factored includes have their own accepted specification when they have
    executable behavior.  We intentionally do not reconstruct prose-derived
    pseudo-specifications here: that would manufacture source values.
    """

    return _source_specification_map(scenario)


def _primary_actor(scenario: dict[str, Any], specification: dict[str, Any]) -> str:
    return _text(
        specification.get("primary_actor")
        or _summary_map(scenario).get(_use_case_id(specification), {}).get("primary_actor")
    )


def _steps(specification: dict[str, Any]) -> list[_Step]:
    use_case_id = _use_case_id(specification)
    result: list[_Step] = []
    order = 0
    for step in specification.get("main_scenario") or []:
        if not isinstance(step, dict) or step.get("step_number") is None:
            continue
        result.append(_Step(
            f"{use_case_id}:main:{step['step_number']}",
            _text(step.get("subject_ref")), _text(step.get("sentence")), order,
        ))
        order += 1
    for extension in specification.get("extensions") or []:
        if not isinstance(extension, dict):
            continue
        label = _text(extension.get("label"))
        for step in extension.get("handling_steps") or []:
            if not isinstance(step, dict):
                continue
            number = _text(step.get("sub_step"))
            if not label or not number:
                continue
            result.append(_Step(
                f"{use_case_id}:extension:{label}:{number}",
                _text(step.get("subject_ref")), _text(step.get("sentence")), order,
            ))
            order += 1
    return result


def _actor_steps(scenario: dict[str, Any], specification: dict[str, Any]) -> list[str]:
    actor = _primary_actor(scenario, specification).casefold()
    if not actor:
        return []
    result: list[str] = []
    for step in _steps(specification):
        if step.subject.casefold() == actor or (not step.subject and re.match(rf"^(?:the )?{re.escape(actor)}\b", step.sentence.casefold())):
            result.append(step.id)
    return result


def _use_case_aliases(scenario: dict[str, Any]) -> dict[str, str]:
    aliases: dict[str, set[str]] = defaultdict(set)
    for collection in (_summary_map(scenario), _specification_map(scenario)):
        for use_case_id, item in collection.items():
            aliases[use_case_id.casefold()].add(use_case_id)
            name = _text(item.get("name") or item.get("use_case_name"))
            if name:
                aliases[name.casefold()].add(use_case_id)
    return {name: next(iter(ids)) for name, ids in aliases.items() if len(ids) == 1}


def relationship_pairs(scenario: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Canonical include/extend caller-to-child pairs from persisted ids."""

    relations = scenario.get("relationships")
    if not isinstance(relations, dict):
        return []
    aliases = _use_case_aliases(scenario)
    result: list[tuple[str, str, str]] = []
    for kind, collection, child_id_key, child_name_keys in (
        ("include", "includes", "included_use_case_id", ("included_use_case", "includedUseCase")),
        ("extend", "extends", "extending_use_case_id", ("extending_use_case", "extendingUseCase")),
    ):
        for item in relations.get(collection) or []:
            if not isinstance(item, dict):
                continue
            base_raw = _text(item.get("base_use_case_id") or item.get("base_use_case") or item.get("baseUseCase"))
            child_raw = _text(item.get(child_id_key) or next((item.get(key) for key in child_name_keys if item.get(key)), ""))
            base, child = aliases.get(base_raw.casefold()), aliases.get(child_raw.casefold())
            if base and child and base != child:
                result.append((kind, base, child))
    return sorted(set(result))


def execution_groups(scenario: dict[str, Any]) -> list[_Group]:
    """Split each actor request into one finite collaboration group.

    An include without an actor is an internal reusable group.  Its operation
    can also be called by each parent group with different ancestor parameter
    sources, so signature reuse never implies one global input binding.
    """

    specs = _specification_map(scenario)
    internal_ids = {
        child for kind, _base, child in relationship_pairs(scenario)
        if kind == "include" or not _actor_steps(scenario, specs.get(child, {}))
    }
    groups: list[_Group] = []
    for use_case_id, specification in sorted(specs.items()):
        steps = _steps(specification)
        if not steps:
            continue
        if use_case_id in internal_ids:
            # An included flow is trace scope on each parent's collaboration,
            # rather than a synthetic standalone execution with no caller
            # values.  That is what lets two parents call one signature with
            # distinct ancestor-call parameter sources.
            continue
        actor_steps = set(_actor_steps(scenario, specification))
        main_steps = [
            step for step in steps if step.id.startswith(f"{use_case_id}:main:")
        ]
        active: str | None = None
        grouped: dict[str, list[str]] = {}
        main_owner: dict[str, str] = {}
        previous_was_actor = False
        for step in main_steps:
            actor_step = step.id in actor_steps
            if actor_step and (active is None or not previous_was_actor):
                active = step.id
                grouped.setdefault(active, [])
            if active:
                grouped[active].append(step.id)
                main_owner[step.id] = active
            previous_was_actor = actor_step
        if not grouped:
            groups.append(_Group(use_case_id, f"{use_case_id}:root", tuple(step.id for step in steps), None, False))
        else:
            for extension in specification.get("extensions") or []:
                if not isinstance(extension, dict):
                    continue
                branch = _text(extension.get("branch_step"))
                label = _text(extension.get("label"))
                owner = main_owner.get(f"{use_case_id}:main:{branch}")
                if not owner or not label:
                    continue
                prefix = f"{use_case_id}:extension:{label}:"
                grouped[owner].extend(
                    step.id for step in steps if step.id.startswith(prefix)
                )
            groups.extend(
                _Group(use_case_id, actor_step, tuple(refs), actor_step, False)
                for actor_step, refs in grouped.items()
            )
    return groups


def _class_in_scope(item: dict[str, Any], use_case_id: str) -> bool:
    return use_case_id in {_text(value) for value in item.get("use_case_ids") or []}


def _trace_scope_ids(group: _Group, scenario: dict[str, Any]) -> list[str]:
    """First id is the execution root; following ids are include/extend scope."""

    children = [
        child for _kind, base, child in relationship_pairs(scenario)
        if base == group.use_case_id
    ]
    return [group.use_case_id, *sorted(set(children))]


def _required_trace_steps(group: _Group, scenario: dict[str, Any]) -> set[str]:
    """Base-group steps plus obligatory included-flow steps.

    ``extend`` is intentionally trace scope but not mandatory: an extension
    executes only when its branch condition is selected.  Includes are
    unconditional, so silently omitting their calls would be a coverage bug.
    """

    required = set(group.step_ids)
    for kind, base, child in relationship_pairs(scenario):
        if kind == "include" and base == group.use_case_id:
            required.update(step.id for step in _steps(_specification_map(scenario).get(child, {})))
    return required


def _available_trace_steps(group: _Group, scenario: dict[str, Any]) -> set[str]:
    """Steps this group may cite without leaking sibling actor interactions."""

    available = set(group.step_ids)
    for child in _trace_scope_ids(group, scenario)[1:]:
        available.update(
            step.id
            for step in _steps(_specification_map(scenario).get(child, {}))
        )
    return available


def _scope_classes(
    model: dict[str, Any], use_case_id: str, *, trace_scope: set[str] | None = None,
) -> list[dict[str, Any]]:
    allowed = trace_scope or {use_case_id}
    return [
        item for item in model.get("Classes") or []
        if isinstance(item, dict)
        and any(_class_in_scope(item, current) for current in allowed)
    ]


def _operation_catalog(model: dict[str, Any], group: _Group) -> dict[str, dict[str, Any]]:
    """Return only known, group-scoped operation signatures by id."""

    operations: dict[str, dict[str, Any]] = {}
    # This helper has no scenario argument, so child include operations are
    # added by the proposal/materialisation callers that do have trace scope.
    for class_item in _scope_classes(model, group.use_case_id):
        class_name = _class_name(class_item)
        for operation in class_item.get("operations") or []:
            if not isinstance(operation, dict):
                continue
            operation_id = _text(operation.get("operationId"))
            if operation_id:
                operations[operation_id] = {
                    "className": class_name,
                    "stereotype": _stereotype(class_item),
                    **deepcopy(operation),
                }
    return operations


def _group_payload(model: dict[str, Any], group: _Group, scenario: dict[str, Any]) -> dict[str, Any]:
    specification = _specification_map(scenario).get(group.use_case_id, {})
    available_steps = _available_trace_steps(group, scenario)
    operations = _operation_catalog(model, group)
    for class_item in _scope_classes(
        model, group.use_case_id, trace_scope=set(_trace_scope_ids(group, scenario)),
    ):
        class_name = _class_name(class_item)
        for operation in class_item.get("operations") or []:
            if isinstance(operation, dict) and _text(operation.get("operationId")):
                operations[_text(operation["operationId"])] = {
                    "className": class_name, "stereotype": _stereotype(class_item), **deepcopy(operation),
                }
    return {
        "collaborationId": group.id,
        "useCaseIds": _trace_scope_ids(group, scenario),
        "entryActor": _primary_actor(scenario, specification) if group.actor_step else None,
        "internalFlow": group.internal,
        "steps": [
            {"id": step.id, "sentence": step.sentence}
            for use_case_id in _trace_scope_ids(group, scenario)
            for step in _steps(_specification_map(scenario).get(use_case_id, {}))
            if step.id in available_steps
        ],
        "requiredTraceStepRefs": sorted(_required_trace_steps(group, scenario)),
        "receiverOperationCandidates": [
            {
                "operationId": operation_id,
                "className": operation["className"],
                "stereotype": operation["stereotype"],
                "parameters": operation.get("parameters") or [],
                "returnType": operation.get("returnType"),
                "stepRefs": operation.get("stepRefs") or [],
            }
            for operation_id, operation in sorted(operations.items())
        ],
        "constraintRequirements": constraints_for_use_case(scenario, group.use_case_id),
    }


def _propose_group(model: dict[str, Any], group: _Group, scenario: dict[str, Any], *, repair: list[str] | None = None) -> CollaborationProposal:
    payload = _group_payload(model, group, scenario)
    if repair:
        payload["repairFindings"] = repair
    parsed = parse_structured(
        [{"role": "system", "content": _REPAIR_SYSTEM if repair else _COLLABORATION_SYSTEM},
         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        CollaborationProposal,
        reasoning_effort="medium",
        max_completion_tokens=settings.design_class_collaboration_max_completion_tokens,
        operation="CollaborationProposal" if not repair else "FailedCollaborationRepair",
        metadata={"collaborationGroup": group.id},
    )
    return CollaborationProposal.model_validate(parsed)


def _precondition_refs(specification: dict[str, Any]) -> set[str]:
    """Expose stable precondition identities without parsing prose into names."""

    use_case_id = _use_case_id(specification)
    refs: set[str] = set()
    raw = specification.get("preconditions") or []
    values = list(raw.values()) if isinstance(raw, dict) else list(raw)
    for index, value in enumerate(values, start=1):
        if _text(value):
            refs.add(f"{use_case_id}:precondition:{index}")
    return refs


def _parameter_type(operation: dict[str, Any], parameter_name: str) -> str:
    for parameter in operation.get("parameters") or []:
        if isinstance(parameter, dict) and _text(parameter.get("name")) == parameter_name:
            return _text(parameter.get("type"))
    return ""


def _ancestors(calls: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    by_id = {call["callId"]: call for call in calls[:index]}
    parent = calls[index].get("parentCallId")
    result: list[dict[str, Any]] = []
    while parent and parent in by_id:
        call = by_id[parent]
        result.append(call)
        parent = call.get("parentCallId")
    return result


def _binding_candidates(
    calls: list[dict[str, Any]], index: int, parameter: dict[str, Any],
    operations: dict[str, dict[str, Any]], group: _Group, scenario: dict[str, Any],
) -> list[str]:
    """Return every valid source in deterministic priority/order.

    Candidate construction is deliberately local to this collaboration.  Thus
    a reused include operation obtains an independent ancestor-call binding at
    each call site instead of carrying stale signature-level input metadata.
    """

    parameter_name, parameter_type = _text(parameter.get("name")), _text(parameter.get("type"))
    specification = _specification_map(scenario).get(group.use_case_id, {})
    candidates: list[str] = []
    if index == 0 and group.actor_step:
        candidates.append(f"{group.actor_step}#{parameter_name}")
    candidates.extend(sorted(_precondition_refs(specification)))
    for ancestor in _ancestors(calls, index):
        operation = operations.get(_text(ancestor.get("receiverOperationId")), {})
        if _parameter_type(operation, parameter_name) == parameter_type:
            candidates.append(f"{ancestor['callId']}#{parameter_name}")
    for earlier in calls[:index]:
        operation = operations.get(_text(earlier.get("receiverOperationId")), {})
        return_type = _text(operation.get("returnType"))
        if return_type and return_type.casefold() != "void" and return_type == parameter_type:
            candidates.append(f"{earlier['callId']}#result")
    return list(dict.fromkeys(candidates))


def _choose_source(
    candidates: list[str], *, group: _Group, call_id: str, parameter: str,
    scenario: dict[str, Any],
) -> str:
    specification = _specification_map(scenario).get(group.use_case_id, {})
    step_text = {step.id: step.sentence for step in _steps(specification)}
    raw_preconditions = specification.get("preconditions") or []
    preconditions = (
        list(raw_preconditions.values())
        if isinstance(raw_preconditions, dict)
        else list(raw_preconditions)
    )

    def evidence(source_ref: str) -> str:
        source_id = source_ref.split("#", 1)[0]
        if source_id in step_text:
            return step_text[source_id]
        match = re.fullmatch(rf"{re.escape(group.use_case_id)}:precondition:(\d+)", source_id)
        if match:
            index = int(match.group(1)) - 1
            if 0 <= index < len(preconditions):
                return _text(preconditions[index])
        return "Value produced by the referenced earlier call"

    parsed = parse_structured(
        [{"role": "system", "content": _SOURCE_SYSTEM}, {"role": "user", "content": json.dumps({
            "collaborationId": group.id, "callId": call_id, "parameter": parameter,
            "candidateSources": [
                {"sourceRef": source_ref, "evidence": evidence(source_ref)}
                for source_ref in candidates
            ],
        }, ensure_ascii=False)}],
        SourceChoice,
        reasoning_effort="low",
        max_completion_tokens=settings.design_class_selector_max_completion_tokens,
        operation="SourceChoice",
        metadata={"collaborationGroup": group.id, "callId": call_id},
    )
    source = _text(SourceChoice.model_validate(parsed).source_ref)
    if source not in candidates:
        raise ValueError(f"sourceRef is not one of the finite candidates for {call_id}#{parameter}")
    return source


def _materialize_calls(
    proposal: CollaborationProposal, model: dict[str, Any], group: _Group, scenario: dict[str, Any],
) -> list[dict[str, Any]]:
    operations = _operation_catalog(model, group)
    for class_item in _scope_classes(
        model, group.use_case_id, trace_scope=set(_trace_scope_ids(group, scenario)),
    ):
        class_name = _class_name(class_item)
        for operation in class_item.get("operations") or []:
            if isinstance(operation, dict) and _text(operation.get("operationId")):
                operations[_text(operation["operationId"])] = {
                    "className": class_name, "stereotype": _stereotype(class_item), **deepcopy(operation),
                }
    if not operations:
        raise ValueError("execution group has no in-scope reusable operations")
    calls: list[dict[str, Any]] = []
    covered: set[str] = set()
    for index, proposal_call in enumerate(proposal.calls):
        operation_id = _text(proposal_call.receiver_operation_id)
        if operation_id not in operations:
            untyped_name = operation_id.partition("(")[0]
            finite_matches = [
                candidate for candidate in operations
                if candidate.partition("(")[0] == untyped_name
            ]
            if len(finite_matches) == 1:
                operation_id = finite_matches[0]
            else:
                raise ValueError(f"call selects unknown receiverOperationId: {operation_id}")
        parent_call_index = proposal_call.parent_call_index
        # At the second call there is exactly one possible earlier parent.  A
        # model choice adds no information; later calls still need an explicit
        # parent because multiple earlier receivers may exist.
        if index == 1 and parent_call_index is None:
            parent_call_index = 1
        if parent_call_index is not None and parent_call_index > index:
            raise ValueError("parentCallIndex must reference an earlier call")
        if index == 0 and parent_call_index is not None:
            raise ValueError("the first call cannot have a parent")
        if index and parent_call_index is None:
            raise ValueError("only the first call may omit parentCallIndex")
        proposed_step_refs = [_text(ref) for ref in proposal_call.step_refs]
        trace_steps = _available_trace_steps(group, scenario)
        step_refs = [ref for ref in proposed_step_refs if ref in trace_steps]
        if index == 0 and group.actor_step and group.actor_step not in step_refs:
            step_refs.insert(0, group.actor_step)
        if not step_refs:
            raise ValueError(
                "call has no stepRefs in collaboration trace scope; "
                f"proposed={proposed_step_refs}, allowed={sorted(trace_steps)}"
            )
        call = {
            "callId": canonical_call_id(group.id, index + 1),
            "parentCallId": (
                canonical_call_id(group.id, parent_call_index)
                if parent_call_index is not None else None
            ),
            "receiverOperationId": operation_id,
            "stepRefs": step_refs,
            "argumentBindings": [],
        }
        calls.append(call)
        covered.update(step_refs)
    missing = sorted(_required_trace_steps(group, scenario) - covered)
    if missing:
        raise ValueError(
            "collaboration calls do not cover every required execution/include step; "
            f"missing={missing}"
        )
    first = operations[calls[0]["receiverOperationId"]]
    if group.actor_step and first["stereotype"] != "boundary":
        raise ValueError("an actor group's root call must target a Boundary operation")
    if group.internal and first["stereotype"] != "control":
        raise ValueError("an internal group's root call must target a Control operation")
    if group.actor_step and not any(
        operations[call["receiverOperationId"]]["stereotype"] == "control"
        for call in calls
    ):
        raise ValueError("an actor group's Boundary root must delegate to a Control call")
    for index, call in enumerate(calls):
        operation = operations[call["receiverOperationId"]]
        bindings: list[dict[str, str]] = []
        for parameter in operation.get("parameters") or []:
            if not isinstance(parameter, dict):
                raise TypeError(
                    "operation parameters must be objects: "
                    f"{call['receiverOperationId']}"
                )
            candidates = _binding_candidates(calls, index, parameter, operations, group, scenario)
            if not candidates:
                raise ValueError(f"no finite source candidate for {call['callId']}#{_text(parameter.get('name'))}")
            bindings.append({
                "parameter": _text(parameter.get("name")),
                "sourceRef": (
                    candidates[0]
                    if len(candidates) == 1
                    else _choose_source(
                        candidates,
                        group=group,
                        call_id=call["callId"],
                        parameter=_text(parameter.get("name")),
                        scenario=scenario,
                    )
                ),
            })
        call["argumentBindings"] = bindings
    return calls


def project_call_dependencies(model: dict[str, Any]) -> list[dict[str, Any]]:
    """Project the behavioral Dependency edges, preserving structural links.

    Call ids make caller and receiver derivable, so no duplicated class names
    are stored on calls.  A direct renderer can use this helper for a model
    that has not yet been enrichment-persisted.
    """

    operation_owner = {
        _text(operation.get("operationId")): _class_name(class_item)
        for class_item in model.get("Classes") or [] if isinstance(class_item, dict)
        for operation in class_item.get("operations") or [] if isinstance(operation, dict)
        if _text(operation.get("operationId"))
    }
    structural = [
        deepcopy(item) for item in model.get("Relationships") or []
        if isinstance(item, dict) and _text(item.get("type")) != "Dependency"
    ]
    derived: dict[tuple[str, str], dict[str, str]] = {}
    for collaboration in model.get("Collaborations") or []:
        if not isinstance(collaboration, dict):
            continue
        calls = { _text(call.get("callId")): call for call in collaboration.get("calls") or [] if isinstance(call, dict) }
        for call in calls.values():
            parent_id = _text(call.get("parentCallId"))
            parent = calls.get(parent_id)
            if not parent:
                continue
            source = operation_owner.get(_text(parent.get("receiverOperationId")), "")
            target = operation_owner.get(_text(call.get("receiverOperationId")), "")
            if source and target and source != target:
                derived[(source, target)] = {"source": source, "target": target, "type": "Dependency"}
    return [*structural, *[derived[key] for key in sorted(derived)]]


def _validate_group_result(model: dict[str, Any], scenario: dict[str, Any], group: _Group, calls: list[dict[str, Any]]) -> list[str]:
    candidate = deepcopy(model)
    candidate["Collaborations"] = [{
        "collaborationId": group.id,
        "useCaseIds": _trace_scope_ids(group, scenario),
        "entryActor": _primary_actor(scenario, _specification_map(scenario).get(group.use_case_id, {})) if group.actor_step else None,
        "calls": calls,
    }]
    candidate["Relationships"] = project_call_dependencies(candidate)
    # Local import prevents the validator/behavior module boundary becoming a
    # cycle while keeping this gate deterministic.  Other execution groups are
    # intentionally absent from this candidate, so retain only findings owned
    # by this collaboration; the full coverage check runs after all groups.
    from app.design.services.class_diagram.validation import operation_contract_issues

    return [
        message for _kind, message, location in operation_contract_issues(
            candidate, {"usecase_spec": scenario}
        )
        if location and (
            location == group.id
            or location.startswith(f"{group.id}::call:")
        )
    ]


def _semantic_review(model: dict[str, Any], scenario: dict[str, Any]) -> list[SemanticIssue]:
    collaboration_ids = {
        _text(item.get("collaborationId")) for item in model.get("Collaborations") or []
        if isinstance(item, dict)
    }
    class_names = {
        _class_name(item) for item in model.get("Classes") or [] if isinstance(item, dict)
    }
    parsed = parse_structured(
        [{"role": "system", "content": _SEMANTIC_REVIEW_SYSTEM}, {"role": "user", "content": json.dumps({
            "scenario": scenario,
            "Classes": model.get("Classes") or [],
            "DataTypes": model.get("DataTypes") or [],
            "Collaborations": model.get("Collaborations") or [],
        }, ensure_ascii=False)}],
        ClassSemanticReview,
        reasoning_effort=settings.design_reasoning_effort,
        max_completion_tokens=settings.design_class_review_max_completion_tokens,
        operation="ClassSemanticReview",
    )
    review = ClassSemanticReview.model_validate(parsed)
    issues: list[SemanticIssue] = []
    for issue in review.issues:
        if issue.collaboration_id not in collaboration_ids:
            continue
        if issue.class_name is not None and issue.class_name not in class_names:
            continue
        issues.append(issue)
    return issues


def _process_group(
    model: dict[str, Any], scenario: dict[str, Any], group: _Group,
) -> tuple[dict[str, Any] | None, _GroupOutcome]:
    """Propose one independent group, retaining failure as visible evidence."""

    try:
        proposal = _propose_group(model, group, scenario)
        calls = _materialize_calls(proposal, model, group, scenario)
        issues = _validate_group_result(model, scenario, group, calls)
        if issues:
            raise ValueError(issues[0])
        collaboration = {
            "collaborationId": group.id,
            "useCaseIds": _trace_scope_ids(group, scenario),
            "entryActor": _primary_actor(scenario, _specification_map(scenario).get(group.use_case_id, {})) if group.actor_step else None,
            "calls": calls,
        }
        return collaboration, _GroupOutcome(group.id, tuple(call["callId"] for call in calls), ())
    except Exception as error:  # noqa: BLE001 - group repair remains bounded and explicit
        initial_issue = _text(error) or "collaboration proposal failed"
        try:
            repaired = _propose_group(model, group, scenario, repair=[initial_issue])
            calls = _materialize_calls(repaired, model, group, scenario)
            issues = _validate_group_result(model, scenario, group, calls)
            if issues:
                raise ValueError(issues[0])
            collaboration = {
                "collaborationId": group.id,
                "useCaseIds": _trace_scope_ids(group, scenario),
                "entryActor": _primary_actor(scenario, _specification_map(scenario).get(group.use_case_id, {})) if group.actor_step else None,
                "calls": calls,
            }
            return collaboration, _GroupOutcome(group.id, tuple(call["callId"] for call in calls), (), repaired=True)
        except Exception as repair_error:  # noqa: BLE001
            return None, _GroupOutcome(
                group.id, (), (initial_issue, _text(repair_error) or "collaboration repair failed"), repaired=True,
            )


def enrich_bce_behavior(scenario: dict[str, Any], skeleton: dict[str, Any]) -> dict[str, Any]:
    """Add collaborations without changing structural or signature decisions."""

    if not isinstance(scenario, dict) or not isinstance(skeleton, dict) or not skeleton:
        return deepcopy(skeleton)
    result = _BehaviorArtifact(deepcopy(skeleton))
    result["Collaborations"] = []
    groups = execution_groups(scenario)
    workers = max(1, int(getattr(settings, "design_class_behavior_parallelism", 4)))
    if len(groups) <= 1 or workers == 1:
        processed = [_process_group(result, scenario, group) for group in groups]
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(groups))) as executor:
            futures = [executor.submit(_process_group, result, scenario, group) for group in groups]
            processed = [future.result() for future in futures]
    outcomes = []
    for collaboration, outcome in processed:
        if collaboration:
            result["Collaborations"].append(collaboration)
        outcomes.append(outcome)
    result["Relationships"] = project_call_dependencies(result)
    semantic_issues: list[SemanticIssue] = []
    try:
        from app.design.services.class_diagram.validation import operation_contract_issues

        deterministic_issues = operation_contract_issues(
            result, {"usecase_spec": scenario}
        )
        if not deterministic_issues and result.get("Collaborations"):
            semantic_issues = _semantic_review(result, scenario)
    except Exception:  # noqa: BLE001 - advisory review cannot invalidate accepted calls
        semantic_issues = []
    # Canonicalize ids and reject accidental legacy fields at the persistence
    # boundary.  Do not write the transient outcomes into the JSON artifact.
    validated = BCEModel.model_validate(result).model_dump(by_alias=True)
    result.clear()
    result.update(validated)
    result._behavior_group_outcomes = tuple(outcomes)
    result._semantic_review_issues = tuple(semantic_issues)
    return result
