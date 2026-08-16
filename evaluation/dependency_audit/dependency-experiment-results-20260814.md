# 의존성 표적 실험 결과

## 결론

2026년 8월 14일 개발 관찰에서 앱–리소스 관계 네 종류는 별도 프로세스와 Docker 경계에서
각각 `10/10` 단계가 통과했다. AWS 두 관계, Azure Load Balancer backend 관계, GCP 두
관계도 정상→개입 실패→복원 성공을 관측했다. 디스크는 실행 중 분리하지 않고, 실제
PostgreSQL을 Azure VM에 배포해 추가 data disk 0개인 구성과 VM 교체 후 같은 data disk를
다시 연결하는 구성을 비교했다.

추가로 AWS·Azure·GCP에서 Probe VM과 State VM을 분리하고, Probe VM이 State VM의 사설
IPv4 주소로 PostgreSQL에 접속하는 경로를 같은 방식으로 확인했다. 세 CSP 모두 SQL
기록·조회, 5432 허용 관계 제거 후 접속 실패, 관계 복원 후 기존 값 재조회가 통과했다.
같은 세 CSP에서 도메인 중립 App까지 연결한 E1 경로도 확인했다. App의 업무 요청은 정상 상태에서
성공했고, 5432 관계를 제거하면 readiness와 업무 요청이 실패했으며, 복원과 같은 State VM의
재부팅·reset 뒤에는 기존 값이 다시 조회됐다.

이번 결과는 관계별 1회 개발 관찰이다. CSP나 앱 모집단 전체의 성공률로 일반화하지 않는다.
활성 제품 KB에서는 실행 중 disk detach claim을 제거했다.

## 앱–리소스 샘플 실험

샘플은 외부 요청을 받는 App과 파일 상태를 소유하는 State의 두 일반 Workload로 구성했다.
특정 DB 제품, 수강신청 용어, 고정 mount path는 판정에 사용하지 않았다.

| 실행 경계 | 결과 | 확인한 관계 | 정리 |
|---|---:|---|---|
| 별도 OS 프로세스 | 10/10 | App port, State endpoint, State 가용성, 영속 data path | 잔여 프로세스 0 |
| Docker | 10/10 | published port, Docker DNS endpoint, State service port, named Volume | label 객체 0 |

Docker 실험은 control에서 값을 기록하고, Volume 없는 State Container로 바꾸면 값이 사라지며,
원래 named Volume을 다시 연결하면 같은 값을 읽을 수 있음을 확인했다. 이는 Volume 선언의 존재만
본 것이 아니라 앱의 기록·조회 기능으로 영속성 관계를 관측한 것이다.

근거:

- `app-resource-process-result-20260814.json`
- `app-resource-docker-result-20260814.json`
- `app-resource-experiment-plan.md`

## CSP 기능 관계

| CSP | 관계와 기능 신호 | 결과 | 해석 |
|---|---|---|---|
| AWS | VM–instance profile–IMDS credential | 확인 | profile 분리 후 상실, 재연결 후 회복 |
| AWS | Subnet default route–외부 HTTPS | 확인 | route 제거 후 상실, 복원 후 회복 |
| Azure | Load Balancer backend pool–HTTP | 조건부 확인 | 자동 NIC NSG를 보정해 기준선을 세운 뒤 backend 제거·복원 성공 |
| GCP | VPC firewall rule–inbound TCP | 확인 | rule 제거 후 상실, 복원 후 회복 |
| GCP | VPC default route–inbound TCP | 확인 | route 제거 후 상실, 복원 후 회복 |

## CSP별 사설 VM 간 PostgreSQL 관계

세 실험은 같은 기능 oracle을 사용했다. State VM에는 `postgres:17-bookworm`을 실행하고,
Probe VM에서 State VM의 사설 IPv4 주소와 TCP 5432로 테이블 생성·기록·조회를 수행했다.
그 다음 Probe의 security group·subnet 범위·source tag를 출발점으로 허용한 관계를 제거해
접속 실패를 확인하고, 같은 관계를 복원해 앞서 기록한 값을 다시 조회했다.

