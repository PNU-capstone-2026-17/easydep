# 클라우드 의존성 용어 원장

이 문서는 활성 DepKB와 논문에서 허용되는 과학적 용어를 고정한다. 등록되지 않은 용어는
관계 판정값으로 사용할 수 없고 내부 UI·알고리즘 명칭은 실증 결과로 인용하지 않는다.

## 외부 근거가 있는 개념

| 용어 | 분류 | 근거와 사용 제한 |
|---|---|---|
| resource, node, relationship, requirement, cardinality | 표준 용어 | [OASIS TOSCA 1.3](https://docs.oasis-open.org/tosca/TOSCA-Simple-Profile-YAML/v1.3/os/TOSCA-Simple-Profile-YAML-v1.3-os.html). 본 연구의 CSP 실측 결과를 TOSCA가 보증한다고 해석하지 않음 |
| implicit/explicit dependency, create/destroy order | 도구 용어 | [Terraform references](https://developer.hashicorp.com/terraform/language/expressions/references), [dependency graph](https://developer.hashicorp.com/terraform/internals/graph). Terraform 실행 의미에만 적용 |
| test oracle | 연구 용어 | [Memon et al.](https://www.cs.umd.edu/~atif/papers/MemonASE2003-abstract.html). 예상 결과와 실제 결과의 비교에만 사용 |
| provenance | 표준 용어 | [W3C PROV-N](https://www.w3.org/TR/2013/REC-prov-n-20130430/). 출처 추적이며 사실성 보증이 아님 |
| inter-parameter dependency | 선행연구 용어 | [IDL/RESTest](https://arxiv.org/abs/2005.03320). Web API 요청 파라미터 제약에만 사용 |

## 본 연구의 조작적 정의

| 용어 | 정의 | 금지 해석 |
|---|---|---|
| `mandatoryForProvisioning` | 명시한 구성에서 B를 제외하면 A 프로비저닝이 거부됨 | 모든 리전·API 버전에서 영구적으로 필수 |
| `conditionalForProvisioning` | 구조화된 조건에 따라 B의 의무가 달라짐 | 조건을 생략한 일반 법칙 |
| `notMandatoryForProvisioning` | 명시한 구성에서 B 없이 A가 프로비저닝됨 | B가 쓸모없거나 런타임에도 불필요 |
| `providerDefaulted` | B를 생략하면 CSP가 기존 기본값을 선택함 | 새 B를 생성함 |
| `providerCreated` | B를 생략하면 CSP가 B를 생성함 | 기존 기본값을 선택함 |
| `explicitlyAttachable` | B 없이 생성 가능하지만 B를 명시적으로 연결할 수 있음 | 모든 조합이 호환됨 |
| 생명주기 finding 4종 | 삭제 전후의 차단·분리·종속 삭제·잔존 관측 | 소유권 또는 삭제 정책의 보편 법칙 |
| 런타임 finding 2종 | 사전에 정한 신호의 통제된 제거·복구 관측 | 애플리케이션 전체 기능·성능 보장 |
| `evidenceStatus` | 현재 증거의 확정·불충분·충돌 상태 | 관계 종류 또는 실패 판정 |
| `replicationStatus` | 이번 연구 리비전의 재실행 상태 | 기존 증거의 참·거짓 |
| `studyDisposition` | 연구 포함 또는 범위 제외 | 관계의 존재 여부 |

조건의 `kind` 역시 본 연구의 내부 분류다.

| 조건 종류 | 의미 |
|---|---|
| `always` | 측정한 구성에서는 별도 분기 조건을 두지 않음 |
| `conditional` | CSP 모드나 구성값에 따라 결과가 달라지나 아직 기계식이 완전하지 않음 |
| `placement` | 개수·리전·가용영역 같은 배치 조건 |
| `exclusiveChoice` | 후보 집합의 cardinality가 1..1인 선택 조건 |
| `compatibility` | 두 자원의 리전·존 등 호환 조건 |

관측 방법 `schemaDeclaration`, `controlPlaneValidation`, `provisioningExecution`,
`runtimeProbe`는 각각 고정 스키마 확인, API 사전 검증, 실제 생성 요청, 런타임 신호 측정을
뜻한다. 이는 증거의 강도 순위가 아니라 서로 다른 획득 방법이다.

## 실험 시점의 한계

기존 실험은 탐색 실행 뒤 코드와 결과에서 예상 결과를 복원한 **후향적
(`retrospective`) 프로토콜**이다. 따라서 이를 사전등록 실험이라고 부르지 않는다.
2026-08-07 재측정은 동결된 기존 기대값을 기준으로 수행한 전향적 재현이지만, 원 실험의
후향적 성격을 없애지는 않는다. 이 구분은 `claims.json.methodology`에 기계 판독 가능하게
고정한다.

## 내부 전용어와 폐기어

`startResource`, `selectedStartResource`, `unmeasured`, `unsupported`는 입력·진행·제품 상태다.
논문 finding으로 집계하지 않는다. 기존 `anchor`, `attachable`, 범용
`required/optional/holds/unknown/outOfScope`, 산문 `predicate`, 증거 등급으로서의
`oracle`은 활성 DepKB 스키마에서 폐기한다.

## 벤더 중립 자원 어휘

`network`, `subnet`, `firewall`, `nic`, `publicIp`, `loadBalancer`, `vm`, `disk`,
`sshKey`, `iamRole`, `internetGateway`는 본 연구의 정규화 어휘다. Cloud-Barista에서
그대로 가져온 표준이라고 주장하지 않는다. TOSCA의 Compute·Network·Port·BlockStorage와
각 CSP 공식 모델을 대조하며 CSP 원어와 정규화 결과를 함께 보고한다.

- [AWS EC2 시작 매개변수](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-launch-parameters.html)
- [AWS ALB 구성요소](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/introduction.html)
- [Azure VM 개요](https://learn.microsoft.com/en-us/azure/virtual-machines/overview)
- [Azure Load Balancer 구성요소](https://learn.microsoft.com/en-us/azure/load-balancer/components)
- [Google Cloud VPC](https://docs.cloud.google.com/vpc/docs/vpc)
- [Google Cloud Load Balancing 자원 모델](https://docs.cloud.google.com/load-balancing/docs/load-balancer-resource-model)

Cloud-Barista는 멀티 클라우드 공통 인터페이스의 비교 사례로만 사용한다.
[기술 개요](https://cloud-barista.github.io/technology/)와 현재 코드 명칭이 정확히 일치하지
않으므로 “Cloud-Barista에서 유래한 어휘”라고 서술하지 않는다.
