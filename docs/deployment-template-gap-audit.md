# 배포 템플릿·IaC·실행 스크립트 지원 범위 점검

- 점검 기준일: 2026-09-08 (Asia/Seoul)
- 기준 커밋: `6d63d81eeab54be83d9e458de94348e26c3e6096`
- 점검 대상: 배포 계획, OpenTofu, VM bootstrap, Compose, 구현 산출물 ZIP, 사용자 실행·재개·삭제 스크립트
- 목적: 현재 EasyDep이 지원하는 배포 범위 안에서 실제 배포를 방해하거나 잘못된 배포를 만들 수 있는 문제만 식별한다.

## 1. 지원 범위

이 문서의 완료 조건은 다음 제품 계약만 대상으로 한다.

- AWS, Azure, GCP
- Docker-on-VM
- 단일 Region
- 사용자가 지정한 고정 replica
- standalone VM과 관리형 VM 그룹
- direct public IP, L4 load balancer, private egress
- HTTP와 workload 사이 내부 TCP
- 단일·복수 workload의 같은 VM 배치 또는 분리 배치
- block disk와 replica별 block disk
- 생성 애플리케이션 image와 지원되는 prebuilt image
- Registry push·pull과 immutable image 실행
- 일반 환경변수와 기존 CSP Secret 전달
- 사용자가 내려받은 PowerShell script를 직접 실행하는 `plan → apply → health → destroy`
- 실패한 단계에서 같은 로컬 OpenTofu state와 산출물을 사용한 재개

지원 범위 밖 기능은 이 문서의 결함·일정·완료 조건에 포함하지 않는다.

현재 검증 corpus는 15개 topology case를 AWS·Azure·GCP에 각각 투영한 45개 module이다. 이
문서는 다음 두 종류만 대상으로 한다.

- 45개 module의 HCL·bootstrap에 실제로 나타나는 분기
- 45개 module이 공통으로 사용하는 late-bound port, image digest, Secret 입력, PowerShell,
  snapshot·ZIP·재개 경로

45개 module에 없는 새 topology나 입력 형태를 위한 일반화 작업은 포함하지 않는다.

## 2. 결론

현재 생성기는 지원 범위의 주요 배치 형태를 이미 세 CSP에서 실배포한 기록이 있다. 단일 VM,
관리형 VM 그룹, 복수 workload, 사설 workload, block disk, replica별 disk, 외부 Secret이 실제
cloud에서 동작한 사례가 있다. 따라서 배포 구조 전체를 다시 만드는 작업은 필요하지 않다.

점검에서 발견해 이번 변경에서 해결한 문제는 여덟 묶음이다.

1. GCP private firewall과 내부 통신 규칙의 의미 오류
2. 45개 module의 port·health path 생성 기본값 불일치 가능성
3. corpus에 포함된 복수 generated application의 build context 불일치
4. 부분 tfvars·image digest·cloud identity를 구분하지 못하는 재개 로직
5. 외부 Secret 최초 전달의 입력·권한 scope·payload 검사 부족
6. 검사한 runtime 파일과 실제 VM이 실행하는 runtime 파일의 차이
7. replica별 보존 disk의 삭제·기록 누락
8. 다운로드 release, private health, 진단 출력의 일관성 부족

이 문제들은 새로운 배포 기능을 구현하는 일이 아니라 현재 지원 경로의 결정적 생성값과
script 동작을 바로잡는 작업이다. 아래 수정은 45개 공식 module과 공통 실행 경로에만 적용했다.

## 3. 확인한 배포 흐름

```text
WorkloadGraph
  → DeploymentPlan
  → provider별 ResourcePlan
  → OpenTofu + cloud-init/bootstrap + Compose
  → 구현 산출물 snapshot
  → 사용자 ZIP 다운로드
  → easydep.ps1
      → 도구·CSP login 확인
      → tfvars 입력
      → Registry 준비
      → image build·push·digest 기록
      → tofu plan
      → 사용자 승인
      → tofu apply
      → public health 확인
      → tofu destroy
```

주요 코드 위치는 다음과 같다.

