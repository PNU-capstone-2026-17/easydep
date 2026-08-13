# Docker-on-VM 논리 배포 토폴로지 결정

> 상태: 현재 Docker-on-VM 배포 결정, CSP별 native 리소스 의존성, VM 추천과 단계별 검증의
> 기준 문서다. 현재 구현 상태와 실행 순서는 [현재 시스템 상태](current-system-status.md)를 따른다.
> 다중 Region 배치의 표현 가능성은 유지하되 현재 adapter에서는 `unsupported`로 처리한다.

## 1. 목적과 범위

이 문서는 EasyDep이 요구사항과 설계 산출물로부터 Docker-on-VM 배포 구조를 정하는
기준을 설명한다. 현재 범위는 다음과 같다.

- AWS·Azure·GCP
- Linux VM과 Docker
- 하나 이상의 컨테이너 Workload
- Workload가 필요로 하는 영속 블록 Volume
- 직접 진입 또는 Load Balancer 진입
- 단일·다중 VM, Zone과 Region 배치
- 현재 구현 목표 runtime: React를 포함한 Spring Boot, 자체 운영 PostgreSQL
- 앱 고가용성을 위한 CSP 관리형 VM 그룹과 Load Balancer

Kubernetes, 서버리스, 관리형 데이터베이스, 자체 구현 PostgreSQL 복제와 다중 Region
실제 배포는 제외한다. 따라서 앱 계층 HA는 지원 목표지만 영속 상태 계층 HA는 아니다.