| CSP | 접근 제한 표현 | 정상/개입/복원 | 전체 시간 | 정리 |
|---|---|---|---:|---|
| AWS | Probe security group → State security group의 TCP 5432 | 통과/통과/통과 | 192.943초 | 소유 리소스 잔여 0 |
| Azure | Probe subnet 주소 범위 → State NSG의 TCP 5432 | 통과/통과/통과 | 623.326초 | 소유 resource group 삭제, 잔여 0 |
| GCP | Probe source tag → State target tag의 TCP 5432 | 통과/통과/통과 | 301.206초 | 소유 리소스 잔여 0 |

이 결과는 다음 두 관계를 구분한다.

- VM이나 PostgreSQL 리소스 생성 자체에는 상대 VM과 5432 경로가 필수 선행조건이 아니다.
- 분리된 App/Probe Workload가 State Workload의 PostgreSQL 기능을 사용하려면 실제 endpoint
  주입과 그 endpoint까지의 허용된 네트워크 경로가 런타임 기능 의존성이다.

AWS와 GCP VM에는 image·package bootstrap을 위한 공인 주소가 있었지만, 판정 대상 SQL은
사설 주소만 사용했다. 따라서 이 실험은 사설 주소를 통한 기능 경로를 확인한 것이지, State가
공개 인터넷에서 절대 접근 불가능함을 확인한 실험은 아니다. Azure는 두 VM 모두 공인 IP 없이
Run Command로 관리했다. 세 실험 모두 한 Region·한 Zone의 1회 개발 관찰이며, 내부 DNS,
서로 다른 Subnet·Zone, 장애 복구, 성능 또는 고가용성을 증명하지 않는다.

원시 체크포인트 로그는 다음 파일이다.

- `inter-vm-postgres-aws-result-20260814.json`
- `inter-vm-postgres-azure-result-20260814.json`
- `inter-vm-postgres-gcp-result-20260814.json`

## 3사 도메인 중립 테스트 앱 E1 경로

직접 `psql`을 호출한 VM 간 실험과 별도로, 같은 도메인 중립 샘플 앱과 PostgreSQL 저장 adapter로
AWS·Azure·GCP의 다음 실행 경로를 확인했다.

```text
App VM의 로컬 또는 제한된 외부 HTTP 요청
→ 테스트 App Container
→ State VM의 사설 IPv4:5432
→ PostgreSQL Container
→ ext4로 포맷·mount한 CSP data Volume의 PGDATA 하위 디렉터리
```

3사의 선택된 확인 실행은 다음과 같다.

| CSP | 5432 허용 표현 | 기준선/차단/복원/같은 VM 재시작 후 보존 | 전체 시간 | 정리 |
|---|---|---|---:|---|
| AWS | App Security Group → State Security Group | 통과/통과/통과/통과 | 247.148초 | 소유 리소스 잔여 0 |
| Azure | App 사설 IPv4 `/32` → State NSG | 통과/통과/통과/통과 | 815.101초 | 소유 Resource Group 삭제, 잔여 0 |
| GCP | App source tag → State target tag | 통과/통과/통과/통과 | 435.416초 | 소유 리소스 잔여 0 |

공통 기능 신호는 기준선의 readiness 200과 값 기록·조회, 차단 상태의 readiness 503과 업무
조회 502, 복원 및 재시작 뒤 기존 값 `kept` 조회다. AWS는 runner `/32`로 제한한 외부 HTTP,
Azure는 Run Command를 통한 App VM localhost, GCP는 serial marker를 통한 App VM localhost를
사용했다. 따라서 외부 진입 방식의 성능이나 우열은 비교하지 않는다.

