# CSP 중립 최소 의존관계와 CSP별 실현

## 1. 목적과 범위

이 문서는 수강신청 애플리케이션을 AWS·Azure·GCP의 Docker-on-Linux-VM 환경에 배포할 때
고려해야 하는 최소 의존관계를 표현 자료용으로 정리한다. CSP 중립 관계를 먼저 제시하고, 그
아래에 각 CSP의 실제 리소스 경계와 실측 결과를 나열한다.

이 문서에서 `A → B`는 해당 관계 유형에서 **A가 B에 의존한다**는 뜻이다. 생성 의존성,
수명주기 제약, 인프라 기능 의존성, 애플리케이션 기능 의존성은 서로 다른 증거이므로 합쳐서
하나의 `dependsOn` 값으로 표현하지 않는다.

증거 표기는 다음과 같다.

- **실측·반복 성공**: 원 관측과 동결 기대 결과의 반복이 일치했다.
- **실측·반복 실패/대기**: 원 관측은 있으나 반복 확인이 실패했거나 끝나지 않았다.
- **설계 관계**: 공식 문서·스키마 또는 앱 기능상 필요하지만 56개 인프라 claim으로 직접
  확인하지 않았다.

실측 정본은 [claims.json](../../app/core/cloudkb/depkb/claims.json), 사람이 읽는 전체 결과는
[VM 리소스 의존성 실측 결과](vm-resource-dependency-results.md)다.

## 2. 최소 관계 개요

```text
Subnet → Virtual Network
VM → Network Attachment → Subnet
Persistent Disk → VM
Application Storage Path → Attached Disk

# 인터넷 공개 시
Public Ingress → Public Addressing + Traffic Filter + External Route

# 다중 VM·장애 허용 시
Load Balancer → Frontend + VM Backends + Health Check
Multi-VM Application → Shared Transactional State
```

`Network Attachment`, `Public Addressing`, `Traffic Filter`, `Frontend`, `Backend`, `Health Check`는
항상 독립 생성 리소스라는 뜻이 아니다. CSP에 따라 독립 리소스, 다른 리소스의 내장 설정,
여러 native 리소스의 합성으로 실현된다.

## 3. `Subnet → Virtual Network`

Subnet은 가상 네트워크 내부의 배치·주소 구역이다.

쉽게 말하면 Virtual Network는 애플리케이션이 사용하는 **전체 사설 네트워크 경계**이고,
Subnet은 그 안에서 VM을 실제로 배치하는 **더 작은 주소 구역**이다. VM은 Virtual Network에
추상적으로 떠 있는 것이 아니라 어느 Subnet에 연결될지 정해져야 한다.

- Virtual Network는 전체 IP 주소 범위와 네트워크 격리 경계를 제공한다.
- Subnet은 그 범위의 일부를 사용하며 리전·가용영역·라우팅과 연결된다.
- Subnet만 따로 만들 수 없으며, 새 Virtual Network를 만들거나 기존 것을 참조해야 한다.
- Virtual Network를 삭제하려면 그 안의 Subnet과 Subnet을 사용하는 연결을 먼저 정리해야 한다.

이 관계는 단순한 화면상 포함 표현이 아니라 세 CSP에서 공통으로 관측된 강한 생성·삭제
의존성이다. 다만 항상 새 Virtual Network와 Subnet을 생성해야 한다는 뜻은 아니다. 기존 자원을
참조하는 구성도 가능하지만, 그 경우에도 `Subnet belongsTo Virtual Network` 관계는 존재한다.

### AWS

- `EC2 Subnet → VPC`
- VPC 참조 없이 Subnet을 생성할 수 없었다.
- Subnet이 남아 있으면 VPC 삭제가 차단됐다.
- **프로비저닝·수명주기 모두 실측·반복 성공**

### Azure

- `Virtual Network Subnet → Virtual Network`
- Subnet은 Virtual Network에 속해야 했다.
- Subnet이 남아 있으면 Virtual Network 삭제가 차단됐다.
- Virtual Network 자체는 Subnet 없이 먼저 생성할 수 있었다.
- **프로비저닝·수명주기 모두 실측·반복 성공**

### GCP

- `Subnetwork → VPC Network`
- Subnetwork 생성에는 VPC Network가 필요했다.
- Subnetwork가 남아 있으면 VPC Network 삭제가 차단됐다.
- **프로비저닝·수명주기 모두 실측·반복 성공**

