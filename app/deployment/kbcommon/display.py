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
    "cfn-schema": "CloudFormation 스키마",
    "cfn-description": "CloudFormation 설명문",
    "cdk-oob": "AWS CDK 참조 정의",
    "heuristic": "이름 규칙 추정",
    "human-review": "손 검수",
    "relationshipRef": "스키마의 관계 선언",
    "bicep-flags": "Bicep 타입 플래그",
    "bicep-type": "Bicep 타입 정의",
    "bicep-ref": "Bicep 참조 선언",
    "arm-hierarchy": "ARM 리소스 계층",
    "swagger-field": "Azure REST 정의",
    "azure-limits-doc": "Azure 한도 문서",
    "azure-limits-note": "Azure 한도 문서(각주)",
    "kcc-ref": "GCP Config Connector 참조 선언",
    "kcc-crd-schema": "GCP Config Connector 스키마",
    "kcc-immutable-prefix": "GCP Config Connector 설명문의 불변 표기",
    "kcc-cel-immutable": "GCP Config Connector 검증 규칙(CEL)",
    "kcc-description": "GCP Config Connector 설명문",
    "cb-spider-driver": "CB-Spider 드라이버",
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
    "tf2crd": "2023-09-26에 나온 Terraform 프로바이더 스냅샷에서 온 값이라 낡았을 수 있습니다",
}


def backend_caveat(backend: str | None) -> str | None:
    """상류가 낡은 경우에만 고지 문구를 돌려준다 (아니면 None).

    `direct`·`dcl2crd`는 아무 말도 하지 않는다 — 침묵이 곧 "특별히 밝힐 게 없다"다.
    다만 그 침묵의 뜻은 부르는 쪽이 꼬리말로 한 번 밝혀야 한다(costkb의 성능 주석과
    같은 방식). 침묵을 안전 신호로 오독하게 두면 안 된다.
    """
    return _BACKEND_CAVEAT.get(backend or "")
