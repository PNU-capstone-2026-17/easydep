"""리전 간 지연 — cb-tumblebug 벤치마크 실측.

**이 축의 위험은 '보장'으로 읽히는 것**이다. 벤더 SLA가 아니라 어느 시점의 관측이고,
측정 시각조차 원본에 없다.

그리고 이 축이 **다른 축의 버그를 잡았다** — 물리 하한 불변식이 `alibaba eu-west-1`을
지목했고, 확인해 보니 이름은 "UK (London)"인데 좌표가 아일랜드(`aws eu-west-1`과 동일)
였다. 좌표를 지어내 고치지 않고 표시만 남긴다.
"""

from __future__ import annotations

import json

import pytest

from envkb.latency import (
    annotate_distance,
    describe,
    faster_than_light,
    haversine_km,
    parse_matrix,
)

MATRIX = (
    ",aws-ap-northeast-2,aws-ap-northeast-1,azure-koreacentral\n"
    "aws-ap-northeast-2,0.446,34.243,3.272\n"
    "aws-ap-northeast-1,34.101,0.398,\n"
    "azure-koreacentral,3.301,,0.512\n"
)


def test_matrix_becomes_pairs() -> None:
    records, stats = parse_matrix(MATRIX)
    assert stats["rows"] == 3 and stats["cols"] == 3
    # 빈칸은 담지 않는다 — 0으로 채우면 "지연 0"이 된다
    assert len(records) == 7


def test_self_latency_is_kept() -> None:
    """A→A는 리전 **안**의 지연이라 뜻이 있다(중앙값 0.45 ms)."""
    records, _ = parse_matrix(MATRIX)
    self_pairs = [
        r for r in records if r["sourceRegion"] == r["targetRegion"]
    ]
    assert len(self_pairs) == 3


def test_cross_provider_pairs_survive() -> None:
    """**같은 도시의 AWS↔Azure가 3.3 ms** — 이게 이 데이터의 값어치다."""
    records, _ = parse_matrix(MATRIX)
    found = next(
        r for r in records
        if r["sourceProvider"] == "aws" and r["targetProvider"] == "azure"
    )
    assert found["latencyMs"] == 3.272


def test_zero_and_blank_are_not_measurements() -> None:
    records, _ = parse_matrix(",a-x\nb-y,0\nc-z,\n")
    assert records == []


# --- 물리 검증 ---------------------------------------------------------------

def test_haversine_seoul_tokyo() -> None:
    seoul, tokyo = (37.5665, 126.978), (35.6895, 139.6917)
    assert 1100 < haversine_km(seoul, tokyo) < 1200


@pytest.mark.parametrize(
    ("km", "ms", "impossible"),
    [
        (1150, 34.2, False),   # 서울↔도쿄 실측 — 정상
        (584, 3.199, True),    # 불변식이 잡은 것 — 왕복 광속보다 빠르다
        (100, 1.5, False),
        (10000, 5.0, True),
    ],
)
def test_faster_than_light(km, ms, impossible) -> None:
    assert faster_than_light(km, ms) is impossible


def test_suspect_is_marked_not_deleted() -> None:
    """**지우지 않는다** — 조용히 버리면 다음 사람이 같은 것을 다시 발견한다."""
    records = [
        {"sourceProvider": "alibaba", "sourceRegion": "eu-west-1",
         "targetProvider": "azure", "targetRegion": "uksouth", "latencyMs": 2.913},
    ]
    coords = {("alibaba", "eu-west-1"): (53.0, -8.0),   # 이름은 런던인데 아일랜드 좌표
              ("azure", "uksouth"): (50.941, -0.799)}
    stats = annotate_distance(records, coords)
    assert stats["fasterThanLight"] == 1
    assert records[0]["suspect"]          # 표시는 남고
    assert records[0]["latencyMs"] == 2.913  # 값은 그대로다


def test_unknown_coords_are_skipped_not_guessed() -> None:
    records = [
        {"sourceProvider": "kt", "sourceRegion": "kr1",
         "targetProvider": "aws", "targetRegion": "ap-northeast-2", "latencyMs": 5.0},
    ]
    stats = annotate_distance(records, {})
    assert stats["withDistance"] == 0
    assert "distanceKm" not in records[0]


# --- 답변 --------------------------------------------------------------------

@pytest.fixture
def built(tmp_path):
    records, _ = parse_matrix(MATRIX)
    (tmp_path / "region-latency.json").write_text(
        json.dumps({"_note": "테스트", "pairs": records}, ensure_ascii=False),
        encoding="utf-8",
    )
    return tmp_path


def test_answer_says_it_is_not_an_sla(built) -> None:
    text = describe("aws-ap-northeast-2", output_dir=built)
    assert "SLA가 아닙니다" in text and "측정 시각은 원본에 없습니다" in text


def test_answer_sorts_by_closest(built) -> None:
    text = describe("aws-ap-northeast-2", output_dir=built)
    lines = [l for l in text.splitlines() if "ms" in l and l.startswith("  ")]
    values = [float(l.split("ms")[0].strip()) for l in lines]
    assert values == sorted(values)


def test_missing_source_says_not_measured(built) -> None:
    """'없다'가 아니라 '이 벤치마크가 안 쟀다'."""
    text = describe("gcp-nowhere", output_dir=built)
    assert "안 쟀다" in text


def test_unbuilt_says_how_to_build(tmp_path) -> None:
    assert "build-latency" in describe("aws-ap-northeast-2", output_dir=tmp_path)
