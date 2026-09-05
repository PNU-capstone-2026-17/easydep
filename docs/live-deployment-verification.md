# 3사 클라우드 실배포 검증 기록

> 검증일: 2026-09-03
> 올인원 배포 도구 추가 검증일: 2026-09-05
> 대상: Docker-on-VM 배포 템플릿과 EasyDep이 생성한 OpenTofu·bootstrap 패키지

배포 입력, 템플릿 선택 규칙, 다이어그램과 IaC 생성 과정까지 함께 보려면
[`deployment-template-system.md`](deployment-template-system.md)를 먼저 읽는다. 이 문서는
실배포 실행별 세부 기록을 보존하는 용도다.

이 문서는 AWS, Azure, GCP에 테스트 리소스를 실제로 만들고 애플리케이션 응답을 확인한
결과를 기록한다. 정적 검사만 통과한 경우와 실제 배포까지 통과한 경우를 구분하며, 실행 중
발견한 오류와 수정 내용도 함께 남긴다.

## 1. 검증 방법

실배포는 [`scripts/run_live_deployment_smoke.py`](../scripts/run_live_deployment_smoke.py)로
실행했다. 이 검증 당시에는 사용자 배포 단계가 아래처럼 여러 파일로 제공되었다.
현재 사용자 패키지는 같은 작업을 `deployment/easydep.ps1` 내부 함수로 옮기고,
`배포/재개`와 `삭제`만 보이는 메뉴로 단순화했다.

```text
doctor.ps1
  → prepare-images.ps1
  → plan.ps1
  → deploy.ps1
  → verify.ps1
  → destroy.ps1
```

테스트 애플리케이션은 외부 Python 패키지가 필요 없는 작은 HTTP 서버다. VM 안에서
Docker 컨테이너가 실행되고 `/actuator/health` 또는 `/healthz`가 HTTP 200을 반환해야
성공으로 판정한다. 분리 배치에서는 공개 `web` workload의 헬스 체크가
`STATE_SERVICE_URL`로 사설 `state` workload의 `/healthz`를 호출한다. 따라서 공개
응답 하나로 두 VM의 컨테이너 기동과 사설 통신을 함께 확인한다. 영속 디스크가 있는
경우에는 컨테이너 시작 전에 디스크 검색, 포맷, 마운트가 모두 성공해야 한다.

AWS는 공개 주소가 없는 VM도 확인할 수 있도록 모든 VM의 직렬 콘솔에서
`EASYDEP_BOOTSTRAP_OK` 표식을 확인한다. 이 방식은 공개 헬스 체크가 시간 초과할 때까지
기다리지 않고 cloud-init 실패를 바로 찾는 데도 사용한다.

각 실행은 `easydep-live-<provider>-<고유값>` 이름을 사용한다. 종료할 때 같은 OpenTofu
상태로 클라우드 리소스를 삭제하고, 해당 실행이 만든 로컬 Docker 이미지와 시스템 임시
디렉터리도 삭제한다.

## 2. 실제로 검증한 템플릿

| 배치 형태 | AWS | Azure | GCP | 확인한 내용 |
|---|---|---|---|---|
| 공개 단일 VM (`public-single`) | 통과 | 통과 | 통과 | Registry, 이미지 push/pull, VM, 공개 IP, 방화벽, HTTP 헬스 체크 |
| 여러 Zone의 관리형 VM 그룹 (`zone-spread`) | 통과 | 통과 | 통과 | VM 그룹, 2개 Zone, Load Balancer, 헬스 체크 |
| 단일 Zone의 관리형 VM 그룹 (`single-zone-managed`) | 기존 관리 그룹 경로로 확인 | 기존 관리 그룹 경로로 확인 | 통과 | 복제본 2개, 한 Zone을 사용하는 regional MIG, Load Balancer, 헬스 체크 |
| 같은 VM의 두 workload (`colocated-two`) | 통과 | 아래 영속 배치로 확인 | 아래 영속 배치로 확인 | 같은 Compose 네트워크의 컨테이너 DNS 통신, host port 중복 방지 |
| 서로 다른 VM의 두 workload (`separated-two`) | 통과 | 통과 | 통과 | 공개 VM에서 사설 VM으로 전달하는 주소·포트, 사설 egress, Registry 권한 |
| 같은 VM의 두 workload와 영속 디스크 (`persistent-colocated`) | 통과 | 통과 | 통과 | 디스크 연결·마운트, 컨테이너 volume, 애플리케이션 기동 |
| 서로 다른 VM의 두 workload와 사설 VM 영속 디스크 (`persistent-separated`) | 통과 | 통과 | 통과 | NAT 이후 사설 VM 기동, 사설 디스크 연결·마운트, 공개 앱에서 사설 앱까지 연쇄 헬스 체크 |
| 서로 다른 VM의 두 workload와 VM별 영속 디스크 (`multi-persistent-separated`) | 통과 | 통과 | 통과 | 두 VM의 독립 디스크 연결·마운트, 두 컨테이너의 파일 쓰기, 공개 앱에서 사설 앱까지 연쇄 헬스 체크 |
| 관리 그룹의 replica별 영속 디스크 (`per-replica-storage`) | 통과 | 통과 | 통과 | 공개 VM, 내부 Load Balancer, 상태 서비스 복제본 2개, replica별 디스크 마운트와 연쇄 헬스 체크 |
| 공개 주소가 없는 단일 VM (`private-single`) | 통과 | `separated-two`의 사설 VM으로 경로 확인 | `separated-two`의 사설 VM으로 경로 확인 | NAT를 통한 이미지 pull과 bootstrap; AWS는 직렬 콘솔로 완료 확인 |
| 외부 비밀값 전달 (`secret-binding`) | 통과 | 통과 | 통과 | Secret Manager·Key Vault, Secret별 최소 읽기 권한, VM 역할·서비스 계정, 실행 시 환경 변수 전달, 앱 내부 값 비교 |
| 실제 생성된 수강신청 앱 (`course-registration-app`) | 통과 | AWS용 설계라 반복하지 않음 | AWS용 설계라 반복하지 않음 | React/Vite 빌드, Spring Boot JAR, Flyway, ECR, VM, EBS, `/healthz`, 자동 정리 |

