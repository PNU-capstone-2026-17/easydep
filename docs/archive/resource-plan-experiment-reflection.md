# 의존성 실험의 배포 계획 반영

> 기준일: 2026-08-15
> 범위: AWS·Azure·GCP의 단일 Region Docker-on-VM 배포
> 2026-08-17 이후 생성 범위는 HTTP 전용이다. 이 문서의 HTTPS/TLS 절은 과거 실험 기록이며
> 현재 ResourcePlan·IaC·다이어그램에 인증서나 TLS 종료를 추가하는 근거가 아니다.

## 목적

도메인 중립 앱 실험 E1~E3에서 관찰한 관계를 보고서에만 두지 않고, 배포 구조도와 IaC가
함께 소비하는 하나의 `ResourcePlan`에 반영한다. 이 계획은 범용 클라우드 표준이 아니라
EasyDep 내부 중간 표현이다.

```text
요구사항·설계 산출물 + 선택 CSP + DepKB
                 ↓
             ResourcePlan
              ├─ 배포 구조도
              └─ IaC 생성 → 정적 결합 검사
```

PlantUML은 사람이 보는 출력이며 IaC 입력이 아니다. IaC 에이전트도 같은 `ResourcePlan`을
받고 이를 새로 설계하거나 Workload를 임의로 합치지 않는다.

## 실험에서 반영한 결정

| 실험 | 관찰한 범위 | 계획에 반영한 내용 | 과장하지 않는 범위 |
|---|---|---|---|
| E1 | App–사설 State 연결, traffic filter, State의 별도 disk와 guest mount | 명시된 독립 Workload별 Compute 배치, `connectsTo`, 영속성 소유 Workload의 disk attachment | 업무 도메인 규칙·HTTPS·성능은 증명하지 않음 |
| E2 | 관리형 App 그룹에서 비정상 App 인스턴스 복구 | 명시적 App 계층 가용성 요구가 있을 때만 관리형 compute group과 LB를 선택하고 App replica를 2로 둠 | 단일 State Workload가 남으면 종단 HA가 아님 |
| E3 | State VM 교체 후 기존 disk 재연결, App의 runtime endpoint 갱신 | `targetEndpoint=runtimeDerived`, 교체 시 설정 갱신, App image 재빌드 불필요를 runtime binding에 기록 | 자동 장애조치·RTO·RPO·State 복제를 증명하지 않음 |

E1~E3는 CSP별 1회 개발 관찰이다. 반복 성공률이나 모든 합법 토폴로지의 완전성 근거로
사용하지 않는다. 원시 결과와 실패 판정은
[`evaluation/dependency_audit/`](../../evaluation/dependency_audit/)에 보존한다.

### 기존 체크포인트 재생 결과

2026-08-15에 기존 수강신청 요구사항 분류와 설계 체크포인트를 보존하고, 배포 단서 추출과
cloud enrichment만 다시 실행했다. 5개 구조화 LLM 샘플에는 69.92초가 걸렸다. “재시작 후
유실 방지”는 `persistent` 의도로 정규화했지만 VM 교체 독립 block storage 요구로 승격하지
않았다. 별도 disk는 자체운영 영속 Workload에 대한 프로젝트 정책과 E3 근거로 기록했다.

같은 설계에서 AWS·Azure·GCP 모두 Workload 2개, Compute 배치 2개, 영속성 소유자 1개를
만들었고 끊어진 edge와 미해결 결정은 0개였다. 세 배포 구조도는 각 계획 노드를 모두
포함했다. 이 재생은 IaC 생성이나 실제 배포 성공을 측정한 것이 아니다. 비민감 요약과 입력
hash는
[`resource-plan-checkpoint-replay-20260815.json`](../../artifacts/measurements/resource-plan-checkpoint-replay-20260815.json)에
기록했다.

## Workload 도출 경계

클래스 수나 `App`, `DB`, 수강신청 같은 이름으로 Workload를 만들지 않는다. 설계 산출물에
실행 환경 또는 데이터 저장 실행 단위가 명시된 경우만 후보로 사용한다. 상위 실행 환경 안에
중첩된 runtime은 별도 배포 artifact를 소유하지 않으면 같은 Workload로 본다.

