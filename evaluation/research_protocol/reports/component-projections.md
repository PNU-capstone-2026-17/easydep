# 단일 변수 구성 요소 사영 계약

이 문서는 종단 과제 P1~P3의 점수 차이를 특정 구성 요소의 효과로 잘못 해석하지 않기 위한 개발 단계 계약이다. 실행 가능한 기계 판독본은 `component-projections.json`이다.

## 비교 단위

각 쌍은 애플리케이션 API, 빌드·컨테이너 계약, CSP, 리전과 seed를 고정하고 클라우드 조건 하나만 바꾼다.

| 쌍 | 통제 조건 | 처치 조건 | 별도 기능 게이트 |
|---|---|---|---|
| 영속 스토리지 | 애플리케이션 데이터가 일시적임 | 데이터 디스크·연결·게스트 마운트 | 컨테이너 재생성 뒤 데이터 조회 |
| 다중 VM 부하분산 | 단일 VM 진입 | 다중 영역 VM·부하분산·상태 검사 기반 백엔드 | 각 백엔드 상태와 업무 API |
| HTTPS 종료 | HTTP 부하분산 진입 | 같은 부하분산 토폴로지의 HTTPS 종료 | TLS 연결과 업무 API |

HTTPS 쌍은 단일 VM의 HTTP와 HTTPS를 비교하지 않는다. 두 조건 모두 같은 부하분산 토폴로지를 사용하고 listener와 certificate 결합만 바꿔야 TLS 효과를 분리할 수 있다.

런타임 provider projection도 이 비교 경계를 따른다. `load-balanced-ingress`는 인증서 없는
HTTP 실현을 선택하고, `https-load-balanced-ingress`는 같은 계열의 HTTPS listener·certificate
결합을 포함한 실현을 선택한다. 일반적인 HTTPS 요구만으로 부하분산기를 강제하지 않는다.

## capability와 벤더 구성의 표현

capability는 공통 리소스 정체성이 아니라 비교할 기능이다. 실제 결정에는 다음 정보를 보존한다.

1. 구성요소 `id`: 해당 CSP 실현 안에서 구분하는 역할
2. `terraformType`과 `terraformKind`: 독립 리소스, 데이터 소스, 중첩 블록 또는 게스트 설정
3. `relations`: 구성요소 사이의 참조와 벤더별 제약

Azure Application Gateway의 listener, backend pool, probe, routing rule, certificate는 서로 독립된 최상위 리소스가 아니라 하나의 `azurerm_application_gateway` 안에 있는 중첩 블록이다. 반면 GCP 외부 애플리케이션 부하분산은 forwarding rule, proxy, URL map, backend service, instance group, health check 등 여러 최상위 리소스로 구성된다. 평가기는 이 차이를 없애지 않고 구성 요소 인스턴스와 연결 관계를 각각 기록한다.

## 정적 평가와 기능 평가의 경계

정적 평가는 두 항목을 분리한다.

- 구성 요소 존재: 필요한 리소스·데이터 소스·중첩 블록이 선언됐는가
- 관계 존재: 선언 간 참조 또는 같은 소유자 내부 이름 결합이 관측되는가

동일 가용 영역, 전용 subnet, 디스크 포맷·마운트 같은 제약은 단순 참조만으로 완전히 증명하지 않는다. 정적 분석 결과에는 `requires-separate-gate`로 남기고 다음 단계에서 검증한다.

- Provider 고정 버전의 `validate` 및 필요 시 `plan`
- 실제 생성·삭제 가능 여부와 정리 성공 여부
- 컨테이너 재생성 후 데이터 보존
- 각 백엔드의 상태 검사와 장애 허용
- TLS 연결과 업무 API 응답

따라서 “리소스를 만들 수 있음”과 “애플리케이션 기능이 작동함”은 서로 다른 성공 조건이다.

## 근거

