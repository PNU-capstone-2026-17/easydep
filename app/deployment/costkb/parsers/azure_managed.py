"""Azure Retail Prices API → 관리형 서비스 **과금 축** (재편 계획 ⑥-B).

게이트는 조사 문서(document/managed-pricing-research-2026-07-24.md)다. 요지 셋:

1. **관리형 가격은 한 종류가 아니다.** 인스턴스-시간형(Redis — VM과 같은 모양) /
   용량-비례형(PostgreSQL vCore, Cosmos RU — 단가에 곱할 수량이 사이징 결과다) /
   사용량형(Service Bus·Key Vault·Functions — 시간당 단가가 **존재하지 않는다**).
   그래서 이 산출물은 "단가 한 칸"이 아니라 **과금 축 목록**이다.
2. **serviceName은 아키타입 경계를 안 지킨다.** `Azure Database for PostgreSQL`
   안에 `Azure Cosmos DB for PostgreSQL`(분산 DB)이 섞여 있다(실측). VM 가격의
   교훈 그대로 판별자는 productName이고, 아래 큐레이션 표가 그 일을 한다 —
   표는 svcmap과 같은 등급(짐작·검수됨)이다.
3. **사용량형에 숫자 하나를 붙이지 않는다.** 그게 "모르는 것을 채우는" 실패다.
   축·단위·단가를 그대로 싣고, 합계는 어디서도 만들지 않는다.

재배포: azure_pricing.py와 같은 소스(무인증 공개, 허가 문구도 금지 문구도 없음 —
not-stated). 같은 고지를 `_note`에 싣고 NOTICE에 등록한다.

수록 범위(보강 3에서 갱신): 여기(azure 6종)에 더해 **gcp는 objectStorage만**
`parsers/gcp_managed.py`가 담는다(Cyclenerd — 그 파일에 있는 관리형 축의 전부).
**AWS는 미수록 확정** — Price List API가 재배포를 명시적으로 금지한다(소스 표의
`denied`). 단가가 곧 산출물이라 커밋 자체가 성립하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import ssl
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from kbcommon.artifact import write_dataset
from kbcommon.fetch import cache_dir
from kbcommon.sources import SOURCES

_UA = "cloudkb-build (https://github.com/cloud-barista)"

#: 과금 축 세 가지. 조사 문서의 분류 그대로다.
AXIS_INSTANCE = "instanceHour"   # 인스턴스-시간: 시간당 단가가 성립
AXIS_CAPACITY = "capacityRate"   # 용량-비례: 단가 × (vCore·RU·GB) — 수량은 사이징 결과
AXIS_USAGE = "usage"             # 사용량: 오퍼레이션·실행·GB-초 — 트래픽을 알아야 함


@dataclass(frozen=True)
class Rule:
    """아키타입 ↔ Azure 서비스 대응 + productName 판별자 (짐작·검수됨)."""

    archetype: str
    service: str
    include: tuple[str, ...] = ()
    """productName 접두 허용 목록. 비면 전부 — 좁힐 이유가 실측되면 적는다."""
    exclude: tuple[str, ...] = ()
    """productName에 이게 들어 있으면 **다른 아키타입의 것**이다(경계 오염)."""


#: 아키타입 6종의 큐레이션 표. svcmap이 다루는 9종 중 Azure Retail에서 과금
#: 축을 실측한 것들이다. exclude는 전부 실측에서 나온 오염이다.
RULES: tuple[Rule, ...] = (
    Rule("keyValueCache", "Redis Cache"),
    Rule("relationalDatabase", "Azure Database for PostgreSQL",
         exclude=("Cosmos DB",)),
    Rule("nosqlDatabase", "Azure Cosmos DB",
         exclude=("PostgreSQL", "Garnet")),
    Rule("messageQueue", "Service Bus"),
    Rule("secretStore", "Key Vault"),
    Rule("serverlessFunction", "Functions"),
)


def axis_of(row: dict) -> str:
    """과금 축 판정 — 실측한 미터 모양에서 정한 규칙(검수됨).

    단위 칸만 믿지 않는다: PostgreSQL vCore 미터도 단위는 '1 Hour'지만 그 값은
    **vCore 하나당** 시간 단가라 인스턴스-시간이 아니다(예약가 단위 함정과 같은
    계보 — 단위 칸이 뜻을 다 담지 못한다).
    """
    meter = row.get("meterName") or ""
    unit = row.get("unitOfMeasure") or ""
    if "vCore" in meter or "RU" in meter:
        return AXIS_CAPACITY
    if "GB/Month" in unit or "GiB/Month" in unit:
        return AXIS_CAPACITY
    if unit in ("1 Hour", "1/Hour", "100 Hours"):
        return AXIS_INSTANCE
    return AXIS_USAGE


def _fetch(service: str, region: str, *, refresh: bool = False) -> tuple[list[dict], str]:
    """한 (서비스, 리전)의 Consumption 가격 전부. `(레코드, 응답 sha256)`."""
    slug = service.lower().replace(" ", "-")
    path = cache_dir() / f"azure-managed-{slug}-{region}.json"
    if path.exists() and not refresh:
        raw = path.read_bytes()
        return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()

    ctx = ssl.create_default_context()
    url = SOURCES["azure-retail-prices"].url + "&" + urllib.parse.urlencode(
        {"$filter": (
            f"serviceName eq '{service}' and armRegionName eq '{region}' "
            "and priceType eq 'Consumption'"
        )}
    )
    rows: list[dict] = []
    while url:
        request = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(request, context=ctx, timeout=120) as response:
            body = json.loads(response.read().decode("utf-8"))
        rows.extend(body.get("Items") or [])
        url = body.get("NextPageLink")
    raw = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return rows, hashlib.sha256(raw).hexdigest()


def classify(rule: Rule, rows: list[dict]) -> tuple[list[dict], Counter]:
    """한 (아키타입, 리전) 응답 → 레코드. 못 담은 것은 이유별로 센다."""
    out: list[dict] = []
    dropped: Counter = Counter()
    seen: dict[tuple, set[float]] = {}
    for row in rows:
        product = row.get("productName") or ""
        if row.get("type") != "Consumption":
            dropped["not-consumption"] += 1
            continue
        if rule.include and not any(product.startswith(p) for p in rule.include):
            dropped["product-not-included"] += 1
            continue
        if any(x in product for x in rule.exclude):
            dropped["boundary-bleed"] += 1
            continue
        price = row.get("retailPrice")
        if not price:
            dropped["zero-or-no-price"] += 1  # 프리 티어 미터 포함 — 0을 값으로 안 담는다
            continue
        # 계층 요금(처음 N단위 $X, 그 뒤 $Y)은 tierMinimumUnits가 가른다 — 실측에서
        # Key Vault HSM·Service Bus 오퍼레이션이 그랬다. 계층을 키에 안 넣으면
        # "값이 여럿"으로 오판해 버리게 되고, 하나를 고르면 임의 선택이 된다.
        key = (product, row.get("skuName") or "", row.get("meterName") or "",
               row.get("unitOfMeasure") or "", float(row.get("tierMinimumUnits") or 0))
        seen.setdefault(key, set()).add(price)

    for (product, sku, meter, unit, tier_min), prices in sorted(seen.items()):
        if len(prices) != 1:
            # 계층까지 갈랐는데도 값이 여럿이면 무엇이 맞는지 우리가 모른다 —
            # 조용히 하나를 고르지 않는다(azure_pricing과 같은 규율).
            dropped["ambiguous"] += 1
            continue
        record = {
            "archetype": rule.archetype,
            "service": rule.service,
            "product": product,
            "sku": sku,
            "meter": meter,
            "unit": unit,
            "axis": axis_of({"meterName": meter, "unitOfMeasure": unit}),
            "unitPriceUSD": round(next(iter(prices)), 6),
        }
        if tier_min:
            record["tierMin"] = tier_min
        out.append(record)
    return out, dropped


def build(
    output: Path,
    *,
    mirror: Path,
    regions: list[str] | None = None,
    refresh: bool = False,
) -> dict:
    """미러에 있는 Azure 리전을 돌며 관리형 서비스 과금 축을 모은다."""
    specs = json.loads(mirror.read_text(encoding="utf-8"))["specs"]
    wanted = regions or sorted(
        {s["region"] for s in specs if s["provider"] == "azure"}
    )

    records: list[dict] = []
    dropped: Counter = Counter()
    digests: list[str] = []
    for region in wanted:
        for rule in RULES:
            rows, digest = _fetch(rule.service, region, refresh=refresh)
            digests.append(digest)
            found, drops = classify(rule, rows)
            dropped.update(drops)
            for record in found:
                records.append({**record, "region": region})

    records.sort(key=lambda r: (r["region"], r["archetype"], r["product"], r["meter"]))
    axis_counts = Counter(r["axis"] for r in records)
    payload = {
        "_note": (
            "Azure 관리형 서비스의 **과금 축 목록**입니다 — 단가 한 칸이 아닙니다. "
            "인스턴스-시간형(instanceHour)만 시간당 단가가 성립하고, 용량-비례형"
            "(capacityRate)은 단가에 곱할 수량(vCore·RU·GB)이 사이징 결과이며, "
            "사용량형(usage)은 트래픽을 알아야 비용이 나옵니다 — 사용량형에 숫자 "
            "하나를 붙이면 모르는 것을 채우는 것이라 붙이지 않습니다. 합계도 "
            "만들지 않습니다. 이 파일은 **azure 수록분**입니다 — gcp는 "
            "objectStorage만 별도 파일(gcp-managed-pricing)에 있고, AWS는 "
            "재배포가 명시적으로 금지라 미수록입니다.\n"
            "**출처와 재배포 상태**: Microsoft Azure Retail Prices API"
            "(https://prices.azure.com/api/retail/prices)에서 받은 값에서 유도했습니다. "
            "가격 데이터의 권리는 Microsoft에 있습니다. **재배포를 허가하는 문구를 "
            "찾지 못했고 금지하는 문구도 찾지 못했습니다** — 무인증으로 공개된 API라 "
            "편의를 위해 유도 산출물을 저장소에 포함했을 뿐, 허가를 받았다는 뜻이 "
            "아닙니다. 권리자가 요청하면 제거합니다. 값은 스냅샷이며 실제 청구서가 "
            "아닙니다."
        ),
        "records": records,
        "_coverage": [
            {
                "provider": "azure",
                "regions": len(wanted),
                "records": len(records),
                "byAxis": dict(axis_counts),
                "dropped": dict(dropped),
                "note": (
                    "`boundary-bleed`는 serviceName이 아키타입 경계를 안 지켜 거른 "
                    "것(예: PostgreSQL 서비스 안의 Cosmos DB 제품)이고, `ambiguous`는 "
                    "같은 미터에 값이 여럿이라 담지 않은 것입니다 — 조용히 하나를 "
                    "고르지 않습니다."
                ),
            }
        ],
        "_source": [
            {
                "source": "azure-retail-prices",
                "pin_kind": "digest",
                "pin": "(고정 불가)",
                "url": SOURCES["azure-retail-prices"].url,
                "sha256": hashlib.sha256(
                    "".join(sorted(digests)).encode("utf-8")
                ).hexdigest(),
                "bytes": None,
                "fetched_at": None,
                "regions": len(wanted),
                "note": (
                    "버전이 없는 API라 재현이 원리적으로 안 됩니다. (서비스,리전)별 "
                    "응답의 sha256을 합쳐 해시했으므로 바뀐 사실은 놓치지 않습니다."
                ),
            }
        ],
    }
    write_dataset(output, payload, SCHEMA)
    print(f"Azure 관리형 과금 축: {len(records)}건 → {output}")
    print(f"  리전 {len(wanted)} · 축 {dict(axis_counts)} · 거름 {dict(dropped)}")
    return payload


SCHEMA = {
    "type": "object",
    "required": ["records", "_source"],
    "properties": {
        "_note": {"type": "string"},
        "_coverage": {"type": "array"},
        "_source": {"type": "array"},
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["archetype", "region", "product", "meter", "unit",
                             "axis", "unitPriceUSD"],
                "additionalProperties": False,
                "properties": {
                    "archetype": {"type": "string"},
                    "region": {"type": "string"},
                    "service": {"type": "string"},
                    "product": {"type": "string"},
                    "sku": {"type": "string"},
                    "meter": {"type": "string"},
                    "unit": {"type": "string"},
                    "axis": {"enum": ["instanceHour", "capacityRate", "usage"]},
                    "unitPriceUSD": {"type": "number"},
                    "tierMin": {
                        "type": "number",
                        "description": "계층 요금의 시작 단위(이 단위부터 이 단가). 없으면 0부터.",
                    },
                },
            },
        },
    },
}
