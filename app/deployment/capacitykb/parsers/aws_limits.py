"""AWS 실제 한도: Price List × botocore 교차 검증.

**왜 두 소스인가.** 감사에서 나온 "확신에 찬 오답"은 전부 **단일 미검증 소스**에서
나왔다. 여기서는 AWS가 서로 다른 두 경로로 내놓은 같은 사실을 대조해, **둘이 같은
값을 말했을 때만** 담는다. 어긋나면 담지 않고 미결로 보고한다.

- **Price List 벌크 API** — `productFamily=Storage` 항목의 `maxVolumeSize` ·
  `maxIopsvolume` · `maxThroughputvolume`을 `volumeApiName`별로 준다. 구조화돼 있지만
  **최댓값만** 있고 값이 반쯤 산문이다(`"16 TiB"`).
- **botocore 서비스 모델** — `CreateVolumeRequest.Size`의 설명문이 규칙적인 목록이라
  **최솟값까지** 담고 있다(`gp2: 1 - 16,384 GiB`). shape 자체에는 min/max가 없어서
  (`Integer` = `{"type":"integer"}`) 산문이 유일한 출처다.

**여기가 `condition`이 처음 쓰이는 곳이다.** 볼륨 크기 한도는 종류마다 달라서
min/max 한 쌍으로는 못 담는다 — 뭉개면 `standard` 볼륨 5,000 GiB처럼 **불가능한 값이
통과하는** 봉투가 된다.

**핀.** Price List는 버전 URL이 있어 그걸 쓴다(`current`는 움직인다). 다만 값이 계속
정확하리란 계약상 보장은 없으므로, 오늘 맞다는 것은 경험적 사실이다 — sha256을 함께
남겨 다음 빌드와 대조한다.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from app.deployment.capacitykb.model import CapacitySet, Constraint
from app.deployment.kbcommon.fetch import describe_source_set, fetch_cached
from app.deployment.kbcommon.sources import SOURCES

EVIDENCE = "aws-cross-checked"
VOLUME = "aws::AWS::EC2::Volume"

#: `"16 TiB"` · `"1 TiB"` 같은 반쯤 산문인 값.
_SIZE = re.compile(r"^\s*([\d.,]+)\s*(TiB|GiB)\s*$", re.I)
#: `"80000"` — 순수 숫자만 받는다. `"250 - based on 1 MiB I/O size"`는 안 받는다.
_NUMBER = re.compile(r"^\s*([\d,]+)\s*$")
#: 설명문의 `gp2 : 1 - 16,384 GiB` / `st1 and sc1 : 125 - 16,384 GiB`
_RANGE = re.compile(
    r"\b([a-z][a-z0-9]*(?:\s+and\s+[a-z][a-z0-9]*)*)\s*:\s*"
    r"([\d,]+)\s*(?:\([^)]*\)\s*)?-\s*([\d,]+)\s*(GiB|IOPS|MiB/s)?",
    re.I,
)

_UNITS = {"tib": 1024, "gib": 1}


def _to_gib(text: str) -> int | None:
    match = _SIZE.match(text or "")
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    return int(number * _UNITS[match.group(2).lower()])


def _to_int(text: str) -> int | None:
    match = _NUMBER.match(str(text or ""))
    return int(match.group(1).replace(",", "")) if match else None


def read_price_list(payload: dict) -> dict[str, dict]:
    """`volumeApiName` → 속성. 볼륨 종류별 최댓값이 여기 있다."""
    out: dict[str, dict] = {}
    for product in (payload.get("products") or {}).values():
        if product.get("productFamily") != "Storage":
            continue
        attrs = product.get("attributes") or {}
        name = attrs.get("volumeApiName")
        if name:
            out[name] = attrs
    return out


def read_botocore(model: dict) -> dict[str, dict[str, tuple[int, int]]]:
    """설명문에서 `{필드: {볼륨종류: (최소, 최대)}}`를 뽑는다.

    `st1 and sc1 : 125 - 16,384` 처럼 한 줄이 여러 종류를 가리키면 각각으로 편다.
    `gp3: 3,000 ( default ) - 80,000` 의 삽입구는 정규식에서 건너뛴다.
    """
    members = (model.get("shapes", {}).get("CreateVolumeRequest", {}).get("members") or {})
    out: dict[str, dict[str, tuple[int, int]]] = {}
    for field in ("Size", "Iops", "Throughput"):
        doc = (members.get(field) or {}).get("documentation") or ""
        plain = re.sub(r"<[^>]+>", " ", doc)
        found: dict[str, tuple[int, int]] = {}
        for match in _RANGE.finditer(plain):
            low = int(match.group(2).replace(",", ""))
            high = int(match.group(3).replace(",", ""))
            for name in re.split(r"\s+and\s+", match.group(1).strip(), flags=re.I):
                name = name.strip().lower()
                # "valid ranges" 같은 머리말이 걸리지 않게 실재 종류만 받는다
                if name in _KNOWN_TYPES:
                    found[name] = (low, high)
        if found:
            out[field] = found
    return out


#: 실재하는 볼륨 종류. 설명문 파싱이 머리말을 종류로 오인하지 않게 하는 울타리다.
_KNOWN_TYPES = {"gp2", "gp3", "io1", "io2", "st1", "sc1", "standard"}


class Report:
    def __init__(self) -> None:
        self.agreed = 0
        self.disagreed: list[tuple[str, str, object, object]] = []
        self.one_sided: list[tuple[str, str, str]] = []


def cross_check(price: dict[str, dict], boto: dict) -> tuple[CapacitySet, Report]:
    """두 소스가 **같은 값을 말한 것만** 레코드로 만든다."""
    capacity = CapacitySet()
    report = Report()

    def add(prop: str, kind: str, value: int, vol: str, unit: str) -> None:
        capacity.add_constraint(
            Constraint(
                type_id=VOLUME, property=prop, kind=kind, value=value,
                evidence=EVIDENCE, unit=unit,
                conditions=({"property": "VolumeType", "op": "eq", "value": vol},),
            )
        )

    # --- 크기: Price List의 maxVolumeSize vs botocore 설명문의 상한
    for vol, attrs in sorted(price.items()):
        pl_max = _to_gib(attrs.get("maxVolumeSize", ""))
        bc = (boto.get("Size") or {}).get(vol)
        if pl_max is None and bc is None:
            continue
        if pl_max is None or bc is None:
            report.one_sided.append(("Size", vol, "Price List만" if bc is None else "botocore만"))
            continue
        low, high = bc
        if pl_max != high:
            report.disagreed.append(("Size.max", vol, pl_max, high))
            continue
        report.agreed += 1
        add("Size", "max", high, vol, "GiB")
        # 최솟값은 botocore에만 있다. 상한이 일치했으므로 같은 문장을 신뢰한다.
        add("Size", "min", low, vol, "GiB")

    # --- IOPS: 숫자로 떨어지는 것만 (sc1/st1/standard는 산문이라 제외)
    for vol, attrs in sorted(price.items()):
        pl_max = _to_int(attrs.get("maxIopsvolume", ""))
        bc = (boto.get("Iops") or {}).get(vol)
        if pl_max is None or bc is None:
            if pl_max is not None or bc is not None:
                report.one_sided.append(("Iops", vol, "Price List만" if bc is None else "botocore만"))
            continue
        low, high = bc
        if pl_max != high:
            report.disagreed.append(("Iops.max", vol, pl_max, high))
            continue
        report.agreed += 1
        add("Iops", "max", high, vol, "IOPS")
        add("Iops", "min", low, vol, "IOPS")

    return capacity, report


def build(output: Path, *, refresh: bool = False) -> CapacitySet:
    price_src = SOURCES["aws-price-list"]
    boto_src = SOURCES["botocore"]
    price_path = fetch_cached(
        price_src.url, f"aws-pricelist-{price_src.pin}.json", refresh=refresh
    )
    boto_path = fetch_cached(
        boto_src.url, f"botocore-ec2-{boto_src.pin}.json", refresh=refresh
    )

    price = read_price_list(json.loads(price_path.read_text(encoding="utf-8")))
    boto = read_botocore(json.loads(boto_path.read_text(encoding="utf-8")))
    capacity, report = cross_check(price, boto)

    capacity.provenance = [
        describe_source_set([price_path], price_src.key),
        describe_source_set([boto_path], boto_src.key),
    ]
    capacity.coverage = [{
        "provider": "aws",
        "types": 1,
        "type_ids": [VOLUME],
        "note": (
            "EBS volume limits by volume type only. a value is included only when "
            "both official sources (Price List · botocore) state the same thing — "
            "where they disagree it is dropped and reported."
        ),
    }]

    print(
        f"aws-limits: 제약 {len(capacity.constraints)}건 "
        f"(두 소스 일치 {report.agreed}쌍)"
    )
    if report.disagreed:
        print("  ⚠ 두 소스가 다른 값을 말해 **담지 않은 것**:", file=sys.stderr)
        for what, vol, a, b in report.disagreed:
            print(f"    - {what} {vol}: Price List={a} vs botocore={b}", file=sys.stderr)
    if report.one_sided:
        print(
            f"  한쪽에만 있어 담지 않은 것 {len(report.one_sided)}건: "
            + ", ".join(f"{w}/{v}({why})" for w, v, why in report.one_sided[:6]),
            file=sys.stderr,
        )
    capacity.save(output)
    return capacity
