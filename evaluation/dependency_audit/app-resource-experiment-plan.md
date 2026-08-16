# 앱–리소스 의존성 개입 실험

## 목적과 범위

이 실험은 특정 업무 도메인의 정답을 평가하지 않는다. Docker-on-VM에 배포되는 상태 사용
애플리케이션이 실제 기능을 제공하려면 필요한 port, State endpoint, State Workload, 영속
Volume 관계를 최소 샘플 앱으로 확인한다.

샘플은 두 Workload 역할로만 구성한다.

- App: 외부 요청을 받고 State로 전달한다.
- State: 값을 파일에 기록하고 다시 조회한다.

PostgreSQL, SQLite, 수강신청 같은 제품·도메인 이름은 실험 판정에 사용하지 않는다.

## 실험 설계

각 관계는 `정상 상태 → 한 관계 개입 → 동일 관계 복원` 순서로 관측한다. 단순히 정상 실행만
확인하지 않으므로, 다른 설정이 우연히 성공 원인이 되는 것을 줄일 수 있다.

| 관계 | 정상 상태 | 개입 | 복원 후 합격 신호 |
|---|---|---|---|
| App–계약 port | 계약 port에서 readiness 200 | App을 다른 port에서 실행 | 계약 port readiness 200 |
| App–State endpoint | 올바른 URL에서 readiness 200 | 연결을 즉시 거부하는 잘못된 State URL 주입 | 올바른 URL에서 readiness 200 |
| App–State Workload | readiness와 업무 요청 성공 | State가 계약 port를 듣지 않게 함 | 계약 port 복원 후 readiness 200 |
| State data path–Volume | 값을 기록하고 조회 | 다른 빈 data path로 재기동 | 원래 path로 돌아오면 기존 값 조회 |

## 실행 층위

1. `process`: 동일한 샘플을 별도 OS 프로세스로 실행해 관계 자체를 확인한다.
2. `docker`: 같은 샘플을 Container, Docker network, named Volume으로 실행해 container 경계를
   확인한다.
3. `cloud`: CSP별 네트워크·LB·disk 관계는 별도의 provider 개입 실험으로 확인한다.

앞 단계 성공은 뒤 단계 성공을 대신하지 않는다. process 결과는 앱 로직과 관계 가설을 확인할
뿐 Docker mount나 CSP 방화벽을 증명하지 않는다.

## 판정과 정리

- 업무 기능은 HTTP 상태뿐 아니라 기록 값도 함께 확인한다.
- 개입 후 실패와 복원 후 재성공을 모두 관측해야 관계를 확인한 것으로 본다.
- 실행마다 고유 run ID를 사용한다.
- Docker 객체와 cloud 리소스는 run ID label 또는 전용 resource group으로 소유 범위를 한정한다.
- 정리가 실패하거나 소유 리소스가 남으면 실험 성공으로 판정하지 않는다.
