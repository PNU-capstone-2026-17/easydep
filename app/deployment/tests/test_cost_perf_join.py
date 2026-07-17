"""costkb × perfkb 조인 테스트.

이 조인이 Phase 2의 핵심이다 — costkb가 t3a.medium을 1순위로 줄 때 perfkb 경고가
함께 나와야, 가격만 보고 버스트 인스턴스를 상시 서버에 붙이는 실수를 막는다.

두 층으로 검증한다:
1. costkb의 annotate 확장점(순수) — perfkb를 모른 채 주석을 붙인다
2. 도구 계층의 브리지(_perf_annotate) — id로 perfkb를 조회하고 fail-open한다
"""

from __future__ import annotations

import json

import pytest

from costkb import agent_api as cost_api
from perfkb import dataset as perf_dataset


# --- 1. costkb annotate 확장점 (perfkb 없이) ---


def test_recommend_specs_appends_annotation() -> None:
    """annotate가 문자열을 주면 해당 후보 줄에 ⚠로 붙는다."""
    text = cost_api.recommend_specs(
        vcpu_min=2, mem_min_gib=4, provider="aws", limit=2,
        annotate=lambda spec: f"경고-{spec['specName']}",
    )
    assert "⚠ 경고-" in text


def test_recommend_specs_skips_none_annotation() -> None:
    """annotate가 None을 주는 후보엔 아무것도 안 붙는다."""
    text = cost_api.recommend_specs(
        vcpu_min=2, mem_min_gib=4, provider="aws", limit=3, annotate=lambda spec: None
    )
    assert "⚠" not in text


def test_recommend_specs_without_annotate_is_unchanged() -> None:
    """조인이 옵션이라는 것 — annotate 없이도 기존대로 동작한다."""
    text = cost_api.recommend_specs(vcpu_min=2, mem_min_gib=4, provider="aws", limit=2)
    assert "추천 후보" in text
    assert "⚠" not in text


# --- 2. 도구 계층 브리지 ---


@pytest.fixture()
def perf_built(tmp_path, monkeypatch):
    records = [
        {"id": "aws+us-east-1+t3a.medium", "provider": "aws", "specName": "t3a.medium",
         "sustainedCpu": {"value": False, "note": "버스트 인스턴스 — CPU 크레딧이 소진되면 baseline 성능으로 떨어집니다.",
                          "evidence": "aws-burstable-field", "confidence": 1.0}},
    ]
    (tmp_path / "tumblebug-perf.json").write_text(
        json.dumps({"_note": "테스트", "specs": records}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(perf_dataset, "DEFAULT_OUTPUT_DIR", tmp_path)
    perf_dataset._load_cached.cache_clear()
    perf_dataset._by_id.cache_clear()
    perf_dataset._schema.cache_clear()
    yield
    perf_dataset._load_cached.cache_clear()
    perf_dataset._by_id.cache_clear()
    perf_dataset._schema.cache_clear()


def test_bridge_warns_on_known_burst_spec(perf_built) -> None:
    from nim_agent.cost_tools import _perf_annotate

    warning = _perf_annotate({"id": "aws+us-east-1+t3a.medium", "specName": "t3a.medium"})
    assert warning is not None and "크레딧" in warning


def test_bridge_failopens_on_bundled_spec_without_id(perf_built) -> None:
    """번들 36건은 id가 없다 — 조회 없이 조용히 넘어간다."""
    from nim_agent.cost_tools import _perf_annotate

    assert _perf_annotate({"specName": "t3.medium"}) is None  # id 키 자체가 없음


def test_bridge_failopens_when_perfkb_absent(tmp_path, monkeypatch) -> None:
    from nim_agent.cost_tools import _perf_annotate

    monkeypatch.setattr(perf_dataset, "DEFAULT_OUTPUT_DIR", tmp_path)
    perf_dataset._load_cached.cache_clear()
    perf_dataset._by_id.cache_clear()
    assert _perf_annotate({"id": "aws+us-east-1+t3a.medium"}) is None
