"""메모리는 **빌드가 고쳐서 담는다** — `memGiB`가 곧 실제 값이다.

상류 CB-Spider 버그로 GCP·Azure 메모리가 실제보다 2.4% 낮게 기록된다
(73,083건 중 46,468건). 처리 방식이 두 번 바뀌었다:

1. 처음엔 원본을 그대로 담고 `memGiBActual`에 보정값을 병기했다 — 라이브 MCP와
   필터 결과를 맞추려던 **배포기의 이유**다. 그 탓에 표시는 보정값, 필터는 버그값을
   써서 "16 GiB 이상"에서 실제로는 만족하는 3,765건이 조용히 빠졌다.
2. 지금은 **빌드가 고쳐서 담는다.** 값 하나를 두 칸에 나눠 두면 어느 쪽으로
   판단하느냐가 자리마다 갈리기 때문이다. 무엇을 어떻게 고쳤는지는 데이터셋
   메타데이터(`_corrections`)에 규칙으로 남고, 그 식으로 원본을 되돌릴 수 있다.
"""

from __future__ import annotations

import json

import pytest

from app.deployment.costkb import dataset
from app.deployment.costkb.dataset import filter_specs
from app.deployment.costkb.parsers.tumblebug import CORRECTIONS, correct_memory

# 원본 15.625 (실제 16 GiB) — 상류 버그의 전형적인 지문
BUGGY_ROW_MEM = 15.625


def test_correction_restores_the_real_value() -> None:
    assert correct_memory("gcp", BUGGY_ROW_MEM) == 16.0
    assert correct_memory("azure", 62.5) == 64.0
    # 영향 없는 프로바이더는 건드리지 않는다 — 실측으로 정상이 확인된 곳이다
    assert correct_memory("aws", 16.0) == 16.0
    assert correct_memory("tencent", 15.625) == 15.625


def test_corrections_are_recorded_as_metadata() -> None:
    """**값마다 칸을 늘리는 대신 규칙을 한 곳에 적는다.**

    이 식이 곧 원본 복원식이라, 원본이 필요한 사람은 되돌릴 수 있다.
    """
    entry = next(c for c in CORRECTIONS if c["field"] == "memGiB")
    assert set(entry["providers"]) == {"azure", "gcp"}
    assert "1.024" in entry["operation"]
    assert "To get back to the original" in entry["reason"]


CORRECTED = {
    "id": "gcp+us-central1+n2-standard-4", "provider": "gcp", "region": "us-central1",
    "specName": "n2-standard-4", "vCPU": 4, "memGiB": 16.0,
    "hourlyUSD": 0.19, "architecture": "x86_64", "infraType": "node",
    "acceleratorCount": 0, "acceleratorMemoryGB": 0.0,
}
CLEAN = {
    "id": "aws+us-east-1+m5.xlarge", "provider": "aws", "region": "us-east-1",
    "specName": "m5.xlarge", "vCPU": 4, "memGiB": 16.0,
    "hourlyUSD": 0.192, "architecture": "x86_64", "infraType": "node",
    "acceleratorCount": 0, "acceleratorMemoryGB": 0.0,
}


@pytest.fixture
def built(tmp_path):
    (tmp_path / "tumblebug-cost.json").write_text(
        json.dumps({
            "_note": "테스트",
            "_corrections": CORRECTIONS,
            "specs": [CORRECTED, CLEAN],
            "_source": [],
        }),
        encoding="utf-8",
    )
    dataset.clear_caches()
    yield tmp_path
    dataset.clear_caches()


def test_corrected_spec_is_not_silently_dropped(built) -> None:
    """**이 계약의 핵심.** 실제 16 GiB면 "16 GiB 이상"에 나와야 한다."""
    rows = filter_specs(mem_min_gib=16, limit=10, output_dir=built)
    assert {r["specName"] for r in rows} == {"n2-standard-4", "m5.xlarge"}


def test_threshold_above_actual_still_excludes(built) -> None:
    """무조건 통과시키는 게 아니다 — 실제 값도 못 미치면 빠진다."""
    assert filter_specs(mem_min_gib=32, limit=10, output_dir=built) == []


def test_no_split_memory_field_remains(built) -> None:
    """`memGiBActual`은 없어졌다 — 값 하나에 칸 하나다.

    두 칸으로 나눠 두면 표시·필터·정렬이 서로 다른 칸을 보게 되고, 실제로 그렇게
    갈려서 답이 자리마다 달랐다.
    """
    rows = filter_specs(mem_min_gib=0, limit=10, output_dir=built)
    assert all("memGiBActual" not in r for r in rows)


def test_schema_rejects_the_old_field() -> None:
    """낡은 산출물이 조용히 섞이지 않도록 스키마가 막는다."""
    import jsonschema

    bad = {
        "_note": "테스트",
        "specs": [{**CORRECTED, "memGiBActual": 16.0}],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, dataset.schema())
