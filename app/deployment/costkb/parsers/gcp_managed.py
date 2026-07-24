"""Cyclenerd GCP 가격표 → 관리형 서비스 과금 축 (보강 3).

## objectStorage 한 종뿐이다 — 그리고 그것이 조사 결과다

"새 소스보다 이미 받아 둔 소스의 안 쓰는 부분부터"(소스 전수 조사의 교훈). 이미
핀 박은 Cyclenerd pricing.yml(Apache-2.0)의 안 쓰던 `storage` 섹션에 Cloud
Storage 버킷의 저장·검색 단가가 리전 전수로 들어 있다(실측 2026-07-24). 그 외
관리형 축은 이 파일에 없다 — Cloud SQL·Memorystore·Pub/Sub 없음.

**다른 길이 없는 것도 실측이다**: GCP Billing Catalog API는 API 키 인증이
필요해 무인증 재현 빌드 규약에 안 맞고, AWS Price List는 재배포가 **명시적으로
금지**라(소스 표의 `denied`) 산출물 자체를 만들 수 없다. 그래서 관리형 축의
수록은 azure 6종 + gcp objectStorage가 전부이고, 답에는 그 사실이 붙어야 한다.

## 두 축을 함께 담아야 한다

저장(GB/월)은 용량-비례형이고, 검색(nearline·coldline·archive의 GB당 요금)은
사용량형이다. 콜드 클래스의 낮은 저장 단가만 보이면 검색 요금이 숨는다 — 검색
축을 떼면 archive($0.0025/GB/월)가 standard($0.023)보다 싸 보이는 것으로 답이
끝나 버린다. 원본 계산기(gcosts)도 검색량(GB)에 이 단가를 곱한다.

산출물 모양은 azure_managed와 같다(스키마 공유) — 과금 축 목록이지 단가 한 칸이
아니고, 합계는 어디서도 만들지 않는다.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import yaml

from costkb.parsers.azure_managed import AXIS_CAPACITY, AXIS_USAGE, SCHEMA
from kbcommon.artifact import write_dataset
from kbcommon.fetch import describe_source, fetch_cached
from kbcommon.sources import SOURCES

try:  # gcp_pricing과 같은 이유 — C 로더가 3.8MB YAML을 10배 빠르게 읽는다
    _Loader = yaml.CSafeLoader
except AttributeError:  # pragma: no cover
    _Loader = yaml.SafeLoader

_SOURCE_KEY = "cyclenerd-gcp-pricing"

#: 멀티/듀얼 리전 클래스 접미. 우리 리전 축은 단일 리전 코드라 조인이 안 된다 —
#: 담으면 리전 칸에 `asia1`·`asia-multi` 같은 다른 체계가 섞인다. 세기만 한다.
_MULTI_SUFFIXES = ("-dual", "-multi")


def _single_region_classes(section: dict) -> dict[str, dict]:
    return {
        name: spec for name, spec in (section or {}).items()
        if not name.endswith(_MULTI_SUFFIXES)
    }


def build(output: Path, *, mirror: Path, refresh: bool = False) -> dict:
    """미러의 gcp 리전과 조인되는 것만 담는다 (gcp_pricing과 같은 규율)."""
    source = SOURCES[_SOURCE_KEY]
    path = fetch_cached(source.url, f"cyclenerd-gcp-pricing-{source.pin}.yml",
                        refresh=refresh)
    doc = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)

    specs = json.loads(mirror.read_text(encoding="utf-8"))["specs"]
    mirror_regions = {s["region"] for s in specs if s["provider"] == "gcp"}

    storage = (doc.get("storage") or {})
    records: list[dict] = []
    dropped: Counter = Counter()

    def add(classes: dict[str, dict], meter: str, unit: str, axis: str) -> None:
        for cls, spec in sorted(classes.items()):
            for region, cell in sorted((spec.get("cost") or {}).items()):
                price = (cell or {}).get("month")
                if not price:
                    dropped["zero-or-no-price"] += 1
                    continue
                if region not in mirror_regions:
                    dropped["region-not-in-mirror"] += 1
                    continue
                records.append({
                    "archetype": "objectStorage",
                    "region": region,
                    "service": "Cloud Storage",
                    "product": f"Cloud Storage {cls}",
                    "sku": cls,
                    "meter": meter,
                    "unit": unit,
                    "axis": axis,
                    "unitPriceUSD": round(float(price), 6),
                })

    add(_single_region_classes(storage.get("bucket")),
        "storage", "GB/월", AXIS_CAPACITY)
    add(_single_region_classes(storage.get("retrieval")),
        "retrieval", "GB(검색당)", AXIS_USAGE)
    multi_skipped = sum(
        1 for section in ("bucket", "retrieval")
        for name in (storage.get(section) or {})
        if name.endswith(_MULTI_SUFFIXES)
    )

    records.sort(key=lambda r: (r["region"], r["product"], r["meter"]))
    axis_counts = Counter(r["axis"] for r in records)
    payload = {
        "_note": (
            "GCP 관리형 서비스의 **과금 축 목록**입니다 — 수록은 objectStorage"
            "(Cloud Storage) 하나뿐이고, 그것이 소스의 전부입니다(Cyclenerd "
            "pricing.yml에 Cloud SQL·Memorystore·Pub/Sub 없음 — 실측). 저장은 "
            "GB/월 단가(용량-비례형 — 곱할 용량은 사이징 결과)이고, 콜드 "
            "클래스의 검색은 GB당 요금(사용량형 — 검색량을 알아야 함)입니다. "
            "합계는 만들지 않습니다. 값은 스냅샷이며 실제 청구서가 아닙니다. "
            "Apache-2.0(Cyclenerd/google-cloud-pricing-cost-calculator)."
        ),
        "records": records,
        "_coverage": [
            {
                "provider": "gcp",
                "regions": len({r["region"] for r in records}),
                "records": len(records),
                "byAxis": dict(axis_counts),
                "dropped": dict(dropped),
                "note": (
                    f"멀티/듀얼 리전 클래스 {multi_skipped}종은 리전 체계가 달라"
                    "(asia1·asia-multi) 담지 않았습니다 — 단일 리전 코드와 "
                    "조인되지 않습니다. standard에는 검색 요금이 없습니다"
                    "(원본에 없음 — 무료라는 뜻이 아니라 축이 없다는 뜻)."
                ),
            }
        ],
        "_source": [describe_source(path, _SOURCE_KEY)],
    }
    write_dataset(output, payload, SCHEMA)
    print(f"GCP 관리형 과금 축: {len(records)}건 → {output}")
    print(f"  리전 {len({r['region'] for r in records})} · 축 {dict(axis_counts)}"
          f" · 거름 {dict(dropped)}")
    return payload
