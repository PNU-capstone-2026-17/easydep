"""근거의 성격: 원본이 명시했는가, 우리가 짐작했는가.

## 왜 숫자를 버렸나

예전에는 `confidence`가 0.5~1.0의 연속값이었다. **척도의 정의가 어디에도 없었다** —
0.9와 0.95의 차이가 무엇인지, 두 근거를 합치면 어떻게 되는지, 0.8이 "80% 확률"인지
"꽤 믿는다"인지 아무도 답할 수 없었다. 실제로 쓰이는 방식도 임계값 하나
(`>= 0.8`)뿐이었고, 그 임계선이 실제로 가른 것은 `cfn-description` 158건 중
**115건이 정확히 0.8**이라 상수를 0.81로만 바꿔도 결과가 뒤집히는 상태였다.

중요한 것은 얼마나 믿느냐가 아니라 **사실이냐 아니냐**다. 그래서 둘로만 나눈다.

- **`stated`** — 원본이 그렇게 적어 놓았다. 우리는 옮겼을 뿐이다.
  CFN 스키마의 `minimum`, bicep의 `flags`, KCC ServiceMapping의 `gvk.kind`처럼
  기계가 읽으라고 만든 구조화 필드가 여기 속한다.
- **`inferred`** — 우리가 짐작했다. 이름이 비슷하다거나, 산문에 그렇게 쓰여 있다거나,
  구조가 그렇게 생겼다는 이유로 만든 것이다. **틀릴 수 있다.**

여기에 기존 `reviewed`가 세 번째 축으로 붙는다. 짐작이라도 사람이 눈으로 보고
맞다고 한 것은 사실로 취급한다 — 그게 이 저장소가 검수 파일을 두는 이유다.

    사실로 취급 = (basis == "stated") or reviewed

## 라벨이 갈리면 라벨을 쪼갠다

한 evidence 라벨은 성격이 하나여야 한다. 갈린다면 그 라벨이 **서로 다른 두 가지를
뭉뚱그리고 있다**는 뜻이다. 실제로 그런 라벨이 둘 있었고, 신뢰도가 두 값으로 갈리는
것으로 먼저 드러났다(`kbcommon/invariants.py`의 one-basis-per-evidence):

    kcc-ref          1.0 = ServiceMapping의 구조화 필드   ← 명시
                     0.9 = 설명문 정규식                   ← 짐작
    azure-limits-doc 0.9 = 표의 숫자                       ← 명시
                     0.7 = 각주·비수치 표현                ← 짐작

그래서 짐작 쪽을 각각 `kcc-description`, `azure-limits-note`로 떼어 냈다.
"""

from __future__ import annotations

STATED = "stated"
INFERRED = "inferred"

VALUES = (STATED, INFERRED)