AWS 첫 실행은 EBS 장치 탐색·포맷·PostgreSQL 시작을 하나의 120초 guest 명령으로 묶어 하네스
시간초과가 났다. 두 번째 실행은 같은 checkpoint에서 원인을 확인했다. ext4 mount root의
`lost+found` 때문에 PostgreSQL이 PGDATA를 빈 디렉터리로 인정하지 않았으며, mount 아래
`data/`를 PGDATA로 사용하도록 수동 교정한 뒤 나머지 기능이 통과했다. 최종 세 번째 실행은
attachment 완료, guest 장치 식별, 포맷·mount, PostgreSQL 시작을 분리하고 위 규칙을 코드에
반영한 상태에서 수동 개입 없이 통과했다. 세 시도의 원시 파일과 분류·해시는
`aws-sample-app-postgres-e1-adjudication-20260814.json`에 보존한다. Azure 첫 실행은 앱 소스를
인라인 Run Command 인자로 보내다 Windows 명령행 길이 제한에 걸렸다. 긴 스크립트만 임시 파일
참조로 전송하도록 일반화한 뒤 두 번째 실행이 통과했다. 실패 실행도 성공 실행과 분리해 보존했다.

3사에서 선택한 실행과 원시 파일 해시, 제외한 하네스 실패는
`multi-provider-sample-app-postgres-e1-adjudication-20260814.json`에 기록했다.

이 결과는 수강신청 기능을 검증한 것이 아니다. 테스트 앱의 readiness와 일반 값 기록·조회로
App→State endpoint·traffic filter·PGDATA binding이라는 리소스 의존성을 확인한 것이다.
DNS·신뢰 가능한 HTTPS·Certificate·Load Balancer는 미측정이다. 같은 State VM의 재부팅 또는
reset만 수행했으므로 VM 교체, 상태 계층 HA 또는 성능도 증명하지 않는다. 이 결과는 E1의
리소스 경로에 대한 개발 관찰이며 수강신청 업무 규칙·동시성 검증을 대신하지 않는다.

## PostgreSQL과 추가 data disk

Azure Korea Central의 `Standard_B2ats_v2`, `Ubuntu2204`, `postgres:17-bookworm`으로 다음을
관찰했다. 두 구성 모두 VM의 OS disk는 존재하며, 비교 대상은 별도 Managed Disk뿐이다.

| 구성 | 결과 | 판정 |
|---|---|---|
| 추가 data disk 0개 | PostgreSQL 테이블 생성·INSERT·SELECT 성공 | 추가 data disk는 기동·기본 SQL 기능의 필수조건이 아님 |
| 4GiB Standard LRS data disk | VM A에서 기록, VM A 삭제, 같은 disk를 VM B에 연결한 뒤 값 조회 성공 | Azure에서 VM과 분리된 보존 저장공간의 한 실현으로 확인 |

첫 구성의 PostgreSQL 공식 이미지는 Docker의 익명 volume을 사용했고 그 실제 저장 위치는 VM의
OS disk였다. 같은 container 재시작 뒤 조회까지만 확인했으므로 container 재생성 뒤 보존을
주장하지 않는다. 두 번째 구성은 Managed Disk를 mount하고 PostgreSQL data path에 명시적으로
연결했다.

실행 중 detach는 하지 않았다. raw 결과와 합동 판정은 다음 파일에 있다.

- `azure-postgres-disk-result-20260814.json`: boot-disk-only control. Azure Run Command의
  stdout 부재를 바깥 문자열 판정이 실패로 오인했지만, data disk 개수 0과 guest SQL 명령
  성공은 보존돼 있다.
- `azure-postgres-disk-replacement-result-20260814.json`: data disk를 VM 교체 뒤 재연결한
  확인 실행이며 결과는 `passed`다.
- `azure-postgres-storage-adjudication-20260814.json`: 위 두 raw 실행의 판정과 해시다.

원시 근거는 `app/core/cloudkb/depkb/replications/2026-08-14/`에 있다. 각 cloud 실행 뒤
래퍼 결과와 독립 조회에서 이번 실험 소유 리소스 잔여가 0임을 확인했다.

