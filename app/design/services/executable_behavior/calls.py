"""UC-level call-structure generation and deterministic BCE validation."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.design.services.executable_behavior.contracts import (
    ValidatedCallStructure,
    ValidatedCatalogDraft,
    canonical_digest,
    make_provenance,
    provenance_matches_subject,
)


class CallValidationError(ValueError):
    """A proposed call structure violates the finite catalog or scenario slice."""


@dataclass(frozen=True)
class ActorEntry:
    group_key: str
    actor: str
    boundary_class: str
    required_step_refs: tuple[str, ...]


@dataclass(frozen=True)
class CallProposal:
    operation_key: str
    call_instance_key: str
    parent_call_instance_key: str | None = None
    group_key: str = ""
    guard_refs: tuple[str, ...] = ()
    export_result: bool = False


@dataclass(frozen=True)
class CallNode:
    call_instance_key: str
    operation_key: str
    parent_call_instance_key: str | None
    group_key: str
    step_refs: tuple[str, ...]
    guard_refs: tuple[str, ...] = ()
    export_result: bool = False


@dataclass(frozen=True)
class CallStructure:
    use_case_id: str
    calls: tuple[CallNode, ...]
    actor_entries: tuple[ActorEntry, ...]

    def as_payload(self) -> dict[str, Any]:
        return {
            "useCaseId": self.use_case_id,
            "actorEntries": [
                {
                    "groupKey": entry.group_key,
                    "actor": entry.actor,
                    "boundaryClass": entry.boundary_class,
                    "requiredStepRefs": list(entry.required_step_refs),
                }
                for entry in self.actor_entries
            ],
            "calls": [
                {
                    "callId": call.call_instance_key,
                    "receiverOperationId": call.operation_key,
                    "parentCallId": call.parent_call_instance_key,
                    "groupKey": call.group_key,
                    "stepRefs": list(call.step_refs),
                    "guardRefs": list(call.guard_refs),
                    "exportResult": call.export_result,
                }
                for call in self.calls
            ],
        }


def operation_key(operation: Mapping[str, Any]) -> str:
    return str(
        operation.get("operationId") or operation.get("operation_key") or ""
    ).strip()


def catalog_operations(catalog: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Flatten a BCE catalog while retaining its class role and identity."""
    result: dict[str, dict[str, Any]] = {}
    for class_item in catalog.get("Classes", []) or []:
        if not isinstance(class_item, Mapping):
            continue
        for operation in class_item.get("operations", []) or []:
            if not isinstance(operation, Mapping) or not operation_key(operation):
                continue
            result[operation_key(operation)] = {
                **dict(operation),
                "className": str(class_item.get("className") or ""),
                "stereotype": str(class_item.get("stereotype") or ""),
            }
    return result


def _actor_entry(value: ActorEntry | Mapping[str, Any]) -> ActorEntry:
    if isinstance(value, ActorEntry):
        return value
    return ActorEntry(
        group_key=str(value.get("groupKey") or value.get("group_key") or ""),
        actor=str(value.get("actor") or ""),
        boundary_class=str(
            value.get("boundaryClass") or value.get("boundary_class") or ""
        ),
        required_step_refs=tuple(
            str(item)
            for item in (
                value.get("requiredStepRefs")
                or value.get("required_step_refs")
                or ()
            )
        ),
    )


def _call_proposal(value: CallProposal | Mapping[str, Any]) -> CallProposal:
    if isinstance(value, CallProposal):
        return value
    parent = (
        value.get("parentCallInstanceKey")
        or value.get("parentCallId")
        or value.get("parent_call_instance_key")
    )
    return CallProposal(
        operation_key=str(
            value.get("operationKey")
            or value.get("receiverOperationId")
            or value.get("operation_key")
            or ""
        ),
        call_instance_key=str(
            value.get("callInstanceKey")
            or value.get("callId")
            or value.get("call_instance_key")
            or ""
        ),
        parent_call_instance_key=str(parent) if parent else None,
        group_key=str(value.get("groupKey") or value.get("group_key") or ""),
        guard_refs=tuple(
            str(item)
            for item in value.get("guardRefs") or value.get("guard_refs") or ()
        ),
        export_result=bool(
            value.get("exportResult") or value.get("export_result") or False
        ),
    )


def _stereotype(operation: Mapping[str, Any]) -> str:
    return str(operation.get("stereotype") or "").casefold()