## 4. `VM → Network Attachment → Subnet`

VM은 네트워크 연결을 통해 Subnet에 배치된다. NIC의 독립 수명주기 여부는 CSP마다 다르다.

VM이 생성됐다는 사실만으로 네트워크 통신이 가능한 것은 아니다. VM에는 패킷을 주고받는
network interface가 필요하고, 그 interface가 어느 Subnet을 사용하는지 정해져야 한다.

```text
VM
└─ Network Attachment/NIC
   └─ Subnet
      └─ Virtual Network
```

중립 모델의 `Network Attachment`는 사용자가 선택하는 별도 리소스가 아니라 VM과 Subnet의
연결 관계다. AWS와 Azure에서는 독립 NIC 리소스로 나타날 수 있고, GCP에서는 VM 내부
`network_interface` 설정으로 나타날 수 있다. 따라서 EasyDep은 NIC를 항상 같은 native 리소스로
생성한다고 가정하지 않고, `VM이 Subnet에 연결돼야 한다`는 의미만 공통으로 보존한다.

이 관계가 없거나 잘못되면 VM 생성이 거부되거나, CSP 기본 네트워크로 의도치 않게 보완되거나,
VM이 생성돼도 원하는 격리 네트워크에 배치되지 않을 수 있다.

### AWS

- `EC2 Instance → ENI → Subnet`
- 명시적 ENI 생성에는 Subnet이 필수였다.
- VM은 명시적 ENI 없이 primary ENI를 암묵적으로 생성할 수 있었다.
- Subnet을 생략하면 default VPC의 Subnet으로 보완될 수 있었다.
- ENI가 존재하는 동안 Subnet 삭제가 차단됐다.
- **명시 ENI–Subnet과 수명주기 관계는 실측·반복 성공**

### Azure

- `Virtual Machine → Network Interface → Subnet`
- VM 생성에는 독립 Network Interface 참조가 필수였다.
- NIC의 IP configuration에는 Subnet이 필수였다.
- NIC가 연결된 동안 Subnet 삭제가 차단됐다.
- **프로비저닝·수명주기 모두 실측·반복 성공**

### GCP

- `Compute Instance → embedded network_interface → Network/Subnetwork`
- Instance에는 내장 network interface 구성이 필수였다.
- custom-mode에서는 Subnetwork가 필요하지만 auto-mode에서는 리전 Subnetwork가 보완될 수 있었다.
- NIC가 사용하는 Subnetwork 삭제가 차단됐다.
- **VM–NIC와 수명주기는 실측·반복 성공, NIC–Subnet 프로비저닝은 조건부**

## 5. `Persistent Disk → VM`

Disk와 VM은 선택 리소스이고, attachment는 둘을 연결하는 관계다. VM 생성 가능성과 수강신청
데이터의 영속성은 별개다.

Disk는 데이터를 보관하고 VM은 애플리케이션과 DB 프로세스를 실행한다. Disk를 생성하기만
해서는 VM이 사용할 수 없으며, 특정 VM에 연결하는 attachment 관계가 필요하다.

```text
Persistent Disk ─ attachesTo → VM
```

여기서 attachment는 중립 선택 리소스가 아니다. CSP에 따라 독립 attachment 리소스 또는 VM의
내장 disk 설정으로 실현되는 관계다. 또한 VM의 부트 Disk와 수강신청 데이터를 저장하는 data
Disk를 구분해야 한다. 부트 Disk가 존재한다고 신청 데이터가 별도 영속 경로에 저장된다는 뜻은
아니다.

- **생성 관점:** VM이 별도 data Disk 없이 만들어질 수 있는가를 본다.
- **수명주기 관점:** 연결된 Disk를 그대로 삭제할 수 있는가를 본다.
- **기능 관점:** 연결 해제 후에도 DB 쓰기와 업무 데이터 보존이 가능한가를 본다.

수강신청 도메인에서는 신청·취소·대기열 상태를 재시작 뒤에도 보존해야 하므로 별도 영속 저장
경로가 필요하다. 그러나 이것은 앱 기능 요구에서 나온 판단이지, 모든 VM의 프로비저닝에 data
Disk가 필수라는 뜻은 아니다.

### AWS

- `EBS Volume → EC2 Instance` attachment로 실현된다.
- 별도 data disk 없이 VM을 생성할 수 있었고 AMI의 root volume이 생성됐다.
- **별도 data disk가 프로비저닝 필수는 아니라는 결과는 실측·반복 성공**
- Disk를 포맷·마운트하고 앱 데이터가 재시작 뒤에도 남는지는 56개 claim에서 측정하지 않았다.