## AWS 도메인 중립 테스트 앱 E2 App 계층 장애 대응

E1과 같은 App·PostgreSQL·업무 값 오라클을 유지하고 App 실행부만 다음과 같이 바꿨다.

```text
공개 HTTP ALB
→ `/health/ready`를 검사하는 Target Group
→ 서로 다른 두 AZ의 Auto Scaling Group App VM 2대
→ 단일 State VM의 사설 PostgreSQL과 EBS PGDATA
```

확인 실행 `easydep-e2-b53649d9`에서 target 두 개가 healthy인 기준선을 만든 뒤, 한 App VM의
Container를 정지했다. ALB가 해당 target을 unhealthy로 판정했고 ASG는 새 VM을 만들어 healthy
target 두 개로 복구했다. 결과는 다음과 같다.

| 관측 | 결과 |
|---|---:|
| App Container 정지 | 시작 후 11.216초 |
| victim target unhealthy | 37.972초 |
| replacement target healthy | 245.027초 |
| 교체 구간 업무 probe | 208회 중 201회 성공, 7회 실패 |
| 최대 연속 기능 실패시간 | 2.087초 |
| 교체 진행 중 성공 요청 | 174회 |
| 최종 업무 값 | 기존 값 `kept` 조회 |
| 정리 | 소유 Instance·Volume·Security Group·ASG 잔여 0 |

합격 기준은 사후 성공률 숫자에 맞춘 것이 아니다. Target Group의 10초 간격과 2회 실패 임계값에
라우팅 전파 여유 10초를 더해 기능 복구 예산을 30초로 먼저 계산했다. 실제 강제 장애에서는
health 탐지 전 짧은 오류가 발생했으므로 `무중단`이나 SLA라고 표현하지 않는다. State VM은 한
대이므로 이 결과는 App 계층 장애 대응만 확인한다.

첫 시도는 Launch Template user-data에 shebang이 없어 cloud-init이 앱을 실행하지 않은 하네스
실패였다. 두 번째는 관리형 교체가 완료됐지만 오류 0건이라는 과도한 evaluator 정책으로 실패
처리됐다. 세 번째는 설정 기반 복구시간과 교체 중 성공 요청을 분리 기록해 무인 통과했다. 세
시도와 독립 잔여 조회는 `aws-sample-app-postgres-e2-adjudication-20260815.json`에 보존한다.

## GCP 도메인 중립 테스트 앱 E2 App 계층 관리형 복구

AWS와 같은 App·PostgreSQL·업무 값 오라클을 다음 GCP 리소스 경로에 투영했다.

```text
공개 HTTP Forwarding Rule
→ Target HTTP Proxy → URL Map → Backend Service와 `/health/ready` Health Check
→ Zonal Managed Instance Group App VM 2대
→ 단일 State VM의 사설 PostgreSQL과 Persistent Disk
```

확인 실행 `easydep-e2-fe1bb9ac`에서 App VM 두 대가 모두 healthy인 기준선을 만든 뒤, 임의
토큰이 있어야 활성화되는 시험 전용 endpoint로 한 App 프로세스를 종료했다. Backend Service가
해당 VM을 unhealthy로 판정했고 MIG는 새 VM 교체가 아니라 같은 VM의 관리형 재기동으로
복구했다. 따라서 관리형 복구를 특정 교체 형태로 고정하지 않고 실제 동작을 구분해 기록한다.

| 관측 | 결과 |
|---|---:|
| 장애 요청 수락 | 시작 후 0.107초 |
| victim backend unhealthy | 101.647초 |
| 관리형 재기동 뒤 healthy 복구 | 217.956초 |
| 복구 중 업무 probe | 167회 중 167회 성공 |
| 최대 연속 기능 실패시간 | 0초 |
| 관리형 복구 중 성공 요청 | 91회 |
| 최종 업무 값 | 기존 값 `kept` 조회 |
| 정리 | 모든 `easydep-e2-` GCP 리소스 독립 조회 잔여 0 |

