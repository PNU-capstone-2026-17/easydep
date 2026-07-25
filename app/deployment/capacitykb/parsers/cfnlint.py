"""cfn-lint 확장 데이터 → 리전별 허용값.

**왜 필요한가.** "이 리전에서 이 인스턴스 타입을 쓸 수 있나"는 배포에서 늘 나오는
질문인데, CloudFormation 레지스트리 스키마에는 `InstanceType`이 그냥 문자열이다.
cfn-lint는 그걸 표현할 방법이 없어서 **별도 파일로 관리**한다 — 그래서 레지스트리와
겹치지 않는다.

실측(1.53.1): 17개 파일에 **(리전, 값) 쌍 79,809건**. 가장 큰 것이
`aws_ec2_instance/instancetype_enum.json`으로 38개 리전 24,183값이다.

**함정 — `"all"` 키를 리전으로 읽으면 안 된다.**
`aws_ec2_instance/instancetype_enum.json`의 `"all"`은 **빈 enum**이다(실측, 이 파일
하나만 그렇다). 리전인 줄 알고 그대로 담으면 "허용값이 0개"인 제약이 생기고,
그러면 **모든 인스턴스 타입이 거부된다.** 하필 값이 가장 많은 파일에서 그렇다.
잘못 막는 게 침묵보다 나쁘다는 우리 원칙에 정면으로 어긋나므로, 리전 모양
(`us-east-1` 꼴)에 맞는 키만 읽는다.

**속성 이름은 짐작하지 않고 대조한다.** 파일명(`instancetype_enum.json`)에서 후보를
만들되, 그 이름이 **CFN 스키마에 실재하는지 확인**하고 없으면 담지 않고 센다.
`elasticsearchclusterconfig_instancetype_enum.json`처럼 중첩 경로를 담은 이름이 있어
규칙만으로는 못 맞춘다.
"""

from __future__ import annotations

import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path

from capacitykb.model import CapacitySet, Constraint
from kbcommon.fetch import describe_source_set
from kbcommon.sources import SOURCES

EVIDENCE = "cfn-lint-region"
#: `us-east-1` · `ap-northeast-2` 꼴만 리전으로 본다. `"all"`은 리전이 아니다.
_REGION = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-\d$")
_EXT_DIR = "/data/schemas/extensions/"


class Report:
    def __init__(self) -> None:
        self.unmapped_types: list[str] = []
        self.unmapped_props: list[tuple[str, str]] = []
        self.skipped_all = 0
        """리전이 아닌 `all` 키를 건너뛴 횟수. 조용히 넘기면 함정을 다시 밟는다."""


def property_names(schema: dict) -> set[str]:
    """CFN 스키마 어디에든 나오는 프로퍼티 이름 (중첩 정의 포함)."""
    out: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                out.update(props)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return out


def type_id_of(directory: str, known: dict[str, str]) -> str | None:
    """`aws_ec2_instance` → `aws::AWS::EC2::Instance`. 못 찾으면 None."""
    return known.get(directory.replace("_", "").lower())


def property_of(filename: str, available: set[str]) -> str | None:
    """`instancetype_enum.json` → `InstanceType` (스키마에 있을 때만).

    파일명이 중첩 경로를 담기도 한다(`elasticsearchclusterconfig_instancetype_enum`).
    그럴 땐 **뒤에서부터 잘라가며** 실재하는 이름을 찾는다. 못 찾으면 None —
    없는 속성 이름을 지어내면 그 제약은 영원히 아무 것에도 안 걸린다.
    """
    stem = filename[: -len("_enum.json")] if filename.endswith("_enum.json") else filename
    tokens = stem.split("_")
    lowered = {p.lower(): p for p in available}
    for start in range(len(tokens)):
        candidate = "".join(tokens[start:])
        hit = lowered.get(candidate)
        if hit:
            return hit
    return None


def parse_wheel(
    wheel: Path, *, cfn_schemas: dict[str, dict]
) -> tuple[CapacitySet, Report]:
    """cfn-lint wheel에서 리전별 허용값을 뽑는다.

    Args:
        cfn_schemas: `aws::AWS::EC2::Instance` → 레지스트리 스키마.
            속성 이름을 대조하는 데 쓴다.
    """
    capacity = CapacitySet()
    report = Report()
    by_key = {t.split("::", 1)[1].replace("::", "").lower(): t for t in cfn_schemas}
    props_cache: dict[str, set[str]] = {}

    with zipfile.ZipFile(wheel) as zf:
        for name in zf.namelist():
            if _EXT_DIR not in name or not name.endswith("_enum.json"):
                continue
            try:
                data = json.loads(zf.read(name))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            regions = {
                key: body["enum"]
                for key, body in data.items()
                if isinstance(body, dict) and isinstance(body.get("enum"), list)
            }
            real = {k: v for k, v in regions.items() if _REGION.match(k)}
            if not real:
                continue
            report.skipped_all += len(regions) - len(real)

            directory, filename = name.split(_EXT_DIR)[1].split("/")
            type_id = type_id_of(directory, by_key)
            if type_id is None:
                report.unmapped_types.append(directory)
                continue
            if type_id not in props_cache:
                props_cache[type_id] = property_names(cfn_schemas[type_id])
            prop = property_of(filename, props_cache[type_id])
            if prop is None:
                report.unmapped_props.append((directory, filename))
                continue

            for region, values in sorted(real.items()):
                if not values:
                    # 빈 목록은 "아무것도 못 쓴다"가 아니라 "데이터가 없다"로 본다.
                    continue
                capacity.add_constraint(
                    Constraint(
                        type_id=type_id, property=prop, kind="enum",
                        value=sorted(values), evidence=EVIDENCE,
                        conditions=({"property": "Region", "op": "eq", "value": region},),
                    )
                )
    return capacity, report


