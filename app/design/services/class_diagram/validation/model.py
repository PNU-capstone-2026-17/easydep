"""완성된 BCE 클래스 모델의 저장 계약과 협업 불변식을 검증한다."""
from __future__ import annotations

import re
from typing import Any

from app.design.schemas.class_model import BCEModel, canonical_operation_id
from app.design.services.class_diagram.scenario import ScenarioIndex, text
from app.design.services.class_diagram.type_system import types_compatible
from app.validation import CheckSpec, Finding, ValidationReport, run_checks


def class_name(item: dict[str, Any]) -> str:
    return text(item.get("className") or item.get("class_name"))


def runtime_value_source(type_expression: str) -> str:
    """시간 타입에 허용되는 명시적 런타임 값 출처를 반환한다."""

    normalized = re.sub(r"\s+", "", text(type_expression)).casefold()
    normalized = normalized.removeprefix("java.time.")
    return {
        "date": "runtime#currentDate",
        "localdate": "runtime#currentDate",
        "datetime": "runtime#currentDateTime",
        "localdatetime": "runtime#currentDateTime",
        "offsetdatetime": "runtime#currentDateTime",
        "zoneddatetime": "runtime#currentDateTime",
        "instant": "runtime#currentInstant",
        "timestamp": "runtime#currentInstant",
    }.get(normalized, "")


def type_can_default(type_expression: str) -> bool:
    normalized = re.sub(r"\s+", "", text(type_expression)).casefold()
    return normalized.startswith("optional<") or normalized.startswith("optional[")


def optional_inner_type(type_expression: str) -> str:
    normalized = re.sub(r"\s+", "", text(type_expression))
    match = re.fullmatch(r"(?i:optional)[<\[](.+)[>\]]", normalized)
    return match.group(1) if match else ""


def derived_value_source(target_type: str, field_sources: dict[str, str]) -> str:
    assignments = ",".join(
        f"{name}={field_sources[name]}" for name in sorted(field_sources)
    )
    return f"derived#{target_type}({assignments})"


def derived_value_parts(source_ref: str) -> tuple[str, dict[str, str]]:
    match = re.fullmatch(r"derived#([A-Za-z_][A-Za-z0-9_]*)\((.*)\)", source_ref)
    if not match:
        return "", {}
    assignments: dict[str, str] = {}
    if match.group(2):
        for raw in match.group(2).split(","):
            name, separator, value = raw.partition("=")
            if not separator or not name or not value or name in assignments:
                return "", {}
            assignments[name] = value
    return match.group(1), assignments


def _structured_value_is_derivable(
    target_type: str,
    named_source_types: dict[str, set[str]],
    fields_by_type: dict[str, dict[str, str]],
) -> bool:
    target_fields = fields_by_type.get(target_type, {})
    if not target_fields:
        return False
    for name, expected in target_fields.items():
        available = named_source_types.get(name.casefold(), set())
        if any(types_compatible(source, expected) for source in available):
            continue
        if runtime_value_source(expected) or type_can_default(expected):
            continue
        return False
    return True


