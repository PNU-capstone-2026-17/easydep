"""전역 BCE 인벤토리의 LLM 제안, 정규화와 수락을 소유한다.

입력은 정규화된 ``ScenarioIndex``이며, LLM에는 프로젝트 원문 대신 역할·목표·단계와
유스케이스 관계만 압축해 전달한다. 응답 ``InventoryProposal``은 저장 shape로 정규화한 뒤
``INVENTORY_CHECKS``를 통과해야 ``AcceptedInventory``가 된다.

이 모듈은 LLM 호출과 최대 한 번의 inventory replacement라는 부작용을 가진다. 연산,
협업, graph state와 저장소를 직접 참조하지 않는다.
"""
from __future__ import annotations

import json
from typing import Any

from app.config import settings
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
from app.validation import Finding, run_checks

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
    """제안 schema를 저장 직전의 BCE inventory shape로 멱등 변환한다.

    LLM은 필드를 ``{"name": ..., "type": ...}``로 반환하지만 저장 모델은
    ``name : Type`` 문자열을 사용한다. DataType의 use-case 범위는 LLM의 빈 배열을 믿지
    않고 Entity 필드의 전이 참조에서 다시 계산한다.
    """

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
    # 구조 타입은 독립된 행동 소유자가 아니다. 사용 범위를 Entity 필드 참조에서
    # 유도해야 operation 단계가 관련 없는 DTO 후보를 받지 않는다.
    scopes = {item["className"]: set(item.get("useCaseIds") or []) for item in classes}
    type_index = {item["name"]: item for item in data_types}
    type_scopes: dict[str, set[str]] = {name: set() for name in type_index}
    for item in classes:
        for raw_field in item.get("fields") or []:
            for name in referenced_type_names(field_type(raw_field)) & type_index.keys():
                type_scopes[name].update(scopes[item["className"]])
    # 한 타입이 다른 타입을 중첩할 수 있으므로 고정점까지 전파한다. 직접 참조만 보면
    # Entity -> Address -> CountryCode에서 CountryCode의 scope가 사라진다.
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
    """전역 구조 결정에 필요한 시나리오 근거만 LLM payload로 투영한다.

    Args:
        index: 유스케이스, 단계와 관계를 정규화한 입력이다.

    Returns:
        ``useCases``와 ``relationships``만 포함하는 JSON 직렬화 가능 payload다.

    Notes:
        원문 명세의 구현 메모나 이미 생성된 산출물은 보내지 않는다. 선택 공간을 줄이면서
        BCE 책임과 추적 범위를 결정하는 근거는 모두 보존한다.
    """

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
    """전역 inventory를 생성하고 최대 한 번의 전체 replacement로 수락한다.

    Args:
        index: inventory의 허용 이름·유스케이스 범위를 제공하는 시나리오 인덱스다.

    Returns:
        정규화와 모든 inventory 검사를 통과한 frozen 수락 단위다.

    Raises:
        RuntimeError: 검사기 자체가 예외를 내 검증을 완료하지 못한 경우다.
        ValueError: 최초 제안과 한 번의 replacement가 모두 finding을 남긴 경우다.

    Notes:
        repair에는 최초 messages, 전체 candidate와 모든 finding을 함께 보낸다. 부분 patch는
        허용하지 않으며 두 번째 결과도 같은 schema와 규칙을 통과해야 한다.
    """

    # 1. 원문을 재전송하지 않고 inventory 결정에 필요한 압축 payload를 한 번 만든다.
    messages = [
        {"role": "system", "content": INVENTORY_PROMPT},
        {"role": "user", "content": json.dumps(inventory_payload(index), ensure_ascii=False)},
    ]
    # 2. 응답 타입을 InventoryProposal로 고정해 설명문이나 임의 필드를 받지 않는다.
    parsed = parse_structured(
        messages, InventoryProposal, reasoning_effort=settings.design_reasoning_effort,
        max_completion_tokens=settings.design_class_structure_max_completion_tokens,
        operation="InteractionInventory",
    )
    # 3. LLM shape를 저장 shape로 바꾼 뒤에야 결정론 규칙이 후보를 판단한다.
    candidate = _normalize_inventory(InventoryProposal.model_validate(parsed))
    report = run_checks(INVENTORY_CHECKS, candidate, index)
    if report.errors:
        raise RuntimeError("; ".join(report.errors))
    if report.findings:
        # 4. finding이 있으면 같은 소유 단위 전체만 한 번 교체한다. 부분 결과를 합치면
        # 관계와 타입 scope가 서로 다른 버전이 될 수 있어 full replacement만 허용한다.
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
    # 5. raw dict가 하위 단계로 새지 않도록 frozen 수락 경계로 닫는다.
    return AcceptedInventory.from_payload(candidate)


def normalize_inventory(proposal: InventoryProposal) -> AcceptedInventory:
    """제안 계약을 단계 경계의 불변 inventory로 정규화한다.

    Args:
        proposal: Pydantic 검증을 마친 일시적 LLM 응답이다.

    Returns:
        저장 alias와 파생 scope가 확정된 ``AcceptedInventory``다.
    """

    return AcceptedInventory.from_payload(_normalize_inventory(proposal))


def inventory_model(inventory: AcceptedInventory) -> BCEModel:
    """수락 inventory를 연산·협업이 비어 있는 BCE skeleton으로 투영한다.

    Args:
        inventory: 구조 단계에서 수락한 클래스, 타입과 관계다.

    Returns:
        operation과 collaboration을 후속 단계가 채울 수 있는 유효 ``BCEModel``이다.

    Notes:
        proposal 전용 scope 필드는 저장 schema에 맞게 제거·변환한다. 이 함수는 LLM을
        호출하거나 입력 inventory를 수정하지 않는다.
    """

    payload = inventory.as_payload()
    return BCEModel.model_validate({
        "Classes": [{**{key: value for key, value in item.items() if key not in {"useCaseIds", "values"}},
                     "use_case_ids": [], "operations": []}
                    for item in payload["Classes"]],
        "DataTypes": [{key: value for key, value in item.items() if key not in {"useCaseIds", "identifier"}}
                      for item in payload["DataTypes"] if isinstance(item, dict)],
        "Relationships": payload["Relationships"], "Collaborations": [],
    })
