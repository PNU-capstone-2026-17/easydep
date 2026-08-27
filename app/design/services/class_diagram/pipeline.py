"""Owned-unit generation for the executable BCE class model.

The inventory owns domain structure.  One immutable fragment owns the
operations of one use case.  Calls and value provenance remain the concern of
the collaboration stage.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import settings
from app.core.validation import CheckSpec, Finding, run_checks
from app.design import progress as design_progress
from app.design.schemas.class_model import (
    BCEModel,
    ClassParameter,
    canonical_operation_id,
)
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.behavior import execution_groups
from app.design.services.class_diagram.type_system import (
    field_name,
    field_type,
    reachable_data_type_names,
    structured_field_types,
    structure_type_contract,
    type_is_resolved,
    types_compatible,
)
from app.design.services.common import fields
from app.design.services.common.structured import parse_structured


class _Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class InventoryField(_Proposal):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    type: str = Field(min_length=1)


class InventoryItem(_Proposal):
    name: str = Field(min_length=1)
    kind: Literal["Boundary", "Control", "Entity", "valueObject", "enumeration"]
    description: str = ""
    fields: list[InventoryField] = Field(default_factory=list)
    identifier: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def definition_matches_kind(self) -> "InventoryItem":
        field_names = {item.name for item in self.fields}
        if self.kind in {"Boundary", "Control"} and (
            self.fields or self.identifier or self.values
        ):
            raise ValueError("Boundary and Control items cannot retain fields or values")
        if self.kind == "Entity" and (not self.fields or self.values):
            raise ValueError("Entity requires typed persistent fields and cannot declare values")
        if self.kind == "Entity" and not set(self.identifier) <= field_names:
            raise ValueError("Entity identifiers must name declared fields")
        if self.kind == "valueObject" and (
            not self.fields or self.identifier or self.values
        ):
            raise ValueError("valueObject requires fields and cannot declare values")
        if self.kind == "enumeration" and (
            not self.values or self.fields or self.identifier
        ):
            raise ValueError("enumeration requires values and cannot declare fields")
        return self


class InventoryRelationship(_Proposal):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    type: Literal["Association", "Aggregation", "Composition", "Inheritance"]
    source_multiplicity: str = Field(alias="sourceMultiplicity", min_length=1)
    target_multiplicity: str = Field(alias="targetMultiplicity", min_length=1)
    description: str = ""


class ClassInventoryProposal(_Proposal):
    items: list[InventoryItem] = Field(min_length=1)
    Relationships: list[InventoryRelationship] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_names(self) -> "ClassInventoryProposal":
        names = [item.name for item in self.items]
        if len(names) != len(set(names)):
            raise ValueError("inventory item names must be unique")
        return self


CanonicalStepRef = Annotated[
    str,
    Field(pattern=r"^[^:\s]+:(?:main:[^:\s]+|extension:[^:\s]+:[^:\s]+)$"),
]


class ProposedOperation(_Proposal):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    parameters: list[ClassParameter] = Field(default_factory=list)
    return_type: str = Field(alias="returnType", min_length=1)
    step_refs: list[CanonicalStepRef] = Field(alias="stepRefs", min_length=1)

    @field_validator("step_refs")
    @classmethod
    def normalize_step_refs(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(str(value).strip() for value in values))

    @model_validator(mode="after")
    def unique_parameter_names(self) -> "ProposedOperation":
        names = [item.name for item in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("operation parameter names must be unique")
        return self


class OperationSet(_Proposal):
    class_name: str = Field(alias="className", min_length=1)
    operations: list[ProposedOperation] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_operation_names(self) -> "OperationSet":
        names = [item.name for item in self.operations]
        if len(names) != len(set(names)):
            raise ValueError("operation names must be unique within a class fragment")
        return self


class UseCaseOperationFragment(_Proposal):
    Classes: list[OperationSet] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_class_sets(self) -> "UseCaseOperationFragment":
        names = [item.class_name for item in self.Classes]
        if len(names) != len(set(names)):
            raise ValueError("a fragment may contain each class once")
        return self


_INVENTORY_SYSTEM = """
Propose the fixed BCE type inventory for the supplied use-case specification.
Return one items list and Entity-to-Entity structural relationships. Classify
each item exactly once as Boundary, Control, Entity, valueObject, or enumeration.
Do not return operations, methods, use-case ids, calls, dependencies, or
collaborations.

