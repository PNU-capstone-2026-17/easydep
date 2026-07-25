"""AWS 관리형 과금 축 — **로컬 빌드 전용, 커밋 금지.**

여기서 가장 중요한 테스트는 값이 아니라 **부재**다: AWS Price List는 재배포가
명시적으로 금지라(소스 표의 denied), 유도 산출물이 data/에 나타나는 순간 위반이다.
빌드가 정상 동작하는 것과 저장소가 깨끗한 것을 둘 다 고정한다.

값 테스트는 전부 픽스처다 — 이 산출물은 커밋되지 않아 CI에는 없는 것이 기본이고,
네트워크 없는 테스트가 원칙이다.
"""

from __future__ import annotations

import json

from app.deployment.costkb.dataset import managed_axes, managed_built
from app.deployment.costkb.parsers.aws_managed import (
    AXIS_CAPACITY,
    AXIS_INSTANCE,
    AXIS_USAGE,
    RULES,
    Rule,
    axis_of,
    classify,
)

import pytest


# --- 커밋 금지 ------------------------------------------------------------------

def test_aws_managed_is_never_committed() -> None:
    """**이 테스트가 이 축의 라이선스 방벽이다.** data/에 aws 가격 산출물이
    보이면 재배포 위반 — 지우고, 왜 들어갔는지부터 찾을 것."""
    from app.deployment.kbcommon.artifact import BUNDLED_DIR

    assert not (BUNDLED_DIR / "aws-managed-pricing.json.gz").exists()
    assert not (BUNDLED_DIR / "aws-managed-pricing.json").exists()


# --- 축 분류 --------------------------------------------------------------------

@pytest.mark.parametrize("unit,expected", [
    ("Hrs", AXIS_INSTANCE),                    # RDS 인스턴스·EKS 클러스터
    ("ReadCapacityUnit-Hrs", AXIS_CAPACITY),   # DynamoDB RCU — 단위 수가 사이징
    ("GB-Mo", AXIS_CAPACITY),                  # 저장 용량 × 시간
    ("GB-months", AXIS_CAPACITY),
    ("LCU-Hrs", AXIS_USAGE),                   # 이름은 용량이지만 부하가 소비
    ("Requests", AXIS_USAGE),
    ("Lambda-GB-Second", AXIS_USAGE),
    ("정체불명단위", AXIS_USAGE),               # 모르면 usage — 시간당 단가를 지어내지 않는 방향
])
def test_axis_classification(unit: str, expected: str) -> None:
    assert axis_of(unit) == expected


# --- 큐레이션·판별자 (전부 실측에서 나온 것) --------------------------------------

def _doc(*rows) -> dict:
    """(sku, family, attributes, unit, price) 목록 → 오퍼 문서 모양."""
    products = {}
    ondemand = {}
    for i, (family, attributes, unit, price) in enumerate(rows):
        sku = f"SKU{i}"
        products[sku] = {"productFamily": family, "attributes": attributes}
        ondemand[sku] = {"t": {"priceDimensions": {"d": {
            "unit": unit, "pricePerUnit": {"USD": str(price)}, "beginRange": "0",
        }}}}
    return {"products": products, "terms": {"OnDemand": ondemand}}


def test_nlb_only_from_the_elb_offer() -> None:
    """ELB 파일엔 ALB·GWLB·CLB가 섞여 있다(실측 family 4종) — 우리 진입점은
    NLB뿐이라 Load Balancer-Network만 담는다."""
    rule = next(r for r in RULES if r.archetype == "loadBalancer")
    doc = _doc(
        ("Load Balancer-Network", {"group": "ELB:Balancer"}, "Hrs", 0.0225),
        ("Load Balancer-Application", {"group": "ELB:Balancer"}, "Hrs", 0.0225),
        ("Load Balancer-Gateway", {"group": "ELB:Balancer"}, "Hrs", 0.0125),
    )
    records, dropped = classify(rule, doc)
    assert len(records) == 1
    assert dropped["family-not-included"] == 2


def test_s3_data_transfer_is_boundary_bleed() -> None:
    rule = next(r for r in RULES if r.archetype == "objectStorage")
    doc = _doc(
        ("Storage", {"storageClass": "General Purpose"}, "GB-Mo", 0.025),
        ("Data Transfer", {}, "GB", 0.126),
    )
    records, dropped = classify(rule, doc)
    assert len(records) == 1 and records[0]["axis"] == AXIS_CAPACITY
    assert dropped["boundary-bleed"] == 1


def test_outposts_rows_are_dropped() -> None:
    rule = Rule("relationalDatabase", "AmazonRDS")
    doc = _doc(
        ("Database Instance",
         {"instanceType": "db.m5.large", "locationType": "AWS Outposts"},
         "Hrs", 0.2),
    )
    records, dropped = classify(rule, doc)
    assert not records and dropped["not-a-region"] == 1


