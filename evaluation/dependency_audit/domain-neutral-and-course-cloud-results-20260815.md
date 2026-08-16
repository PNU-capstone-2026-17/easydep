# 도메인 중립 의존성 및 수강신청 E1 클라우드 실험 결과

## 1. 이번 실행의 목적

이번 작업은 새 요구사항이나 앱을 다시 생성하지 않았다. 먼저 Docker-on-VM 범위에서
도메인 중립 앱으로 아직 직접 확인하지 못했던 TLS 경로를 보완하고, 이미 생성·수정·로컬
검증을 마친 수강신청 E1 앱을 AWS·Azure·GCP에서 같은 업무 오라클로 확인했다.

이 결과가 증명하는 것은 특정 리소스 연결과 앱 기능의 개발 단계 관찰이다. 모든 앱이나
리전에서의 성공률, 고가용성 SLA, 성능·비용 최적성을 증명하지 않는다.

## 2. 도메인 중립 앱으로 확인한 범위

| 경로 | AWS | Azure | GCP | 판정 |
|---|---|---|---|---|
| App VM → State VM 사설 주소 → PostgreSQL | E1 통과 | E1 통과 | E1 통과 | 사설 endpoint와 traffic filter가 읽기·쓰기에 필요함을 개입·복원으로 관찰 |
| PostgreSQL PGDATA → 별도 data disk | E1 통과 | E1 통과 | E1 통과 | 디스크 attach만이 아니라 파일시스템·mount·PGDATA 연결까지 필요 |
| State VM 재기동 뒤 데이터 보존 | E1 통과 | E1 통과 | E1 통과 | 동일 VM 재기동·reset 범위에서 관찰 |
| State VM 교체 → 기존 disk 재연결 → 새 endpoint 주입 | E3 통과 | E3 통과 | E3 통과 | App image 재빌드 없이 기존 값 조회 |
| 관리형 App 그룹의 장애 감지·복구 | E2 통과 | E2 통과 | E2 통과 | AWS/GCP는 선택 진입 경로와 일치, Azure는 Standard LB 관찰이라 Application Gateway 연속성은 별도 |
| 직접 HTTPS 종단 | 로컬 중립 실험 | 로컬 중립 실험 | 로컬 중립 실험 | TLS terminator 제거 시 실패하고 복원 시 업무 값 회복 |
| 관리형 HTTPS → backend binding | ALB 1회 | Application Gateway 1회 | External Application LB 3회 | HTTPS readiness·업무 요청 통과, backend 제거 시 실패, 복원 시 회복 |

주요 원시 증거는 다음과 같다.

- 직접 TLS: `domain-neutral-direct-tls-result-20260815.json`
- AWS 관리형 TLS: `aws-sample-app-managed-tls-result-20260815.json`
- Azure 관리형 TLS: `azure-sample-app-managed-tls-result-20260815.json`
- GCP 관리형 TLS: `../research_protocol/intervention-results/intervention.gcp.backend-service-backend-group.necessity.json`
- E1~E3 종합 판정: 같은 디렉터리의 provider별 결과와
  `multi-provider-sample-app-postgres-e3-adjudication-20260815.json`

직접 TLS 실험의 인증서는 하루짜리 self-signed 인증서다. 따라서 TLS listener와 업무 경로는
관찰했지만 DNS 소유권, 공개 CA 신뢰, 인증서 자동 갱신은 측정하지 않았다. 관리형 TLS 실험도
같은 한계를 가진다.

## 3. 수강신청 E1의 세 CSP 결과

세 CSP에서 확인한 최종 구조는 App VM 1대, State VM 1대, State용 별도 data disk 1개,
App의 공개 시험용 HTTPS endpoint다. App은 State VM의 사설 IPv4와 5432만 사용했다.
관리형 LB, 성능 SLO, App 자동 복구는 E1의 대상이 아니다.

| CSP | 최종 상태 | 업무·동시성 | DB 중단 health | 재기동 후 영속성 | 소유 잔여 |
|---|---|---:|---:|---:|---:|
| AWS | harness 보정 후 통과 | 13/13 | 503/DOWN | 2/2 | 0 |
| Azure | 통과 | 13/13 | 503/DOWN | 2/2 | 0 |
| GCP | 통과 | 13/13 | 503/DOWN | 2/2 | 0 |

