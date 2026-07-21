"""리전 해석 + 서비스 엔드포인트 소재 (botocore endpoints.json).

이 축에서 지켜야 하는 것은 **"없음"을 단언하지 않는 것**이다. 원본에 글로벌
서비스 판별자가 서비스 307개 중 22개에만 있어서, 엔드포인트가 없다는 사실만으로는
"그 리전에서 못 쓴다"를 말할 수 없다. 그 경계가 무너지는지 여기서 잡는다.
"""

from __future__ import annotations

import json

import pytest

from capacitykb import agent_api
from capacitykb.parsers import aws_endpoints
from kbcommon import regions as regions_mod

RAW = {
    "partitions": [
        {
            "partition": "aws",
            "partitionName": "AWS Standard",
            "regions": {
                "ap-northeast-2": {"description": "Asia Pacific (Seoul)"},
                "us-east-1": {"description": "US East (N. Virginia)"},
                "eusc-de-east-1": {"description": "AWS European Sovereign Cloud (Germany)"},
            },
            "services": {
                "ec2": {
                    "endpoints": {
                        "ap-northeast-2": {},
                        "us-east-1": {},
                        # 리전이 아니라 같은 리전의 다른 접속점이다
                        "fips-us-east-1": {},
                    }
                },
                "cloudfront": {
                    "isRegionalized": False,
                    "partitionEndpoint": "aws-global",
                    "endpoints": {"aws-global": {}},
                },
                # 좁은데 판별자가 없다 — 글로벌인지 리전 제한인지 알 수 없는 부류
                "devicefarm": {"endpoints": {"us-east-1": {}}},
                "acm-pca": {"endpoints": {"us-east-1": {}}},
            },
        }
    ]
}


@pytest.fixture
def built(tmp_path, monkeypatch):
    """산출물을 임시 디렉터리에 만들고 캐시를 비운다."""
    raw = tmp_path / "endpoints.json"
    raw.write_text(json.dumps(RAW), encoding="utf-8")
    out = tmp_path / "aws-endpoints.json"

    monkeypatch.setattr(
        aws_endpoints, "fetch_cached", lambda url, name, **kw: raw
    )
    aws_endpoints.build(out)

    regions_mod._catalog.cache_clear()
    agent_api._endpoints.cache_clear()
    yield tmp_path
    regions_mod._catalog.cache_clear()
    agent_api._endpoints.cache_clear()


# --- 파서 -----------------------------------------------------------------

def test_pseudo_regions_are_dropped(built):
    """`fips-us-east-1`은 리전이 아니다 — 담으면 리전 수가 부풀려진다."""
    data = json.loads((built / "aws-endpoints.json").read_text(encoding="utf-8"))
    assert data["partitions"]["aws"]["regions"].keys() == {
        "ap-northeast-2",
        "us-east-1",
        "eusc-de-east-1",
    }
    assert data["services"]["aws"]["ec2"]["regions"] == ["ap-northeast-2", "us-east-1"]
    # `fips-us-east-1`(같은 리전의 다른 접속점)과 `aws-global`(가짜 리전) 둘 다.
    assert data["dropped_pseudo_regions"] == 2


def test_four_letter_region_survives(built):
    """`eusc-de-east-1`은 진짜 리전이다.

    처음엔 `us-east-1` 꼴 정규식으로 걸렀는데 앞이 두 글자라고 짐작한 탓에
    이 리전을 버리고 있었다. 리전 판정은 원본의 `regions` 선언에 맡긴다.
    """
    assert regions_mod.name_of("eusc-de-east-1", output_dir=str(built)) is not None


def test_global_flag_is_none_when_source_is_silent(built):
    """원본이 말 안 한 것을 False로 적으면 우리가 짐작한 게 된다."""
    services = json.loads(
        (built / "aws-endpoints.json").read_text(encoding="utf-8")
    )["services"]["aws"]
    assert services["cloudfront"]["global"] is True
    assert services["devicefarm"]["global"] is None


# --- 리전 해석 ------------------------------------------------------------

def test_korean_place_resolves(built):
    """실측에서 못 답했던 바로 그 실패 — '서울'이 색인 키가 되지 못했다."""
    found = regions_mod.resolve_region("서울", output_dir=str(built))
    assert [r.code for r in found] == ["ap-northeast-2"]
    assert found[0].matched_by == "alias"


def test_korean_alias_matches_inside_a_sentence(built):
    """한국어는 조사가 붙는다 — '서울에서'도 걸려야 한다."""
    found = regions_mod.resolve_region(
        "서울에서 GPU 인스턴스 쓸 수 있어?", output_dir=str(built)
    )
    assert [r.code for r in found] == ["ap-northeast-2"]


def test_source_provided_name_is_marked_apart_from_our_translation(built):
    """원본이 말한 것과 우리가 더한 번역을 같은 무게로 말하면 안 된다."""
    assert regions_mod.resolve_region("Seoul", output_dir=str(built))[0].matched_by == "name"
    assert regions_mod.resolve_region("서울", output_dir=str(built))[0].matched_by == "alias"
    assert (
        regions_mod.resolve_region("ap-northeast-2", output_dir=str(built))[0].matched_by
        == "code"
    )


def test_unknown_place_is_not_claimed_absent(built):
    """못 알아들은 것을 '그런 리전 없다'로 답하면 안 된다."""
    assert regions_mod.resolve_region("화성", output_dir=str(built)) == []
    text = agent_api.region_lookup("화성", output_dir=built)
    assert "못 알아들었다" in text
    assert "없다는 뜻이 아니라" in text


def test_missing_artifact_guides_to_build(tmp_path):
    regions_mod._catalog.cache_clear()
    agent_api._endpoints.cache_clear()
    assert "build --source aws-endpoints" in agent_api.region_lookup("서울", output_dir=tmp_path)
    regions_mod._catalog.cache_clear()
    agent_api._endpoints.cache_clear()


# --- 서비스 소재 ----------------------------------------------------------

def test_presence_is_stated_absence_is_not(built):
    """엔드포인트가 하나뿐이어도 '거기서만 된다'고 말하지 않는다."""
    text = agent_api.where_available("devicefarm", output_dir=built)
    assert "us-east-1" in text
    assert "모른다" in text
    assert "못 쓴다" not in text.replace("'못 쓴다'가 아니라", "")


def test_global_service_is_not_reported_as_one_region(built):
    """CloudFront를 'us-east-1에서만 됩니다'로 답하면 확신에 찬 오답이다."""
    text = agent_api.where_available("cloudfront", output_dir=built)
    assert "글로벌" in text
    assert "us-east-1" not in text


def test_cfn_type_joins_by_hyphen_normalisation(built):
    """`acmpca` → `acm-pca`. 실측으로 충돌이 0건이라 안전한 정규화다."""
    text = agent_api.where_available("aws::AWS::ACMPCA::CertificateAuthority", output_dir=built)
    assert text.startswith("acm-pca:")


def test_unmappable_service_refuses_instead_of_guessing(built):
    """`cloudwatch`는 원본에서 `monitoring`이다 — 규칙으로 못 맞힌다.

    짐작으로 붙이면 **엉뚱한 서비스의 리전을 자신 있게 답하게 된다.**
    """
    text = agent_api.where_available("AWS::CloudWatch::Alarm", output_dir=built)
    assert "확정할 수 없습니다" in text
    assert "monitoring" in text  # 사용자가 다시 물을 수 있도록 알려는 준다
