# EasyDep 비교평가 계획과 현재 결과

> 갱신일: 2026-08-14  
> 범위: AWS·Azure·GCP의 Docker-on-VM 개발 지원  
> 상태: 과거 비교실험 기록. 비교 평가기는 현재 보류했으며 제품 실행 기준으로 사용하지 않는다.

현재 반복 실행은 `evaluation.easydep.product`가 프론트엔드와 같은 Workspace API를
호출하는 방식만 유지한다. 아래의 기준선·평가 항목과 결과는 새 평가기를 구현하겠다는
뜻이 아니라, 당시 실험의 의도와 한계를 보존하는 기록이다.

## 1. 무엇을 비교하는가

EasyDep은 클라우드 리소스 선택기만이 아니라 요구사항부터 설계·구현·시험·배포 산출물을
순서대로 만들고 서로 대조하는 개발 지원 시스템이다. 따라서 주 비교 질문은 다음과 같다.

> 같은 복잡한 요구와 예산 안에서 EasyDep이 기준 시스템보다 완결되고 서로 모순되지 않으며
> 실제로 동작하는 설계·코드·시험·배포 산출물을 만드는가?

외부 비교는 시스템 전체 차이만 평가한다.

| 조건 | 방식 | 고정 버전·역할 |
|---|---|---|
| B1 | 단일 LLM + CoT | 단일 에이전트 기준선 |
| B2 | MetaGPT | 0.8.2 기반 SOP 멀티 에이전트 |
| B3 | ChatDev | 1.1.6 기반 대화형 단계 시스템 |
| P | EasyDep full | 제안 시스템 |

MetaGPT와 ChatDev도 순차 단계가 있으므로 “단계가 있다는 사실”은 차별점이 아니다.
EasyDep의 평가 대상은 기계 판독 계약, 요구사항–설계–code/test–CSP 리소스 추적,
공통 검증 gate와 checkpoint 부분수정의 결합이다. 외부 비교 결과로 DepKB나 멀티 에이전트
구조 하나의 인과 효과를 주장하지 않는다.

구성요소 효과는 EasyDep 내부에서만 제한적으로 본다.

| 절제 | 질문 | 해석 한계 |
|---|---|---|
| `no-depkb` | CSP 리소스 근거가 누락·참조·기능 결과에 영향을 주는가 | 같은 upstream 입력과 평가기를 사용해야 함 |
| `no-verification` | 단계별 검증 피드백이 오류 발견·수정에 영향을 주는가 | full-restart 대비 효율을 직접 입증하지 않음 |

## 2. 평가 사례

수강신청 자체가 cloud-native이기 때문이 아니라, 제한 수량에 대한 동시 변경과 공유 영속
상태를 한 사례에서 시험할 수 있어 합성 통합 벤치마크로 사용한다. 모델과 평가기는 도메인
이름이 아니라 Workload·Endpoint·Connection·영속성·반드시 지켜야 할 업무 규칙을 입력받는다.

### 2.1 고정 업무 범위

- 강좌 조회
- 신청과 취소
- 정원 초과 금지
- 동일 학생의 중복 신청 금지
- 재시작 후 신청 데이터 보존

인증·결제·선수과목·대기열·마이크로서비스·관리형 DB는 제외한다. 배포 실험은 합성 데이터와
임시 HTTP endpoint만 사용하며 HTTPS/TLS, 인증서와 도메인 관리는 평가 범위에 포함하지 않는다.

동시성 oracle은 같은 seed와 초기 DB에서 barrier로 요청을 동시에 시작하고 응답뿐 아니라
최종 DB row와 잔여 좌석을 함께 검사한다.

- 남은 좌석 1개에 서로 다른 학생 N명이 신청하면 성공은 정확히 1개다.
- 같은 학생이 같은 강좌에 N회 신청해도 최종 신청 row는 1개 이하다.

### 2.2 배포 조건

| ID | 동일 업무 | 배포 구조 | 보는 것 |
|---|---|---|---|
| E1 | 위 업무 전체 | App 1개 + 자체 운영 상태 Workload 1개 + Volume | 산출물 연쇄, 동시성, 영속성 |
| E2 | E1과 동일 | CSP 관리형 앱 VM 그룹 replica 2개 + LB + 같은 상태 Workload | 공유 상태 연결, 앱 VM 장애 중 업무와 자동 교체 |
| D1 | 제한 수량 예약으로 용어만 변경 | E2와 같은 업무 규칙 | 수강신청 문자열 오버피팅 탐지 |

