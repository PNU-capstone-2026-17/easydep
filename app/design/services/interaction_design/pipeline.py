"""Generate one executable BCE model without legacy fallback paths."""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, create_model

from app.core.config import settings
from app.core.validation import Finding, run_checks
from app.design import progress as design_progress
from app.design.schemas.class_model import BCEModel, canonical_call_id, canonical_operation_id
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.type_system import (
    field_name,
    field_type,
    projected_field_type,
    referenced_type_names,
    reachable_data_type_names,
    structured_field_types,
    structure_type_contract,
    types_compatible,
)
from app.design.services.common import fields
from app.design.services.common.structured import parse_structured
from app.design.services.interaction_design.checks import (
    COLLABORATION_CHECKS,
    INVENTORY_CHECKS,
    OPERATION_CHECKS,
    CollaborationContext,
    OperationContext,
    class_name,
    derived_value_source,
    final_model_findings,
    operation_catalog,
    optional_inner_type,
    runtime_value_source,
    type_can_default,
)
from app.design.services.interaction_design.contracts import (
    CallPlanProposal,
    FeedbackScope,
    InventoryProposal,
    OperationFragment,
    ProposedCall,
)
from app.design.services.interaction_design.scenario import (
    ExecutionGroup,
    ScenarioIndex,
    UseCase,
    build_scenario_index,
    id_key,
    text,
)


logger = logging.getLogger(__name__)


_INVENTORY_PROMPT = ("""
Build one fixed BCE inventory for the supplied accepted use-case
specifications. Return only items and Entity structural relationships. Classify
each item once as Boundary, Control, Entity, valueObject, or enumeration.

Boundary is an actor or external-system interface. Control coordinates cohesive
use-case behavior. Entity owns persistent business state. Actor roles are not
automatically classes. Boundary and Control retain no fields. Every Entity has
typed state and identifiers name declared fields. Declare a valueObject or
enumeration here only when an Entity field transitively requires it. Request,
criteria, summary, result, and export types belong to the later use-case
operation task and must not be declared in this inventory.

Prefer cohesive reusable classes over one class per sentence or use case.
Boundary classes represent actor channels or cohesive interfaces, not use-case
titles. Control classes represent domain capabilities and may coordinate a
related lifecycle or query family across several use cases; do not mirror the
use-case list with one Control per item. Reuse an item by assigning multiple
useCaseIds whenever the same responsibility and business state are involved.
Declare only types required by the supplied behavior. Relationships connect
only independently grounded Entities, appear in one direction, and include
both endpoint multiplicities. Do not return operations, calls, dependencies,
source bindings, or review commentary. For every Boundary, Control, and Entity,
declare all and only the supplied useCaseIds whose operations may use that
class. Use an empty useCaseIds list for valueObjects and enumerations; their
availability is derived from Entity fields. Return every array field explicitly,
using an empty array only when the selected kind requires none. Class ids are proposal scope only and are not
persisted as a separate design decision.
Assign an Entity as a candidate for every use case that reads or changes its persistent state.
For Entity items, useCaseIds means that the main or extension flow directly
reads or changes that Entity. Authentication, actor presence, a precondition,
or indirect domain context alone does not justify assigning an Entity to a use
case. Candidate scope does not force the later operation task to select it.
""".strip() + "\n\n" + structure_type_contract())


_OPERATION_PROMPT = ("""
Build the complete operation fragment for exactly one execution slice within one use case and the
fixed inventory. Use only listed classes, structural types, and locally declared
DataTypes. Cover every allowedStepRef.
An actor entry is owned by one Boundary operation and delegates to Control.
Persistent state behavior is owned by Entity and called through Control.
Do not emit placeholder operations such as none or noop. Only Boundary owns the
actor entry step; delegated Control and Entity operations own system steps.

Group adjacent specification steps into cohesive reusable operations; do not
create one method per sentence. A Boundary operation may cover both an actor
request and the later result produced by its return. Do not invent display,
notify, or inform methods merely to cover an output sentence. Actor-facing
operations that return stated data or outcomes are non-void. Do not require
unstated generated identifiers, clocks, or defaults as caller inputs.

Operations in reservedOperations already belong to accepted use cases. Reuse
an exact signature or choose a distinct cohesive name; do not overload a name
with another parameter or return signature. DataTypes in reservedDataTypes also
belong to accepted fragments: reuse their exact definition or choose a distinct
name. Return no calls, bindings,
relationships, operation ids, or classes outside the inventory. You may declare
request, command, criteria, summary, detail, result, or export DataTypes used by
this use case's operation signatures. Do not redeclare a fixed type. Declare no
unused local type.

Expose one orchestration operation per Control class rather than public helper
operations or Control self-calls. An Entity may expose distinct read and change
operations only when the flow actually calls both. Select
only the Entity candidates whose persistent state this use case directly reads
or changes. Prefer one local request or criteria valueObject when four or more
cohesive inputs would otherwise expand a Boundary or Control signature.

Design signatures as one closed value flow. Every delegated parameter must be
obtainable from an ancestor operation parameter or from the result of an
earlier completed call. If actor or precondition context is needed downstream,
expose it through the Boundary signature. If later work needs data discovered
by an earlier operation, return that data in a declared result type. Keep
generated clocks, sequence positions, and defaults inside their owning
operation instead of inventing caller inputs.
When an Entity mutation applies actor-supplied data, consume a compatible
upstream request or details value. Do not make that mutation parameterless
merely to evade provenance.
An Entity operation must not accept its own complete Entity type as a
parameter: the Entity is already the receiver. Accept compatible upstream
request fields or create its owned state internally.

A Control operation owns the full contiguous coordination span of the calls it
delegates. Its stepRefs begin no later than any nested Entity or Control call;
do not label a coordinator only with a later persistence or retrieval step
when it must first perform validation or authorization.
""".strip() + "\n\n" + structure_type_contract())


_CALL_PLAN_PROMPT = """
Build the ordered call tree for exactly one execution group. Select only the
supplied receiverOperationId values. Step references are projected from the
selected operation contracts; do not return them. Return no classes, methods,
call ids, values, or argument bindings.

The first call has no parent. Every later call names the one-based index of an
earlier caller. An actor group enters through Boundary and delegates to
Control. Control delegates persistent-state work to Entity. Boundary never
calls Entity directly. Cover every requiredStepRef, including included-flow
steps, without repeating calls merely to repeat a step label.
""".strip()


_BINDING_PROMPT = """
Select one sourceRef for each supplied choice. The response schema restricts
every field to that parameter's finite candidates. Resolve all fields and
return no explanation.
Prefer the source whose name and role best match the receiver parameter; do
not invent values or identifiers.
""".strip()


@dataclass(frozen=True)
class _Collision(Exception):
    class_name: str
    operation_name: str

    def __str__(self) -> str:
        return f"operation signature collision: {self.class_name}.{self.operation_name}"


@dataclass(frozen=True)
class _DataTypeCollision(Exception):
    type_name: str

    def __str__(self) -> str:
        return f"DataType definition collision: {self.type_name}"


@dataclass(frozen=True)
class _GroupResult:
    group_id: str
    collaboration: dict[str, Any] | None
    issue: str = ""


@dataclass(frozen=True)
class _OperationUnit:
    id: str
    use_case: UseCase
    step_ids: tuple[str, ...]
    execution_group_id: str = ""


def _finding_text(findings: tuple[Finding, ...]) -> list[str]:
    return [
        f"{finding.location}: {finding.message}" if finding.location else finding.message
        for finding in findings
    ]


def _normalize_inventory(proposal: InventoryProposal) -> dict[str, Any]:
    classes: list[dict[str, Any]] = []
    data_types: list[dict[str, Any]] = []
    for item in proposal.model_dump(by_alias=True)["items"]:
        typed_fields = [
            fields.normalize_java_field(f"{field['name']} : {field['type']}")
            for field in item["fields"]
        ]
        if item["kind"] in {"Boundary", "Control", "Entity"}:
            classes.append({
                "className": item["name"],
                "stereotype": item["kind"],
                "description": item["description"],
                "fields": typed_fields,
                "identifier": list(item["identifier"]),
                "values": list(item["values"]),
                "useCaseIds": list(item["useCaseIds"]),
            })
        else:
            data_types.append({
                "name": item["name"],
                "kind": item["kind"],
                "fields": typed_fields,
                "values": list(item["values"]),
                "identifier": list(item["identifier"]),
                "useCaseIds": [],
            })
    scopes = {
        item["className"]: set(item.get("useCaseIds") or []) for item in classes
    }
    type_index = {item["name"]: item for item in data_types}
    type_scopes: dict[str, set[str]] = {name: set() for name in type_index}
    for item in classes:
        for raw_field in item.get("fields") or []:
            for name in referenced_type_names(field_type(raw_field)) & type_index.keys():
                type_scopes[name].update(scopes[item["className"]])
    changed = True
    while changed:
        changed = False
        for name, item in type_index.items():
            for raw_field in item.get("fields") or []:
                for target in referenced_type_names(field_type(raw_field)) & type_index.keys():
                    before = len(type_scopes[target])
                    type_scopes[target].update(type_scopes[name])
                    changed = changed or before != len(type_scopes[target])
    for name, item in type_index.items():
        item["useCaseIds"] = sorted(type_scopes[name], key=id_key)
    return {
        "Classes": classes,
        "DataTypes": data_types,
        "Relationships": proposal.model_dump(by_alias=True)["Relationships"],
    }


