"""costkb × perfkb 조인 테스트.

이 조인이 Phase 2의 핵심이다 — costkb가 t3a.medium을 1순위로 줄 때 perfkb 경고가
함께 나와야, 가격만 보고 버스트 인스턴스를 상시 서버에 붙이는 실수를 막는다.

두 층으로 검증한다:
1. costkb의 annotate/footer 확장점(순수) — perfkb를 모른 채 주석을 붙인다
2. 도구 계층의 브리지(_perf_annotate/_perf_footer) — perfkb를 조회하고 상태를 구분한다

**결함 C3·C4 회귀 방어가 여기 있다.** 조인이 id로만 이뤄지던 시절, costkb 번들 36건에는
id가 없어서 경고가 100% 침묵했다(하필 번들이 t3.*/B*/e2-*라 추천 상위를 독점한다).
그리고 "경고 없음"과 "정보 없음"의 출력이 바이트 단위로 같았다.
"""

from __future__ import annotations

import json

import pytest

from costkb import agent_api as cost_api
from perfkb import dataset as perf_dataset


# --- 1. costkb annotate 확장점 (perfkb 없이) ---


def test_recommend_specs_appends_annotation_verbatim() -> None:
    """콜백 문자열이 **그대로** 붙는다 — 기호는 costkb가 아니라 콜백 소유다.

    예전엔 costkb가 `⚠`를 하드코딩했는데, 그러면 "모든 주석은 경고"라는 가정이 박혀
    "정보 없음" 같은 비경고 주석을 달 수 없다(C4의 표시 측 원인).
    """
    text = cost_api.recommend_specs(
        vcpu_min=2, mem_min_gib=4, provider="aws", limit=2,
        annotate=lambda spec: f"· 고지-{spec['specName']}",
    )
    assert "· 고지-" in text
    assert "⚠" not in text


def test_recommend_specs_skips_none_annotation() -> None:
    """annotate가 None을 주는 후보엔 아무것도 안 붙는다."""
    text = cost_api.recommend_specs(
        vcpu_min=2, mem_min_gib=4, provider="aws", limit=3, annotate=lambda spec: None
    )
    assert "⚠" not in text


def test_recommend_specs_without_annotate_is_unchanged() -> None:
    """조인이 옵션이라는 것 — annotate 없이도 기존대로 동작한다."""
    text = cost_api.recommend_specs(vcpu_min=2, mem_min_gib=4, provider="aws", limit=2)
    assert "Recommended candidates (on-demand list price, hourly rate):" in " ".join(text.split())
    assert "⚠" not in text


def test_footer_receives_results_and_appends_once() -> None:
    """footer는 후보 목록 전체를 받아 블록 끝에 한 번만 붙는다."""
    seen: list[int] = []
    text = cost_api.recommend_specs(
        vcpu_min=2, mem_min_gib=4, provider="aws", limit=3,
        footer=lambda specs: (seen.append(len(specs)), "꼬리말")[1],
    )
    assert seen == [3]
    assert text.count("※ 꼬리말") == 1


# --- 2. 도구 계층 브리지 ---


_BURST_NOTE = "버스트 인스턴스 — CPU 크레딧이 소진되면 baseline 성능으로 떨어집니다."


