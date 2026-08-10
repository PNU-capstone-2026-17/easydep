# 클라우드 의존성·앱 계약·용량 추천 개발 결과

이 문서는 승인된 연구 배경인 `research.md`를 변경하지 않고, 2026년 8월 10일까지 실제로 구현하고
측정한 범위와 한계를 정리한다. 범위는 AWS·Azure·GCP의 Docker-on-Linux-VM 배포다.

## 1. 현재 결론

EasyDep은 제한된 범위에서 다음 경로를 구현했다.

1. 공식 문서에서 확인한 벤더 리소스 사실을 근거 claim으로 저장한다.
2. 자연어 요구를 작은 벤더 중립 capability로 연결한다.
3. capability를 CSP별 복수 리소스와 관계로 투영한다.
4. 생성된 Terraform에서 구성요소 존재와 명시 참조 관계를 검사한다.
5. 앱의 빌드·포트·저장 경로 요구와 배포 환경을 별도 계약으로 대조한다.
6. 부하 측정으로 CPU·메모리·인스턴스·디스크 하한을 계산하고 가격 카탈로그를 필터링한다.

의존성 지식이 설계 입력으로 전달된다는 사실에 더해, 동일 앱·동일 요구분석 출력의 54셀
treatment 절제에서 평균적인 양의 개발 효과를 관찰했다. `full`의 VM delivery 완료는 20/27로
`no-depkb`의 15/27보다 높았고, 근거 참조 완결은 14/27 대 8/27이었다. 다만 capability·CSP·반복
간 차이가 크고 세 번째 반복의 평균 참조 차이는 음수였으므로 안정적인 일반 효과로 주장하지 않는다.
상세 결과는 `depkb-effect-evaluation-20260810.md`에 분리했다.

## 2. 클라우드 리소스 의존성 분석 기준

### 2.1 세 층을 분리한 이유

하나의 중립 리소스가 모든 CSP를 완전히 표현한다고 가정하지 않는다. 모델은 다음 세 층으로
구성된다.

| 층 | 저장하는 내용 | 근거와 역할 |
|---|---|---|
| 벤더 사실 | native 리소스, 참조 관계, 배치·수명주기 제약 | CSP 공식 문서와 provider schema에 직접 결합 |
| 중립 capability | `persistent-block-storage`, `load-balanced-ingress`, `tls-termination` | 요구사항과 벤더 사실을 연결하는 비교 좌표 |
| 실행 투영 | 한 capability에 필요한 CSP별 구성요소·관계·제약 | 1:1을 강요하지 않고 1:N·N:M 대응 보존 |

TOSCA 2.0의 requirement–capability–relationship 분리는 요구와 제공 사실을 구별하는 표현 근거로만
참고했다. EasyDep 스키마가 TOSCA 호환이라는 뜻은 아니다. 세 capability도 클라우드 전체의
대표 표본이 아니라 Docker-on-VM에서 앱 기능 차이를 만들 수 있는 개발축이다.

### 2.2 claim 채택 기준

claim은 CSP 공식 문서의 원문 위치, 수집 시각, SHA-256, 주체·관계·객체를 보존한다. 공식 근거가
없거나 재확인되지 않은 항목은 런타임 지식에서 제외한다. 현재 기준선에는 관계 claim 56개가 있고
49개가 반복 확인됐으며, 실패·대기 claim은 확정 주장에 포함하지 않는다.

관측 지표는 섞어서 하나의 “의존성 정확도”로 만들지 않는다.

- 구성요소 존재: 필요한 native 유형이 선언됐는가.
- 구조 참조: 근거 관계의 양 끝점과 명시적인 참조 edge가 하나 이상 있는가.
- cardinality: 현재는 설명 metadata로만 보존하며 점수에서 제외한다.
- 배치·런타임 제약: 다중 AZ, 동일 AZ, 인증서 수, 디스크 format/mount 등은 각각 독립 gate다.
- 생성 가능성: Terraform 문법과 provider `init/validate`, 이후 실제 `plan/apply`다.
- 앱 기능: ready, 업무 API, 재시작·장애 후 요구 기능이다.
- 정리: `destroy`와 잔여 리소스 0 확인이다.