- AWS EBS는 볼륨과 인스턴스가 같은 가용 영역에 있어야 하며 연결 뒤 게스트에서 파일 시스템과 마운트가 필요하다. [볼륨 연결](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-attaching-volume.html), [볼륨 사용](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-using-volumes.html)
- Azure 관리 디스크는 VM 연결과 게스트 마운트가 별개다. [Linux VM 데이터 디스크 연결](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/attach-disk-portal)
- GCP 영속 디스크 연결은 독립 연결 리소스 또는 VM 내부 중첩 블록으로 표현할 수 있다. [디스크 연결](https://cloud.google.com/compute/docs/disks/attach-disks)
- Azure HTTPS 종료는 L4 Azure Load Balancer가 아니라 Application Gateway listener와 인증서 결합으로 모델링한다. [구성 요소](https://learn.microsoft.com/en-us/azure/application-gateway/application-gateway-components), [TLS 종료](https://learn.microsoft.com/en-us/azure/application-gateway/ssl-overview)
- AWS Application Load Balancer의 HTTPS listener에는 서버 인증서와 보안 정책이 필요하며
  프런트엔드 TLS를 종료한다. [HTTPS listener 생성](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/create-https-listener.html)
- GCP 외부 애플리케이션 부하분산은 HTTPS proxy, URL map, backend service와 backend group의 합성이다. [외부 애플리케이션 부하분산](https://cloud.google.com/load-balancing/docs/https)

## 검증 상태와 Provider 캐시

현재 계약 상태는 `development-candidate`다. AWS `5.100.0`, AzureRM `5.0.1`, Google `5.45.2`의 스키마 감사와 최소 관계 fixture의 `validate`는 통과했다. 실제 생성·기능 게이트와 독립 검토가 끝나기 전에는 확증 실험용 gold oracle로 승격하지 않는다.

## CNA 합성 사례의 출처 경계

PURE 같은 일반 요구사항 코퍼스는 CNA 배포 capability의 후보 빈도나 대표성 근거로 사용하지
않는다. 이 개발 사례의 입력 근거는 기존 CNA 애플리케이션 기능, 3사 공식 문서, CSP별
리소스 실현표와 Provider 검증 자료로 제한한다. LLM은 근거 카드에 있는 capability를 자연어 요구사항
문장으로 표현하는 역할만 맡으며, 리소스 의존성이나 oracle을 새로 결정하지 않는다.

현재 18개 component case는 구성 요소 사영, 통제·처치 쌍, 기능 oracle에는 연결되지만 합성
당시의 모델·프롬프트 해시·seed·근거 카드 해시가 보존되지 않았다. 따라서 개발 파일럿에는
사용할 수 있어도 재현 가능한 LLM 합성 코퍼스라고 소급해 주장하지 않는다. 다음 사례를
추가하거나 기존 사례를 재생성할 때는 사례 묶음에 다음 네 값을 함께 보존한다.

- 합성 모델과 버전
- 시스템·사용자 프롬프트의 정규화 해시
- seed 및 sampling 설정
- 입력 근거 카드의 정규화 해시

`python -m evaluation.research_protocol.commands.cna_case_audit`은 각 축의 3사 공식 근거, projection
구성 요소·관계, 개발 suite의 통제·처치 쌍, 고정된 앱 기능 oracle과 합성 계보를 분리해
점검한다. 근거와 쌍 계약이 통과해도 합성 계보가 없으면
`eligibleAsReproducibleSyntheticCorpus=false`로 남는다.

검증된 플러그인은 `.easydep/provider-plugin-cache`에 위 세 버전만 직렬로 보존한다. 감사기는 허용 목록 밖의 Provider나 버전을 발견하면 자동으로 지우지 않고 실행을 거부한다. 캐시는 약 1.08GB이며 연구 종료 또는 버전 교체 때 전용 디렉터리 전체를 정리한다. 캐시는 다운로드를 줄이지만 `init`의 설치·복사 시간까지 제거한다고 가정하지 않으므로 단계별 벽시계 시간을 계속 기록한다.

## 실행과 추정량

개발 suite는 `evaluation/baselines/component-cases/suite.json`이며 다음 108개 run을 직렬 실행한다.

```text
3개 단일 변수 쌍 × 2개 조건 × 3개 CSP × 2개 EasyDep 조건 × 3회 반복 = 108회
```

- `easydep-full`: 근거 기반 DepKB 사용
- `easydep-no-depkb`: 같은 오케스트레이터·모델·검증 경로에서 DepKB 입력만 제거

실행 전 schedule만 확인할 수 있다.

```powershell
python -m evaluation.experiment --study component --split development --print-schedule
```

파일럿은 `--case`, `--arm`, `--repetition`, `--limit`로 명시적으로 제한한다. 실패한 EasyDep 작업은 동일 run의 실패 checkpoint에서 수정하며 처음부터 새 run으로 대체하지 않는다. 독립 반복만 새 run으로 시작한다.

요약기는 각 arm 안에서 `처치 - 통제`를 먼저 계산하고, DepKB 효과를 다음 차이의 차이로 기록한다.

```text
(easydep-full의 처치-통제) - (easydep-no-depkb의 처치-통제)
```

완전한 통제·처치 쌍만 이 추정량에 포함하고, timeout·클라우드 스케줄링·평가 누락은 탈락 수와 원인을 별도로 보고한다. 이 개발 suite는 설계 검증용이며 독립 검토와 실제 기능 파일럿 전에 확증 결과로 사용하지 않는다.
