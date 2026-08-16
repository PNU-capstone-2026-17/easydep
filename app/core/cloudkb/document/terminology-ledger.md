# DepKB 용어 원장

이 문서는 프로그램이 사용하는 관계 판정의 의미와 금지된 해석을 고정한다.

## 관계 판정

| 용어 | 의미 | 금지된 해석 |
|---|---|---|
| `mandatoryForProvisioning` | 관측한 구성에서 대상이 없으면 주체 생성이 거부된다. | 모든 리전과 API 버전에서 영구적으로 필수다. |
| `conditionalForProvisioning` | 구조화된 조건에 따라 대상 필요 여부가 달라진다. | 조건을 생략하고 일반 규칙으로 사용한다. |
| `notMandatoryForProvisioning` | 관측한 구성에서는 대상을 명시하지 않아도 생성된다. | 대상이 기능상 불필요하다. |
| `runtimeRequiredForSignal` | 대상을 제거·변경하면 사전에 정한 인프라 신호가 실패한다. | 애플리케이션 전체 기능이나 성능을 보장한다. |
| `noRuntimeEffectObserved` | 해당 실험의 신호 변화가 관측되지 않았다. | 두 리소스가 무관하다. |

## 실현 방식

| 용어 | 의미 |
|---|---|
| `providerDefaulted` | 명시하지 않은 값을 CSP의 기존 기본값으로 보완한다. |
| `providerCreated` | 명시하지 않은 하위 리소스를 CSP가 생성한다. |
| `explicitlyAttachable` | 없어도 생성되지만 사용자가 별도로 만들어 연결할 수 있다. |

## 조건

| 종류 | 의미 |
|---|---|
| `always` | 관측 범위 안에서 추가 분기 조건이 없다. |
| `conditional` | CSP 모드나 구성값에 따라 결과가 달라진다. |
| `placement` | 개수·리전·가용 영역 같은 배치 조건이다. |
| `exclusiveChoice` | 후보 중 정확히 하나를 선택해야 한다. |
| `compatibility` | 두 리소스의 리전·영역·종류가 호환되어야 한다. |

## 증거 상태

| 용어 | 의미 |
|---|---|
| `evidenceStatus` | 현재 근거가 확인·불충분·충돌 중 어느 상태인지 나타낸다. |
| `replicationStatus` | 동결한 기대 결과를 반복 실행했는지 나타낸다. |
| `studyDisposition` | 현재 연구 범위에 포함했는지 나타낸다. |

`schemaDeclaration`, `controlPlaneValidation`, `provisioningExecution`, `runtimeProbe`는
서로 다른 관측 방법이며 단순한 증거 강도 순위가 아니다.

## 제품 모델에서 다루지 않는 정보

리소스 경계는 독립 식별자와 생성·조회 제어면을 근거로 판별한다. 실험 종료 시의 자원 정리는
비용과 안전을 위한 실행 장치이며, 배포 의존성 claim에는 포함하지 않는다.

범용적인 `required`, `optional`, `holds`, `unknown`, `outOfScope`는 관계군을 숨기므로 claim
판정으로 사용하지 않는다.
