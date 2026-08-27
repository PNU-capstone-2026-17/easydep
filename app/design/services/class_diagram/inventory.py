"""전역 BCE 인벤토리 제안, 정규화와 검증을 소유한다."""
from __future__ import annotations

import json
from typing import Any

from app.core.config import settings
from app.core.validation import Finding, run_checks
from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram.models import AcceptedInventory
from app.design.services.class_diagram.proposals import InventoryProposal
from app.design.services.class_diagram.scenario import ScenarioIndex, id_key, text
from app.design.services.class_diagram.type_system import (
    field_type,
    referenced_type_names,
    structure_type_contract,
)
from app.design.services.class_diagram.validation.inventory import INVENTORY_CHECKS
from app.design.services.common import fields
from app.design.services.common.structured import parse_structured

INVENTORY_PROMPT = ("""
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


def finding_text(findings: tuple[Finding, ...]) -> list[str]:
    """검사 finding을 수리 프롬프트에 넣을 간결한 문자열로 바꾼다."""

    return [
        f"{finding.location}: {finding.message}" if finding.location else finding.message
        for finding in findings
    ]


def _normalize_inventory(proposal: InventoryProposal) -> dict[str, Any]:
    """제안 스키마를 영속 BCE 인벤토리 모양으로 정규화한다."""

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
    scopes = {item["className"]: set(item.get("useCaseIds") or []) for item in classes}
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


def inventory_payload(index: ScenarioIndex) -> dict[str, Any]:
    """하나의 전역 구조 결정에 필요한 근거만 노출한다."""

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
                "supportingActors": list(summaries.get(use_case.id, {}).get("supporting_actors") or []),
                "steps": [
                    {"stepRef": step.id, "branch": step.branch, "subject": step.subject,
                     "sentence": step.sentence, "condition": step.condition}
                    for step in use_case.steps
                ],
            }
            for use_case in index.use_cases
        ],
        "relationships": [
            {"kind": relationship.kind, "baseUseCaseId": relationship.base_id,
             "relatedUseCaseId": relationship.child_id,
             "anchorStepRefs": list(relationship.anchor_step_ids)}
            for relationship in index.relationships
        ],
    }


def inventory_proposal(index: ScenarioIndex) -> AcceptedInventory:
    """인벤토리를 한 번 생성하고 finding이 있으면 한 번만 전체 수리한다."""

    messages = [
        {"role": "system", "content": INVENTORY_PROMPT},
        {"role": "user", "content": json.dumps(inventory_payload(index), ensure_ascii=False)},
    ]
    parsed = parse_structured(
        messages, InventoryProposal, reasoning_effort=settings.design_reasoning_effort,
        max_completion_tokens=settings.design_class_structure_max_completion_tokens,
        operation="InteractionInventory",
    )
    candidate = _normalize_inventory(InventoryProposal.model_validate(parsed))
    report = run_checks(INVENTORY_CHECKS, candidate, index)
    if report.errors:
        raise RuntimeError("; ".join(report.errors))
    if report.findings:
        parsed = parse_structured(
            [*messages, {"role": "user", "content": json.dumps({
                "task": "Return one full repaired inventory. Preserve valid decisions and resolve every finding.",
                "candidate": candidate, "findings": finding_text(report.findings),
            }, ensure_ascii=False)}],
            InventoryProposal, reasoning_effort=settings.design_reasoning_effort,
            max_completion_tokens=settings.design_class_structure_max_completion_tokens,
            operation="InteractionInventoryRepair",
        )
        candidate = _normalize_inventory(InventoryProposal.model_validate(parsed))
        report = run_checks(INVENTORY_CHECKS, candidate, index)
    if report.errors or report.findings:
        raise ValueError("class inventory remains invalid: " + "; ".join(
            [*report.errors, *finding_text(report.findings)]
        ))
    return AcceptedInventory.from_payload(candidate)


def normalize_inventory(proposal: InventoryProposal) -> AcceptedInventory:
    """제안 계약을 단계 경계의 불변 인벤토리로 정규화한다."""

    return AcceptedInventory.from_payload(_normalize_inventory(proposal))


def inventory_model(inventory: AcceptedInventory) -> BCEModel:
    """인벤토리를 연산·협업이 비어 있는 유효 BCE 모델로 투영한다."""

    payload = inventory.as_payload()
    return BCEModel.model_validate({
        "Classes": [{**{key: value for key, value in item.items() if key not in {"useCaseIds", "values"}},
                     "use_case_ids": [], "operations": []}
                    for item in payload["Classes"]],
        "DataTypes": [{key: value for key, value in item.items() if key not in {"useCaseIds", "identifier"}}
                      for item in payload["DataTypes"] if isinstance(item, dict)],
        "Relationships": payload["Relationships"], "Collaborations": [],
    })



