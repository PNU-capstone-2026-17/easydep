"""`details` 파서는 **필드 순서에 기대지 않는다.**

"Go가 필드를 알파벳순으로 찍는다"가 이 파서의 전제인 줄 알았는데, 실측(2026-07-22)
결과 **9개 프로바이더 중 4개에서만 참**이었다. Go의 `%v`는 맵은 키를 정렬하지만
구조체는 선언 순서로 찍는다 — CSP마다 어느 쪽인지가 다르다.

    aws 18,564 · gcp 11,622 · ibm 2,002    100% 정렬됨
    azure 34,846                             0%  ← 우리가 파싱하는 것 중 가장 크다
    tencent · ncp · kt · nhn · openstack     0%

지금 파서는 키마다 따로 정규식을 돌리므로 순서와 무관하다. 그래서 전제가 틀려도
아무 일이 없었다 — **아무 일이 없었다는 게 전제가 맞다는 뜻은 아니다.** 한 번 훑으며
순서대로 뽑는 식으로 "최적화"하면 azure 34,846건이 조용히 깨진다.
"""

from __future__ import annotations

import json

import pytest

from app.deployment.perfkb.parsers.details import DetailsMismatch, go_field, parse_details

# 실측에서 azure가 어긋나는 자리 그대로 — 알파벳 역순이다.
AZURE_LIKE = [
    {"key": "ResourceDiskSizeInMB", "value": "16384"},
    {"key": "MaxResourceVolumeMB", "value": "16384"},
    {"key": "MaxDataDiskCount", "value": "8"},
    {"key": "ACUs", "value": "160"},
]


def _blob(items: list[dict]) -> str:
    return json.dumps(items)


def test_details_is_order_independent() -> None:
    """**핵심 회귀.** 뒤집어도 같은 것이 나와야 한다."""
    forward = parse_details(_blob(AZURE_LIKE))
    backward = parse_details(_blob(AZURE_LIKE[::-1]))
    assert forward == backward
    assert forward["ACUs"] == "160"


def test_azure_ordering_is_not_alphabetical() -> None:
    """전제가 거짓이라는 것 자체를 고정한다 — 픽스처가 조용히 '정리'되지 않게."""
    keys = [item["key"] for item in AZURE_LIKE]
    assert keys != sorted(keys), "이 픽스처는 실측의 어긋난 순서를 재현해야 한다"


def test_go_field_does_not_depend_on_position() -> None:
    """중첩 값이 앞에 있든 뒤에 있든 스칼라를 같게 뽑는다."""
    head = "{EbsOptimizedInfo:{BaselineIops:2000},ThreadsPerCore:2}"
    tail = "{ThreadsPerCore:2,EbsOptimizedInfo:{BaselineIops:2000}}"
    assert go_field(head, "ThreadsPerCore") == go_field(tail, "ThreadsPerCore") == "2"


def test_duplicate_keys_still_fail_loudly_regardless_of_order() -> None:
    """값이 갈리면 순서와 무관하게 크게 실패한다 — 조용한 드리프트 금지."""
    clashing = [
        {"key": "MaxDataDiskCount", "value": "8"},
        {"key": "ACUs", "value": "160"},
        {"key": "MaxDataDiskCount", "value": "16"},
    ]
    for items in (clashing, clashing[::-1]):
        with pytest.raises(DetailsMismatch):
            parse_details(_blob(items))