- 배포 graph·계획: [`app/design/services/deployment_diagram/`](../app/design/services/deployment_diagram/)
- OpenTofu·bootstrap 렌더링: [`app/implementation/delivery/iac_renderer.py`](../app/implementation/delivery/iac_renderer.py)
- Compose·PowerShell 패키지: [`app/implementation/delivery/package.py`](../app/implementation/delivery/package.py)
- 패키지 검증: [`app/implementation/delivery/verification.py`](../app/implementation/delivery/verification.py)
- 배포 산출물 refresh: [`app/implementation/delivery/refresh.py`](../app/implementation/delivery/refresh.py)
- snapshot 파일 선별: [`app/implementation/application/source_files.py`](../app/implementation/application/source_files.py)
- ZIP 다운로드: [`app/implementation/interfaces/http.py`](../app/implementation/interfaces/http.py)
- OpenTofu 예제 검사: [`scripts/validate_deployment_iac_examples.py`](../scripts/validate_deployment_iac_examples.py)

## 4. 이미 확인된 정상 동작

### 4.1 구조와 생성 방식

- LLM은 provider resource나 HCL을 직접 작성하지 않는다.
- 코드가 검증된 `ResourcePlan`을 provider별 OpenTofu로 결정적으로 렌더링한다.
- resource reference, CIDR 배치, workload placement, storage binding을 단위 테스트가 검사한다.
- OpenTofu provider version은 정확한 버전으로 고정한다.
- 생성 애플리케이션은 Registry push 결과의 SHA-256 digest로 실행한다.
- Secret 실제 값은 설계 bundle에 저장하지 않고 CSP Secret reference만 배포 입력으로 남긴다.
- 사용자는 OpenTofu plan을 본 뒤 별도로 승인해야 apply할 수 있다.
- destroy는 `DESTROY` 확인을 요구한다.

### 4.2 기존 실배포 기록

[`live-deployment-verification.md`](live-deployment-verification.md)에 다음 사례가 기록되어 있다.

- 세 CSP의 공개 단일 VM
- 세 CSP의 관리형 VM 그룹과 load balancer
- 같은 VM과 분리 VM의 복수 workload
- 공개 workload에서 사설 workload로의 내부 연결
- 영속 disk와 replica별 disk
- 공개 주소가 없는 VM의 NAT 기반 Registry pull
- AWS·Azure·GCP 외부 Secret 전달
- 실패 단계별 재개와 동일 state 삭제

이 기록은 현재 결함을 무시할 근거가 아니라, 수정 뒤 전 범위를 다시 설계할 필요가 없다는
근거로 사용한다.

### 4.3 배포되지 않는다고 전달받은 앱 확인 결과

앱 `3135e7d2-32a8-4401-ab2f-d66fbb372c52`는 다음 항목이 일치했다.

- Spring Boot, Dockerfile, Compose, cloud security, user-data, health URL의 port가 모두 `8000`
- H2 file DB를 사용하고 `SPRING_DATASOURCE_URL`, `SPRING_DATASOURCE_USERNAME`,
  `SPRING_DATASOURCE_PASSWORD` 계약 일치
- 구현 source와 Docker build context 포함
- 설계 manifest 14개 파일의 hash 일치
- Docker image build와 local container `/healthz`의 `UP` 응답 성공
- AWS login과 AMI 자동 조회 성공

직접 실행은 `secret_reference_application_security_password` 입력에서 중단했다. billable AWS
resource는 만들지 않았지만 일부 값만 채워진 `terraform.tfvars`가 남았다. 이 앱에서 실제로
확인된 제품 결함은 필수 Secret 입력을 중단한 뒤 다음 실행이 누락 값을 다시 묻지 않는 재개
로직이다.

따라서 전달받은 port `8000/8080` 불일치, datasource 변수 전달, 앱 source 누락은 이 패키지의
실제 원인이 아니다. Secret과 재개 문제를 수정한 뒤 같은 패키지의 AWS
`plan → apply → /healthz → destroy`가 최종 확인 절차다.

## 5. 현재 지원 범위의 발견 사항과 처리 상태

