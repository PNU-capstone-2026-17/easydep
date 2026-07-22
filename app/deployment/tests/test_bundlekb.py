"""번들 모델·로드·질의.

이 축의 위험은 하나로 요약된다 — **명시(stated)와 실측(observed)을 섞는 것**.
섞는 순간 "템플릿 330개 중 100%"가 "Azure가 강제한다"로 읽힌다.
"""

from __future__ import annotations

import json

import pytest

from bundlekb import dataset
from bundlekb.agent_api import describe_named_bundle, list_bundles, resource_bundle
from bundlekb.model import ALWAYS, OPTIONAL, REQUIRED, Bundle, Companion, Member
from kbcommon.basis import OBSERVED, STATED, describe, is_fact, needs_hedge

VM = "azure::Microsoft.Compute/virtualMachines"
NIC = "azure::Microsoft.Network/networkInterfaces"
LOCK = "azure::Microsoft.Authorization/locks"

BUNDLES = {
    "bundles": [
        {
            "id": "avm::res/compute/virtual-machine",
            "name": "avm/res/compute/virtual-machine",
            "provider": "azure",
            "evidence": "avm-module",
            "anchor": VM,
            "description": "VM 모듈",
            "caveat": None,
            "members": [
                {"typeId": VM, "tier": "always", "note": None},
                {"typeId": NIC, "tier": "required", "note": None},
                {"typeId": LOCK, "tier": "optional", "note": None},
            ],
        },
        {
            "id": "tumblebug::sg-default",
            "name": "sg-default",
            "provider": "core",
            "evidence": "tumblebug-template",
            "anchor": "core::securityGroup",
            "description": "Opens all TCP/UDP ports (development/testing use).",
            "caveat": "Opens all TCP/UDP ports (development/testing use).",
            "members": [
                {"typeId": "core::securityGroup", "tier": "always", "note": "규칙 3개"}
            ],
        },
    ],
    "cooccurrence": [
        {"anchor": VM, "typeId": NIC, "hits": 330, "samples": 330, "evidence": "aqt-corpus"},
        {"anchor": VM, "typeId": LOCK, "hits": 18, "samples": 330, "evidence": "aqt-corpus"},
        # 표본이 적은 앵커 — 비율을 내면 안 된다
        {"anchor": "azure::Microsoft.Rare/thing", "typeId": NIC,
         "hits": 6, "samples": 17, "evidence": "aqt-corpus"},
    ],
    "_coverage": [],
    "_source": [],
}


@pytest.fixture
def built(tmp_path):
    (tmp_path / "avm-bundles.json").write_text(
        json.dumps(BUNDLES, ensure_ascii=False), encoding="utf-8"
    )
    dataset.clear_caches()
    yield tmp_path
    dataset.clear_caches()


# --- 새 등급의 뜻 ------------------------------------------------------------

def test_observed_is_not_a_fact() -> None:
    """**핵심.** 100%가 나와도 그걸로 값을 거부하면 안 된다."""
    assert not is_fact(OBSERVED)
    assert not is_fact(OBSERVED, reviewed=True)
    assert is_fact(STATED)


def test_observed_always_hedges() -> None:
    assert needs_hedge(OBSERVED)


def test_observed_label_says_what_was_counted() -> None:
    """"실측"이라고만 쓰면 클라우드를 잰 것처럼 읽힌다."""
    assert "템플릿" in describe(OBSERVED)


# --- 모델 --------------------------------------------------------------------

def test_unknown_tier_is_rejected() -> None:
    with pytest.raises(ValueError):
        Member("x", "maybe")


def test_ratio_needs_samples() -> None:
    assert Companion("a", "b", hits=3, samples=0, evidence="aqt-corpus").ratio == 0.0
    assert Companion("a", "b", hits=6, samples=17, evidence="aqt-corpus").ratio == pytest.approx(0.3529, abs=1e-3)


def test_bundle_roundtrip() -> None:
    bundle = Bundle.from_dict(BUNDLES["bundles"][0])
    assert Bundle.from_dict(bundle.to_dict()) == bundle
    assert bundle.by_tier(REQUIRED)[0].type_id == NIC


# --- 로드·조회 ---------------------------------------------------------------

def test_small_samples_are_not_reported(built) -> None:
    """**6/17을 35.3%로 보여주면 숫자에 없는 확신을 준다.**"""
    assert dataset.companions_of("azure::Microsoft.Rare/thing", output_dir=built) == ()


def test_large_samples_are_reported(built) -> None:
    found = dataset.companions_of(VM, output_dir=built)
    assert found[0].type_id == NIC and found[0].ratio == 1.0


def test_bundles_for_ignores_optional_membership(built) -> None:
    """`locks`는 148개 모듈에 선택으로 붙는다 — 그걸로 번들을 찾으면 목록이 무의미해진다."""
    assert dataset.bundles_for(LOCK, built) == ()
    assert len(dataset.bundles_for(VM, built)) == 1


# --- 답변 --------------------------------------------------------------------

def test_answer_separates_stated_from_observed(built) -> None:
    """두 근거를 한 줄에 섞으면 코퍼스 통계가 클라우드 사실처럼 읽힌다."""
    text = resource_bundle(VM, output_dir=built)
    assert "[값을 반드시 줘야 함]" in text
    assert "[실제 템플릿에서 함께 나온 비율]" in text
    assert "클라우드가 강제한다는 뜻이 아닙니다" in text


def test_cross_check_marks_agreement(built) -> None:
    """AVM(명시)과 코퍼스(실측)가 같은 답을 낸 것만 표시한다."""
    text = resource_bundle(VM, output_dir=built)
    assert "교차 확인" in text


def test_low_ratio_member_is_not_cross_checked(built) -> None:
    """18/330(5.5%)짜리에 교차 확인이 붙으면 표시의 뜻이 사라진다."""
    text = resource_bundle(VM, output_dir=built)
    lock_line = next(l for l in text.splitlines() if "locks" in l and l.startswith("  - "))
    assert "교차 확인" not in lock_line


def test_source_caveat_travels_with_the_bundle(built) -> None:
    """`sg-default`는 "전 포트를 연다"고 **자기가** 적어 두었다."""
    text = describe_named_bundle("sg-default", output_dir=built)
    assert "⚠" in text and "development/testing" in text


def test_missing_type_says_dataset_not_world(built) -> None:
    text = resource_bundle("azure::Microsoft.Nope/nope", output_dir=built)
    assert "이 데이터셋에 없다" in text


def test_unbuilt_says_how_to_build(tmp_path) -> None:
    dataset.clear_caches()
    try:
        assert "bundlekb build" in resource_bundle(VM, output_dir=tmp_path)
    finally:
        dataset.clear_caches()


def test_search_finds_by_name(built) -> None:
    assert "sg-default" in list_bundles("sg", output_dir=built)
