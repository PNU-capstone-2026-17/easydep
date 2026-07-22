"""AWS CloudFormation 스키마 → 속성 제약 추출.

세 종류의 제약을 뽑는다:

1. **값 제약** (`minimum`/`maximum`/`minLength`/`pattern`/`enum`/`default` …)
   → evidence=`cfn-schema`, confidence 1.0.
2. **변경 제약** (`required` / `createOnlyProperties` / `conditionalCreateOnlyProperties`
   / `readOnlyProperties`) → 같은 evidence. 실측상 이쪽이 훨씬 풍부하다
   (스키마 1628개 중 86.5%가 createOnlyProperties를 명시).
3. **산문 제약** — 정작 중요한 용량 숫자(EBS 볼륨 1~65,536 GiB 등)가 설명문에만
   있어서 `prose.py`로 추출한다 → evidence=`cfn-description`, confidence 0.6~0.8.

산문 오탐 방어(prose.py의 veto/자기모순 검사에 더해 이 파서가 책임지는 부분):
- **R1** 같은 (타입, 프로퍼티, 종류)에 스키마 값이 이미 있으면 산문은 만들지 않는다.
- **R2** 산문 값이 스키마 값과 모순이면(산문 max < 스키마 min 등) 버리고 경고한다.
- **게이트** 산문 범위는 `type`이 integer/number인 프로퍼티에만 적용한다. 이 규칙이
  `Lambda.EphemeralStorage`처럼 산문이 `$ref` 래퍼에 붙어 있고 실제 제약은 한 단계
  아래(`definitions.EphemeralStorage.Size`의 minimum/maximum)에 있는 함정도 막아준다.
"""

from __future__ import annotations

import json
import sys
import zipfile
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from capacitykb.model import CapacitySet, Constraint
from capacitykb.prose import extract_default, extract_enum, extract_ranges
from kbcommon.fetch import describe_source, fetch_cached
from kbcommon.invariants import announce
from kbcommon.sources import SOURCES

# ⚠️ 고정 불가 — kbcommon/sources.py의 `cfn-schema` 항목 참조.
DEFAULT_ZIP_URL = SOURCES["cfn-schema"].url

EVIDENCE_SCHEMA = "cfn-schema"
EVIDENCE_PROSE = "cfn-description"

# 스키마 키워드 → 우리 모델의 제약 종류
_VALUE_KEYWORDS: dict[str, str] = {
    "minimum": "min",
    "maximum": "max",
    "minLength": "min_length",
    "maxLength": "max_length",
    "minItems": "min_items",
    "maxItems": "max_items",
    "pattern": "pattern",
    "enum": "enum",
    "default": "default",
}

# 포인터 목록 키워드 → mutability 값
_MUTABILITY_POINTERS: dict[str, str] = {
    "createOnlyProperties": "create_only",
    "conditionalCreateOnlyProperties": "conditional_create_only",
    "readOnlyProperties": "read_only",
}

_NUMERIC_TYPES = frozenset({"integer", "number"})
_MAX_DEPTH = 32


def iter_schemas(zip_path: Path) -> Iterator[dict]:
    """스키마 zip에서 리소스 스키마를 하나씩 읽는다 (깨진 파일은 경고 후 건너뜀)."""
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            if not name.endswith(".json"):
                continue
            try:
                yield json.loads(archive.read(name))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                print(f"경고: 스키마 파싱 실패, 건너뜀 — {name}: {exc}", file=sys.stderr)


def _pointer_to_property(pointer: str) -> str | None:
    """"/properties/VpcId" → "VpcId", "/properties/A/B" → "A/B"."""
    parts = pointer.split("/")
    if len(parts) >= 3 and parts[1] == "properties":
        return "/".join(parts[2:])
    return None


def _join(path: str, name: str) -> str:
    return f"{path}/{name}" if path else name


def _scalar_type(prop: dict) -> str | None:
    """프로퍼티의 JSON 타입. `["string", "null"]` 같은 리스트 표기도 처리한다."""
    value_type = prop.get("type")
    if isinstance(value_type, list):
        return next((t for t in value_type if t != "null"), None)
    return value_type if isinstance(value_type, str) else None


def _default_contradicts_bounds(prop: dict, default) -> bool:
    """기본값이 자기 속성의 min·max를 벗어나는가 — 그러면 싣지 않는다.

    **상류 스키마가 실제로 모순돼 있다.** AWS가 이렇게 적어 놓았다:

        "Period": {"type": "number", "default": 0, "minimum": 10, "maximum": 86400}

    `default: 0`의 실체는 "안 적어도 되고, 안 적으면 0으로 직렬화된다"이지 쓸 수 있는
    값이 아니다. 그대로 실으면 에이전트가 **스키마가 거부할 값**을 권하게 된다.
    AWS의 의도를 우리가 지어낼 수는 없으므로, 아는 것만 남긴다 — 경계는 싣고
    모순된 기본값은 버린다. "기본값 정보 없음"이 "기본값 0"보다 정직하다.

    30건이 이에 해당하며 전부 `cfn-schema`(신뢰도 1.0)라, 소비자가 신뢰도로
    걸러낼 수도 없었다.
    """
    if not isinstance(default, (int, float)) or isinstance(default, bool):
        return False
    low, high = prop.get("minimum"), prop.get("maximum")
    if isinstance(low, (int, float)) and default < low:
        return True
    return isinstance(high, (int, float)) and default > high


