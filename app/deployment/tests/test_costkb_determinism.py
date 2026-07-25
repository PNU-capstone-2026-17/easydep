"""이유가 원본에 미기재였던 것들을 고정한다 — 동점 순서와 아키텍처 값.

셋 다 "왜 이렇게 되어 있는지 아무 데도 안 적혀 있다"로 기록돼 있던 항목이다.
적기만 해서는 다음 사람이 무심코 바꾼다. 검사로 못을 박는다.
"""

from __future__ import annotations

import json

import pytest

from app.deployment.costkb import dataset
from app.deployment.costkb.dataset import filter_specs


def _spec(provider: str, name: str, region: str, price: float, vcpu: int = 2) -> dict:
    return {
        "id": f"{provider}+{region}+{name}", "provider": provider, "region": region,
        "specName": name, "vCPU": vcpu, "memGiB": 4.0, "hourlyUSD": price,
        "architecture": "x86_64", "infraType": "node",
        "acceleratorCount": 0, "acceleratorMemoryGB": 0.0,
    }


# 전부 2 vCPU · 4 GiB · 같은 단가 — 정렬 축으로는 완전 동점이다.
TIED = [
    _spec("tencent", "S5.MEDIUM4", "ap-chengdu", 0.05),
    _spec("aws", "t3.medium", "us-east-1", 0.05),
    _spec("gcp", "e2-medium", "us-central1", 0.05),
    _spec("aws", "t3a.medium", "us-east-1", 0.05),
]


@pytest.fixture
def built(tmp_path):
    (tmp_path / "tumblebug-cost.json").write_text(
        json.dumps({"_note": "테스트", "specs": TIED, "_source": []}), encoding="utf-8"
    )
    dataset.clear_caches()
    yield tmp_path
    dataset.clear_caches()


@pytest.fixture
def reversed_file(tmp_path):
    """**같은 데이터, 반대 파일 순서.** 답이 달라지면 안 된다."""
    (tmp_path / "tumblebug-cost.json").write_text(
        json.dumps({"_note": "테스트", "specs": TIED[::-1], "_source": []}),
        encoding="utf-8",
    )
    dataset.clear_caches()
    yield tmp_path
    dataset.clear_caches()


def test_ties_do_not_depend_on_file_order(built, reversed_file) -> None:
    """**동점 tie-break가 없으면 덤프의 행 순서가 답을 정한다.**

    `sort_by='vcpu'`는 vCPU만 보므로 2 vCPU짜리 수천 건이 전부 동점이고, 그중 어느
    5개가 나오는지 설명할 수 없었다. 다음 빌드에서 조용히 바뀌기도 한다.
    """
    a = [s["specName"] for s in filter_specs(sort_by="vcpu", limit=99, output_dir=built)]
    b = [
        s["specName"]
        for s in filter_specs(sort_by="vcpu", limit=99, output_dir=reversed_file)
    ]
    assert a == b


def test_tiebreak_order_is_explicable(built) -> None:
    """프로바이더 → 이름 → 리전. 사람이 읽어도 납득이 되는 순서다."""
    names = [s["specName"] for s in filter_specs(sort_by="cost", limit=99, output_dir=built)]
    assert names == ["t3.medium", "t3a.medium", "e2-medium", "S5.MEDIUM4"]


@pytest.mark.parametrize("sort_by", ["cost", "vcpu", "memory"])
def test_every_sort_is_deterministic(built, reversed_file, sort_by) -> None:
    """축마다 따로 tie-break를 붙였으므로 축마다 확인한다."""
    a = [s["id"] for s in filter_specs(sort_by=sort_by, limit=99, output_dir=built)]
    b = [s["id"] for s in filter_specs(sort_by=sort_by, limit=99, output_dir=reversed_file)]
    assert a == b


def test_zero_limit_returns_one_not_none(built) -> None:
    """**빈 목록은 거짓을 말하게 한다.**

    호출부가 "조건을 만족하는 스펙이 데이터셋에 없습니다"라고 답하는데, 데이터가
    없는 것과 0개를 요청받은 것은 다른 일이다.
    """
    assert len(filter_specs(limit=0, output_dir=built)) == 1
    assert len(filter_specs(limit=-5, output_dir=built)) == 1


#: 미러 실측(73,083건)에서 나온 전부. `provider`와 달리 스키마 enum으로 잠그지 않는다 —
#: 소스에서 그대로 오는 값이라 새 값이 빌드를 막으면 안 된다. 대신 여기서 알린다.
KNOWN_ARCHITECTURES = {"x86_64", "arm64", "arm64_mac", "x86_64_mac", None}


def test_no_unknown_architecture_slips_in_silently() -> None:
    """**기본 필터가 x86_64라 모르는 값은 모든 기본 질의에서 조용히 빠진다.**

    memGiB 버그값으로 필터링하다 3,765건을 조용히 떨어뜨린 것과 같은 실패 모양이다.
    새 값이 생기면 여기서 멈추고, 그때 필터 기본값을 어떻게 할지 정하면 된다.

    커밋된 미러(73,083건)를 **직접** 읽는다. `load_specs()`를 쓰면 conftest가
    기본 경로를 빈 디렉터리로 돌려놔서 손 큐레이션 36건만 보게 되고, 그 36건에
    없는 값은 검사되지 않는다 — 검사하려던 상황 자체가 사라진다.
    """
    import gzip
    from pathlib import Path

    packed = Path(__file__).resolve().parent.parent / "data/tumblebug-cost.json.gz"
    if not packed.exists():
        pytest.skip("커밋된 미러 번들이 없습니다")
    with gzip.open(packed, "rt", encoding="utf-8") as fh:
        specs = json.load(fh)["specs"]

    assert len(specs) > 1000, "미러를 읽었어야 한다 (번들 36건이 아니라)"
    found = {spec.get("architecture") for spec in specs}
    assert found <= KNOWN_ARCHITECTURES, f"모르는 아키텍처: {found - KNOWN_ARCHITECTURES}"