Boundary represents an actor or external-system interface. Control coordinates
use-case behavior. Entity owns persistent business state that outlives one
interaction. Requests, responses, selections, and results are DataTypes rather
than Entity classes. Boundary and Control have no retained fields. Every Entity
has grounded typed state fields; each identifier also names one of those fields.
Use cohesive classes across related use cases rather than one class per sentence.
Boundary classes represent interaction channels or cohesive interfaces, not one
Boundary per use-case title. Control classes represent cohesive domain
capabilities and may coordinate several related use cases; do not mirror the
use-case list with one Control per item. An actor role is not automatically a
Class or Entity. Model a role as an enumeration or responsibility of a grounded
identity class unless the specification gives that role distinct persistent
state of its own.
Classify requests, search criteria, selections, responses, summaries, details,
results, export payloads, and other transient data as valueObject. Classify a
finite set of roles, states, statuses, or formats as enumeration. These items
are not Entities even when their values are important to a use case. Declare
only value objects and enumerations that operation inputs or results will need
later; do not invent business concepts. Structural relationships are
optional and may connect only independently grounded Entity classes.
Every structural relationship must declare both endpoint multiplicities and
must appear once; do not emit a second inverse-direction copy of the same
semantic association.
""".strip() + "\n\n" + structure_type_contract()


_OPERATION_SYSTEM = """
Propose the complete operation fragment for exactly one use case inside a fixed
BCE inventory. Return only the supplied classes and operations schema. Use only
listed class names and declared types; never add or rename a class or type.

