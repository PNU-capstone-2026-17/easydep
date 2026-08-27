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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings
from app.core.traceability import constraints_for_use_case
from app.design import progress as design_progress
from app.design.schemas.class_model import BCEModel, canonical_call_id
from app.design.services.class_diagram.type_system import (
    projected_field_type,
    structured_field_types,
    types_compatible,
)
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.common.structured import parse_structured


class ProposedCall(BaseModel):
    """A deliberately id-free LLM choice for one ordered call position."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    receiver_operation_id: str = Field(alias="receiverOperationId", min_length=1)
    parent_call_index: int | None = Field(default=None, alias="parentCallIndex", ge=1)
    step_refs: list[str] = Field(alias="stepRefs", min_length=1)

    @field_validator("step_refs")
    @classmethod
    def step_refs_are_nonblank(cls, values: list[str]) -> list[str]:
        if any(not str(value).strip() for value in values):
            raise ValueError("stepRefs cannot contain blank values")
        return values


class CollaborationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calls: list[ProposedCall] = Field(min_length=1)


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
Every call stepRef must be selected from that receiver operation candidate's
declared stepRefs. Never assign a group step to an operation that does not
declare it. The actor group's root Boundary operation must declare the exact
actor entry step.

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

_REPAIR_SYSTEM = """
Repair one failed collaboration proposal only.  The structural model and all
operation signatures are fixed.  Choose only supplied receiverOperationId
values; do not create identifiers or source refs.  Return a full replacement
proposal for this execution group and cover all its steps.
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


def _relationship_invocation_steps(
    scenario: dict[str, Any], kind: str, base: str, child: str,
) -> set[str]:
    """Return canonical base-step anchors when the relation declares them."""

    relations = scenario.get("relationships")
    if not isinstance(relations, dict):
        return set()
    aliases = _use_case_aliases(scenario)
    collection = "includes" if kind == "include" else "extends"
    child_id_key = "included_use_case_id" if kind == "include" else "extending_use_case_id"
    child_name_keys = (
        ("included_use_case", "includedUseCase")
        if kind == "include"
        else ("extending_use_case", "extendingUseCase")
    )
    for item in relations.get(collection) or []:
        if not isinstance(item, dict):
            continue
        base_raw = _text(
            item.get("base_use_case_id")
            or item.get("base_use_case")
            or item.get("baseUseCase")
        )
        child_raw = _text(
            item.get(child_id_key)
            or next((item.get(key) for key in child_name_keys if item.get(key)), "")
        )
        if aliases.get(base_raw.casefold()) != base or aliases.get(child_raw.casefold()) != child:
            continue
        anchors = {
            f"{base}:{_text(ref.get('step_ref'))}"
            for ref in item.get("step_refs") or []
            if isinstance(ref, dict)
            and _text(ref.get("use_case_id")) == base
            and _text(ref.get("step_ref"))
        }
        if kind == "extend" and not anchors:
            extension_point = _text(item.get("extension_point"))
            if extension_point:
                anchors.add(f"{base}:{extension_point}")
        return anchors
    return set()


def _relationship_applies_to_group(
    scenario: dict[str, Any], group: _Group, kind: str, base: str, child: str,
) -> bool:
    anchors = _relationship_invocation_steps(scenario, kind, base, child)
    return not anchors or bool(anchors & set(group.step_ids))


