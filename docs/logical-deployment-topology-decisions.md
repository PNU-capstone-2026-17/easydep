# Docker-on-VM 배포 토폴로지와 리소스 의존성

이 문서는 EasyDep의 Docker-on-VM 배포 다이어그램과 IaC 생성에 사용하는 현재 정본이다.
논리 요구사항을 CSP 실제 리소스로 투영하며, 벤더 중립 가상 리소스를 저장하지 않는다.

## 1. 지원 범위

지원한다.

- CSP 한 곳과 Region 한 곳
- Spring Boot 애플리케이션 컨테이너
- 선택적 PostgreSQL 컨테이너
- 단일 VM 또는 CSP 관리형 VM 그룹
- VM 직접 공개 또는 L4 Load Balancer 공개
- 선택적 독립 영속 block disk
- 사용자가 EasyDep 배포 번들로 앱 이미지를 한 번 build·push하고 digest로 pull하는 배포
- HTTP 애플리케이션과 HTTP readiness endpoint

지원하지 않는다.

- Multi-Region
- HTTPS/TLS, 인증서, 도메인 검증
- managed database, shared filesystem, Kubernetes, ECS 같은 별도 실행 플랫폼
- autoscaling, SLA 또는 고가용성 결과 보장
- 여러 쓰기 replica가 하나의 block disk를 공유하는 구성

현재 공개 Load Balancer는 HTTP 내용을 해석하는 L7 제품이 아니라 TCP를 전달하는 L4 제품이다.
따라서 이 범위의 공개 endpoint는 개발·검증용 HTTP이며 production-secure endpoint가 아니다.

## 2. 입력 결정과 유효한 조합

`DeploymentTopology/v1`은 다음 세 결정을 조합한다.

| 결정 | 값 |
|---|---|
| App compute | `standaloneOne`, `managedGroupOne`, `managedGroupManySingleZone`, `managedGroupManyMultiZone` |
| PostgreSQL 배치 | `none`, `colocated`, `dedicated` |
| 공개 진입 | `direct`, `loadBalanced` |

제약은 다음과 같다.

- `direct`는 `standaloneOne`에만 허용한다.
- 관리형 그룹은 `loadBalanced`와 조합한다.
- `colocated` PostgreSQL은 `standaloneOne`에만 허용한다.
- 여러 App VM을 선택하면 App은 무상태여야 한다.
- `managedGroupManyMultiZone`은 최소 두 VM과 최소 두 Zone을 요구한다.
- VM 그룹, Multi-Zone, Load Balancer는 배치·운영 선택이지 고가용성 보장이 아니다.

CSP, Region, 예산은 초기 요구사항에서 받을 수 있다. VM 수, Zone, DB 배치, 진입 방식은
구체 설계 단계에서 확정해도 된다. 확정되지 않은 필드는 추측하지 않고 `needsInput`으로 남긴다.

## 3. 정본과 생성 흐름

```text
요구사항과 논리 배포 모델
  → DeploymentTopology/v1
  → provider projection policy
  → DepKB capability realization
  → ResourcePlan/v1
  ├─ provisioning dependency diagram
  ├─ runtime deployment diagram
  └─ IaC 생성·검증
```

`ResourcePlan/v1`이 다이어그램과 IaC 생성의 공통 입력이다. 별도의 수작업 HTML 모델은 두지
않으며, 선택 배포에서 생성한 PUML과 SVG가 실제 ResourcePlan의 시각화 결과다.

다이어그램은 둘로 나눈다.

- provisioning: `선행 리소스 → 후행 리소스` 방향만 표시한다.
- runtime: 실제 요청, health probe, 애플리케이션 간 통신과 disk attachment를 표시한다.

ResourcePlan 내부 edge는 소비자에서 참조 대상으로 기록될 수 있으므로, provisioning 렌더러가
화살표를 뒤집어 항상 `선행 → 후행`으로 보여 준다.

## 4. 공통 배포 준비 과정

EasyDep는 CSP 계정이나 credential을 받지 않는다. 사용자가 로컬 AWS·Azure·Google Cloud
인증으로 생성 번들을 실행하며, 생성 IaC가 아래 리소스를 만든다.

1. CSP, Region, topology 조합과 runtime port·readiness path를 확정한다.
2. PostgreSQL을 선택하면 사용자가 provider Secret을 먼저 만들고 그 reference만 배포 변수로 준다.
3. Network, Subnet, traffic filter와 필요한 공인 주소·outbound 경로를 만든다.
4. CSP Registry와 App VM pull identity를 만든다.
5. 앱 이미지를 한 번 build·push한 뒤 digest를 checkpoint에 고정한다.
6. 고정 digest가 준비된 뒤 VM 또는 VM template의 user-data/cloud-init으로 Docker와 앱을 실행한다.
7. PostgreSQL을 선택했다면 전용 Secret read identity, State VM과 disk를 만들고 attach, filesystem, mount, Docker bind를 수행한다.
8. 앱의 datasource와 runtime 환경설정을 주입한다. State VM 교체 시 고정 사설 주소를 재사용하므로 앱 이미지는 다시 빌드하지 않는다.
9. 외부 업무 요청, readiness와 데이터 보존을 검증한다.
10. endpoint, resource ID, image digest, status·destroy 절차를 출력한다.

Secret은 `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`를 가진 JSON 객체로 고정한다.
EasyDep는 Secret 값이나 CSP credential을 수집·저장하지 않는다. App VM은 Registry pull과
Secret read 권한을 가진 identity를 쓰고, 별도 State VM은 Registry 권한이 없는 전용 Secret
read identity를 써서 최소 권한을 유지한다.

사설 VM이 Registry나 package 저장소에 접근해야 하면 NAT 또는 private endpoint가 필요하다.
사전 제작 이미지로 모든 실행물을 포함하는 경우에만 해당 outbound 의존성을 제거할 수 있다.
현재 v1은 private endpoint 대신 NAT를 선택한다. 직접 공개 App VM은 자신의 공인 주소로
송신하고, 별도 State VM만 사설이면 두 경로를 함께 사용한다.

## 5. AWS

### 5.1 기본 VM

주요 생성 의존성은 다음과 같다.

```text
Region
  → VPC
  → Subnet
  → Security Group
AMI + Subnet + Security Group
  → EC2 Instance
```

- VPC는 Regional 리소스다.
- Subnet은 한 Availability Zone에 속하고 VPC ID를 참조한다.
- Security Group은 VPC ID를 참조한다.
- EC2는 AMI, Subnet, Security Group을 참조한다.
- 기본 Primary ENI와 Root EBS Volume은 EC2 생성 결과로 생긴다. ResourcePlan/IaC 그래프에는 사용자가 작성해야 하는 리소스만 기본 표시한다.

직접 공개 경로는 다음과 같다.

```text
VPC → Internet Gateway
VPC → Route Table
Internet Gateway + Route Table → default Route
Subnet + Route Table → Route Table Association
EC2 + Elastic IP → Elastic IP Association
```

기능 경로는 `Internet → EIP → ENI → EC2 guest port → container port`다.

