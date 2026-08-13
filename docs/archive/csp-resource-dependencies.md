# AWS·Azure·GCP별 Docker-on-VM 리소스 의존성

> 상태: 현재 범위에서 사용하는 CSP native 리소스의 생성 의존성과 앱 기능 의존성을
> 한곳에 정리한 기준 문서다. 중립 리소스 모델은 다루지 않는다.

## 1. 범위와 읽는 법

현재 대상으로 삼는 배포는 다음과 같다.

- 단일 Region의 Linux VM과 Docker
- 단일 앱 Workload의 VM 직접 진입
- 고가용성이 필요한 앱 Workload의 `관리형 VM 그룹 + Load Balancer`
- 자체 운영 영속 Workload 한 개와 block disk 한 개
- 명시적인 network, subnet, route와 traffic filter
- 필요할 때만 workload identity와 외부 송신 경로

관리형 데이터베이스, 자체 구현 PostgreSQL 복제, 다중 Region, Kubernetes와 서버리스는
제외한다. 따라서 앱 계층은 CSP 기능으로 VM 장애와 Zone 장애에 대응할 수 있지만,
단일 영속 Workload가 필요한 서비스 전체를 고가용성이라고 부르지는 않는다.

이 문서의 의존성은 두 종류다.

- **생성 의존성**: 선택한 배포 구조를 만들 때 리소스가 다른 리소스의 식별자나 속성을
  참조해야 하는 관계다.
- **기능 의존성**: 리소스 생성 후 실제 앱 요청, 자동 복구, 내부 연결이나 데이터 보존이
  동작하기 위해 필요한 관계다.

표의 근거 표시는 다음과 같다.

| 표시 | 의미 |
|---|---|
| `실측` | 제품 DepKB의 CLI 반복 관찰에서 확인됨 |
| `공식` | CSP 공식 문서와 API 구조에서 도출함 |
| `계약` | 앱의 port·health·mount·내부 연결 계약에서 도출함 |

`선택`은 모든 배포에 필요하지 않다는 뜻이다. 해당 기능을 선택했다면 그 행의 참조는
필수다. CSP가 제공하는 default network를 이용해 일부 입력을 생략할 수 있더라도,
EasyDep은 재현성과 보안 경계를 위해 이 문서의 명시적인 network를 생성한다.

생성 표에는 직접 참조를 적는다. 예를 들어 EC2가 Subnet을 참조하고 Subnet이 VPC를
참조하면 `EC2 → Subnet → VPC`라는 전이 의존성도 성립하지만 EC2 행에 VPC를 다시
중복하지 않는다.

## 2. AWS

### 2.1 사용하는 리소스와 역할

| 영역 | AWS 리소스 | 역할 |
|---|---|---|
| 위치 | Region, Availability Zone | 리소스의 배치 위치. 독립 생성 리소스는 아님 |
| network | VPC | 배포의 사설 주소 공간 |
| network | Subnet | 한 AZ 안의 VM·ALB 배치 범위 |
| network | Internet Gateway, Route Table, Route, Association | 공개 진입 및 선택적 외부 송신 경로 |
| 보안 | Security Group과 rule | ALB→앱, 앱→상태 포트만 허용 |
| 직접 진입 | ENI의 Public IPv4 또는 Elastic IP | 단일 VM을 직접 공개할 때 사용 |
| 앱 HA | Launch Template, Auto Scaling Group | 동일한 앱 VM을 여러 AZ에 유지하고 비정상 VM을 교체 |
| 진입 | Application Load Balancer, Listener, Target Group, Health Check | HTTPS 종료와 정상 앱 VM으로의 전달 |
| 실행 | EC2 Instance, ENI, root EBS | 단일 앱 또는 자체 운영 상태 Workload 실행 |
| 저장 | EBS Volume, Volume Attachment | 상태 Workload 데이터 보존 |
| 권한 | IAM Role, Instance Profile | 앱이 AWS API 자격 증명을 요구할 때만 사용 |
| 인증서 | ACM Certificate | HTTPS를 ALB에서 종료할 때 사용 |

