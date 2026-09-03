# 3사 클라우드 실배포 검증 기록

> 검증일: 2026-09-03
> 대상: Docker-on-VM 배포 템플릿과 EasyDep이 생성한 OpenTofu·bootstrap 패키지

이 문서는 AWS, Azure, GCP에 테스트 리소스를 실제로 만들고 애플리케이션 응답을 확인한
결과를 기록한다. 정적 검사만 통과한 경우와 실제 배포까지 통과한 경우를 구분하며, 실행 중
발견한 오류와 수정 내용도 함께 남긴다.

## 1. 검증 방법

실배포는 [`scripts/run_live_deployment_smoke.py`](../scripts/run_live_deployment_smoke.py)로
실행했다. 이 스크립트는 별도의 배포 구현을 사용하지 않고 EasyDep이 사용자에게 제공하는
다음 스크립트를 그대로 호출한다.

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
| 관리 그룹의 replica별 영속 디스크 (`per-replica-storage`) | 통과 | 통과 | 통과 | 공개 VM, 내부 Load Balancer, 상태 서비스 복제본 2개, replica별 디스크 마운트와 연쇄 헬스 체크 |
| 공개 주소가 없는 단일 VM (`private-single`) | 통과 | `separated-two`의 사설 VM으로 경로 확인 | `separated-two`의 사설 VM으로 경로 확인 | NAT를 통한 이미지 pull과 bootstrap; AWS는 직렬 콘솔로 완료 확인 |
| 외부 비밀값 전달 (`secret-binding`) | 통과 | 통과 | 통과 | Secret Manager·Key Vault, Secret별 최소 읽기 권한, VM 역할·서비스 계정, 실행 시 환경 변수 전달, 앱 내부 값 비교 |

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

수정된 주요 코드는
[`app/implementation/delivery/iac_renderer.py`](../app/implementation/delivery/iac_renderer.py)와
[`app/implementation/delivery/container.py`](../app/implementation/delivery/container.py)에 있다.

## 5. 일시적인 외부 환경 오류

다음 오류는 같은 체크포인트 또는 상태에서 재개했으며 템플릿 오류로 처리하지 않았다.

- AWS STS 주소를 찾지 못한 DNS 오류가 한 번 발생했다. 로그인 확인 뒤 같은
  `private-single` 실행을 재시도해 통과했다.
- GCP zone-spread 정리 중 `compute.googleapis.com` DNS 조회가 한 번 실패했다. 이미 삭제가
  시작된 OpenTofu 상태에서 `destroy`만 재개했고 상태가 빈 것을 확인했다.
- 실제 생성된 수강신청 애플리케이션의 frontend 이미지 build에서 npm Registry 연결이
  `ECONNRESET`으로 끊겼다. 이 실행은 작은 smoke 앱과 달리 외부 npm 다운로드에 의존한다.
  따라서 현재 기록은 배포 템플릿의 실배포 성공을 뜻하며, 수강신청 애플리케이션 전체
  이미지의 실배포 완료를 뜻하지는 않는다.
- GCP Secret Manager API가 처음에는 비활성 상태여서 Secret 생성 전에 멈췄다. 테스트
  프로젝트에서 해당 API를 활성화한 뒤 같은 `secret-binding` 사례를 다시 실행해 통과했다.
- Azure 구독에 `Microsoft.KeyVault` 리소스 공급자가 등록되지 않아 첫 vault 생성 전에
  멈췄다. 해당 공급자를 등록한 뒤 같은 `secret-binding` 사례를 다시 실행해 통과했다.

## 6. 아직 실배포하지 않은 조합

다음 항목은 현재 템플릿의 주요 경로와 겹치거나 외부 패키지 다운로드 실패로 아직 전체
실배포를 끝내지 못했다.

- 여러 VM에 각각 영속 디스크가 있는 배치
- 실제 수강신청 애플리케이션 이미지의 npm 의존성 다운로드 이후 전체 배포

이 항목을 검증할 때는 새 배포 체계를 만들지 않고 같은 runner에 작은 case만 추가한다.

## 7. 코드 검증 결과

실배포 수정 뒤 다음 검사를 통과했다.

```text
python -X utf8 -m pytest tests/test_deployment_templates.py -q
python -X utf8 -m ruff check app/implementation/delivery/container.py app/implementation/delivery/iac_renderer.py tests/test_deployment_templates.py scripts/run_live_deployment_smoke.py
python -X utf8 -m mypy app/implementation/delivery/container.py app/implementation/delivery/iac_renderer.py scripts/run_live_deployment_smoke.py
python -X utf8 -m compileall -q app/implementation/delivery scripts/run_live_deployment_smoke.py
git diff --check
```

최종 조회에서 AWS의 instance·VPC·Auto Scaling Group·ECR repository, Azure의
`easydep-live-*` Resource Group, GCP의 instance·관리형 instance group·network·Artifact
Registry repository가 남지 않았다. 이번 검증에서 만든 AWS·GCP Secret, Azure Key Vault와
soft-deleted vault도 남지 않았다. 같은 접두사의 로컬 Docker 이미지와 시스템 임시
디렉터리도 남지 않았다.