별도 State VM 또는 사설 LB backend의 outbound는 다음과 같다.

```text
Public Subnet + EIP → NAT Gateway
Private Subnet + private Route Table → default Route → NAT Gateway
Public Subnet + public Route Table → default Route → Internet Gateway
```

직접 공개 App VM과 별도 State VM을 함께 쓰면 App Subnet은 public, State Subnet은 private으로
분리한다. 하나의 Subnet을 서로 다른 default Route Table에 동시에 연결하지 않는다.

### 5.2 Network Load Balancer

선택 리소스는 정확히 다음과 같다.

- Network Load Balancer: `aws_lb`와 `load_balancer_type = "network"`
- Network Load Balancer Listener: `aws_lb_listener`
- Target Group: `aws_lb_target_group`
- 단일 VM이면 Target Group Attachment, ASG이면 `target_group_arns`

생성 참조는 다음과 같다.

```text
VPC → Target Group
Subnet 1개 이상 + 선택적 NLB Security Group → Network Load Balancer
Network Load Balancer + Target Group → TCP Listener :80
Target Group + EC2 → Target Group Attachment        # standalone
Launch Template + App Subnet들 + Target Group → Auto Scaling Group
```

NLB 자체는 Subnet 한 개 이상으로 생성할 수 있다. Multi-Zone topology를 선택했을 때만 선택
Zone마다 Subnet을 연결한다. 이는 ALB의 서로 다른 AZ Subnet 최소 2개 규칙과 다르다.

요청 경로는 `NLB → TCP Listener :80 → Target Group → healthy EC2 → 앱 host port`다.
Target Group의 전달 protocol은 TCP이고, readiness 판정에는 HTTP health check와 앱 경로를 쓸 수 있다.
ASG가 ELB health를 사용하려면 Target Group 연결과 ASG health check type 설정이 모두 필요하다.

## 6. Azure

### 6.1 기본 VM

```text
Resource Group + Region → Virtual Network
Virtual Network → Subnet
Subnet → Network Interface
Network Interface + Network Security Group → NIC–NSG Association
Image + Network Interface → Linux Virtual Machine
Public IP + Network Interface → 직접 공개 IP configuration
```

Resource Group과 location은 대부분 Azure 리소스의 공통 생성 문맥이다. 다이어그램은 주요 ID
참조를 우선 표시하고, 공통 문맥을 생략할 때에는 생략 정책을 명시한다.

### 6.2 Load Balancer

선택 리소스는 다음과 같다.

- Load Balancer: `azurerm_lb`, Standard SKU
- Load Balancer / Frontend IP Configuration: `azurerm_lb` 내부 구성
- Backend Address Pool: `azurerm_lb_backend_address_pool`
- Probe: `azurerm_lb_probe`
- Load Balancing Rule: `azurerm_lb_rule`
- 단일 VM: `azurerm_network_interface_backend_address_pool_association`
- VMSS: NIC IP configuration의 `load_balancer_backend_address_pool_ids`

생성 참조는 다음과 같다.

```text
Public IP → Load Balancer / Frontend IP Configuration
Load Balancer → Backend Address Pool
Load Balancer → Probe
Load Balancer + Frontend config + Backend Pool + Probe → Load Balancing Rule
NIC + Backend Pool → NIC Backend Address Pool Association   # standalone
VMSS NIC configuration + Backend Pool → VMSS               # managed group
```

Load Balancing Rule은 TCP frontend port 80을 앱 backend port로 전달한다. Probe는 앱 readiness
path를 HTTP로 확인한다. Application Gateway와 달리 전용 Subnet, HTTP Listener, Backend HTTP
Settings, Request Routing Rule은 필요하지 않다.

사설 VM outbound는 Standard Static Public IP와 NAT Gateway를 각각 만든 뒤
`azurerm_nat_gateway_public_ip_association`과
`azurerm_subnet_nat_gateway_association` 두 객체로 주소와 Subnet을 모두 연결한다.

## 7. GCP

### 7.1 기본 VM

```text
Project → VPC Network
VPC Network + Region → Subnetwork
VPC Network → Firewall Rule
OS Image + Subnetwork → VM Instance
Regional External IP → network_interface.access_config   # 직접 공개
```

VPC Network는 global, Subnetwork는 regional, VM과 일반 Persistent Disk는 zonal이다.

### 7.2 Regional External Passthrough Network Load Balancer

선택 리소스는 다음과 같다.

- Regional Static External IP Address: `google_compute_address`
- Forwarding Rule: `google_compute_forwarding_rule`
- Region Backend Service: `google_compute_region_backend_service`
- Region Health Check: `google_compute_region_health_check`
- standalone이면 Unmanaged Instance Group, managed이면 MIG가 노출하는 Instance Group

생성 참조는 다음과 같다.

```text
Regional Address + Region Backend Service → Forwarding Rule
Region Health Check + Instance Group → Region Backend Service
VM → Unmanaged Instance Group                            # standalone
Instance Template → Managed Instance Group              # managed group
```

설정은 `load_balancing_scheme = "EXTERNAL"`, TCP, frontend port 80이다. 이 제품은 proxy가 아니라
passthrough이므로 Target HTTP Proxy와 URL Map이 없다. 또한 destination port를 변환하지 않으므로
VM host port 80을 열고 필요하면 Docker가 `host 80 → container HTTP port`로 게시해야 한다.
Firewall Rule은 client traffic과 Google health-check source의 TCP/HTTP probe를 허용해야 한다.

사설 VM outbound는 VPC Network의 기존 default internet route를 유지하고, 같은 Region의
Cloud Router와 Cloud NAT를 만든다. Cloud NAT는 `AUTO_ONLY`, `LIST_OF_SUBNETWORKS`, 선택
Subnetwork의 `ALL_IP_RANGES`로 고정한다.

## 8. 영속 disk와 애플리케이션 의존성

세 CSP 모두 독립 block disk는 연결할 VM과 호환되는 Region/Zone에 있어야 한다.

| CSP | data disk | 연결 |
|---|---|---|
| AWS | EBS Volume | `aws_volume_attachment` |
| Azure | Managed Disk | `azurerm_virtual_machine_data_disk_attachment` |
| GCP | Persistent Disk | `google_compute_attached_disk` |

IaC의 attachment는 guest filesystem을 자동으로 준비하지 않는다. user-data/cloud-init이 다음을
멱등적으로 수행해야 한다.

```text
disk attach
  → 안정적인 device ID 확인
  → filesystem이 없을 때만 format
  → guest mount
  → Docker bind mount
  → PostgreSQL data directory 사용
```

앱과 리소스 사이의 필수 계약은 다음과 같다.

- Registry digest와 VM pull identity
- 앱 container port와 VM host port
- 외부 요청을 허용하는 Security Group, NSG 또는 Firewall Rule
- Load Balancer health check와 앱 readiness path
- PostgreSQL endpoint와 datasource 설정
- disk mount와 PostgreSQL data path

readiness는 Load Balancer가 트래픽 대상을 고르는 신호다. VM 그룹의 교체 판단 신호와 같게
구성할 수 있지만 역할과 실패 영향이 다르므로 ResourcePlan에서는 별도 정책으로 취급한다.

