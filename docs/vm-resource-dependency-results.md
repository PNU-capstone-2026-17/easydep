# Docker-on-VM 클라우드 리소스 의존성 분석 결과

이 문서는 EasyDep이 현재 보유한 AWS·Azure·GCP의 Docker-on-Linux-VM 리소스 의존성 분석
결과를 사람이 읽을 수 있게 전수 정리한다. 리소스 역할, CSP별 대응, 관측된 56개 관계, 설계에
사용하는 9개 보수적 관계, 영속 디스크·로드밸런서·TLS capability의 실제 Terraform 구성을 모두
포함한다.

정본은 다음 파일이다.

- 전체 관측 claim: [claims.json](../app/core/cloudkb/depkb/claims.json)
- 설계 입력으로 승격한 관계: [official-dependencies.json](../app/core/cloudkb/depkb/official-dependencies.json)
- capability별 CSP 구성: [component-projections.json](../evaluation/research_protocol/definitions/component-projections.json)
- 부하분산 중립 대응: [provider-projections.json](../app/core/cloudkb/depkb/provider-projections.json)
- VM 범위 정의: [scope.py](../app/core/cloudkb/depkb/scope.py)

## 1. 결과를 읽는 방법

### 1.1 리소스 이름은 비교용 이름이다

`vm`, `subnet`, `firewall` 같은 이름은 세 CSP의 제품이 완전히 같다는 뜻이 아니다. 서로 비슷한
역할을 비교하기 위해 EasyDep이 정규화한 이름이다. 실제 배포에서는 CSP별 native 리소스나
리소스 내부 블록으로 바뀐다.

### 1.2 관계에는 세 종류가 있다

| 관계 계열 | 묻는 질문 | 예시 |
|---|---|---|
| 프로비저닝 | B 없이 A를 생성할 수 있는가? | subnet 없이 NIC를 만들 수 있는가? |
| 생명주기 | A가 B를 사용하는 동안 B를 지울 수 있는가? | NIC가 붙은 subnet을 지울 수 있는가? |
| 런타임 | B를 제거하면 정해 둔 기능 신호가 실패하는가? | 공인 IP를 떼면 외부 SSH가 끊기는가? |

### 1.3 판정 용어

| 판정 | 쉬운 뜻 |
|---|---|
| `mandatoryForProvisioning` | 해당 조건에서 대상이 없으면 생성할 수 없음 |
| `conditionalForProvisioning` | 구성 방식에 따라 필수 여부가 달라짐 |
| `notMandatoryForProvisioning` | 대상 없이도 생성 가능함. 기능상 불필요하다는 뜻은 아님 |
| `deleteBlockedWhileAttached` | 사용 중인 대상을 먼저 분리하지 않으면 삭제가 거부됨 |
| `runtimeRequiredForSignal` | 정해 둔 기능 신호에는 필요함 |

`providerDefaulted`는 생략하면 CSP가 기본 리소스를 선택한다는 뜻이고, `providerCreated`는 CSP가
암묵적으로 리소스를 만든다는 뜻이다. `explicitlyAttachable`은 선택적으로 명시 연결할 수 있다는
뜻이다.

### 1.4 증거 상태를 읽는 법

- `confirmed`: 원래 관측에서 claim이 확인됐다.
- `replicated`: 동결된 기대 결과로 재실행도 일치했다.
- `pending`: 재실행이 완료되지 않았다.
- `failed`: 재실행에서 기대 결과를 다시 얻지 못했다. 원 관측을 삭제하지 않지만 강한 일반화에는
  사용하지 않는다.

따라서 `evidenceStatus=confirmed`, `replicationStatus=failed`인 행은 “원 관측은 있으나 반복 확인은
실패했다”로 읽어야 한다.

## 2. 포함 리소스와 역할

### 2.1 공통 역할 사전