### 2.2 생성 의존성

| 생성 대상 | 참조하거나 먼저 준비할 대상 | 조건 | 근거 |
|---|---|---|---|
| VPC | CIDR과 Region | 항상 | 공식 |
| Internet Gateway attachment | Internet Gateway + VPC | 공개 진입 | 공식 |
| Subnet | VPC + AZ + CIDR | 항상 | 실측·공식 |
| Route Table | VPC | 항상 | 공식 |
| public default Route | Route Table + Internet Gateway | 공개 Subnet | 공식 |
| Route Table Association | Route Table + Subnet | 항상 | 공식 |
| Security Group | VPC | 항상 | 실측·공식 |
| Security Group rule | source CIDR/SG + target SG + protocol/port | 통신별 | 계약 |
| Launch Template | AMI + instance type + App Security Group + user data | 앱 HA | 공식·계약 |
| Auto Scaling Group | Launch Template + 서로 다른 AZ의 App Subnet들 | 앱 HA | 공식 |
| Target Group | VPC + protocol/port + health path | 앱 HA | 공식·계약 |
| ASG의 Target Group 연결 | Auto Scaling Group + Target Group | 앱 HA | 공식 |
| ALB | 서로 다른 AZ의 public Subnet들 + ALB Security Group | 앱 HA | 실측·공식 |
| HTTPS Listener | ALB + ACM Certificate + Target Group | HTTPS 앱 HA | 공식 |
| 직접 공개 EC2 | AMI + instance type + Subnet + Security Group | 단일 앱 직접 진입 | 실측·공식 |
| Elastic IP association | Elastic IP + EC2의 ENI | 고정 직접 주소 | 공식 |
| 상태 EC2 | AMI + instance type + private Data Subnet + Data Security Group | 영속 Workload | 실측·공식 |
| data EBS Volume | 상태 EC2와 같은 AZ | 영속 Workload | 공식 |
| EBS Attachment | EBS Volume + 상태 EC2 + device name | 영속 Workload | 공식 |
| Instance Profile | IAM Role | AWS API 접근 | 공식 |
| EC2/Launch Template의 identity | Instance Profile | AWS API 접근 | 실측·공식 |

EC2의 primary ENI와 root EBS는 EC2 선언 안에서 암묵 생성할 수 있다. 이 사실은 앱의
data EBS attachment나 명시적인 Subnet·Security Group 참조를 생략해도 된다는 뜻이 아니다.

### 2.3 기능 의존성

| 기능 | 필요한 실행 경로 | 검증 |
|---|---|---|
| 직접 공개 앱 | IGW/route → Public IP/ENI → SG → EC2 → container port | 외부 업무 HTTP 요청 |
| HA 앱 진입 | ALB Listener → Target Group → healthy ASG instance → container port | 각 backend health와 외부 업무 요청 |
| 앱 VM 자동 복구 | 앱 health endpoint → ALB health → ASG health 판단 → 새 EC2 생성·등록 | 한 EC2 중지 중 연속 업무 요청과 교체 확인 |
| 앱→상태 연결 | App SG → Data SG의 내부 port → 상태 EC2 listen port | 앱을 통한 상태 쓰기·조회 |
| 영속성 | EBS attachment → guest filesystem/mount → Docker volume → Workload data path | 쓰기→container/VM 재시작→재조회 |
| 외부 송신 | private route → NAT Gateway 또는 필요한 VPC Endpoint | 요구된 외부 목적지 호출 |
| AWS API 접근 | Instance Profile → metadata credentials → 대상 API 권한 | 최소 권한 API probe |

Load Balancer와 Auto Scaling Group은 앱 계층의 고가용성을 제공하지만, 단일 상태 EC2와
EBS가 장애점으로 남으면 종단 서비스 HA는 충족하지 않는다.