Azure와 GCP의 `separated-two`에는 공개 주소가 없는 두 번째 VM이 포함된다. 따라서 별도의
`private-single`을 반복하지 않고도 두 공급자의 사설 subnet, NAT, Registry pull,
bootstrap 경로를 실제로 확인했다.

모든 표의 `통과`는 다음 조건을 뜻한다.

- OpenTofu `init`, `validate`, `plan`, `apply`가 성공했다.
- 컨테이너 이미지를 해당 CSP Registry에 올리고 VM에서 내려받았다.
- 공개 진입점이 있는 템플릿은 HTTP 헬스 체크가 성공했다.
- 실행이 만든 클라우드 리소스, 로컬 이미지, 임시 디렉터리가 정리됐다.

## 3. 실행 중 관찰한 내용

### 생성 순서

- AWS 사설 VM과 Auto Scaling Group은 private route가 준비된 다음 생성돼야 한다. VPC나
  subnet 참조만으로는 OpenTofu가 이 순서를 알 수 없다.
- GCP에서 공개 IP가 있는 VM은 Cloud NAT와 병렬로 만들어도 된다. 반면 사설 VM과 관리형
  VM 그룹은 Cloud NAT가 완료된 뒤 만들어져야 초기 이미지 pull이 안정적이다.
- GCP의 `zones=1` 관리 그룹도 현재 템플릿은 zonal MIG가 아니라 Zone 목록이 하나인
  regional MIG로 만든다. `single-zone-managed` 실배포에서 복제본 2개와 regional Load
  Balancer가 정상 동작했고 14개 리소스를 정리했다.
- Azure의 분리 배치는 공개 VM 한 대와 NAT를 사용하는 사설 VM 한 대를 만들었고, 두 VM의
  Registry pull과 공개 VM의 헬스 체크가 모두 성공했다.
- Azure 사설 VM 생성이 NAT subnet 연결과 동시에 시작될 수 있는 로그를 확인했다. VM
  생성 시간이 더 길어 당시 배포는 성공했지만, 초기 bootstrap이 네트워크보다 먼저
  시작되는 경우를 막기 위해 사설 VM과 VM Scale Set이 NAT subnet 연결을 기다리게 했다.
- 최초 Azure 분리 영속 배포는 공개 앱만 검사해 사설 앱의 상태를 놓칠 수 있었다. smoke
  앱의 공개 헬스 체크가 사설 앱을 다시 호출하도록 바꾼 뒤 Azure와 GCP에서 재검증했다.
  따라서 현재 결과는 단순히 공개 포트가 열렸다는 뜻이 아니라 두 앱의 사설 통신까지
  성공했다는 뜻이다.
- AWS `per-replica-storage`에서는 공개 VM과 Auto Scaling Group 복제본 2개가 모두
  `EASYDEP_BOOTSTRAP_OK`를 출력했다. 관리형 복제본은 각각 약 50초와 59초에 replica별
  EBS 준비와 컨테이너 시작을 마쳤다. 첫 실행에서는 내부 NLB의 두 대상이 보안 그룹 규칙
  누락으로 `Target.FailedHealthChecks` 상태였지만, 상태 검사 포트를 VPC 내부에 허용한 뒤
  다시 실행해 공개 앱에서 NLB와 상태 서비스까지 이어지는 헬스 체크가 통과했다.