| ID | 우선순위 | 45개 module 적용 | 기존 영향 | 상태 |
|---|---:|---:|---|---|
| DPL-01 | P0 | 13개 | GCP 8개 전체 TCP 공개, AWS 5개 내부 TCP 전체 허용 | 해결 |
| DPL-02 | P0 | 45개 | 생성 port·health path가 공식 기본값으로 고정되지 않음 | 해결 |
| DPL-03 | P0 | 6개 | 두 generated application을 같은 source에서 반복 build | 해결 |
| DPL-04 | P0 | 45개 | 부분 입력과 잘못된 digest를 정상 checkpoint로 오인 | 해결 |
| DPL-05 | P0 | 3개 | provider별 Secret reference 의미 불일치 | 해결 |
| DPL-06 | P0 | 45개 | 실제 VM 산출물 누락과 `INCONCLUSIVE` publish | 해결 |
| DPL-07 | P1 | 3개 | replica별 retained disk ID와 결과 기록 누락 | 해결 |
| DPL-08 | P1 | 공통 경로 | snapshot 혼합과 잘못된 완료·접근 안내 | 해결 |

### DPL-01. private·internal traffic rule을 의도대로 제한하지 못함

**현재 동작**

[`iac_renderer.py`](../app/implementation/delivery/iac_renderer.py)의 GCP firewall 렌더링은
`publicInterfaces`와 `source_tags`가 모두 없으면 다음 값을 만든다.

```hcl
allow {
  protocol = "tcp"
  ports    = ["1-65535"]
}
source_ranges = ["0.0.0.0/0"]
```

GCP 15개 중 8개에서 위 rule이 실제로 생성된다. 두 private case뿐 아니라 분리 배치에서
사설 compute를 사용하는 6개 case에도 나타난다. private workload에 inbound 요구가 없거나
허용 source가 정해져 있는데 공개 allow rule이 생기므로 현재 corpus의 보안·기능 결함이다.

AWS workload 사이 security group도 source security group이 있으면 TCP `1-65535`를 허용한다.
이 분기는 AWS 15개 중 분리·내부 연결이 있는 5개 case에 나타난다. HTTP·내부 TCP 지원에는
계획된 connection port만 열면 된다.

**수정 조건**

- inbound가 없는 GCP target에는 allow rule을 만들지 않는다.
- 내부 통신은 계획된 target port와 source workload identity/tag만 허용한다.
- load balancer health check rule은 workload traffic과 분리한다.
- private plan에서 public source range가 0개인지 negative test로 확인한다.

### DPL-02. port·health path를 생성 원천에서 고정하지 않음

**현재 동작**

45개 graph의 interface port는 runtime binding 전에는 비어 있고 package가
`terraform.tfvars.example`에 공식 container port 기본값을 만든다. health path도 생성 앱의
runtime 관찰 결과에서 결합된다. 이 생성 원천을 회귀 조건으로 고정하지 않으면 template이나
fixture 변경이 잘못된 port·경로를 공식 산출물에 넣을 수 있다.

**수정 조건**

- 공식 15개 topology가 사용하는 port 기본값을 package 생성 원천에서 결정한다.
- 생성 앱은 관찰된 실제 port와 health path를 binding한다.
- corpus HTTP interface는 실제 예제 경로 `/healthz`를 결정적으로 생성한다.
- 별도의 WorkloadGraph·Terraform 중복 validator는 추가하지 않고, 45개 정상 생성 invariant로
  회귀를 막는다.

### DPL-03. 두 generated application을 같은 source로 build함

**현재 동작**

45개 module 중 6개는 `web`과 `worker`가 모두 `generatedApplication`인 두 topology case의
AWS·Azure·GCP projection이다. `easydep.ps1`은 두 Registry target을 순회하지만 두 workload 모두
같은 application root를 `docker build`한다. workload별 Dockerfile·context가 없으므로 같은 앱을
두 번 build·push한다.

나머지 18개 prebuilt 포함 module은 fixture 단계에서 이미 SHA-256 digest를 사용하므로 이번
수정 대상이 아니다.