# evidence 라벨 → 근거의 성격. **라벨 하나에 성격 하나**가 규칙이다.
BASIS_OF_EVIDENCE: dict[str, str] = {
    # --- graphkb ---
    "relationshipRef": STATED,      # CFN 스키마의 relationshipRef 필드
    "swagger-field": STATED,        # (구) Azure swagger의 arm-id 메타데이터
    "arm-id": STATED,               # (구) 같은 계열
    "arm-hierarchy": STATED,        # ARM 타입명이 곧 계층 (규칙 위반 0/2,223 확인)
    "kcc-ref": STATED,              # KCC ServiceMapping의 gvk.kind
    # projectRef/folderRef/organizationRef/billingAccountRef — GCP 자원 계층.
    # 이름이 KCC의 규약이고 설명문이 "belongs to"라고 말한다(projectRef 273/296).
    "kcc-hierarchy": STATED,
    "cdk-oob": STATED,              # AWS CDK가 손으로 정리해 배포하는 관계표
    # AVM 모듈의 dependsOn. **"모듈이 이 순서로 배포한다"**이지 "API가 강제한다"가
    # 아니다 — tpg-schema의 ForceNew와 같은 구분이다. 모듈 저자가 명시한 것이라 stated다.
    "avm-dependson": STATED,
    "kcc-description": INFERRED,    # CRD 설명문 정규식
    "bicep-ref": INFERRED,          # 대상 객체에 id가 있는지로 판별 (검수표로 확정)
    "heuristic": INFERRED,          # 이름 끝 맞추기
    "cb-spider-driver": INFERRED,   # 드라이버 코드를 사람이 읽고 만든 매핑
    "human-review": STATED,         # 사람이 직접 적어 넣은 것
    # --- capacitykb ---
    "cfn-schema": STATED,           # 스키마 키워드 그대로
    "bicep-flags": STATED,          # flags 비트
    "bicep-type": STATED,           # 스칼라 타입의 제약 필드
    "azure-limits-doc": STATED,     # 한도 문서 표의 숫자
    "azure-limits-note": INFERRED,  # 각주·"varies"처럼 숫자가 아닌 표현
    "cfn-description": INFERRED,    # 설명문에서 뽑은 숫자
    # KCC(GCP). 셋 다 **원본이 명시한 것**이다.
    "kcc-crd-schema": STATED,       # OpenAPI 스키마 키워드 그대로
    "kcc-cel-immutable": STATED,    # x-kubernetes-validations의 self == oldSelf (기계가 강제)
    # `Immutable.` 접두사는 산문에 있지만 KCC의 **표기 규약**이다. CEL과 대조해보니
    # 접두사가 있는데 CEL이 변경을 허용하는 모순은 0건이고, 누락만 19건 있었다.
    # 즉 과다 보고를 하지 않으므로 짐작이 아니라 명시로 취급한다.
    "kcc-immutable-prefix": STATED,
    # 생성된 terraform-provider-google의 스키마 리터럴 그대로.
    # 주의: ForceNew는 "Terraform이 재생성한다"이지 "API가 거부한다"가 아니다.
    "tpg-schema": STATED,
    # terraform-provider-aws. 같은 Terraform이지만 google과 성격이 다르다 —
    # 저쪽은 생성 코드고 이쪽은 사람이 쓴 것이라 틀리는 방식이 다르다.
    "tpaws-schema": STATED,
    # cfn-lint가 별도 관리하는 리전별 허용값. 손 큐레이션이지만 원본이
    # 그렇게 적어 놓은 것이라 stated다.
    "cfn-lint-region": STATED,
    "cfn-lint-conditional": STATED,
    # Azure 명세가 직접 단 주석(`x-ms-mutability`)이다. 교차 검증할 짝이 없는
    # 단일 소스이지만, 우리가 짐작한 게 아니라 원본이 적어 둔 것이라 stated다.
    "swagger-mutability": STATED,
    # 같은 Azure 명세가 직접 단 주석(`x-ms-secret`). 비밀값이라는 표시를 원본이
    # 붙인 것이라 stated다. swagger-mutability와 같은 소스·같은 성격이다.
    "swagger-secret": STATED,
    # AWS 한도. 두 공식 소스가 **같은 값을 말했을 때만** 이 라벨을 단다 —
    # 어느 한쪽만으로는 담지 않는다. 단일 미검증 소스가 감사에서 나온
    # "확신에 찬 오답"의 원인이었다.
    "aws-cross-checked": STATED,
    # --- perfkb ---
    # 이 두 필드는 **한 방향만** 직접 말한다 — "버스트다"/"공유 코어다".
    "aws-burstable-field": STATED,   # BurstablePerformanceSupported=true
    "gcp-shared-cpu-field": STATED,  # IsSharedCpu=true
    # 반대 방향은 "그렇게 분류되지 않았다"에서 끌어낸 추론이다. t1.micro가 이 추론이
    # 깨지는 실례다 — AWS가 false를 주지만 상시 성능이 보장되지는 않는다.
    "aws-non-burstable-inferred": INFERRED,
    "gcp-dedicated-cpu-inferred": INFERRED,
    "azure-family-name": INFERRED,
    # 인스턴스에 실제로 붙어 있는 하드웨어. 측정이 아니라 사양이다.
    "ec2-hardware-probe": STATED,  # family가 standardB로 시작하는지
}


def basis_of(evidence: str) -> str:
    """근거 라벨의 성격. **모르는 라벨은 짐작으로 본다.**

    새 라벨이 조용히 사실로 취급되면 안 된다 — 등록을 잊었을 때 안전한 쪽으로
    틀리는 편이 낫다.
    """
    return BASIS_OF_EVIDENCE.get(evidence, INFERRED)


def is_fact(basis: str, reviewed: bool = False) -> bool:
    """사실로 취급할 수 있는가.

    짐작이라도 사람이 확인했으면 사실이다. 소스에 핀이 박혀 있으므로
    (`kbcommon/sources.py`) 그 확인은 다음 빌드에서도 유효하다.
    """
    return basis == STATED or reviewed


# 화면에 보여줄 말. 숫자 대신 **무엇을 근거로 아는지**를 그대로 적는다.
_WORDS = {STATED: "원본에 명시됨", INFERRED: "짐작"}


def describe(basis: str, reviewed: bool = False) -> str:
    """사람이 읽을 한 마디. `0.8` 같은 숫자보다 이쪽이 실제 정보를 준다."""
    if basis == INFERRED and reviewed:
        return "짐작(검수됨)"
    return _WORDS.get(basis, "출처 불명")
