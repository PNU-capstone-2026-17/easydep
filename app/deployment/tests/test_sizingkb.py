"""사이징 규칙.

**이 KB의 위험은 하나다 — 모르는 것을 0으로 채우는 것.** `networkinfo.yaml`은 손
큐레이션이라 채움이 고르지 않고, **AWS의 예약 IP 칸이 비어 있는데 AWS는 실제로 5개를
예약한다.** 공백을 0으로 읽으면 251대 자리에 256대라고 답하게 된다.
"""

from __future__ import annotations

import json

import pytest

from sizingkb import dataset
from sizingkb.agent_api import (
    container_presets,
    reference_points,
    requirements,
    subnet_capacity,
)
from sizingkb.model import Rule, usable_ips

RULES = {
    "rules": [
        {
            "id": "t::reserved/azure", "kind": "reserved_ips", "scope": "azure",
            "metric": "reservedIps", "value": 5, "unit": "개",
            "evidence": "tumblebug-networkinfo",
            "note": "Network·Gateway·DNS·Reserved·Broadcast", "caveat": None,
        },
        {
            "id": "t::k8s-min/vCPU", "kind": "minimum", "scope": "k8s-node",
            "metric": "vCPU", "value": 2, "unit": None,
            "evidence": "tumblebug-dynamic", "note": None,
            "caveat": "이 도구가 강제하는 최소치입니다.",
        },
        {
            "id": "t::k8s-subnet/aws", "kind": "required_count", "scope": "aws",
            "metric": "requiredSubnetCount", "value": 2, "unit": "서브넷",
            "evidence": "tumblebug-k8sinfo", "note": None, "caveat": None,
        },
        {
            "id": "t::ref/web", "kind": "reference_point", "scope": "workload:web",
            "metric": "specId", "value": "aws+ap-northeast-2+t3.small", "unit": None,
            "evidence": "tumblebug-template", "note": "Small AWS web server",
            "caveat": "이 소스의 예시입니다. 정답이 아닙니다.",
        },
        {
            "id": "b::nano/requests/cpu", "kind": "preset", "scope": "container:nano",
            "metric": "requests.cpu", "value": "100m", "unit": None,
            "evidence": "bitnami-preset", "note": None,
            "caveat": "not meant to be used in production",
        },
    ],
    "_coverage": [],
    "_source": [],
}


@pytest.fixture
def built(tmp_path):
    (tmp_path / "tumblebug-sizing.json").write_text(
        json.dumps(RULES, ensure_ascii=False), encoding="utf-8"
    )
    dataset.clear_caches()
    yield tmp_path
    dataset.clear_caches()


# --- 공식 --------------------------------------------------------------------

@pytest.mark.parametrize(
    ("prefix", "reserved", "expected"),
    [(24, 5, 251), (24, 4, 252), (28, 5, 11), (16, 5, 65531), (32, 5, 0)],
)
def test_usable_ips(prefix, reserved, expected) -> None:
    assert usable_ips(prefix, reserved) == expected


def test_tiny_subnet_never_goes_negative() -> None:
    """음수를 주면 "마이너스 3대 띄울 수 있다"는 답이 나온다."""
    assert usable_ips(31, 5) == 0


def test_prefix_out_of_range_is_rejected() -> None:
    with pytest.raises(ValueError):
        usable_ips(33, 5)


# --- 모르는 것을 0으로 채우지 않는다 ------------------------------------------

def test_known_provider_calculates(built) -> None:
    text = subnet_capacity(24, "azure", output_dir=built)
    assert "251" in text


def test_unknown_provider_refuses_to_guess(built) -> None:
    """**핵심.** AWS는 표가 비어 있다 — 0으로 두면 256대라고 답하게 된다."""
    text = subnet_capacity(24, "aws", output_dir=built)
    assert "251" not in text
    assert "모릅니다" in text
    assert "예약이 없다는 뜻이 아닙니다" in text


def test_unknown_provider_still_gives_the_total(built) -> None:
    """아는 것까지 감추지는 않는다 — 전체 주소 수는 산술이라 확실하다."""
    assert "256" in subnet_capacity(24, "aws", output_dir=built)


def test_bad_prefix_is_reported_not_raised(built) -> None:
    assert "범위를 벗어납니다" in subnet_capacity(99, "azure", output_dir=built)


# --- 경고가 값과 함께 간다 ----------------------------------------------------

def test_tool_minimum_says_who_requires_it(built) -> None:
    """K8s 최소치는 **도구가 강제하는 값**이지 쿠버네티스가 정한 값이 아니다."""
    text = requirements("k8s-node", output_dir=built)
    assert "이 도구가 강제하는" in text


def test_reference_point_is_not_an_answer(built) -> None:
    text = reference_points("web", output_dir=built)
    assert "정답이 아니라" in text and "t3.small" in text


def test_preset_carries_the_source_warning(built) -> None:
    """값만 옮기고 경고를 떼면 테스트용 숫자가 권장값이 된다."""
    text = container_presets(output_dir=built)
    assert "not meant to be used in production" in text
    assert "인스턴스 규모가 아닙니다" in text


def test_every_answer_says_sizing_is_an_estimate(built) -> None:
    for text in (
        subnet_capacity(24, "azure", output_dir=built),
        requirements("k8s-node", output_dir=built),
        reference_points("web", output_dir=built),
        container_presets(output_dir=built),
    ):
        assert "부하 테스트로 검증" in text


# --- 모델 --------------------------------------------------------------------

def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        Rule(id="x", kind="vibes", scope="a", metric="m", value=1, evidence="e")


def test_missing_scope_lists_what_is_known(built) -> None:
    text = requirements("nope", output_dir=built)
    assert "담긴 범위" in text


def test_unbuilt_says_how_to_build(tmp_path) -> None:
    dataset.clear_caches()
    try:
        assert "sizingkb build" in subnet_capacity(24, "azure", output_dir=tmp_path)
    finally:
        dataset.clear_caches()