def _inventory_payload(index: ScenarioIndex) -> dict[str, Any]:
    """Expose only evidence needed for the one global structure decision."""

    summaries = {
        text(item.get("id")): item
        for item in index.raw.get("use_cases") or []
        if isinstance(item, dict) and text(item.get("id"))
    }
    return {
        "useCases": [
            {
                "id": use_case.id,
                "name": use_case.name,
                "goal": text(summaries.get(use_case.id, {}).get("goal")),
                "primaryActor": use_case.primary_actor,
                "supportingActors": list(
                    summaries.get(use_case.id, {}).get("supporting_actors") or []
                ),
                "steps": [
                    {
                        "stepRef": step.id,
                        "branch": step.branch,
                        "subject": step.subject,
                        "sentence": step.sentence,
                        "condition": step.condition,
                    }
                    for step in use_case.steps
                ],
            }
            for use_case in index.use_cases
        ],
        "relationships": [
            {
                "kind": relationship.kind,
                "baseUseCaseId": relationship.base_id,
                "relatedUseCaseId": relationship.child_id,
                "anchorStepRefs": list(relationship.anchor_step_ids),
            }
            for relationship in index.relationships
        ],
    }


def _inventory_proposal(index: ScenarioIndex) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": _INVENTORY_PROMPT},
        {
            "role": "user",
            "content": json.dumps(_inventory_payload(index), ensure_ascii=False),
        },
    ]
    parsed = parse_structured(
        messages,
        InventoryProposal,
        reasoning_effort=settings.design_reasoning_effort,
        max_completion_tokens=settings.design_class_structure_max_completion_tokens,
        operation="InteractionInventory",
    )
    candidate = _normalize_inventory(InventoryProposal.model_validate(parsed))
    report = run_checks(INVENTORY_CHECKS, candidate, index, parallel=True)
    if report.errors:
        raise RuntimeError("; ".join(report.errors))
    if report.findings:
        parsed = parse_structured(
            [
                *messages,
                {
                    "role": "user",
                    "content": json.dumps({
                        "task": "Return one full repaired inventory. Preserve valid decisions and resolve every finding.",
                        "candidate": candidate,
                        "findings": _finding_text(report.findings),
                    }, ensure_ascii=False),
                },
            ],
            InventoryProposal,
            reasoning_effort=settings.design_reasoning_effort,
            max_completion_tokens=settings.design_class_structure_max_completion_tokens,
            operation="InteractionInventoryRepair",
        )
        candidate = _normalize_inventory(InventoryProposal.model_validate(parsed))
        report = run_checks(INVENTORY_CHECKS, candidate, index, parallel=True)
    if report.errors or report.findings:
        raise ValueError(
            "class inventory remains invalid: "
            + "; ".join([*report.errors, *_finding_text(report.findings)])
        )
    return candidate


def _inventory_model(inventory: dict[str, Any]) -> dict[str, Any]:
    return BCEModel.model_validate({
        "Classes": [
            {
                **{
                    key: value for key, value in item.items()
                    if key not in {"useCaseIds", "values"}
                },
                "use_case_ids": [],
                "operations": [],
            }
            for item in inventory.get("Classes") or []
        ],
        "DataTypes": [
            {
                key: value for key, value in item.items()
                if key not in {"useCaseIds", "identifier"}
            }
            for item in inventory.get("DataTypes") or [] if isinstance(item, dict)
        ],
        "Relationships": inventory.get("Relationships") or [],
        "Collaborations": [],
    }).model_dump(by_alias=True)


def _reserved_operations(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "className": class_name(item),
            "operations": list(item.get("operations") or []),
        }
        for item in model.get("Classes") or []
        if isinstance(item, dict) and item.get("operations")
    ]


def _operation_payload(
    index: ScenarioIndex,
    inventory: dict[str, Any],
    use_case: UseCase,
    *,
    previous: dict[str, Any] | None = None,
    findings: list[str] | None = None,
    reserved: list[dict[str, Any]] | None = None,
    reserved_types: list[dict[str, Any]] | None = None,
    allowed_step_ids: tuple[str, ...] = (),
    execution_group_id: str = "",
) -> dict[str, Any]:
    summary = next((
        item for item in index.raw.get("use_cases") or []
        if isinstance(item, dict) and text(item.get("id")) == use_case.id
    ), {})
    scoped_classes = [
        {
            key: value for key, value in item.items()
            if key not in {"useCaseIds", "values"}
        }
        for item in inventory.get("Classes") or []
        if use_case.id in set(item.get("useCaseIds") or [])
    ]
    scoped_types = [
        {
            key: value for key, value in item.items()
            if key not in {"useCaseIds", "identifier"}
        }
        for item in inventory.get("DataTypes") or []
        if use_case.id in set(item.get("useCaseIds") or [])
    ]
    scoped_names = {class_name(item) for item in scoped_classes}
    scoped_reserved = [
        item for item in (reserved or [])
        if text(item.get("className")) in scoped_names
    ]
    allowed = set(allowed_step_ids) or {step.id for step in use_case.steps}
    payload: dict[str, Any] = {
        "useCase": summary,
        "executionSlice": {
            "id": execution_group_id or use_case.id,
            "steps": [
                {
                    "stepRef": step.id,
                    "subject": step.subject,
                    "sentence": step.sentence,
                    "condition": step.condition,
                }
                for step in use_case.steps if step.id in allowed
            ],
        },
        "allowedStepRefs": sorted(allowed, key=id_key),
        "fixedClasses": scoped_classes,
        "fixedDataTypes": scoped_types,
        "reservedOperations": scoped_reserved,
        "reservedDataTypes": list(reserved_types or []),
    }
    if previous is not None:
        payload["previousFragment"] = previous
    if findings:
        payload["task"] = (
            "Return a full replacement for this use-case fragment only. "
            "Preserve valid operations and resolve every finding."
        )
        payload["findings"] = findings
    return payload