- Azure `per-replica-storage`에서는 NAT 연결이 끝난 뒤 VM Scale Set 복제본 2개가
  만들어졌다. 각 복제본의 관리 디스크 준비와 상태 컨테이너 실행, 내부 Load Balancer
  probe, 공개 앱의 연쇄 헬스 체크가 모두 통과했고 26개 리소스를 삭제했다.
- 이로써 AWS Auto Scaling Group, Azure VM Scale Set, GCP Managed Instance Group의
  replica별 영속 디스크 조합을 모두 실제 배포로 확인했다.
- `multi-persistent-separated`는 공개 `web` VM과 사설 `state` VM에 각각 별도 영속
  디스크를 연결했다. 두 컨테이너가 자신의 마운트 경로에 파일을 쓸 수 있어야 하고 공개
  앱에서 사설 앱까지 이어지는 상태 검사도 성공해야 통과하도록 했다. AWS 실행
  `easydep-live-aws-7ca2227e`는 32개, Azure 실행
  `easydep-live-azure-603c2d3f`는 28개, GCP 실행
  `easydep-live-gcp-a103bc0d`는 22개 리소스를 각각 정리했다.
- 16개 요구사항(RR1~RR16)으로 생성한 실제 수강신청 앱은 React/TypeScript/Vite
  frontend와 Java 21/Spring Boot backend를 한 이미지로 빌드했다. 로컬 사전 검사에서
  Flyway migration과 `/healthz` 응답을 확인했고, AWS 실행
  `easydep-live-aws-83fe5673`에서도 EBS를 마운트한 뒤 같은 상태 검사가 통과했다.
  OpenTofu 리소스 15개, ECR 이미지, 로컬 이미지, 임시 디렉터리를 모두 정리했다.
- 이 앱의 원래 배포 설계는 database runtime engine이 정해지지 않아 `needsInput` 상태였다.
  실배포 결과를 반영한 현재 기본 템플릿은 단일 VM·단일 복제본을 선택한 경우 파일 기반
  H2와 영속 디스크를 함께 계획하고, 같은 datasource 값을 구현물과 cloud-init에 전달한다.
  여러 복제본이나 별도 DB workload는 이 기본값을 사용하지 않고 명시적인 DB 선택을
  요구한다.
- AWS `secret-binding`에서는 실행 전 임시 Secret을 만들고, 해당 ARN만 읽을 수 있는 VM
  역할과 정책을 배포했다. bootstrap이 Secret Manager에서 값을 읽어 컨테이너 환경 변수로
  전달했으며, 테스트 앱이 전달받은 값과 실행 전 기대값이 정확히 같을 때만 HTTP 200을
  반환하도록 검사했다. 검증 뒤 OpenTofu 리소스 14개, Registry 이미지, 임시 Secret을
  모두 삭제했다.
- GCP `secret-binding`에서는 Secret Manager의 Secret 이름을 입력으로 주고, 생성된 VM
  서비스 계정에 해당 Secret만 읽는 역할을 연결했다. VM이 metadata token으로 Secret 값을
  읽고 컨테이너에 전달한 뒤 앱 내부 값 비교와 HTTP 헬스 체크가 통과했다. OpenTofu
  리소스 9개, Registry 이미지, 임시 Secret을 모두 삭제했다.
- Azure `secret-binding`에서는 Key Vault Secret의 Azure 리소스 ID를 입력으로 주고,
  사용자 할당 ID에 해당 Secret만 읽는 역할을 연결했다. bootstrap은 리소스 ID에서 vault
  이름과 Secret 이름을 꺼내 값을 읽었고, 앱 내부 값 비교와 HTTP 헬스 체크가 통과했다.
  OpenTofu 리소스 12개, Registry 이미지, 검증용 Key Vault Resource Group과 soft-deleted
  vault까지 모두 삭제했다.

### 실행 시간에 영향을 준 부분

- VM과 Registry 생성·삭제는 공급자 API 응답을 기다리므로 로컬 정적 검사보다 오래 걸린다.
- 같은 작은 이미지라도 Registry push가 수십 초 동안 같은 layer 상태를 반복 출력하거나,
  업로드 완료 뒤 Docker CLI가 늦게 종료되는 경우가 있었다. 서버의 tag와 digest는 정상
  등록됐으며 이후 배포도 성공했다.
- 실제 앱의 첫 Docker 빌드는 배포 디렉터리까지 문맥에 포함해 약 720MB를 전송했다.
  루트 `deployment`를 제외한 재실행에서는 22.07KB만 전송했고 frontend 빌드 단계도
  Docker cache를 그대로 사용했다.
- GCP 네트워크와 Azure Resource Group 삭제는 하위 리소스가 사라진 뒤에도 수십 초 더
  걸렸다. OpenTofu 상태가 빌 때까지 기다려 정리를 완료했다.

## 4. 발견한 오류와 수정 사항