| 정규화 이름 | 역할 | 항상 필요한가? |
|---|---|---|
| `network` | 주소 공간과 네트워크 격리 경계를 제공 | CSP 기본 네트워크 사용 여부에 따라 다름 |
| `subnet` | network를 리전·가용 영역 단위의 주소 구역으로 나눔 | NIC 배치에는 실질적으로 필요하지만 기본값 대체 가능성은 CSP마다 다름 |
| `firewall` | 허용·차단할 방향, 프로토콜, 포트를 규정 | 생략 시 기본 보안 규칙이 적용될 수 있음 |
| `nic` | VM을 subnet·network와 연결하는 네트워크 인터페이스 | VM 생성 방식에 따라 명시 또는 암묵 생성 |
| `publicIp` | 인터넷에서 VM이나 진입점으로 접근할 주소 | 사설 배포나 LB 뒤 VM에는 불필요할 수 있음 |
| `publicIPPrefix` | Azure에서 공인 IP 주소 범위를 미리 확보 | Azure LB frontend 선택지 중 하나이며 항상 필요하지 않음 |
| `loadBalancer` | 요청을 한 개 이상의 backend로 전달 | 단일 VM 직접 진입이면 불필요할 수 있음 |
| `vm` | Docker 엔진과 앱 컨테이너가 실행되는 호스트 | 현재 연구의 중심 실행 리소스 |
| `disk` | 부트 또는 영속 데이터를 저장 | 부트 디스크는 필요하지만 별도 데이터 디스크는 선택 |
| `sshKey` | VM 관리 접속에 사용할 공개키 | 다른 접속·관리 방식이면 생략 가능 |
| `workloadIdentity` | VM의 워크로드가 CSP API를 호출할 권한을 획득 | CSP API 접근이 필요할 때만 사용 |
| `defaultRoute` | 보통 `0.0.0.0/0` 트래픽을 인터넷 게이트웨이로 전달 | 외부 통신 기능에는 필요할 수 있으나 VM 생성 자체와는 별개 |

`image`는 VM 부팅 이미지 선택에 필요하지만 현재 활성 claim의 두 끝점에 포함되지 않는다.
`workloadIdentity`, `defaultRoute`, `publicIPPrefix`는 claim 범위에는 있으나 기본 어휘의 모든 CSP에
독립 CRUD 리소스가 있다는 뜻은 아니다.

### 2.2 기본 native 대응

| 역할 | AWS | Azure | GCP |
|---|---|---|---|
| network | VPC (`AWS::EC2::VPC`) | Virtual Network | VPC Network (`Network`) |
| subnet | Subnet (`AWS::EC2::Subnet`) | Virtual Network의 Subnet | Subnetwork |
| firewall | Security Group | Network Security Group | VPC Firewall rule |
| nic | Elastic Network Interface | Network Interface | VM 내부 `NetworkInterface` |
| public IP | Elastic IP | Public IP Address | Address 또는 VM `accessConfig` |
| load balancer 진입점 | ELBv2 Load Balancer | Load Balancer 또는 Application Gateway | Forwarding Rule을 포함한 복수 리소스 구성 |
| VM | EC2 Instance | Virtual Machine | Compute Engine Instance |
| disk | EBS Volume | Managed Disk | Persistent Disk |
| SSH key | EC2 Key Pair | SSH Public Key 또는 VM 설정 | 독립 Compute CRUD가 아니라 metadata·OS Login |
| workload identity | Instance Profile/IAM Role | Managed Identity | Service Account 결합 |
| default route | Route table의 IGW route | Route table·시스템 route | default-internet-gateway route |

이 표는 역할 대응이다. 특히 GCP의 NIC와 SSH key, Azure Application Gateway의 listener·probe처럼
독립 리소스 경계가 다른 항목을 1:1 자원이라고 해석하면 안 된다.

## 3. 전체 관측 claim 56개

아래 표는 [claims.json](../app/core/cloudkb/depkb/claims.json)의 활성 claim을 빠짐없이 옮긴 것이다.
`A → B`는 “A가 B와 맺는 관계를 시험했다”는 뜻이지 항상 생성 순서를 뜻하지 않는다.

