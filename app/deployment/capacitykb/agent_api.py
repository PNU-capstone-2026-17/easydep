"""에이전트용 사전 정의 질의 API (capacitykb).

graphkb의 agent_api와 같은 관례: 예외 대신 에이전트가 그대로 읽을 수 있는
한국어 텍스트를 반환하고, 산출물이 없으면 빌드 명령을 안내한다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from capacitykb.model import CapacitySet
from capacitykb.query import (
    check_value,
    find_quota,
    immutable_properties,
    limits_for,
    resolve_type,
)

DEFAULT_OUTPUT_DIR = Path("output")
CAPACITY_FILES = ("aws-capacity.json", "azure-capacity.json", "azure-quota.json")

_MISSING_MESSAGE = (
    "용량·제약 산출물이 없습니다. 먼저 `python -m capacitykb build --source "
    "cfn|azure|azure-quota` 로 생성하세요."
)


@lru_cache(maxsize=4)
def _load_merged_cached(output_dir: str) -> CapacitySet | None:
    base = Path(output_dir)
    merged = CapacitySet()
    found = False
    for name in CAPACITY_FILES:
        path = base / name
        if path.exists():
            merged.merge(CapacitySet.load(path))
            found = True
    return merged if found else None


def load_merged(output_dir: Path | str = DEFAULT_OUTPUT_DIR) -> CapacitySet | None:
    """output/의 용량 산출물을 모두 병합해 반환한다 (없으면 None, 캐시됨)."""
    return _load_merged_cached(str(output_dir))


def _resolve(capacity: CapacitySet, name: str):
    try:
        return resolve_type(capacity, name), None
    except ValueError as exc:
        return None, str(exc)


def _describe(constraint) -> str:
    unit = f" {constraint.unit}" if constraint.unit else ""
    labels = {
        "min": "최소",
        "max": "최대",
        "min_length": "최소 길이",
        "max_length": "최대 길이",
        "min_items": "최소 개수",
        "max_items": "최대 개수",
        "pattern": "패턴",
        "enum": "허용값",
        "default": "기본값",
        "required": "필수",
        "mutability": "변경",
    }
    mutability_labels = {
        "create_only": "생성 후 변경 불가 (바꾸면 리소스 재생성)",
        "conditional_create_only": "조건부 변경 불가 (경우에 따라 재생성)",
        "read_only": "읽기 전용 (설정 불가)",
    }
    if constraint.kind == "mutability":
        text = mutability_labels.get(constraint.value, constraint.value)
    elif constraint.kind == "required":
        text = "필수 항목"
    else:
        text = f"{labels.get(constraint.kind, constraint.kind)} {constraint.value}{unit}"
    tags = []
    if constraint.conditional:
        tags.append("조건부")
    tags.append(f"근거 {constraint.evidence}, 신뢰도 {constraint.confidence}")
    suffix = f" ({', '.join(tags)})"
    note = f"\n    ※ {constraint.note}" if constraint.note else ""
    return f"  - {constraint.property}: {text}{suffix}{note}"


def property_limits(
    resource_type: str,
    property_name: str | None = None,
    *,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
) -> str:
    """리소스 타입(또는 특정 속성)의 제약을 요약해 반환한다."""
    capacity = load_merged(output_dir)
    if capacity is None:
        return _MISSING_MESSAGE
    type_id, error = _resolve(capacity, resource_type)
    if type_id is None:
        return error
    found = limits_for(capacity, type_id, prop=property_name)
    if not found:
        target = f"{type_id}.{property_name}" if property_name else type_id
        return f"{target} 에 대해 알려진 제약이 없습니다."
    header = f"{type_id}" + (f".{property_name}" if property_name else "")
    lines = [f"{header} 제약 {len(found)}건:"]
    lines.extend(_describe(c) for c in found)
    return "\n".join(lines)


def check(
    resource_type: str,
    property_name: str,
    value: float | str,
    *,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
) -> str:
    """값이 허용 범위인지 판정한다."""
    capacity = load_merged(output_dir)
    if capacity is None:
        return _MISSING_MESSAGE
    type_id, error = _resolve(capacity, resource_type)
    if type_id is None:
        return error
    result = check_value(capacity, type_id, property_name, value)
    target = f"{type_id}.{property_name} = {value}"

    if result.verdict == "unknown":
        return (
            f"{type_id}.{property_name} 에 대해 알려진 제약이 없어 판정할 수 없습니다. "
            "지식베이스에 없는 값이므로 공식 문서를 확인하세요."
        )
    if result.verdict == "violation":
        lines = [f"불가: {target} 는 제약을 위반합니다."]
        lines.extend(f"  - {v}" for v in result.violations)
    elif result.verdict == "advisory":
        # 확정 제약은 없지만 참고 정보상 벗어남 → "가능"이라고 하면 거짓 긍정이 된다
        lines = [
            f"판정 보류: {target} 는 확정된 제약을 위반하진 않지만, "
            "신뢰도가 낮은 참고 정보상 범위를 벗어납니다. 공식 문서 확인을 권합니다."
        ]
    else:
        lines = [f"가능: {target} 는 알려진 제약 {result.checked}건을 만족합니다."]

    if result.advisories:
        lines.append("  참고(신뢰도가 낮아 확정 판정엔 쓰지 않음):")
        lines.extend(f"  - {a}" for a in result.advisories)
    return "\n".join(lines)


def immutable(
    resource_type: str, *, output_dir: Path | str = DEFAULT_OUTPUT_DIR
) -> str:
    """변경 시 리소스가 재생성되는 속성들을 반환한다."""
    capacity = load_merged(output_dir)
    if capacity is None:
        return _MISSING_MESSAGE
    type_id, error = _resolve(capacity, resource_type)
    if type_id is None:
        return error
    found = immutable_properties(capacity, type_id)
    if not found:
        return f"{type_id} 에 변경 불가로 알려진 속성이 없습니다."
    lines = [f"{type_id} 의 변경 시 재생성되는 속성 {len(found)}개:"]
    lines.extend(_describe(c) for c in found)
    return "\n".join(lines)


def allowed_values(
    resource_type: str,
    property_name: str,
    *,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
) -> str:
    """속성의 허용값(enum)·패턴·기본값을 반환한다."""
    capacity = load_merged(output_dir)
    if capacity is None:
        return _MISSING_MESSAGE
    type_id, error = _resolve(capacity, resource_type)
    if type_id is None:
        return error
    found = [
        c
        for c in limits_for(capacity, type_id, prop=property_name)
        if c.kind in ("enum", "pattern", "default")
    ]
    if not found:
        return f"{type_id}.{property_name} 에 알려진 허용값/패턴 정보가 없습니다."
    lines = [f"{type_id}.{property_name} 허용값 정보:"]
    lines.extend(_describe(c) for c in found)
    return "\n".join(lines)


def service_quota(
    keyword: str, *, limit: int = 15, output_dir: Path | str = DEFAULT_OUTPUT_DIR
) -> str:
    """서비스 쿼터(계정/구독 단위 상한)를 키워드로 찾는다."""
    capacity = load_merged(output_dir)
    if capacity is None:
        return _MISSING_MESSAGE
    found = find_quota(capacity, keyword)
    if not found:
        return (
            f"'{keyword}' 에 해당하는 쿼터가 없습니다. "
            "현재 쿼터 데이터는 Azure만 수록돼 있습니다."
        )
    shown = found[: max(1, limit)]
    lines = [f"'{keyword}' 쿼터 {len(found)}건 중 {len(shown)}건:"]
    for quota in shown:
        parts = [f"기본 {quota.default}"] if quota.default is not None else []
        if quota.maximum is not None:
            parts.append(f"최대 {quota.maximum}")
        detail = ", ".join(parts) if parts else "값 없음"
        note = f" ※ {quota.note}" if quota.note else ""
        lines.append(
            f"  - [{quota.provider}] {quota.name}: {detail} "
            f"(출처 {quota.source_doc}, 신뢰도 {quota.confidence}){note}"
        )
    return "\n".join(lines)
