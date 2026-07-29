"""내부 식별자를 사람이 읽는 말로 바꾼다.

**왜 따로 두나.** 지금까지 이건 프롬프트로만 막혀 있었다 — "내부 ID를 답변에 쓰지
말라"고 모델에게 시키는 식이다. 그런데 실측해보니 **API가 애초에 내보내고 있었다**:

    aws::AWS::EC2::Subnet 의 변경 시 재생성되는 속성 11개:
      - AvailabilityZone: ... (근거 cfn-schema, 원본에 명시됨)

`aws::` 접두사도, `cfn-schema`라는 라벨도 사용자 어휘가 아니다. 모델에게 지우라고
시키는 것보다 **API가 안 만드는 편이 확실하다.** 모델은 시키는 걸 잊지만 함수는 안 잊는다.

근거를 없애자는 게 아니다 — 어디서 온 정보인지는 계속 밝혀야 한다. 다만 그걸
`cfn-schema`가 아니라 "CloudFormation 스키마"라고 적자는 것뿐이다.
"""

from __future__ import annotations

#: `provider::` 접두사를 떼도 무엇인지 알 수 있는 타입 이름들.
#: 벤더 타입 이름은 그 자체로 프로바이더를 밝힌다(`AWS::EC2::Subnet`,
#: `Microsoft.Network/virtualNetworks`). 접두사는 우리 내부 색인 키일 뿐이다.
_KNOWN_PREFIXES = ("aws::", "azure::", "gcp::", "core::")

#: 근거 라벨 → 사람이 읽는 출처 이름.
#: 여기 없는 라벨은 그대로 내보낸다 — 새 라벨이 생겼을 때 조용히 이름이 사라지는
#: 것보다, 낯선 이름이 보여서 여기에 추가하게 되는 편이 낫다.
_EVIDENCE_NAMES = {
    "cfn-schema": "CloudFormation schema",
    "cfn-description": "CloudFormation description text",
    "cdk-oob": "AWS CDK reference definition",
    "avm-dependson": "deployment order in an Azure Verified Module",
    "heuristic": "name-convention guess",
    "human-review": "hand review",
    "relationshipRef": "relationship declaration in the schema",
    "bicep-flags": "Bicep type flags",
    "bicep-type": "Bicep type definition",
    "bicep-ref": "Bicep reference declaration",
    "arm-hierarchy": "ARM resource hierarchy",
    "swagger-field": "Azure REST definition",
    "azure-limits-doc": "Azure limits documentation",
    "azure-limits-note": "Azure limits documentation (footnote)",
    "kcc-ref": "GCP Config Connector reference declaration",
    "kcc-crd-schema": "GCP Config Connector schema",
    "kcc-immutable-prefix": "immutability marker in GCP Config Connector description text",
    "kcc-cel-immutable": "GCP Config Connector validation rule (CEL)",
    "kcc-description": "GCP Config Connector description text",
    "tpg-schema": "Terraform Google provider schema",
    "tpaws-schema": "Terraform AWS provider schema",
    # 프로바이더 이름을 라벨에 박지 않는다. 예전엔 "(Alibaba·Tencent)"였는데 이후
    # ibm·ncp·openstack·oracle·nhn까지 같은 라벨을 쓰게 되면서, NHN 타입의 근거를
    # **"Alibaba·Tencent"라고 말하고 있었다** — 출처를 틀리게 대는 것이다.
    # 어느 프로바이더인지는 type_id의 네임스페이스가 이미 말한다(`nhn::nhncloud_…`).
    "tpcsp-schema": "that CSP's Terraform provider schema",
    "cfn-lint-region": "cfn-lint per-region allowed values",
    "cfn-lint-conditional": "cfn-lint conditional allowed values",
    "swagger-mutability": "mutability marker in the Azure REST specification",
    "swagger-secret": "secret-value marker in the Azure REST specification",
    "ec2-hardware-probe": "EC2 instance hardware check",
    "azure-sizes-doc": "Azure VM size documentation table",
    "cyclenerd-gcp-catalog": "GCP machine catalog (community-curated)",
    "aws-price-list": "AWS Price List API",
    "botocore-doc": "AWS SDK description text",
    "aws-cross-checked": "cross-checked against two official AWS sources",
    "cb-spider-driver": "CB-Spider driver",
    "svcmap-cross-checked": "MS comparison table + diagrams taxonomy cross-checked",
    "ms-learn-comparison": "Microsoft Learn service comparison table",
    "mingrammer-taxonomy": "diagrams library taxonomy",
    "svcmap-reviewed": "hand-reviewed mapping (no independent source)",
    "arm-id": "arm-id marker in the Azure specification",
    "kcc-hierarchy": "GCP Config Connector resource-hierarchy reference",
    # perfkb. **한 방향만 원본이 말한다** — "버스트다"/"공유 코어다"는 표시 필드이고,
    # 반대 방향은 "그렇게 분류되지 않았다"에서 끌어낸 추론이다. 라벨에서 갈라 둔다.
    "aws-burstable-field": "AWS burst instance marker",
    "aws-non-burstable-inferred": "AWS does not classify it as burst (inferred)",
    "gcp-shared-cpu-field": "GCP shared-core marker",
    "gcp-dedicated-cpu-inferred": "GCP does not mark it as shared-core (inferred)",
    "azure-family-name": "Azure instance family name (inferred)",
    "avm-module": "resources an Azure Verified Module deploys",
    "tumblebug-dynamic": "resources cb-tumblebug's dynamic creation makes",
    "tumblebug-template": "cb-tumblebug curated template",
    "aws-solutions-construct": "AWS Solutions Constructs pattern",
    "kcc-sample": "GCP Config Connector sample configuration",
    "aqt-corpus": "measured over the Azure Quickstart template corpus",
    "awscfn-corpus": "measured over the AWS CloudFormation sample corpus",
    "bundle-cross-checked": "the stated source and the corpus agree",
    # 산문 지침이라 이름부터 "지침"이다 — "문서"라고만 쓰면 사실 소스처럼 읽힌다.
    "pattern-advisory": "design pattern document (advisory, not fact)",
    "tumblebug-networkinfo": "cb-tumblebug network planning table",
    "tumblebug-k8sinfo": "cb-tumblebug cluster requirements table",
    "bitnami-preset": "size preset in the bitnami chart",
}


