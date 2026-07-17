"""perfkb 사전 정의 질의 API 테스트.

핵심: 무엇을 경고로 삼고 무엇을 안 삼는가. burst_network는 AWS의 절반이라 추천 경고에서
빼야 하고(노이즈), 상시 CPU 미보장·구세대만 남긴다. fail-open도 여기서 고정한다.
"""

from __future__ import annotations

import json

import pytest

from perfkb import agent_api, dataset


@pytest.fixture()
def perf_built(tmp_path, monkeypatch):
    """임시 output에 성능 데이터셋을 심고 캐시를 격리한다."""
    records = [
        # 버스트(상시 미보장) + 네트워크 버스트 — 네트워크 버스트는 추천 경고에 안 나와야 함
        {"id": "aws+us-east-1+t3a.medium", "provider": "aws", "specName": "t3a.medium",
         "sustainedCpu": {"value": False, "note": "버스트 인스턴스 — CPU 크레딧이 소진되면 baseline 성능으로 떨어집니다.",
                          "evidence": "aws-burstable-field", "confidence": 1.0},
         "currentGeneration": True, "networkIsBurst": True, "clockGHz": 2.2},
        # 구세대(상시 보장)
        {"id": "aws+us-east-1+m5.large", "provider": "aws", "specName": "m5.large",
         "sustainedCpu": {"value": True, "note": None, "evidence": "aws-burstable-field", "confidence": 1.0},
         "currentGeneration": False, "clockGHz": 3.1, "ebsBaselineMbps": 650, "ebsMaxMbps": 4750},
        # 문제 없음
        {"id": "aws+us-east-1+m7i.large", "provider": "aws", "specName": "m7i.large",
         "sustainedCpu": {"value": True, "note": None, "evidence": "aws-burstable-field", "confidence": 1.0},
         "currentGeneration": True},
        # Azure B계열 — 이름 추론이라 신뢰도 0.8
        {"id": "azure+eastus+Standard_B2s", "provider": "azure", "specName": "Standard_B2s",
         "sustainedCpu": {"value": False, "note": "B계열(버스트) — 크레딧 모델이라 상시 부하에서 성능이 떨어집니다.",
                          "evidence": "azure-family-name", "confidence": 0.8},
         "acu": 160},
        # 비교용: 같은 AWS 스펙 하나 더 (클럭·EBS 있음)
        {"id": "azure+eastus+Standard_D2s_v3", "provider": "azure", "specName": "Standard_D2s_v3",
         "sustainedCpu": {"value": True, "note": None, "evidence": "azure-family-name", "confidence": 0.8},
         "acu": 210, "diskIops": 3200},
    ]
    (tmp_path / "tumblebug-perf.json").write_text(
        json.dumps({"_note": "테스트", "specs": records}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(dataset, "DEFAULT_OUTPUT_DIR", tmp_path)
    dataset._load_cached.cache_clear()
    dataset._by_id.cache_clear()
    dataset._schema.cache_clear()
    yield
    dataset._load_cached.cache_clear()
    dataset._by_id.cache_clear()
    dataset._schema.cache_clear()


# --- recommend_warning: 무엇을 경고하나 ---


def test_burstable_instance_warns_with_mechanism(perf_built) -> None:
    warning = agent_api.recommend_warning("aws+us-east-1+t3a.medium")
    assert warning is not None
    assert "크레딧" in warning  # note의 메커니즘이 전달돼야 함


def test_network_burst_is_not_a_recommend_warning(perf_built) -> None:
    """네트워크 버스트는 AWS의 절반이라 추천 경고에 넣으면 노이즈다."""
    warning = agent_api.recommend_warning("aws+us-east-1+t3a.medium")
    assert "네트워크" not in warning  # sustainedCpu 경고는 나오되 네트워크 버스트는 아님


def test_old_generation_warns(perf_built) -> None:
    warning = agent_api.recommend_warning("aws+us-east-1+m5.large")
    assert warning is not None
    assert "구세대" in warning


def test_clean_instance_has_no_warning(perf_built) -> None:
    assert agent_api.recommend_warning("aws+us-east-1+m7i.large") is None


def test_azure_burst_family_warns(perf_built) -> None:
    warning = agent_api.recommend_warning("azure+eastus+Standard_B2s")
    assert warning is not None
    assert "B계열" in warning


# --- fail-open ---


def test_unknown_id_returns_none(perf_built) -> None:
    assert agent_api.recommend_warning("aws+us-east-1+does-not-exist") is None


def test_none_and_empty_id_return_none(perf_built) -> None:
    assert agent_api.recommend_warning(None) is None
    assert agent_api.recommend_warning("") is None


def test_no_build_returns_none(tmp_path, monkeypatch) -> None:
    """성능 데이터셋이 없으면 경고 없이 조용히 넘어간다(빌드 안 한 사용자)."""
    monkeypatch.setattr(dataset, "DEFAULT_OUTPUT_DIR", tmp_path)
    dataset._load_cached.cache_clear()
    dataset._by_id.cache_clear()
    assert agent_api.recommend_warning("aws+us-east-1+t3a.medium") is None


# --- instance_profile ---


def test_profile_shows_confidence_hedge_for_inferred(perf_built) -> None:
    text = agent_api.instance_profile("azure", "Standard_B2s")
    assert "신뢰도 0.8" in text  # 이름 추론임을 사람에게 밝힌다


def test_profile_of_missing_spec_is_graceful(perf_built) -> None:
    text = agent_api.instance_profile("aws", "no-such-spec")
    assert "성능 데이터가 없습니다" in text


# --- compare: 승자를 뽑지 않고, 프로바이더 간은 거부 ---


def test_compare_lays_out_axes_without_declaring_winner(perf_built) -> None:
    text = agent_api.compare("aws", ["m5.large", "m7i.large"])
    assert "m5.large" in text and "m7i.large" in text
    assert "클럭(GHz)" in text  # AWS 전용 축
    assert "승자를 단정하지 않습니다" in text


def test_compare_marks_generation_difference(perf_built) -> None:
    """m5.large는 구세대, m7i.large는 최신 — 상시 CPU 행에 드러나야 함(둘 다 보장이지만)."""
    text = agent_api.compare("aws", ["m5.large", "m7i.large"])
    assert "상시 CPU 성능" in text


def test_compare_needs_two_found_specs(perf_built) -> None:
    text = agent_api.compare("aws", ["m5.large", "does-not-exist"])
    assert "2개 이상 필요" in text
    assert "does-not-exist" in text  # 못 찾은 것을 밝힌다


def test_compare_azure_uses_acu_axis(perf_built) -> None:
    text = agent_api.compare("azure", ["Standard_B2s", "Standard_D2s_v3"])
    assert "ACU" in text  # Azure 전용 축
    assert "클럭" not in text  # AWS 축은 안 나와야 함


def test_ebs_baseline_filter_ranks_and_notes_burst(perf_built) -> None:
    text = agent_api.specs_meeting_ebs_baseline(600)
    assert "m5.large" in text  # baseline 650 >= 600
    assert "지속" in text and "버스트 최대" in text  # baseline vs max 구분


def test_ebs_baseline_filter_excludes_below_threshold(perf_built) -> None:
    text = agent_api.specs_meeting_ebs_baseline(10000)
    assert "찾지 못했습니다" in text