| 발견한 오류 | 원인 | 수정 사항 | 확인 결과 |
|---|---|---|---|
| AWS 사설 VM이 외부 통신 경로보다 먼저 부팅될 수 있음 | subnet 참조만 있고 private route와 직접 관계가 없었음 | `aws_instance`와 `aws_autoscaling_group`이 해당 private route를 기다리도록 생성 관계 추가 | AWS zone-spread, separated-two, private-single 통과 |
| 같은 VM에 있는 두 컨테이너가 같은 host port를 요구함 | 모든 internal interface를 host에 공개함 | 같은 Compose 네트워크는 컨테이너 DNS를 사용하고, 다른 VM 또는 내부 LB가 호출하는 대상만 host port 공개 | AWS colocated-two와 3사 persistent-colocated 통과 |
| Azure/GCP의 일부 port 속성 타입 불일치 | OpenTofu 표현식을 문자열 안에 넣어 문자열이 중첩됨 | Azure는 `tostring(...)`, GCP는 문자열 목록 안의 `tostring(...)` 표현식으로 생성 | 3사 관련 plan과 실배포 통과 |
| AWS EBS가 연결됐지만 guest에서 장치를 찾지 못함 | Nitro VM의 실제 NVMe 이름이 요청한 `/dev/sdX`와 다를 수 있음 | `/dev/disk/by-id`, sysfs serial, 기존 xvd/sd 이름 순서로 찾고, 마지막에는 root가 아닌 유일한 미사용 디스크만 안전하게 선택 | AWS persistent-colocated 통과 |
| AWS ASG replica별 EBS를 guest에서 찾지 못함 | Launch Template이 각 복제본에서 만드는 volume ID는 OpenTofu 계획 시점에 알 수 없고, Nitro VM에서는 요청한 `/dev/sdX`가 NVMe 이름으로 보임 | 단독 EBS의 ID 기반 탐색과 별개로, root 장치의 실제 이름을 확인한 뒤 NVMe·xvd·sd 후보에서 root와 이미 사용한 장치를 제외한 유일한 디스크를 선택 | 관리형 복제본 2개 모두 EBS 준비, 컨테이너 시작, `EASYDEP_BOOTSTRAP_OK` 확인 |
| AWS 내부 NLB의 대상이 `unhealthy` | 상태 서비스 VM의 기본 보안 그룹에는 ingress가 없고, workload 간 보안 그룹은 공개 `web` VM에서 오는 요청만 허용함. NLB 상태 검사는 이 source security group과 일치하지 않음 | 관리형 대상의 ResourcePlan에 상태 검사 포트를 기록하고, AWS 대상 보안 그룹이 그 포트를 VPC CIDR에만 허용하도록 생성 | AWS per-replica-storage 재실행에서 복제본 2개와 공개 연쇄 헬스 체크 통과. 리소스 32개 삭제 |
| GCP instance template 이름이 37자를 넘음 | `name_prefix`에 전체 EasyDep 이름을 그대로 사용함 | `substr(..., 0, 37)`로 provider 제한 적용 | GCP zone-spread plan과 실배포 통과 |
| GCP 내부 헬스 체크 이름이 63자를 넘음 | 긴 내부 Load Balancer 역할 이름과 배포 접두사를 그대로 이어 붙임 | 짧은 이름은 유지하고, 긴 GCP 리소스 이름만 앞부분과 node ID 해시를 조합해 공급자 제한 안으로 줄이는 공통 표현 적용 | 45개 정적 템플릿 파싱과 GCP per-replica-storage 실배포 통과 |
| GCP VM 그룹이 Cloud NAT와 동시에 생성됨 | VM 그룹이 NAT 리소스를 직접 참조하지 않음 | 사설 단일 VM과 관리형 VM 그룹에 Cloud NAT `depends_on` 추가 | GCP separated-two에서 사설 VM이 NAT 완료 뒤 생성됨을 확인 |
| Azure 사설 VM이 NAT subnet 연결과 동시에 생성될 수 있음 | VM이 NIC만 참조하고 NAT 연결을 직접 참조하지 않음 | 사설 VM과 VM Scale Set이 해당 `azurerm_subnet_nat_gateway_association`을 기다리도록 생성 관계 추가 | Azure persistent-separated에서 NAT 연결 완료 뒤 사설 VM 생성과 연쇄 헬스 체크 통과 |
| 공개 URL 검사만으로 사설 workload 실패를 놓칠 수 있음 | smoke 앱이 자신만 정상인지 응답했음 | 공개 앱이 `STATE_SERVICE_URL`의 사설 앱 헬스 체크까지 성공해야 HTTP 200을 반환하도록 변경 | Azure와 GCP persistent-separated 통과 |
| bootstrap 실패를 공개 헬스 체크 시간 초과 뒤에만 알 수 있음 | 공개 URL만 관찰함 | 컨테이너 실행 상태를 검사한 뒤 AWS 직렬 콘솔에 `EASYDEP_BOOTSTRAP_OK` 출력, runner가 모든 VM 표식 확인 | 공개 주소 없는 AWS VM까지 확인 가능 |
| 생성된 frontend 이미지에 필요한 도구가 없을 수 있음 | `npm ci` 성공만 확인함 | 이미지 build 단계에서 `tsc`와 `vite` 실행 파일 존재 여부를 즉시 검사 | Dockerfile 생성 테스트와 정적 검사 통과 |
| AWS Secret 읽기 정책의 `Resource`가 빈 문자열이 됨 | 실배포 runner가 Secret ARN을 `TF_VAR_*` 환경 변수로 넣었지만, 복사한 `terraform.tfvars`의 빈 예제 값이 더 높은 우선순위로 환경 변수를 가림 | smoke용 로컬 `terraform.tfvars`에 실행 중 만든 Secret ARN을 직접 기록 | 첫 실패의 AWS 리소스 13개와 Secret을 정리한 뒤 재실행. 앱 내부 값 비교와 HTTP 헬스 체크 통과, 리소스 14개와 Secret 정리 |
| Azure의 Secret 참조 하나를 권한 범위와 실행 시 조회에 함께 사용할 수 없음 | 역할의 `scope`는 Azure 리소스 ID가 필요하지만 `az keyvault secret show --id`는 데이터 영역 URL을 요구함 | 배포 입력은 권한 범위로 바로 쓸 수 있는 Secret 리소스 ID로 통일하고, bootstrap이 그 ID에서 vault 이름과 Secret 이름을 추출해 조회 | Azure plan, Secret 단위 역할 생성, VM의 실제 값 조회와 앱 내부 비교 통과 |
| 실제 수강신청 앱의 검증 URL이 `/actuator/health`로 생성됨 | 배포 입력에 앱의 상태 검사 경로가 없어서 Spring Boot 기본값을 사용함. 실제 앱은 actuator를 `/healthz`로 옮겨 둠 | 실제 앱 workload interface에 `healthPath: /healthz`를 명시 | AWS VM bootstrap 뒤 생성된 `/healthz` URL의 HTTP 검증 통과 |
| 실제 앱 Docker 빌드 문맥이 약 720MB까지 커짐 | 앱 안에 생성한 `deployment/tofu/.terraform` provider 파일을 `COPY . .` 빌드 문맥이 다시 포함함 | 생성하는 `.dockerignore`와 기존 앱을 복사하는 실배포 runner에서 루트 `/deployment` 제외 | 재실행 빌드 문맥 22.07KB, frontend cache 재사용, 전체 이미지 빌드·push 통과 |