**수정 조건**

- 현재 단일 generated source 계약에 맞춰 `web`만 generated application으로 둔다.
- 두 topology의 `worker`는 SHA-256 digest가 고정된 prebuilt workload로 생성한다.
- 한 source를 두 workload 이름으로 중복 build·push하지 않는다.

### DPL-04. 입력·image·cloud identity checkpoint 검증 부족

**현재 동작**

`Initialize-Inputs`는 `terraform.tfvars`가 존재하면 즉시 반환한다. 최초 실행은 example을 먼저
복사하고 값을 하나씩 기록하므로 prompt 중단 시 부분 파일이 남는다. 다음 실행은 빈 required
variable을 다시 묻지 않는다.

`runtime/image-digests.env`도 존재 여부만으로 image 준비 전체를 건너뛴다. 다음 정보가 없다.

- 모든 generated workload의 digest가 들어 있는지
- digest가 정확한 `sha256:<64 hex>`인지
- 현재 Registry에 해당 digest가 존재하는지
- 현재 provider, region, account·subscription·project와 같은 대상인지
- application source와 Dockerfile이 checkpoint 생성 때와 같은지

OpenTofu state와 입력에도 실제 cloud identity를 고정하지 않는다. 사용자가 CLI account를 바꾼
뒤 같은 폴더를 실행하면 plan·Registry login·state가 서로 다른 대상을 볼 수 있다.

**수정 조건**

- 실행마다 required tfvars를 parse하고 누락되거나 invalid인 값만 다시 묻는다.
- 완성된 입력을 임시 파일에 쓴 뒤 atomic rename한다.
- workload별 digest와 Registry 존재 여부를 검증한다.
- checkpoint에 provider, region, cloud identity, workload ID, source hash를 기록한다.
- plan/apply/destroy 전에 현재 CLI identity와 checkpoint identity가 같은지 확인한다.
- Ctrl+C 뒤 재개와 부분 checkpoint를 자동 테스트한다.

### DPL-05. 외부 Secret 최초 전달의 provider 계약 부족

**현재 동작**

외부 Secret 전달은 현재 지원 기능이다. 실제 값은 bundle에 저장하지 않는 구조도 올바르다.
다만 script는 사용자가 입력한 reference가 provider가 기대하는 형식이고 실제로 존재한다고
가정한다. Registry나 VM을 만들기 전에 이를 확인하지 않는다.

- AWS bootstrap은 `SecretString` 전체를 하나의 환경변수로 넣는다. 지원 payload가 단일 문자열인지
  script가 먼저 확인하지 않는다.
- Azure bootstrap은 data-plane URL과 resource ID 형태를 함께 받지만 role assignment의 scope에도
  같은 입력을 사용할 수 있다. 조회 좌표와 RBAC scope를 하나의 문자열로 겸용하면 입력 형태에
  따라 둘 중 하나가 맞지 않을 수 있다.
- GCP는 짧은 Secret 이름과 `projects/.../secrets/...` 경로를 모두 받으므로 canonical resource를
  만든 뒤 존재 여부를 확인해야 한다.
- 중단 뒤 남은 부분 tfvars는 Secret reference 입력도 건너뛴다.

**수정 조건**

- AWS ARN, Azure Key Vault resource ID, GCP full Secret resource path를 provider별로 검증한다.
- Azure는 입력 resource ID에서 RBAC scope와 vault·Secret 조회 좌표를 별도로 파생한다.
- billable workload를 만들기 전에 caller credential로 Secret metadata 존재를 확인한다.
- 지원 payload를 단일 문자열로 명시하고 다른 형식이면 값 자체를 출력하지 않은 채 실패한다.
- Secret 실제 값은 tfvars, OpenTofu state, plan, progress log, error log에 기록하지 않는다.
- 렌더링 test에서 IAM binding과 container 환경변수가 같은 논리 Secret을 가리키는지 확인한다.

사전 검사는 입력 reference와 현재 caller가 볼 수 있는 metadata를 확인한다. apply 뒤 만들어지는
VM identity의 최종 read 권한은 OpenTofu 관계와 실제 bootstrap health로 확인한다.