@pytest.fixture()
def perf_built(tmp_path, monkeypatch):
    """aws만 수록된 작은 성능 산출물.

    - `t3.medium`  — costkb 번들에 실재하고 id가 없는 스펙(C3 회귀용). 버스트.
    - `t3a.medium` — id로 조회되는 스펙(기존 경로).
    - `c5.large`   — 번들에 실재하는 정상 스펙(경고 없음).
    - `t3.large`는 **일부러 빼둔다** — 추적 프로바이더인데 레코드가 없는 경우.
    - azure/gcp/alibaba도 없다 — 미추적 프로바이더 경로.
    """
    burst = {"value": False, "note": _BURST_NOTE,
             "evidence": "aws-burstable-field", "basis": "stated"}
    records = [
        {"id": "aws+us-east-1+t3a.medium", "provider": "aws", "specName": "t3a.medium",
         "sustainedCpu": burst},
        {"id": "aws+us-east-1+t3.medium", "provider": "aws", "specName": "t3.medium",
         "sustainedCpu": burst},
        {"id": "aws+us-east-1+c5.large", "provider": "aws", "specName": "c5.large",
         "sustainedCpu": {"value": True, "note": None,
                          "evidence": "aws-burstable-field", "basis": "stated"},
         "currentGeneration": True},
    ]
    (tmp_path / "tumblebug-perf.json").write_text(
        json.dumps({"_note": "테스트", "specs": records}, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setattr(perf_dataset, "DEFAULT_OUTPUT_DIR", tmp_path)
    perf_dataset.clear_caches()
    yield
    perf_dataset.clear_caches()


@pytest.fixture()
def perf_partial(tmp_path, monkeypatch):
    """aws(신호 있음) + ibm(신호 없음)이 섞인 산출물.

    IBM 카탈로그엔 버스트 여부도 세대 표시도 없다. 레코드는 있으니 "미추적"도
    "레코드 없음"도 아니지만, **판정은 못 한다** — 그 세 번째 경우를 고정한다.
    """
    records = [
        {"id": "aws+us-east-1+c5.large", "provider": "aws", "specName": "c5.large",
         "sustainedCpu": {"value": True, "note": None,
                          "evidence": "aws-burstable-field", "basis": "stated"},
         "currentGeneration": True},
        {"id": "ibm+us-south+bx2-2x8", "provider": "ibm", "specName": "bx2-2x8",
         "networkBandwidthMbps": 4000, "vcpuTenancy": "dedicated"},
    ]
    (tmp_path / "tumblebug-perf.json").write_text(
        json.dumps({"_note": "테스트", "specs": records}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(perf_dataset, "DEFAULT_OUTPUT_DIR", tmp_path)
    perf_dataset.clear_caches()
    yield
    perf_dataset.clear_caches()


@pytest.fixture()
def perf_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(perf_dataset, "DEFAULT_OUTPUT_DIR", tmp_path)
    perf_dataset.clear_caches()
    yield
    perf_dataset.clear_caches()


def test_bridge_warns_on_known_burst_spec(perf_built) -> None:
    from nim_agent.cost_tools import _perf_annotate

    warning = _perf_annotate(
        {"id": "aws+us-east-1+t3a.medium", "provider": "aws", "specName": "t3a.medium"}
    )
    assert warning is not None and warning.startswith("⚠") and "크레딧" in warning


def test_bridge_joins_bundled_spec_without_id(perf_built) -> None:
    """**C3 회귀**: 번들 36건은 id가 없다 — (provider, specName)으로 조인돼야 한다.

    예전엔 여기서 조용히 None이 나왔고, 번들이 t3.*/B*/e2-*라 추천 상위가 통째로
    무경고였다.
    """
    from nim_agent.cost_tools import _perf_annotate

    warning = _perf_annotate({"provider": "aws", "specName": "t3.medium"})
    assert warning is not None and "크레딧" in warning


def test_bridge_distinguishes_untracked_from_ok(perf_built) -> None:
    """**C4 회귀**: 미추적·레코드없음·정상이 서로 다른 출력을 낸다."""
    from nim_agent.cost_tools import _perf_annotate

    untracked = _perf_annotate({"provider": "alibaba", "specName": "ecs.t5-lc1m2.large"})
    no_record = _perf_annotate({"provider": "aws", "specName": "t3.large"})
    ok = _perf_annotate({"provider": "aws", "specName": "c5.large"})

    assert untracked is not None and "alibaba" in untracked
    assert no_record is not None and "t3.large" in no_record
    assert ok is None  # 정상은 줄을 만들지 않는다 (꼬리말이 대신 밝힌다)
    assert untracked != no_record


def test_bridge_failopens_when_perfkb_absent(perf_absent) -> None:
    """미빌드는 경고를 지어내지 않는다 — 후보 줄은 비우고 꼬리말로만 알린다."""
    from nim_agent.cost_tools import _perf_annotate, _perf_footer

    spec = {"id": "aws+us-east-1+t3a.medium", "provider": "aws", "specName": "t3a.medium"}
    assert _perf_annotate(spec) is None
    footer = _perf_footer([spec])
    assert footer is not None and "perfkb build" in footer


def test_bridge_survives_corrupted_output(tmp_path, monkeypatch) -> None:
    """잘린 산출물에 추천 전체가 죽지 않는다 (결함 (마)의 조인 측)."""
    from nim_agent.cost_tools import _perf_annotate

    (tmp_path / "tumblebug-perf.json").write_text('{"_note": "잘림", "spe', encoding="utf-8")
    monkeypatch.setattr(perf_dataset, "DEFAULT_OUTPUT_DIR", tmp_path)
    perf_dataset.clear_caches()
    try:
        assert _perf_annotate({"provider": "aws", "specName": "t3.medium"}) is None
    finally:
        perf_dataset.clear_caches()


def test_footer_explains_silence_when_built(perf_built) -> None:
    from nim_agent.cost_tools import _perf_footer

    footer = _perf_footer([{"provider": "aws", "specName": "c5.large"}])
    assert footer is not None
    assert "performance-confirmed" in " ".join(footer.split())


def test_footer_does_not_call_partial_candidates_confirmed(perf_partial) -> None:
    """**IBM을 붙이며 생긴 구멍.** 레코드는 있는데 버스트·세대 신호가 없으면
    `_warning_for`가 아무것도 못 찾는다. 그걸 `ok`와 같이 다루면 꼬리말이
    "경고가 없는 후보는 성능 확인됨"이라고 **거짓말**한다.
    """
    from nim_agent.cost_tools import _perf_footer

    mixed = _perf_footer([
        {"provider": "aws", "specName": "c5.large"},
        {"provider": "ibm", "specName": "bx2-2x8"},
    ])
    assert mixed is not None
    assert "does not mean we confirmed it" in " ".join(mixed.split())


def test_footer_speaks_when_every_candidate_is_partial(perf_partial) -> None:
    """후보가 **전부** partial이면 두 갈래에 안 걸려 꼬리말이 통째로 사라졌다 —
    그러면 다시 침묵이 안전 신호로 읽힌다(결함 C4가 되돌아오는 자리).
    """
    from nim_agent.cost_tools import _perf_footer

    footer = _perf_footer([{"provider": "ibm", "specName": "bx2-2x8"}])
    assert footer is not None
    assert "**performance was not confirmed.**" in " ".join(footer.split())


# --- 3. 전 구간: 번들 모드에서 실제로 경고가 붙는가 (C3의 최종 형태) ---


def test_bundle_mode_recommendation_carries_warning(perf_built) -> None:
    """conftest가 강제하는 번들 36건 모드에서 t3.medium이 경고와 함께 1순위로 나온다."""
    from nim_agent.cost_tools import _perf_annotate, _perf_footer

    # limit=4: t3.medium(us-east-1) / t3.medium(ap-northeast-2) / t3.large / c5.large
    # — 세 상태(경고·정보없음·정상)가 한 블록에 다 나오는 최소 크기다.
    text = cost_api.recommend_specs(
        vcpu_min=2, mem_min_gib=4, provider="aws", limit=4,
        annotate=_perf_annotate, footer=_perf_footer,
    )
    flat = " ".join(text.split())
    assert "t3.medium" in flat
    assert "⚠" in flat and "크레딧" in flat  # 버스트 note는 산출물(한국어) 그대로다
    assert "· No performance data" in flat  # t3.large — 레코드 없음
    assert "performance-confirmed" in flat  # c5.large — 정상