data disk는 ResourcePlan에서 `retain`이며 Terraform에는 `lifecycle.prevent_destroy = true`를
둔다. 일반 `destroy`는 disk를 detach하고 Terraform state에서 제외한 뒤 나머지 실행 소유
리소스만 제거한다. 데이터 삭제는 확인 절차가 있는 별도 `purge`에서만 수행한다.

## 9. 근거와 검증 상태

공식 문서 확인, Terraform plan, 실제 CSP 생성, runtime 기능 검증을 구분한다. 원장 구조 검사가
통과했다는 사실을 의미 검증 또는 동적 검증으로 표현하지 않는다.

과거 AWS ALB, Azure Application Gateway, GCP External Application Load Balancer 실험은 당시
L7 구성의 역사적 artifact다. 현재 선택한 세 L4 경로의 근거로 재사용하지 않는다.
2026-08-17에는 같은 도메인 중립 최소 앱으로 AWS Network Load Balancer, Azure Standard Load
Balancer, GCP Regional External Passthrough Network Load Balancer를 각각 1회 동적 검증했다.
따라서 `runtimeEvidence.managedL4Ingress`는 관찰된 항목과 미측정 항목을 분리해 기록한다.

필수 동적 검증은 다음과 같다.

- TCP port 80 외부 요청
- HTTP readiness 실패 backend 제외와 운영자 복원
- 여러 VM을 선택했을 때 각 backend 도달
- 실행 소유 리소스 cleanup 뒤 잔여 0
- State VM 재부팅·교체 뒤 disk reattach와 데이터 보존

L4 실험은 SLA, 처리량·지연시간, 관리형 VM 자동교체를 측정하지 않았다. 세 항목은 L4 전달
의존성의 성공으로 간주하지 않는다. 상세 결과는 `managed-l4-ingress-experiment-20260817.md`에 있다.

## 10. 산출물과 재현성

다이어그램 예시는 `scripts/generate_deployment_diagram_examples.py`가 모든 유효 topology 조합의
PUML과 SVG를 결정적으로 생성한다. PNG는 만들지 않으며 `docs/examples/`는 생성 산출물이므로
Git에서 제외한다.

최종 사용자 배포 번들은 최소한 다음을 포함해야 한다.

- 애플리케이션 소스와 Dockerfile
- provider별 Terraform
- `doctor`, `plan`, `deploy`, `status`, `destroy` 실행 진입점
- 영속 disk가 있으면 명시적 데이터 삭제용 `purge` 진입점
- 변수 예시와 비용·보안 경계 안내
- 배포 checkpoint: provider, Region, deployment ID, resource IDs, image digest, 완료 단계

체크포인트로 재개할 때에는 실행 ID, 앱 ID, 완료 단계와 산출물 대응이 일치하는지 확인한다.
배포 순서는 `Registry 생성 → build·push → digest 기록 → compute 생성`으로 고정하며, 이미
기록된 같은 digest는 재개 시 다시 build하지 않는다.

## 11. 배포 다이어그램은 무엇을 근거로 만드는가

배포 다이어그램은 LLM이 임의로 그리는 그림이 아니다. 다음 네 입력을 합쳐 결정적으로 만든다.

| 입력 | 확인하는 사실 | 다이어그램에 미치는 영향 |
|---|---|---|
| 논리 배포 모델 | 실행할 App workload, PostgreSQL workload, App→DB 연결 | 컨테이너, State VM, datasource 흐름 |
| Resource Spec | CSP, Region, compute profile, replica 수, Zone, DB 배치, 공개 진입 | 실제 provider 리소스 종류와 수량·배치 |
| 앱 runtime 계약 | container port, health path, datasource 형식, mount path | LB backend/probe, firewall port, guest·container 설정 |
| DepKB와 프로젝트 정책 | CSP 생성 참조, 선택한 L4 realization, OS/PostgreSQL image 정책 | ResourcePlan의 provider node·edge와 검증 규칙 |

필수 Resource Spec 필드는 다음과 같다.

```text
provider                aws | azure | gcp
region                  실제 Region
computeProfile          standaloneOne | managedGroupOne |
                        managedGroupManySingleZone | managedGroupManyMultiZone
replicaCount            one profile은 1, many profile은 2 이상
selectedZones           single-zone은 최대 1개, multi-zone은 2개 이상
databasePlacement       none | colocated | dedicated
publicIngress           direct | loadBalanced
applicationStateless    replica가 여러 개면 true라는 분석 근거 필요
```

사용자 credential, Secret 값, 자동 복구 또는 고가용성 희망 여부는 이 구조 입력에 넣지 않는다.
credential은 사용자의 실행 환경에만 있고, Secret은 기존 provider Secret reference만 배포 시
받는다. 가용성 결과는 현재 다이어그램이 주장하지 않는다.

## 12. 생성 알고리즘을 손으로 따라 하는 방법

### 12.1 논리 workload를 먼저 찾는다

1. `executionEnvironment` 노드를 App workload로 잡는다.
2. `database` 노드가 있으면 PostgreSQL workload로 잡는다.
3. App에서 Database로 향하는 논리 연결을 기록한다.
4. controller, entity, repository 같은 코드 구조는 별도 VM으로 승격하지 않는다.

예를 들어 `Application Runtime → PostgreSQL`이면 실행 workload는 둘이지만, DB 배치가
`colocated`이면 같은 VM에 두고 `dedicated`이면 별도 State VM에 둔다.

### 12.2 토폴로지 조합을 확정한다

다음 순서로 검사한다.

1. `standaloneOne`만 `direct`를 허용한다.
2. managed group은 하나의 고정 진입점이 필요하므로 `loadBalanced`를 사용한다.
3. `colocated` PostgreSQL은 `standaloneOne`만 허용한다.
4. many profile은 replica가 2 이상이고 App이 무상태라는 근거가 있어야 한다.
5. multi-zone profile은 서로 다른 Zone이 2개 이상이어야 한다.

하나라도 충족하지 못하면 그림을 추측해서 완성하지 않고 `needsInput`으로 중단한다. 이 검사를
통과하면 12개 논리 조합 중 하나가 되고, CSP를 곱하면 36개 provider 조합 중 하나가 된다.

### 12.3 provider projection policy를 만든다

토폴로지를 다음 실행 제약으로 바꾼다.

- standalone 또는 provider-native managed group
- 정확한 fixed replica 수; autoscaling 없음
- 최소 Zone·Subnet 수와 선택 Zone
- L4 Load Balancer 필요 여부
- backend health check 필요 여부
- AWS Multi-Zone이면 Zone별 App Subnet과 NLB Ingress Subnet

이 정책은 “두 대이므로 HA” 같은 결과를 만들지 않는다. 오직 무엇을 몇 개, 어디에 둘지만
정한다.

### 12.4 DepKB에서 필요한 provider 관계를 가져온다