### DPL-06. 검사한 runtime 파일과 VM이 실행하는 파일이 다를 수 있음

**현재 동작**

Compose 생성 경로가 둘이다.

1. [`package.py`](../app/implementation/delivery/package.py)의 `runtime/compose.yaml`
2. [`iac_renderer.py`](../app/implementation/delivery/iac_renderer.py)의 provider bootstrap 안 inline Compose

같은 storage ID도 한쪽은 `/mnt/easydep/workload-data`, 다른 쪽은 label 정규화를 거쳐
`/mnt/easydep/workload_data`가 될 수 있다. 패키지 검증기는 정적 Compose에
`docker compose config`를 실행하지만 실제 VM에서 작성할 inline Compose 전체를 같은 방식으로
검사하지 않는다. 45개 module 모두 late-bound port와 inline Compose를 사용하므로 공통으로
이 검증 사각지대를 지난다.

cloud-init schema 검사도 대표 template 중심이며 모든 compute별 bootstrap을 보증하지 않는다.
필수 도구가 없거나 실행할 수 없으면 검증 결과가 `INCONCLUSIVE`가 될 수 있는데, IaC 생성기는
`FAIL`만 차단해 이 패키지를 배포 가능한 산출물로 저장할 수 있다.

runtime `.env`를 shell에서 `source`하는 경로도 있다. 환경변수 파일 내용이 shell 문법으로
실행될 수 있으므로 Docker 환경 파일로만 다뤄야 한다.

**수정 조건**

- Compose 내용을 한 canonical renderer에서 한 번 만들고 package와 bootstrap이 공유한다.
- 모든 compute별 실제 bootstrap을 sample 변수로 렌더링한다.
- 실제 산출물마다 cloud-init schema, `bash -n`, ShellCheck, `docker compose config`를 실행한다.
- 필수 검사가 `PASS`가 아니면 배포 가능한 release로 publish하지 않는다.
- `.env`는 source하지 않고 허용된 key/value parser 또는 Compose `env_file`로만 전달한다.

### DPL-07. replica별 retained disk가 삭제 기록에서 빠짐

**현재 동작**

replica별 storage는 compute의 embedded block으로 렌더링된다. 반면 `easydep.ps1`의
`retainedResources`는 독립 node 중 `deletionPolicy=retain`인 항목만 수집한다.

- AWS는 `delete_on_termination=false`로 disk를 남길 수 있지만 보존 목록에 없다.
- GCP는 `auto_delete=false`로 disk를 남길 수 있지만 보존 목록에 없다.
- Azure는 replica별 disk의 retain 의미와 기록 경로가 동일하게 표현되지 않는다.

따라서 destroy가 성공해도 사용자가 유지하기로 한 disk의 cloud ID를 안내받지 못하거나, 과금
disk가 설명 없이 남을 수 있다.

**수정 조건**

- replica별 disk도 deployment·workload·replica를 식별하는 tag/label을 가진다.
- destroy 전에 provider별 실제 disk ID와 삭제 정책을 기록한다.
- `retained-resources.txt`에 resource 종류, provider ID, workload, replica, region을 기록한다.
- destroy 뒤 동일 deployment label로 조회해 retained 목록과 잔여 disk가 정확히 일치하는지 확인한다.

### DPL-08. release 일관성·private health·진단 안내 부족

**현재 동작**

delivery refresh는 `DEPLOYMENT_FILE`과 `IAC_CODE` snapshot을 순서대로 별도 저장한다. download는
artifact type별 최신 snapshot을 다시 조회한다. refresh 중간 실패나 동시 download가 발생하면
서로 다른 세대의 PowerShell과 HCL이 한 ZIP에 섞일 수 있다. manifest에는 artifact version과
file count만 있고 release ID와 각 파일 hash가 없다.

public health 확인은 반복 HTTP 요청을 하지만 마지막 status, body, 오류를 보존하지 않는다.
private application은 공개 health URL이 없으면 사용자가 cloud 내부에서 확인하라는 메시지만
출력하고 종료한다. 지원 대상인 private 배포의 성공 여부를 script 결과만으로 알 수 없다.