def _canonicalize_downstream_input_types(
    candidate: dict[str, Any], inventory: dict[str, Any],
) -> dict[str, Any]:
    """Reuse one grounded upstream DTO instead of an uncallable layer DTO."""

    for class_set in candidate.get("Classes") or []:
        if not isinstance(class_set, dict):
            continue
        class_set["operations"] = [
            operation
            for operation in class_set.get("operations") or []
            if isinstance(operation, dict)
            and re.sub(
                r"[^a-z0-9]", "", text(operation.get("name")).casefold(),
            ) not in {"none", "noop", "notapplicable"}
        ]
    candidate["Classes"] = [
        class_set for class_set in candidate.get("Classes") or []
        if isinstance(class_set, dict) and class_set.get("operations")
    ]
    local_types = {
        text(item.get("name")): item
        for item in candidate.get("DataTypes") or []
        if isinstance(item, dict) and text(item.get("name"))
    }
    if not local_types:
        return candidate
    fields_by_type = structured_field_types({
        "Classes": inventory.get("Classes") or [],
        "DataTypes": [
            *(inventory.get("DataTypes") or []),
            *(candidate.get("DataTypes") or []),
        ],
    })
    stereotypes = {
        class_name(item): text(item.get("stereotype"))
        for item in inventory.get("Classes") or [] if isinstance(item, dict)
    }
    class_sets = [
        item for item in candidate.get("Classes") or [] if isinstance(item, dict)
    ]

    def parameter_types(allowed: set[str]) -> list[str]:
        return list(dict.fromkeys(
            text(parameter.get("type"))
            for class_set in class_sets
            if stereotypes.get(text(class_set.get("className"))) in allowed
            for operation in class_set.get("operations") or []
            if isinstance(operation, dict)
            for parameter in operation.get("parameters") or []
            if isinstance(parameter, dict) and text(parameter.get("type"))
        ))

    for class_set in class_sets:
        stereotype = stereotypes.get(text(class_set.get("className")), "")
        if stereotype not in {"Control", "Entity"}:
            continue
        allowed = {"Boundary"} if stereotype == "Control" else {"Boundary", "Control"}
        upstream_types = parameter_types(allowed)
        named_upstream: dict[str, set[str]] = {}
        for source_type in upstream_types:
            for name, source_field_type in fields_by_type.get(source_type, {}).items():
                named_upstream.setdefault(name.casefold(), set()).add(source_field_type)
        for operation in class_set.get("operations") or []:
            if not isinstance(operation, dict):
                continue
            for parameter in operation.get("parameters") or []:
                if not isinstance(parameter, dict):
                    continue
                target_type = text(parameter.get("type"))
                target_fields = fields_by_type.get(target_type, {})
                if target_type not in local_types or not target_fields:
                    continue
                fully_derived = all(
                    any(
                        types_compatible(source, expected)
                        for source in named_upstream.get(name.casefold(), set())
                    )
                    or runtime_value_source(expected)
                    or type_can_default(expected)
                    for name, expected in target_fields.items()
                )
                if fully_derived:
                    continue
                replacements: list[tuple[int, str]] = []
                for source_type in upstream_types:
                    source_fields = fields_by_type.get(source_type, {})
                    if not source_fields:
                        continue
                    overlap = sum(
                        1
                        for name, source_type_value in source_fields.items()
                        if name in target_fields
                        and types_compatible(source_type_value, target_fields[name])
                    )
                    if overlap:
                        replacements.append((overlap, source_type))
                if not replacements:
                    continue
                best_size = max(size for size, _source in replacements)
                best = sorted({
                    source for size, source in replacements if size == best_size
                })
                if len(best) == 1:
                    parameter["type"] = best[0]

    referenced = {
        name
        for class_set in class_sets
        for operation in class_set.get("operations") or []
        if isinstance(operation, dict)
        for expression in [
            *(text(parameter.get("type")) for parameter in operation.get("parameters") or []),
            text(operation.get("returnType")),
        ]
        for name in referenced_type_names(expression)
        if name in local_types
    }
    pending = list(referenced)
    while pending:
        owner = pending.pop()
        for raw_field in local_types[owner].get("fields") or []:
            for target in referenced_type_names(field_type(raw_field)):
                if target in local_types and target not in referenced:
                    referenced.add(target)
                    pending.append(target)
    candidate["DataTypes"] = [
        item for item in candidate.get("DataTypes") or []
        if text(item.get("name")) in referenced
    ]
    return candidate


def _canonicalize_step_ownership(
    candidate: dict[str, Any],
    inventory: dict[str, Any],
    actor_entry_refs: set[str],
) -> dict[str, Any]:
    """Project the fixed actor-entry ownership rule without another LLM call."""

    normalized = deepcopy(candidate)
    stereotypes = {
        class_name(item): text(item.get("stereotype")).casefold()
        for item in inventory.get("Classes") or [] if isinstance(item, dict)
    }
    class_sets: list[dict[str, Any]] = []
    for class_set in normalized.get("Classes") or []:
        if not isinstance(class_set, dict):
            continue
        owner = text(class_set.get("className"))
        operations: list[dict[str, Any]] = []
        for operation in class_set.get("operations") or []:
            if not isinstance(operation, dict):
                continue
            owned = deepcopy(operation)
            if stereotypes.get(owner) != "boundary":
                owned["stepRefs"] = [
                    ref for ref in owned.get("stepRefs") or []
                    if text(ref) not in actor_entry_refs
                ]
            if owned.get("stepRefs"):
                operations.append(owned)
        if operations:
            class_sets.append({**class_set, "operations": operations})
    normalized["Classes"] = class_sets
    return normalized


def _parse_fragment(
    index: ScenarioIndex,
    inventory: dict[str, Any],
    use_case: UseCase,
    *,
    previous: dict[str, Any] | None = None,
    findings: list[str] | None = None,
    reserved: list[dict[str, Any]] | None = None,
    reserved_types: list[dict[str, Any]] | None = None,
    allowed_step_ids: tuple[str, ...] = (),
    execution_group_id: str = "",
    operation: str = "InteractionOperations",
) -> dict[str, Any]:
    parsed = parse_structured(
        [
            {"role": "system", "content": _OPERATION_PROMPT},
            {"role": "user", "content": json.dumps(
                _operation_payload(
                    index,
                    inventory,
                    use_case,
                    previous=previous,
                    findings=findings,
                    reserved=reserved,
                    reserved_types=reserved_types,
                    allowed_step_ids=allowed_step_ids,
                    execution_group_id=execution_group_id,
                ),
                ensure_ascii=False,
            )},
        ],
        OperationFragment,
        reasoning_effort=settings.design_reasoning_effort,
        max_completion_tokens=settings.design_class_collaboration_max_completion_tokens,
        operation=operation,
        metadata={"useCaseId": use_case.id},
    )
    candidate = OperationFragment.model_validate(parsed).model_dump(by_alias=True)
    fixed_names = {
        class_name(item) for item in inventory.get("Classes") or []
        if isinstance(item, dict)
    } | {
        text(item.get("name")) for item in inventory.get("DataTypes") or []
        if isinstance(item, dict)
    } | {
        text(item.get("name")) for item in reserved_types or []
        if isinstance(item, dict)
    }
    candidate["DataTypes"] = [
        {
            **item,
            "fields": [
                fields.normalize_java_field(f"{field['name']} : {field['type']}")
                for field in item.get("fields") or []
            ],
        }
        for item in candidate.get("DataTypes") or []
        if text(item.get("name")) not in fixed_names
    ]
    candidate = _canonicalize_downstream_input_types(candidate, inventory)
    actor_entry_refs = {
        group.actor_step
        for group in index.groups
        if group.use_case_id == use_case.id
        and group.actor_step
        and (not execution_group_id or group.id == execution_group_id)
    }
    return _canonicalize_step_ownership(
        candidate, inventory, actor_entry_refs,
    )


def _checked_fragment(
    index: ScenarioIndex,
    inventory: dict[str, Any],
    use_case: UseCase,
    *,
    previous: dict[str, Any] | None = None,
    findings: list[str] | None = None,
    reserved: list[dict[str, Any]] | None = None,
    reserved_types: list[dict[str, Any]] | None = None,
    allowed_step_ids: tuple[str, ...] = (),
    execution_group_id: str = "",
    operation: str = "InteractionOperations",
) -> dict[str, Any]:
    candidate = _parse_fragment(
        index,
        inventory,
        use_case,
        previous=previous,
        findings=findings,
        reserved=reserved,
        reserved_types=reserved_types,
        allowed_step_ids=allowed_step_ids,
        execution_group_id=execution_group_id,
        operation=operation,
    )
    validation_inventory = {
        **inventory,
        "DataTypes": [
            *(inventory.get("DataTypes") or []),
            *(
                item for item in (reserved_types or [])
                if isinstance(item, dict)
                and text(item.get("name")) not in {
                    text(existing.get("name"))
                    for existing in inventory.get("DataTypes") or []
                    if isinstance(existing, dict)
                }
            ),
        ],
    }
    context = OperationContext(
        index,
        validation_inventory,
        use_case,
        allowed_step_ids,
        (execution_group_id,) if execution_group_id else (),
    )
    report = run_checks(OPERATION_CHECKS, candidate, context, parallel=True)
    if report.errors:
        raise RuntimeError("; ".join(report.errors))
    if report.findings:
        candidate = _parse_fragment(
            index,
            inventory,
            use_case,
            previous=candidate,
            findings=_finding_text(report.findings),
            reserved=reserved,
            reserved_types=reserved_types,
            allowed_step_ids=allowed_step_ids,
            execution_group_id=execution_group_id,
            operation=(
                "InteractionOperationsRepair"
                if operation == "InteractionOperations"
                else f"{operation}Repair"
            ),
        )
        report = run_checks(OPERATION_CHECKS, candidate, context, parallel=True)
    if report.errors or report.findings:
        raise ValueError(
            f"operation fragment {use_case.id} remains invalid: "
            + "; ".join([*report.errors, *_finding_text(report.findings)])
        )
    return candidate


def _operation_signature(operation: dict[str, Any]) -> tuple[Any, ...]:
    return (
        tuple(
            (text(parameter.get("name")), text(parameter.get("type")))
            for parameter in operation.get("parameters") or [] if isinstance(parameter, dict)
        ),
        text(operation.get("returnType")),
    )


def _data_type_signature(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        text(item.get("kind")),
        tuple(item.get("fields") or []),
        tuple(item.get("values") or []),
    )


