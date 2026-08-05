"""AWS Solutions Constructs → AWS가 공식으로 묶어 둔 조합.

## 왜 이 소스인가

번들의 나머지 소스는 전부 Azure 쪽이다(AVM·Quickstart). AWS에는 그만한 **선언적**
번들 카탈로그가 없다 — CDK는 TypeScript, SAM은 파이썬 변환 규칙이라 둘 다 코드다.

Solutions Constructs는 다르다. **패턴 이름 자체가 조합**이다.

    aws-apigateway-lambda        API Gateway + Lambda
    aws-cloudfront-apigateway-lambda
    aws-dynamodbstreams-lambda-elasticsearch-kibana

실측: 패턴 83개 · 등장 서비스 41종 (lambda 36 · s3 13 · fargate 12 · apigateway 11).

## 정직하게 말하면 — 조합은 AWS가 말했고, 타입 매핑은 우리가 했다

이름은 **서비스**를 말하지 리소스 **타입**을 말하지 않는다. `lambda`가
`AWS::Lambda::Function`인 것은 분명하지만 `fargate`가 무엇인지는 갈린다
(ECS 서비스? 태스크 정의? 클러스터?).

그래서 **모호하지 않은 것만 매핑하고 나머지는 담지 않는다.** 안 담은 서비스는
coverage에 개수로 적는다 — 조용히 빠뜨리지 않는다. 이 저장소가 리전 이름에서
"코드만으로는 서울인 줄 모른다"며 짐작 매핑을 거부한 것과 같은 선이다.
"""

from __future__ import annotations

import collections
import json
import re
import sys
import tarfile
from pathlib import Path

from app.core.cloudkb.bundlekb.model import ALWAYS, Bundle, BundleSet, Member
from app.core.cloudkb.kbcommon.fetch import describe_source_set, fetch_cached
from app.core.cloudkb.kbcommon.sources import SOURCES

EVIDENCE = "aws-solutions-construct"
PROVIDER = "aws"

_PATTERN_DIR = re.compile(r"/source/patterns/@aws-solutions-constructs/([^/]+)/")

#: 서비스 이름 → 대표 CFN 타입. **모호하지 않은 것만 넣는다.**
#: 여기 없는 이름은 담지 않고 coverage에 개수로 남긴다.
_SERVICE_TYPE = {
    "lambda": "AWS::Lambda::Function",
    "s3": "AWS::S3::Bucket",
    "dynamodb": "AWS::DynamoDB::Table",
    "sqs": "AWS::SQS::Queue",
    "sns": "AWS::SNS::Topic",
    "apigateway": "AWS::ApiGateway::RestApi",
    "cloudfront": "AWS::CloudFront::Distribution",
    "kinesisstreams": "AWS::Kinesis::Stream",
    "kinesisfirehose": "AWS::KinesisFirehose::DeliveryStream",
    "eventbridge": "AWS::Events::Rule",
    "stepfunctions": "AWS::StepFunctions::StateMachine",
    "cognito": "AWS::Cognito::UserPool",
    "secretsmanager": "AWS::SecretsManager::Secret",
    "ssmstringparameter": "AWS::SSM::Parameter",
    "wafwebacl": "AWS::WAFv2::WebACL",
    "alb": "AWS::ElasticLoadBalancingV2::LoadBalancer",
    "openseach": "AWS::OpenSearchService::Domain",   # 원본 저장소의 오타 그대로
    "opensearch": "AWS::OpenSearchService::Domain",
    "sagemakerendpoint": "AWS::SageMaker::Endpoint",
    "iot": "AWS::IoT::TopicRule",
    "kendra": "AWS::Kendra::Index",
}