영속성 요구가 있을 때 소유자는 다음 순서로만 확정한다.

1. 설계가 독립 데이터 저장 실행 단위 하나를 명시한 경우 그 Workload
2. 배포 Workload가 하나뿐인 경우 그 Workload
3. 후보가 여러 개인데 소유자가 불명확하면 `unresolved`

따라서 단순히 Entity 클래스나 ERD가 있다는 이유로 별도 VM·disk를 만들지 않는다.

## 현재 기계 검증

IaC 생성 뒤 다음을 구분해 기록한다.

- `passed`: HCL에서 계획이 요구한 CSP 리소스 유형과 개수를 관찰함
- `failed`: 필요한 VM·network·subnet·traffic filter·LB·disk 등이 없거나, 별도 disk를
  만들었지만 Compute 연결을 선언하지 않음
- `notObserved`: 동적 `count`·`for_each`, guest mount, container 실행, runtime endpoint처럼
  Terraform 원문만으로 확정할 수 없음

고정 provider 검증 환경에서는 `validate` 뒤 `plan -refresh=false`와 `show -json`을 시도한다.
Plan 원문과 값은 보존하지 않고 SHA-256, 리소스 유형별 수, address만 남긴다. 입력 변수나
인증 부족으로 Plan을 만들 수 없으면 `notObserved`이며 성공으로 바꾸지 않는다. mount·port·
사설 연결·업무 기능은 Docker 또는 실제 cloud gate가 담당한다.

## 3사 IaC 정적 종단 실험 결과

같은 도메인 중립 입력을 AWS·Azure·GCP에 각각 투영해 실제 IaC 생성 에이전트를 호출했다.
최종 세 셀 모두 HCL 검사, 고정 provider `validate`, `plan -refresh=false`, Plan JSON의
`ResourcePlan` 대조를 통과했다. 생성한 Plan의 원문과 임시 변수 값은 저장하지 않았다.

| CSP | 최종 실행 | LLM 생성·수정 | Plan 대조 | 핵심 관찰 |
|---|---|---:|---|---|
| AWS | `resource-plan-iac-aws-20260814T214245Z` | 생성 1회, 41.07초 | 통과 | VM 2, EBS 1, attachment 1, VPC·subnet·SG·public IP 관찰 |
| Azure | `resource-plan-iac-azure-20260814T222326Z` | 생성 1회, 33.81초 | 통과 | VM 2, managed disk 1, attachment 1, VNet·subnet·NSG·public IP 관찰 |
| GCP | `resource-plan-iac-gcp-20260814T221850Z` | 생성 36.01초, 수정 18.99초 | 통과 | VM 2, persistent disk 1, VPC·subnet·firewall·public IP 관찰 |

AWS와 GCP의 provider/project 입력 보완, Azure의 SSH 공개키 형식 보완은 이미 생성된 IaC를
버리지 않고 같은 체크포인트에서 Plan 단계만 다시 실행했다. 세 재검증의 LLM 호출 수는 모두
0이다. 이는 환경 또는 검증 입력만 달라졌을 때 상위 산출물을 재생성하지 않는 경로가 실제로
작동했음을 보여준다.

최초 Azure·GCP 출력은 HTTP 계획에 요청하지 않은 HTTPS 종료를 추가했다. 기존 검증기는 이를
통과시켰으나, endpoint protocol 정합성 gate를 추가한 뒤 실패로 재판정하고 공통 프롬프트로
각 CSP를 한 번 다시 생성했다. Azure의 reserved provider 파일 위반과 Windows 파일 잠금 실패도
원시 결과에 남겼다. 따라서 최종 성공 셀만 골라 성공률로 해석하지 않는다.

GCP의 별도 disk는 `google_compute_instance` 내부 블록으로 연결되어 별도 attachment 리소스가
없다. Plan의 VM·disk 개수는 일치하지만 실제 format·mount·컨테이너 경로 연결은 정적으로
확정하지 않고 `not-observed`로 남겼다. 세 CSP 모두 사설 runtime 연결, 업무 기능, 성능과 실제
cloud apply는 이 실험의 측정 대상이 아니다. 기계 판정 요약은
[`multi-provider-resource-plan-iac-adjudication-20260815.json`](../../artifacts/measurements/resource-plan-iac/multi-provider-resource-plan-iac-adjudication-20260815.json)에
있다.