첫 PoC의 상세 요구사항과 실행 입력은
[`evaluation/baselines/course-registration-cases/README.md`](../evaluation/baselines/course-registration-cases/README.md)에
둔다. 아직 기존 동결 suite에는 포함하지 않는다.

E2는 App 계층의 장애 대응만 평가한다. 단일 상태 Workload가 있으므로 종단 HA나 Zone
전체 장애 무중단을 주장하지 않는다. D1은 가까운 용어 전이일 뿐 모든 도메인 일반화를
증명하지 않는다.

P1~P3과 기존 holdout은 동결된 구성요소·smoke 회귀다. 새 주 비교의 표본이나 토폴로지
근거로 사용하지 않으며 추가 대규모 실행도 하지 않는다.

## 3. 공정한 실행 조건

모든 조건에 다음을 동일하게 준다.

- 자연어 요구사항과 동결된 업무 oracle
- 가능한 경우 같은 LLM endpoint·model·seed·temperature
- wall-clock, token, LLM call과 tool 예산
- 네트워크·Docker·Terraform 실행 환경
- 최종 산출물의 의미 요구와 독립 공통 평가기

framework가 요구하는 출력 형식은 adapter로 정규화하되 내부 EasyDep 스키마를 기준군에
강요하지 않는다. 모델·endpoint가 달라지면 run별로 기록하고 “시스템+모델 묶음” 결과로만
해석한다. 특정 MetaGPT·ChatDev 버전 결과를 최신판이나 framework 전체로 일반화하지 않는다.

runner가 종료하고 공통 평가기가 성공 또는 실패를 정상 분류하면 subject 실패도 분모에
포함한다. 파일 누락, 잘못된 코드·IaC, timeout과 자체 과도한 호출로 생긴 rate limit은
시스템 결과다. 공통 외부 endpoint 장애, CSP control-plane 장애, runner 고장처럼 방식 밖의
사건만 `environmentFailure` 또는 `harnessFailure`로 검열한다.

## 4. 공통 평가 gate

### 4.1 산출물 연쇄

실행 전에 요구사항별 필수 동작 조건과 연결 관계를 확정한다. subject가 적은 trace ID를 그대로
신뢰하지 않고 공통 evaluator가 다음을 의미 단위로 대조한다.

```text
요구사항 ID
→ 설계 결정·ERD·핵심 sequence·OpenAPI·RuntimeContract
→ ResourcePlan·배포 구조도
→ code·test·Terraform Plan
```

지표는 요구 추적률, 필수 링크 recall, 교차 산출물 모순 수와 근거 없는 결정 수다.
공통 의미 adapter가 완성되기 전에는 EasyDep의 산출물 연쇄 우위를 주장하지 않는다.

### 4.2 사전 배포와 로컬 기능

- 구조·schema와 데이터 일관성 검사
- compile, unit·integration test와 테스트 0개 성공 거부
- Docker image build, readiness와 black-box 업무 oracle
- 동시 요청에서도 지켜야 할 업무 규칙
- Volume을 유지한 새 container/VM에서 데이터 재조회
- 실행이 만든 container·volume의 정리

### 4.3 배포 정합성

같은 `ResourcePlan`에서 배포 구조도와 Terraform을 생성하고 저장된 Plan JSON을 대조한다.
VM 그룹·desired capacity·Region/Zone·network/Subnet·Volume·Endpoint/LB·port와 native 참조가
일치해야 한다. `terraform validate`는 실제 배포나 기능 성공을 대신하지 않는다.

### 4.4 실제 cloud 기능과 NFR

```text
apply → ready → 같은 업무 oracle → NFR
→ 요구된 app VM fault → 자동 교체와 업무 재확인
→ destroy → 해당 run이 만든 리소스 잔여 0
```

고정 workload profile은 image·seed·operation 비율·payload·목표 도착률·시험시간·warm-up,
p95와 오류율 상한, 부하 발생기 위치·quota를 포함한다. offered RPS, achieved success RPS,
dropped request, p95, error rate를 합격 지표로 기록한다. CPU·memory·network·disk I/O는 요구
threshold가 없으면 병목 진단값이다.

App VM 장애 시험은 요청을 계속 보내면서 한 instance를 중지하고, 허용 복구시간 동안 업무
업무 규칙과 SLO를 만족하는지 및 CSP 관리형 VM 그룹이 instance를 교체하는지 확인한다.

### 4.5 효율과 부분수정

- 전체·단계·하위 작업 시간
- LLM call, token, retry와 rate limit
- 실패 소유 단계 식별
- 상류 checkpoint 보존 여부와 재실행 범위
- 복구 후 최종 기능 성공