### Azure

- `Managed Disk → Virtual Machine` attachment로 실현된다.
- 별도 data disk 없이 VM을 생성할 수 있었다.
- VM에 연결된 data disk는 먼저 분리하지 않으면 삭제할 수 없었다.
- Disk 분리 후 guest write 실패를 관측했으나 반복은 대기 상태다.
- **프로비저닝 선택·수명주기 차단은 실측·반복 성공, volume-write는 반복 대기**

### GCP

- `Persistent Disk → Compute Instance`의 boot/attached disk 설정으로 실현된다.
- 측정한 구성에서는 VM에 disk가 필수였고 VM과 disk의 zone이 같아야 했다.
- 연결된 Persistent Disk 삭제가 차단됐다.
- **프로비저닝·수명주기 모두 실측·반복 성공**

## 6. `Public Ingress → Public Addressing`

인터넷 공개 방식에서는 외부 주소가 VM의 네트워크 endpoint 또는 Load Balancer frontend에
연결돼야 한다. `Public Ingress`는 capability이며 배포 리소스가 아니다.

`Public Ingress`는 “인터넷 사용자가 서비스에 들어올 수 있어야 한다”는 요구다. `Public
Addressing`은 그 요구를 실현하기 위해 외부에서 찾을 수 있는 주소를 VM의 NIC 또는 Load
Balancer의 frontend에 연결하는 방식이다.

```text
Internet
└─ Public Addressing
   └─ VM NIC 또는 Load Balancer Frontend
```

Public Address는 CSP에 따라 독립적으로 예약하는 리소스일 수도 있고, NIC의 내장 설정이나 자동
할당 값일 수도 있다. 따라서 중립 모델에서는 Public IP를 항상 독립 선택 리소스로 고정하지 않는다.

공개 주소가 없으면 사설 네트워크 내부 통신은 가능해도 인터넷 사용자가 해당 endpoint를 직접
찾아올 수 없다. 반대로 공개 주소만 있다고 서비스가 동작하는 것도 아니다. 외부 경로, 허용 규칙,
앱의 listen port가 함께 맞아야 한다.

### AWS

- `Elastic IP/Public IPv4 → ENI 또는 EC2 Instance`
- 실행 중인 VM에서 Elastic IP를 분리한 뒤 외부 TCP 도달이 실패했다.
- **인바운드 TCP 기능 의존성 실측·반복 성공**

### Azure

- `Public IP Address → NIC IP Configuration 또는 Load Balancer Frontend`
- 사설 NIC는 Public IP 없이 생성할 수 있었다.
- NIC가 참조하는 Public IP는 연결을 해소하기 전 삭제가 차단됐다.
- 실행 중 NIC에서 Public IP를 분리한 뒤 외부 TCP 도달이 실패했다.
- **생성 선택·수명주기·인바운드 TCP 모두 실측·반복 성공**

### GCP

- `External IP/accessConfig → Instance network_interface`
- Public address가 독립 Address일 수도 있고 VM 내부 `accessConfig`일 수도 있다.
- 실행 중 `accessConfig`를 제거한 뒤 외부 TCP 도달이 실패했다.
- **인바운드 TCP 기능 의존성 실측·반복 성공**

## 7. `Public Ingress → Traffic Filter + External Route`

공개 주소만 존재한다고 외부 요청이 도달하는 것은 아니다. 경로와 허용 규칙이 함께 맞아야 한다.

쉽게 비유하면 Public Address가 건물의 주소라면, External Route는 그 건물까지 이어지는 길이고,
Traffic Filter는 출입문에서 어떤 요청을 통과시킬지 정하는 규칙이다.

- **External Route:** 인터넷과 workload Subnet 사이에 패킷이 오갈 경로를 제공한다.
- **Traffic Filter:** 프로토콜, 포트, 출발지와 대상을 기준으로 필요한 트래픽만 허용한다.
- **앱 포트 바인딩:** 허용된 트래픽을 실제 애플리케이션 프로세스가 받아야 한다.

예를 들어 HTTPS 수강신청 서비스라면 외부에서는 TCP 443만 허용하고, Load Balancer를 사용할
때는 VM의 앱 포트를 인터넷 전체가 아니라 Load Balancer 경로에서만 허용하는 것이 바람직하다.

