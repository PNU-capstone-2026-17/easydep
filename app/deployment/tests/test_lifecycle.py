"""관리형 서비스 수명주기 (endoflife.date).

여기서 지켜야 하는 것:
- **본문을 읽지 않는다.** 저장소는 MIT이지만 본문 산문은 CC BY-SA다.
  frontmatter만 파싱하는 것이 라이선스 경계다.
- **`false`는 날짜가 아니다.** endoflife.date는 "종료일 미정"을 `false`로 적는다.
  이걸 날짜로 읽으면 "미정"이 "종료됨"으로 뒤집힌다.
- **연장 지원을 빠뜨리지 않는다.** 아직 쓸 수 있는 버전을 못 쓴다고 답하게 된다.
"""

from __future__ import annotations

import json

import pytest

from app.deployment.envkb import lifecycle


def flat(text: str) -> str:
    """줄바꿈·들여쓰기를 공백 하나로 눌러 문구 대조를 줄나눔에서 독립시킨다."""
    return " ".join(text.split())


PRODUCT_MD = """---
title: Amazon EKS
category: service
releases:
  - releaseCycle: "1.33"
    releaseDate: 2026-01-15
    eol: 2027-07-29
    latest: "1.33-eks-41"
  - releaseCycle: "1.30"
    releaseDate: 2024-05-23
    eol: 2025-07-23
    eoes: 2026-07-23
  - releaseCycle: "9.9"
    releaseDate: 2026-07-01
    eol: false
---

> Amazon EKS is a managed service. 이 문장은 CC BY-SA라 **담으면 안 된다**.
> secret-sauce-do-not-copy
"""


def test_body_prose_is_not_read() -> None:
    """라이선스 경계 — 본문이 산출물에 새어 들면 안 된다."""
    record = lifecycle.parse_product("amazon-eks", PRODUCT_MD, None)
    assert "secret-sauce-do-not-copy" not in json.dumps(record, ensure_ascii=False)
    assert record["title"] == "Amazon EKS"


def test_false_eol_is_not_a_date() -> None:
    """`eol: false`는 '미정'이지 '종료됨'이 아니다."""
    record = lifecycle.parse_product("amazon-eks", PRODUCT_MD, None)
    undated = next(r for r in record["releases"] if r["cycle"] == "9.9")
    assert undated["eol"] is None
    assert lifecycle._status(undated, "2026-07-22") == "end date undetermined"


def test_extended_support_is_reported() -> None:
    """연장 지원이 남아 있으면 그걸 밝힌다."""
    record = lifecycle.parse_product("amazon-eks", PRODUCT_MD, None)
    ended = next(r for r in record["releases"] if r["cycle"] == "1.30")
    text = lifecycle._status(ended, "2026-01-01")
    assert "extended support until 2026-07-23" in flat(text)


def test_status_flips_with_today() -> None:
    """오늘이 지나면 '지원'에서 '종료'로 바뀐다."""
    record = lifecycle.parse_product("amazon-eks", PRODUCT_MD, None)
    live = next(r for r in record["releases"] if r["cycle"] == "1.33")
    assert "supported until 2027-07-29" in flat(lifecycle._status(live, "2026-07-22"))
    assert "support ended" in flat(lifecycle._status(live, "2028-01-01"))


@pytest.fixture
def built(tmp_path):
    record = lifecycle.parse_product(
        "amazon-eks", PRODUCT_MD, "aws::AWS::EKS::Cluster"
    )
    (tmp_path / "service-lifecycle.json").write_text(
        json.dumps({"products": [record], "_source": []}), encoding="utf-8"
    )
    lifecycle._load.cache_clear()
    yield tmp_path
    lifecycle._load.cache_clear()


def test_lookup_by_resource_type(built) -> None:
    """제품 이름을 몰라도 리소스 타입으로 물을 수 있어야 한다."""
    for query in ("aws::AWS::EKS::Cluster", "AWS::EKS::Cluster", "amazon-eks"):
        assert lifecycle.find(query, output_dir=str(built)), query


def test_unknown_service_is_not_claimed_endless(built) -> None:
    """수록 안 된 것을 '종료일 없음'으로 답하면 안 된다."""
    text = lifecycle.describe("cassandra", output_dir=str(built))
    assert "it is not in this source" in flat(text)


def test_unknown_version_lists_known_ones(built) -> None:
    text = lifecycle.describe("amazon-eks", "0.1", output_dir=str(built))
    assert "version '0.1' not found" in flat(text)
    assert "1.33" in text


def test_missing_artifact_guides_to_build(tmp_path) -> None:
    lifecycle._load.cache_clear()
    assert "build-lifecycle" in lifecycle.describe("amazon-eks", output_dir=str(tmp_path))
    lifecycle._load.cache_clear()


def test_hand_mapping_is_verified_against_graph(tmp_path) -> None:
    """손매핑한 타입이 graphkb에 없으면 빌드가 알려야 한다."""
    (tmp_path / "x-graph.json").write_text(
        json.dumps({"nodes": [{"id": "aws::AWS::EKS::Cluster"}]}), encoding="utf-8"
    )
    products = [
        {"product": "a", "type_id": "aws::AWS::EKS::Cluster", "releases": []},
        {"product": "b", "type_id": "aws::AWS::Nope::Nope", "releases": []},
    ]
    assert lifecycle._check_types(products, tmp_path) == ["aws::AWS::Nope::Nope"]