direct public IP가 있으면 provider 설정과 관계없이 `ssh easydep@<ip>`를 출력한다. AWS와 GCP는
해당 user·key·port를 구성하지 않으므로 동작하지 않는 안내가 된다. SSH output은 21개
direct-public module에 존재하며, 그중 AWS·GCP 14개가 설정과 맞지 않는다. 공개 health output이
없는 private module은 6개다.

**수정 조건**

- deployment file과 IaC version을 하나의 release ID로 묶고 download가 그 ID를 고정해 읽는다.
- manifest에 release ID, job ID, file path, size, SHA-256을 기록한다.
- public health 실패 시 최근 HTTP 오류와 bootstrap/container 진단 명령을 안내한다.
- private 배포는 `health=UNVERIFIED`를 명시하고 provider-native 내부 확인 명령을 제공한다.
- 실제 접근 수단을 생성하지 않은 SSH output은 제거한다.

## 6. provider별 수정 지점

| 주제 | AWS | Azure | GCP |
|---|---|---|---|
| private·internal ingress | source SG의 실제 target port만 허용 | 실제 target port 연결 확인 | inbound 없는 firewall allow 제거 |
| Secret reference | ARN·SecretString metadata 검사 | resource ID에서 RBAC scope와 조회 좌표 분리 | 이름/path canonicalize와 metadata 검사 |
| image checkpoint | ECR digest와 account·region 확인 | ACR digest와 subscription 확인 | Artifact Registry digest와 project 확인 |
| retained disk | EBS volume ID 기록 | managed disk ID·정책 기록 | persistent disk ID 기록 |
| cloud identity | STS account 고정 | subscription·tenant 고정 | project·active account 고정 |

## 7. 6시간 수정 계획

여덟 발견 사항을 파일 소유권이 겹치지 않게 나누고 마지막에 통합 검증한다.

| 경과 시간 | 작업 | 산출물 |
|---:|---|---|
| 0:00~0:30 | 기준 test와 fixture 고정 | 현재 실패를 재현하는 negative test 목록 |
| 0:30~1:30 | DPL-01·02 | network semantic과 정상 port·health 생성값 수정 |
| 0:30~1:45 | DPL-04·05 병렬 작업 | tfvars/digest/identity 재개와 Secret preflight 수정 |
| 0:30~1:45 | DPL-03·07 병렬 작업 | 복수 generated build 차단·연결과 retained disk 추적 수정 |
| 1:45~3:15 | DPL-06·08 | actual runtime gate와 release·health·출력 수정 |
| 3:15~4:30 | 통합 회귀 수정 | 단위·package·PowerShell test 통과 |
| 4:30~5:30 | OpenTofu 검사 | 세 provider 45개 validate와 영향받은 대표 plan |
| 5:30~6:00 | 실제 앱 재검증·문서·커밋 | Docker health, 변경 기록, 최종 commit |

시간이 부족하면 기능을 추측해 구현하지 않는다. 우선순위는 P0 전부, DPL-07, DPL-08의 잘못된
SSH·health 상태 순이다. release transaction 변경이 남으면 현재 download endpoint에 동시 refresh
차단을 두어 혼합 release부터 방지한다.

## 8. 검증 기준

### 8.1 단위·semantic test

- private GCP plan에는 public ingress CIDR이 없다.
- workload connection rule에는 계획된 target port만 있다.
- 45개 공식 module이 유효한 기본 port와 `/`로 시작하는 실제 health path를 생성한다.
- 6개 복수 workload module에서 generated source는 하나이고 worker는 고정 digest prebuilt이다.
- 부분 tfvars는 누락된 값만 다시 요구한다.
- 비어 있거나 일부 workload만 있는 digest checkpoint를 재사용하지 않는다.
- cloud identity가 다르면 plan·apply·destroy를 차단한다.
- provider별 잘못된 Secret reference와 존재하지 않는 metadata를 과금 단계 전에 차단한다.
- 실제 bootstrap Compose와 package Compose가 같은 canonical 내용에서 생성된다.
- 필수 검사가 `INCONCLUSIVE`이면 deliverable이 아니다.
- replica별 retain disk가 보존 manifest 대상에 포함된다.
- 지원되지 않는 SSH command를 output하지 않는다.