### 3.1 AWS: 20개

| 번호 | A → B | 계열·판정 | 조건 또는 관측 의미 | 반복 |
|---:|---|---|---|---|
| A1 | firewall → network | 프로비저닝·선택 | VPC를 생략하면 기본 VPC로 대체 가능 | 성공 |
| A2 | loadBalancer → firewall | 프로비저닝·선택 | 시험한 구성에서 SG 없이 생성 가능; SG는 명시 연결 가능 | 성공 |
| A3 | loadBalancer → subnet | 프로비저닝·필수 | ALB는 서로 다른 AZ의 subnet 2개 이상, NLB는 1개 조건 | 성공 |
| A4 | nic → firewall | 생명주기·삭제 차단 | NIC가 SG를 사용하는 동안 SG 삭제가 거부됨 | 성공 |
| A5 | nic → firewall | 프로비저닝·선택 | 생략하면 VPC default SG가 부착됨 | 성공 |
| A6 | nic → subnet | 생명주기·삭제 차단 | NIC가 존재하는 subnet 삭제가 거부됨 | 성공 |
| A7 | nic → subnet | 프로비저닝·필수 | 명시적 ENI 생성에는 subnet이 필요 | 성공 |
| A8 | subnet → defaultRoute | 런타임·기능 필수 | 기본 route 삭제를 control plane이 막지 않음 | 성공 |
| A9 | subnet → defaultRoute | 런타임·기능 필수 | route 제거 시 guest outbound HTTPS가 실패해야 한다는 반복 | 실패 |
| A10 | subnet → network | 생명주기·삭제 차단 | subnet이 남아 있으면 VPC 삭제가 거부됨 | 성공 |
| A11 | subnet → network | 프로비저닝·필수 | subnet 생성에는 VPC가 필요 | 성공 |
| A12 | vm → disk | 프로비저닝·선택 | 별도 disk를 주지 않아도 AMI의 root volume을 CSP가 생성 | 성공 |
| A13 | vm → firewall | 프로비저닝·선택 | 생략하면 default SG가 적용됨 | 성공 |
| A14 | vm → firewall | 런타임·기능 필수 | 빈 ingress SG로 교체하면 정해 둔 접근 신호가 실패 | 성공 |
| A15 | vm → nic | 프로비저닝·선택 | 명시 ENI 없이도 CSP가 primary ENI를 생성 | 성공 |
| A16 | vm → publicIp | 런타임·기능 필수 | EIP를 분리하면 외부 TCP 22 도달성이 사라짐 | 성공 |
| A17 | vm → sshKey | 프로비저닝·선택 | key pair 없이도 VM 생성 가능 | 성공 |
| A18 | vm → subnet | 프로비저닝·선택 | 생략하면 기본 VPC subnet으로 대체 가능 | 성공 |
| A19 | vm → workloadIdentity | 프로비저닝·선택 | instance profile 없이 VM 생성 가능 | 성공 |
| A20 | vm → workloadIdentity | 런타임·기능 필수 | profile 분리 후 IMDSv2 자격증명 획득 실패를 기대한 반복 | 실패 |

AWS 결과의 핵심은 “VM 생성 시 생략 가능”과 “원하는 기능에 불필요”를 구분하는 것이다. 예를
들어 SG, ENI, subnet을 입력하지 않아도 기본값이나 암묵 생성으로 VM이 만들어질 수 있지만,
EasyDep이 격리된 명시적 배포를 만들 때는 그 리소스와 참조를 설계에 포함할 수 있다.

### 3.2 Azure: 21개