#: 매핑하지 않는 이름과 그 이유. **조용히 빠뜨리지 않는다.**
_UNMAPPED_REASON = {
    "fargate": (
        "the name alone does not say whether this is the ECS service, task "
        "definition, or cluster"
    ),
    "dynamodbstreams": "a stream is a property of the table, not a type of its own",
    "elasticsearch": "renamed to OpenSearch, so which one it means is ambiguous",
    "kibana": "a feature, not a resource type",
    "pipes": "EventBridge Pipes — its role varies across combinations",
    "oai": "a CloudFront OAI is an attachment of the distribution",
    "constructs": "a collection of factories, not a pattern",
    "apigatewayv2websocket": (
        "a different type from the REST API, so no single representative can be chosen"
    ),
    "route53": "record set or hosted zone — it varies",
    "iotanalytics": "a group of several types",
}


def patterns_in(tar: Path) -> list[str]:
    names: set[str] = set()
    with tarfile.open(tar, "r:gz") as archive:
        for member in archive:
            found = _PATTERN_DIR.search("/" + member.name.replace("\\", "/"))
            if found:
                names.add(found.group(1))
    return sorted(names)


def services_of(name: str) -> list[str]:
    parts = name.split("-")
    return parts[1:] if parts and parts[0] == "aws" else parts


class Report:
    def __init__(self) -> None:
        self.patterns = 0
        self.kept = 0
        self.dropped_all_unmapped = 0
        self.unmapped: collections.Counter = collections.Counter()


def parse_tarball(tar: Path) -> tuple[BundleSet, Report]:
    out = BundleSet()
    report = Report()
    for name in patterns_in(tar):
        report.patterns += 1
        services = services_of(name)
        members = []
        for service in services:
            type_name = _SERVICE_TYPE.get(service)
            if type_name is None:
                report.unmapped[service] += 1
                continue
            members.append(
                Member(f"{PROVIDER}::{type_name}", ALWAYS, f"'{service}' in the pattern name")
            )
        # 같은 타입이 두 번 나오는 이름이 있다(중복 제거).
        unique = {m.type_id: m for m in members}
        if len(unique) < 2:
            # 조합이 하나뿐이면 '리소스 군'이 아니다.
            report.dropped_all_unmapped += 1
            continue
        report.kept += 1
        out.add(
            Bundle(
                id=f"awscon::{name}",
                name=name,
                provider=PROVIDER,
                evidence=EVIDENCE,
                members=tuple(unique.values()),
                description=f"AWS Solutions Constructs pattern — {' + '.join(services)}",
                caveat=(
                    "AWS officially grouped the combination, and **mapping the "
                    "service names to resource types is ours**. Attachments the "
                    "pattern actually creates, such as IAM roles and log groups, "
                    "are not included."
                ),
            )
        )
    return out, report


def build(output: Path, *, refresh: bool = False) -> BundleSet:
    source = SOURCES["aws-solutions-constructs"]
    tar = fetch_cached(source.url, f"awscon-{source.pin}.tar.gz", refresh=refresh)
    bundles, report = parse_tarball(tar)
    bundles.provenance = [describe_source_set([tar], source.key)]
    unmapped = ", ".join(f"{s}({n})" for s, n in report.unmapped.most_common(8))
    bundles.coverage = [
        {
            "provider": PROVIDER,
            "bundles": len(bundles.bundles),
            "note": (
                f"{report.kept} of {report.patterns} AWS Solutions Constructs "
                "patterns. **AWS declared the combination and the service→type "
                "mapping is ours** — ambiguous names were left unmapped "
                f"({unmapped}). The reason is written per name in the parser's "
                "`_UNMAPPED_REASON`. Attachments the pattern actually creates, "
                "such as IAM roles and log groups, do not show in the name, so "
                "they are **not included** — read that as 'this source does not "
                "tell us', not as 'there are no attachments'."
            ),
        }
    ]
    from app.core.cloudkb.kbcommon import artifact

    artifact.write_dataset(output, bundles.to_dict(), _schema())
    print(f"aws 패턴 번들: {report.kept}/{report.patterns}개 → {output}")
    print(f"  매핑 못 한 서비스: {dict(report.unmapped.most_common(10))}", file=sys.stderr)
    return bundles


def _schema() -> dict:
    return json.loads(
        (Path(__file__).resolve().parent.parent / "schema.json").read_text(encoding="utf-8")
    )
