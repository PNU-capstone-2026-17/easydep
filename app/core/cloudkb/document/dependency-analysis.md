# 클라우드 리소스 의존성 분석

## 목적

EasyDep의 의존성 모델은 AWS·Azure·GCP에서 새로운 Docker-on-Linux-VM 배포를 만들고,
생성된 인프라가 애플리케이션 기능을 제공하는 데 필요한 관계만 다룬다.

다음 항목은 제품 모델의 범위가 아니다.

- 연결된 리소스를 어떤 순서로 삭제할지
- 소유 리소스를 삭제할 때 부속 리소스가 함께 삭제되는지
- 교체·마이그레이션·재구성 절차

이러한 정리 순서는 Terraform/OpenTofu의 상태와 참조 그래프가 담당한다. 클라우드 실험은
항상 종료 후 정리하지만, 그 절차를 EasyDep의 배포 설계 모델로 중복 표현하지 않는다.

## 제품에서 사용하는 관계

| 관계군 | 판정 | 의미 |
|---|---|---|
| 프로비저닝 | `mandatoryForProvisioning` | 대상이 없으면 주체를 생성할 수 없다. |
| 프로비저닝 | `conditionalForProvisioning` | 구성 선택에 따라 필요 여부가 달라진다. |
| 프로비저닝 | `notMandatoryForProvisioning` | 해당 실험 조건에서는 대상을 명시하지 않아도 생성할 수 있다. |
| 런타임 | `runtimeRequiredForSignal` | 대상을 제거하거나 바꾸면 정의된 인프라 기능 신호가 실패한다. |
| 런타임 | `noRuntimeEffectObserved` | 정의된 실험에서는 기능 변화가 관측되지 않았다. |

`notMandatoryForProvisioning`은 필요 없다는 뜻이 아니다. CSP 기본값이나 provider 자동 생성으로
보완될 수 있고, 애플리케이션 요구사항 때문에 별도로 필요할 수도 있다.

## 리소스 경계와 삭제 의존성의 구분

벤더 API에서 어떤 대상이 독립적인 create/read/update/delete 연산을 갖는지는 리소스와 하위
구성 요소를 구분하는 근거로 계속 사용한다. 반면 이미 연결된 두 리소스 중 무엇을 먼저
삭제해야 하는지는 배포 생성 모델에서 제외한다. 두 의미를 모두 `lifecycle`이라고 부르지 않는다.

## 산출물

- `claims.source.json`: 조사 과정의 원천 관측
- `claims.json`: 제품이 읽는 프로비저닝·런타임 관계만 포함한 생성 산출물
- `InfraIntent`: 필수 리소스, 생성 순서, 조건, 런타임 의존성, CSP 제약과 근거
- `provision_view`: 생성 및 준비 상태 확인에 필요한 정보

현재 제품 KB에는 프로비저닝 33개와 런타임 12개, 총 45개의 CSP별 claim이 있다.
