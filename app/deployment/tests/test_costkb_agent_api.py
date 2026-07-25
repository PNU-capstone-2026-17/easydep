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


def test_running_total_is_computed_by_the_tool_not_the_model() -> None:
    """**합계를 낼 도구가 없어서 모델이 암산할 수밖에 없었다.**

    지시문은 "구성요소별·합계를 도구로 계산하라"와 "직접 암산하지 마라"를 동시에
    요구했는데, `estimate_monthly_cost`는 구성요소 하나만 계산했다. 모델은 두 번
    부른 뒤 스스로 더했고, 그 합계는 **어떤 도구 출력에도 없는 값**이 됐다
    (실측 RS2: $285.28 — 주장 대조가 근거 없음으로 잡았다).

    사용자가 예산을 대는 바로 그 숫자라 근거가 없으면 안 된다.
    """
    from costkb.agent_api import estimate_monthly_cost

    first = estimate_monthly_cost(0.0468, 1, 730)
    assert "34.16" in first
    # 첫 구성요소에는 누적 줄이 붙지 않는다 — 더할 것이 없으므로.
    assert "Running total" not in first

    second = estimate_monthly_cost(0.344, 1, 730, 34.16)
    assert "251.12" in second
    assert "285.28" in second, "합계가 도구 출력에 없다"
    # **어디서 왔는지 함께 낸다.** 합계만 주면 그것도 근거 없는 숫자로 보인다.
    assert "34.16" in second and "so far" in second


def test_the_prompt_tells_the_model_to_thread_the_running_total() -> None:
    """도구에 칸을 만들어도 지시문이 안 가리키면 안 쓰인다 — 축 표 교훈과 같다."""
    from nim_agent.agent import INSTRUCTIONS

    flat = " ".join(INSTRUCTIONS.split())
    assert "running_total_usd" in flat
    assert "The total is arithmetic too." in flat
