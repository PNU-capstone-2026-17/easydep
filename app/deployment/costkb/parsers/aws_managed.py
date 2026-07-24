"""AWS Price List → 관리형 서비스 과금 축. **로컬 빌드 전용 — 커밋 금지.**

## 왜 커밋하지 않는가

접근은 무인증으로 열려 있다(리전 오퍼 파일, 버전 URL이라 핀도 된다 — 버전이
아예 없는 Azure Retail보다 낫다). 막힌 것은 **재배포**다: AWS 약관이 가격
데이터의 재배포를 명시적으로 금지한다(소스 표의 `denied` — "문구가 없는" Azure·
IBM과 달리 "안 된다고 적혀 있는" 경우). 과금 축은 단가 그 자체라 EBS 한도식
우회(값은 안 남김)도 성립하지 않는다.

그래서 이 산출물은 `output/`에만 존재한다:

    python -m costkb build-aws-managed          # 각자 로컬에서
    data/aws-managed-pricing.json.gz            # 존재하면 안 된다 — 테스트가 막는다

클론 직후에는 aws 관리형 축이 비어 있는 것이 **정상**이고, 도구가 빌드 명령을
안내한다(azure-discount 선례).

## 구조 실측 (2026-07-24, ap-northeast-2)

오퍼 파일이 서비스별로 갈라져 있어 Azure serviceName 같은 잡탕이 없다. 예외 둘만
큐레이션한다: AWSELB는 ALB·GWLB·CLB가 한 파일이라 **Load Balancer-Network**
(우리 NLB)만 취하고, AmazonS3는 Data Transfer(이그레스) family가 섞여 있어 뺀다.
OnDemand 텀만 읽고(예약은 별개 축), Outposts·Local Zone 행은 locationType으로
거른다.

## 축 판정 — 단위 문자열의 함정

    Hrs (맨 시간)               instanceHour   RDS 인스턴스·EKS 클러스터
    *CapacityUnit*-Hrs          capacityRate   DynamoDB RCU/WCU — 단위 수가 사이징
    GB-Mo · *-months            capacityRate   저장 용량 × 시간
    LCU-Hrs · Requests · GB …   usage          부하가 소비하는 단위 — 트래픽 의존

DynamoDB의 'ReadCapacityUnit-Hrs'는 단위가 시간이지만 **용량 단위 수에 비례**한다
— Azure의 vCore·Throughput Unit과 같은 함정이다. LCU는 반대로 이름이 용량이지만
**부하가 소비**하므로 사용량이다(AWS가 그렇게 과금한다고 문서화한 구조 그대로).
모르는 단위는 usage로 둔다 — 시간당 단가를 지어내는 방향보다 안전하다.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from costkb.parsers.azure_managed import (
    AXIS_CAPACITY,
    AXIS_INSTANCE,
    AXIS_USAGE,
    SCHEMA,
)
from kbcommon.artifact import write_dataset
from kbcommon.fetch import cache_dir

_BASE = "https://pricing.us-east-1.amazonaws.com"
_UA = "cloudkb-build (https://github.com/cloud-barista)"


@dataclass(frozen=True)
class Rule:
    """아키타입 ↔ 오퍼 코드 (+ productFamily 큐레이션, 실측 근거)."""

    archetype: str
    offer: str
    family_include: tuple[str, ...] = ()
    family_exclude: tuple[str, ...] = ()


RULES: tuple[Rule, ...] = (
    Rule("relationalDatabase", "AmazonRDS"),
    Rule("keyValueCache", "AmazonElastiCache"),
    Rule("nosqlDatabase", "AmazonDynamoDB"),
    Rule("messageQueue", "AWSQueueService"),
    Rule("secretStore", "AWSSecretsManager"),
    Rule("serverlessFunction", "AWSLambda"),
    # Data Transfer(이그레스)가 같은 파일에 섞여 있다(실측 4건) — 객체 스토리지
    # 축이 아니라 뺀다.
    Rule("objectStorage", "AmazonS3", family_exclude=("Data Transfer",)),
    Rule("containerService", "AmazonEKS"),
    Rule("eventStream", "AmazonKinesis"),
    Rule("apiGateway", "AmazonApiGateway"),
    Rule("searchIndex", "AmazonES"),
    # ALB·GWLB·CLB가 한 파일이다(실측 family 4종) — 우리 진입점은 NLB뿐.
    Rule("loadBalancer", "AWSELB", family_include=("Load Balancer-Network",)),
)


def axis_of(unit: str) -> str:
    """단위 문자열 → 과금 축 (검수된 규칙 — 모듈 docstring의 표)."""
    u = (unit or "").strip()
    if u in ("Hrs", "Hours", "Hour"):
        return AXIS_INSTANCE
    low = u.lower()
    if "capacityunit" in low:
        return AXIS_CAPACITY
    if "lcu" in low:
        return AXIS_USAGE  # 이름은 용량이지만 부하가 소비한다
    if "-mo" in low or "month" in low or "gb-hours" in low or "shardhour" in low:
        return AXIS_CAPACITY
    return AXIS_USAGE


def _fetch(offer: str, region: str, *, refresh: bool = False) -> Path:
    """리전 오퍼 파일 하나(캐시됨). 버전 URL이 아니라 current를 받되, 응답 안의
    version 칸을 산출물에 남긴다 — 재현은 그 버전 URL로 할 수 있다."""
    dest = cache_dir() / f"aws-managed-{offer}-{region}.json"
    if dest.exists() and not refresh:
        return dest
    url = f"{_BASE}/offers/v1.0/aws/{offer}/current/{region}/index.json"
    ctx = ssl.create_default_context()
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(request, context=ctx, timeout=300) as response:
        dest.write_bytes(response.read())
    return dest


def classify(rule: Rule, doc: dict) -> tuple[list[dict], Counter]:
    """오퍼 문서 하나 → 레코드. 못 담은 것은 이유별로 센다 (azure와 같은 규율)."""
    out: list[dict] = []
    dropped: Counter = Counter()
    products = doc.get("products") or {}
    ondemand = (doc.get("terms") or {}).get("OnDemand") or {}
    seen: dict[tuple, set[float]] = {}
    meta: dict[tuple, dict] = {}

    for sku, terms in ondemand.items():
        product = products.get(sku) or {}
        attributes = product.get("attributes") or {}
        family = product.get("productFamily") or ""
        location_type = attributes.get("locationType") or "AWS Region"
        if location_type != "AWS Region":
            dropped["not-a-region"] += 1  # Outposts·Local Zone
            continue
        if rule.family_include and family not in rule.family_include:
            dropped["family-not-included"] += 1
            continue
        if family in rule.family_exclude:
            dropped["boundary-bleed"] += 1
            continue
        # 판별자를 sku 칸에 전부 싣는다 — 같은 usagetype에 단가가 갈리는 축이
        # 실측으로 넷 나왔다: RDS 엔진·배포 옵션·라이선스 모델(Oracle BYOL vs
        # License included)·SQL Server 에디션, ElastiCache 캐시 엔진(Redis/
        # Memcached/Valkey), S3 그룹 설명(같은 API 티어의 다른 오퍼레이션).
        # 빼면 정당한 값들이 ambiguous로 버려진다(첫 빌드에서 443건).
        license_model = attributes.get("licenseModel")
        if license_model == "No license required":
            license_model = None  # 대부분의 행에 붙는 무의미 꼬리 — 판별력이 없다
        base = (attributes.get("instanceType")
                or attributes.get("cacheNodeType")
                or attributes.get("storageClass")
                or attributes.get("group") or "")
        parts = [
            base,
            attributes.get("databaseEngine"),
            attributes.get("databaseEdition"),
            attributes.get("deploymentOption"),
            license_model,
            attributes.get("cacheEngine"),
        ]
        if attributes.get("group") and attributes.get("groupDescription"):
            parts.append(attributes["groupDescription"])
        detail = " ".join(x for x in parts if x)
        for term in terms.values():
            for dim in (term.get("priceDimensions") or {}).values():
                price_text = (dim.get("pricePerUnit") or {}).get("USD")
                price = float(price_text) if price_text else 0.0
                if not price:
                    dropped["zero-or-no-price"] += 1
                    continue
                begin = dim.get("beginRange") or "0"
                tier_min = 0.0 if begin in ("0", "Inf") else float(begin)
                key = (family or attributes.get("usagetype") or "",
                       detail, attributes.get("usagetype") or "",
                       dim.get("unit") or "", tier_min)
                seen.setdefault(key, set()).add(price)
                meta[key] = attributes

    for (family, detail, usagetype, unit, tier_min), prices in sorted(seen.items()):
        if len(prices) != 1:
            dropped["ambiguous"] += 1  # 조용히 하나를 고르지 않는다
            continue
        record = {
            "archetype": rule.archetype,
            "service": rule.offer,
            "product": family,
            "sku": detail,
            "meter": usagetype,
            "unit": unit,
            "axis": axis_of(unit),
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
    specs = json.loads(mirror.read_text(encoding="utf-8"))["specs"]
    wanted = regions or sorted({s["region"] for s in specs if s["provider"] == "aws"})

    records: list[dict] = []
    dropped: Counter = Counter()
    versions: dict[str, str] = {}
    missing: list[str] = []
    for region in wanted:
        for rule in RULES:
            try:
                path = _fetch(rule.offer, region, refresh=refresh)
            except urllib.error.HTTPError as exc:
                # 이 서비스가 이 리전에 없다 — 지어내지 않고 센다.
                missing.append(f"{rule.offer}/{region} ({exc.code})")
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))
            versions[rule.offer] = doc.get("version") or "?"
            found, drops = classify(rule, doc)
            dropped.update(drops)
            for record in found:
                records.append({**record, "region": region})

    records.sort(key=lambda r: (r["region"], r["archetype"], r["product"],
                                r["sku"], r["meter"]))
    axis_counts = Counter(r["axis"] for r in records)
    payload = {
        "_note": (
            "AWS 관리형 서비스의 **과금 축 목록**입니다 — 단가 한 칸이 아닙니다"
            "(인스턴스-시간형만 시간당 단가가 성립하고, 용량-비례형은 곱할 수량이 "
            "사이징 결과이며, 사용량형은 트래픽을 알아야 합니다). 합계는 만들지 "
            "않습니다.\n"
            "**재배포 금지 소스입니다.** AWS Price List의 가격 데이터는 재배포가 "
            "명시적으로 금지되어, 이 파일은 로컬 빌드 산출물로만 존재해야 합니다 — "
            "`data/`에 포장하거나 저장소에 커밋하지 마세요(테스트가 막습니다). "
            "값은 받은 시점의 스냅샷이며 실제 청구서가 아닙니다."
        ),
        "records": records,
        "_coverage": [
            {
                "provider": "aws",
                "regions": len(wanted),
                "records": len(records),
                "byAxis": dict(axis_counts),
                "dropped": dict(dropped),
                "note": (
                    "OnDemand 텀만 담습니다(예약·Savings Plan은 별개 축). "
                    "`family-not-included`는 ELB 파일에서 NLB 외(ALB·GWLB·CLB)를, "
                    "`boundary-bleed`는 S3 파일의 Data Transfer(이그레스)를 거른 "
                    "것입니다. 리전에 없는 서비스: "
                    + (", ".join(missing[:8]) or "없음")
                    + (f" 외 {len(missing) - 8}건" if len(missing) > 8 else "")
                ),
            }
        ],
        "_source": [
            {
                "source": "aws-price-list",
                "pin_kind": "tag",
                "pin": "current(오퍼별 version은 아래)",
                "url": f"{_BASE}/offers/v1.0/aws/",
                "sha256": "0" * 64,
                "bytes": None,
                "fetched_at": None,
                "note": (
                    "오퍼별 응답의 version 칸: "
                    + " · ".join(f"{k}={v}" for k, v in sorted(versions.items()))
                    + " — 재현은 이 버전이 박힌 URL로 할 수 있습니다."
                ),
            }
        ],
    }
    write_dataset(output, payload, SCHEMA)
    print(f"AWS 관리형 과금 축: {len(records)}건 → {output}")
    print(f"  리전 {len(wanted)} · 축 {dict(axis_counts)} · 거름 {dict(dropped)}")
    if missing:
        print(f"  리전에 없는 서비스 {len(missing)}건 (coverage에 기록)")
    return payload
