"""cb-tumblebug `cloudinfo.yaml` → 프로바이더별 리전 이름·위치.

**왜 필요한가.** 리전 이름 매핑을 botocore로 먼저 붙였는데 그건 **AWS만** 준다.
그래서 "서울에서 GCP 스팟 얼마?"를 물으면 우리 도구가 `ap-northeast-2`(AWS)를
주고, GCP 서울인 `asia-northeast3`는 모델의 기억에 맡겨야 했다. 실제로 모델이
`asia-north`**h**`east3`로 오타를 냈고 그게 틀린 답으로 이어졌다(실측).

**이미 핀 박은 저장소의 안 쓰던 파일이다.** 우리는 cb-tumblebug `v0.11.8`에서
`assets.dump.gz`(spec_infos)만 쓰고 있었는데, 같은 태그 안에 이 파일이 있다.
이번 소스 조사에서 가장 크게 배운 것이 그것이다 — 새 소스를 찾기 전에 이미 받아 둔
소스의 안 쓰는 부분부터 본다.

**미러와 같은 세계라는 것이 결정적이다.** costkb·perfkb의 리전 코드는 cb-tumblebug에서
왔고 이 파일도 같은 저장소다. 그래서 조인이 정확하다(소문자 정규화 후 95%).

## 실측 (v0.11.8)

    프로바이더 10곳 · 리전 188개 · 표시이름 188 · 위경도 188 · 가용영역 175

    alibaba 31 · aws 29 · azure 47 · gcp 42 · ibm 11
    tencent 19 · nhn 4 · ncp 3 · kt 1 · openstack 1

'서울'이 프로바이더마다 다르다는 것이 이 파일의 값어치다:

    alibaba/aws  ap-northeast-2      gcp      asia-northeast3
    azure        koreacentral·south  tencent  ap-seoul
    kt           KR1                 ncp      KR          nhn  KR1·KR2

## 함정 — 대소문자

`kt`·`ncp`·`nhn`은 이 파일이 `KR1`·`KR`로 적는데 미러는 `kr1`·`kr`로 적는다.
그대로 조인하면 이 셋이 **0%**가 된다(실측). 소문자로 맞추면 100%다. 코드는
원본 표기를 남기고 조인 키만 소문자로 둔다 — 원본을 고쳐 쓰면 그건 우리 값이 된다.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.deployment.kbcommon.artifact import write_dataset
from app.deployment.kbcommon.fetch import describe_source, fetch_cached
from app.deployment.kbcommon.sources import SOURCES

try:
    _Loader = yaml.CSafeLoader
except AttributeError:  # pragma: no cover
    _Loader = yaml.SafeLoader

SCHEMA = {
    "type": "object",
    "required": ["providers", "_source"],
    "properties": {
        "_note": {"type": "string"},
        "_source": {"type": "array"},
        "providers": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["description", "regions"],
                "properties": {
                    "description": {"type": "string"},
                    "regions": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "required": ["code", "name"],
                            "additionalProperties": False,
                            "properties": {
                                # 원본 표기(대소문자 그대로). 키는 소문자다.
                                "code": {"type": "string", "minLength": 1},
                                "name": {"type": "string", "minLength": 1},
                                "latitude": {"type": ["number", "null"]},
                                "longitude": {"type": ["number", "null"]},
                                "zones": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                },
            },
        },
    },
}


def parse(doc: dict) -> tuple[dict, dict]:
    """cloudinfo.yaml → {provider: {regions: {소문자코드: {...}}}}. (데이터, 통계)."""
    providers: dict[str, dict] = {}
    stats = {"providers": 0, "regions": 0, "with_latlon": 0, "with_zones": 0}

    for csp, body in sorted((doc.get("cloud") or {}).items()):
        regions: dict[str, dict] = {}
        for code, region in (body.get("region") or {}).items():
            region = region or {}
            location = region.get("location") or {}
            # 표시 이름은 location.display를 먼저, 없으면 description을 쓴다.
            # 둘 다 없으면 코드 자체가 이름이다 — 빈 이름을 만들지 않는다.
            name = location.get("display") or region.get("description") or code
            zones = [str(z) for z in (region.get("zone") or []) if z]
            regions[code.lower()] = {
                "code": code,  # 원본 표기를 남긴다. 조인 키만 소문자다.
                "name": str(name),
                "latitude": location.get("latitude"),
                "longitude": location.get("longitude"),
                "zones": zones,
            }
            stats["regions"] += 1
            stats["with_latlon"] += location.get("latitude") is not None
            stats["with_zones"] += bool(zones)
        if not regions:
            continue
        providers[csp] = {
            "description": str(body.get("description") or csp),
            "regions": regions,
        }
        stats["providers"] += 1
    return providers, stats


def build(output: Path, *, refresh: bool = False) -> dict:
    source = SOURCES["tumblebug-cloudinfo"]
    path = fetch_cached(
        source.url, f"tumblebug-cloudinfo-{source.pin}.yaml", refresh=refresh
    )
    with open(path, encoding="utf-8") as handle:
        doc = yaml.load(handle, Loader=_Loader)
    providers, stats = parse(doc)

    dataset = {
        "_note": (
            "cb-tumblebug's region definitions (cloudinfo.yaml). It lives in the "
            "same repository as the mirror, so the region codes match exactly. "
            "The join key is lowercased and `code` keeps the source's spelling — "
            "kt·ncp·nhn are uppercase in the source (KR1) but lowercase in the "
            "mirror (kr1)."
        ),
        "_source": [describe_source(path, source.key)],
        "providers": providers,
    }
    write_dataset(output, dataset, SCHEMA)

    print(
        f"cloud-regions: {stats['providers']} providers · {stats['regions']} regions "
        f"(lat/lon {stats['with_latlon']} · zones {stats['with_zones']})"
    )
    for csp, body in sorted(providers.items()):
        print(f"  {csp:10} {len(body['regions']):3} regions")
    return dataset