총 벽시계 시간은 1,111.783초였고 기록된 단계 합은 801.637초였다. 큰 구간은 State 준비
101.858초, Backend Service에 MIG 연결 94.476초, 공개 LB readiness 116.994초, autohealing
설정 반영 87.753초, 관리형 복구 217.956초였다. 나머지 310.146초에는 리소스 삭제와 잔여
조회처럼 단계 밖 정리가 포함된다. 따라서 이 실행의 병목은 App 업무 처리보다 cloud
control-plane 전파·관리형 복구·정리였다.

unhealthy 판정까지 걸린 시간과 사용자 기능 실패시간은 다른 지표다. 이 실행에서는 나머지
healthy Backend가 요청을 처리해 기능 실패가 관측되지 않았지만, 한 번의 개발 실행을 근거로
무중단이나 SLA를 주장하지 않는다. MIG는 한 Zone에 있으며 State VM도 한 대이므로 Zone·Region
장애와 State 계층 고가용성은 검증하지 않았다.

앞선 다섯 시도는 subnet region 누락, 제거된 gcloud 명령, 불안정한 per-instance metadata 장애
주입, 새 VM ID만 인정한 판정, 단일 직렬 로그 마커 의존을 각각 드러냈다. 대상 시스템 실패와
섞지 않고 모두 보존했으며, 최종 확인 실행과 독립 잔여 조회는
`gcp-sample-app-postgres-e2-adjudication-20260815.json`에 정리했다.

## Azure 도메인 중립 테스트 앱 E2 App 계층 관리형 교체

같은 오라클을 Azure Standard Load Balancer와 VM Scale Set에 투영했다. 이 실행은 VMSS
automatic repair의 Azure Load Balancer health source를 격리하기 위한 것으로, 최종 HTTPS
토폴로지의 Application Gateway나 TLS를 검증한 것이 아니다.

```text
공개 Standard Load Balancer와 HTTP Health Probe
→ automatic repair `Replace`, App VMSS 2대
→ 단일 State VM의 사설 PostgreSQL과 Managed Disk
→ Subnet NAT Gateway를 통한 bootstrap outbound
```

확인 실행 `easydep-e2-bdfacbd3`에서 한 App 프로세스를 종료하자 해당 VM은
`HealthState/unhealthy`, 다른 VM은 `HealthState/healthy`가 됐다. Azure CLI가 허용하는 최소
automatic repair grace 30분이 지난 뒤 unhealthy VM이 제거되고 다른 VM ID의 새 instance가
생성됐다.

| 관측 | 결과 |
|---|---:|
| 장애 요청 수락 | 시작 후 0.043초 |
| 새 VM 교체 관측 | 1,982.756초 |
| 새 VM healthy 확인 | 1,982.860초 |
| 관리형 교체 중 업무 probe | 1,747회 중 1,747회 성공 |
| 최대 연속 기능 실패시간 | 0초 |
| 최종 업무 값 | 기존 값 `kept` 조회 |
| 자동 복구 정책 | `enabled=true`, `gracePeriod=PT30M`, `repairAction=Replace` |

총 벽시계 시간은 2,679.125초였고 기록된 단계 합은 2,417.959초였다. 그중 관리형 교체 대기가
1,982.860초로 전체 병목의 대부분을 차지했다. 나머지 261.166초에는 Resource Group 삭제와
존재·태그 잔여 조회가 포함된다. 따라서 Azure E2 반복 수는 LLM이나 앱 실행시간이 아니라
provider가 강제하는 최소 repair grace와 control-plane 정리시간을 기준으로 정해야 한다.

