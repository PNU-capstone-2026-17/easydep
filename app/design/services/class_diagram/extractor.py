"""One global, structure-only proposal for the BCE class artifact."""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import settings
from app.design.knowledge import rules
from app.design.schemas.class_model import BCEModel, ClassParameter, DataType
from app.design.services.class_diagram.type_system import (
    field_type,
    reachable_data_type_names,
    structure_type_contract,
    type_is_resolved,
)
from app.design.services.common import fields
from app.design.services.common.structured import StructuredLlmError, parse_structured


def rules_section(stage: str = rules.CLASS_DIAGRAM) -> str:
    """Compatibility helper for the ERD reviser; class extraction is concise."""

    section = f"\n## Rules\n{rules.generation_prompt_block(stage)}\n"
    if not_rules := rules.non_rules_block(stage):
        section += f"\n## Not rules\n{not_rules}\n"
    return section


BCE_CLASS_EXTRACTION_SYSTEM_PROMPT = """
Derive one analysis-level BCE domain structure from the supplied use-case
specification. Ground every item in the supplied requirements; missing facts
are not permission to invent classes, fields, operations, or relationships.

Return Classes with BCE responsibility, typed fields/identifiers, use_case_ids,
and reusable typed operation signatures. Return a valueObject or enumeration
DataType only when it is referenced by an operation or field. Return only
Entity-to-Entity structural relationships, with both multiplicities where the
relationship is structural.
Structural relationships are optional: omit one unless both endpoint Entities
are independently grounded in the scenarios with their own persistent fields
and state-bearing operations. Never add a Class or Entity solely to make a
relationship endpoint valid.

Boundary mediates an actor or external-system interface. Control coordinates
use-case flow and business decisions. Entity is persistent business state that
outlives one interaction. A request, response, result, selection, or other
transient message is not an Entity; represent it as a valueObject DataType only
when a typed operation actually needs it. Do not invent identity or retained
state for Boundary or Control classes, and do not create duplicate message types
with equivalent fields.
Boundary is an active interface object with operations, never a request,
response, summary, detail, or result data carrier.  Such typed data belongs in
DataTypes, not in empty Boundary classes.

An Entity retains the persistent state named in the scenarios, even when that
state is passed through a valueObject at an interface.  At design time you may
choose concise symbolic names and ordinary types for fields and parameters when
the scenario says an actor selects, identifies, supplies, records, or changes
the corresponding concept.  This is typed design vocabulary, not invention of
new business behavior or implementation values.
For every Entity you choose, declare its named persistent attributes in fields;
an identifier name must also appear as a typed field.  If the scenario supplies
no persistent state for a proposed Entity, omit that Entity instead of emitting
an empty marker class.
When a scenario explicitly reads, creates, changes, or removes persistent
state, put the state-bearing operation on the relevant Entity as well as the
coordination operation on its Control.  Do not hide all persistent behavior in
Control classes, and do not invent a repository, database, or framework class
unless the supplied design explicitly requires one.

Every actor-driven use case must be covered by at least one Boundary and one
Control; a cohesive class may cover several related use cases, so do not force
one class per use case. Each Boundary and Control must own at least one reusable
operation covering a stated step. An internal included use case may start at a
scoped Control instead of a Boundary, but it still needs its Control operation
and step trace. Use exact canonical step references: a main step N is
`<useCaseId>:main:<N>` and an extension sub-step S under label L is
`<useCaseId>:extension:<L>:<S>`. Do not use empty placeholders.
Treat class use_case_ids as an execution trace contract: every operation stepRef
must belong to one of its class's declared use cases. Every Entity use case must
have at least one state-bearing Entity operation traced to that use case; do not
claim Entity participation only through a field or description.
An extension condition or extension point is not an executable stepRef; never
invent a `:condition` or `:extensionPoint` suffix. When a base scenario step
invokes an included use case, retain the base stepRef as well as the included
flow's own stepRefs on the operations that trace those respective steps.
Across the operations in each use case, cover every main and extension step
that an execution collaboration must trace. A single operation may cite
several steps, including later output and extension steps fulfilled by its
return; do not create another operation merely for trace coverage.
When a later actor request follows completed system behavior, it is a new
entry interaction and needs a distinct Boundary operation. Consecutive actor
input steps with no intervening system response may remain one submission.
When a synchronous actor interaction returns stated data or a success/failure
outcome, the actor-facing Boundary operation returns that typed value rather
than `void`; the delegated Control operation returns the same usable outcome.

Operation signatures must be composable into a finite call chain. Every
parameter of a delegated operation must be sourceable either from an ancestor
call parameter with the exact same name and type, from a same-named typed field
of an ancestor structured parameter, from the exact return type or a declared
same-named field of an earlier non-void operation. A prose precondition is a
flow condition, not a typed argument value. Encapsulate an unstated clock,
generated identifier, or default state behind the responsible operation rather
than adding it as a caller-supplied parameter.
Align actor-facing Boundary and delegated
Control parameters by name and type when they convey the same input. Do not
require a composite object from a caller that has only separate component
values; if a child needs that object, an earlier operation must return it before
the child call.
An Entity creation operation need not receive generated identifiers or default
state as parameters when the scenario never supplies them; those are outcomes
of the state-bearing operation, not invented actor inputs.

Do not persist legacy methods, behavioral Dependency relationships,
actor-entry flags, input bindings, Collaborations, calls, call ids, or argument
sources. A later deterministic behavior stage owns calls, provenance, and
Dependency projection. Return only the supplied schema.
""".strip() + "\n\n" + structure_type_contract()


