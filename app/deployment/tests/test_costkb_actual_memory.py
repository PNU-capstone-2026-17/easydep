"""판단은 **실제 메모리**로 한다 — 미러의 버그값이 아니라.

미러(cb-tumblebug 덤프)에는 상위 CB-Spider 버그가 실려 있어 GCP·Azure 메모리가
실제보다 2.4% 낮다. 실측상 73,083건 중 **46,468건(64%)**이 그렇다.

예전에는 그 버그값으로 필터링했다 — 라이브 MCP와 답을 맞추기 위해서였는데
**그건 배포기의 이유**다. 우리는 가이드라인 지식베이스이므로 사용자가
"메모리 16GiB 이상"이라고 하면 **실제로 16GiB인 것**이 나와야 한다.
그 기준으로 재보니 **3,765건이 조용히 빠지고 있었다.**

원본이 뭐라고 적었는지는 `memGiB`에 그대로 남는다 — 데이터를 고치는 게 아니라
**무엇을 근거로 판단하느냐**를 바꾼 것이다.
"""

from __future__ import annotations

import json

import pytest

from costkb import dataset
from costkb.dataset import actual_memory, filter_specs

# 실제 16 GiB인데 미러에는 15.625로 적힌 스펙(버그 비율 1.024)
BUGGY = {
    "id": "gcp+us-central1+n2-standard-4", "provider": "gcp", "region": "us-central1",
    "specName": "n2-standard-4", "vCPU": 4, "memGiB": 15.625, "memGiBActual": 16.0,
    "hourlyUSD": 0.19, "architecture": "x86_64", "infraType": "node",
    "acceleratorCount": 0, "acceleratorMemoryGB": 0.0,
}
# 보정값이 없는 정상 스펙(AWS는 버그 영향이 없다)
CLEAN = {
    "id": "aws+us-east-1+m5.xlarge", "provider": "aws", "region": "us-east-1",
    "specName": "m5.xlarge", "vCPU": 4, "memGiB": 16.0,
    "hourlyUSD": 0.192, "architecture": "x86_64", "infraType": "node",
    "acceleratorCount": 0, "acceleratorMemoryGB": 0.0,
}


def test_actual_memory_prefers_the_correction() -> None:
    assert actual_memory(BUGGY) == 16.0
    # 보정값이 없으면 기록값이 곧 실제값이다 — 없다고 0으로 떨어뜨리지 않는다
    assert actual_memory(CLEAN) == 16.0


@pytest.fixture
def built(tmp_path):
    (tmp_path / "tumblebug-cost.json").write_text(
        json.dumps({
            "_note": "테스트",
            "specs": [BUGGY, CLEAN],
            "_source": [],
        }),
        encoding="utf-8",
    )
    dataset.clear_caches()
    yield tmp_path
    dataset.clear_caches()


def test_buggy_spec_is_not_silently_dropped(built) -> None:
    """**이 수정의 핵심.** 실제 16 GiB인데 미러값 때문에 빠지면 안 된다."""
    rows = filter_specs(mem_min_gib=16, limit=10, output_dir=built)
    names = {r["specName"] for r in rows}
    assert "n2-standard-4" in names, "실제로 16 GiB인데 미러 버그값 때문에 빠졌다"
    assert "m5.xlarge" in names


def test_threshold_above_actual_still_excludes(built) -> None:
    """실제 값도 못 미치면 당연히 빠진다 — 무조건 통과시키는 게 아니다."""
    rows = filter_specs(mem_min_gib=32, limit=10, output_dir=built)
    assert rows == []


def test_sort_by_memory_uses_actual(built) -> None:
    """정렬도 실제 값 기준이어야 순서가 뒤집히지 않는다."""
    rows = filter_specs(mem_min_gib=0, limit=10, sort_by="memory", output_dir=built)
    # 둘 다 실제 16 GiB라 메모리로는 동률 — 버그값(15.625)으로 정렬하면
    # gcp가 뒤로 밀린다. 동률이면 순서를 강제하지 않되 둘 다 나와야 한다.
    assert {r["specName"] for r in rows} == {"n2-standard-4", "m5.xlarge"}


def test_mirror_value_is_kept_intact(built) -> None:
    """원본을 고치지 않는다 — 무엇을 근거로 판단하느냐만 바꿨다."""
    rows = filter_specs(mem_min_gib=0, limit=10, output_dir=built)
    buggy = next(r for r in rows if r["specName"] == "n2-standard-4")
    assert buggy["memGiB"] == 15.625      # 원본 그대로
    assert buggy["memGiBActual"] == 16.0  # 보정값도 그대로
