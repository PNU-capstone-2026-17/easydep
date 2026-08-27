"""Feedback scope, model reconstruction, and selected collaboration replacement."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any

from app.core.config import settings
from app.core.validation import run_checks
from app.design.services.class_diagram import collaboration, inventory
from app.design.services.class_diagram.models import GroupResult
from app.design.services.class_diagram.proposals import (
    FeedbackScope,
    InventoryProposal,
)
from app.design.services.class_diagram.scenario import (
    ExecutionGroup,
    ScenarioIndex,
    id_key,
    text,
)
from app.design.services.class_diagram.type_system import (
    field_name,
    field_type,
    referenced_type_names,
)
from app.design.services.class_diagram.validation.inventory import INVENTORY_CHECKS
from app.design.services.class_diagram.validation.model import class_name
from app.design.services.common.structured import parse_structured


def inventory_from_model(model: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the fixed inventory without operation-local declaration noise."""

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
    data_types = {name: item for name, item in all_data_types.items() if name in structural_names}
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


def inventory_as_proposal(inventory_model: dict[str, Any]) -> dict[str, Any]:
    """Rehydrate a persisted inventory into the LLM proposal contract."""

    items: list[dict[str, Any]] = []
    for item in inventory_model.get("Classes") or []:
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
    for item in inventory_model.get("DataTypes") or []:
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
    return {
        "items": items,
        "Relationships": deepcopy(inventory_model.get("Relationships") or []),
    }


def fragments_from_model(
    index: ScenarioIndex, model: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Recover each operation-local fragment from the persisted BCE model."""

    inventory_type_names = {
        text(item.get("name"))
        for item in inventory_from_model(model).get("DataTypes") or []
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
                        if target in all_types and target not in inventory_type_names and target not in local_names:
                            local_names.add(target)
                            pending.append(target)
            result[use_case.id] = {
                "DataTypes": [deepcopy(all_types[name]) for name in sorted(local_names)],
                "Classes": class_sets,
            }
    return result


def feedback_scope(
    index: ScenarioIndex,
    model: dict[str, Any],
    feedback: str,
    targets: set[str],
) -> FeedbackScope:
    """Resolve the smallest mutable owner before asking an LLM when necessary."""

    inventory_ids = {
        class_name(item) for item in model.get("Classes") or [] if isinstance(item, dict)
    } | {
        text(item.get("name"))
        for item in inventory_from_model(model).get("DataTypes") or []
        if isinstance(item, dict)
    }
    fragments = fragments_from_model(index, model)
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
                use_case_id for target in targets for use_case_id in local_type_owners[target]
            }
            return FeedbackScope(kind="operation", ids=sorted(owners, key=id_key))
    mentioned_local_owners = {
        use_case_id
        for name, owners in local_type_owners.items()
        if name.casefold() in feedback.casefold()
        for use_case_id in owners
    }
    if mentioned_local_owners:
        return FeedbackScope(kind="operation", ids=sorted(mentioned_local_owners, key=id_key))
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


def propose_inventory_revision(
    index: ScenarioIndex,
    inventory_model: dict[str, Any],
    feedback: str,
    target_ids: set[str],
) -> dict[str, Any]:
    """Request and validate a full inventory replacement, retaining untargeted owners."""

    current = inventory_as_proposal(inventory_model)
    parsed = parse_structured(
        [
            {"role": "system", "content": inventory.INVENTORY_PROMPT},
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
    candidate = inventory.normalize_inventory(proposal)
    report = run_checks(INVENTORY_CHECKS, candidate, index, parallel=True)
    if report.errors or report.findings:
        raise ValueError("inventory feedback is invalid: " + "; ".join([
            *report.errors, *inventory.finding_text(report.findings),
        ]))
    return candidate


def replace_selected_groups(
    index: ScenarioIndex,
    model: dict[str, Any],
    groups: list[ExecutionGroup],
    *,
    feedback: str = "",
    workers: int | None = None,
) -> list[GroupResult]:
    """Replan only selected groups while preserving bounded parallelism and repair."""

    workers = max(1, min(
        workers if workers is not None else int(
            getattr(settings, "design_class_behavior_parallelism", 2),
        ),
        len(groups) or 1,
    ))
    directive = f"Apply this user feedback to this call plan only: {feedback}" if feedback else ""
    if workers == 1 or len(groups) <= 1:
        return [collaboration.process_group(index, model, group, directive) for group in groups]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(collaboration.process_group, index, model, group, directive)
            for group in groups
        ]
        return [future.result() for future in futures]


revise_inventory = propose_inventory_revision
run_selected_groups = replace_selected_groups

__all__ = [
    "feedback_scope",
    "fragments_from_model",
    "inventory_as_proposal",
    "inventory_from_model",
    "propose_inventory_revision",
    "replace_selected_groups",
    "revise_inventory",
    "run_selected_groups",
]



