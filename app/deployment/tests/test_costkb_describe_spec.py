"""이름으로 스펙 하나를 조회한다 (`cost_describe_spec`).

**데이터는 처음부터 있었고 표면이 없었을 뿐이다.** 조건 필터(`filter_specs`)만
있고 이름 조회가 없어서 "n2-highmem-8 메모리 몇 GiB?"가 실측에서 **0/3 실패**하고
web_search로 3/3 샜다. 도구 호출이 11회까지 난 적도 있다.

이 프로젝트에서 반복해 나온 실패 모양이다 — 데이터는 있는데 에이전트가 못 닿는다.
"""

from __future__ import annotations

import json

import pytest

from costkb import dataset
from costkb.agent_api import describe_spec
from costkb.dataset import find_by_name, name_suggestions

GCP = {
    "id": "gcp+us-central1+n2-highmem-8", "provider": "gcp", "region": "us-central1",
    "specName": "n2-highmem-8", "vCPU": 8, "memGiB": 64.0, "hourlyUSD": 0.5241,
    "architecture": "x86_64", "infraType": "node",
    "acceleratorCount": 0, "acceleratorMemoryGB": 0.0,
}
GCP_OTHER_REGION = {**GCP, "id": "gcp+asia-east1+n2-highmem-8",
                    "region": "asia-east1", "hourlyUSD": 0.8385}
# 이름이 겹치는 실제 사례 — aws와 openstack의 m1.*
AWS_M1 = {
    "id": "aws+us-east-1+m1.small", "provider": "aws", "region": "us-east-1",
    "specName": "m1.small", "vCPU": 1, "memGiB": 1.7, "hourlyUSD": None,
    "architecture": "x86_64", "infraType": "node",
    "acceleratorCount": 0, "acceleratorMemoryGB": 0.0,
}
OS_M1 = {**AWS_M1, "id": "openstack+regionone+m1.small", "provider": "openstack",
         "region": "regionone", "memGiB": 2.0}


@pytest.fixture
def built(tmp_path):
    (tmp_path / "tumblebug-cost.json").write_text(
        json.dumps({
            "_note": "테스트",
            "specs": [GCP, GCP_OTHER_REGION, AWS_M1, OS_M1],
            "_source": [],
        }),
        encoding="utf-8",
    )
    dataset.clear_caches()
    yield tmp_path
    dataset.clear_caches()


def test_find_by_name_returns_every_region(built) -> None:
    rows = find_by_name("n2-highmem-8", output_dir=built)
    assert {r["region"] for r in rows} == {"us-central1", "asia-east1"}


def test_lookup_is_case_insensitive(built) -> None:
    assert find_by_name("N2-HIGHMEM-8", output_dir=built)


def test_answer_carries_the_core_facts(built) -> None:
    """묻는 것이 곧 이것이다 — vCPU와 메모리."""
    text = describe_spec("n2-highmem-8", output_dir=built)
    assert "8" in text and "64 GiB" in text


def test_price_range_not_a_single_number(built) -> None:
    """리전마다 단가가 다르다 — 하나만 고르면 그게 대표값인 양 읽힌다."""
    text = describe_spec("n2-highmem-8", output_dir=built)
    assert "0.5241" in text and "0.8385" in text
    assert "리전마다 다름" in text


def test_specific_region_narrows(built) -> None:
    text = describe_spec("n2-highmem-8", region="asia-east1", output_dir=built)
    assert "0.8385" in text
    assert "0.5241" not in text


def test_missing_region_is_told_apart(built) -> None:
    """그 리전에 없는 것과 스펙이 없는 것은 다른 답이다."""
    text = describe_spec("n2-highmem-8", region="eu-west-9", output_dir=built)
    assert "리전에는 없습니다" in text
    assert "asia-east1" in text  # 있는 리전을 알려준다


def test_name_collision_shows_both(built) -> None:
    """`m1.small`은 aws와 openstack에 다 있다 — 하나를 고르지 않는다."""
    text = describe_spec("m1.small", output_dir=built)
    assert "AWS" in text and "OPENSTACK" in text
    # 메모리가 서로 다르다는 것이 정보다
    assert "1.7 GiB" in text and "2 GiB" in text


def test_provider_disambiguates(built) -> None:
    text = describe_spec("m1.small", provider="openstack", output_dir=built)
    assert "OPENSTACK" in text and "AWS" not in text


def test_unknown_name_suggests_and_does_not_deny(built) -> None:
    """**없다고 단정하지 않는다** — 우리 데이터셋에 없을 뿐이다."""
    text = describe_spec("n2-highmem-oops", output_dir=built)
    assert "이 데이터셋에 없다" in text
    assert "n2-highmem-8" in text  # 비슷한 이름 제안


def test_suggestions_are_close_matches(built) -> None:
    assert "n2-highmem-8" in name_suggestions("n2-highmem-9", output_dir=built)


def test_unpriced_spec_says_so(built) -> None:
    """가격 없음을 0으로 떨어뜨리지 않는다."""
    text = describe_spec("m1.small", provider="aws", output_dir=built)
    assert "가격 정보 없음" in text
