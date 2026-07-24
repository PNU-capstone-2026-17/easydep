"""Azure 관리형 서비스 과금 축 (⑥-B).

고정하는 것 셋:
1. **축 분류가 단위 칸만 믿지 않는다** — PostgreSQL vCore 미터도 단위는
   '1 Hour'지만 vCore 하나당 단가라 인스턴스-시간이 아니다.
2. **경계 오염을 거른다** — serviceName이 아키타입 경계를 안 지킨다는 실측
   (PostgreSQL 서비스 안의 Cosmos DB 제품)이 큐레이션 표의 존재 이유다.
3. **계층 요금을 임의로 고르지 않는다** — tierMinimumUnits가 다른 값들은
   "값이 여럿"이 아니라 계층이고, 계층 그대로 담는다.
"""

from __future__ import annotations

import pytest

from kbcommon.artifact import DEFAULT_OUTPUT

from costkb.dataset import managed_axes
from costkb.parsers.azure_managed import (
    AXIS_CAPACITY,
    AXIS_INSTANCE,
    AXIS_USAGE,
    RULES,
    Rule,
    axis_of,
    classify,
)


# --- 축 분류 --------------------------------------------------------------------

@pytest.mark.parametrize("meter,unit,expected", [
    ("C1 Cache Instance", "1 Hour", AXIS_INSTANCE),
    ("vCore", "1 Hour", AXIS_CAPACITY),          # 단위 칸만 믿으면 틀리는 자리
    ("100 RU/s", "1/Hour", AXIS_CAPACITY),
    ("Data Stored", "1 GB/Month", AXIS_CAPACITY),
    ("Operations", "10K", AXIS_USAGE),
    ("Standard Execution Time", "1 GB Second", AXIS_USAGE),
])
def test_axis_classification(meter: str, unit: str, expected: str) -> None:
    assert axis_of({"meterName": meter, "unitOfMeasure": unit}) == expected


# --- 큐레이션 표 ----------------------------------------------------------------

def _row(**overrides) -> dict:
    base = {
        "type": "Consumption",
        "productName": "Azure Redis Cache Standard",
        "skuName": "C1",
        "meterName": "C1 Cache Instance",
        "unitOfMeasure": "1 Hour",
        "retailPrice": 0.05,
    }
    base.update(overrides)
    return base


def test_boundary_bleed_is_filtered_and_counted() -> None:
    """PostgreSQL 서비스 응답 안의 Cosmos DB 제품 — 그대로 담으면 관계형 DB의
    과금 축에 분산 DB 값이 섞인다(실측 706건)."""
    rule = next(r for r in RULES if r.archetype == "relationalDatabase")
    rows = [
        _row(productName="Azure Database for PostgreSQL Flexible Server",
             meterName="vCore"),
        _row(productName="Azure Cosmos DB for PostgreSQL Compute", meterName="vCore"),
    ]
    records, dropped = classify(rule, rows)
    assert len(records) == 1
    assert dropped["boundary-bleed"] == 1
    assert all("Cosmos" not in r["product"] for r in records)


def test_tiered_pricing_is_kept_per_tier_not_dropped() -> None:
    """실측: Service Bus 오퍼레이션이 (100단위 $0.5 / 2500단위 $0.2)처럼 계층으로
    온다. 계층을 무시하면 '값이 여럿'으로 오판하고, 하나를 고르면 임의 선택이다."""
    rule = Rule("messageQueue", "Service Bus")
    rows = [
        _row(productName="Service Bus", meterName="Standard Messaging Operations",
             unitOfMeasure="1M", retailPrice=0.5, tierMinimumUnits=100.0),
        _row(productName="Service Bus", meterName="Standard Messaging Operations",
             unitOfMeasure="1M", retailPrice=0.2, tierMinimumUnits=2500.0),
    ]
    records, dropped = classify(rule, rows)
    assert len(records) == 2
    assert dropped["ambiguous"] == 0
    assert sorted(r["tierMin"] for r in records) == [100.0, 2500.0]


def test_zero_price_and_non_consumption_are_dropped_with_reasons() -> None:
    rule = Rule("keyValueCache", "Redis Cache")
    rows = [
        _row(retailPrice=0),
        _row(type="Reservation"),
        _row(),
    ]
    records, dropped = classify(rule, rows)
    assert len(records) == 1
    assert dropped["zero-or-no-price"] == 1
    assert dropped["not-consumption"] == 1


# --- 커밋된 산출물 로더 ---------------------------------------------------------

def test_committed_dataset_answers_for_redis_in_seoul() -> None:
    axes = managed_axes("keyValueCache", "koreasouth", output_dir=DEFAULT_OUTPUT)
    assert axes, "커밋된 azure-managed-pricing에 Redis·koreasouth이 있어야 한다"
    assert all(r["axis"] == "instanceHour" for r in axes), (
        "Redis는 실측에서 전부 인스턴스-시간형이었다 — 아니면 분류가 바뀐 것"
    )


def test_unknown_archetype_is_empty_not_none() -> None:
    """빌드됐는데 없음([])과 미빌드(None)는 뜻이 반대다 — 합치면 '안 봤다'가
    '봤는데 없다'로 읽힌다."""
    assert managed_axes("objectStorage", "koreasouth", output_dir=DEFAULT_OUTPUT) == []


def test_unbuilt_dir_is_none(tmp_path) -> None:
    assert managed_axes("keyValueCache", "koreasouth", output_dir=tmp_path) is None


def test_app_prefix_is_accepted() -> None:
    assert managed_axes("app::keyValueCache", "koreasouth", output_dir=DEFAULT_OUTPUT)


# --- 배포 계획 소비자 -----------------------------------------------------------

def test_azure_plan_managed_nodes_carry_billing_axes(monkeypatch) -> None:
    """관리형 노드의 "값 없음"이 azure에서는 **과금 축 목록**으로 바뀐다 —
    합계는 여전히 없고, 그 이유(수량을 모름)가 문장에 실린다.

    conftest가 costkb를 빈 디렉터리(번들 모드)로 고정하므로, 커밋된 관리형
    산출물을 보려면 이 테스트만 기본 위치로 되돌린다.
    """
    import json
    from pathlib import Path

    from costkb import dataset
    from nim_agent.design_tools import _render_plan_text, compose

    monkeypatch.setattr(dataset, "DEFAULT_OUTPUT_DIR", DEFAULT_OUTPUT)
    dataset.clear_caches()

    design = json.loads(
        (Path(__file__).resolve().parent.parent / "appkb" / "examples"
         / "order-demo.json").read_text(encoding="utf-8")
    )
    design["requirements"]["provider"] = "azure"
    design["requirements"]["region"] = "koreasouth"
    plan = compose(design)
    db_notes = [n.text for n in plan.node("order-api-db").notes]
    assert any("과금 축" in t for t in db_notes)
    text = _render_plan_text(plan)
    assert "과금 축 목록" in text  # 전역 고지가 "가격 없음"에서 바뀐다
    assert "합계를 내지 않습니다" in text


def test_aws_plan_keeps_the_no_price_notice() -> None:
    """azure만 수록이다 — aws 계획에서 과금 축이 나오면 그게 거짓이다."""
    import json
    from pathlib import Path

    from nim_agent.design_tools import _render_plan_text, compose

    design = json.loads(
        (Path(__file__).resolve().parent.parent / "appkb" / "examples"
         / "order-demo.json").read_text(encoding="utf-8")
    )
    plan = compose(design)
    text = _render_plan_text(plan)
    assert "과금 축" not in text
    assert "관리형 서비스 가격은 이 데이터셋에 없어" in text