def build(output: Path, *, refresh: bool = False) -> CapacitySet:
    from capacitykb.parsers.cfn import iter_schemas
    from kbcommon.fetch import fetch_cached

    source = SOURCES["cfn-lint"]
    wheel = fetch_cached(source.url, f"cfn-lint-{source.pin}.whl", refresh=refresh)

    zip_path = fetch_cached(
        SOURCES["cfn-schema"].url, "CloudformationSchema.zip", refresh=refresh
    )
    schemas = {
        f"aws::{schema['typeName']}": schema
        for schema in iter_schemas(zip_path)
        if isinstance(schema, dict) and schema.get("typeName")
    }
    capacity, report = parse_wheel(wheel, cfn_schemas=schemas)
    capacity.provenance = [describe_source_set([wheel], source.key)]
    capacity.coverage = [{
        "provider": "aws",
        "types": len({c.type_id for c in capacity.constraints}),
        "type_ids": sorted({c.type_id for c in capacity.constraints}),
        "note": (
            "per-region allowed values that cfn-lint maintains separately. the "
            "CloudFormation registry has no way to express them, which is why the "
            "data lives apart and does not overlap. without the region code there is "
            "no verdict, so they are stored as a condition."
        ),
    }]

    regions = len({c.conditions[0]["value"] for c in capacity.constraints})
    values = sum(len(c.value) for c in capacity.constraints)
    print(
        f"cfn-lint: 제약 {len(capacity.constraints):,}건 "
        f"({len({c.type_id for c in capacity.constraints})}종 · 리전 {regions}개 · "
        f"값 {values:,}개)"
    )
    if report.skipped_all:
        print(
            f"  리전이 아닌 키 {report.skipped_all}개를 건너뜀 — "
            "`all`을 리전으로 읽으면 EC2 인스턴스 타입이 **빈 허용값**이 되어 "
            "전부 거부됩니다(실측).",
            file=sys.stderr,
        )
    if report.unmapped_types or report.unmapped_props:
        print(
            f"  안 담음: CFN 타입 못 찾음 {len(report.unmapped_types)} · "
            f"속성 이름 못 찾음 {len(report.unmapped_props)}"
            + (f" {report.unmapped_props}" if report.unmapped_props else ""),
            file=sys.stderr,
        )
    capacity.save(output)
    return capacity


# --- if/then 조건 블록 -------------------------------------------------------
#
# 리전별 허용값과 **같은 wheel의 다른 모양**이다. 저쪽은 `{리전: {enum: [...]}}`이고
# 이쪽은 JSON Schema의 `allOf: [{if: ..., then: ...}]`이다.
#
# 실측(1.53.1): 파일 10개에 블록 987개.
#   조건 개수 — 2개가 938블록 · 1개가 42 · 0개가 7
#   연산자    — eq 979 · matches 939
#   대상      — DBInstanceClass→enum 938 · EngineVersion→enum 21 · maximum 4 · 그 외
#
# **여기서는 속성 이름을 짐작하지 않는다.** 리전 쪽은 파일명에서 이름을 만들어야
# 했지만, if/then은 `then.properties`의 키가 곧 속성 이름이다. 그래도 CFN 스키마에
# 실재하는지는 대조한다 — 없는 이름을 담으면 그 제약은 영원히 아무 것에도 안 걸린다.

EVIDENCE_CONDITIONAL = "cfn-lint-conditional"

#: `then`이 정하는 것 → 우리 `kind`. 여기 없는 모양(`format`·`items`)은 담지 않고 센다.
_THEN_KINDS = {"enum": "enum", "maximum": "max", "minimum": "min"}


class ConditionReport:
    def __init__(self) -> None:
        self.no_conditions = 0
        """`if`에 값 조건이 없어 **언제 적용되는지 말할 수 없는** 블록."""
        self.unsupported_then: Counter = Counter()
        """우리가 담을 칸이 없는 `then` 모양. 조용히 넘기면 다음 사람이 또 조사한다."""
        self.unmapped_types: list[str] = []
        self.unmapped_props: list[tuple[str, str]] = []


