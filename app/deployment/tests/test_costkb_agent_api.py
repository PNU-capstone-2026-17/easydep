"""costkb agent_api 출력 계약 테스트.

이 모듈은 **전용 테스트가 0건**이었다. 에이전트가 읽는 문자열이라 형식이 곧 계약이고,
특히 "추천 출력에 월 비용을 넣지 않는다"는 규칙은 실측으로 얻은 것이라 고정해 둔다.
"""

from __future__ import annotations

import pytest

from costkb.agent_api import (
    HOURS_PER_MONTH,
    coverage_text,
    estimate_monthly_cost,
    recommend_specs,
)


# --- recommend_specs: 시간당 단가까지만 ---


def test_recommend_lists_candidates_with_hourly_price() -> None:
    out = recommend_specs(vcpu_min=2, mem_min_gib=4, provider="aws", region="us-east-1")
    flat = " ".join(out.split())
    assert "Recommended candidates (on-demand list price, hourly rate):" in flat
    assert "/h" in out
    assert "vCPU" in out
    assert "GiB" in out


def test_recommend_does_not_include_monthly_cost() -> None:
    """월 비용을 여기서 주면 모델이 estimate 도구를 건너뛰고 암산한다 (실측 5/5 생략).

    도구 분해상으로도 추천은 카탈로그+단가, 월 계산은 전용 도구의 몫이다.
    """
    out = recommend_specs(limit=5)
    flat = " ".join(out.split())
    # 후보 목록 안에 월 수치가 없어야 (꼬리말의 "Compute monthly cost with ..." 앞부분)
    assert "/month" not in flat.split("Compute monthly cost with")[0]
    assert "≈" not in out


def test_recommend_points_to_the_cost_tool() -> None:
    """다음에 뭘 해야 하는지 도구 출력 자체가 알려준다 (docstring보다 가까운 압력)."""
    out = recommend_specs(limit=2)
    flat = " ".join(out.split())
    assert "estimate_monthly_cost" in flat
    assert "Do not multiply it out yourself." in flat


def test_recommend_respects_filters() -> None:
    out = recommend_specs(provider="gcp", limit=3)
    assert "GCP" in out
    assert "AWS" not in out


def test_recommend_out_of_range_explains_coverage() -> None:
    """경계를 넘으면 침묵하지 않고 커버리지를 알려준다."""
    out = recommend_specs(vcpu_min=1024)
    flat = " ".join(out.split())
    assert "No spec in the dataset meets these conditions." in flat
    assert "coverage" in flat or "ap-northeast-2" in flat


# --- estimate_monthly_cost: 월 계산 + 한계 고지 ---


def test_estimate_computes_total_and_per_node() -> None:
    out = estimate_monthly_cost(0.1, count=2)
    flat = " ".join(out.split())
    assert "$146.00" in flat  # 0.1 × 730 × 2
    assert "$73.00" in flat  # 대당
    assert "× 2 nodes" in flat


def test_estimate_default_hours_is_always_on() -> None:
    assert HOURS_PER_MONTH == 730
    assert "at 730h/month" in " ".join(estimate_monthly_cost(0.1).split())


def test_estimate_honours_partial_uptime() -> None:
    out = estimate_monthly_cost(0.1, count=1, hours_per_month=100)
    flat = " ".join(out.split())
    assert "$10.00" in flat
    assert "at 100h/month" in flat


def test_estimate_carries_the_disclaimer() -> None:
    """이 고지가 답변에 실리는 게 이 도구를 반드시 부르게 하는 이유 중 하나다."""
    out = estimate_monthly_cost(0.1)
    flat = " ".join(out.split())
    assert "on-demand list price" in flat
    assert "not included" in flat


# --- coverage_text ---


def test_coverage_text_lists_all_groups() -> None:
    text = coverage_text()
    for region in ("us-east-1", "ap-northeast-2", "eastus", "us-central1"):
        assert region in text
    assert "vCPU" in text


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_coverage_text_covers_each_provider(provider: str) -> None:
    assert provider in coverage_text()
