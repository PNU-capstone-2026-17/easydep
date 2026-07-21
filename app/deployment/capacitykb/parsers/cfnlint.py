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
                        condition={"property": "Region", "op": "eq", "value": region},
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
            "cfn-lint가 별도 관리하는 리전별 허용값. CloudFormation 레지스트리에는 "
            "표현할 방법이 없어 따로 있는 데이터라 겹치지 않는다. 리전을 모르면 "
            "판정할 수 없으므로 condition으로 담는다."
        ),
    }]

    regions = len({c.condition["value"] for c in capacity.constraints})
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
