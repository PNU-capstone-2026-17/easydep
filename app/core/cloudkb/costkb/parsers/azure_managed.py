"""Azure Retail Prices API → 관리형 서비스 **과금 축** (재편 계획 ⑥-B).

게이트는 조사 문서(document/archive/managed-pricing-research-2026-07-24.md)다. 요지 셋:

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

수록 범위(1층 보강에서 갱신, 2026-07-24): 여기 azure 11종 + 진입점용 의사
아키타입 loadBalancer. gcp는 `parsers/gcp_managed.py`가 objectStorage·
networkEgress를 담는다(Cyclenerd — 그 파일에 있는 관리형 축의 전부).
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

from app.core.cloudkb.kbcommon.artifact import write_dataset
from app.core.cloudkb.kbcommon.fetch import cache_dir
from app.core.cloudkb.kbcommon.sources import SOURCES

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
    meter_include: tuple[str, ...] = ()
    """meterName 접두 허용 목록. LB처럼 한 productName 안에 다른 제품층(Gateway
    LB·크로스리전 LB)이 섞일 때만 쓴다 — 비면 전부."""
    region_override: str = ""
    """리전 루프 대신 이 리전 하나만 받는다. Load Balancer가 이 경우다 —
    **일반 리전 행이 없고 armRegionName='Global'로만 공표된다**(실측 2026-07-24).
    리전별 값을 지어내지 않고 원본 표기 그대로 Global로 담는다."""


#: 큐레이션 표 — ⑥-B의 6종 + 1층 보강(2026-07-24)의 6종. exclude·meter_include는
#: 전부 실측에서 나온 경계 오염이다.
RULES: tuple[Rule, ...] = (
    Rule("keyValueCache", "Redis Cache"),
    Rule("relationalDatabase", "Azure Database for PostgreSQL",
         exclude=("Cosmos DB",)),
    Rule("nosqlDatabase", "Azure Cosmos DB",
         exclude=("PostgreSQL", "Garnet")),
    Rule("messageQueue", "Service Bus"),
    Rule("secretStore", "Key Vault"),
    Rule("serverlessFunction", "Functions"),
    # --- 1층 보강 ---
    # Storage는 잡탕이다(실측: koreacentral 1,412건·제품 35종 — Managed Disk·
    # Files·Tables·Data Lake 혼재). Blob(블록 블랍)만 objectStorage다.
    # Hierarchical Namespace 변형은 Data Lake Gen2라 뺀다.
    Rule("objectStorage", "Storage",
         include=("Blob Storage", "General Block Blob"),
         exclude=("Hierarchical Namespace",)),
    Rule("containerService", "Azure Kubernetes Service"),
    # serviceName은 옛 이름(Cognitive Search)이고 productName이 신명(AI Search)
    # 이다 — 'Azure AI Search'로 조회하면 0건이 나온다(실측).
    Rule("searchIndex", "Azure Cognitive Search"),
    Rule("eventStream", "Event Hubs"),
    Rule("apiGateway", "API Management"),
    # svcmap 아키타입이 아니라 **의사 아키타입**이다 — 배포 계획의 진입점(ingress)
    # 노드가 소비한다. Standard(우리 NLB 대응)만 담고 Gateway LB·크로스리전
    # (Global 티어) 미터는 다른 제품층이라 거른다.
    Rule("loadBalancer", "Load Balancer",
         meter_include=("Standard ",), region_override="Global"),
    # 인터넷 이그레스(의사 아키타입 networkEgress — gcp와 같은 소비자). Bandwidth
    # 파일에는 In·Inter-Region·Inter-AZ도 섞여 있다(실측 17건) — 인터넷 이그레스가
    # 아닌 축이라 Data Transfer Out만 담는다. 구간(첫 100GB 무료 뒤 계층)은
    # tierMin이 그대로 받는다. 라우팅 선호(MGN vs Internet)는 product가 가른다.
    Rule("networkEgress", "Bandwidth",
         meter_include=("Standard Data Transfer Out",)),
)


def axis_of(row: dict) -> str:
    """과금 축 판정 — 실측한 미터 모양에서 정한 규칙(검수됨).

    단위 칸만 믿지 않는다: PostgreSQL vCore 미터도 단위는 '1 Hour'지만 그 값은
    **vCore 하나당** 시간 단가라 인스턴스-시간이 아니다(예약가 단위 함정과 같은
    계보 — 단위 칸이 뜻을 다 담지 못한다). Event Hubs의 Throughput/Processing/
    Capacity Unit도 같은 함정이다(실측 2026-07-24) — 단위 수가 사이징 결과다.
    """
    meter = row.get("meterName") or ""
    unit = row.get("unitOfMeasure") or ""
    if "vCore" in meter or "RU" in meter:
        return AXIS_CAPACITY
    if any(k in meter for k in ("Throughput Unit", "Processing Unit", "Capacity Unit")):
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
        if rule.meter_include and not any(
            (row.get("meterName") or "").startswith(m) for m in rule.meter_include
        ):
            dropped["meter-not-included"] += 1
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
    # 리전 무관(Global) 공표 규칙 — 리전 루프 밖에서 한 번만 받는다.
    for rule in RULES:
        if not rule.region_override:
            continue
        rows, digest = _fetch(rule.service, rule.region_override, refresh=refresh)
        digests.append(digest)
        found, drops = classify(rule, rows)
        dropped.update(drops)
        for record in found:
            records.append({**record, "region": rule.region_override})
    for region in wanted:
        for rule in RULES:
            if rule.region_override:
                continue
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
            "A **list of billing axes** for Azure managed services — not a single "
            "unit-price cell. Only instance-hour (instanceHour) has a valid hourly "
            "rate; for capacity-rate (capacityRate) the quantity to multiply the "
            "unit price by (vCore, RU, GB) is a sizing result; and usage-based "
            "(usage) needs the traffic before a cost exists — attaching one number "
            "to a usage axis would be filling in what we do not know, so we do not. "
            "No total is produced either. This file is the **azure portion** "
            "(11 archetypes + loadBalancer for the entry point — the source "
            "publishes LB as region-independent Global). gcp has only objectStorage "
            "and networkEgress, in a separate file (gcp-managed-pricing), and AWS is "
            "not included because redistribution is explicitly forbidden.\n"
            "**Source and redistribution status**: derived from values fetched from "
            "the Microsoft Azure Retail Prices API "
            "(https://prices.azure.com/api/retail/prices). Rights to the price data "
            "belong to Microsoft. **We found no wording granting redistribution, and "
            "none forbidding it either** — it is a publicly readable API with no "
            "auth, so we include the derived artifact in the repository for "
            "convenience; that does not mean permission was granted. We will remove "
            "it if the rights holder asks. Values are a snapshot, not an actual bill."
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
                    "`boundary-bleed` is what was dropped because serviceName does "
                    "not respect archetype boundaries (e.g. a Cosmos DB product "
                    "inside the PostgreSQL service); `ambiguous` is what was not "
                    "included because one meter had several values — we do not "
                    "silently pick one."
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
                    "The API has no version, so reproduction is impossible in "
                    "principle. We hash the combined sha256 of each "
                    "(service, region) response, so a change never goes unnoticed."
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