### HTTPS 외부 입력 경계

HTTPS 구조도에 인증서 노드가 있다는 사실만으로 배포 준비가 끝난 것은 아니다. 도메인 이름과
인증서 입력 근거가 없으면 `ResourcePlan.unresolved`에 두 항목을 남기며, IaC 생성 에이전트는
호출하지 않는다. 입력은 accepted deployment need의 구조화된 `metadata.tls.hostname`과
`metadata.tls.certificateInputRef`에서만 받는다. 자연어의 HTTPS 언급이나 CSP 이름으로 값을
추측하지 않는다.

AWS·Azure·GCP의 직접 HTTPS와 관리형 LB HTTPS를 공통 회귀시험으로 확인했다. 입력이 없는
6개 계획은 모두 구조도에 외부 전제와 미해결 항목을 표시하고 IaC LLM 호출 0회로 차단됐다.
명시 입력을 준 3개 계획은 요구사항 ID를 보존한 채 미해결 상태가 해소됐다. 9개 셀의 기계
판정은 [`tls-input-gate-20260815.json`](../../artifacts/measurements/tls-input-gate-20260815.json)에
있다. 이는 실제 인증서 발급·DNS 검증 성공 근거가 아니라, 외부 소유 입력이 준비되기 전
잘못된 IaC를 만들지 않는 사전 조건이다.

## 수강신청 생성 앱의 로컬 게이트

기존 수강신청 구현 체크포인트를 처음부터 생성하지 않고, 실패한 구현 하위 작업만 수정했다.
최초 Docker 실행은 `/health` 500, 빈 강좌 목록, H2 메모리 DB 사용 때문에 실패했다. 이 결과는
[`course-registration-local-gate-20260815.json`](../../artifacts/measurements/course-registration-local-gate-20260815.json)에
보존했다. 이후 일반 계약 진단과 독립 HTTP 오라클이 다음 문제를 차례로 찾았다.

- 요구된 PostgreSQL과 실제 H2 설정의 불일치
- PostgreSQL용 Flyway 런타임 모듈 누락
- JSON 요청 본문 바인딩과 업무 흐름 누락
- 마지막 좌석과 중복 신청을 직렬화하지 못하는 동시성 처리
- DB가 끊겨도 `/health`가 200을 반환하거나 응답이 너무 늦는 문제

수정은 요구사항·설계·scaffold를 다시 만들지 않고 `implementation.logic` 체크포인트에서만
진행했다. 완료된 멤버 workflow도 외부 검증의 구조화된 수리 피드백이 있으면 구현 LLM을 한 번
호출하며, 단순한 PostgreSQL/Flyway 복합 의존성은 결정론적으로 닫는다. 특정 강좌 이름을 코드
규칙으로 추가하지 않았고, 요청 바인딩·동시성·DB health라는 일반 계약만 보완했다.

최종적으로 빠른 재검증 이미지와 생성된 원본 Dockerfile 이미지 모두 다음 게이트를 통과했다.

| 게이트 | 결과 | 증명하는 범위 |
|---|---:|---|
| 업무·동시성 오라클 | 13/13 통과 | 조회·신청·취소, 마지막 좌석 1건만 성공, 동시 중복 신청 방지 |
| DB container 재생성 후 영속성 | 2/2 통과 | 동일 named volume을 재연결하면 신청과 잔여 좌석이 보존됨 |
| DB 중단 시 health | 1/1 통과 | `/health`가 약 5.3초 안에 503과 `DOWN`을 반환 |
| 실험 잔여물 | 0 | 해당 실행이 만든 container와 volume을 정리함 |

