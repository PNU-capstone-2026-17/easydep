"""리전 질의는 **정확 일치가 이긴다** — 없을 때만 부분 일치로 넓히고, 밝힌다.

미러 이탈 두 곳 중 하나였다. Tumblebug은 정확 비교인데 우리는 무조건 부분 일치라,
실존 리전 **15쌍이 서로의 부분문자열**인 탓에 질의가 오염됐다(실측):

    region='centralus'  1,234건이어야 하는데 4,120건 (north/south/westcentralus)
    region='us-east'      190건이어야 하는데 4,349건 (us-east-1/2, us-east1/4/5)
    region='eastus'     1,301건이어야 하는데 2,504건 (eastus2)

**`us-east`는 그 자체로 실존 리전이면서 다섯 리전의 접두사다.** 그래서 "부분 일치를
없앤다"도 "유지한다"도 답이 아니었다 — 정확 일치를 우선하면 둘 다 산다.

conftest가 리전 4개짜리 번들만 보므로 이 충돌은 구조적으로 안 잡혔다. 여기서는
겹치는 리전을 직접 만들어 고정한다.
"""

from __future__ import annotations

import json

import pytest

from costkb import dataset
from costkb.agent_api import recommend_specs
from costkb.dataset import filter_specs, resolve_region


def _spec(provider: str, region: str, name: str, price: float | None = 0.05) -> dict:
    return {
        "id": f"{provider}+{region}+{name}", "provider": provider, "region": region,
        "specName": name, "vCPU": 2, "memGiB": 4.0, "hourlyUSD": price,
        "architecture": "x86_64", "infraType": "node",
        "acceleratorCount": 0, "acceleratorMemoryGB": 0.0,
    }


# 실제로 겹치는 이름들만 골랐다 — 지어낸 리전으로는 이 결함이 재현되지 않는다.
SPECS = [
    _spec("azure", "centralus", "A"),
    _spec("azure", "northcentralus", "B"),
    _spec("azure", "southcentralus", "C"),
    _spec("ibm", "us-east", "D"),           # us-east는 그 자체로 실존 리전이다
    _spec("aws", "us-east-1", "E"),
    _spec("gcp", "us-east1", "F"),
    _spec("aws", "ap-northeast-1", "G"),    # 정확 일치가 없는 접두사
    _spec("aws", "ap-northeast-2", "H"),
]


@pytest.fixture
def built(tmp_path):
    (tmp_path / "tumblebug-cost.json").write_text(
        json.dumps({"_note": "테스트", "specs": SPECS, "_source": []}), encoding="utf-8"
    )
    dataset.clear_caches()
    yield tmp_path
    dataset.clear_caches()


# --- 해결 규칙 -------------------------------------------------------------

def test_exact_match_wins_over_longer_neighbours(built) -> None:
    """**핵심 회귀.** centralus가 north/southcentralus를 함께 물지 않는다."""
    regions, how = resolve_region("centralus", built)
    assert how == "exact"
    assert regions == frozenset({"centralus"})


def test_region_that_is_also_a_prefix_resolves_to_itself(built) -> None:
    """us-east는 실존 리전이면서 us-east-1/us-east1의 접두사이기도 하다."""
    assert resolve_region("us-east", built) == (frozenset({"us-east"}), "exact")


def test_substring_survives_where_there_is_no_exact_match(built) -> None:
    """편의를 버리지 않는다 — ap-northeast는 여전히 -1/-2를 잡는다."""
    regions, how = resolve_region("ap-northeast", built)
    assert how == "partial"
    assert regions == frozenset({"ap-northeast-1", "ap-northeast-2"})


def test_unknown_region_is_told_apart_from_a_wide_one(built) -> None:
    assert resolve_region("nowhere-9", built) == (frozenset(), "none")


def test_case_is_ignored(built) -> None:
    assert resolve_region("CentralUS", built)[0] == frozenset({"centralus"})


# --- 필터에 실제로 걸리는가 ------------------------------------------------

def test_filter_returns_only_the_exact_region(built) -> None:
    found = filter_specs(region="centralus", limit=99, output_dir=built)
    assert {s["region"] for s in found} == {"centralus"}


def test_filter_still_spans_on_fallback(built) -> None:
    found = filter_specs(region="ap-northeast", limit=99, output_dir=built)
    assert {s["region"] for s in found} == {"ap-northeast-1", "ap-northeast-2"}


# --- 밝히는가 --------------------------------------------------------------

def test_exact_query_says_nothing_extra(built) -> None:
    """사용자가 물은 그대로이므로 덧붙일 말이 없다."""
    text = recommend_specs(region="centralus", limit=5, output_dir=built)
    assert "묶어" not in text


def test_widened_query_discloses_the_mix(built) -> None:
    """**여러 리전 단가를 하나로 읽으면 틀린 비교가 된다.**"""
    text = recommend_specs(region="ap-northeast", limit=5, output_dir=built)
    assert "2개 리전을 묶어" in text
    assert "ap-northeast-1" in text and "ap-northeast-2" in text


def test_unknown_region_names_the_culprit(built) -> None:
    """빈 결과에 '조건을 조정하세요'만 주면 **사용자는 vCPU·메모리만 낮추다 끝난다.**

    범인이 리전 이름이면 그걸 먼저 말해야 한다 — 가속기 조건에서 이미 겪은 모양이다.
    """
    text = recommend_specs(region="nowhere-9", limit=5, output_dir=built)
    assert "nowhere-9" in text and "리전 이름" in text


# --- 미러 이탈 고지 --------------------------------------------------------

def test_source_note_admits_the_deviations() -> None:
    """`_note`가 '두 경로의 답이 일치합니다'를 **무조건 단언**하고 있었다.

    같은 문단이 바로 다음 문장에서 memGiB를 보정했다고 말한다 — 한 문단 안의 모순이다.
    일부러 다르게 하는 곳 셋을 이름으로 적는다.
    """
    from costkb.parsers.tumblebug import SOURCE_NOTE

    assert "항상 같지는 않고" in SOURCE_NOTE
    for deviation in ("memGiB", "가격 미상", "정확 일치"):
        assert deviation in SOURCE_NOTE
