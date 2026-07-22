"""GCP 스팟·약정 보강 (Cyclenerd, gcp_pricing.py).

이 축에서 지켜야 하는 것:
- **미러를 안 건드린다.** 온디맨드는 tumblebug 그대로. 스팟·약정만 별도 파일.
- **조인되는 것만 담는다.** 미러에 없는 스펙·리전은 답할 길이 없다.
- **스냅샷 괴리를 기록한다.** Cyclenerd 온디맨드가 미러와 5% 넘게 다르면 표시하고,
  그 사실이 답변에 실린다 — 두 스냅샷을 조용히 섞으면 안 된다.
"""

from __future__ import annotations

import json

import pytest

from costkb import dataset
from costkb.agent_api import discount_pricing
from costkb.parsers.gcp_pricing import build_records

PRICING = {
    "compute": {
        "instance": {
            "e2-standard-4": {
                "cost": {
                    "asia-northeast3": {
                        "hour": 0.17193, "hour_spot": 0.064728,
                        "month_1y": 79.07, "month_3y": 56.48,
                    },
                    "us-central1": {
                        "hour": 0.13401, "hour_spot": 0.04, "month_1y": 60.0, "month_3y": 40.0,
                    },
                }
            },
            "n2d-standard-4": {
                "cost": {
                    # 미러 온디맨드(0.268)와 2.4배 차이 → 스냅샷 괴리
                    "asia-south1": {
                        "hour": 0.11153, "hour_spot": 0.0555,
                        "month_1y": 51.30, "month_3y": 36.64,
                    }
                }
            },
        }
    }
}

MIRROR = [
    {"provider": "gcp", "specName": "e2-standard-4", "region": "asia-northeast3",
     "hourlyUSD": 0.17193},
    {"provider": "gcp", "specName": "n2d-standard-4", "region": "asia-south1",
     "hourlyUSD": 0.26824},  # Cyclenerd 0.11153의 2.4배
    # 미러에 있으나 Cyclenerd에 없는 스펙 — 안 담겨야 한다
    {"provider": "gcp", "specName": "z3-highmem-88", "region": "us-east1",
     "hourlyUSD": 5.0},
    # AWS 스펙 — GCP 보강이 건드리면 안 된다
    {"provider": "aws", "specName": "m5.large", "region": "us-east-1", "hourlyUSD": 0.096},
]


def test_only_gcp_and_joined_are_kept() -> None:
    records, stats = build_records(PRICING, MIRROR)
    keys = {(r["specName"], r["region"]) for r in records}
    # 미러를 순회하므로 미러에 있는 조합만 담긴다. Cyclenerd에 e2/us-central1이
    # 있어도 미러에 그 조합이 없으면 안 담긴다 — 답할 길이 없기 때문이다.
    assert keys == {
        ("e2-standard-4", "asia-northeast3"),
        ("n2d-standard-4", "asia-south1"),
    }
    assert all(r["provider"] == "gcp" for r in records)
    assert stats["missing_spec"] == 1  # z3-highmem-88


def test_ondemand_stays_in_mirror_not_here() -> None:
    """이 산출물엔 온디맨드가 답으로 실리지 않는다 — 미러의 것을 쓴다.

    hourRefUSD는 Cyclenerd 자신의 온디맨드(스팟 기준)일 뿐, 미러를 덮지 않는다.
    """
    records, _ = build_records(PRICING, MIRROR)
    rec = next(r for r in records if r["specName"] == "e2-standard-4"
               and r["region"] == "asia-northeast3")
    assert "hourlyUSD" not in rec  # 온디맨드 답을 여기 담지 않는다
    assert rec["hourRefUSD"] == 0.17193  # Cyclenerd 온디맨드(참조용)


def test_snapshot_divergence_is_flagged() -> None:
    """미러와 5% 넘게 다른 온디맨드는 표시된다 — 조용히 섞으면 안 된다."""
    records, stats = build_records(PRICING, MIRROR)
    aligned = next(r for r in records if r["region"] == "asia-northeast3")
    diverged = next(r for r in records if r["region"] == "asia-south1")
    assert aligned["snapshotMatchesMirror"] is True
    assert diverged["snapshotMatchesMirror"] is False
    assert diverged["mirrorRatio"] == pytest.approx(2.405, abs=0.01)
    assert stats["snapshot_diverged"] == 1


@pytest.fixture
def built(tmp_path):
    """스팟·약정 산출물을 임시 디렉터리에 쓰고 캐시를 비운다."""
    records, _ = build_records(PRICING, MIRROR)
    (tmp_path / "gcp-spot-commit.json").write_text(
        json.dumps({"records": records, "_source": []}), encoding="utf-8"
    )
    dataset.clear_caches()
    yield tmp_path
    dataset.clear_caches()


def test_api_shows_spot_and_commitment(built) -> None:
    text = discount_pricing("e2-standard-4", "asia-northeast3", output_dir=built)
    assert "스팟 $0.0647/h" in text
    assert "1년 약정" in text and "3년 약정" in text
    assert "Cyclenerd" in text  # 출처를 밝힌다


def test_api_warns_on_diverged_region(built) -> None:
    text = discount_pricing("n2d-standard-4", "asia-south1", output_dir=built)
    assert "기준 온디맨드" in text
    assert "스냅샷 시점 차이" in text


def test_api_distinguishes_untracked_provider(built) -> None:
    """AWS 스펙엔 '없음'이 아니라 'GCP 전용 미수록'이라고 답한다."""
    text = discount_pricing("m5.large", output_dir=built)
    assert "GCP 전용" in text


def test_api_lists_regions_when_region_unknown(built) -> None:
    text = discount_pricing("e2-standard-4", "nonexistent-region", output_dir=built)
    assert "정보가 없습니다" in text
    assert "asia-northeast3" in text  # 가진 리전을 안내