An actor-driven interaction enters through Boundary and delegates to Control.
Persistent behavior delegates from Control to the responsible Entity. An
internal included use case may start at Control. Cover every supplied executable
main and extension step with exact stepRefs, grouping adjacent steps into
cohesive reusable operations rather than creating one method per sentence.
Select every stepRef exactly from the supplied allowedStepRefs list.
Boundary parameters are actor inputs and its return is the actor-visible result.
One actor-facing Boundary operation may trace both the request and later
presentation, confirmation, or error steps fulfilled by its return. Do not add
a separate display, notify, or inform operation merely to cover those output
steps.
Control and Entity parameters must be sourceable from those inputs, a typed
field, or a previous operation result. Do not use preconditions as parameter
values and do not require unstated generated ids, clocks, or defaults as caller
inputs. Use non-void returns for stated data or outcomes. Do not return calls,
argument bindings, relationships, operation ids, or use-case ids.
Operations listed in reservedOperations belong to already accepted use cases.
For the same class and operation name, either reuse its exact parameter and
return signature or choose a distinct cohesive name; never overload that name
with a different signature.
""".strip() + "\n\n" + structure_type_contract()


def _id_key(value: str) -> tuple[Any, ...]:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", value)
    )


def _specifications(scenario: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        [
            item for item in scenario.get("use_case_specs") or []
            if isinstance(item, dict) and str(item.get("use_case_id") or "").strip()
        ],
        key=lambda item: _id_key(str(item.get("use_case_id") or "")),
    )


def _required_steps(specification: dict[str, Any]) -> set[str]:
    use_case_id = str(specification.get("use_case_id") or "").strip()
    result = {
        f"{use_case_id}:main:{step['step_number']}"
        for step in specification.get("main_scenario") or []
        if isinstance(step, dict) and step.get("step_number") is not None
    }
    for extension in specification.get("extensions") or []:
        if not isinstance(extension, dict):
            continue
        label = str(extension.get("label") or "").strip()
        result.update(
            f"{use_case_id}:extension:{label}:{step['sub_step']}"
            for step in extension.get("handling_steps") or []
            if label and isinstance(step, dict) and step.get("sub_step") is not None
        )
    return result


def _primary_actor(scenario: dict[str, Any], use_case_id: str) -> str:
    for item in scenario.get("use_cases") or []:
        if isinstance(item, dict) and str(item.get("id") or "") == use_case_id:
            return str(item.get("primary_actor") or "").strip()
    return ""


def _normalize_inventory(proposal: ClassInventoryProposal) -> dict[str, Any]:
    raw = proposal.model_dump(by_alias=True)
    classes: list[dict[str, Any]] = []
    data_types: list[dict[str, Any]] = []
    for item in raw["items"]:
        typed_fields = [
            fields.normalize_java_field(f"{value['name']} : {value['type']}")
            for value in item["fields"]
        ]
        if item["kind"] in {"Boundary", "Control", "Entity"}:
            classes.append({
                "className": item["name"],
                "stereotype": item["kind"],
                "description": item["description"],
                "fields": typed_fields,
                "identifier": list(item["identifier"]),
            })
        else:
            data_types.append({
                "name": item["name"],
                "kind": item["kind"],
                "fields": typed_fields,
                "values": list(item["values"]),
            })
    return {
        "Classes": classes,
        "DataTypes": data_types,
        "Relationships": raw["Relationships"],
    }


def _inventory_model(inventory: dict[str, Any]) -> dict[str, Any]:
    return {
        "Classes": [
            {**item, "use_case_ids": [], "operations": []}
            for item in inventory.get("Classes") or []
        ],
        "DataTypes": list(inventory.get("DataTypes") or []),
        "Relationships": list(inventory.get("Relationships") or []),
        "Collaborations": [],
    }


def _inventory_names_check(
    inventory: dict[str, Any], _scenario: dict[str, Any],
) -> list[Finding]:
    findings: list[Finding] = []
    for item in inventory.get("Classes") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("className") or "")
        if not re.fullmatch(r"[A-Z][A-Za-z0-9]*", name):
            findings.append(Finding(
                "class.inventory.names", "className must be concrete PascalCase", name,
            ))
        if "unknownclass" in name.casefold().replace(" ", ""):
            findings.append(Finding(
                "class.inventory.names", "placeholder class names are forbidden", name,
            ))
    return findings


def _inventory_fields_check(
    inventory: dict[str, Any], _scenario: dict[str, Any],
) -> list[Finding]:
    findings: list[Finding] = []
    classes = {
        str(item.get("className") or ""): item
        for item in inventory.get("Classes") or [] if isinstance(item, dict)
    }
    names = set(classes) | {
        str(item.get("name") or "")
        for item in inventory.get("DataTypes") or [] if isinstance(item, dict)
    }
    for name, item in classes.items():
        stereotype = str(item.get("stereotype") or "")
        declared_fields = list(item.get("fields") or [])
        if stereotype == "Entity" and not declared_fields:
            findings.append(Finding(
                "class.inventory.fields",
                "Entity requires grounded persistent typed fields; if this name "
                "represents finite roles or states, move it to an enumeration "
                "DataType with values instead",
                name,
            ))
        if stereotype != "Entity" and declared_fields:
            findings.append(Finding(
                "class.inventory.fields", "Boundary and Control cannot retain fields", name,
            ))
        field_names = {field_name(value) for value in declared_fields}
        for value in declared_fields:
            type_name = field_type(value)
            if not field_name(value) or not type_is_resolved(type_name, names, allow_void=False):
                findings.append(Finding(
                    "class.inventory.fields", f"field type does not resolve: {value}", name,
                ))
        missing = sorted(set(item.get("identifier") or []) - field_names)
        if missing:
            findings.append(Finding(
                "class.inventory.fields",
                f"identifiers must name typed fields: {missing}",
                name,
            ))
    for item in inventory.get("DataTypes") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        kind = str(item.get("kind") or "")
        declared_fields = list(item.get("fields") or [])
        if kind == "enumeration" and declared_fields:
            findings.append(Finding(
                "class.inventory.fields",
                "enumeration literals belong in values, not fields",
                name,
            ))
        for value in declared_fields:
            if not field_name(value) or not type_is_resolved(
                field_type(value), names, allow_void=False,
            ):
                findings.append(Finding(
                    "class.inventory.fields",
                    f"DataType field must use a resolved 'name : Type': {value}",
                    name,
                ))
    return findings


def _inventory_relationship_check(
    inventory: dict[str, Any], _scenario: dict[str, Any],
) -> list[Finding]:
    classes = {
        str(item.get("className") or ""): item
        for item in inventory.get("Classes") or [] if isinstance(item, dict)
    }
    findings: list[Finding] = []
    for item in inventory.get("Relationships") or []:
        if not isinstance(item, dict):
            continue
        source, target = str(item.get("source") or ""), str(item.get("target") or "")
        if (
            source not in classes
            or target not in classes
            or classes[source].get("stereotype") != "Entity"
            or classes[target].get("stereotype") != "Entity"
        ):
            findings.append(Finding(
                "class.inventory.relationships",
                "structural relationships may connect only declared Entity classes",
                f"{source}->{target}",
            ))
        if not str(item.get("sourceMultiplicity") or "").strip() or not str(
            item.get("targetMultiplicity") or ""
        ).strip():
            findings.append(Finding(
                "class.inventory.relationships",
                "structural relationships require both endpoint multiplicities",
                f"{source}->{target}",
            ))
    return findings


CLASS_INVENTORY_CHECKS = (
    CheckSpec("class.inventory.names", _inventory_names_check),
    CheckSpec("class.inventory.fields", _inventory_fields_check),
    CheckSpec("class.inventory.relationships", _inventory_relationship_check),
)


def _operation_reference_check(
    fragment: dict[str, Any], context: dict[str, Any],
) -> list[Finding]:
    inventory, use_case_id = context["inventory"], context["use_case_id"]
    class_names = {
        str(item.get("className") or "")
        for item in inventory.get("Classes") or [] if isinstance(item, dict)
    }
    type_names = class_names | {
        str(item.get("name") or "")
        for item in inventory.get("DataTypes") or [] if isinstance(item, dict)
    }
    required = set(context["required_steps"])
    findings: list[Finding] = []
    for class_set in fragment.get("Classes") or []:
        if not isinstance(class_set, dict):
            continue
        class_name = str(class_set.get("className") or "")
        if class_name not in class_names:
            findings.append(Finding(
                "class.operation.references", "operation uses a class outside the inventory", class_name,
            ))
        for operation in class_set.get("operations") or []:
            if not isinstance(operation, dict):
                continue
            location = f"{use_case_id}:{class_name}.{operation.get('name')}"
            refs = {str(value) for value in operation.get("stepRefs") or []}
            if not refs or not refs <= required:
                findings.append(Finding(
                    "class.operation.references", "operation stepRefs must belong to this use case", location,
                ))
            for parameter in operation.get("parameters") or []:
                if isinstance(parameter, dict) and not type_is_resolved(
                    str(parameter.get("type") or ""), type_names, allow_void=False,
                ):
                    findings.append(Finding(
                        "class.operation.references", "parameter type does not resolve", location,
                    ))
            if not type_is_resolved(
                str(operation.get("returnType") or ""), type_names, allow_void=True,
            ):
                findings.append(Finding(
                    "class.operation.references", "return type does not resolve", location,
                ))
    return findings


def _operation_coverage_check(
    fragment: dict[str, Any], context: dict[str, Any],
) -> list[Finding]:
    use_case_id = context["use_case_id"]
    classes = {
        str(item.get("className") or ""): str(item.get("stereotype") or "")
        for item in context["inventory"].get("Classes") or [] if isinstance(item, dict)
    }
    participating = {
        classes.get(str(item.get("className") or ""), "")
        for item in fragment.get("Classes") or [] if isinstance(item, dict)
    }
    covered = {
        str(ref)
        for item in fragment.get("Classes") or [] if isinstance(item, dict)
        for operation in item.get("operations") or [] if isinstance(operation, dict)
        for ref in operation.get("stepRefs") or []
    }
    findings: list[Finding] = []
    missing = sorted(set(context["required_steps"]) - covered)
    if missing:
        findings.append(Finding(
            "class.operation.coverage", f"operations do not cover steps: {missing}", use_case_id,
        ))
    if "Control" not in participating:
        findings.append(Finding(
            "class.operation.coverage", "use case requires a Control operation", use_case_id,
        ))
    if context["primary_actor"] and "Boundary" not in participating:
        findings.append(Finding(
            "class.operation.coverage", "actor-driven use case requires a Boundary operation", use_case_id,
        ))
    return findings


def _operation_execution_group_check(
    fragment: dict[str, Any], context: dict[str, Any],
) -> list[Finding]:
    """Keep operation ownership finite before call ordering begins."""

    use_case_id = context["use_case_id"]
    stereotypes = {
        str(item.get("className") or ""): str(item.get("stereotype") or "")
        for item in context["inventory"].get("Classes") or []
        if isinstance(item, dict)
    }
    operations: list[tuple[str, str, str, set[str]]] = []
    for class_set in fragment.get("Classes") or []:
        if not isinstance(class_set, dict):
            continue
        class_name = str(class_set.get("className") or "")
        for operation in class_set.get("operations") or []:
            if isinstance(operation, dict):
                operations.append((
                    class_name,
                    stereotypes.get(class_name, ""),
                    str(operation.get("name") or ""),
                    {str(ref) for ref in operation.get("stepRefs") or []},
                ))

    findings: list[Finding] = []
    groups = [
        group for group in execution_groups(context["scenario"])
        if group.use_case_id == use_case_id
    ]
    actor_entries = {group.actor_step for group in groups if group.actor_step}
    for group in groups:
        if group.actor_step:
            owners = [
                item for item in operations
                if item[1] == "Boundary" and group.actor_step in item[3]
            ]
            if len(owners) != 1:
                findings.append(Finding(
                    "class.operation.execution-groups",
                    "each actor entry step must be owned by exactly one Boundary operation",
                    group.id,
                ))
        if not any(
            stereotype == "Control" and refs & set(group.step_ids)
            for _class_name, stereotype, _name, refs in operations
        ):
            findings.append(Finding(
                "class.operation.execution-groups",
                "each execution group requires an in-group Control operation",
                group.id,
            ))

    for class_name, _stereotype, operation_name, refs in operations:
        if len(refs & actor_entries) > 1:
            findings.append(Finding(
                "class.operation.execution-groups",
                "one Boundary operation cannot merge separate actor entries",
                f"{use_case_id}:{class_name}.{operation_name}",
            ))
    owners_by_class_step: dict[tuple[str, str], list[str]] = {}
    for class_name, _stereotype, operation_name, refs in operations:
        for ref in refs:
            owners_by_class_step.setdefault((class_name, ref), []).append(operation_name)
    for (class_name, step_ref), names in owners_by_class_step.items():
        if len(names) > 1:
            findings.append(Finding(
                "class.operation.execution-groups",
                "one class must expose one cohesive operation for the same scenario step",
                f"{use_case_id}:{class_name}:{step_ref}",
            ))
    return findings


def _operation_value_flow_check(
    fragment: dict[str, Any], context: dict[str, Any],
) -> list[Finding]:
    """Require one finite provenance path for every non-entry parameter."""

    use_case_id = context["use_case_id"]
    inventory = context["inventory"]
    stereotypes = {
        str(item.get("className") or ""): str(item.get("stereotype") or "")
        for item in inventory.get("Classes") or [] if isinstance(item, dict)
    }
    fields_by_type = structured_field_types(inventory)
    operations = [
        {
            "className": str(class_set.get("className") or ""),
            "stereotype": stereotypes.get(str(class_set.get("className") or ""), ""),
            **operation,
        }
        for class_set in fragment.get("Classes") or [] if isinstance(class_set, dict)
        for operation in class_set.get("operations") or [] if isinstance(operation, dict)
    ]
    findings: list[Finding] = []

    for group in execution_groups(context["scenario"]):
        if group.use_case_id != use_case_id:
            continue
        order = {step_ref: index for index, step_ref in enumerate(group.step_ids)}
        scoped = [
            operation for operation in operations
            if set(str(ref) for ref in operation.get("stepRefs") or []) & set(order)
        ]
        roots = [
            operation for operation in scoped
            if operation["stereotype"] == "Boundary"
            and group.actor_step in set(operation.get("stepRefs") or [])
        ] if group.actor_step else [
            operation for operation in scoped
            if operation["stereotype"] == "Control"
        ]
        if len(roots) != 1:
            # The execution-group ownership rule reports this first; without a
            # unique entry there is no honest provenance origin to inspect.
            continue
        root = roots[0]
        available_values: list[tuple[str, str]] = []
        available_results: list[str] = []

        def add_value(name: str, type_name: str) -> None:
            if name and type_name and (name, type_name) not in available_values:
                available_values.append((name, type_name))
            for field, projected_type in fields_by_type.get(type_name, {}).items():
                if (field, projected_type) not in available_values:
                    available_values.append((field, projected_type))

        for parameter in root.get("parameters") or []:
            if isinstance(parameter, dict):
                add_value(str(parameter.get("name") or ""), str(parameter.get("type") or ""))

        def sourceable(parameter: dict[str, Any]) -> bool:
            name = str(parameter.get("name") or "")
            type_name = str(parameter.get("type") or "")
            return any(
                source_name == name and types_compatible(source_type, type_name)
                for source_name, source_type in available_values
            ) or any(
                types_compatible(result_type, type_name)
                for result_type in available_results
            )

        reachable = {id(root)}
        orders: dict[int, list[dict[str, Any]]] = {}
        for operation in scoped:
            refs = [str(ref) for ref in operation.get("stepRefs") or [] if str(ref) in order]
            if refs:
                orders.setdefault(min(order[ref] for ref in refs), []).append(operation)
        for position in sorted(orders):
            pending = [item for item in orders[position] if id(item) not in reachable]
            progressed = True
            while progressed:
                progressed = False
                for operation in list(pending):
                    parameters = [
                        item for item in operation.get("parameters") or []
                        if isinstance(item, dict)
                    ]
                    if not all(sourceable(parameter) for parameter in parameters):
                        continue
                    reachable.add(id(operation))
                    for parameter in parameters:
                        add_value(
                            str(parameter.get("name") or ""),
                            str(parameter.get("type") or ""),
                        )
                    pending.remove(operation)
                    progressed = True
            for operation in orders[position]:
                if id(operation) not in reachable:
                    for parameter in operation.get("parameters") or []:
                        if isinstance(parameter, dict) and not sourceable(parameter):
                            findings.append(Finding(
                                "class.operation.value-flow",
                                "operation parameter has no finite source from entry "
                                "inputs, structured fields, or an earlier result",
                                (
                                    f"{group.id}:{operation['className']}."
                                    f"{operation.get('name')}#{parameter.get('name')}"
                                ),
                            ))
                return_type = str(operation.get("returnType") or "")
                if id(operation) in reachable and return_type.casefold() != "void":
                    available_results.append(return_type)
                    for field, projected_type in fields_by_type.get(return_type, {}).items():
                        add_value(field, projected_type)
    return findings


CLASS_OPERATION_CHECKS = (
    CheckSpec("class.operation.references", _operation_reference_check),
    CheckSpec("class.operation.coverage", _operation_coverage_check),
    CheckSpec("class.operation.execution-groups", _operation_execution_group_check),
    CheckSpec("class.operation.value-flow", _operation_value_flow_check),
)


def _finding_text(report) -> list[str]:
    if report.errors:
        raise RuntimeError("; ".join(report.errors))
    return [
        f"{item.location}: {item.message}" if item.location else item.message
        for item in report.findings
    ]


def _generate_inventory(scenario: dict[str, Any]) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": _INVENTORY_SYSTEM},
        {"role": "user", "content": json.dumps(scenario, ensure_ascii=False)},
    ]
    parsed = parse_structured(
        messages,
        ClassInventoryProposal,
        reasoning_effort=settings.design_reasoning_effort,
        max_completion_tokens=settings.design_class_structure_max_completion_tokens,
        operation="ClassInventoryProposal",
    )
    candidate = _normalize_inventory(ClassInventoryProposal.model_validate(parsed))
    finding_history: list[dict[str, Any]] = []
    findings = _finding_text(
        run_checks(CLASS_INVENTORY_CHECKS, candidate, scenario, parallel=True)
    )
    # A structure correction can expose a previously hidden type-closure
    # defect (for example, after moving a finite state from Entity to enum).
    # Keep this repair local to the one Inventory and cap it at two attempts.
    for attempt in range(1, 3):
        if not findings:
            break
        finding_history.append({"attempt": attempt, "findings": findings})
        parsed = parse_structured(
            [
                *messages,
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": (
                                "Repair only the candidate inventory so every finding "
                                "is resolved. Preserve already valid classes, DataTypes, "
                                "and relationships. Return the full replacement inventory."
                            ),
                            "candidate": candidate,
                            "findingHistory": finding_history,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            ClassInventoryProposal,
            reasoning_effort=settings.design_reasoning_effort,
            max_completion_tokens=settings.design_class_structure_max_completion_tokens,
            operation="ClassInventoryRepair",
        )
        candidate = _normalize_inventory(ClassInventoryProposal.model_validate(parsed))
        findings = _finding_text(
            run_checks(CLASS_INVENTORY_CHECKS, candidate, scenario, parallel=True)
        )
    if findings:
        raise ValueError("class inventory remains invalid: " + "; ".join(findings))
    return candidate


def _operation_context(
    scenario: dict[str, Any], inventory: dict[str, Any], specification: dict[str, Any],
) -> dict[str, Any]:
    use_case_id = str(specification.get("use_case_id") or "")
    return {
        "inventory": inventory,
        "scenario": scenario,
        "use_case_id": use_case_id,
        "required_steps": sorted(_required_steps(specification)),
        "primary_actor": _primary_actor(scenario, use_case_id),
    }


def _fragment_payload(
    scenario: dict[str, Any], inventory: dict[str, Any], specification: dict[str, Any],
    *, findings: list[str] | None = None,
    previous: dict[str, Any] | None = None,
    reserved: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    use_case_id = str(specification.get("use_case_id") or "")
    use_case = next((
        item for item in scenario.get("use_cases") or []
        if isinstance(item, dict) and str(item.get("id") or "") == use_case_id
    ), {})
    payload = {
        "useCase": use_case,
        "specification": specification,
        "allowedStepRefs": sorted(_required_steps(specification)),
        "fixedClasses": inventory.get("Classes") or [],
        "fixedDataTypes": inventory.get("DataTypes") or [],
        "reservedOperations": reserved or [],
    }
    if findings:
        payload["task"] = (
            "Repair only this use-case operation fragment so every finding is "
            "resolved. Preserve valid operations and the fixed inventory. Return "
            "the full replacement fragment for this use case."
        )
        payload["findings"] = findings
    if previous:
        payload["previousFragment"] = previous
    return payload


def _parse_fragment(
    scenario: dict[str, Any], inventory: dict[str, Any], specification: dict[str, Any],
    *, findings: list[str] | None = None,
    previous: dict[str, Any] | None = None,
    reserved: list[dict[str, Any]] | None = None,
    operation: str = "UseCaseOperationProposal",
) -> dict[str, Any]:
    use_case_id = str(specification.get("use_case_id") or "")
    parsed = parse_structured(
        [
            {"role": "system", "content": _OPERATION_SYSTEM},
            {
                "role": "user",
                "content": json.dumps(
                    _fragment_payload(
                        scenario,
                        inventory,
                        specification,
                        findings=findings,
                        previous=previous,
                        reserved=reserved,
                    ),
                    ensure_ascii=False,
                ),
            },
        ],
        UseCaseOperationFragment,
        reasoning_effort=settings.design_reasoning_effort,
        max_completion_tokens=settings.design_class_collaboration_max_completion_tokens,
        operation=operation,
        metadata={"useCaseId": use_case_id},
    )
    return UseCaseOperationFragment.model_validate(parsed).model_dump(by_alias=True)


def _generate_fragment(
    scenario: dict[str, Any], inventory: dict[str, Any], specification: dict[str, Any],
) -> dict[str, Any]:
    context = _operation_context(scenario, inventory, specification)
    candidate = _parse_fragment(scenario, inventory, specification)
    findings = _finding_text(
        run_checks(CLASS_OPERATION_CHECKS, candidate, context, parallel=True)
    )
    if findings:
        candidate = _parse_fragment(
            scenario,
            inventory,
            specification,
            findings=findings,
            previous=candidate,
            operation="UseCaseOperationRepair",
        )
        findings = _finding_text(
            run_checks(CLASS_OPERATION_CHECKS, candidate, context, parallel=True)
        )
    if findings:
        raise ValueError(
            f"operation fragment {context['use_case_id']} remains invalid: "
            + "; ".join(findings)
        )
    return candidate


class _OperationCollision(ValueError):
    def __init__(self, class_name: str, operation_name: str) -> None:
        self.class_name = class_name
        self.operation_name = operation_name
        super().__init__(f"operation name collision on {class_name}.{operation_name}")


def _operation_signature(operation: dict[str, Any]) -> tuple[Any, ...]:
    return (
        tuple(
            (str(item.get("name") or ""), str(item.get("type") or ""))
            for item in operation.get("parameters") or [] if isinstance(item, dict)
        ),
        str(operation.get("returnType") or ""),
    )


def _compose(
    inventory: dict[str, Any],
    fragments: list[tuple[str, dict[str, Any]]],
    *,
    final: bool = False,
) -> dict[str, Any]:
    classes = {
        str(item.get("className") or ""): {
            **item, "use_case_ids": [], "operations": [],
        }
        for item in inventory.get("Classes") or [] if isinstance(item, dict)
    }
    for use_case_id, fragment in fragments:
        for class_set in fragment.get("Classes") or []:
            if not isinstance(class_set, dict):
                continue
            class_name = str(class_set.get("className") or "")
            target = classes[class_name]
            for proposed in class_set.get("operations") or []:
                if not isinstance(proposed, dict):
                    continue
                existing = next((
                    item for item in target["operations"]
                    if item.get("name") == proposed.get("name")
                ), None)
                if existing is not None:
                    if _operation_signature(existing) != _operation_signature(proposed):
                        raise _OperationCollision(class_name, str(proposed.get("name") or ""))
                    existing["stepRefs"] = list(dict.fromkeys([
                        *(existing.get("stepRefs") or []),
                        *(proposed.get("stepRefs") or []),
                    ]))
                    continue
                parameters = list(proposed.get("parameters") or [])
                target["operations"].append({
                    "operationId": canonical_operation_id(
                        class_name, str(proposed.get("name") or ""), parameters,
                    ),
                    **proposed,
                })
            if class_set.get("operations") and use_case_id not in target["use_case_ids"]:
                target["use_case_ids"].append(use_case_id)

    result_classes = list(classes.values())
    relationships = list(inventory.get("Relationships") or [])
    data_types = list(inventory.get("DataTypes") or [])
    if final:
        retained_names = {
            str(item.get("className") or "") for item in result_classes
            if item.get("operations")
        }
        result_classes = [
            item for item in result_classes
            if str(item.get("className") or "") in retained_names
        ]
        relationships = [
            item for item in relationships
            if isinstance(item, dict)
            and str(item.get("source") or "") in retained_names
            and str(item.get("target") or "") in retained_names
        ]
        reachable = reachable_data_type_names(result_classes, data_types)
        data_types = [
            item for item in data_types
            if isinstance(item, dict) and str(item.get("name") or "") in reachable
        ]
    return BCEModel.model_validate({
        "Classes": result_classes,
        "DataTypes": data_types,
        "Relationships": relationships,
        "Collaborations": [],
    }).model_dump(by_alias=True)


def _emit_preview(
    model: dict[str, Any], *, phase: str, unit: str, completed: int, total: int,
) -> None:
    puml = generate_plantuml_from_bce_json(model)
    if not puml:
        return
    labels = {
        "inventory": "Building the class inventory",
        "operations": f"Adding operations for {unit}",
        "final": "Finalizing the class contract",
    }
    design_progress.emit_progress(
        "classDiagramSnapshotAccepted",
        puml=puml,
        phase=phase,
        unit=unit,
        completed=completed,
        total=total,
        detail=labels.get(phase, "Updating the class diagram"),
    )


def _reserved_operations(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "className": str(class_item.get("className") or ""),
            "operations": list(class_item.get("operations") or []),
        }
        for class_item in model.get("Classes") or [] if isinstance(class_item, dict)
        and class_item.get("operations")
    ]


def generate_class_skeleton(scenario: dict[str, Any]) -> dict[str, Any]:
    """Generate one structure and immutable per-UC operation fragments."""

    if not isinstance(scenario, dict) or not scenario.get("use_case_specs"):
        return {}
    inventory = _generate_inventory(scenario)
    specifications = _specifications(scenario)
    _emit_preview(
        _inventory_model(inventory),
        phase="inventory",
        unit="inventory",
        completed=1,
        total=max(1, len(specifications) + 1),
    )

    workers = max(1, min(
        len(specifications) or 1,
        int(getattr(settings, "design_class_behavior_parallelism", 4)),
    ))
    if workers == 1:
        proposed = [
            _generate_fragment(scenario, inventory, item) for item in specifications
        ]
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(_generate_fragment, scenario, inventory, item)
                for item in specifications
            ]
            # Consume in canonical UC order. Parallel completion order therefore
            # cannot alter merge ownership or the visible whiteboard history.
            proposed = [future.result() for future in futures]

    committed: list[tuple[str, dict[str, Any]]] = []
    for index, (specification, fragment) in enumerate(
        zip(specifications, proposed, strict=True), start=1,
    ):
        use_case_id = str(specification.get("use_case_id") or "")
        try:
            preview = _compose(inventory, [*committed, (use_case_id, fragment)])
        except _OperationCollision as collision:
            current = _compose(inventory, committed)
            collision_history = [str(collision)]
            for _attempt in range(2):
                repaired = _parse_fragment(
                    scenario,
                    inventory,
                    specification,
                    findings=collision_history,
                    previous=fragment,
                    reserved=_reserved_operations(current),
                    operation="UseCaseOperationCollisionRepair",
                )
                context = _operation_context(scenario, inventory, specification)
                findings = _finding_text(
                    run_checks(CLASS_OPERATION_CHECKS, repaired, context, parallel=True)
                )
                if findings:
                    raise ValueError(
                        f"operation collision repair {use_case_id} is invalid: "
                        + "; ".join(findings)
                    )
                fragment = repaired
                try:
                    preview = _compose(
                        inventory, [*committed, (use_case_id, fragment)]
                    )
                    break
                except _OperationCollision as repeated:
                    collision_history.append(str(repeated))
            else:
                raise _OperationCollision(
                    collision.class_name, collision.operation_name,
                )
        committed.append((use_case_id, fragment))
        _emit_preview(
            preview,
            phase="operations",
            unit=use_case_id,
            completed=index + 1,
            total=len(specifications) + 1,
        )

    result = _compose(inventory, committed, final=True)
    _emit_preview(
        result,
        phase="final",
        unit="operations",
        completed=len(specifications) + 1,
        total=len(specifications) + 1,
    )
    return result


def _inventory_from_model(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "Classes": [
            {
                key: value for key, value in item.items()
                if key not in {"use_case_ids", "operations"}
            }
            for item in model.get("Classes") or [] if isinstance(item, dict)
        ],
        "DataTypes": list(model.get("DataTypes") or []),
        "Relationships": [
            item for item in model.get("Relationships") or []
            if isinstance(item, dict) and str(item.get("type") or "") != "Dependency"
        ],
    }


def _fragments_from_model(
    model: dict[str, Any], scenario: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for specification in _specifications(scenario):
        use_case_id = str(specification.get("use_case_id") or "")
        class_sets: list[dict[str, Any]] = []
        for class_item in model.get("Classes") or []:
            if not isinstance(class_item, dict):
                continue
            operations = []
            for operation in class_item.get("operations") or []:
                if not isinstance(operation, dict):
                    continue
                refs = [
                    str(ref) for ref in operation.get("stepRefs") or []
                    if str(ref).partition(":")[0] == use_case_id
                ]
                if refs:
                    operations.append({
                        key: value for key, value in operation.items()
                        if key != "operationId"
                    } | {"stepRefs": refs})
            if operations:
                class_sets.append({
                    "className": str(class_item.get("className") or ""),
                    "operations": operations,
                })
        if class_sets:
            result[use_case_id] = {"Classes": class_sets}
    return result


def repair_operation_fragment_for_finding(
    model: dict[str, Any],
    scenario: dict[str, Any],
    use_case_id: str,
    findings: list[str],
) -> dict[str, Any]:
    """Perform the one allowed collaboration-to-operation handoff for a UC."""

    inventory = _inventory_from_model(model)
    specifications = {
        str(item.get("use_case_id") or ""): item for item in _specifications(scenario)
    }
    specification = specifications.get(use_case_id)
    if specification is None:
        return model
    fragments = _fragments_from_model(model, scenario)
    previous = fragments.get(use_case_id)
    if previous is None:
        return model
    other_fragments = [
        (current_id, fragment)
        for current_id, fragment in sorted(fragments.items(), key=lambda item: _id_key(item[0]))
        if current_id != use_case_id
    ]
    base = _compose(inventory, other_fragments)
    repaired = _parse_fragment(
        scenario,
        inventory,
        specification,
        findings=findings,
        previous=previous,
        reserved=_reserved_operations(base),
        operation="CollaborationSignatureRepair",
    )
    context = _operation_context(scenario, inventory, specification)
    remaining = _finding_text(
        run_checks(CLASS_OPERATION_CHECKS, repaired, context, parallel=True)
    )
    if remaining:
        raise ValueError(
            f"collaboration signature repair {use_case_id} is invalid: "
            + "; ".join(remaining)
        )
    fragments[use_case_id] = repaired
    ordered = sorted(fragments.items(), key=lambda item: _id_key(item[0]))
    try:
        return _compose(inventory, ordered, final=True)
    except _OperationCollision as collision:
        # Collision is a distinct composition defect discovered only after the
        # repaired fragment is placed beside its immutable siblings. Give this
        # same UC one bounded correction; never reopen the inventory or another
        # UC fragment.
        repaired = _parse_fragment(
            scenario,
            inventory,
            specification,
            findings=[*findings, str(collision)],
            previous=repaired,
            reserved=_reserved_operations(base),
            operation="CollaborationSignatureCollisionRepair",
        )
        remaining = _finding_text(
            run_checks(CLASS_OPERATION_CHECKS, repaired, context, parallel=True)
        )
        if remaining:
            raise ValueError(
                f"collaboration collision repair {use_case_id} is invalid: "
                + "; ".join(remaining)
            )
        fragments[use_case_id] = repaired
        return _compose(
            inventory,
            sorted(fragments.items(), key=lambda item: _id_key(item[0])),
            final=True,
        )