기본 anchor는 `vm`이다. PostgreSQL이 있으면 `disk`, Load Balancer를 선택하면
`loadBalancer`와 `load-balanced-ingress` realization을 추가한다. 여기서 얻는 것은 VPC와
Subnet 같은 CSP 생성 참조 및 선택한 LB 구성요소다. Registry, Secret, guest 초기화처럼 현재
DepKB 밖의 정책은 명시적인 프로젝트 정책으로 ResourcePlan에 더한다. 근거가 다른 두 종류를
한 출처인 것처럼 표시하지 않는다.

### 12.5 ResourcePlan 노드를 만든다

노드는 다음 셋으로 구분한다.

- `create`: 생성 IaC가 독립 Terraform resource로 만든다.
- `referenceExisting`: AMI·VM Image·OS Image와 사용자 Secret처럼 기존 객체를 조회·참조한다.
- `configureInsideOwner`: Azure LB Frontend IP Configuration처럼 상위 리소스 안에서 설정한다.

Primary ENI, Root/OS/Boot Disk, AWS main Route Table 같은 provider 자동 생성 결과는 실제로
존재하지만, 사용자가 작성할 현재 ResourcePlan 기본 노드에서는 생략한다. Attachment,
Association, 권한 binding처럼 Terraform이 독립 객체로 작성해야 하는 요소는 ResourcePlan에는
별도 노드로 보존한다. 다만 사용자용 다이어그램에서는 CSP 리소스로 오해하지 않도록 양 끝
리소스 사이의 이름 있는 관계선으로 접는다.

모든 조합에 다음 공통 노드가 들어간다.

```text
기존 OS image reference
provider-native App Registry
App runtime identity
Registry pull binding
Network + App Subnet + traffic filter
standalone VM 또는 managed VM group/template
공개 주소 또는 L4 Load Balancer 경로
```

PostgreSQL을 선택하면 다음이 추가된다.

```text
기존 provider Secret reference
App identity의 Secret read binding
영속 data Disk + Attachment
colocated: 같은 App VM의 PostgreSQL container
dedicated: 고정 사설 IP State VM + 전용 Secret identity/binding
```

사설 VM이 생기는 `loadBalanced` 또는 `dedicated` 조합에는 NAT를 추가한다. 단, AWS의
`direct + dedicated`는 App public Subnet과 State private Subnet을 분리한다.

### 12.6 생성 의존성 edge를 그린다

ResourcePlan 내부 edge는 `의존하는 쪽 → 참조 대상`으로 저장한다. 예를 들어 Subnet이 VPC ID를
받으면 `Subnet → VPC`다. 사람이 보는 provisioning 다이어그램에서는 일반 생성 참조를 뒤집어
`선행 리소스 → 후행 리소스`로 그린다.

```text
ResourcePlan 저장: Subnet → VPC          # Subnet이 VPC를 참조
화면 표시:       VPC → Subnet           # VPC를 먼저 생성
```

연결 객체는 예외다. `aws_volume_attachment` 같은 Terraform type을 가짜 CSP 리소스 노드로
표시하지 않고 다음처럼 방향 없는 관계선으로 투영한다.

```text
ResourcePlan 내부: Attachment → EBS, Attachment → EC2
화면 표시:        EBS Volume — attached — EC2 Instance
```

Route Table Association, NIC–NSG Association, backend membership, IAM/RBAC binding도 같은 원칙을
적용한다. 정확한 Terraform type과 양쪽 ID 참조는 ResourcePlan과 IaC 검증에 그대로 남는다.

edge를 추가하는 기준은 구체적인 입력값 또는 명시적 생성 순서다.

- `subnet_id`, `vpc_id`, resource ID/ARN/self_link를 받는다.
- Attachment·Association 객체가 양쪽 리소스 ID를 받는다.
- 상위 리소스 안에 embedded configuration을 작성한다.
- VM bootstrap 전에 IAM binding 또는 image digest가 반드시 준비되어야 한다.

단순히 패킷이 지나간다는 이유로 생성 edge를 추가하지 않는다. 요청 경로는 기능 다이어그램에
그린다.

### 12.7 앱 runtime 계약을 마지막에 결합한다

초기 ResourcePlan에서 port와 health path를 모르면 `runtimeDerived`로 둔다. 앱 분석이 끝나면
다음을 한 번에 같은 값으로 묶는다.

```text
Spring server.port
Docker container port
VM host publish port
Security Group / NSG / Firewall 허용 port
LB backend port
LB health-check port + readiness path
공개 endpoint port
```

GCP passthrough Network Load Balancer는 port 변환을 하지 않으므로 VM host port를 80으로
고정하고 `host 80 → app container port`로 publish한다. AWS와 Azure는 backend port를 앱 host
port로 전달할 수 있다.

### 12.8 두 개의 그림을 각각 렌더링한다

provisioning view에는 provider resource와 생성 참조만 넣는다. 화살표는 `선행 → 후행`, 방향
없는 점선은 IaC가 적용하는 attach·association·permission·route 관계다. runtime view에는 실제
VM 수를 펼쳐서 다음을 표시한다.

- Internet client → Public IP/LB → 각 App container
- LB health check → readiness endpoint
- App container → PostgreSQL container
- VM → attached persistent disk
- traffic filter가 허용하는 대상

many profile은 `x2`라고 축약하지 않고 최소 replica 두 개를 각각 그린다. 선택 Zone이 있으면
replica를 Zone에 순환 배치한다. 생성·기능 의미를 한 화살표에 섞지 않는다.

## 13. 앱–리소스 의존성을 이해하고 검증하는 방법

### 13.1 앱–리소스 의존성이란 무엇인가

클라우드 콘솔에 VM, Load Balancer와 Disk가 모두 보인다고 애플리케이션 배포가 끝난 것은
아니다. 리소스가 존재하는 것과 앱이 그 리소스를 올바르게 사용하는 것은 서로 다른 문제다.

예를 들어 EBS Volume을 EC2에 attach하면 AWS 작업은 성공한다. 하지만 Linux에서 filesystem을
만들고 디렉터리에 mount한 뒤 그 디렉터리를 PostgreSQL data path에 연결하지 않으면
PostgreSQL은 EBS가 아니라 컨테이너의 임시 layer에 데이터를 쓴다. 이 경우 “EBS 생성 성공”은
참이지만 “DB 데이터 영속성 확보”는 거짓이다.

EasyDep는 다음 세 층이 끊기지 않고 이어졌을 때만 의존성이 해결됐다고 판단해야 한다.

```text
CSP 리소스
  VM, Registry, IAM identity, Public IP, Load Balancer, Secret, Disk
        ↓
VM guest 구성
  OS image, cloud-init, Docker, image pull, filesystem, mount, restart policy
        ↓
애플리케이션 계약
  container port, readiness path, datasource URL, DB credential, PGDATA
```

이 세 층 중 하나라도 빠지면 provider 리소스 그래프는 멀쩡해 보여도 실제 서비스는 실패한다.

### 13.2 검증도 네 단계로 나눈다

EasyDep는 모든 검사를 단순히 “검증 통과”라고 부르면 안 된다. 다음 수준을 따로 기록한다.