따라서 리소스가 생성될 수 있다는 사실은 앱 기능 성공을 대신하지 않으며, mount 명령 문자열도
실제 영속성 성공으로 채점하지 않는다.

## 3. 의존성 모델의 효용 측정 결과

### 3.1 확인된 효용

- 새 LLM 출력에서 세 capability stable ID 3/3과 CSP 투영 9/9를 확인했다.
- 동일 LLM 출력 고정 절제에서 입력 해시 9/9가 같았고, `full`은 modeled outcome 9개와
  realization 6개를 만들었으며 `no-depkb`는 모두 0개였다.
- 근거 투영에서 기계적으로 파생한 고정 provider fixture의 구조 참조는 AWS 9/9, Azure 11/11,
  GCP 11/11이었다. 이는 평가기 검증이지 생성 성공률이 아니다.
- 실제 GCP 한 관계에서 backend service–backend group edge를 제거하면 기능이 실패하고 복원하면
  성공하는 결과를 3회 확인했으며 실험 후 잔여 리소스는 0이었다.

이 결과는 DepKB가 요구→벤더 구성의 추적 가능한 중간 표현과 누락 검출 기준을 제공한다는 효용을
뒷받침한다.

### 3.2 생성 지원 효과

같은 완료 앱 스냅샷과 같은 저장된 요구 분석 결과를 사용해 PS-control/treatment ×
full/no-depkb 4셀을 실행했다. 요구사항·설계·앱 생성 LLM 호출은 모두 0회였고 전체 실행은
352.9초였다.

| 조건 | VM delivery | 핵심 관찰 |
|---|---|---|
| control/full | 완료, 107.3초 | 불필요한 LB·HTTPS가 생성돼 정적 6통과/3실패 |
| control/no-depkb | 완료, 39.8초 | 동일하게 불필요한 LB·HTTPS 생성 |
| treatment/full | 실패, 60.7초 | `templatefile` 변수 누락으로 provider 검증 실패 |
| treatment/no-depkb | 완료, 68.5초 | 디스크·attachment 참조는 관측됐으나 mount 관계 실패 |

이 1회 파일럿의 혼합 결과 때문에 같은 입력 경계를 일반화하고 세 capability·세 CSP·두 arm을
3회 반복했다. 총 54셀에서 `full`의 delivery 완료는 20/27, `no-depkb`는 15/27이었고 근거 참조
완결은 14/27 대 8/27이었다. paired 승패와 반복 변동성을 포함한 최종 해석은
`depkb-effect-evaluation-20260810.md`를 따른다. 초기 4셀은 러너 개발 자료로만 유지한다.

## 4. 앱 요구사항–클라우드 환경 충돌

계약은 기술별 정답표가 아니라 다음 세 종류를 분리한다.

- 앱이 요구하는 사실: 빌드 API, 런타임 통합, listen port, 상태 접근 경로와 durability.
- 클라우드가 제공할 capability: VM, 블록 저장소, 부하분산, TLS 등.
- 두 사실의 binding: 의존성, 포트, guest mount, container target, 상태와 HA의 정합성.

동일 고정 입력의 4개 구조 불일치에서 validator는 mismatch 4/4를 조기에 찾고 control 오탐은
0/4였으며 수정 소유 하위 작업도 4/4 일치했다. 최신 동일 앱 스냅샷 LLM 수리 재실행에서는 다음
결과가 나왔다.

| 사례 | 결과 | 상위 단계 재실행 |
|---|---|---:|
| build/runtime dependency | 진단 해소·앱 테스트 통과, 수리 6.7초 | 0 |
| port binding | 진단 해소·앱 테스트 통과, 수리 50.9초 | 0 |
| storage binding | 제한된 1회 수리 뒤 진단·앱 테스트 실패 | 0 |