수정된 주요 코드는
[`app/implementation/delivery/iac_renderer.py`](../app/implementation/delivery/iac_renderer.py)와
[`app/implementation/delivery/container.py`](../app/implementation/delivery/container.py),
[`app/implementation/delivery/package.py`](../app/implementation/delivery/package.py)에 있다.

## 5. 일시적인 외부 환경 오류

다음 오류는 같은 체크포인트 또는 상태에서 재개했으며 템플릿 오류로 처리하지 않았다.

- AWS STS 주소를 찾지 못한 DNS 오류가 한 번 발생했다. 로그인 확인 뒤 같은
  `private-single` 실행을 재시도해 통과했다.
- GCP zone-spread 정리 중 `compute.googleapis.com` DNS 조회가 한 번 실패했다. 이미 삭제가
  시작된 OpenTofu 상태에서 `destroy`만 재개했고 상태가 빈 것을 확인했다.
- 실제 생성된 수강신청 애플리케이션의 첫 frontend 이미지 build에서 npm Registry 연결이
  `ECONNRESET`으로 한 번 끊겼다. 로컬 사전 검사를 재개하자 npm·TypeScript·Vite·Gradle
  빌드가 모두 통과했고, 이후 AWS 전체 실배포도 완료했다.
- GCP Secret Manager API가 처음에는 비활성 상태여서 Secret 생성 전에 멈췄다. 테스트
  프로젝트에서 해당 API를 활성화한 뒤 같은 `secret-binding` 사례를 다시 실행해 통과했다.
- Azure 구독에 `Microsoft.KeyVault` 리소스 공급자가 등록되지 않아 첫 vault 생성 전에
  멈췄다. 해당 공급자를 등록한 뒤 같은 `secret-binding` 사례를 다시 실행해 통과했다.

## 6. 남은 실배포 범위

계획했던 고유한 템플릿 조합과 실제 수강신청 앱의 AWS 배포는 모두 확인했다. 다음 항목은
이미 검증한 경로를 공급자나 이름만 바꿔 반복하는 경우이므로 이번 검증에서는 실행하지
않았다.