def _compose(
    inventory: dict[str, Any],
    fragments: list[tuple[str, dict[str, Any]]],
    *,
    final: bool = False,
) -> dict[str, Any]:
    classes = {
        class_name(item): {
            **{
                key: deepcopy(value) for key, value in item.items()
                if key not in {"useCaseIds", "values"}
            },
            "use_case_ids": [],
            "operations": [],
        }
        for item in inventory.get("Classes") or [] if isinstance(item, dict)
    }
    data_type_index = {
        text(item.get("name")): {
            key: deepcopy(value) for key, value in item.items()
            if key not in {"useCaseIds", "identifier"}
        }
        for item in inventory.get("DataTypes") or [] if isinstance(item, dict)
    }
    for use_case_id, fragment in fragments:
        for proposed_type in fragment.get("DataTypes") or []:
            if not isinstance(proposed_type, dict):
                continue
            name = text(proposed_type.get("name"))
            existing_type = data_type_index.get(name)
            if existing_type is not None:
                if _data_type_signature(existing_type) != _data_type_signature(proposed_type):
                    raise _DataTypeCollision(name)
                continue
            data_type_index[name] = deepcopy(proposed_type)
        for class_set in fragment.get("Classes") or []:
            if not isinstance(class_set, dict):
                continue
            owner = text(class_set.get("className"))
            if owner not in classes:
                raise ValueError(f"fragment selected class outside inventory: {owner}")
            target = classes[owner]
            for proposed in class_set.get("operations") or []:
                if not isinstance(proposed, dict):
                    continue
                existing = next((
                    item for item in target["operations"]
                    if text(item.get("name")) == text(proposed.get("name"))
                ), None)
                if existing is not None:
                    if _operation_signature(existing) != _operation_signature(proposed):
                        raise _Collision(owner, text(proposed.get("name")))
                    existing["stepRefs"] = list(dict.fromkeys([
                        *(existing.get("stepRefs") or []),
                        *(proposed.get("stepRefs") or []),
                    ]))
                    continue
                parameters = list(proposed.get("parameters") or [])
                target["operations"].append({
                    "operationId": canonical_operation_id(
                        owner, text(proposed.get("name")), parameters,
                    ),
                    **deepcopy(proposed),
                })
            if class_set.get("operations") and use_case_id not in target["use_case_ids"]:
                target["use_case_ids"].append(use_case_id)
    result_classes = list(classes.values())
    relationships = deepcopy(inventory.get("Relationships") or [])
    data_types = list(data_type_index.values())
    if final:
        retained = {
            class_name(item) for item in result_classes if item.get("operations")
        }
        result_classes = [item for item in result_classes if class_name(item) in retained]
        relationships = [
            item for item in relationships if isinstance(item, dict)
            and text(item.get("source")) in retained and text(item.get("target")) in retained
        ]
        reachable = reachable_data_type_names(result_classes, data_types)
        data_types = [
            item for item in data_types if isinstance(item, dict)
            and text(item.get("name")) in reachable
        ]
    return BCEModel.model_validate({
        "Classes": result_classes,
        "DataTypes": data_types,
        "Relationships": relationships,
        "Collaborations": [],
    }).model_dump(by_alias=True)


def _preview(
    model: dict[str, Any], phase: str, unit: str, completed: int, total: int,
) -> None:
    puml = generate_plantuml_from_bce_json(model)
    if puml:
        design_progress.emit_progress(
            "classDiagramSnapshotAccepted",
            puml=puml,
            phase=phase,
            unit=unit,
            completed=completed,
            total=total,
            detail={
                "inventory": "Building the class inventory",
                "operations": f"Adding operations for {unit}",
                "collaborations": f"Planning collaboration {unit}",
            }.get(phase, "Updating the class contract"),
        )