즉 3건 중 2건은 실패 소유 하위 작업만 수정해 복구됐고, 저장 사례는 실패를 그대로 보존했다.
이는 일반 성공률이 아니라 체크포인트 부분 복구와 계약 gate의 개발 증거다. 실제 cloud endpoint는
이 실행에서 측정하지 않았다. 원시 결과는 `artifacts/confirmatory/app-cloud-repairs.json`이다.

저장 사례만 같은 스냅샷과 소유 작업에서 한 번 더 실행했지만 118.7초 뒤 동일하게 실패했다.
provider 초기화까지 완료됐으나 생성과 한 차례 수정 뒤에도 컨테이너 경로가
`/mnt/evaluation-mismatch`로 남아 계약 경로 `/srv/state`와 일치하지 않았다. 승격 후보는 생성되지
않았으며 실제 Azure `apply`도 수행하지 않았다. 이 반복 실패를 특정 경로용 프롬프트로 고치지 않고
현재 생성기의 한계로 기록한다. 원시 결과는
`artifacts/confirmatory/app-cloud-storage-retry-20260810.json`이다.

## 5. 성능·비용 기반 리소스 추천

### 5.1 산정 방법

Google SRE는 수요 예측과 함께 서버·디스크의 원시 용량을 서비스 용량과 연결하는 정기 부하
시험을 capacity planning의 필수 단계로 제시한다. AWS Compute Optimizer도 vCPU·메모리·스토리지
사양과 일정 기간의 실제 지표를 사용하며, GCP rightsizing도 CPU·메모리의 다일 관측을 사용한다.
따라서 자연어 요구만으로 vCPU나 디스크를 추측하지 않고 다음 순서를 사용한다.

1. 대표 이미지·데이터·요청 형태를 고정한다.
2. 동시성을 단계적으로 높이며 RPS, p95 지연, 오류율, CPU core 사용량, RSS를 측정한다.
3. SLO를 만족한 관측점만 용량 계산에 사용한다.
4. `인스턴스 수 = ceil(목표 RPS / 인스턴스당 관측 RPS)`로 계산한다. 수평 확장 가능성이
   확인되지 않았는데 목표가 관측 범위를 넘으면 질문으로 보류한다.
5. `vCPU 하한 = ceil(p95 사용 core / 목표 CPU 사용률)`을 사용한다.
6. `메모리 하한 = p99 RSS × headroom`을 0.25GiB 단위로 올림한다.
7. 영속 디스크는 `현재 크기 + write당 증가량 × 목표 write RPS × 보존 시간`에 headroom을
   적용한다. 증가량이나 보존기간이 없으면 계산하지 않는다.
8. 이 하한으로 고정 가격 카탈로그를 필터링하고, 예산·리전·성능 경고를 적용한다.
9. 실제 cloud 후보에 배포한 뒤 같은 시험을 반복해 재조정한다.