def _add_schema_constraints(
    capacity: CapacitySet, type_id: str, prop_path: str, prop: dict
) -> None:
    """프로퍼티 하나의 스키마 키워드를 제약 레코드로 만든다."""
    value_type = _scalar_type(prop)
    for keyword, kind in _VALUE_KEYWORDS.items():
        if keyword not in prop:
            continue
        value = prop[keyword]
        if keyword == "enum" and not isinstance(value, list):
            continue
        if keyword == "default" and _default_contradicts_bounds(prop, value):
            continue
        capacity.add_constraint(
            Constraint(
                type_id=type_id,
                property=prop_path,
                kind=kind,
                value=value,
                evidence=EVIDENCE_SCHEMA,
                value_type=value_type,
            )
        )


def _walk(
    capacity: CapacitySet,
    type_id: str,
    node: dict,
    path: str,
    *,
    readonly_top: set[str],
    readonly_paths: set[str],
    prose_targets: list[tuple[str, dict]],
    depth: int,
) -> None:
    """properties/items를 재귀 순회하며 제약을 수집한다."""
    if depth > _MAX_DEPTH or not isinstance(node, dict):
        return

    required = node.get("required")
    required_set = set(required) if isinstance(required, list) else set()

    properties = node.get("properties")
    if isinstance(properties, dict):
        for name, prop in properties.items():
            if not isinstance(prop, dict):
                continue
            prop_path = _join(path, name)
            is_readonly_top = not path and name in readonly_top

            # 읽기 전용 속성은 `required`로 싣지 않는다. CFN의
            # `definitions.X.required`는 "응답에 늘 들어 있다"는 뜻인데, 그대로
            # 옮기면 "네가 채워야 한다"로 읽힌다 — 사용자에게 채울 수 없는 칸을
            # 채우라고 하게 된다. EmailContact가 그랬다: 하위 6개가 전부
            # readOnlyProperties에 있으면서 definitions.EmailContact.required에도 있다.
            if name in required_set and prop_path not in readonly_paths:
                capacity.add_constraint(
                    Constraint(
                        type_id=type_id,
                        property=prop_path,
                        kind="required",
                        value=True,
                        evidence=EVIDENCE_SCHEMA,
                    )
                )
            _add_schema_constraints(capacity, type_id, prop_path, prop)
            if not is_readonly_top and prop.get("description"):
                # 산문은 스키마 제약을 다 수집한 뒤에 처리한다 (R1 판정을 위해)
                prose_targets.append((prop_path, prop))
            _walk(
                capacity,
                type_id,
                prop,
                prop_path,
                readonly_top=readonly_top,
                readonly_paths=readonly_paths,
                prose_targets=prose_targets,
                depth=depth + 1,
            )

    items = node.get("items")
    if isinstance(items, dict):
        _walk(
            capacity,
            type_id,
            items,
            path,
            readonly_top=readonly_top,
            readonly_paths=readonly_paths,
            prose_targets=prose_targets,
            depth=depth + 1,
        )


def _machine_value(capacity: CapacitySet, type_id: str, prop_path: str, kind: str):
    found = capacity.get_constraint(type_id, prop_path, kind)
    return None if found is None else found.value


def _apply_prose(
    capacity: CapacitySet,
    type_id: str,
    prop_path: str,
    prop: dict,
    stats: Counter,
) -> None:
    """산문 추출 결과를 R1/R2 방어를 거쳐 레코드로 만든다."""
    description = prop.get("description") or ""
    value_type = _scalar_type(prop)
    numeric = value_type in _NUMERIC_TYPES

    extractions = []
    if numeric:
        extractions.extend(extract_ranges(description, prop_path))
        default = extract_default(description, numeric=True)
        if default is not None:
            extractions.append(default)
    elif value_type == "string":
        default = extract_default(description, numeric=False)
        if default is not None:
            extractions.append(default)
        enum = extract_enum(description)
        if enum is not None:
            extractions.append(enum)

    machine_min = _machine_value(capacity, type_id, prop_path, "min")
    machine_max = _machine_value(capacity, type_id, prop_path, "max")

    for item in extractions:
        # R1: 스키마에 같은 종류가 이미 있으면 산문은 쓰지 않는다
        if capacity.has_constraint(type_id, prop_path, item.kind):
            continue
        # R2: 스키마 값과 모순되면 버린다 (정규식 버그 신호)
        if item.kind == "min" and machine_max is not None and item.value > machine_max:
            print(
                f"경고: 산문 하한이 스키마 상한과 모순 — {type_id}.{prop_path} "
                f"({item.value} > {machine_max}), 버림",
                file=sys.stderr,
            )
            continue
        if item.kind == "max" and machine_min is not None and item.value < machine_min:
            print(
                f"경고: 산문 상한이 스키마 하한과 모순 — {type_id}.{prop_path} "
                f"({item.value} < {machine_min}), 버림",
                file=sys.stderr,
            )
            continue
        stats[item.rule] += 1
        capacity.add_constraint(
            Constraint(
                type_id=type_id,
                property=prop_path,
                kind=item.kind,
                value=item.value,
                evidence=EVIDENCE_PROSE,
                value_type=value_type,
                unit=item.unit,
                conditional=item.conditional,
                note=item.note,
            )
        )