| 수준 | 확인하는 것 | 확인하지 못하는 것 |
|---|---|---|
| 계약·구조 검사 | 필수 입력, 노드·간선, port·mount·Secret 계약의 누락과 모순 | 실제 CSP가 생성되는지 |
| Terraform source·Plan 검사 | 필요한 리소스와 ID 참조, 수량, Zone, 보안 규칙이 Plan에 나타나는지 | guest OS 안에서 명령이 성공하는지 |
| runtime 기능 검사 | 실제 image pull, 앱 기동, HTTP 요청, DB migration·읽기·쓰기 | 장애 후 복구와 데이터 보존 |
| fault·lifecycle 검사 | process·VM 재시작, State VM 교체, destroy·purge 결과 | SLA와 장기 성능 |

구조 검사만 통과한 배포를 “앱 검증 완료”라고 표시해서는 안 된다. runtime이나 fault 검사를
실행하지 않았다면 `notMeasured`로 남겨야 한다.

### 13.3 앱 소스에서 실행 중 컨테이너까지

#### 13.3.1 앱 build

Spring Boot 소스만으로 VM이 실행되지는 않는다. 소스와 Dockerfile, 고정된 build/runtime base
image를 사용해 실행 가능한 OCI image를 만들어야 한다.

```text
생성 앱 소스
  + Dockerfile
  + gradle:8.14.2-jdk21
  + eclipse-temurin:21-jre
  → App OCI image
```

EasyDep가 확인해야 할 것:

1. 애플리케이션 test와 Docker image build가 실제로 성공하는가?
2. Dockerfile이 존재하지 않는 파일이나 잘못된 build output을 복사하지 않는가?
3. 테스트한 image와 배포할 image가 같은 digest인가?
4. 배포 단계에서 소스를 다시 build해 다른 image를 만들지 않는가?

가장 안전한 결과는 테스트를 통과한 image를 OCI archive와 digest로 보존하고, 사용자의 배포
단계에서는 그 동일 image를 선택한 CSP Registry에 push하는 것이다.

실패 예시: 테스트에서는 Java 21로 빌드했지만 배포 스크립트가 mutable `latest` base image로
다시 빌드하면 테스트하지 않은 image가 운영 VM에서 실행될 수 있다.

#### 13.3.2 Registry 게시와 digest 고정

VM 여러 대에 동일한 앱을 배포하려면 한 번 만든 image를 provider-native Registry에 게시한다.

| CSP | Registry |
|---|---|
| AWS | Amazon ECR Repository |
| Azure | Azure Container Registry |
| GCP | Artifact Registry Repository |

여기서 tag와 digest의 차이가 중요하다. `app:latest`는 나중에 다른 image를 가리킬 수 있지만
`app@sha256:...`는 내용이 바뀌지 않는 불변 참조다. VM이나 VM template에는 tag가 아니라 push
결과 digest를 전달해야 한다.

EasyDep가 확인해야 할 것:

- push가 성공했고 Registry가 반환한 digest가 checkpoint에 기록됐는가?
- VM bootstrap과 모든 VM Group instance가 그 digest를 사용하는가?
- 재시도할 때 이미 성공한 동일 digest를 다시 build하지 않는가?
- status가 보고한 실행 중 container digest가 checkpoint와 같은가?

실패 예시: VM 1은 어제의 `latest`, VM 2는 오늘의 `latest`를 pull하면 같은 VM Group 안에서 서로
다른 코드가 실행된다.

#### 13.3.3 image pull identity와 권한

private Registry는 아무 VM이나 image를 내려받게 하지 않는다. VM에는 CSP가 발급하는 runtime
identity가 필요하고, 그 identity에는 선택 Repository를 읽는 최소 권한이 필요하다.

```text
AWS   EC2 IAM Role / Instance Profile ── ECR read ── ECR Repository
Azure Managed Identity               ── AcrPull  ── Container Registry
GCP   Service Account                ── Reader   ── Artifact Registry
```

사용자 개인 access key를 VM에 복사하면 안 된다. EasyDep가 생성해야 하는 것은 VM 전용 identity와
Registry read 관계이며, 다이어그램에서는 IAM Attachment 같은 IaC 객체를 별도 CSP 리소스 노드가
아니라 두 실제 리소스 사이의 권한 관계선으로 표시한다.

EasyDep가 확인해야 할 것:

- VM 또는 Launch/Instance Template에 계획한 identity가 연결됐는가?
- 권한 scope가 전체 계정이 아니라 선택 Repository로 제한됐는가?
- VM identity로 실제 digest pull에 성공하는가?
- 별도 State VM에는 필요 없는 App Registry 권한을 부여하지 않았는가?

#### 13.3.4 image pull을 위한 outbound

권한이 있어도 Registry까지 네트워크가 열려 있지 않으면 pull할 수 없다.

- 공인 주소가 있는 직접 공개 VM은 Internet Gateway를 통한 outbound를 사용할 수 있다.
- 공인 주소가 없는 App/State VM은 NAT와 DNS·route가 필요하다.
- 별도 State VM은 Docker Hub에서 `postgres:17-bookworm`도 pull해야 한다.

EasyDep가 확인해야 할 것:

- 사설 Subnet의 default route가 올바른 NAT를 가리키는가?
- NAT가 공인 주소와 인터넷 경로를 갖는가?
- GCP 기본 internet route를 실수로 삭제하지 않았는가?
- guest에서 Registry hostname과 Docker Hub에 실제로 도달하는가?

실패 예시: Terraform으로 VM과 IAM Role은 정상 생성됐지만 private Subnet에 NAT가 없으면
cloud-init의 첫 `docker pull`에서 배포가 멈춘다.

#### 13.3.5 OS 부팅과 guest 초기화

AMI, Azure VM Image, Compute Engine OS Image는 VM의 최초 boot disk 내용을 정한다. 앱 OCI
image와는 역할이 다르다. OS image 위에서 cloud-init/user-data가 Docker를 준비하고 image를
pull한 뒤 container를 실행한다.

EasyDep가 확인해야 할 것:

- OS image ID가 실제 Region에서 조회되고 checkpoint에 기록됐는가?
- guest 초기화 스크립트가 실패 시 조용히 넘어가지 않는가?
- Docker daemon 준비 후에 pull·run을 수행하는가?
- 재부팅 뒤 App과 PostgreSQL container가 다시 실행되는가?

### 13.4 외부 요청이 앱 port까지 도달하는 과정

#### 13.4.1 하나의 port 계약

다음 값들은 서로 독립적으로 정하면 안 된다. 모두 앱 분석에서 얻은 하나의 port 계약에서
파생해야 한다.

```text
Spring Boot server.port
  ↔ Docker container port
  ↔ VM host publish port
  ↔ Security Group / NSG / Firewall 허용 port
  ↔ Load Balancer backend port
  ↔ health-check port
```

예를 들어 앱이 container port `8080`에서 수신한다고 하자.

- AWS 직접 공개: `EIP → EC2 host 8080 → container 8080`
- AWS/Azure LB: frontend 80에서 받은 요청을 backend host 8080으로 전달할 수 있다.
- GCP passthrough NLB: port 변환을 하지 않으므로 `frontend 80 → host 80`, Docker가
  `host 80 → container 8080`으로 게시한다.