부분수정 효과는 고정 오류를 주입한 관찰에서 “상류 단계 재실행 0회로 복구했다”처럼 기술한다.
별도 full-restart 통제군 없이 일반적으로 시간을 줄였다고 주장하지 않는다.

## 5. VM 추천의 별도 평가

개발 시스템 비교는 모든 방식에 동일한 평가 VM 사양을 사용한다. 그렇지 않으면 더 비싼
리소스를 쓴 효과와 산출물 품질이 섞인다.

추천 구성요소는 고정된 같은 image·seed·workload profile로 별도 평가한다.

1. 목표 부하·p95·오류율 또는 명시 CPU·memory 하한이 없으면 `unresolved`다.
2. Docker 관측으로 CSP 카탈로그 후보를 좁히면 `provisional`이다.
3. 특정 CSP·SKU의 cloud 시험이 SLO를 통과하면 해당 profile에서만 `validated`다.

결과는 SLO 통과 여부와 compute on-demand 목록가격을 함께 보고한다. disk, LB, public IP,
egress·세금·할인은 제외하고 최적·최소 SKU라고 부르지 않는다. Disk는 용량 하한만 다루며
IOPS·throughput 최적화는 평가 범위 밖이다.

## 6. 실행 규모와 중단 기준

| 실행 | 상한 |
|---|---:|
| E1·E2 × 네 시스템 1회 파일럿 | 8회 |
| runner와 evaluator가 결과를 정상 분류할 때 3회로 확대 | 종단 총 24회 |
| D1 × 네 시스템 개발 실행 | 4회 |
| E2 `no-depkb`, `no-verification`; full 재사용 | 추가 6회 이하 |
| 실제 cloud | 사전 지정 CSP의 E1·E2 우선, 최대 6회 |

파일럿에서 시간·비용·검열률을 확인한 뒤 반복을 확대한다. 동일 오류가 반복되거나 환경 실패가
지배적이면 대규모 실행을 중단하고 harness를 먼저 고친다. 반복이 부족하면 유의확률을 만들지
않고 사례별 원시 결과와 한계를 보고한다.

## 7. 현재까지 나온 결과

### 7.1 DepKB

- native 의존성 제품 KB: 프로비저닝 33개, 런타임 12개 claim
- 반복 상태: 성공 38, 실패 5, 대기 2
- 고정 LLM 출력 projection 처치 충실도: stable capability ID 3/3, CSP projection 9/9
- 동일 앱·동일 요구분석 출력 54셀 개발 절제:
  - VM delivery 완료: full 20/27, no-depkb 15/27
  - 근거 참조 완결: full 14/27, no-depkb 8/27

평균적인 양의 개발 관찰이지만 capability·CSP·반복별 편차가 크고 세 번째 반복의 평균 참조
차이가 음수였다. 일반 효과나 인과효과로 확정하지 않는다.

### 7.2 앱–cloud 계약과 부분수정

- 고정 입력 mismatch 4/4 검출, control false positive 0/4, owner 4/4
- build dependency·port·storage 세 snapshot에서 소유 하위 작업부터 복구하고 로컬 HTTP 통과
- GCP 한 backend 관계의 제거→기능 실패→복원 성공 3회, 잔여 0

이는 구조화 진단과 부분 복구가 가능한 개발 증거다. 세 CSP의 실제 cloud 기능 효과나 자연어
질문→답변→계약 갱신의 일반 효과는 아직 아니다.

### 7.3 종단 실행

- P1 AWS·Azure·GCP에서 로컬 앱 기능과 CSP 정적 oracle 통과
- 실제 cloud 기능 개입은 GCP 한 관계, Azure 영속 apply 후보는 보류
- E1·E2와 네 시스템의 반복 비교 결과는 없음

따라서 현재 말할 수 있는 것은 제한 범위의 구조와 개발 관찰이다. EasyDep이 기준군보다
우수하다거나 실제 workload right-sizing·종단 HA·전체 비용 최적화를 달성했다는 주장은
아직 할 수 없다.

## 8. 구현 순서

1. 축소 `ResourcePlan`과 구조 검증기
2. 같은 plan의 CSP 구조도·Terraform renderer와 Plan JSON 대조
3. E1 다중 Workload·동시성·영속성 로컬 종단
4. E2 관리형 앱 VM 그룹·LB·자동 복구 종단
5. 고정 profile의 VM 후보와 실제 cloud 재검증
6. 공통 의미 adapter
7. 8회 파일럿 후 조건부 반복 확대

새 capability나 범용 토폴로지 유형을 추가하기보다 이 경로를 끝까지 연결하는 것을 우선한다.