원본 이미지 결과는
[`course-registration-business-oracle-original-image-20260815.json`](../../artifacts/measurements/course-registration-business-oracle-original-image-20260815.json),
[`course-registration-persistence-original-image-20260815.json`](../../artifacts/measurements/course-registration-persistence-original-image-20260815.json),
[`course-registration-database-unavailable-health-original-image-20260815.json`](../../artifacts/measurements/course-registration-database-unavailable-health-original-image-20260815.json)에
있다. 영속성 통과는 동일 volume 재연결의 근거이며 DB 복제나 고가용성을 뜻하지 않는다.

업무 오라클은 단계마다 상태를 남긴다. 마지막 좌석 경쟁에 사용한 학생이 다음 중복 신청
단계와 우연히 겹치면서 최종 목록 길이가 달라지는 오라클 상태 누출도 발견했다. 앱을 사례에
맞춰 고치는 대신 두 단계의 synthetic actor 집합을 분리했다. 이는 평가기의 잘못을 피평가
시스템 실패로 세지 않기 위한 수정이다.

## 수강신청 IaC 종단 결과

로컬 게이트 통과 뒤 같은 체크포인트에서 IaC 단계만 실행했다. 검증기는 다음 오류를 순서대로
찾아냈다.

1. 앱의 Flyway migration과 중복되는 DB schema·seed 초기화
2. JDBC URL 접두사 손실, AWS VPC outbound 경로 누락, Nitro EBS guest device 고정 가정
3. 존재하지 않는 AMI 검색 조건과 Terraform 변수 default의 리소스 참조
4. 고정 AWS Provider `5.100.0` 대신 `~> 5.100` 사용 및 보조 `template_file` Provider 추가

일반 보완으로 application-owned migration, 필수 환경변수, 관측된 URL 접두사, AWS bootstrap
egress, EBS stable identity, Plan 생성 여부를 사전 검증한다. Amazon Linux 2023 이미지는 공식
공개 SSM parameter 또는 명시적 `ami_id`만 사용하도록 입력했다. 마지막 실행은 생성 38.53초와
수정 35.33초 뒤 4번 Provider 계약 위반으로 차단됐으며, 검증되지 않은 파일은 `application/infra`에
승격되지 않았다. 측정은
[`course-registration-iac-ami-closure-20260815.json`](../../artifacts/measurements/course-registration-iac-ami-closure-20260815.json)에
있다.

이후 생성 응답을 새로 만들지 않고 같은 체크포인트의 IaC 응답을 재생해 생성 경계를 보완했다.
시스템 소유 Provider 블록을 한 곳으로 정규화하고 표준 `template_file` 사용을 내장
`templatefile()`로 낮춘 결과 Plan의 18개 리소스는 통과했다. 그러나 강화된 runtime 의미
검사는 생성 IaC가 `ebsnvme-id`를 장치 열거 없이 잘못 호출하고 있음을 찾아냈다. 따라서 순수
시스템 생성 IaC의 종단 성공으로 판정하지 않았다. 세 replay의 시간과 최종 재판정은
[`course-registration-iac-replay-adjudication-20260815.json`](../../artifacts/measurements/course-registration-iac-replay-adjudication-20260815.json)에
요약했다.

실제 AWS 개발 실험은 이 출력에 아래 네 가지 일반 bootstrap 수정을 명시한 실험 harness로
수행했다.

- EBS ID와 일치하는 NVMe 장치를 열거해 선택
- filesystem UUID를 `/etc/fstab`에 기록
- 장기 실행 container에 restart policy 지정
- 새 filesystem의 root가 아니라 그 아래 전용 data 디렉터리를 실제 runtime data path로 지정

초기 apply는 18개 리소스를 113.2초에 만들었지만, ext4 root를 PostgreSQL data path에 바로
연결해 두 container가 반복 재시작했다. 새 filesystem root에는 `lost+found` 같은 항목이 생길 수
있어 빈 data directory를 요구하는 runtime 초기화와 충돌한다. 네트워크·subnet·NAT·security
group·EBS를 보존하고 상태 VM, 앱 VM과 두 연결 객체만 교체한 부분 복구는 99.8초가 걸렸다.
수정 뒤 실제 AWS HTTPS endpoint에서 다음 결과를 얻었다.

