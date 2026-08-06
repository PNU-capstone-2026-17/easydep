# 클라우드 리소스 의존성 분석

## 연구 정의

본 연구의 클라우드 리소스 의존성 분석은 Docker-on-VM 배포에서 자원 A와 B 사이의
프로비저닝, 생명주기, 런타임 관계를 CSP별 대조 실험으로 판정하는 절차다. 이는 선행 표준의
고유 명칭이 아니라 본 연구의 **조작적 정의**다.

기반 개념은 TOSCA의 node·relationship·requirement·cardinality, Terraform의 참조 기반
의존성과 생성·삭제 순서, 소프트웨어 테스트의 예상/실제 결과 비교, W3C PROV의 출처 추적이다.
상세 출처와 허용·금지 해석은 [용어 원장](terminology-ledger.md)에 고정한다.

## 관계와 판정

| 계열 | 판정 | 관측 규칙 |
|---|---|---|
| 프로비저닝 | `mandatoryForProvisioning` | B가 있는 대조군은 성공하고 B를 제외한 처리군은 거부됨 |
| 프로비저닝 | `conditionalForProvisioning` | 구조화된 조건값에 따라 위 결과가 달라짐 |
| 프로비저닝 | `notMandatoryForProvisioning` | 동일 구성에서 B 없이 A가 생성됨 |
| 생명주기 | `deleteBlockedWhileAttached` | A가 참조하는 동안 B 삭제가 거부되고 참조 제거 뒤 성공함 |
| 생명주기 | `detachRequiredBeforeDelete` | 관계 분리 전 삭제가 거부되고 분리 뒤 성공함 |
| 생명주기 | `cascadeDeletedWithOwner` | 소유자 삭제 뒤 대상도 사라짐 |
| 생명주기 | `persistsAfterOwnerDeletion` | 소유자 삭제 뒤 대상이 남음 |
| 런타임 | `runtimeRequiredForSignal` | 대조 신호 성공 → B 제거 후 실패 → 복원 후 회복 |
| 런타임 | `noRuntimeEffectObserved` | 같은 절차에서 정의한 신호 변화가 관측되지 않음 |

`providerDefaulted`, `providerCreated`, `explicitlyAttachable`은 의존성의 참·거짓이 아니라
프로비저닝 관계가 실제로 구현되는 방식을 기록한다.

## 증거와 재현

`schemaDeclaration`, `controlPlaneValidation`, `provisioningExecution`, `runtimeProbe`는
증거 강도 등급이 아니라 서로 다른 관측 방법이다. CSP apply 응답을 oracle이라고 부르지
않는다. 테스트 oracle은 동결된 예상 결과와 실제 결과의 비교 절차에만 사용한다.

관측이 부족하면 `evidenceStatus=inconclusive`, 범위 제외는
`studyDisposition=excludedByScope`와 사유로 기록한다. 재측정 여부는
`replicationStatus`로 분리한다. 실험은 CSP별로 순차 실행하고 종료 상태와 무관하게 `depkb`
접두 자원을 정리한 뒤 잔존 자원 0건을 확인한다.

스키마 관측의 `cite`는 단순 문자열로 신뢰하지 않는다. 다음 명령은 활성 claim의 고유 좌표
35개를 해시 고정 AWS CloudFormation·GCP Discovery 원문과 커밋된 Azure 스키마에서 직접
해석하며, 경로가 하나라도 없으면 실패한다.

```powershell
python -m app.core.cloudkb.depkb.schema_evidence
```

활성 범위는 AWS·Azure·GCP의 Docker-on-VM 배포다. Kubernetes, VPN, 서버리스와 관리형
애플리케이션 서비스는 범위 밖이며, 이들에 대해 의존성이 없다고 주장하지 않는다.