Traffic Filter는 중립적으로 필요한 통제 기능이지만, 반드시 `Security Policy`라는 독립 리소스로
구현되는 것은 아니다. AWS Security Group, Azure NSG, GCP VPC Firewall Rule은 적용 범위와
연결 방식이 다르므로 CSP projection에서 구체화한다.

### AWS

- `Security Group ingress → VM/ENI의 인바운드 TCP`
- VM을 빈 ingress Security Group으로 교체한 뒤 외부 TCP가 실패했다.
- `Subnet default route → Internet Gateway` 제거 후 inbound TCP가 실패했다.
- **Security Group과 inbound route는 실측·반복 성공**
- 동일 route의 egress HTTPS 반복은 실패했으므로 egress 일반화에는 사용하지 않는다.

### Azure

- `Network Security Group → NIC 또는 Subnet`
- 측정한 사례에서는 Subnet에서 NSG를 분리한 뒤 외부 TCP가 실패했다.
- **실측·반복 성공**이지만 NSG 부재와 Standard Public IP의 secure-by-default 동작이
  결합된 결과이므로 NSG 하나의 독립 효과로 해석하지 않는다.

### GCP

- `VPC Firewall Rule → Network 범위의 대상 VM`
- Firewall Rule 삭제 후 외부 TCP 실패를 관측했으나 반복은 실패했다.
- default-internet-gateway route 제거 후 외부 TCP 실패도 반복은 실패했다.
- **원 관측은 있으나 반복 실패**이므로 보수적 설계·기능 probe 대상으로만 사용한다.

## 8. `Load Balancer → Frontend + VM Backends + Health Check`

Load Balancer는 다중 VM 또는 단일 VM 장애 허용 요구가 있을 때만 선택한다.

Load Balancer는 외부 요청을 받는 진입점과 요청을 처리할 VM 목록 사이에서 트래픽을 분배한다.
Load Balancer 리소스 하나만 생성해서는 이 기능이 완성되지 않는다.

```text
사용자 요청
  → Frontend/Listener
  → Load Balancer 규칙
  → Backend 등록
  → 정상 상태의 VM
```

- **Frontend:** 요청을 받을 주소와 프로토콜·포트를 정의한다.
- **Listener/Rule:** 받은 요청을 어느 backend로 전달할지 결정한다.
- **Backend:** 트래픽을 받을 VM 또는 VM 연결을 등록한다.
- **Health Check:** 요청을 보낼 수 있는 정상 VM인지 확인한다.

backend 등록이 빠지면 Load Balancer는 존재해도 전달할 VM을 찾을 수 없다. Health Check가 앱의
실제 상태를 확인하지 못하면 정상 VM을 제외하거나 고장 난 VM에 계속 요청을 보낼 수 있다.

Frontend, Listener, Backend, Health Check는 중립 모델의 구성 역할이다. CSP에 따라 LB 내부
설정이거나 여러 독립 native 리소스일 수 있으므로 모두를 같은 수준의 선택 리소스로 취급하지
않는다.

### AWS

- `Load Balancer → Subnet`
- 측정한 ALB는 서로 다른 AZ의 Subnet 2개 이상, NLB는 Subnet 1개가 필요했다.
- 측정한 NLB 구성은 Security Group 없이 생성할 수 있었다.
- **LB–Subnet 프로비저닝은 실측·반복 성공**

### Azure

- `Load Balancer Frontend → Subnet | Public IP | Public IP Prefix`
- public frontend는 Subnet 없이, internal frontend는 Public IP 없이 구성할 수 있었다.
- 측정한 frontend 구성에서 세 대상 중 정확히 하나를 선택하는 조건부 관계는 반복 대기다.
- backend pool에서 VM NIC를 제거한 뒤 frontend HTTP 200 실패를 관측했으나 반복은 실패했다.
- **frontend 개별 선택 결과는 반복 성공, 통합 조건은 대기, LB–VM 기능은 반복 실패**

### GCP

- External Load Balancer는 Subnet 직접 결합이 불필요할 수 있고, internal 방식은 Subnet이 필요하다.
- forwarding rule, proxy, backend service, backend group, health check 등 여러 native 리소스가
  하나의 중립 Load Balancer 기능을 나눠 실현한다.
- **LB–Subnet 조건부 관계는 실측·반복 성공**
- 전체 LB 구성요소와 앱 업무 기능의 종단 성공은 56개 claim으로 직접 확인하지 않았다.