def display(type_id: str) -> str:
    """내부 타입 id에서 `provider::` 접두사를 떼어 사용자에게 보일 이름으로.

    >>> display("aws::AWS::EC2::Subnet")
    'AWS::EC2::Subnet'
    >>> display("core::vNet")
    'vNet'

    모르는 접두사는 건드리지 않는다 — 잘못 자르면 이름이 망가진다.
    """
    for prefix in _KNOWN_PREFIXES:
        if type_id.startswith(prefix):
            return type_id[len(prefix):]
    return type_id


def evidence_name(label: str) -> str:
    """근거 라벨을 사람이 읽는 출처 이름으로 (모르는 라벨은 그대로)."""
    return _EVIDENCE_NAMES.get(label, label)


#: 상류 파이프라인 → 사용자에게 밝힐 한 마디. **낡은 것만 적는다.**
#: 전부 적으면 매 줄에 꼬리표가 붙어 노이즈가 되고, 그러면 진짜 경고가 안 보인다.
_BACKEND_CAVEAT = {
    "tf2crd": "this value comes from a Terraform provider snapshot taken on 2023-09-26, so it may be outdated",
}


def backend_caveat(backend: str | None) -> str | None:
    """상류가 낡은 경우에만 고지 문구를 돌려준다 (아니면 None).

    `direct`·`dcl2crd`는 아무 말도 하지 않는다 — 침묵이 곧 "특별히 밝힐 게 없다"다.
    다만 그 침묵의 뜻은 부르는 쪽이 꼬리말로 한 번 밝혀야 한다(costkb의 성능 주석과
    같은 방식). 침묵을 안전 신호로 오독하게 두면 안 된다.
    """
    return _BACKEND_CAVEAT.get(backend or "")