| 실제 cloud gate | 결과 | 해석 |
|---|---:|---|
| health ready | 200, `UP` | 앱과 사설 PostgreSQL 연결이 실제 배포에서 동작 |
| 업무·동시성 | 13/13 통과 | 조회·신청·취소 및 두 동시성 불변식 충족 |
| DB VM 중지 | 503, `DOWN` | 앱 health가 필수 상태 연결 실패를 반영 |
| DB VM 재기동 | EC2 status OK까지 148.4초 | 이 한 개발 실행의 관찰값이며 복구시간 보장 아님 |
| 재기동 후 영속성 | 2/2 통과 | 같은 EBS 재연결·자동 mount 뒤 신청과 좌석 수 보존 |
| cleanup | state 리소스 18개 destroy, 실행 소유 잔여 0 | 공유·기존 리소스는 삭제 대상 아님 |

업무 연결에는 하루짜리 synthetic self-signed 인증서를 사용했으며 인증서 검증은 시험에서만
비활성화했다. 전체 결과는
[`course-registration-aws-cloud-experiment-20260815.json`](../../artifacts/measurements/course-registration-aws-cloud-experiment-20260815.json)에
있다. 이 결과는 AWS 1회 개발 관찰이며, harness 수정이 포함됐으므로 순수 생성 성공이나 3사
일반화 근거가 아니다. DB VM 재기동 뒤 데이터가 남았다는 사실도 상태 계층 HA를 뜻하지 않는다.
AWS의 Linux Nitro 장치 식별은
[EBS 공식 문서](https://docs.aws.amazon.com/ebs/latest/userguide/identify-nvme-ebs-device.html)를
따랐다. PostgreSQL도 mount point root에 생긴 `lost+found` 때문에 초기화가 실패할 수 있으므로
그 아래 디렉터리를 쓰라고
[`initdb` 공식 문서](https://www.postgresql.org/docs/current/app-initdb.html)에서 안내한다.

따라서 이 실행은 다음처럼 해석한다.

- 수강신청 생성 앱의 로컬 업무·동시성·영속성·DB 장애 동작은 확인했다.
- 실제 cloud apply와 업무·장애·영속성 gate는 통과했지만, 명시적 harness 수정이 포함됐으므로
  순수 시스템 생성 성공으로 바꾸지 않았다.
- 앞선 도메인 중립 E1~E3 실험이 3사의 App–사설 State–disk·traffic filter·endpoint 재결합
  관계를 확인했지만, 그것이 이 앱의 cloud 업무 성공을 대신하지는 않는다.

## 시간 병목과 다음 순서

이번 실행에서 원본 Docker image build는 약 89.9초였다. 의존성을 한 번 채운 뒤 같은 workspace의
offline build는 약 18.8초였고, 두 DB health 수정의 LLM 시간은 각각 약 20.1초와 6.36초였다.
가장 낭비가 컸던 부분은 Windows sandbox의 Gradle report/cache 접근 실패를 앱 실패로 오인한
두 번의 대기(약 69초와 60초)였다. 이후에는 같은 체크포인트·고정 dependency cache·원본
Dockerfile 검증을 사용했다.

다음 작업은 범위를 늘리지 않고 아래 순서로 제한한다.

1. 새 filesystem root와 runtime data path 사이에 전용 하위 디렉터리를 두는 규칙을 공통 생성
   지시와 의미 검사에 반영한다.
2. 같은 오류를 사례별 LLM 재시도로 덮지 않고, 다음 생성 실행에서 일반 gate가 차단하는지만
   확인한다.
3. 실제 실험의 state 소유 리소스 18개와 public image repository는 정리했고, 과금 가능 자원
   유형과 state의 잔여가 모두 0임을 확인했다.
4. Azure·GCP 앱 종단 결과나 성능 추천은 이 AWS 결과로 대신하지 않는다. 별도 고정 workload
   profile과 실제 cloud 재검증이 준비될 때까지 미확정으로 둔다.

이 작업은 중립 앱 의존성 사례를 더 늘리는 것이 아니라, 이미 정한 ResourcePlan이 한 실제
생성 앱의 검증 가능한 IaC와 cloud 동작으로 이어지는지 확인하는 마지막 통합 경계다.
