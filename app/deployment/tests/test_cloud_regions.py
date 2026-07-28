"""프로바이더별 리전 이름 (cb-tumblebug cloudinfo.yaml).

여기서 지켜야 하는 것:
- **서울이 프로바이더마다 다르다**는 사실이 답에 드러난다. 하나로 좁혀 주면
  우리가 고른 것이 사용자가 뜻한 것인 양 보인다.
- **대소문자.** 원본은 `KR1`, 미러는 `kr1`이다. 조인 키만 소문자로 맞추고
  원본 표기는 남긴다 — 원본을 고쳐 쓰면 그건 우리 값이 된다.
- **못 찾은 것을 없다고 하지 않는다.** Azure는 방위 이름(`Southeast Asia`)을
  쓰기 때문에, 실제로 있는데 이름이 달라 못 찾는 경우가 있다.
"""

from __future__ import annotations

import json

import pytest

from app.deployment.envkb import cloudinfo
from app.deployment.envkb import regions as regions_mod
from app.deployment.tests._helpers import flat

RAW = {
    "cloud": {
        "aws": {
            "description": "Amazon Web Services",
            "region": {
                "ap-northeast-2": {
                    "description": "Asia Pacific (Seoul)",
                    "location": {
                        "display": "South Korea (Seoul)",
                        "latitude": 37.56, "longitude": 126.98,
                    },
                    "zone": ["ap-northeast-2a", "ap-northeast-2c"],
                },
                "us-east-1": {
                    "description": "US East (N. Virginia)",
                    "location": {"display": "USA (Virginia)", "latitude": 38.9,
                                 "longitude": -77.4},
                    "zone": ["us-east-1a"],
                },
            },
        },
        "gcp": {
            "description": "Google Cloud Platform",
            "region": {
                "asia-northeast3": {
                    "description": "Seoul South Korea",
                    "location": {"display": "South Korea (Seoul)", "latitude": 37.53,
                                 "longitude": 126.99},
                    "zone": ["asia-northeast3-a"],
                },
            },
        },
        "azure": {
            "description": "Microsoft Azure",
            "region": {
                # 방위 이름 — 도시가 안 들어 있다
                "koreacentral": {
                    "description": "Korea Central",
                    "location": {"display": "Korea Central", "latitude": 37.5,
                                 "longitude": 127.0},
                },
                "southeastasia": {
                    "description": "Southeast Asia",
                    "location": {"display": "Southeast Asia", "latitude": 1.28,
                                 "longitude": 103.8},
                },
            },
        },
        "nhn": {
            "description": "NHN Cloud",
            # **대문자다.** 미러는 kr1으로 적는다.
            "region": {
                "KR1": {
                    "description": "Pangyo (South Korea)",
                    "location": {"display": "Pangyo (South Korea)", "latitude": 37.4,
                                 "longitude": 127.1},
                },
            },
        },
    }
}


@pytest.fixture
def built(tmp_path, monkeypatch):
    raw = tmp_path / "cloudinfo.yaml"
    raw.write_text(json.dumps(RAW), encoding="utf-8")  # YAML은 JSON의 상위집합
    monkeypatch.setattr(cloudinfo, "fetch_cached", lambda url, name, **kw: raw)
    cloudinfo.build(tmp_path / "cloud-regions.json")

    regions_mod._catalog.cache_clear()
    yield tmp_path
    regions_mod._catalog.cache_clear()


# --- 파서 -----------------------------------------------------------------

def test_join_key_is_lowercased_but_original_is_kept(built) -> None:
    """`KR1` → 키는 `kr1`, `code`는 `KR1`.

    소문자로 안 맞추면 kt·ncp·nhn이 미러와 **0%** 조인된다(실측). 반대로 원본을
    소문자로 덮어쓰면 원본이 뭐라 적었는지 잃는다.
    """
    data = json.loads((built / "cloud-regions.json").read_text(encoding="utf-8"))
    nhn = data["providers"]["nhn"]["regions"]
    assert set(nhn) == {"kr1"}
    assert nhn["kr1"]["code"] == "KR1"


def test_location_is_carried(built) -> None:
    data = json.loads((built / "cloud-regions.json").read_text(encoding="utf-8"))
    seoul = data["providers"]["aws"]["regions"]["ap-northeast-2"]
    assert seoul["latitude"] == 37.56
    assert seoul["zones"] == ["ap-northeast-2a", "ap-northeast-2c"]


# --- 해석 -----------------------------------------------------------------

def test_seoul_differs_by_provider(built) -> None:
    """이 축을 만든 이유 — 서울이 프로바이더마다 다른 코드다."""
    found = regions_mod.resolve_region("서울", output_dir=str(built))
    got = {(r.provider, r.code) for r in found}
    assert ("aws", "ap-northeast-2") in got
    assert ("gcp", "asia-northeast3") in got
    assert ("azure", "koreacentral") in got  # 이름에 Seoul이 없어도 Korea로 잡힌다
    assert ("nhn", "KR1") in got


def test_provider_narrows(built) -> None:
    """GCP 질문엔 GCP 코드만 줘야 한다 — AWS 코드를 넘기면 못 찾는다."""
    found = regions_mod.resolve_region("서울", provider="gcp", output_dir=str(built))
    assert [(r.provider, r.code) for r in found] == [("gcp", "asia-northeast3")]


def test_korean_alias_matches_inside_a_sentence(built) -> None:
    """한국어는 조사가 붙는다 — '서울에서'도 걸려야 한다."""
    found = regions_mod.resolve_region(
        "서울에서 GPU 인스턴스 쓸 수 있어?", provider="aws", output_dir=str(built)
    )
    assert [r.code for r in found] == ["ap-northeast-2"]


def test_source_provided_name_is_marked_apart_from_our_translation(built) -> None:
    """원본이 말한 것과 우리가 더한 번역을 같은 무게로 말하면 안 된다."""
    by_name = regions_mod.resolve_region("Seoul", provider="aws", output_dir=str(built))
    by_alias = regions_mod.resolve_region("서울", provider="aws", output_dir=str(built))
    by_code = regions_mod.resolve_region(
        "ap-northeast-2", provider="aws", output_dir=str(built)
    )
    assert by_name[0].matched_by == "name"
    assert by_alias[0].matched_by == "alias"
    assert by_code[0].matched_by == "code"


def test_compass_name_is_not_mapped_to_a_city(built) -> None:
    """Azure `Southeast Asia`를 싱가포르로 적지 않는다.

    그건 번역이 아니라 **새 사실 주장**이다. 못 찾으면 못 찾았다고 답한다.
    """
    assert regions_mod.resolve_region(
        "싱가포르", provider="azure", output_dir=str(built)
    ) == []


def test_unknown_place_is_not_claimed_absent(built) -> None:
    text = flat(regions_mod.region_lookup("화성", output_dir=built))
    assert "**we did not understand it**" in text
    assert "does not mean no such region exists" in text


def test_multi_provider_answer_warns_about_codes(built) -> None:
    """프로바이더마다 코드가 다르다는 걸 답이 말해야 한다."""
    text = flat(regions_mod.region_lookup("서울", output_dir=built))
    assert "**Region codes differ by provider.**" in text
    assert "[gcp] asia-northeast3" in text


def test_missing_artifact_guides_to_build(tmp_path) -> None:
    regions_mod._catalog.cache_clear()
    assert "build-regions" in regions_mod.region_lookup("서울", output_dir=tmp_path)
    regions_mod._catalog.cache_clear()