def _conditions_of(if_block: dict) -> tuple[dict, ...]:
    """`if.properties` → 우리 조건 목록.

    `{"type": "string"}`처럼 값이 없는 항목은 **조건이 아니라 대상 표시**다 —
    if 스키마가 그 속성의 존재를 요구하려고 넣어 둔 것이라 조건으로 읽으면 안 된다.
    """
    out = []
    for name, spec in (if_block.get("properties") or {}).items():
        if not isinstance(spec, dict):
            continue
        if "const" in spec:
            out.append({"property": name, "op": "eq", "value": spec["const"]})
        elif "pattern" in spec:
            out.append({"property": name, "op": "matches", "value": spec["pattern"]})
    return tuple(sorted(out, key=lambda c: c["property"]))


def parse_conditions(
    wheel: Path, *, cfn_schemas: dict[str, dict]
) -> tuple[CapacitySet, ConditionReport]:
    """cfn-lint의 `allOf: [{if, then}]` 블록을 조건부 제약으로."""
    capacity = CapacitySet()
    report = ConditionReport()
    by_key = {t.split("::", 1)[1].replace("::", "").lower(): t for t in cfn_schemas}
    props_cache: dict[str, set[str]] = {}

    with zipfile.ZipFile(wheel) as zf:
        for name in zf.namelist():
            if _EXT_DIR not in name or not name.endswith(".json"):
                continue
            try:
                data = json.loads(zf.read(name))
            except Exception:
                continue
            blocks = data.get("allOf") if isinstance(data, dict) else None
            if not isinstance(blocks, list):
                continue

            directory = name.split(_EXT_DIR)[1].split("/")[0]
            type_id = type_id_of(directory, by_key)
            if type_id is None:
                report.unmapped_types.append(directory)
                continue
            if type_id not in props_cache:
                props_cache[type_id] = property_names(cfn_schemas[type_id])
            available = props_cache[type_id]

            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if_block, then_block = block.get("if"), block.get("then")
                if not (isinstance(if_block, dict) and isinstance(then_block, dict)):
                    continue
                conditions = _conditions_of(if_block)
                if not conditions:
                    # 언제 적용되는지 못 말하면 담지 않는다. 무조건으로 담으면
                    # 그 순간 봉투가 된다 — 우리가 막으려는 바로 그 실패다.
                    report.no_conditions += 1
                    continue
                for prop, spec in (then_block.get("properties") or {}).items():
                    if not isinstance(spec, dict) or prop not in available:
                        if isinstance(spec, dict) and prop not in available:
                            report.unmapped_props.append((directory, prop))
                        continue
                    matched = False
                    for keyword, kind in _THEN_KINDS.items():
                        if keyword in spec:
                            capacity.add_constraint(
                                Constraint(
                                    type_id=type_id, property=prop, kind=kind,
                                    value=(sorted(spec[keyword])
                                           if kind == "enum" else spec[keyword]),
                                    evidence=EVIDENCE_CONDITIONAL,
                                    conditions=conditions,
                                )
                            )
                            matched = True
                    if not matched:
                        report.unsupported_then[
                            ",".join(sorted(k for k in spec if k != "type"))
                        ] += 1
    return capacity, report


def build_conditions(output: Path, *, refresh: bool = False) -> CapacitySet:
    from capacitykb.parsers.cfn import iter_schemas
    from kbcommon.fetch import fetch_cached

    source = SOURCES["cfn-lint"]
    wheel = fetch_cached(source.url, f"cfn-lint-{source.pin}.whl", refresh=refresh)
    zip_path = fetch_cached(
        SOURCES["cfn-schema"].url, "CloudformationSchema.zip", refresh=refresh
    )
    schemas = {
        f"aws::{schema['typeName']}": schema
        for schema in iter_schemas(zip_path)
        if isinstance(schema, dict) and schema.get("typeName")
    }
    capacity, report = parse_conditions(wheel, cfn_schemas=schemas)
    capacity.provenance = [describe_source_set([wheel], source.key)]
    capacity.coverage = [{
        "provider": "aws",
        "types": len({c.type_id for c in capacity.constraints}),
        "type_ids": sorted({c.type_id for c in capacity.constraints}),
        "note": (
            "cfn-lint if/then condition blocks. most of them carry two conditions "
            "(engine × version), which a single condition could not hold, so "
            "condition was widened into a list."
        ),
    }]

    counts = Counter(len(c.conditions) for c in capacity.constraints)
    print(
        f"cfn-lint 조건 블록: 제약 {len(capacity.constraints):,}건 "
        f"({len({c.type_id for c in capacity.constraints})}종 · "
        f"조건 개수 {dict(sorted(counts.items()))})"
    )
    if report.no_conditions:
        print(
            f"  값 조건이 없어 담지 않은 블록 {report.no_conditions}개 — 언제 적용되는지 "
            "못 말하면 무조건으로 담을 수 없습니다(그러면 봉투가 됩니다).",
            file=sys.stderr,
        )
    if report.unsupported_then:
        print(
            f"  담을 칸이 없는 then 모양: {dict(report.unsupported_then)}",
            file=sys.stderr,
        )
    if report.unmapped_types or report.unmapped_props:
        print(
            f"  안 담음: CFN 타입 못 찾음 {len(report.unmapped_types)} · "
            f"속성 이름 못 찾음 {len(report.unmapped_props)}",
            file=sys.stderr,
        )
    capacity.save(output)
    return capacity