첫 시도에서는 Standard Load Balancer를 추가한 Subnet의 State VM에 명시적 outbound가 없어
Docker bootstrap이 실행되지 않았다. Subnet NAT Gateway를 추가했다. 두 번째 시도는 legacy
Run Command가 빈 stdout·stderr와 `Enable succeeded`만 반환하는데도 guest script 결과를
확인할 수 없는 문제를 드러냈다. 세 번째 시도에서는 Managed Run Command의 `instanceView`가
중첩된 반환 구조임을 반영하지 못했다. 네 번째 시도는 `executionState=Succeeded`, `exitCode=0`,
고유 성공 마커를 함께 검사한 뒤 무인 통과했다. 이 결과도 단일 State VM 때문에 App 계층
장애 대응일 뿐 종단 HA가 아니다.

## 실행 중 발견한 하네스 문제

- AWS PEM은 Windows에서 `chmod(0600)`만으로 ACL이 제한되지 않아 OpenSSH가 거부했다. 실험용
  PEM 한 파일의 상속 ACL을 제거하고 같은 VM 체크포인트에서 재개했다.
- Azure `az vm create`가 NIC NSG를 추가 생성해 Subnet NSG의 80 허용만으로는 LB 기준선이
  서지 않았다. 자동 NSG에도 실험 소유 80 규칙을 추가하도록 고쳤다.
- Azure PostgreSQL 첫 시도는 `Standard_B1s` capacity restriction으로 실행 전에 검열됐다.
  학생 구독에서 기존 실험으로 확인된 `Standard_B2ats_v2`를 사용했다.
  이는 계정·리전의 실행환경 제약이며 PostgreSQL의 VM 사양 의존성으로 해석하지 않는다.
- Azure CLI의 `--query length(...)`가 Windows `az.cmd`에서 깨지고, 불필요한 VM
  `deleteOption` 갱신이 구독에서 거부된 하네스 문제를 제거했다.
- GCP route 검색은 API URL host 차이 때문에 두 차례 하네스 실패가 있었다. network selfLink의
  리소스 경로를 비교하도록 고친 뒤 최종 실행이 통과했다.
- 기존 날짜별 증거 파일명이 같은 날 재실행을 덮어썼다. 이후 실행은 시각 suffix를 붙여 모든
  시도를 보존하도록 고쳤다.
- 첫 GCP VM 간 실험은 두 VM과 PostgreSQL이 준비됐지만 Windows `gcloud`의 SSH 관리 세션이
  시간초과되어 기능 판정 전에 하네스 실패로 종료됐다. 소유 리소스 잔여 0을 확인한 뒤,
  Probe VM이 결과를 직렬 콘솔에 기록하고 metadata로 개입 단계를 전달받도록 일반 관리 경계를
  바꿨다. 재실행에서는 SSH inbound 규칙 없이 기능 경로와 정리가 모두 통과했다.
- 위 SSH 원인 분리를 위해 실행한 `gcloud --troubleshoot`가 프로젝트의 Network Management
  API를 활성화했다. 공유 프로젝트의 다른 사용 여부를 알 수 없어 실험 종료 시 임의로
  비활성화하지 않았으며, VM·network·firewall 잔여 판정과는 별도로 기록한다.

## 현재 판정과 다음 경계

이번 실험으로 확인된 관계는 제품에 자동 반영하지 않는다. 다음 단계에서 근거 레코드와 제품
스키마를 연결할 때 다음 규칙을 적용한다.

1. 확인된 관계도 조건과 기능 신호를 함께 저장한다.
2. `VM 교체 뒤 데이터 보존` 요구는 교체 VM과 분리되어 보존되는 저장공간을 요구한다. Azure
   실험에서는 Managed Disk로 실현했으며, 이를 3사의 유일한 구현으로 일반화하지 않는다.
3. process 성공을 Docker 성공으로, Docker 성공을 cloud 성공으로 대체하지 않는다.
4. Azure LB의 결과는 Application Gateway나 모든 Azure LB 유형으로 일반화하지 않는다.
5. 실제 제품 적용 전에는 관계별 source evidence와 validation gate를 명시적으로 연결한다.