| 번호 | A → B | 계열·판정 | 조건 또는 관측 의미 | 반복 |
|---:|---|---|---|---|
| Z1 | loadBalancer → publicIp | 프로비저닝·선택 | 내부 frontend 등에서는 public IP 없이 생성 가능 | 성공 |
| Z2 | loadBalancer → subnet | 프로비저닝·선택 | public frontend 등에서는 subnet 없이 생성 가능 | 성공 |
| Z3 | loadBalancer → subnet/publicIp/publicIPPrefix | 프로비저닝·조건부 | 시험한 frontend IP 구성에서 셋 중 정확히 하나 선택 | 대기 |
| Z4 | loadBalancer → vm | 런타임·기능 필수 | backend pool에서 NIC 제거 후 LB HTTP 200 실패를 기대 | 실패 |
| Z5 | network → subnet | 프로비저닝·선택 | VNet은 subnet 없이 먼저 생성할 수 있음 | 성공 |
| Z6 | nic → firewall | 생명주기·삭제 차단 | NIC가 NSG를 참조하는 동안 NSG 삭제가 거부됨 | 성공 |
| Z7 | nic → firewall | 프로비저닝·선택 | NIC는 NSG 없이도 생성 가능 | 성공 |
| Z8 | nic → publicIp | 생명주기·삭제 차단 | NIC가 참조하는 Public IP 삭제가 거부됨 | 성공 |
| Z9 | nic → publicIp | 프로비저닝·선택 | 사설 NIC는 Public IP 없이 생성 가능 | 성공 |
| Z10 | nic → publicIp | 런타임·기능 필수 | VM NIC에서 PIP를 떼면 외부 TCP 22 도달성이 사라짐 | 성공 |
| Z11 | nic → subnet | 생명주기·삭제 차단 | NIC가 속한 subnet 삭제가 거부됨 | 성공 |
| Z12 | nic → subnet | 프로비저닝·필수 | NIC의 IP configuration에는 subnet이 필요 | 성공 |
| Z13 | subnet → firewall | 프로비저닝·선택 | subnet은 NSG 없이도 생성 가능 | 성공 |
| Z14 | subnet → firewall | 런타임·기능 필수 | NSG 분리와 Standard PIP 기본 보안의 합성으로 접근 신호 변화 | 성공 |
| Z15 | subnet → network | 생명주기·삭제 차단 | subnet이 남아 있으면 VNet 삭제가 거부됨 | 성공 |
| Z16 | subnet → network | 프로비저닝·필수 | subnet은 VNet에 속해야 함 | 성공 |
| Z17 | vm → disk | 생명주기·삭제 차단 | VM에 붙은 data disk 삭제가 거부됨 | 성공 |
| Z18 | vm → disk | 프로비저닝·선택 | 별도 data disk 없이 VM 생성 가능 | 성공 |
| Z19 | vm → disk | 런타임·기능 필수 | 실행 중 data disk detach 후 guest direct write 실패를 기대 | 대기 |
| Z20 | vm → nic | 프로비저닝·필수 | VM 생성에는 NIC 참조가 필요 | 성공 |
| Z21 | vm → workloadIdentity | 프로비저닝·선택 | Managed Identity 없이 VM 생성 가능 | 성공 |

Azure에서 특히 중요한 차이는 VM과 NIC가 독립 리소스라는 점이다. VM은 NIC를 명시적으로
참조해야 하고, NIC가 다시 subnet을 참조한다. 반면 Application Gateway의 listener, backend pool,
probe 등은 아래 5절에서 보듯 하나의 gateway 리소스 내부 중첩 블록이다.

### 3.3 GCP: 15개