근거: [Google SRE의 capacity planning](https://sre.google/sre-book/introduction/),
[AWS Compute Optimizer 지표](https://docs.aws.amazon.com/compute-optimizer/latest/ug/metrics.html),
[GCP VM rightsizing 방식](https://docs.cloud.google.com/compute/docs/instances/apply-machine-type-recommendations-for-instances).

### 5.2 개발 실측

완료 체크포인트의 Spring Boot 앱을 고정 Docker 이미지로 빌드해 `/notes` GET에 동시성 4,
15초 부하를 주었다. 컨테이너·임시 이미지는 측정 뒤 제거했다.

| 지표 | 관측값 |
|---|---:|
| 성공 요청 | 3,735/3,735 |
| 관측 처리량 | 229.204 RPS |
| p95 / p99 지연 | 34.132 / 64.316 ms |
| p95 CPU | 5.094 core |
| p99 RSS | 325,373,133 byte |

200 RPS, p95 100ms, 오류율 1% 이하, CPU 목표 사용률 70%, 메모리 headroom 1.25를 적용하면
개발 하한은 8 vCPU·0.5GiB·1대다. 65,032개 고정 카탈로그에서 얻은 compute 목록가격 후보는
다음과 같다.

| CSP·리전 | 후보 | 월 compute 목록가격 |
|---|---|---:|
| AWS ap-northeast-2 | `c5a.2xlarge` | $251.12 |
| Azure eastus | `Standard_D8als_v6` | $235.06 |
| GCP asia-northeast3 | `e2-highcpu-8` | $185.51 |

CPU 값이 짧은 로컬 관측에서 높게 나타났으므로 이 표는 배포 후보이지 최종 권장이 아니다.
스토리지·네트워크·LB·세금·할인도 가격에 포함되지 않는다. 저장 증가량을 측정하지 않은 영속
사례는 `missing_disk_growth_measurement`로 보류됐다. 원시 측정은
`artifacts/measurements/http-capacity-development-point-20260810.json`, 추천 결과는
`artifacts/measurements/capacity-recommendation-development-20260810.json`이다.

## 6. 실행 효율과 주장 경계

환경만 바뀌면 요구사항부터 다시 실행하지 않고 manifest hash와 완료 단계를 검증한 체크포인트를
사용한다. 연결 점검은 동일 앱·요구 분석 결과로 구현/검증과 IaC 경계만 실행하고, 논문용 종단
측정에서만 처음부터 실행한다.

구현 에이전트의 가장 큰 병목은 매 하위 작업마다 `compileJava + bootJar + test` 전체를 반복하는
것이었다. 한 wiring 작업은 155.4초 중 Gradle 검증만 123.6초였다. 권장 정책은 작업마다 compile과
영향 테스트, 기능군 checkpoint에서 통합 테스트, 최종 승격 직전에 전체 test·bootJar·계약 검증을
한 번 수행하는 것이다. 멤버 구현은 직접 바꾸지 않고 오케스트레이터의 공개 검증 정책 경계에서
적용한다.

현재 주장 가능한 범위는 “Docker-on-VM 개발 범위에서 근거 기반 의존성 표현, 교차 계층 충돌
진단, 부분 재개, 실측 하한 기반 VM 후보 추천 경로를 구현하고 개발 사례로 확인했다”까지다.
범용 클라우드 모델의 완결성, DepKB의 인과적 성능 향상, 클라우드 실제 처리량, 총비용 최적성,
멀티 에이전트 구조 자체의 우월성은 주장하지 않는다.

54셀 DepKB 절제 결과를 실패 단계별로 재분석한 결과, 19개 전달 실패 중 18개가 provider schema
검증 단계였고 전달 완료 후 의존 참조가 불완전한 셀은 13개였다. 전달 실패 뒤의 후속 check를 실제
의존 누락으로 세지 않았다. 같은 반복에서 두 arm 모두 전달됐고 한 arm만 구조적으로 완결된 실제
cloud preflight 대조 후보는 GCP LB 반복 1과 AWS LB 반복 2의 두 쌍으로 기계적으로 좁혀졌다.
이는 cloud 실행 준비 완료 판정이 아니며, 입력 정합성·plan·apply·기능을 별도로 통과해야 한다.
TLS는 현재
paired 기능 효과를 측정할 준비가 되지 않았으며 실패 자체를 결과로 유지한다.

두 LB 구조 대조 후보를 cloud preflight한 결과 GCP full은 project binding 누락, GCP no-depkb와
AWS no-depkb는 HTTP 요구와 무관한 TLS 입력 때문에 plan에서 실패했다. AWS full만 plan을 통과해
원본 apply했으나, ALB의 단일 subnet·단일 AZ 구성과 리전에 존재하지 않는 고정 AMI 때문에 25.4초
뒤 provisioning에 실패했다. 앱 기능은 관측하지 못했다. destroy는 13.5초에 통과했고 Terraform
state와 AWS 이름·태그 기반 후속 조회의 잔여는 모두 0이었다. 따라서 정적 평균 개선은 확인됐지만
실제 cloud 앱 기능 개선으로 승격되지 않았다.