class CallStructureBuilder:
    """Build a validated call forest from catalog-constrained choices."""

    def __init__(
        self,
        operations: Mapping[str, Mapping[str, Any]] | Iterable[Mapping[str, Any]],
    ) -> None:
        if isinstance(operations, Mapping):
            self.operations = {str(key): dict(value) for key, value in operations.items()}
        else:
            self.operations = {
                operation_key(item): dict(item)
                for item in operations
                if operation_key(item)
            }

    def build(
        self,
        use_case_id: str,
        proposals: Iterable[CallProposal | Mapping[str, Any]],
        *,
        actor_entries: Iterable[ActorEntry | Mapping[str, Any]],
    ) -> CallStructure:
        entries = tuple(_actor_entry(item) for item in actor_entries)
        if not use_case_id or not entries:
            raise CallValidationError("use case and at least one actor entry are required")
        if any(
            not entry.group_key
            or not entry.actor
            or not entry.boundary_class
            or not entry.required_step_refs
            for entry in entries
        ):
            raise CallValidationError(
                "every actor entry requires group, actor, Boundary, and steps"
            )
        if len({entry.group_key for entry in entries}) != len(entries):
            raise CallValidationError("actor entry group keys must be unique")

        nodes: list[CallNode] = []
        by_key: dict[str, CallNode] = {}
        root_groups: list[str] = []
        entry_by_group = {entry.group_key: entry for entry in entries}
        direct_control_groups: set[str] = set()

        for proposal in (_call_proposal(item) for item in proposals):
            if not proposal.call_instance_key or proposal.call_instance_key in by_key:
                raise CallValidationError("call instance keys must be non-empty and unique")
            operation = self.operations.get(proposal.operation_key)
            if operation is None:
                raise CallValidationError(f"unknown operation key: {proposal.operation_key}")

            parent = (
                by_key.get(proposal.parent_call_instance_key)
                if proposal.parent_call_instance_key is not None
                else None
            )
            if proposal.parent_call_instance_key is not None and parent is None:
                raise CallValidationError("parent must reference an earlier call instance")
            if parent is None:
                if _stereotype(operation) != "boundary":
                    raise CallValidationError("actor entry root must be Boundary")
                group_key = proposal.group_key
                if group_key not in entry_by_group or group_key in root_groups:
                    raise CallValidationError("roots must map one-to-one to actor entries")
                root_groups.append(group_key)
                entry = entry_by_group[group_key]
                class_name = str(operation.get("className") or "")
                if entry.boundary_class and class_name != entry.boundary_class:
                    raise CallValidationError("root Boundary does not represent the actor entry")
            else:
                group_key = proposal.group_key or parent.group_key
                if group_key != parent.group_key:
                    raise CallValidationError("a child call cannot change execution group")
                root = next(
                    item
                    for item in nodes
                    if item.group_key == group_key
                    and item.parent_call_instance_key is None
                )
                self._check_edge(
                    self.operations[parent.operation_key],
                    operation,
                    root_boundary_class=str(
                        self.operations[root.operation_key].get("className") or ""
                    ),
                )
                if (
                    parent.parent_call_instance_key is None
                    and _stereotype(operation) == "control"
                ):
                    direct_control_groups.add(group_key)

            entry = entry_by_group[group_key]
            operation_steps = {
                str(item) for item in operation.get("stepRefs", []) or []
            }
            step_refs = tuple(
                ref for ref in entry.required_step_refs if ref in operation_steps
            )
            if entry.required_step_refs and not step_refs:
                raise CallValidationError(
                    f"call {proposal.call_instance_key} owns no required step in its group"
                )
            node = CallNode(
                proposal.call_instance_key,
                proposal.operation_key,
                proposal.parent_call_instance_key,
                group_key,
                step_refs,
                proposal.guard_refs,
                proposal.export_result,
            )
            nodes.append(node)
            by_key[node.call_instance_key] = node

        if tuple(root_groups) != tuple(entry.group_key for entry in entries):
            raise CallValidationError("root order must match actor entry order")
        if direct_control_groups != set(entry_by_group):
            raise CallValidationError("each Boundary root must directly delegate to Control")
        for entry in entries:
            covered = {
                ref
                for node in nodes
                if node.group_key == entry.group_key
                for ref in node.step_refs
            }
            missing = set(entry.required_step_refs) - covered
            if missing:
                raise CallValidationError(
                    "call structure does not cover required steps: "
                    + ", ".join(sorted(missing))
                )
        return CallStructure(use_case_id, tuple(nodes), entries)

    @staticmethod
    def _check_edge(
        parent: Mapping[str, Any],
        child: Mapping[str, Any],
        *,
        root_boundary_class: str,
    ) -> None:
        source = _stereotype(parent)
        target = _stereotype(child)
        target_class = str(child.get("className") or "")
        if source == "boundary" and target != "control":
            raise CallValidationError("Boundary may delegate only to Control")
        if source == "entity" and target != "entity":
            raise CallValidationError("Entity may call only another Entity")
        if source == "control" and target not in {"control", "entity", "boundary"}:
            raise CallValidationError("Control target has an invalid BCE role")
        if (
            source == "control"
            and target == "boundary"
            and target_class == root_boundary_class
        ):
            raise CallValidationError("Control cannot call the actor-entry Boundary")


def validated_call_structure(
    catalog: ValidatedCatalogDraft,
    *,
    scenario: Mapping[str, Any],
    use_case_id: str,
    proposals: Iterable[CallProposal | Mapping[str, Any]],
    actor_entries: Iterable[ActorEntry | Mapping[str, Any]],
    revision: int = 1,
) -> ValidatedCallStructure:
    """Validate a call structure and pin it to catalog and scenario snapshots."""
    if not provenance_matches_subject(catalog.provenance, catalog.payload):
        raise CallValidationError("catalog content changed after validation")
    if catalog.provenance.input_digests.get("scenario") != canonical_digest(scenario):
        raise CallValidationError("catalog and call structure use different scenarios")
    structure = CallStructureBuilder(catalog_operations(catalog.payload)).build(
        use_case_id,
        proposals,
        actor_entries=actor_entries,
    )
    payload = structure.as_payload()
    return ValidatedCallStructure(
        useCaseId=use_case_id,
        payload=payload,
        provenance=make_provenance(
            revision=revision,
            inputs={
                "scenario": scenario,
                "catalog": catalog.model_dump(by_alias=True),
            },
            subject=payload,
            validator_version="executable-behavior.calls.v1",
        ),
    )


__all__ = [
    "ActorEntry",
    "CallNode",
    "CallProposal",
    "CallStructure",
    "CallStructureBuilder",
    "CallValidationError",
    "catalog_operations",
    "operation_key",
    "validated_call_structure",
]
