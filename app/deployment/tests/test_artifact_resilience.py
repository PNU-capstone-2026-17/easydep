"""산출물이 깨졌을 때 KB가 계속 동작하는가 (결함 C2·(마) 회귀).

예전 실패 모드:

1. 빌드가 **무검증**으로 써서, 상위 스키마가 드리프트하면(새 프로바이더 `oracle`)
   ⚠️ 한 줄만 찍고 exit 0으로 끝나며 깨진 파일을 남겼다. 이후 모든 로드가
   `ValidationError`를 던져 **`output/`를 손으로 지우기 전까지 복구 불가**였다.
2. 컬럼 리네임으로 전 행이 무효가 되면 `specs: []` → `priced / len(specs)`에서
   **ZeroDivisionError**. 파일은 이미 기록된 뒤였다.
3. perfkb 빌드가 쓰다 끊기면 잘린 JSON이 남고, 그 순간부터 성능 도구가 아니라
   **비용 추천 전체가 죽었다**((마)).
"""

from __future__ import annotations

import json

import pytest

from costkb import agent_api as cost_api
from costkb import dataset as cost_dataset
from kbcommon.artifact import ArtifactInvalid, read_dataset, write_dataset

_SCHEMA = {
    "type": "object",
    "required": ["specs"],
    "properties": {"specs": {"type": "array", "minItems": 1}},
}


# --- kbcommon/artifact: 쓰기 ---


def test_invalid_dataset_is_never_written(tmp_path) -> None:
    """검증 실패면 파일을 **만들지 않는다** — 나쁜 산출물보다 없는 게 낫다."""
    out = tmp_path / "ds.json"
    with pytest.raises(ArtifactInvalid):
        write_dataset(out, {"specs": []}, _SCHEMA)
    assert not out.exists()
    assert not (tmp_path / "ds.json.part").exists()  # 임시 파일도 안 남는다


def test_invalid_build_leaves_previous_artifact_intact(tmp_path) -> None:
    """기존 산출물이 있으면 실패한 빌드가 그걸 덮지 않는다."""
    out = tmp_path / "ds.json"
    write_dataset(out, {"specs": [{"id": "good"}]}, _SCHEMA)
    with pytest.raises(ArtifactInvalid):
        write_dataset(out, {"specs": []}, _SCHEMA)
    assert json.loads(out.read_text(encoding="utf-8"))["specs"] == [{"id": "good"}]


def test_write_error_message_points_at_the_field(tmp_path) -> None:
    """어디가 틀렸는지 안 알려주면 빌드 로그만 보고는 못 고친다."""
    with pytest.raises(ArtifactInvalid, match="specs"):
        write_dataset(tmp_path / "ds.json", {"specs": []}, _SCHEMA)


# --- kbcommon/artifact: 읽기 ---


def test_missing_file_is_not_an_error(tmp_path) -> None:
    """부재는 사건이 아니다 — 아직 빌드 안 한 것뿐."""
    assert read_dataset(tmp_path / "nope.json", _SCHEMA) == (None, None)


@pytest.mark.parametrize(
    ("content", "hint"),
    [('{"specs": [1], ', "JSON"), ('{"specs": []}', "스키마")],
    ids=["truncated", "schema-violation"],
)
def test_damaged_file_reports_instead_of_raising(tmp_path, content, hint) -> None:
    path = tmp_path / "ds.json"
    path.write_text(content, encoding="utf-8")
    data, error = read_dataset(path, _SCHEMA)
    assert data is None
    assert error is not None and hint in error


# --- costkb: 깨진 산출물 → 번들 폴백 + 고지 ---


@pytest.fixture()
def poisoned_build(tmp_path, monkeypatch):
    """스키마에 없는 프로바이더가 들어간 산출물 (C2의 실제 시나리오)."""
    (tmp_path / "tumblebug-cost.json").write_text(
        json.dumps(
            {
                "_note": "오염된 빌드",
                "specs": [
                    {
                        "provider": "oracle",  # enum에 없다
                        "region": "us-ashburn-1",
                        "specName": "VM.Standard.E4",
                        "vCPU": 2,
                        "memGiB": 4,
                        "hourlyUSD": 0.05,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cost_dataset, "DEFAULT_OUTPUT_DIR", tmp_path)
    cost_dataset.clear_caches()
    yield
    cost_dataset.clear_caches()


def test_poisoned_build_falls_back_to_bundle(poisoned_build) -> None:
    """**C2 회귀**: 예외 대신 번들 36건으로 답한다."""
    specs = cost_dataset.load_specs()
    assert len(specs) == 36
    assert cost_dataset.is_built() is False  # 파일은 있지만 쓰고 있지 않다


def test_poisoned_build_is_disclosed_in_the_answer(poisoned_build) -> None:
    """조용한 폴백은 "커버리지가 왜 좁아졌지?"를 미궁으로 만든다."""
    warning = cost_dataset.load_warning()
    assert warning is not None and "번들" in warning

    text = cost_api.recommend_specs(vcpu_min=2, mem_min_gib=4, provider="aws", limit=2)
    assert "번들" in text and "costkb build" in text


def test_truncated_build_also_falls_back(tmp_path, monkeypatch) -> None:
    """쓰다 만 파일 — 빌드가 중간에 끊긴 경우."""
    (tmp_path / "tumblebug-cost.json").write_text('{"_note": "잘림", "spe', encoding="utf-8")
    monkeypatch.setattr(cost_dataset, "DEFAULT_OUTPUT_DIR", tmp_path)
    cost_dataset.clear_caches()
    try:
        assert len(cost_dataset.load_specs()) == 36
        assert cost_dataset.load_warning() is not None
    finally:
        cost_dataset.clear_caches()