def execution_groups(scenario: dict[str, Any]) -> list[_Group]:
    """Split each actor request into one finite collaboration group.

    An include without an actor is an internal reusable group.  Its operation
    can also be called by each parent group with different ancestor parameter
    sources, so signature reuse never implies one global input binding.
    """

    specs = _specification_map(scenario)
    internal_ids = {
        child for _kind, _base, child in relationship_pairs(scenario)
        if not _actor_steps(scenario, specs.get(child, {}))
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
        for step in main_steps:
            actor_step = step.id in actor_steps
            if actor_step:
                active = step.id
                grouped.setdefault(active, [])
            if active:
                grouped[active].append(step.id)
                main_owner[step.id] = active
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
    """First id is the execution root; following ids are invoked include scope.

    An extending use case owns an independent actor-entry collaboration.  The
    sequence projection may place that collaboration inside the base use
    case's conditional fragment, but the class contract must not fake a
    Boundary-to-Boundary call merely to embed it.
    """

    children = [
        child for kind, base, child in relationship_pairs(scenario)
        if kind == "include"
        and base == group.use_case_id
        and _relationship_applies_to_group(scenario, group, kind, base, child)
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
        if (
            kind == "include"
            and base == group.use_case_id
            and _relationship_applies_to_group(scenario, group, kind, base, child)
        ):
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


def _propose_group(
    model: dict[str, Any], group: _Group, scenario: dict[str, Any], *,
    repair: list[str] | None = None, previous: CollaborationProposal | None = None,
) -> CollaborationProposal:
    payload = _group_payload(model, group, scenario)
    if repair:
        payload["repairFindings"] = repair
        if previous is not None:
            payload["previousProposal"] = previous.model_dump(by_alias=True)
    parsed = parse_structured(
        [{"role": "system", "content": _REPAIR_SYSTEM if repair else _COLLABORATION_SYSTEM},
         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        CollaborationProposal,
        reasoning_effort=settings.design_reasoning_effort,
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
    model: dict[str, Any] | None = None,
) -> list[str]:
    """Return every valid source in deterministic priority/order.

    Candidate construction is deliberately local to this collaboration.  Thus
    a reused include operation obtains an independent ancestor-call binding at
    each call site instead of carrying stale signature-level input metadata.
    """

    parameter_name, parameter_type = _text(parameter.get("name")), _text(parameter.get("type"))
    if index == 0 and group.actor_step:
        return [f"{group.actor_step}#{parameter_name}"]
    candidates: list[str] = []
    fields_by_type = structured_field_types(model or {})
    for ancestor in _ancestors(calls, index):
        operation = operations.get(_text(ancestor.get("receiverOperationId")), {})
        if types_compatible(_parameter_type(operation, parameter_name), parameter_type):
            candidates.append(f"{ancestor['callId']}#{parameter_name}")
        for source_parameter in operation.get("parameters") or []:
            if not isinstance(source_parameter, dict):
                continue
            source_name = _text(source_parameter.get("name"))
            source_type = _text(source_parameter.get("type"))
            for field_path in fields_by_type.get(source_type, {}):
                projected_type = projected_field_type(
                    source_type, field_path, fields_by_type,
                )
                if field_path == parameter_name and types_compatible(
                    projected_type, parameter_type,
                ):
                    candidates.append(
                        f"{ancestor['callId']}#{source_name}.{field_path}"
                    )
    if candidates:
        return list(dict.fromkeys(candidates))
    # A compatible result produced immediately before this call is the most
    # local value.  Reverse traversal keeps that policy deterministic without
    # asking the model to choose among equivalent provenance candidates.
    for earlier in reversed(calls[:index]):
        operation = operations.get(_text(earlier.get("receiverOperationId")), {})
        return_type = _text(operation.get("returnType"))
        if return_type and return_type.casefold() != "void" and types_compatible(
            return_type, parameter_type,
        ):
            candidates.append(f"{earlier['callId']}#result")
        for field_path in fields_by_type.get(return_type, {}):
            projected_type = projected_field_type(return_type, field_path, fields_by_type)
            if field_path == parameter_name and types_compatible(
                projected_type, parameter_type,
            ):
                candidates.append(f"{earlier['callId']}#result.{field_path}")
    if candidates:
        return list(dict.fromkeys(candidates))
    return list(dict.fromkeys(candidates))


def _materialize_calls(
    proposal: CollaborationProposal,
    model: dict[str, Any],
    group: _Group,
    scenario: dict[str, Any],
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
        operation = operations[operation_id]
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
        declared_steps = {
            _text(ref) for ref in operation.get("stepRefs") or [] if _text(ref)
        }
        undeclared_steps = sorted(
            ref for ref in proposed_step_refs
            if ref in trace_steps and ref not in declared_steps
        )
        if undeclared_steps:
            raise ValueError(
                "call stepRefs must be declared by its receiver operation; "
                f"operation={operation_id}, undeclared={undeclared_steps}"
            )
        step_refs = [
            ref for ref in proposed_step_refs
            if ref in trace_steps and ref in declared_steps
        ]
        if index == 0 and group.actor_step and group.actor_step not in declared_steps:
            raise ValueError(
                "an actor group's root operation must declare its actor step: "
                f"{group.actor_step}"
            )
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
    call_by_id = {call["callId"]: call for call in calls}
    for call in calls[1:]:
        parent = call_by_id.get(_text(call.get("parentCallId")))
        if not parent:
            continue
        source = operations[parent["receiverOperationId"]]["stereotype"]
        target = operations[call["receiverOperationId"]]["stereotype"]
        if not bce_dependency_allowed(source, target):
            raise ValueError(
                f"call dependency violates BCE communication: {source} -> {target}"
            )
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
    entity_steps = {
        _text(step_ref)
        for operation in operations.values()
        if operation["stereotype"] == "entity"
        for step_ref in operation.get("stepRefs") or []
        if _text(step_ref) in _available_trace_steps(group, scenario)
    }
    covered_entity_steps = {
        _text(step_ref)
        for call in calls
        if operations[call["receiverOperationId"]]["stereotype"] == "entity"
        for step_ref in call.get("stepRefs") or []
        if _text(step_ref) in entity_steps
    }
    missing_entity_steps = sorted(entity_steps - covered_entity_steps)
    if missing_entity_steps:
        raise ValueError(
            "persistent-state steps must delegate to an in-scope Entity operation; "
            f"missing={missing_entity_steps}"
        )
    for index, call in enumerate(calls):
        operation = operations[call["receiverOperationId"]]
        bindings: list[dict[str, str]] = []
        for parameter in operation.get("parameters") or []:
            if not isinstance(parameter, dict):
                raise TypeError(
                    "operation parameters must be objects: "
                    f"{call['receiverOperationId']}"
                )
            candidates = _binding_candidates(
                calls, index, parameter, operations, group, scenario, model,
            )
            if not candidates:
                raise ValueError(f"no finite source candidate for {call['callId']}#{_text(parameter.get('name'))}")
            bindings.append({
                "parameter": _text(parameter.get("name")),
                # Candidate order is the shared provenance policy: nearest
                # ancestor first, then latest compatible earlier result.
                "sourceRef": candidates[0],
            })
        call["argumentBindings"] = bindings
    return calls


def bce_dependency_allowed(source: str, target: str) -> bool:
    """Return whether a directed class dependency is valid in this BCE model."""

    return (str(source).casefold(), str(target).casefold()) not in {
        ("boundary", "entity"),
        ("entity", "boundary"),
        ("boundary", "boundary"),
        ("entity", "control"),
    }


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


def _deterministic_group_calls(
    model: dict[str, Any], scenario: dict[str, Any], group: _Group,
) -> list[dict[str, Any]]:
    """Choose a finite valid tree after both bounded model proposals fail."""

    payload = _group_payload(model, group, scenario)
    allowed_steps = _available_trace_steps(group, scenario)
    operations = {
        _text(item.get("operationId")): item
        for item in payload["receiverOperationCandidates"]
        if _text(item.get("operationId"))
        and set(item.get("stepRefs") or []) & allowed_steps
    }
    root_stereotype = "control" if group.internal else "boundary"
    roots = [
        operation for operation in operations.values()
        if operation.get("stereotype") == root_stereotype
    ]
    controls = [
        operation for operation in operations.values()
        if operation.get("stereotype") == "control"
    ]
    entities = [
        operation for operation in operations.values()
        if operation.get("stereotype") == "entity"
    ]
    fields_by_type = structured_field_types(model)

    def refs(operation: dict[str, Any]) -> list[str]:
        return [
            _text(value) for value in operation.get("stepRefs") or []
            if _text(value) in allowed_steps
        ]

    roots.sort(key=lambda operation: (
        -len(refs(operation)),
        str(operation.get("returnType") or "").casefold() == "void",
        -len(operation.get("parameters") or []),
        _text(operation.get("operationId")),
    ))
    controls.sort(key=lambda operation: (
        -len(refs(operation)),
        _text(operation.get("operationId")),
    ))

    def control_candidates(root: dict[str, Any]) -> list[tuple[dict[str, Any], ...]]:
        if group.internal:
            return [tuple()]
        candidates: list[tuple[dict[str, Any], ...]] = [
            (control,) for control in controls
        ]
        uncovered = set(_required_trace_steps(group, scenario)) - set(refs(root))
        remaining = list(controls)
        greedy: list[dict[str, Any]] = []
        while remaining and uncovered:
            selected = max(
                remaining,
                key=lambda operation: (
                    len(set(refs(operation)) & uncovered),
                    -len(refs(operation)),
                    _text(operation.get("operationId")),
                ),
            )
            if not set(refs(selected)) & uncovered:
                break
            greedy.append(selected)
            uncovered -= set(refs(selected))
            remaining.remove(selected)
        if greedy:
            candidates.append(tuple(greedy))
        if controls:
            candidates.append(tuple(controls))
        unique: dict[tuple[str, ...], tuple[dict[str, Any], ...]] = {}
        for selected in candidates:
            key = tuple(_text(operation.get("operationId")) for operation in selected)
            unique.setdefault(key, selected)
        return sorted(unique.values(), key=lambda selected: (
            -len({step for operation in selected for step in refs(operation)}),
            len(selected),
            tuple(_text(operation.get("operationId")) for operation in selected),
        ))

    def selection_key(selected: tuple[dict[str, Any], ...]) -> tuple[Any, ...]:
        return (
        -len({step for operation in selected for step in refs(operation)}),
        len(selected),
        tuple(_text(operation.get("operationId")) for operation in selected),
        )

    for root in roots:
        for selected_controls in sorted(control_candidates(root), key=selection_key):
            selected_operations = [root, *selected_controls]
            proposal_calls: list[dict[str, Any]] = [{
                "receiverOperationId": root["operationId"],
                "stepRefs": refs(root),
            }]
            for operation in selected_controls:
                proposal_calls.append({
                    "receiverOperationId": operation["operationId"],
                    "parentCallIndex": 1,
                    "stepRefs": refs(operation),
                })

            def available_from(
                required_name: str, required_type: str,
                ancestors: tuple[dict[str, Any], ...],
            ) -> bool:
                ancestor_parameters = [
                    (_text(parameter.get("name")), _text(parameter.get("type")))
                    for operation in ancestors
                    for parameter in operation.get("parameters") or []
                    if isinstance(parameter, dict)
                ]
                produced_types = [
                    _text(operation.get("returnType"))
                    for operation in selected_operations
                    if _text(operation.get("returnType")).casefold() != "void"
                ]
                if any(
                    source_name == required_name
                    and types_compatible(source_type, required_type)
                    for source_name, source_type in ancestor_parameters
                ):
                    return True
                if any(
                    types_compatible(source_type, required_type)
                    for source_type in produced_types
                ):
                    return True
                if any(
                    types_compatible(
                        projected_field_type(
                            source_type, required_name, fields_by_type,
                        ),
                        required_type,
                    )
                    for _source_name, source_type in ancestor_parameters
                ):
                    return True
                return any(
                    types_compatible(
                        projected_field_type(
                            source_type, required_name, fields_by_type,
                        ),
                        required_type,
                    )
                    for source_type in produced_types
                )

            for entity in sorted(entities, key=lambda item: _text(item.get("operationId"))):
                required_parameters = [
                    (_text(parameter.get("name")), _text(parameter.get("type")))
                    for parameter in entity.get("parameters") or []
                    if isinstance(parameter, dict)
                ]
                parent_index: int | None = None
                parent_controls = (
                    [(1, root)]
                    if root.get("stereotype") == "control"
                    else list(enumerate(selected_controls, start=2))
                )
                for index, control in parent_controls:
                    if all(
                        available_from(name, type_name, (root, control))
                        for name, type_name in required_parameters
                    ):
                        parent_index = index
                        break
                if parent_index is None:
                    continue
                proposal_calls.append({
                    "receiverOperationId": entity["operationId"],
                    "parentCallIndex": parent_index,
                    "stepRefs": refs(entity),
                })
                selected_operations.append(entity)

            covered_steps = {
                step
                for proposal_call in proposal_calls
                for step in proposal_call.get("stepRefs") or []
            }
            output_boundaries = [
                operation for operation in operations.values()
                if operation.get("stereotype") == "boundary"
                and operation.get("operationId") != root.get("operationId")
                and set(refs(operation)) - covered_steps
            ]
            for output in sorted(
                output_boundaries,
                key=lambda item: _text(item.get("operationId")),
            ):
                required_parameters = [
                    (_text(parameter.get("name")), _text(parameter.get("type")))
                    for parameter in output.get("parameters") or []
                    if isinstance(parameter, dict)
                ]
                parent_index = next((
                    index
                    for index, control in enumerate(selected_controls, start=2)
                    if all(
                        available_from(name, type_name, (root, control))
                        for name, type_name in required_parameters
                    )
                ), None)
                if parent_index is None:
                    continue
                proposal_calls.append({
                    "receiverOperationId": output["operationId"],
                    "parentCallIndex": parent_index,
                    "stepRefs": refs(output),
                })
                selected_operations.append(output)
                covered_steps.update(refs(output))

            try:
                calls = _materialize_calls(
                    CollaborationProposal.model_validate({"calls": proposal_calls}),
                    model,
                    group,
                    scenario,
                )
                if not _validate_group_result(model, scenario, group, calls):
                    return calls
            except Exception:  # noqa: BLE001 - continue through the finite candidates
                continue
    raise ValueError("no deterministic call tree satisfies the execution-group contract")


def _unique_group_calls(
    model: dict[str, Any], scenario: dict[str, Any], group: _Group,
) -> list[dict[str, Any]]:
    """Materialize only a call tree whose operations and parents are unique."""

    payload = _group_payload(model, group, scenario)
    allowed_steps = _available_trace_steps(group, scenario)
    required_steps = _required_trace_steps(group, scenario)
    operations = {
        _text(item.get("operationId")): item
        for item in payload["receiverOperationCandidates"]
        if _text(item.get("operationId"))
        and set(item.get("stepRefs") or []) & allowed_steps
    }
    root_stereotype = "control" if group.internal else "boundary"
    roots = [
        item for item in operations.values()
        if item.get("stereotype") == root_stereotype
        and (not group.actor_step or group.actor_step in set(item.get("stepRefs") or []))
    ]
    if len(roots) != 1:
        raise ValueError("the execution group has no unique root operation")

    root = roots[0]
    selected = [root]
    proposed = [{
        "receiverOperationId": root["operationId"],
        "stepRefs": [
            ref for ref in root.get("stepRefs") or [] if ref in allowed_steps
        ],
    }]
    covered = set(proposed[0]["stepRefs"])
    remaining = set(required_steps) - covered
    step_order = {step_id: index for index, step_id in enumerate(group.step_ids)}

    while remaining:
        owners: dict[str, list[dict[str, Any]]] = {
            step: [
                item for item in operations.values()
                if item not in selected and step in set(item.get("stepRefs") or [])
            ]
            for step in remaining
        }
        if any(len(items) != 1 for items in owners.values()):
            raise ValueError("the execution group has no unique step-to-operation mapping")
        next_operations = {
            _text(items[0].get("operationId")): items[0]
            for items in owners.values()
        }
        ordered = sorted(
            next_operations.values(),
            key=lambda item: min(
                step_order.get(ref, len(step_order))
                for ref in item.get("stepRefs") or [] if ref in allowed_steps
            ),
        )
        for operation in ordered:
            parents = [
                index for index, parent in enumerate(selected, start=1)
                if bce_dependency_allowed(
                    str(parent.get("stereotype") or ""),
                    str(operation.get("stereotype") or ""),
                )
            ]
            if len(parents) != 1:
                raise ValueError("the execution group has no unique parent operation")
            refs = [
                ref for ref in operation.get("stepRefs") or [] if ref in allowed_steps
            ]
            proposed.append({
                "receiverOperationId": operation["operationId"],
                "parentCallIndex": parents[0],
                "stepRefs": refs,
            })
            selected.append(operation)
            covered.update(refs)
        next_remaining = set(required_steps) - covered
        if next_remaining == remaining:
            raise ValueError("the unique call-tree fallback made no progress")
        remaining = next_remaining

    return _materialize_calls(
        CollaborationProposal.model_validate({"calls": proposed}),
        model,
        group,
        scenario,
    )


def _process_group(
    model: dict[str, Any], scenario: dict[str, Any], group: _Group,
) -> tuple[dict[str, Any] | None, _GroupOutcome]:
    """Propose one independent group, retaining failure as visible evidence."""

    proposal: CollaborationProposal | None = None
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
        repair_findings = [initial_issue]
        previous = proposal
        repair_error: Exception = error
        for _attempt in range(2):
            try:
                repaired = _propose_group(
                    model,
                    group,
                    scenario,
                    repair=repair_findings,
                    previous=previous,
                )
                previous = repaired
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
                return collaboration, _GroupOutcome(
                    group.id,
                    tuple(call["callId"] for call in calls),
                    (),
                    repaired=True,
                )
            except Exception as current_error:  # noqa: BLE001
                repair_error = current_error
                repair_findings.append(
                    _text(current_error) or "collaboration repair failed"
                )
        try:
                calls = _unique_group_calls(model, scenario, group)
                collaboration = {
                    "collaborationId": group.id,
                    "useCaseIds": _trace_scope_ids(group, scenario),
                    "entryActor": _primary_actor(
                        scenario,
                        _specification_map(scenario).get(group.use_case_id, {}),
                    ) if group.actor_step else None,
                    "calls": calls,
                }
                return collaboration, _GroupOutcome(
                    group.id,
                    tuple(call["callId"] for call in calls),
                    (),
                    repaired=True,
                )
        except Exception as fallback_error:  # noqa: BLE001
            return None, _GroupOutcome(
                group.id,
                (),
                (
                    initial_issue,
                    _text(repair_error) or "collaboration repair failed",
                    _text(fallback_error) or "deterministic fallback failed",
                ),
                repaired=True,
            )


def affected_group_ids(
    scenario: dict[str, Any], use_case_ids: set[str],
) -> set[str]:
    return {
        group.id for group in execution_groups(scenario)
        if set(_trace_scope_ids(group, scenario)) & use_case_ids
    }


def enrich_bce_behavior(
    scenario: dict[str, Any],
    skeleton: dict[str, Any],
    *,
    group_ids: set[str] | None = None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add collaborations without changing structural or signature decisions."""

    if not isinstance(scenario, dict) or not isinstance(skeleton, dict) or not skeleton:
        return deepcopy(skeleton)
    result = _BehaviorArtifact(deepcopy(skeleton))
    all_groups = execution_groups(scenario)
    selected_ids = group_ids or {group.id for group in all_groups}
    groups = [group for group in all_groups if group.id in selected_ids]
    retained = {
        _text(item.get("collaborationId")): deepcopy(item)
        for item in (existing or {}).get("Collaborations") or []
        if isinstance(item, dict)
        and _text(item.get("collaborationId")) not in selected_ids
    }
    result["Collaborations"] = list(retained.values())
    workers = max(1, int(getattr(settings, "design_class_behavior_parallelism", 4)))
    if len(groups) <= 1 or workers == 1:
        processed = [_process_group(result, scenario, group) for group in groups]
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(groups))) as executor:
            futures = [executor.submit(_process_group, result, scenario, group) for group in groups]
            processed = [future.result() for future in futures]
    outcome_by_id = {
        outcome.group_id: outcome for outcome in group_outcomes(existing or {})
        if outcome.group_id not in selected_ids
    }
    collaboration_by_id = dict(retained)
    for index, (collaboration, outcome) in enumerate(processed, start=1):
        if collaboration:
            collaboration_by_id[outcome.group_id] = collaboration
            result["Collaborations"] = [
                collaboration_by_id[group.id]
                for group in all_groups if group.id in collaboration_by_id
            ]
            result["Relationships"] = project_call_dependencies(result)
            design_progress.emit_progress(
                "classDiagramSnapshotAccepted",
                puml=generate_plantuml_from_bce_json(result),
                phase="collaborations",
                unit=outcome.group_id,
                completed=index,
                total=len(processed),
                detail=f"Planning collaboration {outcome.group_id}",
            )
        outcome_by_id[outcome.group_id] = outcome
    result["Collaborations"] = [
        collaboration_by_id[group.id]
        for group in all_groups if group.id in collaboration_by_id
    ]
    result["Relationships"] = project_call_dependencies(result)
    # Canonicalize ids and reject accidental legacy fields at the persistence
    # boundary.  Do not write the transient outcomes into the JSON artifact.
    validated = BCEModel.model_validate(result).model_dump(by_alias=True)
    result.clear()
    result.update(validated)
    result._behavior_group_outcomes = tuple(
        outcome_by_id[group.id]
        for group in all_groups if group.id in outcome_by_id
    )
    return result