## 9. `Application Storage Path → Attached Disk`

이 관계는 클라우드 리소스 생성 관계가 아니라 앱–실행환경의 기능 계약이다.

클라우드 관점에서 Disk attachment가 성공해도 운영체제와 애플리케이션이 그 Disk를 실제 데이터
경로로 사용하지 않으면 수강신청 데이터는 보존되지 않는다. 다음 연결이 모두 이어져야 한다.

```text
Cloud Disk
  → VM attachment
  → guest OS device
  → filesystem format
  → host mount path
  → container volume
  → DB/Application data path
```

예를 들어 Disk를 `/mnt/data`에 mount했는데 DB가 컨테이너 내부 임시 경로에 기록하면 Disk는
정상 생성·연결돼도 재시작 후 데이터가 사라질 수 있다. 따라서 이 관계는 Terraform 정적 검사만이
아니라 실제 쓰기, 컨테이너 재시작, VM 재시작, 업무 데이터 재조회로 검증해야 한다.

### AWS·Azure·GCP 공통

- Disk를 VM에 연결한다.
- guest OS에서 Disk를 포맷하고 mount한다.
- mount 경로와 DB 또는 애플리케이션 저장 경로를 일치시킨다.
- 컨테이너 재시작과 VM 재시작 뒤 업무 데이터를 다시 조회한다.

56개 claim은 `volume-write` 같은 인프라 신호만 일부 측정했다. 재시작 뒤 수강신청 데이터가
보존되는지는 별도 앱 기능 시험으로 검증해야 한다.

## 10. `Multi-VM Application → Shared Transactional State`

이 관계는 수강신청 도메인의 핵심 앱 기능 의존성이다.

수강신청에서 가장 중요한 것은 VM 수가 아니라 모든 요청이 하나의 일관된 정원·신청 상태를
기준으로 처리되는 것이다. VM이 두 대인데 각 VM이 서로 다른 로컬 DB나 Disk를 사용하면 같은
강좌의 남은 좌석 수를 다르게 볼 수 있다.

```text
VM 1 ─┐
      ├─ 동일한 트랜잭션 상태
VM 2 ─┘
```

Load Balancer는 요청을 분배할 뿐 데이터 일관성을 제공하지 않는다. 여러 VM에서 다음 조건이
함께 필요하다.

- 모든 인스턴스가 동일한 DB 상태를 읽고 갱신한다.
- 하나의 좌석을 여러 요청이 동시에 차지하지 못하게 한다.
- 중복 신청을 고유 제약이나 동시성 제어로 차단한다.
- 트랜잭션 실패 시 정원 감소와 신청 기록이 부분적으로 남지 않아야 한다.

따라서 `VM 2대 + Load Balancer + VM별 Disk`는 인프라상 생성 가능해도 고가용 수강신청 기능을
충족하지 않는다. 공유 상태 실현 방법이 없으면 사용자에게 질문하거나 지원 불가로 판정해야 한다.

### AWS·Azure·GCP 공통

- 모든 앱 인스턴스가 동일한 수강신청 상태를 읽고 갱신해야 한다.
- 정원 초과와 중복 신청을 막기 위한 트랜잭션·고유 제약 또는 동시성 제어가 필요하다.
- VM마다 개별 Disk를 연결하는 구성은 공유 상태를 제공하지 않는다.

현재 연구 범위는 관리형 DB를 제외한다. 따라서 별도 DB workload와 영속 Disk를 설계하지 못한
다중 VM 구성은 이 capability를 충족한다고 판정하면 안 된다. 이 관계는 현재 56개 인프라
claim으로 직접 측정되지 않았으며, 고정된 동시 요청 업무 시험이 필요하다.

## 11. 해석 한계

- `notMandatoryForProvisioning`은 CSP 기본값이나 암묵 생성이 가능하다는 뜻이지 기능상
  불필요하다는 뜻이 아니다.
- 동일 중립 관계도 CSP와 구성 방식에 따라 독립 리소스, 내장 설정, 합성 리소스로 달라진다.
- 반복 실패·대기 claim은 확정 불변식으로 사용하지 않는다.
- 인프라 TCP·HTTP·volume-write 성공을 수강신청 업무 기능 성공으로 간주하지 않는다.
- 전체 판정은 `provider validate → apply → 인프라 신호 → 업무 기능 → cleanup`을 분리한다.