- AWS용으로 설계된 수강신청 앱을 Azure와 GCP의 동일한 단일 VM 템플릿에 다시 배포하는 것
- Azure와 GCP의 `private-single`을 별도로 반복하는 것. 두 공급자는
  `separated-two`에서 같은 사설 VM·NAT·Registry pull 경로를 이미 확인했다.
- 별도 MySQL을 포함한 운영용 수강신청 앱 구성. 이는 원래 배포 설계에서 database engine을
  선택한 뒤 검증할 별도 사례다.

## 7. 코드 검증 결과

실배포 수정 뒤 다음 검사를 통과했다.

```text
python -X utf8 -m pytest tests/test_deployment_workload_boundary.py -q
python -X utf8 -m pytest tests/test_deployment_templates.py -q
python -X utf8 -m ruff check app/implementation/delivery/container.py app/implementation/delivery/iac_renderer.py tests/test_deployment_templates.py scripts/run_live_deployment_smoke.py
python -X utf8 -m mypy app/implementation/delivery/container.py app/implementation/delivery/iac_renderer.py scripts/run_live_deployment_smoke.py
python -X utf8 -m compileall -q app/implementation/delivery scripts/run_live_deployment_smoke.py
git diff --check
```

저장된 16개 요구사항 수강신청 앱에도 현재 템플릿을 다시 적용했다. 요구사항·설계 LLM을
재호출하지 않고 기존 typed 설계와 생성 소스만 사용했으며, bundle은 `completed`, runtime
결합은 `bound`, AWS ResourcePlan은 issue 0건이었다. 실제 소스에서 8000 포트와 `/healthz`,
datasource 환경 변수 3개와 보안 환경 변수 2개, `/var/lib/easydep/data` mount 사용을
확인했다. EBS disk와 attachment node가 각각 생성됐고, 보안 password는
`secret-reference-application-security-password` 배포 입력으로 만들어졌다.

최종 조회에서 AWS의 instance·VPC·Auto Scaling Group·ECR repository, Azure의
`easydep-live-*` Resource Group, GCP의 instance·관리형 instance group·network·Artifact
Registry repository가 남지 않았다. 이번 검증에서 만든 AWS·GCP Secret, Azure Key Vault와
soft-deleted vault도 남지 않았다. 같은 접두사의 로컬 Docker 이미지와 시스템 임시
디렉터리도 남지 않았다.

## 8. 올인원 배포 도구 실배포 재검증

2026-09-05에 Azure `public-single` 사례를 새 `deployment/easydep.ps1` 메뉴로 다시
검증했다. 실행 접두사는 `easydep-live-azure-38a66cbf`였다. 사용자가 보는 `배포/재개`
항목 하나가 OpenTofu 초기화, Container Registry 생성, Docker image build·push, plan,
apply와 HTTP 상태 확인을 순서대로 수행했다. VM의
`http://20.194.21.16:8000/actuator/health`가 응답하여 배포 완료로 판정했다.

검증 뒤 같은 OpenTofu 상태로 11개 리소스를 삭제했다. Azure에서
`easydep-live-azure-38a66cbf-rg` Resource Group이 존재하지 않는 것을 다시 조회했고,
같은 접두사의 로컬 Docker image와 시스템 임시 디렉터리도 남지 않았음을 확인했다.
따라서 여러 배포 스크립트를 하나로 합친 변경이 실제 생성·배포·상태 확인·정리 경로를
누락하지 않았음을 확인했다.

## 9. 최신 API 산출물의 AWS 실배포 재검증

2026-09-05에는 EasyDep API가 최신 코드로 다시 만든 산출물을 내려받아 AWS에 배포했다.
내려받은 파일을 사람이 고치지 않았으며, 산출물에 포함된
`deployment/easydep.ps1` 메뉴만 사용했다. 따라서 이 검증은 저장소의 테스트용 실행기가
아니라 사용자가 실제로 받는 파일과 같은 경로를 확인한 것이다.

검증 환경과 결과는 다음과 같다.

| 항목 | 확인 내용 |
|---|---|
| 클라우드와 리전 | AWS `af-south-1` |
| 인증 주체 | 루트 계정이 아닌 IAM 사용자 `easydep-deployer` |
| 실행 접두사 | `easydep-live-0905-e901` |
| 사용한 산출물 | API에서 내려받은 `DEPLOYMENT_FILE v7`, `IAC_CODE v6` |
| 실행 방법 | 올인원 메뉴의 `배포/재개` 실행 |
| 애플리케이션 확인 | 공개 `/healthz` 요청이 HTTP 200 반환 |
| VM 초기 설정 확인 | AWS 직렬 콘솔에서 `EASYDEP_BOOTSTRAP_OK` 확인 |
| 생성 자원 | OpenTofu 관리 자원 15개 |
| 정리 결과 | 관리 자원 14개 삭제 후 보존 EBS 1개 별도 삭제, ECR·로컬 이미지·임시 디렉터리 잔여 없음 |