| 번호 | A → B | 계열·판정 | 조건 또는 관측 의미 | 반복 |
|---:|---|---|---|---|
| G1 | firewall → network | 프로비저닝·선택 | network를 생략하면 default network로 대체 가능 | 성공 |
| G2 | loadBalancer → network | 프로비저닝·선택 | external은 직접 불참, internal은 subnet에서 역산 | 성공 |
| G3 | loadBalancer → subnet | 프로비저닝·조건부 | external에는 불필요, internal에는 필요 | 성공 |
| G4 | network → defaultRoute | 런타임·기능 필수 | 자동 default route 삭제를 control plane이 막지 않음 | 실패 |
| G5 | nic → network | 프로비저닝·선택 | network를 명시 연결할 수 있으나 다른 선택 경로가 있음 | 성공 |
| G6 | nic → subnet | 생명주기·삭제 차단 | NIC가 사용하는 subnetwork 삭제가 거부됨 | 성공 |
| G7 | nic → subnet | 프로비저닝·조건부 | custom-mode network는 필수, auto-mode는 리전 subnet 대체 | 성공 |
| G8 | subnet → network | 생명주기·삭제 차단 | subnetwork가 남아 있으면 network 삭제가 거부됨 | 성공 |
| G9 | subnet → network | 프로비저닝·필수 | subnetwork는 network에 속해야 함 | 성공 |
| G10 | vm → disk | 생명주기·삭제 차단 | VM에 연결된 disk 삭제가 거부됨 | 성공 |
| G11 | vm → disk | 프로비저닝·필수 | 부트/연결 disk가 필요하고 disk와 VM zone이 같아야 함 | 성공 |
| G12 | vm → firewall | 런타임·기능 필수 | firewall은 VM 부착 자원이 아니라 network 규칙이며 규칙 제거로 신호 시험 | 실패 |
| G13 | vm → nic | 프로비저닝·필수 | Instance 구성에는 내장 network interface가 필요 | 성공 |
| G14 | vm → publicIp | 런타임·기능 필수 | `accessConfig` 제거 시 외부 TCP 22 도달성이 사라짐 | 성공 |
| G15 | vm → workloadIdentity | 프로비저닝·선택 | Service Account 결합 없이 VM 생성 가능 | 성공 |

GCP의 `loadBalancer`는 단일 제품 리소스가 아니라 여러 리소스의 성좌다. claim의
`loadBalancer → network/subnet`은 Forwarding Rule 계열의 배치 차이를 관측한 것이며, 외부
Application Load Balancer의 전체 구성은 5절의 forwarding rule→proxy→URL map→backend service
연쇄로 표현한다.

### 3.4 전수 개수 확인

| CSP | 프로비저닝 | 생명주기 | 런타임 | 합계 |
|---|---:|---:|---:|---:|
| AWS | 12 | 3 | 5 | 20 |
| Azure | 12 | 5 | 4 | 21 |
| GCP | 9 | 3 | 3 | 15 |
| 합계 | 33 | 11 | 12 | 56 |

## 4. 설계 입력으로 승격한 공식 관계 9개

56개 claim을 모두 “항상 필요한 설계 edge”로 사용하지 않는다. 기본값으로 대체되는 선택 관계,
특정 기능 probe에만 필요한 관계, 반복 확인이 부족한 관계를 무조건 강제하면 오히려 잘못된
토폴로지를 만든다. 현재 [official-dependencies.json](../app/core/cloudkb/depkb/official-dependencies.json)은
다음 9개만 보수적으로 전달한다.

| CSP | 관계 | 설계에서의 의미 | 필수성 상태 |
|---|---|---|---|
| AWS | vm → subnet | VM을 명시 subnet에 배치하는 참조 | 실현에 필요하다고 문서화 |
| AWS | vm → security-group | VM의 허용 통신 규칙을 명시적으로 연결 | 실현에 필요하다고 문서화 |
| AWS | load-balancer → subnet | LB frontend를 subnet에 배치 | 입력 필수로 문서화 |
| AWS | backend-group → virtual-network | target group의 대상 방식에 따라 VPC 결합 | 조건부 필수로 문서화 |
| Azure | subnet → virtual-network | subnet이 소속 VNet을 참조 | 입력 필수로 문서화 |
| Azure | vm → network-interface | VM이 독립 NIC를 참조 | 관계 존재·필수성 문서화 |
| Azure | load-balancer → embedded-components | gateway/LB 내부 frontend·listener·pool 등의 결합 | 존재 확인, 필수성은 별도 미평가 |
| GCP | backend-service → health-check | backend의 건강 상태에 따른 라우팅 | 조건부 필수로 문서화 |
| GCP | backend-service → backend-group | service가 instance group을 backend로 참조 | 존재·필수 확인 |