EasyDep의 정적 검증은 소스·Docker·IaC에서 관찰한 port가 계약값과 모순되는지 검사해야 한다.
동적 검증은 VM localhost와 최종 공개 endpoint에 요청해 실제 응답을 확인해야 한다.

실패 예시: 앱은 8080에서 수신하지만 Security Group만 80을 열고 Docker publish도 하지 않으면
VM은 정상 실행 중이어도 외부 요청은 `connection refused`가 된다.

#### 13.4.2 직접 공개 경로

직접 공개는 다음 경로다.

```text
Internet client → 고정 Public IP → VM NIC → host port → App container
```

필요한 것은 공인 주소, 인터넷 route, 앱 port를 허용하는 traffic filter다. Load Balancer는
자동으로 추가하지 않는다.

검증 항목:

- Public IP가 실제 App VM/NIC에 연결됐는가?
- 허용 source와 port가 계획과 같은가?
- PostgreSQL 5432는 public source에 열리지 않았는가?
- 공개 주소에서 업무 API가 성공하는가?

#### 13.4.3 Load Balancer 경로와 backend membership

LB를 선택하면 공인 주소만 만든 것으로 끝나지 않는다.

```text
Public IP
  → Listener / Load Balancing Rule / Forwarding Rule
  → Target Group / Backend Pool / Backend Service
  → 등록된 App VM
  → App host port
```

LB와 VM 사이에는 Target Group Attachment, Backend Pool Association 같은 IaC 연결 객체가 있다.
ResourcePlan과 Terraform 검증에는 이 객체를 보존하지만, 사용자 다이어그램에서는
`Load Balancer backend — registered — App VM` 관계선으로 접는다.

검증 항목:

- 선택한 App VM만 backend에 등록됐는가?
- many profile이면 최소 두 VM을 각각 확인했는가?
- Listener/Rule의 backend 참조가 ResourcePlan과 같은가?
- LB endpoint로 반복 요청했을 때 계획한 backend들이 실제 응답하는가?

#### 13.4.4 readiness

readiness endpoint는 “process가 살아 있다”가 아니라 “이 VM이 새 업무 요청을 받아도 된다”를
표현한다. LB health check의 port와 path를 앱이 제공하는 readiness와 맞춰야 한다.

```text
LB health check → /actuator/health/readiness
2xx             → backend에 요청 전달
실패/timeout     → backend에서 제외
```

검증 항목:

- 앱에 readiness endpoint가 실제 존재하는가?
- LB가 같은 port와 path를 검사하는가?
- readiness를 실패시켰을 때 해당 backend가 새 요청에서 제외되는가?
- 복원 후 다시 backend로 들어오는가?

이 검증은 VM 자동교체나 고가용성을 증명하지 않는다. 오직 LB가 backend 상태를 트래픽 선택에
반영하는지만 확인한다.

### 13.5 앱과 PostgreSQL 연결

#### 13.5.1 Secret 값이 아니라 Secret reference를 받는다

개발 단계에서는 사용자의 CSP 계정, Secret reference 또는 실제 비밀번호가 필요하지 않다.
사용자가 배포 번들을 실행할 때만 현재 로그인한 Account/Subscription/Project를 확인하고 기존
Secret의 reference를 로컬 배포 변수 `database_secret_ref`로 입력한다.

Secret JSON 계약은 다음 세 key다.

```json
{
  "POSTGRES_DB": "...",
  "POSTGRES_USER": "...",
  "POSTGRES_PASSWORD": "..."
}
```

EasyDep 서버는 이 값, CSP access key 또는 로그인 token을 받거나 저장하지 않는다. 생성 IaC는
Secret 자체나 값을 만드는 대신 App identity와 State identity에 해당 Secret read 권한만 준다.

검증 항목:

- `database_secret_ref` 변수가 sensitive로 선언됐는가?
- reference가 선택한 provider와 배포 대상에 존재하는가?
- App identity와 State identity가 해당 Secret만 읽을 수 있는가?
- Terraform Plan, 로그, checkpoint, 다이어그램에 Secret 값이 노출되지 않는가?
- VM runtime identity로 실제 Secret read가 성공하는가?

#### 13.5.2 App과 State VM은 같은 값, 다른 identity를 사용한다

PostgreSQL container는 최초 database와 사용자를 만들기 위해 Secret을 읽는다. Spring Boot도
같은 사용자로 접속하기 위해 같은 Secret을 읽는다. 값은 같지만 권한 주체는 분리한다.

```text
Existing provider Secret
  ├─ read → App VM identity   → Spring datasource credential
  └─ read → State VM identity → PostgreSQL startup credential
```

State VM identity에 App Registry pull 권한까지 줄 이유는 없다. PostgreSQL image는 현재 Docker
Hub에서 받으므로 State identity에는 Secret read만 부여한다.

실패 예시: PostgreSQL은 `POSTGRES_USER=easydep`로 초기화됐는데 앱이 다른 Secret을 읽으면 두
container 모두 정상 실행 중이어도 DB 인증은 실패한다.

#### 13.5.3 DB endpoint와 datasource

별도 State VM을 선택하면 앱은 다음 구체적인 endpoint를 알아야 한다.

```text
State VM의 고정 사설 IP + TCP 5432 + database name
  → JDBC URL
  → spring.datasource.*
  → Spring DataSource
```

DHCP로 바뀔 수 있는 임시 주소나 public IP를 datasource에 넣지 않는다. State VM을 교체해도
계획한 사설 주소 또는 재결합 절차가 유지되어야 한다.

검증 항목:

- datasource host가 실제 State VM 사설 주소인가?
- driver, JDBC scheme과 dialect가 PostgreSQL로 일치하는가?
- App VM에서 State VM 5432에 TCP 연결할 수 있는가?
- 앱 시작 시 migration과 최소 create/write/read query가 성공하는가?

#### 13.5.4 DB traffic policy

PostgreSQL은 인터넷에 공개하지 않는다. State traffic filter는 App VM 또는 App security identity를
source로 한 TCP 5432만 허용한다.

검증 항목:

- `0.0.0.0/0 → 5432`와 같은 public rule이 Plan에 없는가?
- App source에서는 5432 연결이 성공하는가?
- 허용하지 않은 source에서는 거부되는가?
- State VM에 public IP가 불필요하게 붙지 않았는가?

### 13.6 Disk가 PostgreSQL 데이터가 되기까지

#### 13.6.1 Attachment는 시작일 뿐이다

EBS Volume, Managed Disk, Persistent Disk를 VM에 attach하면 guest에는 block device 하나가
나타난다. 아직 디렉터리도 아니고 PostgreSQL이 사용할 수 있는 저장소도 아니다.

```text
provider data Disk
  — attached — State VM
  → Linux block device
  → filesystem
  → guest mount directory
  → Docker bind mount
  → /var/lib/postgresql/data
```

