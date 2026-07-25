"""botocore `endpoints.json` → 리전 이름 + 서비스 엔드포인트 소재.

**왜 필요한가.** 실측에서 "서울 리전에서 GPU 인스턴스 쓸 수 있나"에 답하지 못했다.
데이터는 있었다 — `output/aws-regions.json`에 `ap-northeast-2`의 EC2 인스턴스 타입
780개가 담겨 있다. 없던 것은 **`서울`을 `ap-northeast-2`로 바꾸는 길**뿐이었다.

**새 소스가 아니다.** 우리는 botocore를 이미 `1.43.52`로 고정해 EC2 서비스 모델만
쓰고 있었는데, 같은 태그 안에 이 파일이 있었다. 받는 비용도, 라이선스 판단도,
고정할 것도 새로 생기지 않는다.

## 이 파일을 읽을 때의 함정

**"엔드포인트가 없다"는 "그 리전에서 못 쓴다"가 아니다.**

    cloudfront   엔드포인트 1개 (us-east-1)
    budgets      엔드포인트 1개 (us-east-1)

CloudFront가 us-east-1에서만 되는 게 아니다 — **글로벌 서비스**라 엔드포인트가 하나인
것이다. 이걸 가용성으로 읽으면 "서울에서는 CloudFront를 못 씁니다"라는, 우리가 가장
경계하는 **확신에 찬 오답**이 나온다.

판별자가 데이터에 있긴 하다(`isRegionalized: false`, `partitionEndpoint: "aws-global"`).
그런데 실측해 보니 **서비스 307개 중 22개에만 있다.** 엔드포인트가 2개 이하인 위험군
34개 중 절반인 17개는 판별자가 없다(`cur`, `marketplacecommerceanalytics`,
`cost-optimization-hub` 등). 즉 **글로벌인지 진짜 리전 제한인지 구분할 수 없다.**

그래서 이 파서는 **있음만 담고 없음은 담지 않는다.** 질의 쪽은 세 상태로 답한다 —
있음 / 글로벌(원본이 그렇게 밝힌 경우만) / 모름. "없음"이라는 답은 만들지 않는다.

## 서비스 이름은 짐작으로 붙이지 않는다

CFN 타입 네임스페이스(`AWS::EC2::` → `ec2`)를 botocore 서비스 id에 붙이려 하면
실측으로 281개 중 148개만 그대로 맞는다. 하이픈을 지우면 34개가 더 붙어 182개(65%)다
(`acmpca` → `acm-pca`). 이 정규화는 충돌이 **0건**이라 안전하다.

남는 99개(35%)는 철자 문제가 아니라 **정말 다른 이름**이다 — `cloudwatch`는 botocore에서
`monitoring`, `cognito`는 `cognito-idp`, `certificatemanager`는 `acm`이다. 규칙으로는
못 맞힌다. 손으로 표를 만들 수도 있겠지만, 틀리면 엉뚱한 서비스의 리전을 답하게 된다.
**못 붙는 것은 못 붙는다고 답한다.**
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from kbcommon.artifact import write_dataset
from kbcommon.fetch import describe_source_set, fetch_cached
from kbcommon.sources import SOURCES

EVIDENCE = "botocore-endpoints"

# 리전 판정은 **원본의 `regions` 선언에 맡긴다.** 처음에는 `us-east-1` 꼴 정규식으로
# 걸렀는데, 실측해 보니 그게 진짜 리전 `eusc-de-east-1`(유럽 주권 클라우드)을 버리고
# 있었다 — 앞이 두 글자라고 짐작한 탓이다. 반대로 파티션의 `regions` 선언에는
# `aws-global`·`fips-us-east-1` 같은 가짜 리전이 **한 건도** 섞여 있지 않았다(실측).
# 원본이 스스로 밝히는 목록이 내 규칙보다 정확하다.

#: 산출물 스키마. CapacitySet(제약·쿼터)과 모양이 다르므로 여기서만 쓴다 —
#: 이건 "속성에 걸린 제약"이 아니라 "무엇이 어디에 있는가"라 억지로 끼워 넣으면
#: `type_id`·`property` 칸을 거짓으로 채우게 된다.
SCHEMA = {
    "type": "object",
    "required": ["partitions", "services"],
    "properties": {
        "partitions": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "required": ["name", "regions"],
                "properties": {
                    "name": {"type": "string"},
                    "regions": {
                        "type": "object",
                        "additionalProperties": {
                            "type": "object",
                            "properties": {"description": {"type": "string"}},
                        },
                    },
                },
            },
        },
        "services": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "required": ["regions"],
                    "properties": {
                        "regions": {"type": "array", "items": {"type": "string"}},
                        "global": {"type": ["boolean", "null"]},
                        "partition_endpoint": {"type": ["string", "null"]},
                    },
                },
            },
        },
        "provenance": {"type": "array"},
        "note": {"type": "string"},
        "dropped_pseudo_regions": {"type": "integer"},
    },
}


class Report:
    def __init__(self) -> None:
        self.pairs = 0
        self.dropped: Counter = Counter()
        """원본이 리전으로 선언하지 않은 엔드포인트 키(`fips-us-east-1` 등).

        조용히 넘기면 함정을 다시 밟는다. FIPS 변형이 대부분인데, 이것들은 리전이
        아니라 **같은 리전의 다른 접속점**이라 리전으로 담으면 리전 수가 부풀려진다.
        """
        self.global_services = 0


def parse(doc: dict) -> tuple[dict, Report]:
    """endpoints.json에서 파티션·리전·서비스 소재를 뽑는다."""
    report = Report()
    partitions: dict[str, dict] = {}
    services: dict[str, dict] = {}

    for part in doc.get("partitions") or []:
        key = part.get("partition")
        if not key:
            continue
        regions = {
            code: {"description": body["description"]}
            for code, body in (part.get("regions") or {}).items()
            if isinstance(body, dict) and body.get("description")
        }
        partitions[key] = {"name": part.get("partitionName") or key, "regions": regions}

        by_service: dict[str, dict] = {}
        for name, body in (part.get("services") or {}).items():
            if not isinstance(body, dict):
                continue
            found = []
            for code in body.get("endpoints") or {}:
                if code in regions:
                    found.append(code)
                else:
                    report.dropped[code] += 1
            regionalized = body.get("isRegionalized")
            partition_endpoint = body.get("partitionEndpoint")
            is_global = False if regionalized is False else None
            if partition_endpoint and regionalized is not True:
                is_global = True
            if is_global:
                report.global_services += 1
            report.pairs += len(found)
            by_service[name] = {
                "regions": sorted(found),
                # None = 원본이 말하지 않았다. False라고 적으면 우리가 짐작한 게 된다.
                "global": is_global,
                "partition_endpoint": partition_endpoint,
            }
        services[key] = by_service

    return {"partitions": partitions, "services": services}, report


def build(output: Path, *, refresh: bool = False) -> dict:
    source = SOURCES["botocore-endpoints"]
    path = fetch_cached(
        source.url, f"botocore-endpoints-{source.pin}.json", refresh=refresh
    )
    dataset, report = parse(json.loads(path.read_text(encoding="utf-8")))

    dataset["provenance"] = [describe_source_set([path], source.key)]
    dataset["dropped_pseudo_regions"] = sum(report.dropped.values())
    dataset["note"] = (
        "records only that an endpoint **exists**. a (service, region) pair that is "
        "not listed does not mean 'you cannot use it' — it means 'this data does not "
        "know'. it may be a global service, and the marker that tells one apart "
        "(isRegionalized) is on only 22 of the 307 services, so the two cannot be "
        "distinguished."
    )

    write_dataset(output, dataset, SCHEMA)

    total_regions = sum(len(p["regions"]) for p in dataset["partitions"].values())
    print(
        f"aws-endpoints: 파티션 {len(dataset['partitions'])}개 · 리전 {total_regions}개 · "
        f"(서비스, 리전) {report.pairs:,}쌍 (원본이 글로벌이라 밝힌 서비스 "
        f"{report.global_services}개)"
    )
    if report.dropped:
        top = ", ".join(f"{k}×{v}" for k, v in report.dropped.most_common(4))
        print(
            f"  원본이 리전으로 선언하지 않아 버린 엔드포인트 키 "
            f"{len(report.dropped)}종: {top}"
        )
    return dataset
