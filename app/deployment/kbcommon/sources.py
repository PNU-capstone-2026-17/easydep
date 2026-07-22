"""소스 버전 고정 — 어느 시점의 원본에서 산출물이 나왔는지 못 박는다.

**왜 필요한가**: 감사(2026-07-20) 중에 이런 일이 있었다.

    로컬 캐시:  CloudformationSchema.zip  2,783,390 B
    같은 URL 라이브:                      2,794,161 B  (Last-Modified 2026-07-18)

`output/`의 산출물은 **더 이상 그 URL에 존재하지 않는 zip**에서 나온 것이었다. 그러면
결함 수치를 재현할 수도, "고쳤다"를 증명할 수도 없다. 검증 전체가 여기 얹혀 있다.

**세 가지 고정 방식**을 소스 성격에 맞게 쓴다:

1. `tag`    — 저장소가 의미 있는 태그를 단다 (GCP KCC, cb-tumblebug, cdk-service-spec)
2. `commit` — 태그가 없거나 쓸모없다 (Azure bicep-types-az의 태그는 `v0.0-test` 류뿐).
              GitHub raw는 커밋 SHA로 접근되므로 SHA를 박으면 완전 재현된다.
3. `digest` — 버전 개념이 아예 없는 소스 (AWS는 zip 하나를 계속 덮어쓴다).
              고정할 URL이 없으므로 **받은 바이트의 sha256을 기록**하고, 다음 빌드에서
              달라지면 알린다. 재현은 못 해도 **바뀐 사실은 놓치지 않는다.**
4. `bundled` — 저장소에 들어 있는 파일 (사람이 검수한 매핑). git이 곧 버전 관리이므로
              재현은 되지만, 손으로 고치는 파일이라 해시는 그대로 기록한다.

`digest` 소스는 재현성이 원리적으로 없다. 그게 이 표에 드러나 있는 것 자체가 목적이다 —
"AWS 스키마는 고정할 수 없다"는 사실이 코드에 기록돼 있어야 다음 사람이 착각하지 않는다.
필요하면 `--source-file`로 보관해 둔 zip을 직접 넘겨 재현할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    """원본 하나의 고정 정보."""

    key: str
    """산출물 프로버넌스에 기록되는 짧은 이름."""

    url: str
    """실제로 받는 URL (고정 ref가 이미 박혀 있다)."""

    pin_kind: str
    """`tag` | `commit` | `digest` — 위 docstring 참조."""

    pin: str
    """태그명·커밋 SHA. `digest` 소스는 `"(고정 불가)"`."""

    note: str = ""


# 고정 ref를 바꿀 때는 **반드시 재빌드하고 산출물 수치를 다시 확인**할 것.
# 그냥 올리면 조용히 다른 세계의 데이터가 섞인다.
SOURCES: dict[str, Source] = {
    "cfn-schema": Source(
        key="cfn-schema",
        url="https://schema.cloudformation.us-east-1.amazonaws.com/CloudformationSchema.zip",
        pin_kind="digest",
        pin="(고정 불가)",
        note="AWS가 같은 URL을 계속 덮어쓴다. 버전·태그·아카이브가 없다.",
    ),
    "cdk-oob": Source(
        key="cdk-oob",
        # Git LFS라 media 호스트를 써야 한다 — raw는 포인터 텍스트를 준다.
        url=(
            "https://media.githubusercontent.com/media/cdklabs/awscdk-service-spec/"
            "@aws-cdk/aws-service-spec@v0.1.196/sources/OobRelationships/relationships.json"
        ),
        pin_kind="tag",
        pin="@aws-cdk/aws-service-spec@v0.1.196",
        note="npm 패키지 버전과 같은 태그. 주 1~2회 릴리스된다.",
    ),
    "bicep-types-az": Source(
        key="bicep-types-az",
        url="https://raw.githubusercontent.com/Azure/bicep-types-az/ef7421bbfef762f59292e253701a9859af32fc2c/generated",
        pin_kind="commit",
        pin="ef7421bbfef762f59292e253701a9859af32fc2c",
        note="태그가 v0.1/v0.0-test뿐이라 쓸 수 없다. 커밋 SHA로 고정. 2026-07-19.",
    ),
    "azure-limits-doc": Source(
        key="azure-limits-doc",
        url="https://raw.githubusercontent.com/MicrosoftDocs/azure-docs/355bbdc30800cb3b4ab856521a1b50c17188bf49/includes",
        pin_kind="commit",
        pin="355bbdc30800cb3b4ab856521a1b50c17188bf49",
        note="문서 저장소라 태그가 없다. 커밋 SHA로 고정. 2026-07-15.",
    ),
    "kcc-crd": Source(
        key="kcc-crd",
        url="https://raw.githubusercontent.com/GoogleCloudPlatform/k8s-config-connector/v1.153.0",
        pin_kind="tag",
        pin="v1.153.0",
        note="파서가 뒤에 /config/<경로>를 붙인다. --tag로 바꿀 수 있다.",
    ),
    "aws-price-list": Source(
        key="aws-price-list",
        url=(
            "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/"
            "20260721012550/ap-east-2/index.json"
        ),
        pin_kind="tag",
        pin="20260721012550",
        note=(
            "버전 URL이 있어 고정한다(`current`는 움직인다). 볼륨 종류 속성은 리전 "
            "불변이라 작은 리전에서 받는다(43MB, 캐시됨). 값이 계속 정확하리란 "
            "계약상 보장은 없으므로 sha256을 함께 남겨 다음 빌드와 대조한다."
        ),
    ),
    "botocore": Source(
        key="botocore",
        url=(
            "https://raw.githubusercontent.com/boto/botocore/1.43.52/"
            "botocore/data/ec2/2016-11-15/service-2.json"
        ),
        pin_kind="tag",
        pin="1.43.52",
        note=(
            "**정정(2026-07-21)**: 예전 주석은 'shape에 min/max가 없다'였는데 틀렸다. "
            "EC2만 봐도 shape 4,069개 중 min 183·max 175·enum 457개가 있다. "
            "참인 것은 EBS의 `Size`·`Iops`·`Throughput`이 제약 없는 공용 `Integer` "
            "shape을 가리킨다는 것뿐인데, 그 관찰을 전체로 일반화했다 — 우리가 막으려는 "
            "'확신에 찬 오답'을 우리 주석이 저지른 것이다. **EBS 한도에 한해서** "
            "설명문이 유일한 출처다."
        ),
    ),
    "botocore-endpoints": Source(
        key="botocore-endpoints",
        url=(
            "https://raw.githubusercontent.com/boto/botocore/1.43.52/"
            "botocore/data/endpoints.json"
        ),
        pin_kind="tag",
        pin="1.43.52",
        note=(
            "**새 소스가 아니라 이미 고정해 둔 태그 안의 안 쓰던 파일이다.** "
            "`botocore`와 같은 1.43.52를 가리키므로 따로 고정할 것이 없다. "
            "리전 표시 이름(`ap-northeast-2` → `Asia Pacific (Seoul)`)과 "
            "(파티션, 서비스, 리전) 엔드포인트 9,039쌍이 들어 있다. "
            "**읽는 법 주의**: 엔드포인트가 있다는 것은 사실이지만, 없다는 것은 "
            "'그 리전에서 못 쓴다'가 아니다 — 글로벌 서비스일 수도 있다. "
            "판별자(`isRegionalized`)가 307개 중 22개에만 있어서 구분이 안 되므로 "
            "**있음만 담고 없음은 담지 않는다.**"
        ),
    ),
    "cfn-lint": Source(
        key="cfn-lint",
        url=(
            "https://files.pythonhosted.org/packages/ba/f3/"
            "5218c6fa4ed79b6ccb92bee07c8702f34798e2c77ac3fedf4de6f4add868/"
            "cfn_lint-1.53.1-py3-none-any.whl"
        ),
        pin_kind="tag",
        pin="1.53.1",
        note=(
            "MIT-0. CloudFormation 레지스트리가 표현 못 하는 것을 cfn-lint가 별도 "
            "파일로 관리한다 — 리전별 인스턴스 타입 (리전,값) 79,809쌍. "
            "PyPI wheel URL은 내용 주소라 불변이다(경로에 해시가 박혀 있다)."
        ),
    ),
    "tpaws-provider": Source(
        key="tpaws-provider",
        url=(
            "https://github.com/hashicorp/terraform-provider-aws/archive/"
            "refs/tags/v6.55.0.tar.gz"
        ),
        pin_kind="tag",
        pin="v6.55.0",
        note=(
            "CloudFormation이 표현 못 하는 교차 필드 조건·조건부 불변이 여기 있다. "
            "google 프로바이더와 달리 **사람이 쓴 코드**라(생성 코드 19%) 빈 목록 증발이 "
            "없는 대신 손 큐레이션이다 — 근거 라벨을 나눈 이유. 108MB, 캐시됨."
        ),
    ),
    "tpg-provider": Source(
        key="tpg-provider",
        url=(
            "https://github.com/hashicorp/terraform-provider-google/archive/"
            "refs/tags/v7.40.0.tar.gz"
        ),
        pin_kind="tag",
        pin="v7.40.0",
        note=(
            "Magic Modules의 **버전이 매겨진 산출물**. MM 저장소 자체는 태그가 0개라 "
            "핀을 못 박고 하루 3.6건씩 바뀌므로, 같은 파이프라인의 주간 릴리스를 쓴다. "
            "KCC가 벤더링한 4.84.0(2023-09-26)보다 3개 메이저 최신이다. 17MB, 캐시됨."
        ),
    ),
    "cyclenerd-gcp-pricing": Source(
        key="cyclenerd-gcp-pricing",
        url=(
            "https://raw.githubusercontent.com/Cyclenerd/"
            "google-cloud-pricing-cost-calculator/574d8fbb68fa/pricing.yml"
        ),
        pin_kind="commit",
        pin="574d8fbb68fa",
        note=(
            "GCP **스팟·약정** 가격. 우리 미러(tumblebug)엔 온디맨드 정가 하나뿐이라 "
            "'예약하면 얼마'에 답할 수 없었다. 라이선스는 파일 안에 Apache-2.0으로 "
            "명시(about.copyright). 태그가 없어 커밋 SHA로 고정한다. "
            "**미러를 대체하지 않는다** — 온디맨드는 tumblebug를 유지하고 스팟·약정만 "
            "보강한다. 이 파일의 온디맨드는 다른 스냅샷(2026-07-16 생성)이라 tumblebug와 "
            "리전×패밀리 단위로 어긋나므로, 조인 시 괴리를 함께 기록한다. "
            "자기정합성은 확인됨(스팟 중앙값 온디맨드의 32%, 이상치 0건). 3.8MB, 캐시됨."
        ),
    ),
    "tumblebug-cloudinfo": Source(
        key="tumblebug-cloudinfo",
        url=(
            "https://raw.githubusercontent.com/cloud-barista/cb-tumblebug/v0.11.8/"
            "assets/cloudinfo.yaml"
        ),
        pin_kind="tag",
        pin="v0.11.8",
        note=(
            "**이미 핀 박은 저장소의 안 쓰던 파일이다.** 우리는 이 저장소에서 "
            "assets.dump.gz(spec_infos)만 쓰고 있었는데, 같은 태그에 리전 정의가 있다. "
            "프로바이더 10곳 188개 리전의 **표시 이름·위경도·가용영역**이 전부 채워져 "
            "있다(실측 188/188). 미러와 같은 저장소라 리전 코드가 정확히 맞는다 — "
            "소문자 정규화 후 미러 리전 163개 중 155개(95%)와 조인된다. "
            "botocore endpoints는 AWS만 주는데 이건 전 프로바이더를 준다. Apache-2.0."
        ),
    ),
    "tumblebug-swagger": Source(
        key="tumblebug-swagger",
        url=(
            "https://raw.githubusercontent.com/cloud-barista/cb-tumblebug/v0.11.8/"
            "src/interface/rest/docs/swagger.json"
        ),
        pin_kind="tag",
        pin="v0.11.8",
    ),
    "cb-spider-map": Source(
        key="cb-spider-map",
        # 네트워크 소스가 아니다 — CB-Spider 드라이버를 사람이 읽고 검수해 만든 번들.
        url="graphkb/parsers/core_vendor_map.json",
        pin_kind="bundled",
        pin="(git으로 버전 관리됨)",
        note="사람이 손으로 고치는 파일이라 오히려 해시 추적이 필요하다.",
    ),
    "azure-rest-api-specs": Source(
        key="azure-rest-api-specs",
        url=(
            "https://codeload.github.com/Azure/azure-rest-api-specs/tar.gz/"
            "76ca9f3e8e7d8ee18676ec410cf72249e5417c22"
        ),
        pin_kind="commit",
        pin="76ca9f3e8e7d8ee18676ec410cf72249e5417c22",
        note=(
            "태그가 없는 저장소라 커밋 SHA로 고정. tarball 191MB(55초, 캐시됨). "
            "`x-ms-mutability` 하나만 쓴다 — 나머지 제약은 bicep-types가 이미 담고 "
            "있고 우리가 소비 중이다. 2026-07-21."
        ),
    ),
    "ec2-hardware": Source(
        key="ec2-hardware",
        url=(
            "https://raw.githubusercontent.com/vantage-sh/ec2instances.info/"
            "4ef36cd2c9867c1076206dcb691412ae2de7e8dd/"
            "scraper/aws/ec2/extras/manually_fetched_data.json"
        ),
        pin_kind="commit",
        pin="4ef36cd2c9867c1076206dcb691412ae2de7e8dd",
        note=(
            "MIT. 태그·릴리스가 없어 커밋 SHA로 고정한다 — 이게 배제 근거가 될 수 "
            "없다는 건 bicep-types-az에서 이미 정리했다. **저장소가 서빙하는 "
            "instances.json(209MB)은 쓰지 않는다** — 커밋돼 있지 않고 AWS Price "
            "List 파생물이라 고정도 재배포도 안 된다. 여기 커밋된 파일만 쓴다. "
            "벤치마크 점수도 들어 있으나 담지 않는다(parsers/hardware.py 참고)."
        ),
    ),
    "tumblebug-dump": Source(
        key="tumblebug-dump",
        url="https://raw.githubusercontent.com/cloud-barista/cb-tumblebug/v0.12.25/assets/assets.dump.gz",
        pin_kind="tag",
        pin="v0.12.25",
        note="파서가 --tag로 바꿀 수 있다.",
    ),
}


def unpinnable() -> list[Source]:
    """원리적으로 재현 불가능한 소스 — 빌드 요약에서 눈에 띄게 하려고."""
    return [s for s in SOURCES.values() if s.pin_kind == "digest"]
