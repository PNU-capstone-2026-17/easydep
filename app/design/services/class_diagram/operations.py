"""수락된 인벤토리에서 BCE 연산 조각을 생성하고 조립한다."""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping, MutableMapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any

from app.core.config import settings
from app.core.validation import run_checks
from app.design import progress as design_progress
from app.design.schemas.class_model import BCEModel, canonical_operation_id
from app.design.services.class_diagram.inventory import finding_text
from app.design.services.class_diagram.models import (
    AcceptedFragment,
    AcceptedInventory,
    CollaborationResult,
)
from app.design.services.class_diagram.models import (
    Collision as _Collision,
)
from app.design.services.class_diagram.models import (
    DataTypeCollision as _DataTypeCollision,
)
from app.design.services.class_diagram.models import (
    OperationUnit as _OperationUnit,
)
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.proposals import OperationFragment
from app.design.services.class_diagram.scenario import (
    ExecutionGroup,
    ScenarioIndex,
    UseCase,
    id_key,
    text,
)
from app.design.services.class_diagram.type_system import (
    field_type,
    reachable_data_type_names,
    referenced_type_names,
    structure_type_contract,
    structured_field_types,
    types_compatible,
)
from app.design.services.class_diagram.validation import OPERATION_CHECKS, OperationContext
from app.design.services.class_diagram.validation.model import (
    class_name,
    runtime_value_source,
    type_can_default,
)
from app.design.services.common import fields
from app.design.services.common.structured import parse_structured

logger = logging.getLogger(__name__)


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


def _propose_fragment(
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
    candidate = _propose_fragment(
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
    report = run_checks(OPERATION_CHECKS, candidate, context)
    if report.errors:
        raise RuntimeError("; ".join(report.errors))
    if report.findings:
        candidate = _propose_fragment(
            index,
            inventory,
            use_case,
            previous=candidate,
            findings=finding_text(report.findings),
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
        report = run_checks(OPERATION_CHECKS, candidate, context)
    if report.errors or report.findings:
        raise ValueError(
            f"operation fragment {use_case.id} remains invalid: "
            + "; ".join([*report.errors, *finding_text(report.findings)])
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


def emit_preview(
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
    *,
    reconstruct_fragments: Callable[[ScenarioIndex, dict[str, Any]], dict[str, dict[str, Any]]],
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
            emit_preview(candidate, "operations", unit.id, position + 1, len(units) + 1)
    skeleton = _compose(inventory, committed, final=True)
    return skeleton, reconstruct_fragments(index, skeleton)


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
    failures: list[CollaborationResult],
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
        merged_classes: dict[str, dict[str, Any]] = {
            text(item.get("className")): deepcopy(item)
            for item in preserved_classes
        }
        for class_set in candidate.get("Classes") or []:
            if not isinstance(class_set, dict):
                continue
            owner = text(class_set.get("className"))
            target = merged_classes.setdefault(
                owner, {"className": owner, "operations": []},
            )
            target_operations = target.get("operations")
            if isinstance(target_operations, list):
                target_operations.extend(deepcopy(class_set.get("operations") or []))
        fragments[use_case_id] = {
            "DataTypes": list(merged_types.values()),
            "Classes": list(merged_classes.values()),
        }
        repaired.add(use_case_id)
    return repaired


def reserved_operations(model: BCEModel) -> list[dict[str, Any]]:
    """수락된 모델에서 이후 조각 생성에 예약할 연산을 읽는다."""
    return _reserved_operations(model.model_dump(by_alias=True))


def propose_fragment(
    index: ScenarioIndex,
    inventory: AcceptedInventory,
    use_case: UseCase,
    *,
    previous: AcceptedFragment | None = None,
    findings: list[str] | None = None,
    reserved: list[dict[str, Any]] | None = None,
    reserved_types: list[dict[str, Any]] | None = None,
    allowed_step_ids: tuple[str, ...] = (),
    execution_group_id: str = "",
    operation: str = "InteractionOperation",
) -> AcceptedFragment:
    """고정 인벤토리 안에서 한 유스케이스 연산 조각을 제안한다."""
    candidate = _propose_fragment(
        index,
        inventory.as_payload(),
        use_case,
        previous=previous.as_payload() if previous else None,
        findings=findings,
        reserved=reserved,
        reserved_types=reserved_types,
        allowed_step_ids=allowed_step_ids,
        execution_group_id=execution_group_id,
        operation=operation,
    )
    return AcceptedFragment(use_case_id=use_case.id, payload=candidate)


def checked_fragment(
    index: ScenarioIndex,
    inventory: AcceptedInventory,
    use_case: UseCase,
    **kwargs: Any,
) -> AcceptedFragment:
    """검사와 수리를 거친 수락 연산 조각을 생성한다."""
    previous = kwargs.pop("previous", None)
    candidate = _checked_fragment(
        index,
        inventory.as_payload(),
        use_case,
        previous=previous.as_payload() if isinstance(previous, AcceptedFragment) else previous,
        **kwargs,
    )
    return AcceptedFragment(use_case_id=use_case.id, payload=candidate)


def build_fragments(
    index: ScenarioIndex,
    inventory: AcceptedInventory,
    *,
    reconstruct_fragments: Callable[[ScenarioIndex, BCEModel], Mapping[str, AcceptedFragment]],
) -> tuple[BCEModel, dict[str, AcceptedFragment]]:
    """유스케이스 조각을 병렬 생성하고 불변 모델 경계로 수락한다."""
    def reconstruct_raw(
        scenario_index: ScenarioIndex, skeleton: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        accepted = reconstruct_fragments(
            scenario_index, BCEModel.model_validate(skeleton),
        )
        return {key: value.as_payload() for key, value in accepted.items()}

    skeleton, fragments = _build_fragments(
        index,
        inventory.as_payload(),
        reconstruct_fragments=reconstruct_raw,
    )
    return (
        BCEModel.model_validate(skeleton),
        {
            use_case_id: AcceptedFragment(use_case_id=use_case_id, payload=fragment)
            for use_case_id, fragment in fragments.items()
        },
    )


def compose_fragments(
    inventory: AcceptedInventory,
    fragments: Mapping[str, AcceptedFragment],
) -> BCEModel:
    """수락된 조각을 하나의 BCE 모델로 조립한다."""
    return BCEModel.model_validate(_compose_fragments(
        inventory.as_payload(),
        {key: value.as_payload() for key, value in fragments.items()},
    ))


def repair_failed_operations(
    index: ScenarioIndex,
    inventory: AcceptedInventory,
    fragments: MutableMapping[str, AcceptedFragment],
    failures: list[CollaborationResult],
    *,
    operation: str = "InteractionOperationHandoff",
) -> set[str]:
    """협업 실패 그룹만 재생성하고 수락 조각을 제자리에서 갱신한다."""
    raw_fragments = {key: value.as_payload() for key, value in fragments.items()}
    repaired = _repair_failed_operations(
        index,
        inventory.as_payload(),
        raw_fragments,
        failures,
        operation=operation,
    )
    fragments.clear()
    fragments.update({
        use_case_id: AcceptedFragment(use_case_id=use_case_id, payload=fragment)
        for use_case_id, fragment in raw_fragments.items()
    })
    return repaired




__all__ = [
    "build_fragments",
    "checked_fragment",
    "compose_fragments",
    "emit_preview",
    "propose_fragment",
    "repair_failed_operations",
    "reserved_operations",
]