EasyDep는 Attachment를 Terraform type 노드로 보여 주는 대신 Disk와 VM 사이의 `attached`
관계선으로 표시한다. 다만 내부 ResourcePlan에는 실제 Terraform type과 양쪽 ID 참조를 보존해
IaC 누락을 검사한다.

#### 13.6.2 올바른 device를 안정적으로 찾는다

`/dev/sdb` 같은 이름은 VM이나 재부팅 상황에 따라 달라질 수 있다. provider가 제공하는 stable
device identity 또는 filesystem UUID를 사용해야 한다.

검증 항목:

- 계획한 Disk ID와 실제 attached device가 대응하는가?
- VM과 Disk의 Region/Zone이 호환되는가?
- boot disk를 data disk로 잘못 선택하지 않았는가?

#### 13.6.3 기존 데이터를 지우지 않는 format

`mkfs`는 filesystem을 새로 만드는 명령이며 기존 데이터를 지울 수 있다. 새 Disk에 filesystem이
없을 때만 실행해야 한다.

```text
filesystem 존재 확인
  ├─ 있음 → format하지 않음
  └─ 없음 → 한 번만 mkfs
```

EasyDep의 정적 validator는 명백한 무조건부 `mkfs`를 오류로 처리하고 `blkid`, `lsblk` 같은 확인
뒤 조건부 format인지 검사해야 한다. 동적 fault 검증에서는 기존 데이터가 있는 Disk를 다시
attach해도 format되지 않는지 확인해야 한다.

#### 13.6.4 mount와 재부팅 지속성

filesystem은 명시한 guest 디렉터리에 mount하고 UUID 기반 `/etc/fstab` 또는 동등한 systemd
mount로 재부팅 뒤에도 복원해야 한다.

실패 예시: 최초 배포 때 `/mnt/data`에 mount했지만 fstab을 쓰지 않으면 재부팅 후 mount가
사라진다. PostgreSQL은 같은 경로명의 boot disk 디렉터리에 새 빈 DB를 만들 수 있다.

검증 항목:

- 계약한 guest mount path와 실제 `mount` 대상이 같은가?
- `/etc/fstab`이 device name이 아니라 UUID를 사용하는가?
- State VM 재부팅 뒤 같은 filesystem이 같은 경로에 mount되는가?

#### 13.6.5 Docker bind와 PostgreSQL data path

guest mount가 있어도 PostgreSQL container에 연결하지 않으면 소용없다. mount 아래 전용 child
directory를 공식 image의 data path `/var/lib/postgresql/data`에 bind한다.

```text
/mnt/easydep-state/postgres     # guest의 전용 child
        ↓ Docker bind
/var/lib/postgresql/data        # container 내부 PGDATA
```

filesystem root 자체를 container에 직접 bind하지 않는다. validator는 guest mount path와 Docker
source, container target이 계약과 일치하는지 구분해서 검사해야 한다.

#### 13.6.6 destroy와 purge

일반 인프라 정리는 데이터 삭제와 같아서는 안 된다. data Disk는 `retain`이며 Terraform에는
`lifecycle.prevent_destroy = true`가 필요하다.

- `destroy`: Disk를 detach하고 보존한 채 실행 소유 리소스를 제거한다.
- `purge`: 사용자가 데이터 삭제를 명시적으로 확인한 경우에만 Disk를 삭제한다.

검증 항목:

- Terraform Plan에 retained Disk 삭제가 포함되지 않는가?
- destroy 뒤 Disk ID와 데이터가 남아 있는가?
- purge 없이는 Disk 삭제 API가 호출되지 않는가?
- State VM을 새로 만들어 기존 Disk를 reattach한 뒤 기존 row를 읽을 수 있는가?

### 13.7 여러 App VM과 재시작

#### 13.7.1 여러 replica의 전제는 무상태 앱이다

App VM을 두 대 만들었다는 이유만으로 동일하게 동작한다고 가정하면 안 된다. 다음 상태를 VM
로컬에 저장하면 요청이 어느 replica로 가는지에 따라 결과가 달라진다.

- 로그인 session
- 사용자 upload 파일
- process memory의 유일한 업무 상태
- 한 VM에서만 실행되어야 하는 scheduler·lock
- 로컬 filesystem에 기록한 데이터

many profile을 허용하기 전에 앱 분석 결과 `applicationStateless=true`가 있어야 한다. 이 값은
사용자 희망이 아니라 코드·설정·저장 경로 분석 근거에서 나와야 한다.

검증 항목:

- VM별 로컬 쓰기와 in-memory session 사용이 없는가?
- 동일 image digest와 동일 일반 설정을 모든 replica가 사용하는가?
- Secret 값은 같고 각 VM은 자기 runtime identity로 읽는가?
- LB를 통해 각 replica에서 같은 업무 결과를 얻는가?

#### 13.7.2 process restart와 VM 복구는 별개다

Docker restart policy는 VM이 살아 있는 상태에서 container process가 종료되거나 VM이 재부팅된
뒤 container를 다시 시작하는 guest 기능이다. 다른 Zone에 replica를 만들거나 VM 자체를
교체하는 기능과는 다르다.

EasyDep가 최소한 확인해야 할 것은 다음이다.

- App과 PostgreSQL container에 명시적인 restart policy가 있는가?
- process kill 뒤 container가 다시 실행되는가?
- VM reboot 뒤 image, Secret, mount가 준비된 다음 container가 실행되는가?
- PostgreSQL은 mount 완료 전에 시작되지 않는가?

### 13.8 EasyDep의 종단 완료 조건

앱–리소스 의존성은 다음 순서가 모두 성공해야 완료다.

```text
1. 앱 test + OCI image build
2. 테스트한 image digest 확정
3. Terraform Plan과 ResourcePlan 참조 대조
4. Registry push와 VM identity pull 시험
5. VM guest 초기화 완료
6. localhost 앱 요청 성공
7. 공개 direct/LB endpoint 업무 요청 성공
8. 선택 시 Secret read + DB migration + create/write/read 성공
9. 선택 시 VM reboot + Disk 데이터 보존 확인
10. destroy가 retained Disk를 지우지 않는지 확인
```

예를 들어 1~4까지만 성공했다면 “인프라 및 image 전달 준비 완료”이지 “애플리케이션 배포
검증 완료”가 아니다. 6~8을 통과해야 업무 기능이 연결됐다고 말할 수 있고, 9~10을 통과해야
재시작과 데이터 lifecycle까지 검증됐다고 말할 수 있다.

### 13.9 시스템의 검증 책임을 어디에 둘 것인가

같은 검사를 LLM prompt에만 적어 두면 누락을 막을 수 없다. 다음 책임을 결정적 코드와 실행
gate로 나눈다.