공식 근거: [ALB와 Subnet](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html),
[Auto Scaling과 Load Balancer](https://docs.aws.amazon.com/autoscaling/ec2/userguide/autoscaling-load-balancer.html),
[ASG health check](https://docs.aws.amazon.com/autoscaling/ec2/userguide/health-checks-overview.html).

## 3. Azure

### 3.1 사용하는 리소스와 역할

| 영역 | Azure 리소스 | 역할 |
|---|---|---|
| 위치 | Region, Availability Zone | VM·disk의 배치 위치. VNet/Subnet의 하위 리소스가 아님 |
| container | Resource Group | 평가 실행에서 만든 리소스의 관리 범위 |
| network | Virtual Network, Subnet | Region 범위 network와 주소 구획 |
| network | Route Table/route, NAT Gateway | 필요한 경우 외부 송신 경로 |
| 보안 | Network Security Group과 rule/association | Gateway→앱, 앱→상태 포트만 허용 |
| 직접 진입 | Public IP, NIC IP configuration | 단일 VM 직접 공개 |
| 앱 HA | VM Scale Set, VM profile, automatic repair policy | 여러 Zone의 앱 VM 유지·자동 복구 |
| 진입 | Public IP, Application Gateway v2 | 공개 HTTPS 진입 |
| Gateway 구성 | frontend IP/port, listener, certificate, rule, backend pool, backend setting, probe | HTTPS를 정상 앱 backend로 전달 |
| 실행 | Linux Virtual Machine, NIC, OS Disk | 단일 앱 또는 상태 Workload 실행 |
| 저장 | Managed Disk, Data Disk Attachment | 상태 데이터 보존 |
| 권한 | Managed Identity | Azure API 접근이 필요할 때만 사용 |

### 3.2 생성 의존성

| 생성 대상 | 참조하거나 먼저 준비할 대상 | 조건 | 근거 |
|---|---|---|---|
| VNet | Resource Group + Region + address space | 항상 | 공식 |
| Subnet | VNet + address prefix | 항상 | 실측·공식 |
| ApplicationGatewaySubnet | VNet + 전용 address prefix | Application Gateway | 공식 |
| NSG | Resource Group + Region | 항상 | 공식 |
| NSG rule | source CIDR/Application Security Group/service tag + protocol/port | 통신별 | 계약 |
| NSG Association | NSG + App/Data Subnet 또는 NIC | 항상 | 공식 |
| Public IP | Resource Group + Region/SKU | 직접 진입 또는 Gateway | 공식 |
| Application Gateway | 전용 Subnet + Public IP + SKU | 앱 HA | 공식 |
| Gateway listener/rule | frontend + certificate + backend pool + backend setting | HTTPS 앱 HA | 공식·계약 |
| Gateway probe | backend protocol/port + health path | 앱 HA | 공식·계약 |
| VM Scale Set | VM image/SKU + NIC configuration + App Subnet + App NSG | 앱 HA | 공식 |
| VMSS backend 연결 | VMSS NIC configuration + Gateway Backend Pool | 앱 HA | 공식 |
| Application Health extension | 앱 health endpoint의 protocol/port/path | 앱 HA | 공식·계약 |
| automatic repair policy | VM Scale Set + Application Health extension | 앱 HA | 공식·계약 |
| 직접 공개 NIC | App Subnet + Public IP + App NSG | 단일 앱 직접 진입 | 실측·공식 |
| 상태 NIC | Data Subnet + Data NSG | 영속 Workload | 실측·공식 |
| 상태 VM | 상태 NIC + image + VM SKU + OS Disk | 영속 Workload | 실측·공식 |
| Managed Data Disk | Resource Group + Region/Zone + 크기 | 영속 Workload | 공식 |
| Data Disk Attachment | Managed Disk + 상태 VM + LUN | 영속 Workload | 공식 |
| Managed Identity | VM/VMSS identity 설정 + role assignment | Azure API 접근 | 실측·공식 |

Application Gateway에는 다른 Workload를 함께 넣지 않는 전용 Subnet이 필요하다. Azure의
일반 Public Load Balancer와 Application Gateway의 Subnet 규칙을 하나의 규칙으로 합치지 않는다.

### 3.3 기능 의존성

| 기능 | 필요한 실행 경로 | 검증 |
|---|---|---|
| 직접 공개 앱 | Public IP → NIC → NSG → VM → container port | 외부 업무 HTTP 요청 |
| HA 앱 진입 | Gateway listener/rule → healthy backend pool member → VMSS instance | probe와 외부 업무 요청 |
| 앱 VM 자동 복구 | health endpoint → Application Health extension → VMSS automatic repair | 한 instance 장애 중 연속 요청과 복구 확인 |
| 앱→상태 연결 | App Subnet CIDR 또는 Application Security Group → Data NSG의 내부 port → 상태 NIC/VM | 앱을 통한 상태 쓰기·조회 |
| 영속성 | disk attachment → guest filesystem/mount → Docker volume → data path | 쓰기→container/VM 재시작→재조회 |
| 외부 송신 | Subnet route/NAT → 요구된 외부 목적지 | 외부 의존 API probe |
| Azure API 접근 | Managed Identity endpoint → role assignment → 대상 API | 최소 권한 API probe |

공식 근거: [VNet과 Subnet](https://learn.microsoft.com/en-us/azure/networking/design-guide/vnets-subnets),
[Application Gateway 인프라](https://learn.microsoft.com/en-us/azure/application-gateway/configuration-infrastructure),
[Gateway probe](https://learn.microsoft.com/en-us/azure/application-gateway/application-gateway-probe-overview),
[VMSS 자동 복구](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-automatic-instance-repairs).

## 4. GCP

### 4.1 사용하는 리소스와 역할

| 영역 | GCP 리소스 | 역할 |
|---|---|---|
| 위치 | Region, Zone | Subnetwork·VM·disk 배치 위치 |
| 사전조건 | Project와 Compute Engine API | 모든 Compute 리소스의 관리 범위와 API |
| network | VPC Network, Subnetwork, Route | 전역 network와 Region 주소 구획 |
| network | Cloud Router, Cloud NAT | private VM의 선택적 외부 송신 |
| 보안 | VPC Firewall Rule | LB/health checker→앱, 앱→상태 통신 허용 |
| 직접 진입 | VM network interface의 `access_config`/External IP | 단일 VM 직접 공개 |
| 앱 HA | Instance Template, Regional Managed Instance Group | 여러 Zone의 동일 앱 VM 유지·autohealing |
| 진입 | Global Address, Global Forwarding Rule | 공개 HTTPS frontend |
| LB 경로 | Target HTTPS Proxy, Certificate, URL Map, Backend Service, Health Check | 요청을 정상 MIG backend로 전달 |
| 실행 | Compute Engine Instance, network interface, boot disk | 단일 앱 또는 상태 Workload 실행 |
| 저장 | Persistent Disk, Attached Disk | 상태 데이터 보존 |
| 권한 | Service Account와 IAM binding | Google API 접근이 필요할 때만 사용 |

### 4.2 생성 의존성

| 생성 대상 | 참조하거나 먼저 준비할 대상 | 조건 | 근거 |
|---|---|---|---|
| VPC Network | Project | 항상 | 공식 |
| Subnetwork | VPC Network + Region + CIDR | 항상 | 실측·공식 |
| Firewall Rule | VPC Network + source range/target tag 또는 service account + port | 항상 | 실측·계약 |
| Instance Template | image/machine type + boot disk + network/Subnetwork + App identity/startup | 앱 HA | 공식·계약 |
| Regional MIG | Instance Template + Region + 선택 Zone들 | 앱 HA | 공식 |
| MIG autohealing policy | Managed Instance Group + autohealing Health Check | 앱 HA | 공식·계약 |
| LB Health Check | 앱 protocol/port/health path | 앱 HA | 공식·계약 |
| autohealing Health Check | 앱 protocol/port/health path | 앱 HA | 공식·계약 |
| Backend Service | LB Health Check + Zone별 MIG instance group backend | 앱 HA | 공식 |
| URL Map | Backend Service | L7 앱 HA | 공식 |
| Certificate | 인증서 또는 관리형 인증서용 domain | HTTPS | 공식 |
| Target HTTPS Proxy | URL Map + Certificate | HTTPS | 공식 |
| Global Forwarding Rule | Target HTTPS Proxy + global IP + port 443 | HTTPS | 공식 |
| 직접 공개 VM | boot disk + network interface + Subnetwork + `access_config` + firewall target | 단일 앱 직접 진입 | 실측·공식 |
| 상태 VM | boot disk + private network interface + Data firewall target | 영속 Workload | 실측·공식 |
| data Persistent Disk | 상태 VM과 같은 Zone | 영속 Workload | 공식 |
| Attached Disk | Persistent Disk + 상태 VM | 영속 Workload | 공식 |
| Cloud NAT | Cloud Router + VPC Network + Region/Subnetwork | private 외부 송신 | 공식 |
| VM identity | Service Account + IAM binding | Google API 접근 | 실측·공식 |

GCP global external Application Load Balancer의 frontend는 Subnetwork 안에 배치되는 하나의
리소스가 아니다. Forwarding Rule부터 MIG backend까지 이어지는 여러 native 리소스의
참조 사슬로 표현한다.

### 4.3 기능 의존성

| 기능 | 필요한 실행 경로 | 검증 |
|---|---|---|
| 직접 공개 앱 | default internet route → External IP → Firewall Rule → VM/container port | 외부 업무 HTTP 요청 |
| HA 앱 진입 | Forwarding Rule → HTTPS Proxy → URL Map → Backend Service → healthy MIG instance | health와 외부 업무 요청 |
| 앱 VM 자동 복구 | 별도 autohealing Health Check → Regional MIG → instance 재생성 | 한 instance 장애 중 연속 요청과 복구 확인 |
| 앱→상태 연결 | App tag/service account → Firewall Rule → 상태 VM의 내부 port | 앱을 통한 상태 쓰기·조회 |
| 영속성 | attached Persistent Disk → guest filesystem/mount → Docker volume → data path | 쓰기→container/VM 재시작→재조회 |
| 외부 송신 | private Subnetwork → Cloud NAT 또는 허용된 외부 주소 | 외부 의존 API probe |
| Google API 접근 | VM Service Account → IAM binding → 대상 API | 최소 권한 API probe |

공식 근거: [VPC와 Subnetwork](https://docs.cloud.google.com/vpc/docs/vpc),
[global external Application Load Balancer](https://docs.cloud.google.com/load-balancing/docs/https/setup-global-ext-https-compute),
[Managed Instance Group](https://docs.cloud.google.com/compute/docs/instance-groups),
[MIG autohealing](https://docs.cloud.google.com/compute/docs/instance-groups/autohealing-instances-in-migs).

## 5. 생성 순서 요약

각 CSP에서 이름은 다르지만 실제 생성은 다음 순서를 따른다.

```text
위치와 관리 범위
→ network와 subnet
→ route와 traffic filter
→ image/template와 identity
→ 상태 VM·disk 또는 앱 관리형 VM 그룹
→ Load Balancer frontend/backend/health
→ guest mount와 container 실행
→ 앱 기능 검증
```

Terraform의 암묵 의존성은 실제 resource reference에서 만들고, API가 값을 직접 참조하지
않지만 기능상 선행돼야 하는 관계는 명시적인 `depends_on` 또는 검증 단계의 선행 gate로
표현한다. 생성 순서를 수기 sleep으로 맞추지 않는다.

## 6. 기능 검증 공통 기준

| Gate | 확인하는 것 | 증명하지 못하는 것 |
|---|---|---|
| Terraform Plan | native 리소스와 참조 관계가 계획과 일치 | 실제 생성·앱 동작 |
| apply/ready | CSP가 리소스를 생성하고 health가 준비됨 | 업무 기능·영속성 |
| 업무 기능 | 공개 요청과 내부 상태 연결이 정상 | 장애 대응·성능 목표 |
| persistence | 재시작 뒤 데이터가 보존됨 | 상태 계층 HA |
| app HA | 한 앱 VM 장애 중 요청 지속과 자동 교체 | 단일 상태 Workload를 포함한 종단 HA |
| cleanup | 해당 run이 만든 리소스의 잔여가 0 | 공유·기존 리소스의 상태 |

앱 HA 합격은 리소스 개수로 판정하지 않는다. 고정된 요청을 보내는 동안 한 앱 VM을
중지하고, 허용한 복구시간 내 오류율·지연시간·업무 불변식을 만족하며 CSP 관리형 그룹이
instance를 교체하는지 확인한다.

## 7. 현재 증거와 남은 검증

제품 정본 [claims.json](../app/core/cloudkb/depkb/claims.json)은 프로비저닝 33개와
런타임 12개, 총 45개 claim을 포함한다. 반복 상태는 성공 38개, 실패 5개, 대기 2개다.
실패와 대기 claim은 확정 규칙으로 점수화하지 않는다.

프로비저닝 claim은 32개가 반복됐고 Azure frontend 선택 조건 1개가 대기 상태다.
런타임 인프라 신호의 상세 결과는 다음과 같다.

| CSP | 관찰 관계 | 기능 신호 | 반복 상태 |
|---|---|---|---|
| AWS | Subnet → default route | 외부 HTTPS 송신 | 실패 |
| AWS | Subnet → default route | 외부 TCP 수신 | 성공 |
| AWS | VM → Security Group | 외부 TCP 수신 | 성공 |
| AWS | VM → Public IP | 외부 TCP 수신 | 성공 |
| AWS | VM → workload identity | metadata 자격 증명 | 실패 |
| Azure | Load Balancer → VM | LB 서비스 응답 | 실패 |
| Azure | NIC → Public IP | 외부 TCP 수신 | 성공 |
| Azure | Subnet → NSG | 외부 TCP 수신 | 성공 |
| Azure | VM → Managed Disk | volume write | 대기 |
| GCP | VPC Network → default route | 외부 TCP 수신 | 실패 |
| GCP | VM → Firewall Rule | 외부 TCP 수신 | 실패 |
| GCP | VM → External IP | 외부 TCP 수신 | 성공 |

여기서 실패는 관계가 불필요하다는 결론이 아니다. 동결한 기대 신호를 반복 실행에서
재현하지 못했다는 뜻이므로 원인 확인 전에는 성공이나 실패의 근거로 사용하지 않는다.

기존 CLI 관찰은 VM·NIC·Subnet·network·firewall·public IP·disk와 일반 LB 관계를
주로 다룬다. 이번 문서에 추가한 다음 관계는 공식 문서에서 파생한 설계 제약이며 아직
모두 EasyDep의 실제 생성 실험으로 확인된 결과는 아니다.

- AWS Auto Scaling Group과 ALB/Target Group의 결합
- Azure VM Scale Set의 health 기반 automatic repair와 Application Gateway backend 결합
- GCP Regional MIG의 autohealing과 global external Application Load Balancer 결합

후속 검증은 리소스를 더 열거하는 작업보다 세 CSP에서 같은 앱 HA 기능 경로를 한 번씩
끝까지 실행하는 데 우선순위를 둔다. 각 실행은 `apply → ready → 업무 요청 → 앱 VM 장애
→ 자동 교체 → 업무 재확인 → destroy → 해당 run 잔여 0`을 기록한다.