### 8.2 회귀 기준과 수정 후 결과

기준 커밋에서는 다음 6개 파일의 기존 테스트 254개가 통과했다. 수정 뒤 같은 파일에 정상
생성 invariant와 publish gate 회귀를 추가했고, 최종 실행에서는 306개가 모두 통과했다.

```powershell
python -X utf8 -m pytest `
  tests/test_deployment_templates.py `
  tests/test_deployment_planner_boundaries.py `
  tests/test_deployment_projection_boundaries.py `
  tests/test_deployment_workload_boundary.py `
  tests/test_deployment_topology_decision_flow.py `
  tests/test_app_cloud_contracts.py -q
```

| 파일 | 기준 | 수정 후 |
|---|---:|---:|
| `test_deployment_templates.py` | 192 | 197 |
| `test_deployment_planner_boundaries.py` | 3 | 3 |
| `test_deployment_projection_boundaries.py` | 13 | 14 |
| `test_deployment_workload_boundary.py` | 13 | 59 |
| `test_deployment_topology_decision_flow.py` | 5 | 5 |
| `test_app_cloud_contracts.py` | 28 | 28 |
| 합계 | 254 | 306 |

추가로 IaC 보안·Secret·retained disk 전용 테스트 5개, snapshot transaction 6개,
다운로드 release 2개, 공통 toolchain gate 8개도 통과했다. `ruff`와 `git diff --check`도
최종 변경 파일에서 통과했다.

### 8.3 OpenTofu·package 검사

```powershell
python -X utf8 scripts/validate_deployment_iac_examples.py --plan
```

- AWS·Azure·GCP 각각 15개, 총 45개 module validate
- provider별 대표 5개 plan
- private, public, managed group, 복수 workload, per-replica storage 포함
- 실제 배포 package의 Compose·bootstrap·PowerShell 검사 PASS

최종 실행은 provider별 checkpoint로 완료했다.

| Provider | Validate | Plan | 결과 |
|---|---:|---:|---|
| AWS | 15 | 5 | PASS |
| Azure | 15 | 5 | PASS |
| GCP | 15 | 5 | PASS |
| 합계 | 45 | 15 | PASS |

검사 도중 AWS launch-template EBS의 unsupported `tags`와 Azure Secret 정규식 escape를 실제
OpenTofu가 발견했다. 각각 해당 실패 case부터 수정·재개한 뒤 위 합계를 완료했다. 공식
배포 다이어그램도 90개 PUML·90개 SVG를 다시 생성했고 byte 비교 검사를 통과했다.

### 8.4 문제가 보고된 앱의 완료 기준

앱 `3135e7d2-32a8-4401-ab2f-d66fbb372c52`에 대해 다음을 확인한다.

1. 기존 부분 tfvars에서 누락된 Secret만 다시 입력받는다.
2. AWS Secret ARN과 metadata를 실제 값 노출 없이 확인한다.
3. OpenTofu validate와 plan이 성공한다.
4. 사용자가 승인하면 Registry·image push와 apply가 성공한다.
5. 생성된 public `/healthz`가 HTTP 성공과 `UP`을 반환한다.
6. 같은 state로 destroy한다.
7. state가 비고 active billable resource가 없으며 retained disk는 manifest와 일치한다.

2026-09-08 재검사 결과는 다음과 같다.

- runtime binding 결과: port `8000`, health path `/healthz`
- 기존 부분 `terraform.tfvars`: resource prefix와 AMI는 있고 Secret reference만 빈 상태 확인
- 최신 script 재생성: 기존 값 즉시 반환 제거, 빈 Secret 재입력 및 AWS `describe-secret`
  사전검사 경로 확인
- Docker image 재빌드 성공
- loopback 임시 container의 `/healthz`: `{"status":"UP","groups":["liveness","readiness"]}`
- 검사용 container와 image tag 삭제 완료