class _DomainModel(BaseModel):
    """Strict transient schema used only for global/revision structure LLM calls."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DomainOperation(_DomainModel):
    operation_id: str = Field(alias="operationId", min_length=1)
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    parameters: list[ClassParameter] = Field(default_factory=list)
    return_type: str = Field(alias="returnType", min_length=1)
    step_refs: list[str] = Field(alias="stepRefs", min_length=1)

    @field_validator("step_refs")
    @classmethod
    def require_nonempty_step_refs(cls, values: list[str]) -> list[str]:
        canonical = re.compile(
            r"^[^:\s]+:(?:main:[^:\s]+|extension:[^:\s]+:[^:\s]+)$"
        )
        if any(not canonical.fullmatch(str(value).strip()) for value in values):
            raise ValueError("stepRefs must use canonical main or extension identities")
        return list(dict.fromkeys(str(value).strip() for value in values))


class DomainClass(_DomainModel):
    class_name: str = Field(alias="className", min_length=1)
    stereotype: Literal["Boundary", "Control", "Entity"]
    description: str = ""
    fields: list[str] = Field(default_factory=list)
    use_case_ids: list[str] = Field(alias="use_case_ids", min_length=1)
    identifier: list[str] = Field(default_factory=list)
    operations: list[DomainOperation] = Field(default_factory=list)

    @field_validator("fields")
    @classmethod
    def require_typed_plain_fields(cls, values: list[str]) -> list[str]:
        for value in values:
            text = str(value).strip()
            name, separator, type_name = text.partition(":")
            if (
                not separator
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name.strip())
                or not type_name.strip()
                or "{" in text
                or "}" in text
            ):
                raise ValueError("fields must use 'name : Type' and cannot include inline {identifier}")
        return values

    @field_validator("use_case_ids")
    @classmethod
    def require_nonempty_use_case_ids(cls, values: list[str]) -> list[str]:
        if any(not str(value).strip() for value in values):
            raise ValueError("use_case_ids cannot contain empty values")
        return values

    @model_validator(mode="after")
    def enforce_structure_contract(self) -> DomainClass:
        if self.stereotype in {"Boundary", "Control"} and not self.operations:
            raise ValueError("Boundary and Control classes need at least one operation")
        return self


class DomainRelationship(_DomainModel):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    type: Literal["Association", "Aggregation", "Composition", "Inheritance"]
    source_multiplicity: str = Field(default="", alias="sourceMultiplicity")
    target_multiplicity: str = Field(default="", alias="targetMultiplicity")
    description: str = ""


class DomainStructureProposal(_DomainModel):
    """LLM boundary that intentionally has no ``Collaborations`` field."""

    Classes: list[DomainClass] = Field(min_length=1)
    DataTypes: list[DataType] = Field(default_factory=list)
    Relationships: list[DomainRelationship] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def omit_relations_without_independent_entity_evidence(cls, value: Any) -> Any:
        """Drop optional relations whose declared Entity endpoint is only a marker."""

        if not isinstance(value, dict):
            return value
        classes = {
            str(item.get("className") or ""): item
            for item in value.get("Classes") or []
            if isinstance(item, dict)
        }
        relationships: list[Any] = []
        for relationship in value.get("Relationships") or []:
            if not isinstance(relationship, dict):
                relationships.append(relationship)
                continue
            source = classes.get(str(relationship.get("source") or ""))
            target = classes.get(str(relationship.get("target") or ""))
            declared_entities = (
                source is not None
                and target is not None
                and source.get("stereotype") == "Entity"
                and target.get("stereotype") == "Entity"
            )
            if declared_entities and (
                not source.get("fields")
                or not target.get("fields")
                or not source.get("operations")
                or not target.get("operations")
            ):
                continue
            relationships.append(relationship)
        return {**value, "Relationships": relationships}

    @model_validator(mode="after")
    def references_are_closed(self) -> DomainStructureProposal:
        classes = {item.class_name: item for item in self.Classes}
        data_type_names = {item.name for item in self.DataTypes}
        overlapping = sorted(set(classes) & data_type_names)
        if overlapping:
            raise ValueError(
                "Class and DataType names must not overlap: "
                + ", ".join(overlapping)
            )
        names = set(classes) | data_type_names
        unresolved: list[str] = []
        for class_item in self.Classes:
            for value in class_item.fields:
                type_name = field_type(value)
                if type_name and not type_is_resolved(
                    type_name, names, allow_void=False,
                ):
                    unresolved.append(f"{class_item.class_name}.{value}")
            for operation in class_item.operations:
                for parameter in operation.parameters:
                    if not type_is_resolved(
                        parameter.type, names, allow_void=False,
                    ):
                        unresolved.append(
                            f"{class_item.class_name}.{operation.name}({parameter.name})"
                        )
                if not type_is_resolved(
                    operation.return_type, names, allow_void=True,
                ):
                    unresolved.append(
                        f"{class_item.class_name}.{operation.name}:return"
                    )
        for data_type in self.DataTypes:
            for value in data_type.fields:
                type_name = field_type(value)
                if type_name and not type_is_resolved(
                    type_name, names, allow_void=False,
                ):
                    unresolved.append(f"{data_type.name}.{value}")
        if unresolved:
            raise ValueError(
                "all field, parameter, and return types must resolve: "
                + ", ".join(unresolved)
            )

        invalid_relationships = [
            f"{item.source}->{item.target}"
            for item in self.Relationships
            if item.source not in classes
            or item.target not in classes
            or classes[item.source].stereotype != "Entity"
            or classes[item.target].stereotype != "Entity"
        ]
        if invalid_relationships:
            raise ValueError(
                "structural relationships must connect declared Entity classes: "
                + ", ".join(invalid_relationships)
            )
        return self


class DomainOperationSet(_DomainModel):
    """Focused replacement operations for one existing class."""

    class_name: str = Field(alias="className", min_length=1)
    operations: list[DomainOperation] = Field(default_factory=list)


class DomainOperationRepair(_DomainModel):
    """Small repair boundary that cannot rewrite class structure."""

    Classes: list[DomainOperationSet] = Field(min_length=1)


def _normalize_field_types(result: dict[str, Any]) -> dict[str, Any]:
    data_types = [
        item for item in result.get("DataTypes") or [] if isinstance(item, dict)
    ]
    data_type_names = {str(item.get("name") or "") for item in data_types}
    classes: list[dict[str, Any]] = []
    for class_item in result.get("Classes") or []:
        if not isinstance(class_item, dict):
            continue
        marker = " ".join(str(class_item.get("description") or "").split()).casefold()
        if (
            str(class_item.get("stereotype") or "") == "Entity"
            and marker in {"valueobject", "value object", "<<valueobject>>"}
            and class_item.get("fields")
        ):
            name = str(class_item.get("className") or "")
            if name and name not in data_type_names:
                data_types.append({
                    "name": name,
                    "kind": "valueObject",
                    "fields": list(class_item.get("fields") or []),
                    "values": [],
                })
                data_type_names.add(name)
            continue
        classes.append(class_item)

    result["Classes"] = classes
    result["DataTypes"] = data_types
    for class_item in classes:
        if isinstance(class_item, dict):
            class_item["fields"] = [
                fields.normalize_java_field(str(field))
                for field in class_item.get("fields") or []
            ]
            # A dangling identifier is not a recoverable source reference.  It
            # is safer to omit that designation and let ERD mapping choose an
            # explicit or surrogate key than to rename a field by guessing.
            field_names = {
                value.partition(":")[0].strip()
                for value in class_item["fields"]
            }
            class_item["identifier"] = [
                str(name) for name in class_item.get("identifier") or []
                if str(name) in field_names
            ]
    for data_type in data_types:
        if isinstance(data_type, dict):
            data_type["fields"] = [
                fields.normalize_java_field(str(field))
                for field in data_type.get("fields") or []
            ]
    reachable = reachable_data_type_names(classes, data_types)
    result["DataTypes"] = [
        data_type for data_type in data_types
        if str(data_type.get("name") or "").strip() in reachable
    ]
    return result


def run_bce_parse(
    messages: list[dict[str, str]], *, operation: str = "DomainStructureProposal",
    reasoning_effort: str = "high", max_completion_tokens: int | None = None,
) -> dict[str, Any]:
    """Parse the global structural proposal with the class artifact schema."""

    parsed = parse_structured(
        messages,
        BCEModel,
        reasoning_effort=reasoning_effort,
        max_completion_tokens=(
            max_completion_tokens
            if max_completion_tokens is not None
            else settings.design_class_structure_max_completion_tokens
        ),
        operation=operation,
    )
    return BCEModel.model_validate(_normalize_field_types(parsed)).model_dump(by_alias=True)


def run_domain_structure_parse(
    messages: list[dict[str, str]], *, operation: str,
    reasoning_effort: str | None = None, max_completion_tokens: int | None = None,
) -> dict[str, Any]:
    """Parse a non-empty structure proposal, then project it to BCEModel.

    ``BCEModel`` remains intentionally permissive enough to load historical
    artifacts and ERD revisions.  This stricter transient boundary is used
    only where an LLM is expected to propose a new executable class structure.
    """

    parsed = parse_structured(
        messages,
        DomainStructureProposal,
        reasoning_effort=reasoning_effort or settings.design_reasoning_effort,
        max_completion_tokens=(
            max_completion_tokens
            if max_completion_tokens is not None
            else settings.design_class_structure_max_completion_tokens
        ),
        operation=operation,
    )
    normalized = _normalize_field_types(parsed)
    return BCEModel.model_validate({**normalized, "Collaborations": []}).model_dump(by_alias=True)


def run_bce_skeleton_parse(messages: list[dict[str, str]]) -> dict[str, Any]:
    """Retain structure/signatures but reject behavior from the global call."""

    parsed = run_domain_structure_parse(messages, operation="DomainStructureProposal")
    stereotypes = {
        str(item.get("className") or ""): str(item.get("stereotype") or "").casefold()
        for item in parsed.get("Classes") or [] if isinstance(item, dict)
    }
    parsed["Relationships"] = [
        relationship for relationship in parsed.get("Relationships") or []
        if isinstance(relationship, dict)
        and str(relationship.get("type") or "") != "Dependency"
        and stereotypes.get(str(relationship.get("source") or "")) == "entity"
        and stereotypes.get(str(relationship.get("target") or "")) == "entity"
    ]
    return BCEModel.model_validate(parsed).model_dump(by_alias=True)


def _scenario_structure_issues(
    model: dict[str, Any], scenario: dict[str, Any],
) -> list[str]:
    """Check scenario-dependent structure facts that JSON Schema cannot express."""

    specifications = {
        str(item.get("use_case_id") or "").strip(): item
        for item in scenario.get("use_case_specs") or []
        if isinstance(item, dict) and str(item.get("use_case_id") or "").strip()
    }
    known_use_cases = set(specifications) | {
        str(item.get("id") or "").strip()
        for item in scenario.get("use_cases") or []
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    required_steps: set[str] = set()
    for use_case_id, specification in specifications.items():
        required_steps.update(
            f"{use_case_id}:main:{step['step_number']}"
            for step in specification.get("main_scenario") or []
            if isinstance(step, dict) and step.get("step_number") is not None
        )
        for extension in specification.get("extensions") or []:
            if not isinstance(extension, dict):
                continue
            label = str(extension.get("label") or "").strip()
            required_steps.update(
                f"{use_case_id}:extension:{label}:{step['sub_step']}"
                for step in extension.get("handling_steps") or []
                if label and isinstance(step, dict) and step.get("sub_step") is not None
            )

    issues: list[str] = []
    covered_steps: set[str] = set()
    classes = [item for item in model.get("Classes") or [] if isinstance(item, dict)]
    for class_item in classes:
        class_name = str(class_item.get("className") or "").strip()
        use_case_ids = {
            str(value).strip() for value in class_item.get("use_case_ids") or []
            if str(value).strip()
        }
        unknown_use_cases = sorted(use_case_ids - known_use_cases)
        if unknown_use_cases:
            issues.append(f"{class_name} references unknown use cases: {unknown_use_cases}")
        if str(class_item.get("stereotype") or "") == "Entity":
            fields = [str(value) for value in class_item.get("fields") or []]
            field_names = {value.partition(":")[0].strip() for value in fields}
            if not fields:
                issues.append(f"Entity {class_name} has no persistent fields")
            dangling_identifiers = sorted(
                str(value).strip() for value in class_item.get("identifier") or []
                if str(value).strip() not in field_names
            )
            if dangling_identifiers:
                issues.append(
                    f"Entity {class_name} identifiers are not typed fields: "
                    f"{dangling_identifiers}"
                )
        for operation in class_item.get("operations") or []:
            if not isinstance(operation, dict):
                continue
            step_refs = {
                str(value).strip() for value in operation.get("stepRefs") or []
                if str(value).strip()
            }
            covered_steps.update(step_refs)
            operation_use_case_ids = {
                step_ref.partition(":")[0] for step_ref in step_refs
            }
            out_of_scope_steps = sorted(operation_use_case_ids - use_case_ids)
            if out_of_scope_steps:
                issues.append(
                    f"{class_name}.{operation.get('name')} traces use cases outside "
                    f"its class scope: {out_of_scope_steps}"
                )
            unknown_steps = sorted(step_refs - required_steps)
            if unknown_steps:
                issues.append(
                    f"{class_name}.{operation.get('name')} references unknown steps: "
                    f"{unknown_steps}"
                )
        if str(class_item.get("stereotype") or "") == "Entity":
            entity_operation_use_cases = {
                str(step_ref).partition(":")[0]
                for operation in class_item.get("operations") or []
                if isinstance(operation, dict)
                for step_ref in operation.get("stepRefs") or []
                if str(step_ref).strip()
            }
            missing_operation_scopes = sorted(
                use_case_ids - entity_operation_use_cases
            )
            if missing_operation_scopes:
                issues.append(
                    f"Entity {class_name} has no state-bearing operation for "
                    f"declared use cases: {missing_operation_scopes}"
                )

    for use_case in scenario.get("use_cases") or []:
        if not isinstance(use_case, dict):
            continue
        use_case_id = str(use_case.get("id") or "").strip()
        primary_actor = str(use_case.get("primary_actor") or "").strip()
        if not use_case_id or not primary_actor:
            continue
        stereotypes = {
            str(item.get("stereotype") or "")
            for item in classes
            if use_case_id in {
                str(value).strip() for value in item.get("use_case_ids") or []
            }
        }
        for required in ("Boundary", "Control"):
            if required not in stereotypes:
                issues.append(
                    f"actor-driven use case {use_case_id} has no {required} class"
                )

    missing_steps = sorted(required_steps - covered_steps)
    if missing_steps:
        issues.append(f"operation stepRefs do not cover scenario steps: {missing_steps}")
    return issues


def _scenario_signature_issues(
    model: dict[str, Any], scenario: dict[str, Any],
) -> list[str]:
    """Find execution groups that cannot form a sourceable BCE call tree."""

    from app.design.services.class_diagram.behavior import (
        _deterministic_group_calls,
        _required_trace_steps,
        execution_groups,
    )

    issues: list[str] = []
    schema_complete = all(
        isinstance(operation, dict)
        and "operationId" in operation
        and "parameters" in operation
        and "returnType" in operation
        for class_item in model.get("Classes") or []
        if isinstance(class_item, dict)
        for operation in class_item.get("operations") or []
    )
    if not schema_complete:
        # Production candidates have already crossed DomainStructureProposal
        # and BCEModel.  A few compatibility tests deliberately pass the old
        # structure-only dictionaries; keep the scenario-step checks useful
        # without pretending those dictionaries can form executable calls.
        return issues
    groups = execution_groups(scenario)

    actor_groups_by_use_case: dict[str, list[Any]] = {}
    for group in groups:
        if group.actor_step:
            actor_groups_by_use_case.setdefault(group.use_case_id, []).append(group)
    for class_item in model.get("Classes") or []:
        if (
            not isinstance(class_item, dict)
            or str(class_item.get("stereotype") or "").casefold() != "boundary"
        ):
            continue
        class_name = str(class_item.get("className") or "").strip()
        for operation in class_item.get("operations") or []:
            if not isinstance(operation, dict):
                continue
            step_refs = {
                str(value).strip() for value in operation.get("stepRefs") or []
                if str(value).strip()
            }
            for use_case_id in class_item.get("use_case_ids") or []:
                current_groups = actor_groups_by_use_case.get(str(use_case_id), [])
                covered_entries = [
                    group for group in current_groups
                    if group.actor_step in step_refs
                ]
                if len(covered_entries) < 2:
                    continue
                if any(
                    _required_trace_steps(group, scenario) - {group.actor_step}
                    for group in covered_entries[:-1]
                ):
                    issues.append(
                        f"Boundary operation {class_name}.{operation.get('name')} "
                        f"merges actor entries separated by completed system "
                        f"behavior in {use_case_id}"
                    )

    for group in groups:
        try:
            _deterministic_group_calls(model, scenario, group)
        except (ValueError, TypeError) as error:
            issues.append(
                f"execution group {group.id} cannot form a sourceable BCE call "
                f"tree: {error}"
            )
    return issues


def _issue_use_case_ids(issues: list[str], scenario: dict[str, Any]) -> list[str]:
    known = [
        str(item.get("id") or "").strip()
        for item in scenario.get("use_cases") or []
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    return [
        use_case_id
        for use_case_id in known
        if any(
            re.search(
                rf"(?<![A-Za-z0-9_-]){re.escape(use_case_id)}(?![A-Za-z0-9_-])",
                issue,
            )
            for issue in issues
        )
    ]


def _focused_operation_prompt(
    candidate: dict[str, Any], scenario: dict[str, Any], use_case_id: str,
    issues: list[str],
) -> list[dict[str, str]]:
    scoped_scenario = {
        "use_cases": [
            item for item in scenario.get("use_cases") or []
            if isinstance(item, dict) and str(item.get("id") or "") == use_case_id
        ],
        "use_case_specs": [
            item for item in scenario.get("use_case_specs") or []
            if isinstance(item, dict)
            and str(item.get("use_case_id") or "") == use_case_id
        ],
    }
    class_catalog = [
        {
            "className": item.get("className"),
            "stereotype": item.get("stereotype"),
            "fields": item.get("fields") or [],
            "operationsForUseCase": [
                operation
                for operation in item.get("operations") or []
                if isinstance(operation, dict)
                and any(
                    str(step_ref).partition(":")[0] == use_case_id
                    for step_ref in operation.get("stepRefs") or []
                )
            ],
            "reservedOperations": [
                operation
                for operation in item.get("operations") or []
                if isinstance(operation, dict)
                and not any(
                    str(step_ref).partition(":")[0] == use_case_id
                    for step_ref in operation.get("stepRefs") or []
                )
            ],
        }
        for item in candidate.get("Classes") or []
        if isinstance(item, dict)
        and (
            use_case_id in {
                str(value).strip()
                for value in item.get("use_case_ids") or []
            }
            or any(
                str(step_ref).partition(":")[0] == use_case_id
                for operation in item.get("operations") or []
                if isinstance(operation, dict)
                for step_ref in operation.get("stepRefs") or []
            )
        )
    ]
    system = """