이 9개는 전체 의존성 우주의 완결 목록이 아니라, 현재 설계 입력으로 직접 사용하기에 근거 상태가
충분한 관계의 부분집합이다. 예를 들어 disk attachment와 TLS certificate 관계는 이 파일이 아니라
capability별 projection과 독립 constraint gate에서 전달한다.

## 5. capability별 CSP 대응

이 절은 사용자의 기능 요구가 실제 Terraform 구성요소로 어떻게 바뀌는지 보여준다.

### 5.1 영속 블록 저장소

공통 의도는 “앱 컨테이너가 쓰는 데이터가 컨테이너나 VM 재시작 뒤에도 남는다”이다.

| 역할 | AWS | Azure | GCP |
|---|---|---|---|
| 데이터 디스크 | `aws_ebs_volume` | `azurerm_managed_disk` | `google_compute_disk` |
| VM 연결 | `aws_volume_attachment` | `azurerm_virtual_machine_data_disk_attachment` | `google_compute_attached_disk` 또는 VM의 `attached_disk` 블록 |
| guest 설정 | 장치 format·mount | 장치 format·mount | 장치 format·mount |

공통 관계는 다음과 같다.

```text
attachment → disk
attachment → vm
guest mount → attachment된 장치
```

추가 제약은 CSP와 구성에 따라 다르다.

- AWS: EBS volume과 VM이 같은 availability zone이어야 한다.
- Azure: managed disk를 VM에 붙인 뒤 guest OS에서 별도로 format·mount해야 한다.
- GCP: 독립 attachment 리소스와 VM 내부 중첩 블록이라는 두 합법 표현을 허용한다.
- 세 CSP 모두: attachment 성공은 앱 경로에 실제 mount됐다는 뜻이 아니다.

따라서 정적 평가에서는 disk와 attachment 참조까지만 확인한다. 실제 성공은 앱이 계약 경로에
데이터를 쓰고, 컨테이너 또는 VM을 재시작한 뒤 같은 데이터를 읽는 기능 gate로 판단한다.

### 5.2 HTTP 다중 VM 부하분산

#### AWS Application Load Balancer

| 구성요소 | Terraform | 역할 |
|---|---|---|
| load balancer | `aws_lb` | subnet에서 요청을 받는 진입점 |
| listener | `aws_lb_listener` | 포트·프로토콜을 받고 전달 규칙을 시작 |
| backend group | `aws_lb_target_group` | backend 포트, 프로토콜, health 설정 보유 |
| membership | `aws_lb_target_group_attachment` | 개별 VM을 target group에 등록 |

```text
load balancer → 여러 subnet
listener → load balancer
listener → target group
target group attachment → target group
target group attachment → VM
```

ALB의 다중 AZ 최소 조건은 `one-to-many`라는 관계 이름에서 추론하지 않고, 서로 다른 AZ의 subnet
2개 이상이라는 별도 배치 제약으로 검사한다.

#### Azure Application Gateway

| 구성요소 | Terraform 표현 | 역할 |
|---|---|---|
| gateway | `azurerm_application_gateway` | L7 요청 처리의 소유 리소스 |
| frontend | 내부 `frontend_ip_configuration` | 요청을 받을 IP 구성 |
| listener | 내부 `http_listener` | frontend·port·protocol 결합 |
| backend pool | 내부 `backend_address_pool` | backend 주소 집합 |
| backend settings | 내부 `backend_http_settings` | backend 포트·protocol·timeout |
| routing rule | 내부 `request_routing_rule` | listener를 pool과 settings에 연결 |
| probe | 내부 `probe` | backend 건강 상태 검사 |
| membership | 독립 association 리소스 | NIC를 backend pool에 등록 |

```text
gateway → 전용 subnet
routing rule → listener
routing rule → backend pool
routing rule → backend settings
backend settings → probe
backend membership → backend pool
backend membership → NIC → VM
```