def operation_catalog(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for class_item in model.get("Classes") or []:
        if not isinstance(class_item, dict):
            continue
        owner = class_name(class_item)
        stereotype = text(class_item.get("stereotype")).casefold()
        for operation in class_item.get("operations") or []:
            if not isinstance(operation, dict):
                continue
            operation_id = text(operation.get("operationId"))
            if operation_id:
                result[operation_id] = {
                    **operation,
                    "className": owner,
                    "stereotype": stereotype,
                }
    return result


def _model_schema(model: dict[str, Any], _index: ScenarioIndex) -> list[Finding]:
    try:
        BCEModel.model_validate(model)
    except Exception as error:  # Pydantic supplies the exact schema location.
        return [Finding("class.model.schema", str(error), "BCEModel", origin="schema")]
    return []


def _operation_ids(model: dict[str, Any], _index: ScenarioIndex) -> list[Finding]:
    findings: list[Finding] = []
    operations = operation_catalog(model)
    for operation_id, operation in operations.items():
        if operation_id != canonical_operation_id(
            operation["className"], text(operation.get("name")), list(operation.get("parameters") or []),
        ):
            findings.append(Finding(
                "class.model.operation-ids", "operationId is not canonical", operation_id,
            ))
    return findings


def _operation_names(model: dict[str, Any], _index: ScenarioIndex) -> list[Finding]:
    findings: list[Finding] = []
    for operation_id, operation in operation_catalog(model).items():
        normalized_name = re.sub(r"[^a-z0-9]", "", text(operation.get("name")).casefold())
        if normalized_name in {"none", "noop", "notapplicable"}:
            findings.append(Finding(
                "class.model.operation-names",
                "operation name must describe concrete behavior",
                operation_id,
            ))
    return findings


def _collaborations(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        text(item.get("collaborationId")): item
        for item in model.get("Collaborations") or []
        if isinstance(item, dict)
    }


def _collaboration_coverage(
    model: dict[str, Any], index: ScenarioIndex,
) -> list[Finding]:
    collaborations = _collaborations(model)
    expected = {group.id for group in index.groups}
    if set(collaborations) == expected:
        return []
    return [
        Finding(
            "class.model.collaboration-coverage",
            f"collaborations must exactly cover execution groups; missing={sorted(expected - set(collaborations))}, extra={sorted(set(collaborations) - expected)}",
            "Collaborations",
        )
    ]


def _collaboration_rule(
    rule_id: str,
) -> CheckSpec[dict[str, Any], ScenarioIndex]:
    """협업 검사를 완성 모델의 실행 그룹 순서로 투영한다.

    협업 검증 모듈이 이 모듈의 타입 도우미를 사용하므로 import는 실행 시점에 한다.
    각 검사기는 자신의 ``rule_id``만 반환하며, 등록 순서가 보고서 순서가 된다.
    """
    def check(model: dict[str, Any], index: ScenarioIndex) -> list[Finding]:
        from app.design.services.class_diagram.validation.collaboration import (
            CollaborationContext,
            _collaboration_bindings,
            _collaboration_contract,
            _collaboration_order,
        )

        rules = {
            "class.collaboration.contract": _collaboration_contract,
            "class.collaboration.order": _collaboration_order,
            "class.collaboration.bindings": _collaboration_bindings,
        }
        owned_check = rules[rule_id]
        collaborations = _collaborations(model)
        findings: list[Finding] = []
        for group in index.groups:
            collaboration = collaborations.get(group.id)
            if collaboration:
                context = CollaborationContext(index, model, group)
                findings.extend(owned_check(collaboration, context))
        return findings

    return CheckSpec(rule_id=rule_id, run=check)


# schema에서 시작해 canonical ID와 coverage를 확인한 뒤 각 execution group에 collaboration
# 규칙을 투영한다. 이 순서 덕분에 후속 검사가 깨진 shape를 의미 있는 모델로 가정하지 않는다.
CLASS_MODEL_CHECKS: tuple[CheckSpec[dict[str, Any], ScenarioIndex], ...] = (
    CheckSpec("class.model.schema", _model_schema),
    CheckSpec("class.model.operation-ids", _operation_ids),
    CheckSpec("class.model.operation-names", _operation_names),
    CheckSpec("class.model.collaboration-coverage", _collaboration_coverage),
    _collaboration_rule("class.collaboration.contract"),
    _collaboration_rule("class.collaboration.order"),
    _collaboration_rule("class.collaboration.bindings"),
)


def validate_class_model(
    model: BCEModel | dict[str, Any], index: ScenarioIndex
) -> ValidationReport:
    """저장할 BCE 모델 전체를 결정론적으로 검증한다.

    Args:
        model: 타입 모델 또는 별칭을 사용하는 저장 JSON이다.
        index: 유스케이스와 실행 그룹의 정규화된 입력이다.

    Returns:
        규칙 등록 순서로 정렬된 finding을 담은 불변 보고서다.

    Notes:
        검증은 모델을 수정하거나 repair를 시작하지 않는다. repair 여부는 서비스가
        보고서를 받은 뒤 소유 단위별로 결정한다.
    """
    if isinstance(model, BCEModel):
        payload = model.model_dump(by_alias=True)
    else:
        payload = model
        try:
            BCEModel.model_validate(payload)
        except Exception:  # 정확한 위치와 메시지는 등록된 schema 검사가 소유한다.
            return run_checks(CLASS_MODEL_CHECKS[:1], payload, index)
    return run_checks(CLASS_MODEL_CHECKS, payload, index)


__all__ = [
    "CLASS_MODEL_CHECKS",
    "class_name",
    "derived_value_parts",
    "derived_value_source",
    "operation_catalog",
    "optional_inner_type",
    "runtime_value_source",
    "type_can_default",
    "validate_class_model",
]