def _build_fragments(
    index: ScenarioIndex,
    inventory: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    groups_by_use_case: dict[str, list[ExecutionGroup]] = {}
    for group in index.groups:
        groups_by_use_case.setdefault(group.use_case_id, []).append(group)
    units = [
        _OperationUnit(
            group.id,
            use_case,
            tuple(group.step_ids),
            group.id,
        )
        for use_case in index.use_cases
        for group in groups_by_use_case.get(use_case.id, [])
    ] + [
        _OperationUnit(
            use_case.id,
            use_case,
            tuple(step.id for step in use_case.steps),
        )
        for use_case in index.use_cases
        if not groups_by_use_case.get(use_case.id)
    ]
    units.sort(key=lambda unit: id_key(unit.id))
    workers = max(1, min(
        len(units) or 1,
        int(getattr(settings, "design_class_behavior_parallelism", 2)),
    ))
    committed: list[tuple[str, dict[str, Any]]] = []
    position = 0
    for offset in range(0, len(units), workers):
        wave = units[offset:offset + workers]
        current = _compose(inventory, committed)
        reserved = _reserved_operations(current)
        reserved_types = list(current.get("DataTypes") or [])
        if len(wave) == 1:
            proposals = [
                _checked_fragment(
                    index,
                    inventory,
                    wave[0].use_case,
                    reserved=reserved,
                    reserved_types=reserved_types,
                    allowed_step_ids=wave[0].step_ids,
                    execution_group_id=wave[0].execution_group_id,
                )
            ]
        else:
            with ThreadPoolExecutor(max_workers=len(wave)) as executor:
                futures = [
                    executor.submit(
                        _checked_fragment,
                        index,
                        inventory,
                        unit.use_case,
                        reserved=reserved,
                        reserved_types=reserved_types,
                        allowed_step_ids=unit.step_ids,
                        execution_group_id=unit.execution_group_id,
                    )
                    for unit in wave
                ]
                proposals = [future.result() for future in futures]
        for unit, fragment in zip(wave, proposals, strict=True):
            try:
                candidate = _compose(
                    inventory, [*committed, (unit.use_case.id, fragment)],
                )
            except (_Collision, _DataTypeCollision) as collision:
                current = _compose(inventory, committed)
                fragment = _checked_fragment(
                    index,
                    inventory,
                    unit.use_case,
                    previous=fragment,
                    findings=[str(collision)],
                    reserved=_reserved_operations(current),
                    reserved_types=list(current.get("DataTypes") or []),
                    allowed_step_ids=unit.step_ids,
                    execution_group_id=unit.execution_group_id,
                    operation="InteractionOperationCollisionRepair",
                )
                candidate = _compose(
                    inventory, [*committed, (unit.use_case.id, fragment)],
                )
            committed.append((unit.use_case.id, fragment))
            position += 1
            _preview(candidate, "operations", unit.id, position + 1, len(units) + 1)
    skeleton = _compose(inventory, committed, final=True)
    return skeleton, _fragments_from_model(index, skeleton)


def _build_skeleton(
    index: ScenarioIndex,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    inventory = _inventory_proposal(index)
    _preview(_inventory_model(inventory), "inventory", "inventory", 1, len(index.use_cases) + 1)
    skeleton, fragments = _build_fragments(index, inventory)
    return skeleton, inventory, fragments


def _group_operations(
    model: dict[str, Any], group: ExecutionGroup,
) -> dict[str, dict[str, Any]]:
    allowed = set(group.trace_use_case_ids)
    return {
        operation_id: operation
        for operation_id, operation in operation_catalog(model).items()
        if allowed & {
            text(use_case_id)
            for class_item in model.get("Classes") or [] if isinstance(class_item, dict)
            if class_name(class_item) == operation["className"]
            for use_case_id in class_item.get("use_case_ids") or []
        }
    }


def _group_payload(
    index: ScenarioIndex, model: dict[str, Any], group: ExecutionGroup,
) -> dict[str, Any]:
    operations = _group_operations(model, group)
    steps = {
        step.id: step for use_case_id in group.trace_use_case_ids
        for step in index.use_case(use_case_id).steps
    }
    return {
        "collaborationId": group.id,
        "entryActor": group.entry_actor,
        "actorStepRef": group.actor_step,
        "requiredStepRefs": list(group.required_step_ids),
        "steps": [
            {"id": step_id, "sentence": steps[step_id].sentence}
            for step_id in group.required_step_ids if step_id in steps
        ],
        "receiverOperations": [
            {
                "operationId": operation_id,
                "className": operation["className"],
                "stereotype": operation["stereotype"],
                "parameters": operation.get("parameters") or [],
                "returnType": operation.get("returnType"),
                "stepRefs": operation.get("stepRefs") or [],
            }
            for operation_id, operation in sorted(operations.items())
            if set(operation.get("stepRefs") or []) & set(group.required_step_ids)
        ],
    }


def _call_plan(
    index: ScenarioIndex,
    model: dict[str, Any],
    group: ExecutionGroup,
    *,
    previous: CallPlanProposal | None = None,
    finding: str = "",
) -> CallPlanProposal:
    payload = _group_payload(index, model, group)
    if previous is not None:
        payload["previousPlan"] = previous.model_dump(by_alias=True)
    if finding:
        payload["task"] = "Return one full repaired call plan and resolve the finding."
        payload["finding"] = finding
    operation_ids = tuple(
        item["operationId"] for item in payload["receiverOperations"]
    )
    if not operation_ids:
        raise ValueError(f"execution group has no receiver operations: {group.id}")
    finite_call = create_model(
        "FiniteProposedCall",
        __base__=ProposedCall,
        receiver_operation_id=(
            Literal.__getitem__(operation_ids),
            Field(alias="receiverOperationId"),
        ),
    )
    finite_plan = create_model(
        "FiniteCallPlan",
        __base__=CallPlanProposal,
        calls=(
            list[finite_call],
            Field(min_length=1, max_length=len(operation_ids)),
        ),
    )
    parsed = parse_structured(
        [
            {"role": "system", "content": _CALL_PLAN_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        finite_plan,
        reasoning_effort=settings.design_reasoning_effort,
        max_completion_tokens=settings.design_class_collaboration_max_completion_tokens,
        operation="InteractionCallPlanRepair" if finding else "InteractionCallPlan",
        metadata={"collaborationGroup": group.id},
    )
    return CallPlanProposal.model_validate(
        finite_plan.model_validate(parsed).model_dump(by_alias=True),
    )


def _ancestors(calls: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    previous = {text(call.get("callId")): call for call in calls[:index]}
    parent_id = text(calls[index].get("parentCallId"))
    result: list[dict[str, Any]] = []
    while parent_id and parent_id in previous:
        call = previous[parent_id]
        result.append(call)
        parent_id = text(call.get("parentCallId"))
    return result


def _parameter_type(operation: dict[str, Any], name: str) -> str:
    return next((
        text(parameter.get("type")) for parameter in operation.get("parameters") or []
        if isinstance(parameter, dict) and text(parameter.get("name")) == name
    ), "")


def _binding_candidates(
    model: dict[str, Any],
    index: ScenarioIndex,
    group: ExecutionGroup,
    calls: list[dict[str, Any]],
    call_index: int,
    parameter: dict[str, Any],
    operations: dict[str, dict[str, Any]],
) -> list[str]:
    name = text(parameter.get("name"))
    target_type = text(parameter.get("type"))
    if call_index == 0 and group.actor_step:
        return [f"{group.actor_step}#{name}"]
    if call_index == 0:
        return [
            f"{ref}#{name}" for use_case_id in group.trace_use_case_ids
            for ref in index.use_case(use_case_id).precondition_refs
        ]
    fields_by_type = structured_field_types(model)
    candidates: list[str] = []
    named_sources: dict[str, list[tuple[str, str]]] = {}

    def add_named(source_name: str, source_type: str, source_ref: str) -> None:
        named_sources.setdefault(source_name.casefold(), []).append(
            (source_type, source_ref)
        )

    ancestors = _ancestors(calls, call_index)
    ancestor_ids = {text(item.get("callId")) for item in ancestors}
    for ancestor in ancestors:
        operation = operations[text(ancestor.get("receiverOperationId"))]
        for source in operation.get("parameters") or []:
            if not isinstance(source, dict):
                continue
            source_name = text(source.get("name"))
            source_type = text(source.get("type"))
            source_ref = f"{ancestor['callId']}#{source_name}"
            add_named(source_name, source_type, source_ref)
            if types_compatible(source_type, target_type):
                candidates.append(source_ref)
            for field_path in fields_by_type.get(source_type, {}):
                projected = projected_field_type(source_type, field_path, fields_by_type)
                field_ref = f"{source_ref}.{field_path}"
                add_named(field_path, projected, field_ref)
                if types_compatible(projected, target_type):
                    candidates.append(field_ref)
    for earlier in reversed(calls[:call_index]):
        if text(earlier.get("callId")) in ancestor_ids:
            continue
        operation = operations[text(earlier.get("receiverOperationId"))]
        return_type = text(operation.get("returnType"))
        if return_type.casefold() != "void" and types_compatible(return_type, target_type):
            candidates.append(f"{earlier['callId']}#result")
        elif types_compatible(optional_inner_type(return_type), target_type):
            candidates.append(f"{earlier['callId']}#result.unwrap")
        for field_path in fields_by_type.get(return_type, {}):
            projected = projected_field_type(return_type, field_path, fields_by_type)
            field_ref = f"{earlier['callId']}#result.{field_path}"
            add_named(field_path, projected, field_ref)
            if types_compatible(projected, target_type):
                candidates.append(field_ref)
    target_fields = fields_by_type.get(target_type, {})
    if not candidates and target_fields:
        mappings: dict[str, str] = {}
        for field, expected in target_fields.items():
            matching = [
                source_ref
                for source_type, source_ref in named_sources.get(field.casefold(), [])
                if types_compatible(source_type, expected)
            ]
            if matching:
                mappings[field] = matching[0]
            elif runtime_value_source(expected):
                mappings[field] = runtime_value_source(expected)
            elif not type_can_default(expected):
                break
        else:
            candidates.append(derived_value_source(target_type, mappings))
    if not candidates and runtime_value_source(target_type):
        candidates.append(runtime_value_source(target_type))
    return list(dict.fromkeys(candidates))


def _select_ambiguous_bindings(
    group: ExecutionGroup,
    ambiguous: dict[str, list[str]],
) -> dict[str, str]:
    fields: dict[str, tuple[Any, Any]] = {}
    choices: list[dict[str, Any]] = []
    locations: dict[str, str] = {}
    for position, (parameter, candidates) in enumerate(
        sorted(ambiguous.items()), start=1,
    ):
        field_name = f"choice{position}"
        finite_values = tuple(dict.fromkeys(candidates))
        if not finite_values:
            raise ValueError(f"binding candidates are empty for {parameter}")
        fields[field_name] = (
            Literal.__getitem__(finite_values),
            Field(description=f"Source for {parameter}"),
        )
        choices.append({
            "choice": field_name,
            "parameter": parameter,
            "candidates": list(finite_values),
        })
        locations[field_name] = parameter
    selection_schema = create_model(
        "FiniteBindingChoices",
        __config__={"extra": "forbid"},
        **fields,
    )
    parsed = parse_structured(
        [
            {"role": "system", "content": _BINDING_PROMPT},
            {"role": "user", "content": json.dumps({
                "collaborationId": group.id,
                "choices": choices,
            }, ensure_ascii=False)},
        ],
        selection_schema,
        reasoning_effort="low",
        max_completion_tokens=min(
            settings.design_class_collaboration_max_completion_tokens, 2048,
        ),
        operation="InteractionBindingSelection",
        metadata={"collaborationGroup": group.id},
    )
    selected = selection_schema.model_validate(parsed).model_dump()
    return {
        locations[field_name]: source_ref
        for field_name, source_ref in selected.items()
    }


def _materialize(
    index: ScenarioIndex,
    model: dict[str, Any],
    group: ExecutionGroup,
    plan: CallPlanProposal,
) -> dict[str, Any]:
    operations = _group_operations(model, group)
    calls: list[dict[str, Any]] = []
    allowed = set(group.required_step_ids)
    for position, proposed in enumerate(plan.calls, start=1):
        operation_id = text(proposed.receiver_operation_id)
        if operation_id not in operations:
            raise ValueError(f"unknown receiverOperationId: {operation_id}")
        parent_index = proposed.parent_call_index
        if position == 1 and parent_index is not None:
            raise ValueError("the first call cannot have a parent")
        if position > 1 and parent_index is None:
            raise ValueError("every delegated call requires an earlier parent")
        if parent_index is not None and parent_index >= position:
            raise ValueError("parentCallIndex must reference an earlier call")
        operation = operations[operation_id]
        declared = {text(ref) for ref in operation.get("stepRefs") or []}
        refs = [ref for ref in group.required_step_ids if ref in declared and ref in allowed]
        if not refs:
            raise ValueError("selected operation has no declared step in this group")
        calls.append({
            "callId": canonical_call_id(group.id, position),
            "parentCallId": canonical_call_id(group.id, parent_index) if parent_index else None,
            "receiverOperationId": operation_id,
            "stepRefs": refs,
            "argumentBindings": [],
        })
    for call in calls[1:]:
        parent = next(item for item in calls if item["callId"] == call["parentCallId"])
        source = operations[parent["receiverOperationId"]]["stereotype"]
        target = operations[call["receiverOperationId"]]["stereotype"]
        if (source, target) in {
            ("boundary", "boundary"), ("boundary", "entity"),
            ("entity", "boundary"), ("entity", "control"),
        }:
            raise ValueError(f"BCE communication is invalid: {source} -> {target}")
    ambiguous: dict[str, list[str]] = {}
    for call_index, call in enumerate(calls):
        operation = operations[call["receiverOperationId"]]
        for parameter in operation.get("parameters") or []:
            if not isinstance(parameter, dict):
                raise TypeError("operation parameter must be an object")
            candidates = _binding_candidates(
                model, index, group, calls, call_index, parameter, operations,
            )
            location = f"{call['callId']}#{text(parameter.get('name'))}"
            if not candidates:
                raise ValueError(f"no finite source for {location}")
            if len(candidates) > 1:
                ambiguous[location] = candidates
            else:
                call["argumentBindings"].append({
                    "parameter": text(parameter.get("name")),
                    "sourceRef": candidates[0],
                })
    selected = _select_ambiguous_bindings(group, ambiguous) if ambiguous else {}
    for call in calls:
        operation = operations[call["receiverOperationId"]]
        existing = {
            text(binding.get("parameter")) for binding in call["argumentBindings"]
        }
        for parameter in operation.get("parameters") or []:
            name = text(parameter.get("name"))
            if name not in existing:
                call["argumentBindings"].append({
                    "parameter": name,
                    "sourceRef": selected[f"{call['callId']}#{name}"],
                })
    collaboration = {
        "collaborationId": group.id,
        "useCaseIds": list(group.trace_use_case_ids),
        "entryActor": group.entry_actor,
        "calls": calls,
    }
    report = run_checks(
        COLLABORATION_CHECKS,
        collaboration,
        CollaborationContext(index, model, group),
        parallel=True,
    )
    if report.errors or report.findings:
        raise ValueError("; ".join([*report.errors, *_finding_text(report.findings)]))
    return collaboration


def _process_group(
    index: ScenarioIndex,
    model: dict[str, Any],
    group: ExecutionGroup,
    directive: str = "",
) -> _GroupResult:
    plan: CallPlanProposal | None = None
    try:
        plan = _call_plan(index, model, group, finding=directive)
        return _GroupResult(group.id, _materialize(index, model, group, plan))
    except Exception as first_error:  # one local replacement, never a global loop
        try:
            repaired = _call_plan(
                index,
                model,
                group,
                previous=plan,
                finding=str(first_error),
            )
            return _GroupResult(group.id, _materialize(index, model, group, repaired))
        except Exception as second_error:
            return _GroupResult(
                group.id,
                None,
                f"{type(second_error).__name__}: {second_error}",
            )


def _compose_fragments(
    inventory: dict[str, Any], fragments: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return _compose(
        inventory,
        sorted(fragments.items(), key=lambda item: id_key(item[0])),
        final=True,
    )


def _repair_failed_operations(
    index: ScenarioIndex,
    inventory: dict[str, Any],
    fragments: dict[str, dict[str, Any]],
    failures: list[_GroupResult],
    *,
    operation: str = "InteractionOperationHandoff",
) -> set[str]:
    group_by_id = {group.id: group for group in index.groups}
    repaired: set[str] = set()
    for result in sorted(failures, key=lambda item: id_key(item.group_id)):
        group = group_by_id[result.group_id]
        use_case_id = group.use_case_id
        use_case = index.use_case(use_case_id)
        existing = fragments[use_case_id]
        group_steps = set(group.step_ids)
        preserved_classes = []
        previous_classes = []
        for class_set in existing.get("Classes") or []:
            if not isinstance(class_set, dict):
                continue
            preserved_operations = [
                deepcopy(operation)
                for operation in class_set.get("operations") or []
                if isinstance(operation, dict)
                and not (group_steps & set(operation.get("stepRefs") or []))
            ]
            previous_operations = [
                deepcopy(operation)
                for operation in class_set.get("operations") or []
                if isinstance(operation, dict)
                and group_steps & set(operation.get("stepRefs") or [])
            ]
            if preserved_operations:
                preserved_classes.append({
                    "className": text(class_set.get("className")),
                    "operations": preserved_operations,
                })
            if previous_operations:
                previous_classes.append({
                    "className": text(class_set.get("className")),
                    "operations": previous_operations,
                })
        preserved = {
            "DataTypes": deepcopy(existing.get("DataTypes") or []),
            "Classes": preserved_classes,
        }
        base_fragments = {
            **{key: value for key, value in fragments.items() if key != use_case_id},
            **({use_case_id: preserved} if preserved_classes else {}),
        }
        base = _compose_fragments(inventory, base_fragments)
        candidate = _checked_fragment(
            index,
            inventory,
            use_case,
            previous={
                "DataTypes": deepcopy(existing.get("DataTypes") or []),
                "Classes": previous_classes,
            },
            findings=[f"execution group {result.group_id}: {result.issue}"],
            reserved=_reserved_operations(base),
            reserved_types=list(base.get("DataTypes") or []),
            allowed_step_ids=tuple(group.step_ids),
            execution_group_id=group.id,
            operation=operation,
        )
        merged_types = {
            text(item.get("name")): deepcopy(item)
            for item in existing.get("DataTypes") or [] if isinstance(item, dict)
        }
        merged_types.update({
            text(item.get("name")): deepcopy(item)
            for item in candidate.get("DataTypes") or [] if isinstance(item, dict)
        })
        merged_classes = {
            text(item.get("className")): deepcopy(item)
            for item in preserved_classes
        }
        for class_set in candidate.get("Classes") or []:
            if not isinstance(class_set, dict):
                continue
            owner = text(class_set.get("className"))
            target = merged_classes.setdefault(owner, {"className": owner, "operations": []})
            target["operations"].extend(deepcopy(class_set.get("operations") or []))
        fragments[use_case_id] = {
            "DataTypes": list(merged_types.values()),
            "Classes": list(merged_classes.values()),
        }
        repaired.add(use_case_id)
    return repaired


def generate_class_model(scenario: dict[str, Any]) -> dict[str, Any]:
    """Generate the persisted BCE model through bounded owned units."""

    index = build_scenario_index(scenario)
    if not index.use_cases:
        return {}
    skeleton, inventory, fragments = _build_skeleton(index)
    workers = max(1, min(
        len(index.groups) or 1,
        int(getattr(settings, "design_class_behavior_parallelism", 2)),
    ))

    def process(selected: list[ExecutionGroup], model: dict[str, Any]) -> list[_GroupResult]:
        if workers == 1 or len(selected) <= 1:
            return [_process_group(index, model, group) for group in selected]
        with ThreadPoolExecutor(max_workers=min(workers, len(selected))) as executor:
            futures = [executor.submit(_process_group, index, model, group) for group in selected]
            return [future.result() for future in futures]

    results = process(list(index.groups), skeleton)
    failures = [result for result in results if result.collaboration is None]
    if failures:
        try:
            repaired_use_cases = _repair_failed_operations(
                index, inventory, fragments, failures,
            )
            skeleton = _compose_fragments(inventory, fragments)
            affected = [
                group for group in index.groups
                if set(group.trace_use_case_ids) & repaired_use_cases
            ]
            retained = {
                result.group_id: result for result in results
                if result.collaboration is not None
                and result.group_id not in {group.id for group in affected}
            }
            retried = process(affected, skeleton)
            results = [
                retained.get(group.id)
                or next(result for result in retried if result.group_id == group.id)
                for group in index.groups
            ]
        except Exception as error:
            # Keep the valid skeleton and explicit missing-collaboration finding.
            # The stage gate can request input without fabricating calls.
            logger.warning(
                "class collaboration operation handoff failed during generation: %s",
                error,
                exc_info=True,
            )
    collaborations: list[dict[str, Any]] = []
    for position, result in enumerate(results, start=1):
        if result.collaboration is not None:
            collaborations.append(result.collaboration)
            preview = {**deepcopy(skeleton), "Collaborations": deepcopy(collaborations)}
            _preview(preview, "collaborations", result.group_id, position, len(index.groups))
    model = BCEModel.model_validate({
        **skeleton,
        "Collaborations": collaborations,
    }).model_dump(by_alias=True)
    # The final check deliberately does not start another repair path.
    final_model_findings(model, index)
    return model


def resume_class_model(
    scenario: dict[str, Any], current: dict[str, Any],
) -> dict[str, Any]:
    """Complete only missing collaboration groups in an accepted skeleton."""

    index = build_scenario_index(scenario)
    model = BCEModel.model_validate(current).model_dump(by_alias=True)
    existing = {
        text(item.get("collaborationId")): item
        for item in model.get("Collaborations") or [] if isinstance(item, dict)
    }
    selected: list[ExecutionGroup] = []
    for group in index.groups:
        collaboration = existing.get(group.id)
        if collaboration is None:
            selected.append(group)
            continue
        report = run_checks(
            COLLABORATION_CHECKS,
            collaboration,
            CollaborationContext(index, model, group),
            parallel=True,
        )
        if report.errors or report.findings:
            selected.append(group)
    if not selected:
        return model
    workers = max(1, min(
        len(selected),
        int(getattr(settings, "design_class_behavior_parallelism", 2)),
    ))
    if workers == 1:
        results = [_process_group(index, model, group) for group in selected]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(_process_group, index, model, group)
                for group in selected
            ]
            results = [future.result() for future in futures]
    selected_ids = {group.id for group in selected}
    failures = [result for result in results if result.collaboration is None]
    working_model = model
    if failures:
        try:
            inventory = _inventory_from_model(model)
            fragments = _fragments_from_model(index, model)
            repaired_use_cases = _repair_failed_operations(
                index, inventory, fragments, failures,
            )
            working_model = _compose_fragments(inventory, fragments)
            affected = [
                group for group in index.groups
                if set(group.trace_use_case_ids) & repaired_use_cases
            ]
            if workers == 1 or len(affected) <= 1:
                retried = [
                    _process_group(index, working_model, group)
                    for group in affected
                ]
            else:
                with ThreadPoolExecutor(max_workers=min(workers, len(affected))) as executor:
                    futures = [
                        executor.submit(_process_group, index, working_model, group)
                        for group in affected
                    ]
                    retried = [future.result() for future in futures]
            affected_ids = {group.id for group in affected}
            results = [
                result for result in results if result.group_id not in affected_ids
            ] + retried
            selected_ids.update(affected_ids)
        except Exception as error:
            logger.warning(
                "class collaboration operation handoff failed during resume: %s",
                error,
                exc_info=True,
            )
    accepted = {
        **{
            group_id: collaboration
            for group_id, collaboration in existing.items()
            if group_id not in selected_ids
        },
        **{
            result.group_id: result.collaboration
            for result in results if result.collaboration is not None
        },
    }
    working_model["Collaborations"] = [
        accepted[group.id] for group in index.groups if group.id in accepted
    ]
    return BCEModel.model_validate(working_model).model_dump(by_alias=True)


def project_call_dependencies(model: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive display-only dependencies from the persisted call tree."""

    owners = {
        operation_id: operation["className"]
        for operation_id, operation in operation_catalog(model).items()
    }
    result: dict[tuple[str, str], dict[str, str]] = {}
    for collaboration in model.get("Collaborations") or []:
        if not isinstance(collaboration, dict):
            continue
        calls = {
            text(call.get("callId")): call for call in collaboration.get("calls") or []
            if isinstance(call, dict)
        }
        for call in calls.values():
            parent = calls.get(text(call.get("parentCallId")))
            if not parent:
                continue
            source = owners.get(text(parent.get("receiverOperationId")), "")
            target = owners.get(text(call.get("receiverOperationId")), "")
            if source and target and source != target:
                result[(source, target)] = {
                    "source": source, "target": target, "type": "Dependency",
                }
    return [result[key] for key in sorted(result)]


def _inventory_from_model(model: dict[str, Any]) -> dict[str, Any]:
    all_data_types = {
        text(item.get("name")): deepcopy(item)
        for item in model.get("DataTypes") or []
        if isinstance(item, dict) and text(item.get("name"))
    }
    structural_names: set[str] = set()
    pending = {
        name
        for item in model.get("Classes") or []
        if isinstance(item, dict) and text(item.get("stereotype")) == "Entity"
        for value in item.get("fields") or []
        for name in referenced_type_names(field_type(value))
        if name in all_data_types
    }
    while pending:
        name = pending.pop()
        if name in structural_names:
            continue
        structural_names.add(name)
        for value in all_data_types[name].get("fields") or []:
            pending.update(
                referenced_type_names(field_type(value))
                & (all_data_types.keys() - structural_names)
            )
    data_types = {
        name: item for name, item in all_data_types.items()
        if name in structural_names
    }
    type_scopes: dict[str, set[str]] = {name: set() for name in data_types}
    classes: list[dict[str, Any]] = []
    for item in model.get("Classes") or []:
        if not isinstance(item, dict):
            continue
        scope = {text(value) for value in item.get("use_case_ids") or [] if text(value)}
        classes.append({
            **{
                key: deepcopy(value) for key, value in item.items()
                if key not in {"use_case_ids", "operations"}
            },
            "useCaseIds": sorted(scope, key=id_key),
        })
        referenced = {
            name
            for value in item.get("fields") or []
            for name in referenced_type_names(field_type(value))
        }
        for name in referenced & type_scopes.keys():
            type_scopes[name].update(scope)
    changed = True
    while changed:
        changed = False
        for name, item in data_types.items():
            targets = {
                target
                for value in item.get("fields") or []
                for target in referenced_type_names(field_type(value))
                if target in type_scopes
            }
            for target in targets:
                before = len(type_scopes[target])
                type_scopes[target].update(type_scopes[name])
                changed = changed or len(type_scopes[target]) != before
    return {
        "Classes": classes,
        "DataTypes": [
            {**item, "useCaseIds": sorted(type_scopes[name], key=id_key)}
            for name, item in data_types.items()
        ],
        "Relationships": [
            deepcopy(item) for item in model.get("Relationships") or []
            if isinstance(item, dict) and text(item.get("type")) != "Dependency"
        ],
    }


def _inventory_as_proposal(inventory: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for item in inventory.get("Classes") or []:
        if not isinstance(item, dict):
            continue
        items.append({
            "name": class_name(item),
            "kind": text(item.get("stereotype")),
            "description": text(item.get("description")),
            "fields": [
                {"name": field_name(value), "type": field_type(value)}
                for value in item.get("fields") or []
            ],
            "identifier": list(item.get("identifier") or []),
            "values": [],
            "useCaseIds": list(item.get("useCaseIds") or []),
        })
    for item in inventory.get("DataTypes") or []:
        if not isinstance(item, dict):
            continue
        items.append({
            "name": text(item.get("name")),
            "kind": text(item.get("kind")),
            "description": "",
            "fields": [
                {"name": field_name(value), "type": field_type(value)}
                for value in item.get("fields") or []
            ],
            "identifier": [],
            "values": list(item.get("values") or []),
            "useCaseIds": list(item.get("useCaseIds") or []),
        })
    return {"items": items, "Relationships": deepcopy(inventory.get("Relationships") or [])}


def _fragments_from_model(
    index: ScenarioIndex, model: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    inventory_type_names = {
        text(item.get("name"))
        for item in _inventory_from_model(model).get("DataTypes") or []
        if isinstance(item, dict)
    }
    all_types = {
        text(item.get("name")): item
        for item in model.get("DataTypes") or []
        if isinstance(item, dict)
    }
    result: dict[str, dict[str, Any]] = {}
    for use_case in index.use_cases:
        class_sets: list[dict[str, Any]] = []
        for item in model.get("Classes") or []:
            if not isinstance(item, dict):
                continue
            operations: list[dict[str, Any]] = []
            for operation in item.get("operations") or []:
                if not isinstance(operation, dict):
                    continue
                refs = [
                    text(ref) for ref in operation.get("stepRefs") or []
                    if text(ref).startswith(f"{use_case.id}:")
                ]
                if refs:
                    operations.append({
                        key: deepcopy(value) for key, value in operation.items()
                        if key != "operationId"
                    } | {"stepRefs": refs})
            if operations:
                class_sets.append({"className": class_name(item), "operations": operations})
        if class_sets:
            local_names = {
                name
                for class_set in class_sets
                for operation in class_set.get("operations") or []
                for type_expression in [
                    *(text(parameter.get("type")) for parameter in operation.get("parameters") or []),
                    text(operation.get("returnType")),
                ]
                for name in referenced_type_names(type_expression)
                if name in all_types and name not in inventory_type_names
            }
            pending = list(local_names)
            while pending:
                name = pending.pop()
                for value in all_types[name].get("fields") or []:
                    for target in referenced_type_names(field_type(value)):
                        if (
                            target in all_types
                            and target not in inventory_type_names
                            and target not in local_names
                        ):
                            local_names.add(target)
                            pending.append(target)
            result[use_case.id] = {
                "DataTypes": [
                    deepcopy(all_types[name]) for name in sorted(local_names)
                ],
                "Classes": class_sets,
            }
    return result


def _feedback_scope(
    index: ScenarioIndex,
    model: dict[str, Any],
    feedback: str,
    targets: set[str],
) -> FeedbackScope:
    inventory_ids = {
        class_name(item) for item in model.get("Classes") or [] if isinstance(item, dict)
    } | {
        text(item.get("name"))
        for item in _inventory_from_model(model).get("DataTypes") or []
        if isinstance(item, dict)
    }
    fragments = _fragments_from_model(index, model)
    local_type_owners: dict[str, set[str]] = {}
    for use_case_id, fragment in fragments.items():
        for item in fragment.get("DataTypes") or []:
            if isinstance(item, dict):
                local_type_owners.setdefault(text(item.get("name")), set()).add(use_case_id)
    use_case_ids = {use_case.id for use_case in index.use_cases}
    group_ids = {group.id for group in index.groups}
    if targets:
        if targets <= group_ids:
            return FeedbackScope(kind="collaboration", ids=sorted(targets, key=id_key))
        if targets <= use_case_ids:
            return FeedbackScope(kind="operation", ids=sorted(targets, key=id_key))
        if targets <= inventory_ids:
            return FeedbackScope(kind="inventory", ids=sorted(targets))
        if targets <= local_type_owners.keys():
            owners = {
                use_case_id for target in targets
                for use_case_id in local_type_owners[target]
            }
            return FeedbackScope(kind="operation", ids=sorted(owners, key=id_key))
    mentioned_local_owners = {
        use_case_id
        for name, owners in local_type_owners.items()
        if name.casefold() in feedback.casefold()
        for use_case_id in owners
    }
    if mentioned_local_owners:
        return FeedbackScope(
            kind="operation", ids=sorted(mentioned_local_owners, key=id_key),
        )
    parsed = parse_structured(
        [
            {
                "role": "system",
                "content": (
                    "Classify the feedback into exactly one smallest design owner. "
                    "inventory changes classes, fields, types, or structural relationships; "
                    "operation changes one or more use-case method contracts; collaboration "
                    "changes call order or delegation only. Select ids only from candidates."
                ),
            },
            {"role": "user", "content": json.dumps({
                "feedback": feedback,
                "candidates": {
                    "inventory": sorted(inventory_ids),
                    "operation": sorted(use_case_ids, key=id_key),
                    "collaboration": sorted(group_ids, key=id_key),
                },
            }, ensure_ascii=False)},
        ],
        FeedbackScope,
        reasoning_effort="low",
        max_completion_tokens=settings.design_class_collaboration_max_completion_tokens,
        operation="InteractionFeedbackScope",
    )
    scope = FeedbackScope.model_validate(parsed)
    allowed = {
        "inventory": inventory_ids,
        "operation": use_case_ids,
        "collaboration": group_ids,
    }[scope.kind]
    if not set(scope.ids) <= allowed:
        raise ValueError("feedback scope selected an unknown target")
    return scope


def _revise_inventory(
    index: ScenarioIndex,
    inventory: dict[str, Any],
    feedback: str,
    target_ids: set[str],
) -> dict[str, Any]:
    current = _inventory_as_proposal(inventory)
    parsed = parse_structured(
        [
            {"role": "system", "content": _INVENTORY_PROMPT},
            {"role": "user", "content": json.dumps({
                "task": "Apply the user feedback to the inventory and return one full replacement inventory.",
                "feedback": feedback,
                "targetIds": sorted(target_ids),
                "currentInventory": current,
                "scenario": index.raw,
            }, ensure_ascii=False)},
        ],
        InventoryProposal,
        reasoning_effort=settings.design_reasoning_effort,
        max_completion_tokens=settings.design_class_structure_max_completion_tokens,
        operation="InteractionInventoryFeedback",
    )
    proposal = InventoryProposal.model_validate(parsed)
    if target_ids:
        replacement = {item.name: item for item in proposal.items}
        original = InventoryProposal.model_validate(current)
        if not target_ids <= {item.name for item in original.items}:
            raise ValueError("inventory feedback target does not exist")
        merged_items = [
            replacement.get(item.name, item) if item.name in target_ids else item
            for item in original.items
        ]
        proposal = InventoryProposal(
            items=merged_items,
            Relationships=(
                proposal.Relationships if any(
                    relationship.source in target_ids or relationship.target in target_ids
                    for relationship in proposal.Relationships
                ) else original.Relationships
            ),
        )
    candidate = _normalize_inventory(proposal)
    report = run_checks(INVENTORY_CHECKS, candidate, index, parallel=True)
    if report.errors or report.findings:
        raise ValueError("inventory feedback is invalid: " + "; ".join([
            *report.errors, *_finding_text(report.findings),
        ]))
    return candidate


def _run_selected_groups(
    index: ScenarioIndex,
    model: dict[str, Any],
    groups: list[ExecutionGroup],
    *,
    feedback: str = "",
) -> list[_GroupResult]:
    workers = max(1, min(
        len(groups) or 1,
        int(getattr(settings, "design_class_behavior_parallelism", 2)),
    ))
    directive = f"Apply this user feedback to this call plan only: {feedback}" if feedback else ""
    if workers == 1 or len(groups) <= 1:
        return [_process_group(index, model, group, directive) for group in groups]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(_process_group, index, model, group, directive)
            for group in groups
        ]
        return [future.result() for future in futures]


def revise_class_model(
    current: dict[str, Any],
    scenario: dict[str, Any],
    feedback: str,
    targets: set[str] | None = None,
) -> dict[str, Any]:
    """Apply feedback only through its owning unit and deterministic reassembly."""

    if not current or not feedback.strip():
        return current or {}
    index = build_scenario_index(scenario)
    scope = _feedback_scope(index, current, feedback, targets or set())
    inventory = _inventory_from_model(current)
    fragments = _fragments_from_model(index, current)
    existing = {
        text(item.get("collaborationId")): deepcopy(item)
        for item in current.get("Collaborations") or [] if isinstance(item, dict)
    }

    if scope.kind == "inventory":
        inventory = _revise_inventory(index, inventory, feedback, set(scope.ids))
        skeleton, fragments = _build_fragments(index, inventory)
        selected_groups = list(index.groups)
    elif scope.kind == "operation":
        selected_use_cases = set(scope.ids) or {use_case.id for use_case in index.use_cases}
        selected_operation_groups = [
            group for group in index.groups if group.use_case_id in selected_use_cases
        ]
        if selected_operation_groups:
            _repair_failed_operations(
                index,
                inventory,
                fragments,
                [
                    _GroupResult(
                        group.id,
                        None,
                        f"User feedback for this execution slice: {feedback}",
                    )
                    for group in selected_operation_groups
                ],
                operation="InteractionOperationFeedback",
            )
        for use_case_id in sorted(selected_use_cases, key=id_key):
            if any(group.use_case_id == use_case_id for group in selected_operation_groups):
                continue
            use_case = index.use_case(use_case_id)
            others = {key: value for key, value in fragments.items() if key != use_case_id}
            base = _compose_fragments(inventory, others)
            fragments[use_case_id] = _checked_fragment(
                index,
                inventory,
                use_case,
                previous=fragments.get(use_case_id),
                findings=[f"User feedback: {feedback}"],
                reserved=_reserved_operations(base),
                reserved_types=list(base.get("DataTypes") or []),
                operation="InteractionOperationFeedback",
            )
        skeleton = _compose_fragments(inventory, fragments)
        selected_groups = [
            group for group in index.groups
            if set(group.trace_use_case_ids) & selected_use_cases
        ]
    else:
        skeleton = BCEModel.model_validate({
            **deepcopy(current), "Collaborations": [],
        }).model_dump(by_alias=True)
        selected = set(scope.ids) or {group.id for group in index.groups}
        selected_groups = [group for group in index.groups if group.id in selected]

    selected_ids = {group.id for group in selected_groups}
    results = _run_selected_groups(
        index,
        skeleton,
        selected_groups,
        feedback=feedback if scope.kind == "collaboration" else "",
    )
    failures = [result for result in results if result.collaboration is None]
    if failures:
        raise ValueError("feedback could not produce accepted collaborations: " + "; ".join(
            f"{result.group_id}: {result.issue}" for result in failures
        ))
    replacements = {
        result.group_id: result.collaboration for result in results
        if result.collaboration is not None
    }
    collaborations = [
        replacements.get(group.id) or existing.get(group.id)
        for group in index.groups
        if (replacements.get(group.id) or existing.get(group.id)) is not None
    ]
    revised = BCEModel.model_validate({
        **skeleton,
        "Collaborations": collaborations,
    }).model_dump(by_alias=True)
    findings = final_model_findings(revised, index)
    if findings:
        raise ValueError("feedback result is invalid: " + "; ".join(
            f"{finding.location}: {finding.message}" for finding in findings
        ))
    return revised