### 9.1 이번 실행에서 발견하고 고친 문제

하나의 실패가 발생할 때마다 처음부터 다시 생성하지 않았다. 같은 API 산출물과 OpenTofu
상태를 사용해 실패한 배포 작업부터 재개했다. 수정 뒤에는 API에서 산출물을 다시 생성하고
내려받아, 사람이 내려받은 파일을 직접 고쳐서 우연히 통과하는 일을 막았다.

| 증상 | 원인 | 수정 내용 | 확인 결과 |
|---|---|---|---|
| Windows에서 ECR 로그인 뒤 Docker push를 시작하지 못함 | 긴 ECR 비밀번호를 PowerShell 파이프로 `docker login --password-stdin`에 넘기는 과정이 실행 환경에 따라 불안정했음 | AWS CLI로 받은 비밀번호를 Docker 표준 형식의 임시 `config.json`에 기록하고, build·push 동안만 `DOCKER_CONFIG`로 사용한 뒤 즉시 삭제 | 같은 올인원 도구에서 image build와 ECR push 성공. 임시 인증 디렉터리 잔여 없음 |
| 환경 변수가 하나도 없는 앱에서 cloud-init이 시작되지 않음 | Base64 환경 파일 값이 빈 문자열이면 YAML의 `content:`가 값 없는 항목으로 생성되어 cloud-init이 파일 내용으로 받아들이지 못함 | 빈 값도 문자열로 해석되도록 `content: "${runtime_env_b64}"` 형태로 생성 | `cloud-init schema` 검사와 실제 VM bootstrap 통과 |
| Amazon Linux 2023에서 Docker Compose 설치가 실패함 | 해당 배포판 저장소에는 요청한 Compose 패키지가 없었고, 한 번의 DNF 명령에 묶인 다른 필수 패키지 설치까지 함께 취소됨 | Docker와 기본 도구를 먼저 설치하고 Compose는 별도로 확인. 패키지가 없을 때는 공식 바이너리를 받아 SHA-256 checksum을 확인한 뒤 설치 | 실제 VM에서 Compose 실행과 애플리케이션 기동 성공 |
| Amazon Linux 2023의 DNF 설치가 `curl` 충돌로 중단됨 | 기본 설치된 `curl-minimal`과 전체 `curl` 패키지를 동시에 설치할 수 없음 | DNF 목록에서 `curl`을 제외하고 이미 제공되는 명령을 사용 | cloud-init 완료 및 `/healthz` HTTP 200 확인 |
| Registry만 생성된 실패 실행을 삭제할 때 image digest를 다시 요구함 | 정상 배포에 필요한 digest 파일이 image push 전에는 아직 없지만, 삭제용 OpenTofu 변수 검사도 같은 값을 요구했음 | 삭제할 때만 형식이 올바른 임시 digest를 전달. 실제 배포와 image 선택에는 사용하지 않음 | image push 전 실패한 실행도 추가 입력 없이 삭제 가능 |
| 배포 파일만 갱신했는데 Gradle과 최종 JAR가 매번 다시 빌드됨 | 다운로드 ZIP의 루트 `manifest.json`에 배포 산출물 버전이 기록되지만 `.dockerignore`가 이 파일을 제외하지 않았음. 애플리케이션 소스가 그대로여도 `COPY . .`의 입력이 달라짐 | 생성하는 `.dockerignore`에 `/manifest.json` 추가 | 반복 실패 세 건이 만든 비공유 Gradle·JAR cache 약 1.10GB만 삭제하고 최신 성공 cache는 유지 |

### 9.2 이 결과가 보장하는 범위

이번 결과로 다음 경로는 최신 생성 코드에서 실제로 동작한다고 볼 수 있다.

- EasyDep API가 배포 파일과 OpenTofu 파일을 다시 생성한다.
- 사용자가 API 산출물을 내려받아 올인원 PowerShell 메뉴를 실행한다.
- AWS Registry를 만들고 로컬 Docker image를 올린다.
- Amazon Linux 2023 VM이 image를 내려받아 Compose로 실행한다.
- cloud-init, 공개 상태 확인 URL, 애플리케이션 컨테이너가 차례로 동작한다.
- 같은 로컬 OpenTofu 상태를 사용해 관리 자원을 삭제한다.
- 보존 대상으로 상태에서 분리한 EBS의 식별 정보를 사용자에게 남긴다.

반대로 이 한 번의 성공만으로 모든 클라우드, 모든 네트워크 배치, 모든 애플리케이션이
항상 성공한다고 보장할 수는 없다. 이전 절의 실배포 결과는 각 템플릿이 동작한다는 근거지만,
공통 bootstrap과 올인원 도구가 바뀌었으므로 아래 후보는 최신 산출물로 다시 확인할 가치가
있다.

## 10. 앞으로 확인할 후보