업무 오라클은 강좌 조회, 신청·취소, 중복 거부, 마지막 한 좌석에 대한 동시 요청 5건 중
정확히 1건 성공, 동일 학생의 동시 중복 요청 5건 중 정확히 1건 성공을 검사한다. 영속성
오라클은 State VM 재기동 뒤 `S1/C-DUP` 신청과 잔여좌석 9를 다시 읽는다.

AWS 결과는 기존
`artifacts/measurements/course-registration-aws-cloud-experiment-20260815.json`을 재사용했다.
Azure와 GCP는 같은 생성 앱 checkpoint를 새로 build한 동일 image index digest
`sha256:ff0b634b5cbcf9a78099f1f4687acd2eb6cbae8ca12a900770abadcfd7a16adb`를 사용했다.
AWS의 이전 build digest는 다르므로 세 결과를 bit-identical image 비교라고 부르지 않는다.
다만 세 실행은 같은 앱 소스 계보와 같은 세 업무 오라클을 사용했다.

Azure와 GCP 원시 결과는 다음 파일에 보존했다.

- `artifacts/measurements/course-registration-azure-cloud-experiment-20260815.json`
- `artifacts/measurements/course-registration-gcp-cloud-experiment-20260815.json`
- 각 CSP의 `course-registration-business-*`, `database-unavailable-*`,
  `persistence-*` 오라클 결과

## 4. 실패에서 확인한 일반 문제

Azure 첫 두 attempt에서는 두 일반 문제가 드러났다.

1. Azure Run Command는 guest shell이 0이 아닌 값으로 끝나도 제어면 응답을
   `ProvisioningState/succeeded`로 돌려줄 수 있었다. 기존 harness는 이를 성공으로 잘못
   기록했다. 스크립트를 Base64로 전달하고 guest 임시 파일에서 실행한 뒤
   `EASYDEP_EXIT_CODE`를 반환하도록 공통 경계를 수정했다.
2. 공인 주소나 NAT가 없는 State VM은 당시 Azure Ubuntu 저장소와 container registry에
   나갈 수 없어 bootstrap에 실패했다. 최종 실험에서는 State VM에도 outbound용 공인 주소를
   두되, NSG는 PostgreSQL 5432를 App 사설 주소에서만 허용했다. 앱의 DB 연결도 사설 주소를
   사용했다.

첫 실패는 health 단계까지 기다린 뒤 원인을 확인했지만, 두 번째 attempt부터는 guest 종료코드
100을 63초 만에 해당 하위작업 실패로 기록했다. 두 실패 결과도 각각 별도 attempt JSON으로
보존했다. 이는 앱이나 요구사항을 다시 만드는 대신 실패한 배포 하위작업에서 진단·수정하는
체크포인트 원칙과 일치한다.

## 5. 정리 결과와 해석 한계

- Azure의 세 attempt는 각각 자기 resource group 삭제 완료와 `exists=false`를 확인했다.
- GCP는 이번 prefix의 VM, disk, firewall, subnet, network가 모두 0임을 확인했다.
- AWS의 실행 소유 active instance가 0임을 다시 확인했다.
- 캠페인 전용 Public ECR repository와 로컬 image tag를 삭제했다.
- 로컬 실험 컨테이너 잔여도 0이다.

이번 세 CSP E1은 시스템이 생성한 IaC 자체의 완전한 종단 성공률 실험이 아니다. AWS는 이전
IaC runtime bootstrap을 harness에서 보정했고, Azure·GCP는 이번 의존성 검증 harness가
리소스를 직접 구성했다. 따라서 이 결과는 다음 주장에만 사용한다.

> 같은 생성 앱과 같은 외부 업무 오라클이 세 CSP의 제한된 Docker-on-VM E1 구조에서
> App–State 사설 연결, DB 중단 상태 노출, 별도 disk를 통한 재기동 영속성을 만족했다.

DepKB 효과, 자동 IaC 생성 성공률, 관리형 고가용성, 처리량·지연시간은 각각 별도 실험으로
평가해야 한다.