Repair operation signatures for exactly one use case inside a fixed BCE class
structure. Return only the supplied DomainOperationRepair schema. Class names,
stereotypes, fields, relationships, and DataTypes are fixed: use only a listed
className and the declared type vocabulary; never add or rename a class or type.

Return the complete replacement set of operations needed for this use case on
each participating existing class. Use only exact main and extension stepRefs
from the scoped specification. An actor entry is Boundary -> Control; persistent
business behavior is Control -> Entity. Boundary parameters are actor-supplied
inputs and its return is the actor-visible result. Do not model output values or
the actor object as Control input parameters. Every delegated parameter must be
available by the same name and type from its Boundary input, a field of a
structured input, or an earlier operation result. Do not require generated ids,
default state, or an unstated clock as caller parameters. Keep one cohesive
operation when it covers several adjacent steps; do not create one operation per
sentence. A reserved operation name is already owned by another use case. Reuse
it only by returning its exact signature with this use case's stepRefs; otherwise
choose a distinct operation name. Do not return calls, argument bindings, or
relationships.
""".strip()
    user = json.dumps(
        {
            "useCaseId": use_case_id,
            "scenario": scoped_scenario,
            "fixedClasses": class_catalog,
            "fixedDataTypes": candidate.get("DataTypes") or [],
            "findings": issues,
        },
        ensure_ascii=False,
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _merge_operation_repair(
    candidate: dict[str, Any], repair: DomainOperationRepair, use_case_id: str,
) -> dict[str, Any]:
    """Replace only operation records traced to one UC; preserve all structure."""

    repaired_by_class = {
        item.class_name: [
            operation.model_dump(by_alias=True)
            for operation in item.operations
            if operation.step_refs
            and all(
                str(step_ref).partition(":")[0] == use_case_id
                for step_ref in operation.step_refs
            )
        ]
        for item in repair.Classes
        if item.operations
    }
    merged = {**candidate, "Classes": []}
    for class_item in candidate.get("Classes") or []:
        if not isinstance(class_item, dict):
            continue
        class_name = str(class_item.get("className") or "")
        if class_name not in repaired_by_class:
            merged["Classes"].append(class_item)
            continue
        preserved = [
            operation
            for operation in class_item.get("operations") or []
            if isinstance(operation, dict)
            and not any(
                str(step_ref).partition(":")[0] == use_case_id
                for step_ref in operation.get("stepRefs") or []
            )
        ]
        replacements = repaired_by_class[class_name]
        combined = list(preserved)
        for replacement in replacements:
            replacement_name = str(replacement.get("name") or "")
            same_name_index = next(
                (
                    index for index, operation in enumerate(combined)
                    if str(operation.get("name") or "") == replacement_name
                ),
                None,
            )
            if same_name_index is None:
                combined.append(replacement)
                continue
            existing = combined[same_name_index]
            if (
                existing.get("parameters") != replacement.get("parameters")
                or existing.get("returnType") != replacement.get("returnType")
            ):
                raise ValueError(
                    f"operation name collision on {class_name}.{replacement_name}"
                )
            combined[same_name_index] = {
                **existing,
                "stepRefs": list(dict.fromkeys([
                    *(existing.get("stepRefs") or []),
                    *(replacement.get("stepRefs") or []),
                ])),
            }
        use_case_ids = list(class_item.get("use_case_ids") or [])
        if replacements and use_case_id not in use_case_ids:
            use_case_ids.append(use_case_id)
        merged["Classes"].append({
            **class_item,
            "use_case_ids": use_case_ids,
            "operations": combined,
        })
    return BCEModel.model_validate(merged).model_dump(by_alias=True)


def _repair_operations_by_use_case(
    candidate: dict[str, Any], scenario: dict[str, Any], issues: list[str],
) -> dict[str, Any]:
    """Repair one UC at a time so shared classes never merge stale proposals."""

    targets = _issue_use_case_ids(issues, scenario)
    if not targets:
        return candidate
    result = candidate
    for target in targets:
        current_findings = [
            issue for issue in _scenario_structure_issues(result, scenario)
            if re.search(
                rf"(?<![A-Za-z0-9_-]){re.escape(target)}(?![A-Za-z0-9_-])",
                issue,
            )
        ]
        if not current_findings:
            current_findings = [
                issue for issue in issues
                if re.search(
                    rf"(?<![A-Za-z0-9_-]){re.escape(target)}(?![A-Za-z0-9_-])",
                    issue,
                )
            ]
        if not current_findings:
            continue
        try:
            parsed = parse_structured(
                _focused_operation_prompt(
                    result, scenario, target, current_findings,
                ),
                DomainOperationRepair,
                reasoning_effort=settings.design_reasoning_effort,
                max_completion_tokens=settings.design_class_collaboration_max_completion_tokens,
                operation="DomainOperationRepair",
                metadata={"useCaseId": target},
            )
            repair = DomainOperationRepair.model_validate(parsed)
            result = _merge_operation_repair(result, repair, target)
        except (StructuredLlmError, ValueError, TypeError):
            # The unchanged candidate keeps the finding explicit for the next
            # bounded round or the final failure report.
            continue
    return result


def repair_bce_operations_for_findings(
    candidate: dict[str, Any], scenario: dict[str, Any], findings: list[str],
) -> dict[str, Any]:
    """Public bounded bridge from concrete collaboration failures to signatures."""

    return _repair_operations_by_use_case(candidate, scenario, findings)


def extract_bce_classes_from_scenario(scenario_text: str) -> dict[str, Any]:
    if not scenario_text:
        return {}
    messages = [
        {"role": "system", "content": BCE_CLASS_EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": f"Requirement Specification Scenario:\n{scenario_text}"},
    ]
    model = run_bce_skeleton_parse(messages)
    try:
        scenario = json.loads(scenario_text)
    except (json.JSONDecodeError, TypeError):
        return model
    if not isinstance(scenario, dict) or not scenario.get("use_case_specs"):
        return model
    candidate = model
    finding_history: list[dict[str, Any]] = []
    repair_budget = max(0, settings.design_max_repair_iters)
    global_repair_budget = min(1, repair_budget)
    for repair_index in range(1, global_repair_budget + 1):
        issues = _scenario_structure_issues(candidate, scenario)
        if not issues:
            return candidate
        finding_history.append({"iteration": repair_index, "findings": issues})
        candidate = run_domain_structure_parse(
            [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "The latest candidate below passed JSON Schema but failed "
                        "deterministic scenario contracts. Regenerate the full structure "
                        "and fix every finding without adding behavior outside the "
                        "scenario. Findings from earlier candidates remain relevant; "
                        "do not reintroduce them. Keep a grounded state-bearing Entity "
                        "operation when its only defect is an internal transition value: "
                        "encapsulate an unstated default or derived value instead of "
                        "deleting the persistent behavior.\n\n"
                        "[Latest candidate]\n"
                        + json.dumps(candidate, ensure_ascii=False)
                        + "\n\n[Finding history]\n"
                        + json.dumps(finding_history, ensure_ascii=False)
                    ),
                },
            ],
            operation="DomainStructureContractRepair",
        )
    remaining = _scenario_structure_issues(candidate, scenario)
    for _ in range(global_repair_budget, repair_budget):
        if not remaining:
            break
        candidate = _repair_operations_by_use_case(candidate, scenario, remaining)
        remaining = _scenario_structure_issues(candidate, scenario)
    if remaining:
        raise ValueError(
            "domain structure remains incomplete after bounded repair: "
            + "; ".join(remaining)
        )
    return candidate