전수 실배포를 매 변경마다 반복하면 시간과 비용이 지나치게 커진다. 공통 코드가 바뀌었을
때는 먼저 정적 검사를 하고, 변경된 경로를 대표하는 소수의 실배포를 선택한다. 아래 순서는
현재 남은 위험과 확인 비용을 함께 고려한 우선순위다.

| 우선순위 | 확인 후보 | 확인하려는 내용 | 권장 방법 |
|---|---|---|---|
| 1 | Azure·GCP 공개 단일 VM | 이번에 바뀐 올인원 도구와 Ubuntu 계열 bootstrap이 두 공급자에서도 그대로 동작하는지 | 공급자별 1회 배포, HTTP 200 확인 후 즉시 삭제 |
| 2 | AWS 분리 workload | 공개 VM에서 사설 VM으로 연결되는 주소·포트, NAT, Registry pull이 최신 산출물에서도 유지되는지 | 기존 설계 체크포인트를 복사해 `separated-two` 1회 실행 |
| 3 | AWS 영속 디스크 | 마운트 경로에 쓴 데이터가 컨테이너 재시작 뒤에도 남고, 삭제 시 보존 EBS 안내가 충분한지 | 파일 기록, 컨테이너 또는 VM 재시작, 재조회 후 수동 볼륨 삭제 |
| 4 | 부분 실패 뒤 재개 | Registry 생성 뒤 push 실패, OpenTofu apply 일부 성공, 상태 확인 시간 초과 뒤 같은 실행을 안전하게 재개할 수 있는지 | 의도적으로 한 지점만 실패시킨 뒤 전체 생성 없이 해당 작업 재실행 |
| 5 | ARM64 AWS VM | Compose 공식 바이너리의 `aarch64` 선택과 checksum이 실제 ARM VM에서 맞는지 | 저비용 ARM 인스턴스로 단일 VM 1회 실행 |
| 6 | 비어 있지 않은 환경 변수와 Secret | 일반 환경 변수, 줄바꿈·특수 문자가 있는 값, Secret 참조가 cloud-init과 Compose를 거쳐 정확히 전달되는지 | 실제 비밀값 대신 검증용 임시 값을 사용하고 앱 내부 비교 |
| 7 | 별도 DB workload | 사설 주소·포트, DB 준비 순서, migration, 재연결과 디스크 보존이 함께 동작하는지 | DB가 분리된 설계 체크포인트를 한 번 만든 뒤 반복 재사용 |
| 8 | 최소 IAM 권한 | 현재 실험의 넓은 관리자 권한 대신 문서화된 최소 권한으로 생성·조회·삭제가 가능한지 | 별도 검증 사용자 또는 역할에 후보 정책을 적용해 plan과 단일 배포 실행 |
| 9 | 서로 다른 Windows 환경 | Windows PowerShell 5.1과 PowerShell 7에서 메뉴, 임시 Docker 인증, UTF-8 파일 처리가 같은지 | 같은 다운로드 패키지로 doctor와 image 준비까지만 비교 |
| 10 | 원격 상태와 동시 실행 | 여러 사용자가 같은 이름이나 같은 상태를 사용했을 때 충돌하지 않는지 | 로컬 상태 지원 범위와 원격 backend 도입 여부를 먼저 결정한 뒤 별도 검증 |

### 10.1 실배포 전에 계속 수행할 빠른 검사

- 생성한 cloud-init 파일을 `cloud-init schema`로 검사한다.
- OpenTofu `fmt`, `init -backend=false`, `validate`, `plan`을 실행한다.
- Trivy로 생성된 IaC를 검사한다.
- Dockerfile과 Compose 파일을 파싱하고 필요한 image·port·volume 값이 있는지 확인한다.
- 배포 패키지의 애플리케이션 경로와 Docker build context가 일치하는지 확인한다.
- 실패한 실행을 재개할 때 앱 ID, 실행 ID, OpenTofu 상태와 산출물 버전이 같은지 확인한다.

### 10.2 실배포 완료 조건

앞으로의 실배포 기록도 단순히 `tofu apply`가 끝난 시점을 성공으로 보지 않는다. 다음을
모두 확인해야 해당 후보를 통과로 기록한다.

1. Registry에 image가 digest와 함께 저장된다.
2. 모든 대상 VM의 bootstrap이 완료된다.
3. 공개 진입점 또는 내부 연쇄 상태 확인이 HTTP 200을 반환한다.
4. 필요한 경우 디스크 기록과 Secret 전달을 애플리케이션 안에서 확인한다.
5. 관리 자원 삭제 뒤 OpenTofu 상태가 비어 있다.
6. 상태에서 분리한 보존 자원도 식별하여 사용자가 유지하거나 직접 삭제한다.
7. 같은 접두사의 Registry, VM, network, 로컬 image와 임시 디렉터리가 남지 않는다.