| 책임 위치 | 입력 | 반드시 차단할 오류 | 결과 |
|---|---|---|---|
| 앱 계약 추출 | 생성 소스·설정 | port·health·DB·mount 계약 미확정 | application contract |
| topology 판정 | Resource Spec·앱 상태성 | 무효 direct/group 조합, many인데 상태성 근거 없음 | DeploymentTopology 또는 `needsInput` |
| ResourcePlan 생성기 | topology·DepKB | dangling edge, 고립 리소스, 수량·Zone·owner 불일치 | provider별 ResourcePlan |
| IaC source validator | 생성 Terraform·스크립트 | 필수 resource/type/변수/entrypoint 누락, port·mount 모순 | source validation report |
| Terraform Plan validator | Plan JSON·ResourcePlan | 생성 resource 집합·ID reference·수량·보안 정책 불일치 | promotion 허용 또는 차단 |
| `doctor` | 사용자 로컬 CSP 로그인·배포 변수 | 잘못된 Account/Project/Subscription, Region·Secret reference 접근 불가 | 실행 대상 확인 |
| `deploy` gate | Registry·VM·guest 로그 | digest pull, cloud-init, container 시작 실패 | 단계별 checkpoint |
| `status`·smoke test | endpoint·container·DB | 업무 HTTP, readiness, migration·query 실패 | runtime evidence |
| fault·lifecycle test | process·VM·Disk·destroy | 재부팅 후 미기동, 데이터 유실, retained Disk 삭제 | fault evidence |

현재 구현의 `topology.py`, `provider_deployment.py`, `iac_binding_validation.py`는 앞쪽의 구조·정적
정합성 gate를 담당한다. 이 단계에서는 특히 다음을 자동 차단한다.

- 지원하지 않는 topology 조합과 multi-zone 수량 오류
- ResourcePlan의 dangling endpoint, 중복 edge, 고립 provider node
- ResourcePlan과 Terraform source·Plan의 resource/type/reference 차이
- HTTP v1 계획에 HTTPS/TLS 리소스를 임의로 추가한 경우
- PostgreSQL 5432를 public source에 연 경우
- `database_secret_ref` 변수가 없거나 sensitive가 아닌 경우
- retained Disk에 `prevent_destroy`가 없는 경우
- GCP Cloud NAT 설정·기본 internet route 정책 불일치
- 필수 `doctor/plan/deploy/status/destroy` 및 선택 `purge` entrypoint 누락

반면 일반 사용자 배포마다 실제 CSP에서 image pull, 업무 HTTP, DB query, reboot, destroy까지
자동 실행해 증거를 남기는 것은 배포 번들의 실행 gate가 담당해야 한다. 해당 gate를 실행하지
않았다면 정적 validator가 통과했더라도 runtime과 fault 상태는 `notMeasured`다.

현재 정적 gate만으로 끝내지 않기 위해 추가로 완성해야 하는 자동화는 다음과 같다.

- 테스트한 OCI image를 archive·digest로 고정하고 배포 시 재빌드하지 않는 gate
- 최종 bundle·Plan 요약·로그에 실제 Secret 값이나 credential이 없는지 검사하는 secret scan
- `doctor`가 감지한 Account/Subscription/Project를 사용자가 확인한 뒤 고정하는 gate
- 배포마다 실제 VM identity로 Registry와 Secret을 읽는 시험
- 공개 endpoint 업무 요청과 선택 시 DB migration·create/write/read 시험
- VM reboot, 기존 Disk reattach, destroy·purge를 구분하는 lifecycle 시험

## 14. 예제: AWS 직접 공개 App + 별도 PostgreSQL

다음 입력을 가정한다.

```text
provider=aws
region=ap-northeast-2
computeProfile=standaloneOne
replicaCount=1
databasePlacement=dedicated
publicIngress=direct
app port=8080
readiness=/actuator/health/readiness
```

논리 workload는 App과 PostgreSQL 둘이다. App은 직접 공개되어 public App Subnet에 두고, State
VM은 public IP 없이 private State Subnet에 둔다. 생성 순서를 손으로 그리면 다음과 같다.

```text
VPC
├─ Internet Gateway
├─ App Subnet ─ public Route Table ─ default Route → Internet Gateway
│  ├─ App Security Group
│  └─ AMI + IAM Instance Profile + EIP → App EC2
└─ State Subnet ─ private Route Table ─ default Route → NAT Gateway
   ├─ State Security Group: App SG source의 TCP 5432만 허용
   └─ AMI + State Secret Instance Profile → State EC2

App Subnet + NAT EIP → NAT Gateway
ECR Repository + App IAM Role + ECR read binding → App image pull 준비
Existing Secret + App Secret policy → App credential read 준비
Existing Secret + State Secret role/policy/profile → State credential read 준비
EBS Volume + State EC2 → Volume Attachment
```

기능 경로는 별도로 다음처럼 그린다.

```text
사용자 build 환경 → ECR push → image@sha256 checkpoint
Internet client → EIP → App EC2:8080 → Spring Boot container
App EC2 → ECR                          # App public outbound
State EC2 → NAT Gateway → Docker Hub   # postgres image pull
App container → State private IP:5432 → PostgreSQL container
EBS attachment → block device → filesystem → UUID mount → Docker bind → PGDATA
```

이 예제에서 NAT가 App inbound를 처리한다고 그리면 틀리다. NAT는 public IP가 없는 State VM의
outbound용이다. 또한 EBS Attachment만 그려 놓고 mount와 Docker bind를 생략하면 provider
리소스 그래프는 맞아도 앱 기능 배포는 미완성이다.

## 15. 직접 다이어그램을 그릴 때의 최종 검사표

1. 선택한 CSP의 공식 리소스 이름과 Terraform type을 사용했는가?
2. provider 자동 생성 요소와 IaC 작성 요소를 구분했는가?
3. Attachment·Association·권한 binding을 가짜 CSP 노드가 아닌 관계선으로 표시했는가?
4. provisioning 화살표가 모두 `선행 → 후행`인가?
5. 패킷 흐름을 provisioning edge로 잘못 섞지 않았는가?
6. standalone과 managed group을 동시에 그리지 않았는가?
7. many profile의 VM을 최소 두 개로 각각 펼쳤는가?
8. `direct`에 LB를 자동 추가하지 않았는가?
9. LB 선택 시 Listener/Rule, backend membership, health check가 모두 있는가?
10. 사설 VM의 image pull outbound가 NAT로 닫혔는가?
11. OS image reference와 테스트한 Registry digest를 모두 표현했는가?
12. PostgreSQL 선택 시 사용자 Secret reference와 두 workload의 최소 read 권한이 있는가?
13. State endpoint가 고정 사설 주소이고 5432가 public이 아닌가?
14. data Disk의 Zone 호환, Attachment, filesystem, mount, Docker bind가 이어지는가?
15. retained disk의 일반 destroy와 명시적 purge가 구분되는가?
16. 앱 port, firewall port, LB backend, health path가 같은 runtime 계약에서 왔는가?
17. 정적 검사와 실제 앱·DB 업무 기능 검증을 구분했는가?
18. 미실행 runtime·fault 검증을 `notMeasured`로 표시했는가?

이 18개를 모두 답할 수 있으면 같은 입력에서 EasyDep의 provisioning view와 runtime view를
수작업으로도 재현할 수 있다. 실제 시스템은 동일한 규칙을
`deployment_diagram/bundle.py → topology.py → provider_deployment.py → provider_plantuml.py`
순서로 적용하고, PUML과 SVG만 결정적으로 생성한다.
