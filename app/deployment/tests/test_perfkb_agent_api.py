"""perfkb 사전 정의 질의 API 테스트.

핵심: 무엇을 경고로 삼고 무엇을 안 삼는가. burst_network는 AWS의 절반이라 추천 경고에서
빼야 하고(노이즈), 상시 CPU 미보장·구세대만 남긴다. fail-open도 여기서 고정한다.
"""

from __future__ import annotations

import json

import pytest

from app.deployment.perfkb import agent_api, dataset


@pytest.fixture()
def perf_built(tmp_path, monkeypatch):
    """임시 output에 성능 데이터셋을 심고 캐시를 격리한다."""
    records = [
        # 버스트(상시 미보장) + 네트워크 버스트 — 네트워크 버스트는 추천 경고에 안 나와야 함
        {"id": "aws+us-east-1+t3a.medium", "provider": "aws", "specName": "t3a.medium",
         "sustainedCpu": {"value": False, "note": "버스트 인스턴스 — CPU 크레딧이 소진되면 baseline 성능으로 떨어집니다.",
                          "evidence": "aws-burstable-field", "basis": "stated"},
         "currentGeneration": True, "networkIsBurst": True, "clockGHz": 2.2},
        # 구세대(상시 보장)
        {"id": "aws+us-east-1+m5.large", "provider": "aws", "specName": "m5.large",
         "sustainedCpu": {"value": True, "note": None, "evidence": "aws-burstable-field", "basis": "stated"},
         "currentGeneration": False, "clockGHz": 3.1, "ebsBaselineMbps": 650, "ebsMaxMbps": 4750},
        # 문제 없음
        {"id": "aws+us-east-1+m7i.large", "provider": "aws", "specName": "m7i.large",
         "sustainedCpu": {"value": True, "note": None, "evidence": "aws-burstable-field", "basis": "stated"},
         "currentGeneration": True},
        # Azure B계열 — 이름 추론이라 신뢰도 0.8
        {"id": "azure+eastus+Standard_B2s", "provider": "azure", "specName": "Standard_B2s",
         "sustainedCpu": {"value": False, "note": "B계열(버스트) — 크레딧 모델이라 상시 부하에서 성능이 떨어집니다.",
                          "evidence": "azure-family-name", "basis": "inferred"},
         "acu": 160},
        # 비교용: 같은 AWS 스펙 하나 더 (클럭·EBS 있음)
        {"id": "azure+eastus+Standard_D2s_v3", "provider": "azure", "specName": "Standard_D2s_v3",
         "sustainedCpu": {"value": True, "note": None, "evidence": "azure-family-name", "basis": "inferred"},
         "acu": 210, "diskIops": 3200},
    ]
    (tmp_path / "tumblebug-perf.json").write_text(
        json.dumps({"_note": "테스트", "specs": records}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(dataset, "DEFAULT_OUTPUT_DIR", tmp_path)
    dataset.clear_caches()
    yield
    dataset.clear_caches()


# --- recommend_warning: 무엇을 경고하나 ---


def test_burstable_instance_warns_with_mechanism(perf_built) -> None:
    warning = agent_api.recommend_warning("aws+us-east-1+t3a.medium")
    assert warning is not None
    assert "크레딧" in warning  # note의 메커니즘이 전달돼야 함


def test_network_burst_is_not_a_recommend_warning(perf_built) -> None:
    """네트워크 버스트는 AWS의 절반이라 추천 경고에 넣으면 노이즈다."""
    warning = agent_api.recommend_warning("aws+us-east-1+t3a.medium")
    assert "network" not in warning.lower()  # sustainedCpu 경고는 나오되 네트워크 버스트는 아님


def test_old_generation_warns(perf_built) -> None:
    warning = agent_api.recommend_warning("aws+us-east-1+m5.large")
    assert warning is not None
    assert "previous-generation instance" in " ".join(warning.split())


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
    dataset.clear_caches()
    assert agent_api.recommend_warning("aws+us-east-1+t3a.medium") is None


# --- instance_profile ---


def test_profile_hedges_when_inferred(perf_built) -> None:
    text = agent_api.instance_profile("azure", "Standard_B2s")
    # 이름 추론임을 사람에게 밝힌다
    assert "(a guess from the naming convention)" in " ".join(text.split())


def test_profile_of_missing_spec_is_graceful(perf_built) -> None:
    text = agent_api.instance_profile("aws", "no-such-spec")
    assert "No performance data for aws no-such-spec." in " ".join(text.split())


# --- compare: 승자를 뽑지 않고, 프로바이더 간은 거부 ---


def test_compare_lays_out_axes_without_declaring_winner(perf_built) -> None:
    flat = " ".join(agent_api.compare("aws", ["m5.large", "m7i.large"]).split())
    assert "m5.large" in flat and "m7i.large" in flat
    assert "clock speed" in flat and "GHz" in flat  # AWS 전용 축
    assert "no winner is declared" in flat


def test_compare_marks_generation_difference(perf_built) -> None:
    """m5.large는 구세대, m7i.large는 최신.

    예전엔 이 테스트가 상시 CPU 행의 존재만 봤다 — 이름은 '세대 차이'인데 세대를
    검사하지 않았고, 그래서 `_COMPARE_AXES`에 `currentGeneration`이 없다는 사실이
    통과하는 테스트 뒤에 가려져 있었다.
    """
    flat = " ".join(agent_api.compare("aws", ["m5.large", "m7i.large"]).split())
    assert "Sustained CPU performance" in flat
    assert "previous generation" in flat and "current generation" in flat


def test_compare_needs_two_found_specs(perf_built) -> None:
    flat = " ".join(agent_api.compare("aws", ["m5.large", "does-not-exist"]).split())
    assert "Comparing needs at least 2 aws specs." in flat
    assert "does-not-exist" in flat  # 못 찾은 것을 밝힌다


def test_compare_azure_uses_acu_axis(perf_built) -> None:
    flat = " ".join(agent_api.compare("azure", ["Standard_B2s", "Standard_D2s_v3"]).split())
    assert "ACU" in flat  # Azure 전용 축
    assert "clock speed" not in flat  # AWS 축은 안 나와야 함


def test_ebs_baseline_filter_ranks_and_notes_burst(perf_built) -> None:
    flat = " ".join(agent_api.specs_meeting_ebs_baseline(600).split())
    assert "m5.large" in flat  # baseline 650 >= 600
    assert "sustained" in flat and "burst max" in flat  # baseline vs max 구분


def test_ebs_baseline_filter_excludes_below_threshold(perf_built) -> None:
    flat = " ".join(agent_api.specs_meeting_ebs_baseline(10000).split())
    assert "Found no AWS spec with sustained EBS bandwidth" in flat


def test_network_burst_warning_sits_under_network_line(tmp_path) -> None:
    """경고는 네트워크 줄 바로 밑에 — 블록 끝에 붙이면 EBS 줄에 달린 것처럼 읽힌다 (결함 ⑤)."""
    from app.deployment.perfkb.agent_api import _describe

    text = _describe({
        "networkPerformance": "Up to 5 Gigabit",
        "networkIsBurst": True,
        "ebsBaselineMbps": 347.0,
        "ebsMaxMbps": 2085.0,
    })
    lines = [line.lower() for line in text.split("\n")]
    warn = next(i for i, l in enumerate(lines) if "burst" in l)
    assert "network" in lines[warn - 1], f"경고가 엉뚱한 줄에 붙었다: {lines[warn - 1]!r}"
    assert "ebs" not in lines[warn - 1]
