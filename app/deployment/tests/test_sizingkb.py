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
    flat = " ".join(text.split()).lower()
    assert "251" not in flat
    assert "does not know how many reserved ips 'aws' takes" in flat
    assert "does not mean there are none" in flat


def test_unknown_provider_still_gives_the_total(built) -> None:
    """아는 것까지 감추지는 않는다 — 전체 주소 수는 산술이라 확실하다."""
    assert "256" in subnet_capacity(24, "aws", output_dir=built)


def test_bad_prefix_is_reported_not_raised(built) -> None:
    text = " ".join(subnet_capacity(99, "azure", output_dir=built).split()).lower()
    assert "prefix length out of range: /99 (0-32)" in text


# --- 경고가 값과 함께 간다 ----------------------------------------------------

def test_hand_entered_value_says_so(built) -> None:
    """**손으로 적은 값이면 그렇다고 말해야 한다.**

    근거 라벨(`손 검수`)만 보이면 "원본에 명시됨"으로 읽혀 기계 판독 소스가 있는
    것처럼 들린다. `networkinfo.yaml`이 aws·gcp를 비워 두었고 아카이브된 AWS 문서도
    비어 있어서 손으로 적은 값이다.
    """
    from sizingkb.model import Rule

    rules = dict(RULES)
    rules["rules"] = RULES["rules"] + [{
        "id": "reviewed::reserved/aws", "kind": "reserved_ips", "scope": "aws",
        "metric": "reservedIps", "value": 5, "unit": "개",
        "evidence": "human-review", "note": "네트워크·라우터·DNS·예약·브로드캐스트",
        "caveat": "**기계 판독 소스가 없어 사람이 적은 값**입니다.",
    }]
    import json as _json
    from pathlib import Path as _Path
    target = _Path(built) / "reviewed-sizing.json"
    target.write_text(_json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    dataset.clear_caches()
    text = subnet_capacity(24, "aws", output_dir=built)
    assert "251" in text
    assert "사람이 적은 값" in text


# --- 손 검수 예약 IP (재편 계획 ⑥-D) ------------------------------------------

def test_reviewed_reserved_ips_all_carry_a_verification_pointer() -> None:
    """손 검수 값은 **사람이 확인할 곳**이 있어야 검증 가능하다 — 확인처 없는
    항목이 늘어나는 순간 이 파일이 출처 불명 상수 모음이 된다."""
    from sizingkb.parsers.reviewed import build_rules

    rules = build_rules().rules
    assert len(rules) >= 6  # aws·gcp + ⑥-D의 tencent·oracle·nhn·ncp
    for rule in rules:
        assert rule.caveat and "확인처" in rule.caveat, rule.id
        assert rule.evidence == "human-review", rule.id


def test_openstack_and_kt_are_deliberately_absent() -> None:
    """openstack은 구성에 따라 가변(Neutron)이고 kt는 고정 수 명시를 찾지 못했다
    (조사 2026-07-24). 조용히 들어오면 그 조사 결론이 무효가 된다 — 넣으려면
    공식 문서의 고정 수 명시가 먼저다."""
    from sizingkb.parsers.reviewed import build_rules

    scopes = {r.scope for r in build_rules().rules}
    assert "openstack" not in scopes
    assert "kt" not in scopes


def test_ncp_reserved_count_matches_the_documented_arithmetic() -> None:
    """확인처가 말하는 것은 예약 수가 아니라 **가용 수(/24→249)**다 — 우리가
    산술로 뒤집은 값(7)이 그 문서 수치를 그대로 재현해야 한다."""
    from sizingkb.model import usable_ips
    from sizingkb.parsers.reviewed import build_rules

    ncp = next(r for r in build_rules().rules if r.scope == "ncp")
    assert usable_ips(24, int(ncp.value)) == 249


def test_tool_minimum_says_who_requires_it(built) -> None:
    """K8s 최소치는 **도구가 강제하는 값**이지 쿠버네티스가 정한 값이 아니다."""
    text = requirements("k8s-node", output_dir=built)
    assert "이 도구가 강제하는" in text


def test_reference_point_is_not_an_answer(built) -> None:
    text = reference_points("web", output_dir=built)
    flat = " ".join(text.split()).lower()
    assert "this source's examples, not the right answer" in flat and "t3.small" in flat


def test_preset_carries_the_source_warning(built) -> None:
    """값만 옮기고 경고를 떼면 테스트용 숫자가 권장값이 된다."""
    text = container_presets(output_dir=built)
    flat = " ".join(text.split()).lower()
    assert "not meant to be used in production" in flat
    assert "these are **container** sizes, not instance sizes" in flat


def test_every_answer_says_sizing_is_an_estimate(built) -> None:
    for text in (
        subnet_capacity(24, "azure", output_dir=built),
        requirements("k8s-node", output_dir=built),
        reference_points("web", output_dir=built),
        container_presets(output_dir=built),
    ):
        assert "**verify it with a load test.**" in " ".join(text.split()).lower()


# --- 모델 --------------------------------------------------------------------

def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        Rule(id="x", kind="vibes", scope="a", metric="m", value=1, evidence="e")


def test_missing_scope_lists_what_is_known(built) -> None:
    text = " ".join(requirements("nope", output_dir=built).split()).lower()
    assert "no sizing requirement for 'nope' in this dataset. scopes included:" in text


def test_unbuilt_says_how_to_build(tmp_path) -> None:
    dataset.clear_caches()
    try:
        assert "sizingkb build" in subnet_capacity(24, "azure", output_dir=tmp_path)
    finally:
        dataset.clear_caches()