def parse_schemas(
    schemas: list[dict], *, prose: bool = True, stats: Counter | None = None
) -> CapacitySet:
    """파싱된 CFN 스키마 목록에서 제약 레코드를 만든다."""
    capacity = CapacitySet()
    counters = stats if stats is not None else Counter()

    for schema in schemas:
        type_name = schema.get("typeName")
        if not isinstance(type_name, str) or not type_name:
            continue
        type_id = f"aws::{type_name}"
        # readonly_top은 최상위만(산문 게이트용), readonly_paths는 중첩까지 전부다.
        # `required`를 거를 때는 후자가 필요하다 — 모순 9건이 전부 중첩 경로
        # (`EmailContact/Arn` 등)에 있었다.
        readonly_paths = {
            prop
            for pointer in schema.get("readOnlyProperties") or []
            if (prop := _pointer_to_property(pointer))
        }
        readonly_top = {p for p in readonly_paths if "/" not in p}

        # 변경 제약 (포인터 목록) — 스키마 레벨
        for keyword, mutability in _MUTABILITY_POINTERS.items():
            for pointer in schema.get(keyword) or []:
                prop_path = _pointer_to_property(pointer)
                if prop_path is None:
                    continue
                capacity.add_constraint(
                    Constraint(
                        type_id=type_id,
                        property=prop_path,
                        kind="mutability",
                        value=mutability,
                        evidence=EVIDENCE_SCHEMA,
                    )
                )

        prose_targets: list[tuple[str, dict]] = []
        _walk(
            capacity,
            type_id,
            schema,
            "",
            readonly_top=readonly_top,
            readonly_paths=readonly_paths,
            prose_targets=prose_targets,
            depth=0,
        )
        # definitions의 프로퍼티는 정의명을 접두사로 붙여 구분한다
        # (예: S3 Bucket의 Tiering/Days 와 RecordExpiration/Days 는 다른 프로퍼티)
        for def_name, definition in (schema.get("definitions") or {}).items():
            if isinstance(definition, dict):
                _walk(
                    capacity,
                    type_id,
                    definition,
                    def_name,
                    readonly_top=set(),
                    readonly_paths=readonly_paths,
                    prose_targets=prose_targets,
                    depth=0,
                )

        if prose:
            for prop_path, prop in prose_targets:
                _apply_prose(capacity, type_id, prop_path, prop, counters)

    return capacity


def parse_zip(
    zip_path: Path, *, prose: bool = True, stats: Counter | None = None
) -> CapacitySet:
    """스키마 zip을 읽어 제약 레코드를 만든다."""
    return parse_schemas(list(iter_schemas(zip_path)), prose=prose, stats=stats)


def build(
    output: Path,
    *,
    zip_url: str = DEFAULT_ZIP_URL,
    prose: bool = True,
    refresh: bool = False,
) -> CapacitySet:
    """스키마를 받아 파싱하고 output에 저장한 뒤 결과를 반환한다."""
    zip_path = fetch_cached(zip_url, "CloudformationSchema.zip", refresh=refresh)
    stats: Counter = Counter()
    capacity = parse_zip(zip_path, prose=prose, stats=stats)
    capacity.provenance = [describe_source(zip_path, "cfn-schema")]
    # AWS는 스키마 zip 전체를 읽으므로 범위 제한이 없다(scope 생략 = 전부).
    capacity.coverage = [{
        "provider": "aws",
        "types": len({c.type_id for c in capacity.constraints}),
        "note": "CloudFormation 스키마 zip 전체를 읽는다.",
    }]
    announce(capacity.save(output), "capacitykb/cfn")

    by_evidence: Counter = Counter(c.evidence for c in capacity.constraints)
    evidence_summary = ", ".join(f"{k}={v}" for k, v in sorted(by_evidence.items()))
    print(f"cfn: 제약 {len(capacity.constraints)}개 ({evidence_summary}) → {output}")
    if stats:
        rules = ", ".join(f"{k}={v}" for k, v in sorted(stats.items()))
        print(f"     산문 규칙별 발화: {rules}")
    return capacity
