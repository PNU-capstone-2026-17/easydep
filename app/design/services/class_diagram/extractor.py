"""One global, structure-only proposal for the BCE class artifact."""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import settings
from app.design.knowledge import rules
from app.design.schemas.class_model import BCEModel, ClassParameter, DataType
from app.design.services.class_diagram.type_system import field_type, type_is_resolved
from app.design.services.common import fields
from app.design.services.common.structured import parse_structured


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
Across the operations in each use case, cover every main and extension step
that an execution collaboration must trace. A single operation may cite
several steps, including later output and extension steps fulfilled by its
return; do not create another operation merely for trace coverage.
When a synchronous actor interaction returns stated data or a success/failure
outcome, the actor-facing Boundary operation returns that typed value rather
than `void`; the delegated Control operation returns the same usable outcome.

Do not persist legacy methods, behavioral Dependency relationships,
actor-entry flags, input bindings, Collaborations, calls, call ids, or argument
sources. A later deterministic behavior stage owns calls, provenance, and
Dependency projection. Return only the supplied schema.
""".strip()


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
        return values


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

    @model_validator(mode="after")
    def references_are_closed(self) -> DomainStructureProposal:
        classes = {item.class_name: item for item in self.Classes}
        names = set(classes) | {item.name for item in self.DataTypes}
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
            if not class_item.get("operations"):
                issues.append(f"Entity {class_name} has no state-bearing operations")
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
            unknown_steps = sorted(step_refs - required_steps)
            if unknown_steps:
                issues.append(
                    f"{class_name}.{operation.get('name')} references unknown steps: "
                    f"{unknown_steps}"
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
    issues = _scenario_structure_issues(model, scenario)
    if not issues:
        return model
    repaired = run_domain_structure_parse(
        [
            *messages,
            {
                "role": "user",
                "content": (
                    "The candidate below passed JSON Schema but failed deterministic "
                    "scenario contracts. Regenerate the full structure and fix every "
                    "finding without adding behavior outside the scenario.\n\n"
                    "[Candidate]\n"
                    + json.dumps(model, ensure_ascii=False)
                    + "\n\n[Findings]\n"
                    + json.dumps(issues, ensure_ascii=False)
                ),
            },
        ],
        operation="DomainStructureContractRepair",
    )
    remaining = _scenario_structure_issues(repaired, scenario)
    if remaining:
        raise ValueError(
            "domain structure remains incomplete after bounded repair: "
            + "; ".join(remaining)
        )
    return repaired