토폴로지는 배포 요소와 이들의 배치·연결을 나타내는 그래프다. 이러한 용법은
서비스를 노드와 관계의 그래프로 표현하는
[OASIS TOSCA 2.0](https://docs.oasis-open.org/tosca/TOSCA/v2.0/TOSCA-v2.0.html)과
부합한다. EasyDep은 TOSCA 전체를 구현하지 않고 현재 범위에 필요한 요소만 사용한다.

## 2. 현재 구현과 목표의 구분

### 2.1 현재 구현

현재 구현기는 React/Vite 프론트엔드와 Spring Boot 백엔드를 생성하고 각각 빌드한다.
하지만 배포 Dockerfile에는 Spring Boot JAR만 들어가며 데이터는 H2가 처리한다.

```text
현재 실제 배포
→ Spring Boot JAR 1개
→ React dist 미포함
→ 별도 PostgreSQL container 없음
```

### 2.2 목표 runtime

현재 먼저 지원할 runtime은 다음 두 가지다.

| runtime | 현재 목표 |
|---|---|
| `springBootBundle` | React dist를 포함한 Spring Boot image |
| `postgresql` | 영속 Volume을 사용하는 PostgreSQL container |

이는 Workload가 반드시 둘이어야 한다는 뜻이 아니다. 독립 실행 단위가 더 있으면
같은 Workload 구조로 표현한다. 다만 표현할 수 있다는 사실을 곧바로 생성·운영 지원으로
간주하지 않는다. 검증하지 않은 runtime은 `unsupported`로 반환한다.

## 3. 최소 토폴로지 요소

현재 범위에서 필요한 요소는 다음과 같다.

| 요소 | 의미 |
|---|---|
| `Workload` | 독립적으로 실행·중지·복제·배치하는 container 단위 |
| `Connection` | Workload 사이의 통신 |
| `Endpoint` | 외부 또는 내부에서 Workload로 들어오는 진입점 |
| `Compute` | Workload instance가 실행되는 VM |
| `Volume` | 영속성이 필요한 Workload instance가 소유하는 디스크 |
| `LoadBalancer` | 여러 대상 instance로 요청을 전달하는 조건부 진입 자원 |

Region, Zone, VPC/VNet과 Subnet은 각각 실제 위치와 네트워크 소속 값으로 기록한다.
이를 다시 하나의 범용 중간 노드로 감싸지 않고, 선택된 CSP의 배포 다이어그램에서
영역이나 꼬리표로 표시한다.

### 3.1 Workload 경계

클래스 수, BCE stereotype 수 또는 패키지 수로 Workload를 나누지 않는다. 다음 중
하나 이상의 설계 근거가 있을 때만 독립 Workload로 본다.

- 별도 실행 artifact 또는 container image가 필요함
- 독립적인 시작·중지·확장 수명주기가 필요함
- 독립 Endpoint나 내부 Connection을 소유함
- 다른 VM 또는 위치에 배치해야 함

근거가 없으면 LLM이 임의의 마이크로서비스를 만들지 않는다. 현재 목표에서는
React를 Spring Boot에 포함하므로 둘은 하나의 Workload다.

### 3.2 영속성

영속성은 Workload의 역할 이름이 아니라 `persistence.required`로 표현한다.

```text
persistence.required = false
→ 전용 영속 Volume 없음

persistence.required = true
→ 각 Workload instance에 별도 영속 Volume 연결
```

현재 범위에서는 여러 active instance가 하나의 블록 Volume을 공유하지 않는다.
영속 Workload를 여러 active instance로 만들려면 해당 runtime의 데이터 복제·조정
방법이 필요하다. 검증된 방법이 없으면 `unsupported`로 반환한다.

PostgreSQL은 영속 Workload의 한 runtime일 뿐이다. PostgreSQL 복제 규칙을 다른
영속 Workload에 자동 적용하지 않는다.

## 4. 최소 결정 모델

다음 JSON은 필요한 의미를 보여주는 예시이며 고정 스키마가 아니다.

```json
{
  "workloads": [
    {
      "id": "web",
      "runtime": "springBootBundle",
      "activeReplicas": 2,
      "persistence": {
        "required": false,
        "mountPath": null
      },
      "regionRefs": [],
      "availability": "singleZone"
    },
    {
      "id": "records",
      "runtime": "postgresql",
      "activeReplicas": 1,
      "persistence": {
        "required": true,
        "mountPath": null
      },
      "regionRefs": [],
      "availability": "none"
    }
  ],
  "placements": [
    {
      "workloadRefs": ["web", "records"],
      "mode": "separateCompute"
    }
  ],
  "connections": [
    {
      "from": "web",
      "to": "records",
      "visibility": "internalOnly",
      "protocol": "tcp",
      "port": 5432
    }
  ],
  "endpoints": [
    {
      "id": "public-web",
      "target": "web",
      "exposure": "public",
      "protocol": "https",
      "ingress": "loadBalanced"
    }
  ]
}
```

위 JSON은 CSP와 무관한 Workload 의도까지만 표현한다. 이것만으로는 최종 AWS 배포
다이어그램을 그릴 수 없다. 선택 CSP가 AWS로 확정되면 같은 Workload 의도를 다음과
같은 `ResourcePlan`으로 구체화해야 한다. 아래 역시 필수 의미를 설명하는 예시이며
고정 스키마가 아니다.

```json
{
  "provider": "aws",
  "deploymentScope": {
    "regions": ["ap-northeast-2"]
  },
  "resources": [
    {
      "id": "vpc-main",
      "kind": "network",
      "providerType": "aws_vpc",
      "scope": "regional",
      "region": "ap-northeast-2",
      "cidr": "10.0.0.0/16"
    },
    {
      "id": "subnet-public-a",
      "kind": "subnet",
      "providerType": "aws_subnet",
      "scope": "zonal",
      "region": "ap-northeast-2",
      "zone": "ap-northeast-2a",
      "cidr": "10.0.1.0/24"
    },
    {
      "id": "subnet-public-b",
      "kind": "subnet",
      "providerType": "aws_subnet",
      "scope": "zonal",
      "region": "ap-northeast-2",
      "zone": "ap-northeast-2b",
      "cidr": "10.0.2.0/24"
    },
    {
      "id": "subnet-app-a",
      "kind": "subnet",
      "providerType": "aws_subnet",
      "scope": "zonal",
      "region": "ap-northeast-2",
      "zone": "ap-northeast-2a",
      "cidr": "10.0.11.0/24"
    },
    {
      "id": "subnet-app-b",
      "kind": "subnet",
      "providerType": "aws_subnet",
      "scope": "zonal",
      "region": "ap-northeast-2",
      "zone": "ap-northeast-2b",
      "cidr": "10.0.12.0/24"
    },
    {
      "id": "subnet-data-a",
      "kind": "subnet",
      "providerType": "aws_subnet",
      "scope": "zonal",
      "region": "ap-northeast-2",
      "zone": "ap-northeast-2a",
      "cidr": "10.0.21.0/24"
    },
    {
      "id": "alb-public",
      "kind": "loadBalancer",
      "providerType": "aws_lb",
      "scope": "regional",
      "region": "ap-northeast-2",
      "subnetRefs": ["subnet-public-a", "subnet-public-b"],
      "logicalRef": "public-web"
    },
    {
      "id": "template-web",
      "kind": "computeTemplate",
      "providerType": "aws_launch_template",
      "scope": "regional",
      "region": "ap-northeast-2"
    },
    {
      "id": "group-web",
      "kind": "managedComputeGroup",
      "providerType": "aws_autoscaling_group",
      "scope": "regional",
      "region": "ap-northeast-2",
      "templateRef": "template-web",
      "subnetRefs": ["subnet-app-a", "subnet-app-b"],
      "desiredCapacity": 2,
      "healthSourceRef": "alb-public"
    },
    {
      "id": "vm-records-a",
      "kind": "compute",
      "providerType": "aws_instance",
      "scope": "zonal",
      "region": "ap-northeast-2",
      "zone": "ap-northeast-2a",
      "subnetRef": "subnet-data-a"
    },
    {
      "id": "volume-records-a",
      "kind": "volume",
      "providerType": "aws_ebs_volume",
      "scope": "zonal",
      "region": "ap-northeast-2",
      "zone": "ap-northeast-2a",
      "logicalRef": "records.persistence"
    }
  ],
  "allocations": [
    {"id": "web-group", "workloadRef": "web", "replicaCount": 2, "computeGroupRef": "group-web"},
    {"id": "records-1", "workloadRef": "records", "replicaIndex": 1, "computeRef": "vm-records-a"}
  ],
  "relations": [
    {"type": "belongsTo", "from": "subnet-public-a", "to": "vpc-main"},
    {"type": "belongsTo", "from": "subnet-public-b", "to": "vpc-main"},
    {"type": "belongsTo", "from": "subnet-app-a", "to": "vpc-main"},
    {"type": "belongsTo", "from": "subnet-app-b", "to": "vpc-main"},
    {"type": "belongsTo", "from": "subnet-data-a", "to": "vpc-main"},
    {"type": "instantiates", "from": "group-web", "to": "template-web"},
    {"type": "attaches", "from": "volume-records-a", "to": "vm-records-a"},
    {"type": "routesTo", "from": "alb-public", "to": "web"},
    {"type": "connectsTo", "from": "web", "to": "records", "protocol": "tcp", "port": 5432}
  ]
}
```

Internet Gateway, Route Table, Security Group, ALB Listener·Target Group·Health Check와
certificate binding은 이 예시에서 생략한 AWS 보조 리소스다. Auto Scaling Group이
만드는 개별 EC2는 안정적인 계획 ID로 열거하지 않고 desired capacity, Subnet과 health
정책으로 표현한다. 실제 `ResourcePlan`과
IaC에서는 각각 `projectionRuleId` 또는 논리 요소의 `logicalRef`를 붙여 보존한다.
따라서 다이어그램 renderer는 위의 배치 경계와 핵심 트래픽 경로를 그릴 수 있고,
IaC generator는 그림에서 접은 보조 리소스까지 생성할 수 있다.

모든 비기본 결정에는 다음 중 하나 이상의 근거를 연결한다.

- 사용자 요구사항
- 설계 또는 runtime contract에서 확인한 사실
- 다른 결정에서 파생된 제약
- 공식 CSP 제약
- 사용자가 적용에 동의한 프로젝트 정책

결과가 달라질 수 있는데 근거가 부족하면 추측하지 않고 `needsInput`으로 남긴다.

### 4.1 `activeReplicas`

정상 상태에서 요청이나 작업을 수행하는 동일 Workload instance 수다.

- 명시적 복제·장애 허용 요구가 없으면 1이다.
- 처리량만 있고 수가 없으면 LLM이 추측하지 않는다.
- 직접 배포에서는 active replica 하나를 VM 하나에 배치한다.
- 앱 HA에서는 replica 수를 CSP 관리형 VM 그룹의 desired capacity로 보존한다.
- 관리형 그룹이 교체하는 개별 VM ID를 고정 토폴로지 식별자로 사용하지 않는다.
- 동일 VM의 다중 replica는 포트와 로컬 라우팅 문제가 있어 제외한다.

### 4.2 `placements`

- `coLocate`: 지정한 단일 replica Workload들을 같은 VM에 배치
- `separateCompute`: 지정한 Workload들을 서로 다른 VM에 배치

어느 Workload라도 active replica가 여러 개이면 현재
범위에서 `coLocate`를 사용하지 않는다.

### 4.3 `connections`와 `endpoints`

Connection은 Workload 간 통신이고 Endpoint는 진입점이다. `App→DB`처럼 특정 역할
이름을 형식에 고정하지 않는다. protocol과 port는 요구사항이나 runtime contract에서
가져오며 근거가 없으면 임의로 만들지 않는다.

```text
공개 단일 Endpoint + 대상 activeReplicas >= 2
→ loadBalanced

대상 activeReplicas = 1 + 명시적 LB 요구 없음
→ direct
```

### 4.4 위치와 가용성

`regionRefs`는 배포 위치이고 `availability`는 견뎌야 할 장애 범위다.

```text
"한국 리전에 배포"
→ 위치 제약

"한 Zone이 중단돼도 해당 Workload instance 유지"
→ availability = singleZone
```

| 값 | 필요한 배치 |
|---|---|
| `none` | 분산 요구 없음 |
| `singleCompute` | 서로 다른 VM에 active replica 2개 이상 |
| `singleZone` | 같은 Region의 서로 다른 Zone에 active replica 2개 이상 |
| `singleRegion` | 서로 다른 Region에 active replica 2개 이상 |

정확한 CSP Region이 주어지면 보존한다. `korea`처럼 지역만 주어지면 선택된 CSP의
공식 Region 목록에서 후보를 확인한다. 단순히 “고가용성”만 있으면 장애 범위를
사용자에게 묻는다.

이 값은 대상 Workload instance가 남는지만 뜻한다. Endpoint, 연결된 Workload와
상태까지 포함한 전체 업무 기능의 지속을 보장하지 않는다.

### 4.5 영속 Workload의 가용성 경계

현재 자체 운영 영속 Workload는 `activeReplicas = 1`과 전용 Volume 하나만 허용한다.
앱 replica를 늘리거나 CSP 관리형 VM 그룹을 사용해도 영속 상태의 복제·승격·연결 전환은
생기지 않는다. 영속 상태 HA 요구는 관리형 데이터베이스를 범위에 포함하기 전까지
`unsupported`로 반환한다.

## 5. 파생 규칙

### 5.1 VM과 Volume

```text
active replica 하나
→ VM 하나

stateless active replica 둘 이상 + 앱 HA
→ CSP 관리형 VM 그룹 하나
→ desired capacity = active replica 수
→ 선택한 여러 Zone에 instance 분산

persistence.required = true
→ 해당 instance의 Volume 하나

두 단일 replica Workload + coLocate
→ VM 하나 공유

```

단일 배포의 VM 수와 Volume 수, HA 배포의 desired capacity는 위 규칙의 결과이지 별도의
자유 선택값이 아니다. 직접 관리하는 instance는 ID로 남기고 관리형 그룹의 교체 가능한
앱 VM은 그룹과 replica 수로 남긴다.

```json
{
  "instances": [
    {
      "id": "web-1",
      "workloadRef": "web",
      "role": "active",
      "computeRef": "vm-1"
    },
    {
      "id": "records-primary",
      "workloadRef": "records",
      "role": "primary",
      "computeRef": "vm-2",
      "volumeRef": "volume-2"
    }
  ]
}
```

### 5.2 Zone과 Region

`singleCompute`, `singleZone`, `singleRegion`을 다시 범용 장애 영역 객체로 만들지
않는다. 각각 VM, Zone, Region 배치 규칙으로 직접 변환한다.

다중 Region에는 다음이 별도로 필요하다.

1. Region별 VM과 Workload
2. 전역 진입 또는 DNS
3. Region 간 내부 Connection
4. 영속 데이터의 복구 방법

현재 EasyDep은 다중 Region 배치를 모델로 표현할 수 있지만 실제 CSP 투영과 생성은
미지원이다. 단일 Region으로 조용히 축소하지 않고 `unsupported`로 반환한다.

### 5.3 종단 가용성

Workload 하나의 배치 성공을 전체 서비스 성공으로 확대하지 않는다. 공개 Endpoint에서
필수 Connection을 따라가며 각 Workload와 상태를 검사한다.

```json
{
  "workloadAvailability": {
    "web": "satisfied",
    "records": "notSatisfied"
  },
  "ingress": "satisfied",
  "endToEndService": "notSatisfied"
}
```

단일 영속 Workload가 필수 연결 경로에 있으면 앱 계층이 여러 Zone에 남아 있어도
`endToEndService`는 HA를 만족하지 않는다.

### 5.4 VM 사양 추천과의 경계

토폴로지는 instance, VM과 Volume의 수·배치를 정한다. CPU·메모리·디스크 성능과
VM SKU는 구현 후 측정·추천 단계에서 결정한다. 현재 수로 성능 요구를 만족하지
못하면 VM을 몰래 추가하지 않고 토폴로지 재검토를 요청한다.

## 6. 대표 후보

다음은 설명용 예시이며 정답 목록이나 전체 경우의 수가 아니다. 앞 절의 `Endpoint`는
논리적 진입 의도다. 실제 배포 다이어그램에서는 이를 `Endpoint`라는 상자 하나로 남기지
않고, 선택 CSP의 공개 주소 또는 Load Balancer, 네트워크 경계와 대상 VM까지 구체화한다.

아래 예시는 AWS를 기준으로 한다. AWS VPC는 Region 범위이고 Subnet은 하나의 AZ에
속하므로 `Region → VPC → AZ → Subnet → EC2 → Workload`의 소속을 표시한다. Internet
Gateway, Route Table처럼 IaC에는 필요하지만 개념 설명에 중요하지 않은 보조 리소스는
선택적으로 접어 표시할 수 있다. 다만 공개 트래픽이 실제로 어디로 들어오고 어떤
Security Group을 거쳐 어느 Workload로 전달되는지는 생략하지 않는다.

### 6.1 단일 Workload

```text
Internet client
      │ HTTPS :443
      ▼
AWS Region ap-northeast-2
└─ VPC 10.0.0.0/16
   ├─ Internet Gateway
   └─ AZ ap-northeast-2a
      └─ Public Subnet 10.0.1.0/24  [0.0.0.0/0 → IGW]
         └─ EC2 VM
            ├─ ENI: Public IPv4 또는 Elastic IP
            ├─ Security Group: inbound 443
            ├─ Workload A-1: TLS termination
            └─ EBS Volume  # persistence.required=true일 때만
```

이 경우 논리 `public Endpoint`는 AWS에서 VM의 Public IPv4 또는 Elastic IP와
Security Group 규칙으로 실현된다. EBS Volume은 연결된 EC2와 같은 AZ에 있어야 한다.

### 6.2 연결된 두 Workload

```text
Internet client
      │ HTTPS :443
      ▼
AWS Region ap-northeast-2
└─ VPC 10.0.0.0/16
   ├─ Internet Gateway
   └─ AZ ap-northeast-2a
      ├─ Public Subnet 10.0.1.0/24  [0.0.0.0/0 → IGW]
      │  └─ EC2 VM 1
      │     ├─ ENI: Public IPv4 또는 Elastic IP
      │     ├─ Web Security Group: inbound 443
      │     └─ Workload A-1: TLS termination
      │              │ TCP :5432, internal connection
      │              ▼
      └─ Private Data Subnet 10.0.11.0/24
         └─ Data Security Group: source=Web Security Group, port=5432
            └─ EC2 VM 2
               ├─ Workload B-1
               └─ EBS Volume
```

두 Workload가 각각 단일 replica이고 같은 VM 배치가 허용되면 VM 하나에 둘 수도
있다. Workload 이름이나 역할이 아니라 `placements`가 이를 결정한다. Workload B는
공개 주소를 갖지 않고 Workload A의 Security Group에서 오는 내부 연결만 허용한다.

### 6.3 복제 Workload

```text
Internet client
      │ HTTPS :443
      ▼
AWS Region ap-northeast-2
└─ VPC 10.0.0.0/16
   ├─ Internet Gateway
   ├─ Application Load Balancer
   │  ├─ Public Subnet A 10.0.1.0/24  [AZ ap-northeast-2a, route→IGW]
   │  ├─ Public Subnet B 10.0.2.0/24  [AZ ap-northeast-2b, route→IGW]
   │  ├─ ALB Security Group: inbound 443
   │  └─ HTTPS Listener + certificate → Target Group → health check
   │                          ├─────────────────────────────┐
   │                          ▼                             ▼
   ├─ Launch Template
   ├─ Auto Scaling Group [desired=2, ALB health check]
   ├─ AZ ap-northeast-2a                    AZ ap-northeast-2b
   │  ├─ Private App Subnet A               └─ Private App Subnet B
   │  │  └─ ASG EC2 VM 1                       └─ ASG EC2 VM 2
   │  │     └─ Workload A-1                      └─ Workload A-2
   │  │            │                                  │
   │  │            └──────── TCP :5432 ───────────────┘
   │  │                             │
   │  └─ Private Data Subnet A      ▼
   │     └─ Data Security Group: source=App Security Group, port=5432
   │        └─ EC2 VM 3
   │           ├─ Workload B-1
   │           └─ EBS Volume
   └─ App Security Group: source=ALB Security Group, application port
```

논리 `public Endpoint`는 ALB의 DNS 이름과 HTTPS Listener로 실현된다. 두 App replica는
개별 EC2를 수기로 관리하지 않고 Launch Template을 사용하는 Auto Scaling Group이 서로
다른 AZ의 private App Subnet에 유지한다. Public Subnet 두 개는 ALB의 CSP 제약에서
파생된 것이지 중립 토폴로지의 고정 숫자가 아니다. 필수 연결 대상인 Workload B가
단일이면 App 계층의 VM 장애에는 대응해도 전체 서비스의 장애점으로 남는다.

Private App Subnet의 Workload가 image registry나 package repository에 접속해야 하면
NAT Gateway 또는 필요한 VPC Endpoint 같은 outbound 경로도 `RuntimeContract`에서
파생해 추가한다. 외부 통신 요구가 없는데 모든 예시에 NAT Gateway를 고정 추가하지 않는다.

### 6.4 영속 Workload가 있는 앱 HA

위 6.3의 Workload B와 EBS는 단일 상태 계층이다. CSP 관리형 그룹은 stateless App VM을
교체하는 데 사용하고 상태 VM을 그 그룹에 넣지 않는다. 상태 VM 장애까지 견뎌야 한다는
요구는 현재 지원 구조로 축소하지 않고 `unsupported`로 반환한다.

### 6.5 같은 논리 토폴로지의 CSP별 차이

다음 표는 같은 `public Endpoint → 복제 App → 단일 영속 상태 Workload` 의도를
각 CSP에서 어떻게 다르게 실현하는지 요약한다. 이름을 바꾼 동일 제품 목록이 아니라,
배치 범위와 native resource 경계가 서로 다른 projection이다.

| 관점 | AWS | Azure | GCP |
|---|---|---|---|
| 사설 network 범위 | VPC는 Region | VNet은 Region이며 Zone을 가로지름 | VPC network는 전역 |
| Subnet 범위 | 하나의 AZ | VNet Region 안에서 Zone과 독립적 | 하나의 Region |
| VM 위치 | AZ | Availability Zone 선택 가능 | Zone |
| 영속 block disk | EBS와 VM이 같은 AZ | zonal Managed Disk면 VM과 같은 Zone | Persistent Disk와 VM이 같은 Zone |
| 공개 L7 진입 | ALB, listener, target group, health check | Public IP + Application Gateway 전용 Subnet + listener/backend/probe | global forwarding rule + target HTTPS proxy + URL map + backend service + health check + instance groups |
| backend 묶음 | Target Group과 Auto Scaling Group 연결 | Backend Pool에 VM Scale Set NIC 등록 | Regional MIG를 Backend Service에 등록 |
| 트래픽 제한 | ALB·App·Data Security Group | Application Gateway Subnet/NSG와 App·Data NSG | VPC Firewall Rule과 health-check/proxy source range |

단일 Workload를 VM에 직접 공개할 때도 차이가 있다.

| 중립 의도 | AWS | Azure | GCP |
|---|---|---|---|
| VM 직접 public Endpoint | ENI의 Public IPv4/Elastic IP + Security Group + public route/IGW | NIC IP configuration의 Public IP + NSG | VM `network_interface.access_config`의 External IP + Firewall Rule |
| 사설 상태 Workload | private Subnet의 EC2와 EBS | App/Data Subnet의 VM NIC와 Managed Disk | regional Subnet의 zonal VM과 Persistent Disk |

### 6.6 Azure 변형

Azure에서는 VNet과 Subnet을 AZ 아래에 중첩하면 안 된다. VNet은 한 Region에 있고
그 Region의 Availability Zone을 가로지르며, VM이 Zone을 선택한다. Application Gateway는
VNet 안의 전용 Subnet이 필요하다.

```text
Internet client
      │ HTTPS :443
      ▼
Azure Region Korea Central
├─ Public IP
└─ VNet 10.0.0.0/16  [Region 범위, Zone을 가로지름]
   ├─ ApplicationGatewaySubnet 10.0.1.0/24  [전용 Subnet]
   │  └─ Application Gateway v2
   │     ├─ Frontend IP configuration ← Public IP
   │     ├─ HTTPS Listener + certificate
   │     ├─ Routing Rule → Backend Pool
   │     └─ Backend HTTP Setting + Health Probe
   │                         ├───────────────────────────┐
   │                         ▼                           ▼
   ├─ App Subnet 10.0.11.0/24
   │  └─ VM Scale Set [2 instances, automatic repair]
   │     ├─ NIC 1 + NSG → Linux VM 1 [Zone 1] → Workload A-1
   │     └─ NIC 2 + NSG → Linux VM 2 [Zone 2] → Workload A-2
   │                         │ TCP :5432
   │                         ▼
   └─ Data Subnet 10.0.21.0/24
      └─ NIC 3 + Data NSG → Linux VM 3 [Zone 1]
                             ├─ Workload B-1
                             └─ Managed Disk [Zone 1]
```

Application Gateway의 backend probe가 통과해야 트래픽이 전달된다. 같은 앱 health
endpoint는 별도의 Application Health extension이 관측하고, VM Scale Set의 automatic
repair가 비정상 instance를 교체한다. App NSG는 인터넷
전체가 아니라 Application Gateway 경로와 필요한 관리 경로만 허용하고, Data NSG는
App 계층에서 오는 상태 protocol만 허용한다. Application Gateway 전용 Subnet에는 다른
종류의 리소스를 함께 배치하지 않는다.

- [Azure VNet·Subnet 범위](https://learn.microsoft.com/en-us/azure/networking/design-guide/vnets-subnets)
- [Application Gateway 전용 Subnet](https://learn.microsoft.com/en-us/azure/application-gateway/configuration-infrastructure)
- [Application Gateway health probe](https://learn.microsoft.com/en-us/azure/application-gateway/application-gateway-probe-overview)

### 6.7 GCP 변형

GCP에서는 VPC network가 전역이고 Subnetwork가 Region 범위이며 VM과 Persistent Disk가
Zone에 속한다. 다음 예시는 global external Application Load Balancer를 사용하므로 frontend
구성요소도 Subnetwork 안에 억지로 넣지 않는다.

```text
Internet client
      │ HTTPS :443
      ▼
Global external Application Load Balancer
├─ Global External IP
├─ Global Forwarding Rule
├─ Target HTTPS Proxy + certificate
├─ URL Map
└─ Global Backend Service + Health Check
                   └─ Regional Managed Instance Group [2 instances, autohealing]
                      ├─ managed instance [asia-northeast3-a]
                      └─ managed instance [asia-northeast3-b]
                              │
                              ▼
Global VPC Network 10.0.0.0/16
└─ Regional Subnetwork 10.0.11.0/24 [asia-northeast3]
   ├─ MIG Compute Engine VM 1 [Zone a] → Workload A-1
   ├─ MIG Compute Engine VM 2 [Zone b] → Workload A-2
   ├─ Firewall Rule
   │  ├─ LB proxy/health-check source → application port
   │  └─ App service account/tag → state port 5432
   └─ Compute Engine VM 3 [Zone a]
      ├─ Workload B-1
      └─ Persistent Disk [Zone a]
```

Zone별 backend VM은 Regional Managed Instance Group을 거쳐 Backend Service에 등록되고
application health check로 autohealing된다. Forwarding Rule,
Target HTTPS Proxy, URL Map, Backend Service와 Health Check는 중립 `loadBalancer` 하나가
여러 GCP native resource로 실현되는 대표적인 일대다 projection이다.

- [GCP global external Application Load Balancer 구성](https://docs.cloud.google.com/load-balancing/docs/https/setup-global-ext-https-compute)
- [GCP target proxy 경로](https://docs.cloud.google.com/load-balancing/docs/target-proxies)
- [GCP VPC·Subnetwork 범위](https://docs.cloud.google.com/vpc/docs/vpc)

## 7. 네트워크와 Subnet

### 7.1 중립 모델

`subnetCount`나 `publicSubnet: true`를 논리 선택값으로 두지 않는다. 먼저 Endpoint와
Connection에 공개·내부 통신 의도를 기록한다.

- 어느 Endpoint가 인터넷에 공개되는가
- 공개 주소를 VM과 LB 중 누가 소유하는가
- 어떤 Workload가 외부 Endpoint를 갖지 않는가
- Workload 간 허용 protocol과 port는 무엇인가

실제 Subnet, Route, Public IP와 방화벽 구조는 선택된 CSP로 투영한다.

### 7.2 Subnet 분리 조건

Workload가 다른 VM에 있다고 반드시 Subnet도 나눌 필요는 없다. 다음 근거가 있을
때만 추가하거나 분리한다.

- 사용자 또는 보안 정책이 네트워크 격리를 요구함
- Zone 배치 때문에 CSP가 영역별 Subnet을 요구함
- 선택한 CSP 서비스가 전용 Subnet을 요구함

예를 들어 AWS Application Load Balancer는 서로 다른 가용 영역의 Subnet을 최소
두 개 요구한다. 이는 CSP 제약이지 중립 모델의 고정 숫자가 아니다.
[AWS ALB Subnet 문서](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html)

## 8. 선택된 CSP의 배포 다이어그램

EasyDep은 CSP를 앞단에서 받으므로 한 실행에서 해당 CSP의 다이어그램 하나만
생성한다. 다이어그램과 IaC는 모두 CSP로 구체화된 같은 `ResourcePlan`을 사용한다.
별도 중립 다이어그램이나 추가 중간 계획을 만들지 않으며 PlantUML을 IaC 입력으로
사용하지 않는다.

```text
요구사항·설계 + 선택된 CSP
        ↓
Workload 토폴로지 결정
        ↓
CSP 제약으로 ResourcePlan 구체화
        ├─ 배포 다이어그램
        └─ IaC
```

### 8.1 CSP별 영역 표현

| CSP | 원칙 |
|---|---|
| AWS | VPC는 Region 범위이고 Subnet은 특정 AZ에 속한다. [AWS 문서](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/using-regions-availability-zones.html) |
| Azure | VNet과 Subnet은 Region의 여러 Zone에 걸치므로 VM의 Zone과 Subnet 소속을 별도로 표시한다. [Azure 문서](https://learn.microsoft.com/en-us/azure/networking/design-guide/multi-region) |
| GCP | VPC는 전역, Subnet은 Region, VM은 Zone 범위다. [GCP 문서](https://docs.cloud.google.com/vpc/docs/vpc) |

모든 CSP에 `Region > VPC > Zone > Subnet > VM`이라는 중첩을 강제하지 않는다.

### 8.2 표시 대상

- Workload instance와 VM 배치
- 영속성이 있으면 Volume
- Endpoint, LB와 대상 Workload
- Workload 사이의 Connection
- 선택한 Region과 확정된 Zone
- VPC/VNet과 Subnet 소속
- 앱 HA이면 CSP 관리형 VM 그룹과 health/자동 복구 관계

Route association처럼 이해에 중요하지 않은 보조 리소스는 그림에서 생략할 수 있지만
`ResourcePlan`과 IaC에는 남긴다.

## 9. CSP별 native 리소스 의존성

### 9.1 범위와 읽는 법

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

### 9.2 AWS

#### 9.2.1 사용하는 리소스와 역할

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

#### 9.2.2 생성 의존성

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

#### 9.2.3 기능 의존성

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

### 9.3 Azure

#### 9.3.1 사용하는 리소스와 역할

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

#### 9.3.2 생성 의존성

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

#### 9.3.3 기능 의존성

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

### 9.4 GCP

#### 9.4.1 사용하는 리소스와 역할

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

#### 9.4.2 생성 의존성

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

#### 9.4.3 기능 의존성

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

### 9.5 생성 순서 요약

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

### 9.6 기능 검증 공통 기준

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

### 9.7 현재 증거와 남은 검증

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

## 10. CSP 투영과 IaC 정합성

### 10.1 ResourcePlan이 보존할 것

- Workload와 active instance 수·역할
- VM 배치와 Volume 소유
- Endpoint와 Connection
- 위치·가용성·복구 요구
- VPC/VNet, Subnet, 주소, 방화벽과 LB 보조 리소스
- 각 결정의 요구사항·설계·CSP 제약·정책 근거

### 10.2 공통 불변식

```text
계획된 Workload replica 수 = 직접 VM 수 또는 관리형 VM 그룹 desired capacity
각 직접 instance는 정확히 하나의 VM에 배치하고, HA 앱은 정확히 하나의 관리형 그룹에 배치
coLocate·separateCompute와 실제 VM 참조가 일치
persistence가 true인 instance마다 서로 다른 Volume 존재
공개 Endpoint 대상이 direct VM 또는 LB backend와 일치
공개 Endpoint가 없는 Workload에는 인터넷 인바운드가 없음
Connection의 protocol·port가 방화벽 허용 관계와 일치
VM의 Region·Zone·Subnet이 계획과 일치
모든 CSP 보조 리소스에 공식 제약 근거가 있음
```

앱 HA에는 관리형 VM 그룹, Load Balancer backend, health check와 자동 교체 정책의 참조를
추가한다. 영속 Workload는 관리형 앱 VM 그룹에 포함하지 않는다.

Terraform은 HCL·provider 검증 후 Plan JSON으로 리소스와 참조 관계를 확인한다.

## 11. 기능 검증

### 11.1 단계별 gate

IaC Plan은 실제 기능을 증명하지 않는다. 다음을 분리해 확인한다.

| 층위 | 확인 대상 |
|---|---|
| 정적 IaC | VM·Volume·Endpoint·Connection과 CSP 참조 |
| Workload runtime | container 시작, health check, 내부 연결, Volume mount |
| 업무 기능 | 외부 요청, 상태 기록·조회, 재시작 후 보존 |
| 앱 계층 HA | 앱 VM 장애 중 업무 요청 지속, health 감지와 CSP 관리형 그룹의 자동 교체 |

### 11.2 애플리케이션–클라우드 계약

특정 스키마에 의존하지 않고 다음 의미를 단계 사이에서 보존한다.

| 계약 의미 | 앱에서 얻는 사실 | 배포에서 확인할 사실 |
|---|---|---|
| 실행 | build artifact, runtime, 시작 명령 | image build와 process 시작 |
| network | listen address·port, health endpoint | container port, LB backend와 probe |
| 설정 | 필수 환경변수·secret·외부 dependency | 주입 위치와 내부/외부 연결 경로 |
| 저장 | 데이터 경로, 영속성·공유 의미 | Volume attachment, mount와 재시작 보존 |
| 가용성 | stateless 여부와 replica 허용 | 관리형 VM 그룹 배치와 공유 상태 연결 |

검출 결과는 `satisfied`, `needsInput`, `unsupported`, `mismatch`로 구분한다. 오류 문자열에
맞춘 사례별 패치가 아니라 구조화된 계약 필드를 비교하고, 수정은 진단이 소유한 하위 작업부터
재실행한다. 앞 단계의 결정이 바뀌어야 하면 현재 단계에서 임의 수정하지 않고 되돌아갈 단계와
영향 범위를 사용자에게 제시한다.

### 11.3 VM 후보와 용량 검증

VM 선택 상태는 다음 세 단계로만 표현한다.

- `unresolved`: CPU·memory 하한이나 평가할 부하/SLO가 없어 추천하지 못함
- `provisional`: 고정 image·데이터·업무 비율·부하 profile의 Docker 관측으로 좁힌 후보
- `validated`: 동일 profile과 특정 CSP·SKU의 실제 cloud 시험을 통과한 후보

현재 구현은 명시된 CPU·memory 하한을 만족하는 on-demand compute 목록가격 후보를
결정론적으로 고른다. disk, LB, public IP, egress와 할인은 이 가격에 포함되지 않는다.
측정 기반 후보는 open-arrival 부하, warm-up, 반복, image·seed·scenario digest를 기록해야
하며 실제 cloud에서 다시 확인하기 전에는 right-sizing이나 최적 사양이라고 부르지 않는다.
Disk는 이번 범위에서 용량 하한만 다루고 IOPS·throughput 최적화는 제외한다.

cloud gate는 `apply → ready → 업무 기능 → NFR → 요구된 fault/restart → destroy → 해당 run
잔여 0` 순서다. CPU·memory 포화가 확인된 경우에만 한 단계 큰 SKU로 최대 한 번 재검증한다.
replica나 LB 변경은 VM 추천이 아니라 토폴로지 변경이므로 설계 단계로 돌아갈지 묻는다.

## 12. 현재 지원 상태

| 항목 | 모델 | 현재 생성·배포 |
|---|---|---|
| 일반 Workload·Connection·Endpoint | 목표 | 미구현 |
| Workload replica와 VM 배치 | 목표 | 부분 구현·검증 필요 |
| Workload 영속 Volume | 목표 | 일부 구현 |
| Spring Boot 단일 Workload | 지원 | 지원 |
| React를 Spring Boot에 포함 | 목표 | 미구현 |
| 자체 운영 PostgreSQL | 목표 | 미구현 |
| CSP 맞춤 배포 다이어그램 | 목표 | 미구현 |
| 단일 Region·다중 Zone | 지원 | CSP별 검증 필요 |
| 다중 Region과 전역 진입 | 표현 가능 | 미지원 |
| CSP 관리형 앱 VM 그룹 HA | 목표 | 미구현 |
| 영속 Workload HA·관리형 DB | 제외 | 미지원 |
| 임의 runtime 자동 구성 | 제외 | 미지원 |

## 13. 결정 규칙 요약

```text
1. 설계 근거가 있는 독립 실행 단위만 Workload로 만든다.
2. Workload별 active replica 수와 영속성 필요 여부를 정한다.
3. 영속 instance마다 별도 Volume을 둔다.
4. coLocate·separateCompute로 Workload의 VM 배치를 정한다.
5. Endpoint와 Connection으로 진입과 내부 통신을 정한다.
6. 위치와 장애 허용을 VM·Zone·Region 배치로 직접 변환한다.
7. 앱 HA는 CSP 관리형 VM 그룹과 Load Balancer로 실현한다.
8. 영속 Workload HA 요구는 현재 `unsupported`로 반환한다.
9. 선택된 CSP의 제약으로 하나의 ResourcePlan을 만든다.
10. 같은 ResourcePlan에서 배포 다이어그램과 IaC를 생성한다.
11. IaC, runtime과 업무 기능을 서로 다른 층위로 검증한다.
12. 근거 없는 값은 추측하지 않고 미지원 기능은 축소하지 않는다.
13. 성능 때문에 VM 수 변경이 필요하면 토폴로지 재검토를 요청한다.
```
