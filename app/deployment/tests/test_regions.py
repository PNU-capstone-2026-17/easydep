"""서비스 엔드포인트 소재 (botocore endpoints.json).

이 축에서 지켜야 하는 것은 **"없음"을 단언하지 않는 것**이다. 원본에 글로벌
서비스 판별자가 서비스 307개 중 22개에만 있어서, 엔드포인트가 없다는 사실만으로는
"그 리전에서 못 쓴다"를 말할 수 없다. 그 경계가 무너지는지 여기서 잡는다.

**리전 이름 해석은 여기가 아니다** — 그건 프로바이더 10곳을 아는 별도 소스
(cb-tumblebug cloudinfo)로 옮겼다. `tests/test_cloud_regions.py` 참조.
botocore는 AWS만 주므로 리전 *이름*의 주 소스가 될 수 없었다.
"""

from __future__ import annotations

import json

import pytest

from app.deployment.capacitykb import agent_api
from app.deployment.capacitykb.parsers import aws_endpoints


def flat(text: str) -> str:
    """줄바꿈·들여쓰기를 공백 하나로 눌러 문구 대조를 줄나눔에서 독립시킨다."""
    return " ".join(text.split())


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

    agent_api._endpoints.cache_clear()
    yield tmp_path
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


def test_global_flag_is_none_when_source_is_silent(built):
    """원본이 말 안 한 것을 False로 적으면 우리가 짐작한 게 된다."""
    services = json.loads(
        (built / "aws-endpoints.json").read_text(encoding="utf-8")
    )["services"]["aws"]
    assert services["cloudfront"]["global"] is True
    assert services["devicefarm"]["global"] is None


# --- 서비스 소재 ----------------------------------------------------------

def test_presence_is_stated_absence_is_not(built):
    """엔드포인트가 하나뿐이어도 '거기서만 된다'고 말하지 않는다."""
    text = flat(agent_api.where_available("devicefarm", output_dir=built))
    assert "us-east-1" in text
    assert "**this data does not know**" in text
    assert "unusable" not in text.replace("is not 'unusable'", "")


def test_global_service_is_not_reported_as_one_region(built):
    """CloudFront를 'us-east-1에서만 됩니다'로 답하면 확신에 찬 오답이다."""
    text = flat(agent_api.where_available("cloudfront", output_dir=built))
    assert "a **global service**" in text
    assert "us-east-1" not in text


def test_cfn_type_joins_by_hyphen_normalisation(built):
    """`acmpca` → `acm-pca`. 실측으로 충돌이 0건이라 안전한 정규화다."""
    text = agent_api.where_available("aws::AWS::ACMPCA::CertificateAuthority", output_dir=built)
    # 답의 머리가 붙인 서비스 id다. 영어가 되며 구분자가 `:` → `—`로 바뀌었다.
    assert flat(text).startswith("acm-pca — ")


def test_unmappable_service_refuses_instead_of_guessing(built):
    """`cloudwatch`는 원본에서 `monitoring`이다 — 규칙으로 못 맞힌다.

    짐작으로 붙이면 **엉뚱한 서비스의 리전을 자신 있게 답하게 된다.**
    """
    text = flat(agent_api.where_available("AWS::CloudWatch::Alarm", output_dir=built))
    assert "our data cannot pin down which AWS SDK service" in text
    assert "monitoring" in text  # 사용자가 다시 물을 수 있도록 알려는 준다