AWS와 달리 listener, pool, settings, probe, rule은 각각 독립 최상위 리소스가 아니라 하나의
Application Gateway 안에 있는 이름 있는 중첩 블록이다. 평가기는 이를 여러 AWS 리소스와 1:1로
억지 대응시키지 않는다.

#### GCP 글로벌 외부 Application Load Balancer

| 구성요소 | Terraform | 역할 |
|---|---|---|
| forwarding rule | `google_compute_global_forwarding_rule` | 외부 IP·port에서 proxy로 전달 |
| HTTP proxy | `google_compute_target_http_proxy` | HTTP 요청을 URL map으로 전달 |
| URL map | `google_compute_url_map` | host/path 규칙으로 backend 선택 |
| backend service | `google_compute_backend_service` | backend group·health check·정책 결합 |
| backend group | `google_compute_instance_group` | VM 집합 |
| health check | `google_compute_health_check` | backend 사용 가능 여부 판정 |

```text
forwarding rule → target HTTP proxy
target HTTP proxy → URL map
URL map → backend service
backend service → instance group
backend service → health check
instance group → VM
```

GCP는 하나의 `loadBalancer` 타입으로 끝나지 않는다. 중립 `network-load-balancer` 역할을 forwarding
rule과 backend service 등이 부분적으로 나눠 담당한다.

### 5.3 HTTPS와 TLS 종료

HTTPS는 HTTP 부하분산 토폴로지를 유지한 채 listener/proxy와 certificate 결합을 추가하는 것으로
비교한다.

| CSP | 추가·변경 구성 | 관계 |
|---|---|---|
| AWS | HTTPS `aws_lb_listener`, `aws_acm_certificate` 또는 certificate data source | listener → ALB, listener → 기본 certificate |
| Azure | Application Gateway 내부 HTTPS listener와 `ssl_certificate` 블록 | listener → gateway, listener → certificate |
| GCP | `google_compute_target_https_proxy`, Google-managed 또는 self-managed SSL certificate | forwarding rule → HTTPS proxy → URL map, proxy → certificate |

AWS의 기본 인증서가 정확히 하나여야 한다는 조건, Azure listener가 올바른 certificate 이름을
참조한다는 조건, GCP proxy가 URL map과 certificate를 함께 참조한다는 조건은 단순 리소스 존재와
별도로 검사해야 한다. 최종 성공은 실제 TLS handshake와 업무 API 응답으로 확인한다.

## 6. 중립 개념의 다대다 대응

부하분산 projection에서 사용하는 비교 좌표와 native 구성의 관계는 다음과 같다.

| 중립 개념 | AWS | Azure | GCP |
|---|---|---|---|
| `neutral.network-load-balancer` | ALB | Application Gateway | forwarding rule과 backend service가 역할 분담 |
| `neutral.load-balancer-listener` | 독립 listener, certificate 일부 | listener·routing rule·certificate 중첩 블록 | HTTP/HTTPS proxy, URL map, certificate |
| `neutral.load-balancer-backend-group` | target group | backend pool과 backend settings | backend service |
| `neutral.compute-group` | target attachment 조합 | NIC–pool membership 조합 | instance group |
| `neutral.load-balancer-health-check` | target group 내부 health 설정 | gateway 내부 probe | 독립 health check |

이 대응은 일부가 `full`, 다수가 `partial` coverage다. `partial`은 잘못된 매핑이라는 뜻이 아니라,
native 구성요소가 중립 개념의 역할 일부만 담당한다는 뜻이다. 그래서 한 중립 개념이 여러 native
구성요소에 대응하고, certificate처럼 한 native 요소가 listener 기능의 일부를 담당할 수 있다.

## 7. 생성 순서와 삭제 순서의 실용적 해석

모든 배포가 동일한 순서를 강제하는 것은 아니지만 명시적 Docker-on-VM 구성은 보통 다음 방향을
따른다.