def test_engine_and_license_distinguish_rds_prices() -> None:
    """같은 usagetype·인스턴스에 엔진/라이선스별 단가가 갈린다(실측: 판별자 없이
    빌드하면 443건이 ambiguous로 버려졌다). 판별자를 넣으면 전부 살아남는다."""
    rule = Rule("relationalDatabase", "AmazonRDS")
    common = {"instanceType": "db.r5.large", "usagetype": "APN2-InstanceUsage:db.r5.lg",
              "deploymentOption": "Single-AZ"}
    doc = _doc(
        ("Database Instance", {**common, "databaseEngine": "MySQL",
                               "licenseModel": "No license required"}, "Hrs", 0.3),
        ("Database Instance", {**common, "databaseEngine": "Oracle",
                               "licenseModel": "License included"}, "Hrs", 0.5),
        ("Database Instance", {**common, "databaseEngine": "Oracle",
                               "licenseModel": "Bring your own license"}, "Hrs", 0.35),
    )
    records, dropped = classify(rule, doc)
    assert len(records) == 3 and dropped["ambiguous"] == 0
    skus = {r["sku"] for r in records}
    assert "db.r5.large MySQL Single-AZ" in skus            # 무의미 꼬리는 뗀다
    assert "db.r5.large Oracle Single-AZ License included" in skus


def test_truly_ambiguous_is_dropped_not_picked() -> None:
    """판별자를 다 넣어도 값이 여럿이면 담지 않는다 — 조용히 하나를 고르는 것이
    이 저장소가 막아 온 실패다(실측: SQL Server 'NA' 라이선스 구석 194건)."""
    rule = Rule("relationalDatabase", "AmazonRDS")
    attrs = {"instanceType": "db.r5.large", "usagetype": "APN2-InstanceUsage:db.r5.lg"}
    doc = _doc(
        ("Database Instance", dict(attrs), "Hrs", 0.3),
        ("Database Instance", dict(attrs), "Hrs", 0.4),
    )
    records, dropped = classify(rule, doc)
    assert not records and dropped["ambiguous"] == 1


# --- 로컬 전용 로드 경로 ---------------------------------------------------------

_FIXTURE = {
    "_note": "테스트 픽스처",
    "records": [{
        "archetype": "relationalDatabase", "region": "ap-northeast-2",
        "service": "AmazonRDS", "product": "Database Instance",
        "sku": "db.r5.large MySQL Single-AZ",
        "meter": "APN2-InstanceUsage:db.r5.lg", "unit": "Hrs",
        "axis": "instanceHour", "unitPriceUSD": 0.3,
    }],
    "_source": [],
}


def test_locally_built_file_joins_managed_axes(tmp_path) -> None:
    from app.deployment.costkb import dataset

    (tmp_path / "aws-managed-pricing.json").write_text(
        json.dumps(_FIXTURE, ensure_ascii=False), encoding="utf-8"
    )
    dataset.clear_caches()
    try:
        assert managed_built("aws", output_dir=tmp_path)
        axes = managed_axes("relationalDatabase", "ap-northeast-2", output_dir=tmp_path)
        assert axes and axes[0]["axis"] == "instanceHour"
    finally:
        dataset.clear_caches()


def test_unbuilt_aws_is_not_built_even_when_azure_is(tmp_path) -> None:
    """**미빌드(안 봤다)와 수록 없음(봤는데 없다)의 구분** — 합쳐진 맵에서는
    리전만으로 못 가르므로 managed_built가 프로바이더 단위로 가른다. aws는
    커밋되지 않아 이게 기본 상태다."""
    from app.deployment.costkb import dataset

    (tmp_path / "azure-managed-pricing.json").write_text(
        json.dumps({"_note": "f", "records": [], "_source": []}), encoding="utf-8"
    )
    dataset.clear_caches()
    try:
        assert managed_built("azure", output_dir=tmp_path)
        assert not managed_built("aws", output_dir=tmp_path)
    finally:
        dataset.clear_caches()


def test_aws_plan_without_local_build_points_at_the_build_command() -> None:
    """클론 직후의 정직한 답 — '없다'가 아니라 '재배포 금지라 없고, 로컬 빌드로
    열 수 있다'까지 말한다(azure-discount의 명령 안내 선례)."""
    import json as _json
    from pathlib import Path

    from app.deployment.nim_agent.design_tools import _render_plan_text, compose

    design = _json.loads(
        (Path(__file__).resolve().parent.parent / "appkb" / "examples"
         / "order-demo.json").read_text(encoding="utf-8")
    )
    text = _render_plan_text(compose(design))  # conftest가 costkb를 빈 tmp로 고정
    flat = " ".join(text.split()).lower()
    assert "build-aws-managed" in flat
    assert "forbids redistribution" in flat