유효한 사용자 Secret ARN이 제공되지 않았으므로 실제 AWS `plan`·`apply`는 실행하지 않았다.
위 3번은 공식 AWS mock plan으로 검증했지만 이 앱 계정의 실제 Secret metadata 검증은 남아 있다.
따라서 이 앱의 cloud 배포 성공이라고 표현하지 않는다.

## 9. 구현 결과

- DPL-01: GCP no-ingress filter는 reference-only anchor로 바꾸고 AWS·GCP 내부 rule은 connection
  target port만 허용한다.
- DPL-02: 공식 module의 port 기본값과 health path를 생성 원천에서 결정하며 중복 validator는
  두지 않는다.
- DPL-03: 6개 module의 worker를 고정 digest prebuilt image로 바꿨다.
- DPL-04: partial tfvars를 병합하고 digest에 provider·region·cloud identity·source hash·workload를
  기록하며 Registry 존재까지 확인한다.
- DPL-05: 세 CSP의 canonical Secret reference와 metadata를 billable workload 전에 확인하고
  실제 값은 tfvars·state·plan·log에 저장하지 않는다.
- DPL-06: 모든 compute별 cloud-init·bootstrap과 inline Compose를 검사하고 `.env`는 Compose
  `env_file`로만 전달한다. gate가 `PASS`가 아니면 publish하지 않는다.
- DPL-07: AWS ASG block device, Azure VMSS instance LUN, GCP MIG instance device에서 실제 disk
  ID를 수집하고 destroy 뒤 provider API로 존재 여부를 기록한다. GCP는 stateful disk
  `delete_rule = "NEVER"`를 사용한다.
- DPL-08: deployment script와 IaC snapshot을 한 transaction에서 publish하고 한 query로 읽는다.
  ZIP manifest에 release·job·version·file hash를 기록하며, private health와 SSH 안내도 실제
  지원 경로에 맞췄다.

## 10. 현재 지원 범위의 완료 조건

다음을 모두 만족하면 현재 EasyDep Docker-on-VM 범위의 배포 script 수정이 완료된 것으로 본다.

- 공식 45개 WorkloadGraph가 유효한 runtime 기본값을 결정적으로 생성한다.
- provider별 network rule이 public·private·internal 의도와 정확히 일치한다.
- generated image checkpoint가 immutable하고 현재 cloud target에 결합돼 있다.
- 중단 뒤 재실행이 부분 입력·Registry·image·state를 검증하고 완료된 단계만 재사용한다.
- 기존 CSP Secret reference를 안전하게 검사하고 VM container에 전달한다.
- VM이 실제 사용하는 Compose·bootstrap과 검증 대상 파일이 같다.
- 필수 검사가 PASS가 아니면 배포 가능 ZIP으로 제공하지 않는다.
- block disk의 delete·retain 결과와 cloud ID를 설명할 수 있다.
- ZIP의 deployment script와 IaC가 같은 release 세대이며 file hash를 확인할 수 있다.
- public health는 검증 결과를, private health는 검증되지 않은 상태와 확인 방법을 정확히 출력한다.
- 관련 단위 테스트, 45개 OpenTofu validate, 대표 plan, 실제 앱 Docker health가 통과한다.

## 11. 작업 시 주의 사항

- 기존 실배포가 통과한 provider topology를 다시 설계하지 않는다.
- 잘못된 입력은 silent fallback보다 명시적 blocking issue로 처리한다.
- Secret 값은 test fixture에도 실제 credential을 넣지 않는다.
- 사용자 작업 중 변경과 배포 수정 파일이 겹치면 기존 변경을 보존한다.
- 외부 CSP·Registry 명령은 처음부터 network 권한이 있는 실행으로 수행한다.
- 실패한 검증은 전체 과정을 반복하지 않고 해당 provider·case부터 재개한다.
- Windows에서 JSON·PowerShell 산출물을 검사할 때 UTF-8을 명시한다.
- 실제 resource를 만든 검증은 같은 state로 정리하고 provider별 잔여 조회까지 수행한다.