```text
network
  → subnet
    → firewall / public IP
      → NIC
        → VM
          → data disk attachment

subnet / public IP
  → load-balancer frontend
    → listener 또는 proxy
      → backend group / backend service
        → VM membership와 health check
```

삭제는 참조 관계를 먼저 분리하고 대체로 역순으로 수행한다. 실제로 subnet–network,
NIC–subnet, NIC–firewall, VM–disk 등 여러 관계에서 사용 중 대상 삭제가 차단됐다. 다만 Terraform의
참조 그래프가 순서를 계산하므로 이 그림을 고정 sleep이나 수동 순서 목록으로 바꾸지 않는다.

## 8. 분석으로 알 수 있는 것과 없는 것

### 알 수 있는 것

- CSP별로 어떤 리소스 또는 내부 구성요소가 서로 참조하는지
- 생략 시 기본값으로 대체되는지, 명시 입력이 필요한지
- 사용 중 삭제가 차단되는 관계인지
- 특정 기능 신호에 필요한 관계인지
- 같은 capability가 CSP마다 어떤 복수 리소스로 실현되는지

### 이것만으로 알 수 없는 것

- 생성된 앱의 업무 로직이 맞는지
- mount 명령이 실제로 성공하고 재시작 뒤 데이터가 남는지
- health check가 통과하고 각 backend가 실제 요청을 처리하는지
- TLS 인증서가 유효하고 handshake가 성공하는지
- 주어진 부하를 견디는 VM 크기인지
- 모든 합법 토폴로지와 모든 VM 관련 CSP 기능을 포괄했는지

따라서 검증은 다음 순서로 분리한다.

```text
구성요소 존재
  → 명시 참조 관계
  → provider validate/plan
  → 실제 apply
  → ready와 업무 API
  → 재시작·장애·TLS 같은 기능 gate
  → destroy와 잔여 리소스 0
```

## 9. 현재 결과의 한계

- 범위는 AWS·Azure·GCP의 Docker-on-Linux-VM이다. Kubernetes, VPN, 서버리스, 관리형 DB·큐·캐시·
  오브젝트 저장소는 포함하지 않는다.
- 전체 56개 중 반복 상태가 `failed` 또는 `pending`인 관계가 있으므로 모든 행을 확정 불변식으로
  사용하지 않는다.
- 9개 공식 관계는 완결 목록이 아니라 보수적으로 승격한 설계 입력이다.
- `persistent-block-storage`, HTTP LB, HTTPS LB는 현재 개발축이지 전체 CNA 요구의 대표 표본이
  아니다.
- provider schema 통과는 cloud apply 또는 앱 기능 성공을 뜻하지 않는다.
- CSP 기본 리소스가 존재하는 계정과 새 격리 network를 만드는 배포의 결과가 다를 수 있다.

현재 정직한 결론은 다음과 같다.

> EasyDep은 제한된 Docker-on-VM 범위에서 56개 벤더 관계 관측을 보존하고, 그중 보수적으로
> 채택한 9개 관계와 capability별 CSP projection을 이용해 VM·네트워크·디스크·부하분산·TLS
> 구성의 누락을 검사할 수 있다. 그러나 이 모델만으로 앱 기능이나 전체 클라우드 모델의 완결성을
> 보장하지 않는다.

## 10. 관련 문서

- 쉬운 전체 흐름: [클라우드 분석과 리소스 추천 안내](cloud-resource-guidance.md)
- 분석 방법의 조작적 정의: [dependency-analysis.md](../app/core/cloudkb/document/dependency-analysis.md)
- VM 연구 범위: [vm-scope.md](../app/core/cloudkb/document/vm-scope.md)
- 정적·기능 평가 경계: [component-projections.md](../evaluation/research_protocol/reports/component-projections.md)
- 실제 측정과 효용: [2026-08-10 연구 결과](research-results-20260810.md)
- 주장별 증거 수준: [연구 주장–증거 연결표](../evaluation/research_protocol/reports/research-claim-evidence-matrix-20260809.md)
