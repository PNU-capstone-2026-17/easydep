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

    redistribution: str = ""
    """유도 산출물을 저장소에 넣어도 되는가. **판단한 소스만 적는다.**

    빈 문자열은 "괜찮다"가 아니라 **"따로 판단하지 않았다"**는 뜻이다. 40개 소스를
    다 감사하지 않았으면서 기본값으로 허가를 주장하면 그게 곧 거짓이 된다.

        "denied"      원본이 재배포를 명시적으로 막는다 → **산출물을 커밋하지 않는다**
        "not-stated"  허가 문구를 찾지 못했다. 금지 문구도 없다 → 넣되 `NOTICE`에 밝힌다
        ""            판단하지 않음 (대부분의 오픈소스 라이선스 소스)

    `not-stated`와 `denied`는 `NOTICE`에 이름이 나와야 한다 —
    `tests/test_redistribution_notice.py`가 강제한다. **명시를 사람 기억에 맡기지
    않으려는 것**이 이 칸의 목적이다.
    """

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
        redistribution="denied",
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
    "ms-architecture-center": Source(
        key="ms-architecture-center",
        url=(
            "https://raw.githubusercontent.com/MicrosoftDocs/architecture-center/"
            "11c3681605cfeb209ddbac372a53d8931696d0cd"
        ),
        pin_kind="commit",
        pin="11c3681605cfeb209ddbac372a53d8931696d0cd",
        note=(
            "AWS↔Azure·GCP↔Azure **서비스 비교표**(CC-BY-4.0 — azure-docs와 같은 "
            "라이선스, 재배포 시 저작자 표시). svcmap의 1차 근거다. 태그가 없어 커밋 "
            "SHA로 고정한다.\n"
            "**문서라 표가 재편된다** — `aws-professional/services.md` 하나였던 것이 "
            "카테고리별 파일 6개로 쪼개져 있었다(조사에서 404를 직접 겪음). 파서가 "
            "행수를 세어 급감하면 알린다.\n"
            "표가 말하는 것은 **서비스 이름 수준의 대응**('Amazon RDS ↔ Azure SQL')"
            "이고, 그걸 타입 id(AWS::RDS::DBInstance)에 붙이는 것은 우리 손 검수다 — "
            "그래서 엣지 basis는 stated가 아니라 **inferred(검수됨)**다. 다리를 "
            "건너며 등급이 떨어지는 그 규칙 그대로."
        ),
    ),
    "mingrammer-diagrams": Source(
        key="mingrammer-diagrams",
        url="https://raw.githubusercontent.com/mingrammer/diagrams/v0.24.4",
        pin_kind="tag",
        pin="v0.24.4",
        note=(
            "MIT. 프로바이더별 서비스 분류(모듈 구조가 곧 분류 체계). svcmap의 "
            "**독립 교차 소스**다 — MS 비교표와 이것이 같은 대응을 말하면 교차 확인. "
            "aws·azure·gcp 외에 **ibm·alibabacloud·oci·openstack** 모듈이 있어 MS "
            "표가 안 덮는 프로바이더를 이쪽이 덮는다(tencent·ncloud는 없다 — 실측)."
        ),
    ),
    "azure-compute-docs": Source(
        key="azure-compute-docs",
        url="https://raw.githubusercontent.com/MicrosoftDocs/azure-compute-docs/9c18d88d498d09e897edde7e2fe8483067f2556a",
        pin_kind="commit",
        pin="9c18d88d498d09e897edde7e2fe8483067f2556a",
        note=(
            "Azure VM **크기 문서의 표**(CC-BY-4.0 — azure-docs와 같은 라이선스). "
            "`articles/virtual-machines/sizes/` 계열별 `*-series.md`에 NIC 수·"
            "네트워크 대역폭 Mbps·디스크 IOPS 표가 있다(실측 2026-07-24). "
            "perfkb azure 보강의 근거 — azure-limits-doc처럼 산문이 아니라 "
            "**표**를 파싱한다. 문서라 재편될 수 있어 최소 매칭 수로 감지한다."
        ),
    ),
    "gcloud-machine-types": Source(
        key="gcloud-machine-types",
        url=(
            "https://raw.githubusercontent.com/Cyclenerd/"
            "google-cloud-compute-machine-types/add204f16413d608d35141715aef4a122b59cb96"
        ),
        pin_kind="commit",
        pin="add204f16413d608d35141715aef4a122b59cb96",
        note=(
            "GCP 머신 **시리즈 특성**(Apache-2.0). instances/series/*.sql의 UPDATE "
            "문에 cpuPlatform·계열·크기별 네트워크 대역폭 Gbps가 들어 있다"
            "(실측 2026-07-24). gcp perfkb가 sustainedCpu 하나뿐이던 구멍을 메운다. "
            "**커뮤니티 큐레이션**이라 basis=inferred — 원문이 Google 문서 URL을 "
            "달아 두어 사람이 확인할 수 있다. gcp-spot-commit과 같은 저자의 다른 "
            "저장소이고, 태그가 없어 커밋 SHA로 고정한다."
        ),
    ),
    "azure-well-architected": Source(
        key="azure-well-architected",
        url="https://raw.githubusercontent.com/MicrosoftDocs/well-architected/1353bbb66e53121b702a46baed0d64e1f6284bb5",
        pin_kind="commit",
        pin="1353bbb66e53121b702a46baed0d64e1f6284bb5",
        note=(
            "Azure Well-Architected **지침 산문**(CC-BY-4.0 — architecture-center와 "
            "같은 라이선스). well-architected/ 하위 md 199편(실측 2026-07-24). "
            "patternkb 코퍼스 확장분 — advisory 전용이고 사실 축이 아니다. "
            "AWS WAF는 재배포 불명+labs는 실습 절차라, GCP는 핀 가능 저장소가 "
            "없어 기각했다(조사 문서)."
        ),
    ),
    "twelve-factor": Source(
        key="twelve-factor",
        url="https://raw.githubusercontent.com/heroku/12factor/1385d2c80bac38c25647651f6f5ec769561828dc",
        pin_kind="commit",
        pin="1385d2c80bac38c25647651f6f5ec769561828dc",
        note=(
            "12factor **배포 원칙 산문**(patternkb 코퍼스). MIT — LICENSE에 "
            "'Copyright (c) 2012 Adam Wiggins' 명시(실측 2026-07-24). 태그가 없어 "
            "커밋 SHA로 고정한다. content/en의 md만 취하고 toc.md는 목차라 뺀다. "
            "**산문이라 값·한도가 아니다** — patternkb는 advisory 전용이고 이 소스가 "
            "사실 축에 섞이면 안 된다."
        ),
    ),
    "ibm-global-catalog": Source(
        key="ibm-global-catalog",
        url="https://globalcatalog.cloud.ibm.com/api/v1?q=is.instance",
        pin_kind="digest",
        pin="(고정 불가)",
        redistribution="not-stated",
        note=(
            "IBM VPC 인스턴스 프로필의 **성능 신호**. perfkb는 aws·azure·gcp 셋뿐이었고 "
            "나머지 일곱 중 **소스가 실재하는 것은 IBM뿐**이다(Terraform provider는 타입 "
            "축만 주고 스펙 카탈로그를 안 준다). 무인증 200. 미러의 ibm 287종과 "
            "**287/287 조인**되고 cpu·ram이 287건 전부 일치해 교차 확인이 된다.\n"
            "**버전이 없어 고정 불가**이고 **재배포 허가 문구도 찾지 못했다**"
            "(금지 문구도 없다) — 유도 산출물은 넣되 `NOTICE`에 그 사실을 밝힌다.\n"
            "페이지네이션 함정: `limit`은 무시되고 `_limit`/`_offset`을 써야 한다 — "
            "첫 페이지만 보면 커버리지가 14%로 보인다.\n"
            "**`freqency`(원본 오타)는 310건 전부 2000이라 담지 않는다** — 담으면 모든 "
            "IBM 스펙에 '2.0GHz'라는 확신에 찬 오답이 된다. `status`·`vcpu_architecture`·"
            "`os_architecture`도 값이 하나뿐이라 뺐다."
        ),
    ),
    "azure-retail-prices": Source(
        key="azure-retail-prices",
        url="https://prices.azure.com/api/retail/prices?api-version=2023-01-01-preview",
        pin_kind="digest",
        pin="(고정 불가)",
        redistribution="not-stated",
        note=(
            "Azure **스팟·예약·저축 플랜** 가격. 문서가 *\"unauthenticated experience\"*라고 "
            "명시하며 실제로 자격증명 없이 200이 온다. **버전이 없다** — 태그도 커밋도 "
            "없고 URL도 질의라, 받은 바이트의 sha256만 남긴다. "
            "**재배포 허가 문구가 없다**: 문서가 밝히는 용도는 *\"your own tools for "
            "internal analysis\"*뿐이다. 금지 문구도 없으므로 유도 산출물은 넣되 "
            "`NOTICE`에 그 사실을 밝힌다(AWS Price List는 이와 달리 **명시적 금지**라 "
            "여전히 안 넣는다). "
            "함정 셋을 실측으로 겪었다 — (1) 같은 armSkuName에 Windows 값이 따로 있고, "
            "(2) `Cloud Services`(구형 PaaS)가 같은 크기 이름을 쓴다. 둘을 안 거르면 "
            "SKU의 93.4%%가 값이 여럿이 된다. (3) 예약의 `retailPrice`는 "
            "**기간 총액인데 `unitOfMeasure`는 1,348건 전부 '1 Hour'라고 적는다** — "
            "그대로 읽으면 5,165배 틀린다. savingsPlan은 반대로 진짜 시간당이다."
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
    "tp-ibm": Source(
        key="tp-ibm",
        url=(
            "https://codeload.github.com/IBM-Cloud/terraform-provider-ibm/"
            "tar.gz/refs/tags/v2.4.0"
        ),
        pin_kind="tag",
        pin="v2.4.0",
        note=(
            "IBM Cloud 리소스 타입·제약. MPL-2.0. **주의**: 이 저장소의 "
            "`provider_metadata.json`은 저장소 태그와 내용이 어긋난 전례가 있다"
            "(조사에서 확인). 우리는 그 파일이 아니라 provider.go 등록표와 Go "
            "스키마를 읽으므로 영향받지 않는다. tarball 7.4MB."
        ),
    ),
    "tp-ncp": Source(
        key="tp-ncp",
        url=(
            "https://codeload.github.com/NaverCloudPlatform/terraform-provider-ncloud/"
            "tar.gz/refs/tags/v4.0.6"
        ),
        pin_kind="tag",
        pin="v4.0.6",
        note="NAVER Cloud 리소스 타입·제약. MPL-2.0. tarball 0.4MB.",
    ),
    "tp-nhn": Source(
        key="tp-nhn",
        url=(
            "https://codeload.github.com/nhn-cloud/terraform-provider-nhncloud/"
            "tar.gz/refs/tags/v1.0.9"
        ),
        pin_kind="tag",
        pin="v1.0.9",
        note=(
            "NHN Cloud 리소스 타입·제약. MPL-2.0. **'공개 프로바이더가 없다'고 "
            "적어 뒀던 것이 틀렸다** — 받아 보니 ResourcesMap에 110종이 있다"
            "(2026-07-22 확인). **OpenStack 프로바이더를 리브랜딩한 것이다** — "
            "구현 파일 111개 중 90개가 `resource_openstack_*.go` 그대로다"
            "(GitHub fork 플래그는 False지만 복사본이다). 그래서 이름이 "
            "openstack_* 와 84/110 겹치는 것은 **교차 검증이 아니라 같은 코드다.** "
            "tp-openstack과 독립된 소스로 세지 말 것."
        ),
    ),
    "tp-openstack": Source(
        key="tp-openstack",
        url=(
            "https://codeload.github.com/terraform-provider-openstack/"
            "terraform-provider-openstack/tar.gz/refs/tags/v3.4.0"
        ),
        pin_kind="tag",
        pin="v3.4.0",
        note=(
            "OpenStack 리소스 타입·제약. MPL-2.0. 타입 이름에 API 버전이 붙는다"
            "(`openstack_networking_network_v2`) — 벤더 규약이라 그대로 쓴다. 0.5MB."
        ),
    ),
    "tp-oracle": Source(
        key="tp-oracle",
        url=(
            "https://codeload.github.com/oracle/terraform-provider-oci/"
            "tar.gz/refs/tags/v8.23.0"
        ),
        pin_kind="tag",
        pin="v8.23.0",
        note=(
            "Oracle Cloud 리소스 타입·제약. MPL-2.0. **등록 형태가 다르다** — "
            "다른 provider는 맵 리터럴인데 이쪽은 `RegisterResource(\"oci_x\", …)` "
            "호출이다(996건). tags API가 `vdev-version`을 먼저 주므로 releases를 "
            "봐야 실제 최신을 안다. tarball 28MB."
        ),
    ),
    "tp-alicloud": Source(
        key="tp-alicloud",
        url=(
            "https://codeload.github.com/aliyun/terraform-provider-alicloud/"
            "tar.gz/refs/tags/v1.285.0"
        ),
        pin_kind="tag",
        pin="v1.285.0",
        note=(
            "Alibaba Cloud 리소스 타입·제약. **이 CSP에는 우리가 쓸 공개 리소스 "
            "스키마가 없어서** Terraform provider가 유일한 경로다. 등록 리소스 "
            "1,161종(실측). MPL-2.0. 타입 이름이 Terraform 것이라 id에 "
            "`alicloud_`가 그대로 남는다 — 나중에 공식 스키마가 생겼을 때 무엇을 "
            "바꿔야 하는지 알 수 있게. tarball 6.9MB."
        ),
    ),
    "tp-tencent": Source(
        key="tp-tencent",
        url=(
            "https://codeload.github.com/tencentcloudstack/terraform-provider-tencentcloud/"
            "tar.gz/refs/tags/v1.83.13"
        ),
        pin_kind="tag",
        pin="v1.83.13",
        note=(
            "Tencent Cloud 리소스 타입·제약. alicloud와 같은 이유·같은 방식이다. "
            "MPL-2.0."
        ),
    ),
    "avm-bicep": Source(
        key="avm-bicep",
        url=(
            "https://codeload.github.com/Azure/bicep-registry-modules/tar.gz/"
            "b7c2b1a25b334fe260c5347f70468e47c7dfeef4"
        ),
        pin_kind="commit",
        pin="b7c2b1a25b334fe260c5347f70468e47c7dfeef4",
        note=(
            "Azure Verified Modules의 **실무 배포 순서**. 컴파일된 ARM 템플릿"
            "(`main.json`) 522개의 `dependsOn`에서 타입 쌍을 뽑는다. "
            "스키마 계층(arm-hierarchy)이나 참조(bicep-ref)와 **성격이 다르다** — "
            "'실제로 배포할 때 무엇을 먼저 만드는가'다. 실측으로 한 모듈(storage-account)의 "
            "타입 쌍 6개 중 5개가 우리 graphkb에 없는 새 관계였다(겹침 0). "
            "**저장소 전체 태그가 없다** — 태그가 모듈별 semver(`storage/storage-account/3.0.1`)라 "
            "커밋 SHA로 고정한다. tarball 11MB, 캐시됨. MIT."
        ),
    ),
    "endoflife-date": Source(
        key="endoflife-date",
        url=(
            "https://raw.githubusercontent.com/endoflife-date/endoflife.date/"
            "2ffcafdaa788/products/"
        ),
        pin_kind="commit",
        pin="2ffcafdaa788",
        note=(
            "관리형 서비스의 **버전별 지원 종료일**. 'EKS 1.28 언제까지 쓸 수 있나'에 "
            "답한다. 클라우드 제품 23종이 있다(amazon-eks·amazon-rds-*·azure-*·gke 등). "
            "**라이선스 함정**: 저장소는 MIT이지만 README가 본문 설명을 'adapted from "
            "Wikipedia, CC BY-SA 3.0'이라고 밝힌다. 그래서 **YAML frontmatter의 "
            "`releases:`만 취하고 `---` 뒤의 산문은 버린다** — 그래야 MIT로 남는다."
        ),
    ),
    "gcp-carbon": Source(
        key="gcp-carbon",
        url=(
            "https://raw.githubusercontent.com/GoogleCloudPlatform/"
            "region-carbon-info/49f3f26bfd68/data/yearly/2024.csv"
        ),
        pin_kind="commit",
        pin="49f3f26bfd68",
        note=(
            "GCP 리전별 **무탄소 에너지 비율(CFE)**과 그리드 탄소집약도. Google이 "
            "직접 발표한 값이라 basis=stated다. Apache-2.0. 우리 gcp 리전 43개 중 "
            "42개(98%)와 조인된다. 연도별 파일이 있어 2024를 쓴다 — 최신 연도를 "
            "따라가면 값이 조용히 바뀌므로 연도를 고정한다."
        ),
    ),
    "ccf-emissions": Source(
        key="ccf-emissions",
        url=(
            "https://raw.githubusercontent.com/cloud-carbon-footprint/"
            "cloud-carbon-footprint/f584c549ee35/packages/"
        ),
        pin_kind="commit",
        pin="f584c549ee35",
        note=(
            "AWS·Azure 리전별 배출계수(t CO2eq/kWh). Apache-2.0. **파일 셋을 함께 "
            "읽어야 한다** — 리전 enum, 계수 표, 미국 NERC 지역 상수. 계수 표가 "
            "NERC 상수를 참조하기 때문이다(us-east-1 → SERC). "
            "**GCP 값과 같은 축에 놓고 비교하면 안 된다** — 이쪽은 공개 그리드 "
            "데이터 추정이고 GCP는 자체 발표라 방법론이 다르다. 실측으로 같은 도시의 "
            "값이 다르고 순서까지 뒤집힌다(서울은 gcp가 최저, 도쿄는 aws가 최저)."
        ),
    ),
    "bitnami-charts": Source(
        key="bitnami-charts",
        url=(
            "https://raw.githubusercontent.com/bitnami/charts/"
            "33201f7e944a60abf9af5c652ae1cdb30a267a5e/"
            "bitnami/common/templates/_resources.tpl"
        ),
        pin_kind="commit",
        pin="33201f7e944a60abf9af5c652ae1cdb30a267a5e",
        note=(
            "컨테이너 **규모 프리셋**(nano~2xlarge → cpu/memory). 파일 헤더가 "
            "APACHE-2.0(저장소 메타는 NOASSERTION이라 파일 쪽을 근거로 삼는다). "
            "**원본이 스스로 '프로덕션용이 아니다'라고 적어 두었고**, 그 문장을 값과 "
            "함께 담는다 — 떼면 테스트용 숫자가 권장값이 된다. 컨테이너 규모이지 "
            "인스턴스 규모가 아니다."
        ),
    ),
    "aws-solutions-constructs": Source(
        key="aws-solutions-constructs",
        url=(
            "https://codeload.github.com/awslabs/aws-solutions-constructs/"
            "tar.gz/refs/tags/v2.103.0"
        ),
        pin_kind="tag",
        pin="v2.103.0",
        note=(
            "AWS가 공식으로 묶어 둔 패턴 83개. Apache-2.0. **이름 자체가 조합**이라"
            "(`aws-apigateway-lambda`) 코드를 파싱하지 않고도 신호가 나온다 — CDK나 "
            "SAM은 코드라 비용이 크다. 서비스→리소스 타입 매핑은 우리가 하며 "
            "모호한 것은 담지 않는다."
        ),
    ),
    "aws-cfn-templates": Source(
        key="aws-cfn-templates",
        url=(
            "https://codeload.github.com/aws-cloudformation/aws-cloudformation-templates/"
            "tar.gz/a0f43bc6d20813052892546f445037cf84c75b54"
        ),
        pin_kind="commit",
        pin="a0f43bc6d20813052892546f445037cf84c75b54",
        note=(
            "AWS 공식 CloudFormation 샘플. Apache-2.0. 태그가 없어 커밋 SHA로 고정"
            "(2026-06-01). **Azure에서 통한 동시 출현 방법이 AWS에서도 되는지**를 재려고 "
            "받았고, 실측으로 확인됐다 — Lambda→IAM::Role 100%(38/38), "
            "EC2::Instance→SecurityGroup 90.2%. 다만 템플릿이 299개뿐이라 "
            "Azure(1,152개)보다 훨씬 얇고 앵커도 22종뿐이다. basis=observed."
        ),
    ),
    "widdix-cf-templates": Source(
        key="widdix-cf-templates",
        url=(
            "https://codeload.github.com/widdix/aws-cf-templates/"
            "tar.gz/1a9f04f934179975a3a56c2496d2ed2b27598bd8"
        ),
        pin_kind="commit",
        pin="1a9f04f934179975a3a56c2496d2ed2b27598bd8",
        note=(
            "두 번째 AWS CFN 코퍼스. Apache-2.0. 태그가 의미 없어 커밋 SHA로 고정. "
            "AWS 공식 샘플 299개만으로는 앵커가 22종뿐이라 얇았다 — 이걸 더하면 "
            "템플릿 362개·앵커 30종이 된다. **RDS(15건)·EKS(2건)는 그래도 임계에 "
            "못 미친다** — 그 둘은 여전히 구멍이고, 채운 척하지 않는다. "
            "성격이 다르다: AWS 샘플은 서비스별 데모이고 이쪽은 운영용 스택이라 "
            "CloudWatch 경보가 55.6%로 나온다."
        ),
    ),
    "azure-quickstart-templates": Source(
        key="azure-quickstart-templates",
        url=(
            "https://codeload.github.com/Azure/azure-quickstart-templates/"
            "tar.gz/331d6f394416122008f71342d20c8a2ba8d9b24a"
        ),
        pin_kind="commit",
        pin="331d6f394416122008f71342d20c8a2ba8d9b24a",
        note=(
            "실제 ARM 템플릿 코퍼스. 태그가 없어 커밋 SHA로 고정(2026-07-17). MIT. "
            "**동시 출현 빈도**를 세는 데만 쓴다 — 이 저장소가 못 하던 '가능한 것'과 "
            "'실제로 필요한 것'의 구분이 여기서 나온다(VM 템플릿 330개 중 NIC 100.0%, "
            "bastion 5.5%). tarball이 **326 MB**라 빌드 시점에만 받고 산출물에는 "
            "파생 표만 담는다. 나오는 값은 `basis=observed` — 코퍼스의 사실이지 "
            "클라우드의 사실이 아니다."
        ),
    ),
    "tumblebug-latency": Source(
        key="tumblebug-latency",
        url=(
            "https://raw.githubusercontent.com/cloud-barista/cb-tumblebug/"
            "v0.12.25/assets/cloudlatencymap.csv"
        ),
        pin_kind="tag",
        pin="v0.12.25",
        note=(
            "리전 간 왕복 지연 행렬. **cb-tumblebug의 benchmark.go가 실제로 VM을 "
            "띄워 잰 값**이고 벤더 SLA가 아니다 — 어느 시점의 관측이다. "
            "측정 시각은 원본에 없다(DB의 measured_at은 적재 시각). Apache-2.0. "
            "검증: 대권거리와 상관 0.817, 양방향 4,851쌍 중 99.9%가 서로 다른 값이라 "
            "전치 복사가 아니다."
        ),
    ),
    "tumblebug-src": Source(
        key="tumblebug-src",
        url=(
            "https://codeload.github.com/cloud-barista/cb-tumblebug/"
            "tar.gz/refs/tags/v0.12.25"
        ),
        pin_kind="tag",
        pin="v0.12.25",
        note=(
            "cb-tumblebug **소스 트리**. 미러(assets.dump.gz)와 **같은 태그**를 써야 "
            "번들과 카탈로그가 어긋나지 않는다. `init/templates/*.json`(이름 붙은 "
            "리소스 군 22개)와 `src/core/infra/provisioning.go`(VM 하나에 실제로 "
            "만들어지는 것)를 읽는다. Apache-2.0."
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
    "cb-spider": Source(
        key="cb-spider",
        url=(
            "https://codeload.github.com/cloud-barista/cb-spider/"
            "tar.gz/refs/tags/v0.12.37"
        ),
        pin_kind="tag",
        pin="v0.12.37",
        note=(
            "**우리 런타임 경로의 하부다.** cb-tumblebug이 CSP를 다룰 때 cb-spider "
            "드라이버를 통하므로, 여기 없는 것은 우리 에이전트가 실제로 만들 수 없다. "
            "드라이버 12곳(alibaba·aws·azure·gcp·ibm·kt·ktclassic·ncp·nhn·openstack·"
            "oracle·tencent)의 핸들러에서 **어느 CSP가 무엇을 만들 수 있는지**를 뽑는다. "
            "Apache-2.0. 2.9MB. `core_vendor_map.json`의 매핑 근거도 이 저장소다."
        ),
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
