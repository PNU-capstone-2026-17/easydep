# 배포 다이어그램과 리소스 의존성 통합 기준

> 상태: Docker-on-VM 배포 결정, 배포 다이어그램 생성, CSP별 native 리소스 의존성,
> 앱-리소스 바인딩, VM 추천과 단계별 검증을 함께 정의하는 **유일한 사람이 읽는 정본 문서**다.
> 현재 구현 상태와 실행 순서는 [현재 시스템 상태](current-system-status.md)를 따른다.
> 다중 Region 배치의 표현 가능성은 유지하되 현재 adapter에서는 `unsupported`로 처리한다.

## 현재 구현 정본: 12개 논리 토폴로지 계열

2026-08-16부터 Docker-on-VM 다이어그램의 조합축은 다음 세 개뿐이다. 이 절은 이 문서 뒤쪽에
남아 있는 자동 복구·고가용성 연구 기록보다 우선한다. `availability`, `failureContinuity`,
`automaticRecovery`는 토폴로지 선택축이 아니며 현재 생성 결과는 가용성·SLA를 주장하지 않는다.

| 축 | 값 | 의미 |
|---|---|---|
| App compute | `standaloneOne` | 독립 VM 1대, 단일 Zone |
|  | `managedGroupOne` | 관리형 VM 그룹, desired 1, 단일 Zone |
|  | `managedGroupManySingleZone` | 관리형 VM 그룹, 2대 이상, 단일 Zone |
|  | `managedGroupManyMultiZone` | 관리형 VM 그룹, 2대 이상, 2개 이상 Zone에 분산 |
| PostgreSQL | `none` · `colocated` · `dedicated` | 없음, standalone App VM에 함께 실행, 별도 State VM에 실행 |
| 공개 진입 | `direct` · `loadBalanced` | 고정 공인 주소로 VM에 직접 진입, 공개 L7 Load Balancer로 진입 |

유효 조합은 다음 계산으로 정확히 12개다.

```text
standaloneOne: 3 database placements × 2 ingress modes = 6
3 managed-group profiles: 2 database placements × loadBalanced only = 6
합계 12 logical families × 3 CSP projections = 36 provider-labelled diagrams
```

다음 조합은 생성하지 않는다.

- 관리형 VM 그룹 + `direct`: 교체 가능한 여러 backend에 하나의 고정 VM 주소를 결합하지 않는다.
- 관리형 VM 그룹 + `colocated`: 교체 가능한 App VM 안에 단일 PostgreSQL 상태를 소유시키지 않는다.
- one 프로필 + `replicaCount != 1`, many 프로필 + `replicaCount < 2`.
- single-Zone 프로필 + 복수 occupied Zone, multi-Zone 프로필 + 2개 미만 Zone.
- `many`인데 앱의 session·upload·singleton job·writable local state가 외부화됐다는 근거가 없는
  경우. 이는 별도 토폴로지가 아니라 배포 전 완료해야 할 검증 constraint다.

`many + singleZone`은 유효하다. 이는 Zone 장애 대응이 아니라 한 Zone 안에서 병렬 용량,
rolling replacement, 관리형 수명주기를 쓰는 구조다. 반대로 `multiZone`도 그 자체로 장애 중
서비스 지속을 보장하지 않는다. 상태 계층, health 판정, 실제 failover 실험이 없으므로 모든
계열의 `availabilityClaim`은 `none`이다.

현재 범위의 고정 정책은 조합축에서 제외한다.

| 정책 | 현재 결정 |
|---|---|
| audience | public만 지원; private/internal 전용 배포는 제외 |
| 공개 protocol | HTTP만 지원 |
| HTTPS/TLS | 현재 범위 밖. 인증서·도메인 검증·TLS proxy를 생성하지 않음 |
| 보안 한계 | 산출물은 production-secure deployment가 아니며 운영 공개 전 별도 TLS 계층이 필요 |
| 앱 image | CSP native registry에 1회 build/push하고 digest로 pull |
| PostgreSQL image | 공식 `postgres:17-bookworm`을 digest로 고정해 pull |
| secret | CSP native secret store에서 runtime에 주입; image·평문 IaC에 넣지 않음 |
| outbound | direct VM은 공인 주소 경로, LB 뒤 private backend와 State VM은 관리형 NAT 사용 |
| 생성·삭제 | 배포 리소스는 새로 생성하고 App compute는 정리 가능; PostgreSQL data disk는 기본 retain |
| autoscaling | v1에서는 비활성; many의 수량은 명시값 |
| 관리 접속 | public SSH를 만들지 않음 |
| multi-region | 별도 전역 ingress·상태 복제 설계가 필요하므로 현재 `unsupported` |

구현의 `DeploymentTopology/v1`이 위 선택의 기계 판독 정본이며, provider projection은 이를
AWS Auto Scaling Group, Azure Virtual Machine Scale Set, GCP Managed Instance Group 등의
실제 리소스로 옮긴다. provider 이름이 붙은 36개 결과는 36개의 새로운 논리 의미가 아니다.

> 아래 자동 복구·고가용성 표와 실험 항목은 과거 설계 검토 기록이다. 향후 별도 검증
> 프로필로 재도입할 수 있지만, 현재 조합 수 계산·UI·ResourcePlan 생성 근거로 사용하지 않는다.

## 0. 문서 정본과 산출물 경계

배포 관련 규칙을 주제별 Markdown에 중복 기록하지 않는다. 요구사항에서 토폴로지를 고르는
규칙, ResourcePlan의 의미, CSP 실제 리소스의 생성·기능 의존성, 애플리케이션 설정과 guest
구성의 연결, 셋업 순서와 완료 gate는 모두 이 문서에서 관리한다.

```text
요구사항·앱 설계·runtime contract + 선택한 CSP
                       ↓
              토폴로지와 배포 결정
       compute · database · public ingress
            placement · outbound
                       ↓
                하나의 ResourcePlan
          ┌────────────┼────────────┐
          ↓            ↓            ↓
   배포 다이어그램   검증용 그래프      IaC
                  ├─ CSP native 원장
                  └─ 앱-배포 바인딩
```

정본이 하나라는 말은 모든 관계를 한 장의 그래프에 겹친다는 뜻이 아니다. 같은 ResourcePlan과
계약에서 다음 뷰를 목적별로 투영한다.

| 뷰 | 답하는 질문 | 포함하는 것 |
|---|---|---|
| 선택된 배포 다이어그램 | 이번 배포에서 무엇을 어디에 몇 개 놓는가 | Workload, VM/VM 그룹, Zone, Disk, Endpoint, Connection |
| CSP native 의존성 | 실제 provider 리소스를 어떤 참조로 만들고 구성하는가 | VPC/VNet, Subnet, NIC, VM, LB 구성요소, Attachment·Association |
| 앱-배포 바인딩 | 만든 리소스가 어떻게 실제 앱 기능으로 이어지는가 | port, health, endpoint, filesystem, mount, Docker bind, secret·identity |

### 0.1 토폴로지 결정을 선정하는 기준

시각화에서 리소스를 추가하기 쉽다는 이유로 이를 곧바로 사용자 요구사항이나 연구축으로
채택하지 않는다. 먼저 Docker-on-VM 배포가 성립하기 위해 답해야 하는 질문을 요구사항과
runtime contract에서 도출하고, 그 뒤에 CSP 리소스를 실현 수단으로 선택한다. 정식 결정은
다음 조건을 만족해야 한다.

| 조건 | 판정 질문 |
|---|---|
| 요구 근거 | CSP 제품명을 모르더라도 사용자가 원하는 결과로 진술할 수 있는가 |
| 토폴로지 영향 | ResourcePlan의 node·edge·수량·배치·runtime binding을 바꾸는가 |
| 개념 분리 | 같은 의미를 다른 필드에 중복하지 않고, 다른 결정과의 결합 제약을 명시할 수 있는가 |
| 3사 공통 의미 | AWS·Azure·GCP에서 같은 결과를 서로 다른 native 요소로 실현할 수 있는가 |
| 범위와 검증 | 현재 Docker-on-VM 범위에서 생성하고 완료 gate로 검증할 수 있는가 |

이 기준으로 역도출한 정식 결정 묶음은 다음 다섯 가지다. 다섯이라는 개수 자체를 목표로
맞춘 것이 아니라 현재 범위에서 서로 다른 질문을 합치지 않은 결과다.

| 결정 묶음 | 먼저 답할 질문 | ResourcePlan에 보존할 의미 | 대표적인 CSP 실현 결과 |
|---|---|---|---|
| Workload 할당 | 어떤 container가 독립 수명주기를 가지며 App과 State를 함께 둘 것인가 | Workload 경계, allocation, co-located/separate compute | 단일 VM, App VM과 State VM 분리 |
| Endpoint 계약 | 누가 접근하며 하나의 분산 진입점이 필요한가 | audience=`public`, protocol=`http`, direct/load-balanced | 공인 주소, 외부 Load Balancer |
| 상태 정책 | 어떤 데이터를 VM 교체 뒤에도 보존하며 누가 소유하는가 | durability, owner, mount/data path, 복제·재연결 방식 | 별도 block Disk와 Attachment, 외부 상태 서비스 |
| 배치 제약·운영 수준 | 어느 Zone 범위에 배치하고 그 안에서 어느 복구·고가용성 수준을 쓸 것인가 | zone scope, operation mode, minimum active instances, fault scope, traffic failover | 독립 VM, 관리형 VM 그룹, Zone 분산, Load Balancer |
| 부트스트랩·outbound | 앱 image를 어떻게 준비하고 PostgreSQL image를 어디서 받는가 | VM 직접 build, Docker Hub PostgreSQL pull, egress policy | 공인 주소 경로 또는 NAT |

보안은 여섯 번째 선택 기능이 아니라 모든 Endpoint와 Connection에 적용하는 불변식이다.
허용할 source·destination·protocol·port와 identity를 계약에서 도출하고 최소 권한의 native
정책으로 실현한다. CSP 플랫폼 기본 정책을 교육용으로 보여 줄 수는 있지만, EasyDep의 명시적
보안 정책을 사용자가 임의로 끄는 토폴로지 옵션으로 취급하지 않는다.

배치와 고가용성은 자유롭게 조합하는 동등 축으로 보여 주지 않는다. 사용자는 먼저 Zone 배치
범위를 정하고, 그 범위 안에서 운영 수준을 선택한다. 서비스 연속성은 대부분의 사용자가 원하는
일반적 품질이므로 “원하는가”를 묻지 않고 각 수준의 비용·장애 결과를 함께 제시한다.

| Zone 배치 범위 | 운영 수준 | 파생 결과 | 주장할 수 있는 범위 |
|---|---|---|---|
| 단일 Zone | 기본 배치 | 독립 VM 1대 | 자동 복구·연속성 없음 |
| 단일 Zone | 관리형 자동 복구 | 관리형 그룹 desired 1 | 자동 재생성, 복구 중 중단 가능 |
| 단일 Zone | 고가용성 | 최소 2대 + application health + 요청형 앱의 LB | App VM 장애 중 지속, Zone 장애 제외 |
| Multi-Zone | 기본 배치 | 서로 다른 Zone의 독립 VM | 위치 분산만 보장, 자동 전환 없음 |
| Multi-Zone | 관리형 자동 복구 | Zone 분산 관리형 그룹 | 자동 재생성, 공통 진입 없으면 연속성 미주장 |
| Multi-Zone | 고가용성 | Zone 분산 최소 2대 + application health + 요청형 앱의 LB | App 계층 Zone 장애 중 지속 |

고가용성 선택은 배치 범위에 맞는 `faultScope`를 파생한다. 단일 Zone에서는 instance 장애까지만,
Multi-Zone에서는 Zone 장애까지를 목표로 한다. 자체 PostgreSQL을 단일 State VM으로 실행하는
현재 범위에서는 어느 경우에도 전체 서비스 HA를 주장하지 않고 App 계층 보장과 State 계층의
단일 장애점을 함께 표시한다.

### 0.2 기존 여섯 시각화 스위치의 지위

`전용 보안 정책`, `외부 접속 가능`, `VM 그룹 모드`, `Load Balancer 사용`, `영속 Disk`,
`Private outbound`는 앞선 리소스 학습 과정에서 눈에 띄는 delta를 조합하려고 만든 화면용
묶음이다. 이 여섯 개를 독립적인 요구사항 축으로 선정했다는 연구 근거는 없으며, 다음처럼
정식 결정의 입력·파생 결과·정책으로 다시 분류한다.

| 기존 스위치 | 정식 지위 | 처리 |
|---|---|---|
| 전용 보안 정책 | 시스템 불변식 | 지원 옵션에서 제거하고 필요한 정책을 항상 명시적으로 생성 |
| 외부 접속 가능 | Endpoint 요구 | endpoint별 audience로 확장하고 internal과 public을 구분 |
| VM 그룹 모드 | 복구·가용성 실현 수단 | 일반 사용자 입력에서 제거하고 목표 정책으로부터 도출; 고급 명시 제약은 별도 보존 |
| Load Balancer 사용 | Endpoint 분산 방식 또는 가용성 실현 수단 | direct/load-balanced로 표현하고 public 여부·VM 그룹과 분리 |
| 영속 Disk | 상태 보존의 현재 native 실현 | 사용자 입력은 durability·owner로 받고 Disk는 범위와 정책에 따라 도출 |
| Private outbound | 배치와 송신 수단이 섞인 묶음 | private/public 배치, outbound 필요, artifact 전달 방법으로 분해 |

VM 그룹은 Load Balancer나 공개 endpoint를 보편적으로 요구하지 않는다. 단일 backend의 Load
Balancer, 내부 Load Balancer, 공개 endpoint가 없는 관리형 그룹도 유효하다. 다만 EasyDep의
현재 **공개 stateless App HA 프로필**은 `관리형 VM 그룹 + 최소 2대 + Zone 분산 + application
health + Load Balancer`를 하나의 제한된 실현 정책으로 선택한다. UI의 자동 조합은 이 프로필을
선택했을 때만 적용하며 CSP의 보편 제약이라고 설명하지 않는다. AWS도 Auto Scaling Group에
Load Balancer를 별도로 연결하고, GCP도 MIG의 autohealing과 load balancing을 별도 기능으로
설명한다. [AWS Auto Scaling과 Load Balancer](https://docs.aws.amazon.com/autoscaling/ec2/userguide/autoscaling-load-balancer.html),
[GCP Instance groups](https://docs.cloud.google.com/compute/docs/instance-groups)

현재 VM bootstrap이 Docker package를 설치하거나 container image를 pull한다면 실제 outbound
경로가 배포 성립 조건이다. 현재 EasyDep는 생성한 앱 소스와 Dockerfile을 VM에서 직접
build하고, PostgreSQL을 선택하면 Docker Hub 공식 `postgres:17-bookworm`을 pull한다.
따라서 `사설 VM + outbound 없음`은 현재 지원 방식에서 완결된 배포가 아니다.
GCP는 VM의 인터넷 송신에 external IP 또는 Cloud NAT·proxy 경로를 요구하고, Azure도 새
private subnet에 명시적 outbound 방식을 요구한다.
[GCP 인터넷 접근 조건](https://docs.cloud.google.com/vpc/docs/vpc#internet_access_reqs),
[Azure NAT Gateway 설계](https://learn.microsoft.com/en-us/azure/nat-gateway/nat-gateway-design)

### 0.3 복잡도와 요구사항 delta를 보여 주는 방법

사람이 처음부터 전체 원장을 읽도록 강요하지 않는다. HTML 시각화는 같은 정본을 다음 세
밀도로 투영한다.

| 밀도 | 목적 | 표시 방법 |
|---|---|---|
| 선택 구성만 | 한 ResourcePlan의 구조 학습 | 선택된 정식 결정에서 도출된 요소만 표시 |
| 플랫폼 최소 대비 | 요구사항이 비용·구성에 미치는 영향 파악 | CSP 플랫폼 최소에서 유지·추가·교체되는 요소를 구분 |
| 전체 원장 | 누락 감사 | 해당 CSP의 연구 범위 전체 요소와 관계 표시 |

화면은 다섯 결정 묶음의 질문과 값을 먼저 보여 주고, VM 그룹·Load Balancer·Disk·NAT 같은
리소스 delta는 그 결과로 표시한다. 플랫폼 최소는 학습용 CSP 기준선이지 EasyDep이 실제로
배포할 권장 최소와 같지 않다. 예를 들어 AWS Default Security Group이 자동 적용되는 경로는
플랫폼 동작을 설명하기 위해 보존하지만, 실제 ResourcePlan은 앱 계약에서 도출한 전용
Security Group을 생성한다.

Load Balancer는 CSP마다 하나의 리소스일 수도 있고 여러 리소스와 내부 구성요소의 조합일
수도 있다. 초보자용 화면에서는 이들을 `Load Balancer 기능 묶음`으로 접을 수 있다. 이 묶음은
화면 전용 경계이며 새로운 벤더 중립 리소스, 원장 노드 또는 IaC 객체가 아니다. 내부를
펼치면 원장에 보존된 실제 CSP 요소와 참조 관계가 다시 나타나야 한다.

앱-배포 바인딩도 같은 원칙을 쓴다. `부팅·이미지`만 무조건 가능한 기본 실행 사슬이라고
가정하지 않고 선택된 artifact delivery와 outbound 정책을 함께 표시한다. 외부 요청, Disk·DB,
상태·복구, 외부 호출·권한 요구사항을 선택할 때 새로 필요한 앱 계약·guest 구성·CSP 요소와
완료 gate를 delta로 강조한다. 전체 바인딩은 전수 감사용으로만 사용한다.

기계 판독 정본은 실행 코드의 `ResourcePlan`, `ApplicationRuntimeContract/v1`,
`CloudCapabilityContract/v1`, `DeploymentBindingContract/v1`과 provider 원장이다. 이 문서는
그 의미와 생성 규칙의 정본이며, HTML은 읽기 전용 시각화다. 실험 결과와 현재 구현 진척은
[의존성 실험의 배포 계획 반영](resource-plan-experiment-reflection.md)과
[현재 시스템 상태](current-system-status.md)에만 기록한다.

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

`availability_policy.py`는 **CSP 관리형 복구 수단의 선택**과 **요구사항에서 파생한
replica·Zone·LB 결정**을 분리한다. 필수 고가용성 근거가 있을 때만 관리형 그룹, 최소 2대,
Zone 분산과 Load Balancer를 선택하고, 근거가 없으면 단일 VM을 선택한다. 이 분기 자체는
회귀시험으로 확인하지만 실제 장애 복구 시간은 아직 종단 측정 근거가 없다.

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

### 3.3 자동 복구·가용성·상태 보존의 세 축

세 축은 서로 대신할 수 없으며 Boolean 하나로 합치지 않는다.

| 축 | 판정 질문 | 최소 표현 |
|---|---|---|
| 자동 복구 | 장애 난 VM이나 앱을 누가 어떤 방식으로 다시 살리는가 | health source, `restart`/`hostRecover`/`replace`, 목표 복구시간 |
| 가용성 | 복구 중에도 몇 개의 정상 instance가 어떤 장애 범위에서 남아야 하는가 | minimum active instances, VM·Zone·Region fault scope, traffic failover |
| 상태 보존 | VM 재시작·교체 뒤 어떤 데이터를 얼마나 보존해야 하는가 | state location, Disk 수명주기, RPO, 재연결 또는 복제 방법 |

```text
자동 복구가 있어도 instance가 하나면 복구 중 중단될 수 있다.
복제본과 LB가 있어도 상태가 한 boot disk에만 있으면 데이터는 보호되지 않는다.
Data Disk가 남아 있어도 VM·mount·DB가 복구될 때까지 서비스는 중단된다.
```

상태 위치는 모호한 `localPersistent` 대신 다음처럼 구분한다.

| 상태 방식 | 의미 | 기본 선택 |
|---|---|---|
| `ephemeral` | RAM, 임시 경로, 교체 시 버려도 되는 cache | VM 그룹 허용 |
| `vmAttachedPersistentDisk` | EBS·Managed Disk·Persistent Disk를 VM filesystem 경로로 사용 | 단일 State VM과 명시적 재연결 계약 |
| `externalStateService` | 외부 DB·공유 파일·object storage가 상태를 소유 | App VM 그룹 허용 |
| `replicatedState` | runtime 자체가 노드 간 복제·승격·quorum을 관리 | runtime별 검증 없으면 `unsupported` |

별도 Data Disk는 물리적으로 CSP network block storage이지만 앱에서는 VM의 파일 경로로
소비한다. Disk가 살아남는 것과 새 VM이 이를 안전하게 detach·attach·mount하고 업무를
재개하는 것은 별도 계약이다.

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

Internet Gateway, Route Table, Security Group, ALB HTTP Listener·Target Group·Health Check는
이 예시에서 생략한 AWS 보조 리소스다. Auto Scaling Group이
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

`regionRefs`는 배포 위치이고 `availability`는 견뎌야 할 장애 범위다. **CSP 관리형 VM
그룹을 사용하는 것과 고가용성을 요구하는 것은 별도 결정**이다. ASG·VMSS·MIG는 직접 만든
VM 복제·감시 로직을 대신하는 구현 수단이며, 관리형 그룹을 쓴다는 사실만으로 최소 2대,
Zone 분산, Load Balancer를 강제하거나 고가용성을 주장하지 않는다.

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

`singleZone`과 `singleRegion`은 리소스를 여러 위치에 놓았다는 뜻이 아니라 **해당 장애
범위에서도 요구된 Workload가 계속 동작해야 한다는 목표**다. 사용자가 단순히 여러 Zone이나
Region에 배치해 달라고 한 경우에는 위치 제약으로 보존하고, 장애 중 업무 지속·RTO·RPO
근거가 없으면 고가용성 충족으로 판정하지 않는다.

정확한 CSP Region이 주어지면 보존한다. `korea`처럼 지역만 주어지면 선택된 CSP의
공식 Region 목록에서 후보를 확인한다. “고가용성을 원합니까?”를 사전 질문으로 만들지는
않는다. 비용과 상태 제약을 설명하지 않은 이 질문은 대부분 긍정 답변을 유도하면서도 설계
근거를 주지 못하기 때문이다. 요구사항에서 중단 허용시간, 단일 VM 장애 허용, RTO·RPO 같은
관측 가능한 목표를 먼저 추출한다. 근거가 없으면 `unspecified`로 보존하고 최소 배포 후보를
선택한다. 비용·로컬 상태와 복구 목표가 충돌할 때만 예상 영향과 대안을 포함해 질문한다.

단, 사용자가 **다중 Zone 배치 자체를 명시했지만 목적을 말하지 않은 경우**에는 구현이
달라지므로 한 번만 조건부 질문한다. 단순 위치 분산이면 서로 다른 Zone의 독립 VM 복제본을
만들고 자동 복구나 고가용성을 주장하지 않는다. Zone 장애 중 업무 지속이 목적이면 CSP
관리형 VM 그룹, 애플리케이션 health, 정상 복제본으로 보내는 Load Balancer를 선택한다.

```text
복구 목표 없음
→ 직접 VM 또는 관리형 그룹 1대가 합법 후보
→ 자동 교체를 써도 교체 중 서비스 중단 가능
→ 고가용성이라고 부르지 않음

단일 앱 VM 장애 중에도 요청 지속
→ CSP 관리형 그룹 2대 이상 + Zone 분산 + Load Balancer
→ 직접 만든 VM 감시·복제 로직은 사용하지 않음
```

멀티 AZ, VM 그룹과 Load Balancer도 서로 다른 결정이다.

| 결정 | 의미 |
|---|---|
| 다중 AZ 배치 | instance를 어느 장애 영역에 놓는가 |
| VM 그룹 | 동일 Template의 instance 수와 교체·확장 수명주기를 누가 조정하는가 |
| Load Balancer | 요청을 어느 정상 backend로 전환하는가 |

따라서 서로 다른 Zone의 독립 VM 두 대와 LB만으로도 한 VM 장애 중 요청을 지속할 수 있다.
다만 고장 난 복제본을 자동으로 다시 만들지 않아 중복 용량은 수동 복구 전까지 감소한다.
동일하고 폐기 가능한 stateless 복제본은 관리형 VM 그룹을 기본으로 한다. 고유 이름·Disk·역할과
애플리케이션 자체 복제 관계를 가진 Primary/Standby, broker, domain controller, network
appliance는 독립 VM 복수 배치가 합법적인 프로덕션 패턴이다. Queue worker처럼 inbound
요청이 없는 그룹은 LB 없이도 사용할 수 있다.

이 값은 대상 Workload instance가 남는지만 뜻한다. Endpoint, 연결된 Workload와
상태까지 포함한 전체 업무 기능의 지속을 보장하지 않는다.

### 4.5 시간으로 표현된 복구 요구

“N분 안에 복구”는 다음 세 값이 있어야 판정할 수 있다.

```json
{
  "scope": "applicationTier",
  "failure": "singleVmFailure",
  "maxRecoverySeconds": 300,
  "sourceRef": "NFR-4"
}
```

`scope`가 없으면 앱 계층인지 단일 상태 Workload까지 포함한 종단 서비스인지 알 수 없다.
`failure`가 없으면 프로세스, VM, Zone, Region 장애 중 무엇을 시험할지도 정할 수 없다. 현재
지원 범위는 **단일 Region에서 단일 앱 VM이 멈춘 경우의 앱 계층 복구**까지다. 자체 운영
단일 상태 Workload와 Region 장애의 시간 목표는 `unsupported`로 남긴다.

다음 두 시간은 분리해 기록한다.

```text
serviceRecoverySeconds
= 장애 발생 → LB가 실패 backend를 제외 → 업무 요청이 다시 안정적으로 성공

capacityRecoverySeconds
= 장애 발생 → 새 VM·Docker·앱 준비 → health 통과 → 관리형 그룹의 원래 용량 회복
```

정상 replica가 남아 있다면 서비스 복구는 새 VM 생성보다 먼저 끝날 수 있다. 반대로 단일
instance는 전체 교체 경로가 서비스 복구의 임계 경로다. 따라서 “교체 4분”과 “서비스 중단
4분”을 같은 값으로 취급하지 않는다.

외부 자료는 후보 선정 근거로만 사용한다. AWS ALB는 기본 30초 간격과 연속 2회 실패를,
GCP autohealing 공식 예시는 10초 간격과 연속 3회 실패를 설명한다. Azure Load Balancer도
probe 간격과 threshold를 설정한다. VM 기동 연구는 AWS와 GCP에서 3개월 동안 각각 30만 건
이상을 측정했으며 CSP·Region·VM 계열에 따른 큰 편차를 보고했다. 이러한 값은 현재 생성한
Spring Boot image의 cloud-init, image pull, readiness, LB 등록시간을 포함하지 않으므로
복구시간 보장이 아니다.

- [AWS ALB health check](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-health-checks.html)
- [AWS ASG health 처리](https://docs.aws.amazon.com/autoscaling/ec2/userguide/health-checks-overview.html)
- [Azure Load Balancer health probe](https://learn.microsoft.com/en-us/azure/load-balancer/load-balancer-custom-probe-overview)
- [Azure VMSS automatic repairs](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-automatic-instance-repairs)
- [GCP MIG autohealing](https://cloud.google.com/compute/docs/instance-groups/autohealing-instances-in-migs)
- [AWS·GCP VM 기동시간 실측 연구](https://arxiv.org/abs/2107.03467)

외부 실측의 사용 예는 다음과 같다. IEEE CLOUD 2021 확장 보고서의 2020년 측정에서는
`t2.micro` cold start가 Region별 약 54.6~61.5초, GCP `f1-micro`는 약
116.9~133.9초였다. warm start는 각각 약 35.6~41초와 28.7~35.1초였다. 이는 특정
시기·VM·OS·image의 **VM 기동 성분**이며 현재 앱의 종단 복구시간이 아니다. Azure에는 같은
방법으로 세 CSP를 비교할 수 있는 공개 실측을 확보하지 못했다. Azure 공식 문서에 일부
cloud-init 배포가 120초를 넘는 문제 사례가 있으나, 이 역시 일반 평균이 아니라 지연 가능성을
보이는 보조 근거다. 따라서 CSP 간 빈칸을 임의 보간하지 않는다.

- [Azure cloud-init 120초 초과 문제 사례](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/linux/cloud-init-deployment-delay)

복구시간 산정과 검증은 다음의 최소 범위로 수행한다.

1. 고정 image에 목표 VM과 같은 CPU·memory 제한을 걸고 `container start → readiness → 첫
   업무 요청 성공`을 측정한다.
2. CSP health 설정과 외부 VM 기동 분포로 후보가 목표를 만족할 가능성이 있는지 판단한다.
   구성요소 평균을 단순 합산한 값은 `estimated`일 뿐이다.
3. 모든 VM SKU를 측정하지 않고, 실제 IaC가 선택한 `CSP + Region + VM SKU + image digest +
   topology`만 실제 cloud에서 짧게 3회 시험한다. 표본이 작으므로 p95를 만들지 않고 원시값과
   최댓값을 보고한다.
4. x86↔ARM, burstable↔일반 VM, CSP·Region, image·startup script가 바뀌거나 CPU·memory
   하한에 가까운 SKU로 바뀔 때만 다시 측정한다. 같은 조건의 유사 SKU 전체를 전수 측정하지
   않는다.
5. 실제 장애 시험에서 업무 복구 목표와 용량 회복을 모두 관찰한 구성만 `validated`로
   표시한다. 외부 자료와 로컬 측정만 있는 후보는 `estimated`로 표시한다.

### 4.6 영속 Workload의 가용성 경계

현재 자체 운영 영속 Workload는 `activeReplicas = 1`과 전용 Volume 하나만 허용한다.
앱 replica를 늘리거나 CSP 관리형 VM 그룹을 사용해도 영속 상태의 복제·승격·연결 전환은
생기지 않는다. 영속 상태 HA 요구는 관리형 데이터베이스를 범위에 포함하기 전까지
`unsupported`로 반환한다.

## 5. 파생 규칙

### 5.1 VM과 Volume

```text
active replica 하나
→ VM 하나

자동 교체 요구 + active replica 하나
→ CSP 관리형 VM 그룹 하나, desired capacity = 1
→ 교체 중 서비스 중단 가능

stateless active replica 둘 이상 + 단일 VM 장애 중 요청 지속
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

#### 5.2.1 다중 Zone 지원 방안

현재 지원하는 다중 Zone은 **단일 Region의 stateless Workload 계층**을 대상으로 하며 두
경로를 구분한다.

| 사용자 의도 | Compute 실현 | 자동 교체 | 단일 진입점 | 주장 가능한 범위 |
|---|---|---|---|---|
| 단순 배치 | 서로 다른 Zone의 독립 VM 2대 이상 | 없음 | 별도 요구 | Zone 분산 배치 |
| Zone 장애 중 업무 지속 | CSP 관리형 VM 그룹 2대 이상 | application health 기반 | LB 필요 | App 계층의 구조적 Zone 장애 대응 |

단순 배치는 VM 복제만으로 끝나지 않을 수 있다. 하나의 공개 Endpoint로 모든 복제본에
접근해야 하면 LB 또는 DNS 같은 트래픽 분배 결정을 별도로 받아야 한다. 쓰기 가능한 상태를
여러 복제본이 공유해야 하면 하나의 zonal disk를 여러 Zone VM에 연결하지 않고, 상태 소유자나
복제 방식을 별도 결정한다.

Zone 장애 중 업무 지속 경로는 다음 조건을 모두 만족해야 `availability = singleZone`을
구조적으로 충족했다고 판정한다.

1. 같은 Region에서 서로 다른 Zone을 둘 이상 선택한다.
2. App `activeReplicas`와 관리형 그룹의 최소·목표 용량이 2 이상이다.
3. 관리형 그룹의 배치 정책이 선택한 Zone들을 실제 대상으로 삼는다.
4. Zone 장애와 무관한 Load Balancer가 application health를 기준으로 정상 instance에
   트래픽을 보낸다.
5. 비정상 instance의 자동 교체 신호와 application readiness를 연결한다.
6. 필수 Connection 경로에 단일 상태 Workload가 있으면 App 계층만 충족으로 표시하고
   `endToEndService`는 충족으로 올리지 않는다.

| CSP | 다중 Zone App 구현 | 필수 배치 제약 |
|---|---|---|
| AWS | Launch Template + Auto Scaling Group + ALB/Target Group | ASG와 ALB가 서로 다른 AZ의 Subnet 둘 이상을 사용 |
| Azure | Zone-spanning VM Scale Set + Application Gateway 또는 선택된 regional LB | VMSS Zone 둘 이상, Gateway 전용 Subnet과 App Subnet 분리, application health 기반 repair |
| GCP | Regional Managed Instance Group + Backend Service + global external Application LB | Regional MIG의 target Zone 둘 이상, health check와 named port 일치 |

독립 VM 배치에서 network 범위는 CSP마다 다르다. AWS Subnet은 하나의 AZ에만 속하므로 선택
AZ마다 Subnet이 필요하다. Azure VNet/Subnet은 Region의 Zone들을 가로지르고 GCP Subnet은
regional 리소스이므로, Zone 수에 맞춰 Subnet을 기계적으로 복제하지 않는다. 세 경우 모두
VM 자체의 Zone 선택은 명시한다.

이 검사는 배치 구조가 Zone 장애에 대응할 수 있는지 확인하는 것이며, 남은 instance가 장애 중
목표 처리량을 감당한다는 뜻은 아니다. 장애 후 최소 처리량이나 복구시간 요구가 있으면 한 Zone의
instance를 제외한 상태에서 업무 부하와 `serviceRecoverySeconds`를 별도로 측정한다. 측정하지
않은 계획은 구조 조건 충족으로만 설명하고, 검증 근거는 `notMeasured`로 남겨 성능까지
검증됐다고 표현하지 않는다.

#### 5.2.2 다중 Region 지원 방안과 현재 경계

ASG·VMSS·Regional MIG 하나가 여러 Region을 포괄한다고 모델링하지 않는다. 다중 Region은
Region마다 독립된 network, 관리형 VM 그룹과 regional 진입 경로를 만들고 그 위에 전역
트래픽 계층을 두는 별도 토폴로지다.

```text
Global ingress / DNS
├─ Region A
│  ├─ regional network·Load Balancer
│  └─ managed VM group → Zone A1, A2
└─ Region B
   ├─ regional network·Load Balancer
   └─ managed VM group → Zone B1, B2
```

생성 지원 전에는 다음 결정이 모두 구조화돼야 한다.

1. `activePassive` 또는 `activeActive` 전략과 정상 시 Region별 active capacity
2. Region별 독립 ResourcePlan과 전역 ingress/DNS health·routing 정책
3. Region 간 내부 Connection과 image·artifact·secret의 배포 방법
4. 영속 데이터의 복제 방향, 일관성, RPO·RTO와 승격 권한
5. split-brain 방지, failover·failback 절차와 수동 개입 경계
6. 대기 Region을 포함한 정상 비용과 Region 간 전송 비용

현재 adapter는 이 계약과 Region 장애 실험을 구현하지 않았다. 따라서 다중 Region 요구를
단일 Region으로 조용히 축소하거나 VM 그룹 하나로 번역하지 않고, 원래 `regionRefs`와 장애
목표를 보존한 채 `unsupported`로 반환한다. 다중 Zone 대안은 비용·보호 범위가 다른 명시적
대안으로만 제시하며 자동 대체하지 않는다.

향후 지원은 다음 순서로 제한한다.

1. Region별 하위 계획과 전역 ingress를 ResourcePlan·다이어그램에 표현하되 생성하지 않는다.
2. 상태가 없는 App의 `activePassive`부터 Region별 관리형 그룹과 전역 health routing을 생성한다.
3. 관리형 PostgreSQL을 별도 범위로 승인한 뒤 cross-Region 데이터 복구 계약을 연결한다.
   자체 운영 PostgreSQL 복제는 계속 제외한다.
4. 실제 Region 장애를 대신하는 격리·전환·failback 실험과 Region별 cleanup을 통과한 경로만
   `validated`로 표시한다.

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
      │ HTTP :80
      ▼
AWS Region ap-northeast-2
└─ VPC 10.0.0.0/16
   ├─ Internet Gateway
   └─ AZ ap-northeast-2a
      └─ Public Subnet 10.0.1.0/24  [0.0.0.0/0 → IGW]
         └─ EC2 VM
            ├─ ENI: Public IPv4 또는 Elastic IP
            ├─ Security Group: inbound application HTTP port
            ├─ Workload A-1: HTTP endpoint
            └─ EBS Volume  # persistence.required=true일 때만
```

이 경우 논리 `public Endpoint`는 AWS에서 VM의 Public IPv4 또는 Elastic IP와
Security Group 규칙으로 실현된다. EBS Volume은 연결된 EC2와 같은 AZ에 있어야 한다.

### 6.2 연결된 두 Workload

```text
Internet client
      │ HTTP :80
      ▼
AWS Region ap-northeast-2
└─ VPC 10.0.0.0/16
   ├─ Internet Gateway
   └─ AZ ap-northeast-2a
      ├─ Public Subnet 10.0.1.0/24  [0.0.0.0/0 → IGW]
      │  └─ EC2 VM 1
      │     ├─ ENI: Public IPv4 또는 Elastic IP
      │     ├─ Web Security Group: inbound application HTTP port
      │     └─ Workload A-1: HTTP endpoint
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
      │ HTTP :80
      ▼
AWS Region ap-northeast-2
└─ VPC 10.0.0.0/16
   ├─ Internet Gateway
   ├─ Application Load Balancer
   │  ├─ Public Subnet A 10.0.1.0/24  [AZ ap-northeast-2a, route→IGW]
   │  ├─ Public Subnet B 10.0.2.0/24  [AZ ap-northeast-2b, route→IGW]
   │  ├─ ALB Security Group: inbound 80
   │  └─ HTTP Listener :80 → Target Group → health check
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

논리 `public Endpoint`는 ALB의 DNS 이름과 HTTP Listener로 실현된다. 두 App replica는
개별 EC2를 수기로 관리하지 않고 Launch Template을 사용하는 Auto Scaling Group이 서로
다른 AZ의 private App Subnet에 유지한다. Public Subnet 두 개는 ALB의 CSP 제약에서
파생된 것이지 Workload 토폴로지의 고정 숫자가 아니다. 필수 연결 대상인 Workload B가
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
| 공개 L7 진입 | ALB, HTTP listener, target group, health check | Public IP + Application Gateway 전용 Subnet + HTTP listener/backend/probe | global forwarding rule + Target HTTP Proxy + URL map + backend service + health check + instance groups |
| backend 묶음 | Target Group과 Auto Scaling Group 연결 | Backend Pool에 VM Scale Set NIC 등록 | Regional MIG를 Backend Service에 등록 |
| 트래픽 제한 | ALB·App·Data Security Group | Application Gateway Subnet/NSG와 App·Data NSG | VPC Firewall Rule과 health-check/proxy source range |

단일 Workload를 VM에 직접 공개할 때도 차이가 있다.

| 배포 의도 | AWS | Azure | GCP |
|---|---|---|---|
| VM 직접 public Endpoint | ENI의 Public IPv4/Elastic IP + Security Group + public route/IGW | NIC IP configuration의 Public IP + NSG | VM `network_interface.access_config`의 External IP + Firewall Rule |
| 사설 상태 Workload | private Subnet의 EC2와 EBS | App/Data Subnet의 VM NIC와 Managed Disk | regional Subnet의 zonal VM과 Persistent Disk |

### 6.6 Azure 변형

Azure에서는 VNet과 Subnet을 AZ 아래에 중첩하면 안 된다. VNet은 한 Region에 있고
그 Region의 Availability Zone을 가로지르며, VM이 Zone을 선택한다. Application Gateway는
VNet 안의 전용 Subnet이 필요하다.

```text
Internet client
      │ HTTP :80
      ▼
Azure Region Korea Central
├─ Public IP
└─ VNet 10.0.0.0/16  [Region 범위, Zone을 가로지름]
   ├─ ApplicationGatewaySubnet 10.0.1.0/24  [전용 Subnet]
   │  └─ Application Gateway v2
   │     ├─ Frontend IP configuration ← Public IP
   │     ├─ HTTP Listener :80
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
      │ HTTP :80
      ▼
Global external Application Load Balancer
├─ Global External IP
├─ Global Forwarding Rule
├─ Target HTTP Proxy
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
Target HTTP Proxy, URL Map, Backend Service와 Health Check는 `load-balanced-ingress`
capability를 여러 GCP native resource가 함께 실현하는 구성이다.

- [GCP global external Application Load Balancer 구성](https://docs.cloud.google.com/load-balancing/docs/https/setup-global-ext-https-compute)
- [GCP target proxy 경로](https://docs.cloud.google.com/load-balancing/docs/target-proxies)
- [GCP VPC·Subnetwork 범위](https://docs.cloud.google.com/vpc/docs/vpc)

## 7. 네트워크와 Subnet

### 7.1 네트워크 의도

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
두 개 요구한다. 이는 CSP 제약이지 Workload 토폴로지의 고정 숫자가 아니다.
[AWS ALB Subnet 문서](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html)

## 8. 선택된 CSP의 배포 다이어그램

EasyDep은 CSP를 앞단에서 받으므로 한 실행에서 해당 CSP의 다이어그램 하나만
생성한다. 다이어그램과 IaC는 모두 CSP로 구체화된 같은 `ResourcePlan`을 사용한다.
별도의 공급자 독립 다이어그램이나 추가 중간 계획을 만들지 않으며 PlantUML을 IaC 입력으로
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

Provider 정본 원장은 Root/OS/Boot Disk, 기본 Route·ACL, 관리형 VM 그룹의 child VM·NIC처럼
CSP가 자동 생성하는 실제 하위 요소도 누락 감사와 검증을 위해 보존한다. 그러나 기본 배포
그래프에는 IaC에 직접 작성·참조하거나 상위 리소스 안에 설정하는 요소와 독립
Attachment·Association만 표시한다. 자동 생성 요소를 통과하던 기능 경로는 화면 전용 축약
간선으로 이어 보여 주며, 이 축약 간선은 새로운 IaC 리소스를 뜻하지 않는다.

화면의 이름은 다음 규칙으로 실제 대상의 층위를 드러낸다.

| 대상 | 표시 규칙 | 예 |
|---|---|---|
| 독립 CSP 리소스 | CSP 공식 리소스명 | `VPC Network`, `Application Gateway` |
| 상위 리소스 내부 구성 | `상위 리소스 / 구성명` | `Application Gateway / Backend HTTP Settings` |
| Terraform이 별도 객체로 관리하는 연결 | `Terraform: 정확한 resource type` | `Terraform: aws_eip_association` |
| 여러 요소를 접은 화면 표현 | `[화면 묶음] 설명` | `[화면 묶음] Global external Application Load Balancer 경로` |

마지막 행은 CSP 리소스나 공급자 중립 리소스 모델이 아니라 오직 화면 복잡도를 줄이는
표현이다. 상세 패널과 IaC 목록에서는 묶음 안의 실제 요소와 정확한 Terraform type을 다시
펼쳐 보여 준다.

### 8.3 앱-리소스 바인딩 표시

클라우드 리소스가 존재하는 것만으로 앱 기능이 동작하지 않는다. 다음과 같이 provider
리소스에서 guest 구성과 앱 계약까지 이어지는 사슬은 **기능 의존성 그래프**의 경계로
표시한다. 생성 그래프에 이 사슬을 겹치지 않는다.

```text
영속성:
Disk → Attachment → block device → filesystem → mount/fstab
     → Docker bind → PostgreSQL data path → PostgreSQL

요청 진입 없음: 호출자·공인 주소·LB를 추가하지 않음
내부 직접: 내부 호출자 → 사설 NIC → VM → server.port → Spring Boot
공개 직접: 인터넷 호출자 → 고정 공인 주소 → NIC → VM → server.port → Spring Boot
공개 LB: 인터넷 호출자 → LB → healthy backend → VM → server.port → Spring Boot

앱 image:
EasyDep 생성 소스 + Dockerfile → VM docker build → VM local image → Spring Boot

PostgreSQL image:
Docker Hub 공식 postgres:17-bookworm → State VM pull → PostgreSQL

DB 연결:
DB endpoint → spring.datasource.* → DataSource
Spring Boot → DataSource → DB endpoint → PostgreSQL
```

노드는 다음 다섯 분류만 사용한다.

| 분류 | 예 |
|---|---|
| 실제 CSP 요소 | EC2, EBS Volume, Target Group, VMSS, MIG |
| 앱이 요구하는 값·인터페이스 | `server.port`, readiness/liveness, DataSource, data path |
| VM·컨테이너 내부 구성 | filesystem, mount, Docker bind, 환경값·secret 주입 |
| 실행 중 workload | Spring Boot, PostgreSQL |
| 실제 외부 입력·호출자 | 서비스 호출자, Docker Hub 공식 `postgres:17-bookworm` |

범용 Registry, 외부·관리형 DB, Secret Manager, 외부 API, CSP SDK·workload identity는
현재 생성·배포 범위가 아니므로 목표 그래프에 추상 노드로 넣지 않는다.

### 8.4 화살표 방향

시각화는 **생성 의존성**과 **기능 의존성**을 별도 그래프로 전환해 본다. 따라서 한
화살표가 생성 순서인지 runtime 요청인지 범례를 보고 추측할 필요가 없다.

| 관계 | `A → B`의 의미 |
|---|---|
| 생성·준비 선행 | A가 먼저 준비되어야 B를 생성·시작할 수 있음 |
| 설정값 전달 | A의 값이 B의 설정에 들어감 |
| 요청·데이터 | 실행 중 요청이나 데이터가 A에서 B로 이동함 |
| 상태 신호 | A가 B를 검사하거나 A의 결과를 B가 traffic·repair 판단에 사용함 |

생성 의존성 그래프는 **선행 리소스 → 후행 리소스**로 그리고 생성·참조·내부 구성 및
Attachment·Association 관계만 포함한다. Attachment·Association은 두 선행 리소스를 받아
연결을 만드는 후행 객체로 표현하고, 단순히 Terraform block의 문법 방향을 화살표로
복사하지 않는다.

기능 의존성 그래프는 실제 요청·데이터 이동, build/pull, mount·DataSource 및 health 신호의
방향으로 그리고 생성 순서 간선을 포함하지 않는다. 두 그래프는 같은 선택 토폴로지를
서로 다른 질문으로 투영한 것이며 별개의 배포 계획이 아니다.

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

생성 표에는 직접 참조를 적는다. 시각화의 생성 화살표는 선행 리소스에서 후행 리소스로
향하므로, EC2가 Subnet을 참조하고 Subnet이 VPC를 참조하면 `VPC → Subnet → EC2`로
표시한다. EC2 행에 전이 선행 대상인 VPC를 다시 중복하지 않는다. 상위 리소스 생성 결과 CSP가
실제 하위 리소스를 자동 생성하는 경우는 `materialize`로 분리한다. 따라서 IaC block이 없다는
이유로 Primary ENI·OS/boot disk·관리형 그룹의 child VM을 그림에서 생략하지 않는다.

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
| 진입 | Application Load Balancer, HTTP Listener, Target Group, Health Check | HTTP 요청을 정상 앱 VM으로 전달 |
| 실행 | EC2 Instance, ENI, root EBS | 단일 앱 또는 자체 운영 상태 Workload 실행 |
| 저장 | EBS Volume, Volume Attachment | 상태 Workload 데이터 보존 |
| 권한 | IAM Role, Instance Profile | 앱이 AWS API 자격 증명을 요구할 때만 사용 |

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
| HTTP Listener | ALB + Target Group + port 80 | HTTP 앱 진입 | 공식 |
| 직접 공개 EC2 | AMI + instance type + Subnet + Security Group | 단일 앱 직접 진입 | 실측·공식 |
| EC2 Primary ENI | 직접 EC2 또는 ASG가 만든 EC2 + Subnet + Security Group | 별도 ENI를 제공하지 않는 기본 생성 | 공식 |
| EC2 Root EBS | EBS-backed AMI + EC2 또는 Launch Template block device mapping | EBS-backed Linux VM | 공식 |
| ASG member EC2 | Auto Scaling Group + Launch Template + desired capacity | 앱 HA | 공식 |
| ALB service-managed ENI·Public IPv4 | internet-facing ALB + 활성 AZ Subnet | HTTP 앱 진입 | 공식 |
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
| 영속성 | EBS attachment → guest filesystem/mount → 전용 하위 data directory → Docker bind → Workload data path | 쓰기→container/VM 재시작→재조회 |
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
| 진입 | Public IP, Application Gateway v2 | 공개 HTTP 진입 |
| Gateway 구성 | frontend IP/port, HTTP listener, rule, backend pool, backend setting, probe | HTTP를 정상 앱 backend로 전달 |
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
| Gateway listener/rule | frontend port 80 + backend pool + backend setting | HTTP 앱 진입 | 공식·계약 |
| Gateway probe | backend protocol/port + health path | 앱 HA | 공식·계약 |
| VM Scale Set | VM image/SKU + NIC configuration + App Subnet + App NSG | 앱 HA | 공식 |
| VMSS backend 연결 | VMSS NIC configuration + Gateway Backend Pool | 앱 HA | 공식 |
| Application Health extension | 앱 health endpoint의 protocol/port/path | 앱 HA | 공식·계약 |
| automatic repair policy | VM Scale Set + Application Health extension | 앱 HA | 공식·계약 |
| 직접 공개 NIC | App Subnet + Public IP + App NSG | 단일 앱 직접 진입 | 실측·공식 |
| 상태 NIC | Data Subnet + Data NSG | 영속 Workload | 실측·공식 |
| 상태 VM | 상태 NIC + image + VM SKU + OS Disk | 영속 Workload | 실측·공식 |
| VM OS Managed Disk | image 기반 Virtual Machine storage profile | 단일 VM | 공식 |
| VMSS child VM·NIC·OS Disk | VM Scale Set model + capacity | 앱 HA | 공식 |
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
| 진입 | Global Address, Global Forwarding Rule | 공개 HTTP frontend |
| LB 경로 | Target HTTP Proxy, URL Map, Backend Service, Health Check | 요청을 정상 MIG backend로 전달 |
| 실행 | Compute Engine Instance, network interface, boot disk | 단일 앱 또는 상태 Workload 실행 |
| 저장 | Persistent Disk, Attached Disk | 상태 데이터 보존 |
| 권한 | Service Account와 IAM binding | Google API 접근이 필요할 때만 사용 |

#### 9.4.2 생성 의존성

| 생성 대상 | 참조하거나 먼저 준비할 대상 | 조건 | 근거 |
|---|---|---|---|
| VPC Network | Project | 항상 | 공식 |
| system-generated default Route | VPC Network | default route를 삭제·대체하지 않을 때 | 공식 |
| Subnetwork | VPC Network + Region + CIDR | 항상 | 실측·공식 |
| system-generated subnet Route | Subnetwork의 primary·secondary IP range | 항상 | 공식 |
| Firewall Rule | VPC Network + source range/target tag 또는 service account + port | 항상 | 실측·계약 |
| Instance Template | image/machine type + boot disk + network/Subnetwork + App identity/startup | 앱 HA | 공식·계약 |
| Regional MIG | Instance Template + Region + 선택 Zone들 | 앱 HA | 공식 |
| underlying Regional Instance Group·member VM | Regional MIG + target size | 앱 HA | 공식 |
| MIG autohealing policy | Regional Managed Instance Group + autohealing Health Check | 앱 HA | 공식·계약 |
| LB Health Check | 앱 protocol/port/health path | 앱 HA | 공식·계약 |
| autohealing Health Check | 앱 protocol/port/health path | 앱 HA | 공식·계약 |
| Backend Service | LB Health Check + Zone별 MIG instance group backend | 앱 HA | 공식 |
| URL Map | Backend Service | L7 앱 HA | 공식 |
| Target HTTP Proxy | URL Map | HTTP | 공식 |
| Global Forwarding Rule | Target HTTP Proxy + global IP + port 80 | HTTP | 공식 |
| 직접 공개 VM | boot disk + network interface + Subnetwork + `access_config` + firewall target | 단일 앱 직접 진입 | 실측·공식 |
| VM boot Persistent Disk | image + Instance 또는 Instance Template initialize params | 모든 image 기반 VM | 공식 |
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
| 앱 진입 | Forwarding Rule → HTTP Proxy → URL Map → Backend Service → healthy MIG instance | health와 외부 업무 요청 |
| 앱 VM 자동 복구 | 별도 autohealing Health Check → Regional MIG → instance 재생성 | 한 instance 장애 중 연속 요청과 복구 확인 |
| 앱→상태 연결 | App tag/service account → Firewall Rule → 상태 VM의 내부 port | 앱을 통한 상태 쓰기·조회 |
| 영속성 | attached Persistent Disk → guest filesystem/mount → Docker volume → data path | 쓰기→container/VM 재시작→재조회 |
| 외부 송신 | private Subnetwork → Cloud NAT 또는 허용된 외부 주소 | 외부 의존 API probe |
| Google API 접근 | VM Service Account → IAM binding → 대상 API | 최소 권한 API probe |

공식 근거: [VPC와 Subnetwork](https://docs.cloud.google.com/vpc/docs/vpc),
[global external Application Load Balancer](https://docs.cloud.google.com/load-balancing/docs/https/setup-global-ext-https-compute),
[Regional Managed Instance Group](https://docs.cloud.google.com/compute/docs/instance-groups/distributing-instances-with-regional-instance-groups),
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

새 filesystem의 mount root를 container data path에 바로 연결하지 않는다. ext 계열의
`lost+found`처럼 filesystem이 만든 항목이 빈 디렉터리를 전제로 하는 runtime 초기화를 깨뜨릴
수 있기 때문이다. ResourcePlan은 Workload의 논리적 `runtime.dataPath`를 보존하고, CSP별
bootstrap은 guest mount 아래 전용 하위 디렉터리를 생성·권한 설정하고, 그 디렉터리를 bind하거나
runtime 설정으로 실제 data path에 지정한다.
이는 PostgreSQL 전용 토폴로지 규칙이 아니라 Volume–Workload 실현 경계의 공통 규칙이다.
직접 관찰한 PostgreSQL 실패는 이 공통 규칙을 필요로 한 한 사례이며, PostgreSQL의 공식
`initdb` 문서도 mount point 아래 하위 디렉터리 사용을 안내한다:
[PostgreSQL `initdb`](https://www.postgresql.org/docs/current/app-initdb.html).

앱 HA 합격은 리소스 개수로 판정하지 않는다. 고정된 요청을 보내는 동안 한 앱 VM을
중지하고, 허용한 복구시간 내 오류율·지연시간·업무 불변식을 만족하며 CSP 관리형 그룹이
instance를 교체하는지 확인한다. 장애 시각, 마지막 실패 요청, 처음으로 다시 성공한 요청,
연속 성공이 안정화된 시각, 그룹 용량 회복 시각을 각각 기록해 `serviceRecoverySeconds`와
`capacityRecoverySeconds`를 계산한다. VM이 `running`이 됐거나 health가 한 번 성공한 것만으로
업무 복구를 판정하지 않는다.

실제 cloud 검증은 선택된 최종 SKU에 대해서만 수행한다. CSP·Region·SKU·image digest,
health interval·threshold, startup script digest, app replica 수와 LB 구성을 실행 manifest에
남긴다. 부하 발생기 위치와 실패 여부도 함께 기록해 네트워크 또는 시험 도구 지연을 앱 복구
지연으로 오인하지 않는다. 시험 뒤에는 해당 run ID와 state/manifest가 소유한 리소스의 잔여가
0인지 확인한다.

### 9.7 현재 증거와 남은 검증

제품 정본 [claims.json](../app/core/cloudkb/depkb/claims.json)은 프로비저닝 33개와
런타임 11개, 총 44개 claim을 포함한다. 반복 상태는 성공 38개, 실패 5개, 대기 1개다.
실패와 대기 claim은 확정 규칙으로 점수화하지 않는다.

프로비저닝 claim은 32개가 반복됐고 Azure frontend 선택 조건 1개가 대기 상태다.
런타임 인프라 신호의 상세 결과는 다음과 같다.

| CSP | 관찰 관계 | 기능 신호 | 반복 상태 |
|---|---|---|---|
| AWS | Subnet → default route | 외부 HTTP 송신 | 실패 |
| AWS | Subnet → default route | 외부 TCP 수신 | 성공 |
| AWS | VM → Security Group | 외부 TCP 수신 | 성공 |
| AWS | VM → Public IP | 외부 TCP 수신 | 성공 |
| AWS | VM → workload identity | metadata 자격 증명 | 실패 |
| Azure | Load Balancer → VM | LB 서비스 응답 | 실패 |
| Azure | NIC → Public IP | 외부 TCP 수신 | 성공 |
| Azure | Subnet → NSG | 외부 TCP 수신 | 성공 |
| GCP | VPC Network → default route | 외부 TCP 수신 | 실패 |
| GCP | VM → Firewall Rule | 외부 TCP 수신 | 실패 |
| GCP | VM → External IP | 외부 TCP 수신 | 성공 |

여기서 실패는 관계가 불필요하다는 결론이 아니다. 동결한 기대 신호를 반복 실행에서
재현하지 못했다는 뜻이므로 원인 확인 전에는 성공이나 실패의 근거로 사용하지 않는다.

기존 claim 반복 상태는 과거 동결 관찰을 보존한다. 후속 도메인 중립 앱 실험은 다음 관리형
기능 경로를 세 CSP에서 각 1회 추가 확인했다.

- AWS Auto Scaling Group과 ALB/Target Group의 결합
- Azure VM Scale Set의 health 기반 automatic repair와 관리형 진입 결합
- GCP Regional MIG의 autohealing과 global external Application Load Balancer 결합

- AWS: ALB health–ASG target–새 EC2 교체
- Azure: Standard LB probe–VMSS application health–새 VM 교체
- GCP: Backend health–MIG autohealing–동일 VM 관리형 재기동

각 실행은 `apply → ready → 업무 요청 → App 장애 → 관리형 복구 → 업무 재확인 → destroy →
해당 run 잔여 0`을 기록했다. 이는 App 계층 1회 개발 관찰이며 단일 State VM을 포함한 종단
HA나 CSP SLA를 증명하지 않는다.

AWS와 GCP의 E2는 현재 ResourcePlan이 선택하는 관리형 진입 종류와 일치한다. Azure E2는
Standard Load Balancer를 사용했지만 현재 ResourcePlan은 Application Gateway를 선택한다.
그러므로 Azure의 증거는 VMSS 자동 복구와 대체 진입 경로의 관찰로만 연결하고, Application
Gateway를 통한 장애 중 업무 연속성은 `notMeasured`로 보존한다. 또한 E1은 State VM의
재기동·reset 뒤 데이터 보존을 관찰했다. 별도 E3에서는 AWS·Azure·GCP 모두 State VM을
교체하고 기존 data disk를 재연결한 뒤 새 사설 endpoint만 App runtime에 주입해, App image를
재빌드하지 않고 기존 값을 읽는 경로를 각 1회 관찰했다.

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
| 실행 | 생성 소스·Dockerfile, runtime, 시작 명령 | VM 직접 `docker build`와 process 시작 |
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

### 11.4 ResourcePlan 종단 셋업 순서

기준 시나리오는 선택한 Endpoint와 운영 수준에 따라 `App VM 또는 관리형 App VM 그룹`을
만들고, PostgreSQL이 필요할 때만 `사설 State VM → 영속 Data Disk`를 더한다. 공개 요청이
항상 Load Balancer를 뜻하지 않으며, 진입 없음·내부 직접·공개 직접·공개 LB를 구분한다.
각 단계는 이전 단계의 산출물과 완료 gate를 소비한다.

| 단계 | 주요 작업 | 완료 gate |
|---:|---|---|
| 1 | CSP, Endpoint, State, 배치·운영 수준 결정 | LB와 VM 그룹을 같은 개념으로 취급하지 않음 |
| 2 | EasyDep 생성 앱 소스·Dockerfile 묶음과 checksum 고정 | build 입력 파일이 완결되고 전달 대상을 식별 |
| 3 | Network, Subnet, 보안 정책, 선택 주소·LB, build/pull outbound 생성 | 선택하지 않은 진입 방식의 리소스가 섞이지 않음 |
| 4 | 선택 시 State VM·Disk 생성과 공식 `postgres:17-bookworm` pull·실행 | State VM 재부팅 뒤 PostgreSQL 데이터 보존 |
| 5 | build 입력을 App VM에 전달해 직접 `docker build`하고 App·선택 DataSource 실행 | image inspect, localhost 업무 API와 선택 시 migration·query 성공 |
| 6 | 선택 Endpoint 요청과 선택한 readiness·liveness 복구 검증 | 선택 경로만 성공하고 주장한 복구 범위와 evidence가 일치 |

State Disk guest 준비는 반드시 다음 순서를 지킨다.

```text
Attachment 완료
→ CSP별 stable device 식별
→ 기존 filesystem 확인, 비어 있을 때만 format
→ UUID mount와 fstab
→ mount 아래 전용 child directory
→ Docker bind
→ workload data path
→ workload 시작
```

### 11.5 앱-배포 바인딩 최소 체크리스트

| 바인딩 | 완료 조건 |
|---|---|
| HTTP port | `server.port` = Docker target port = firewall 허용 port = LB backend/named port |
| App image build | 생성 소스 + Dockerfile → VM `docker build` → local image, base image outbound |
| Readiness | 실제 앱 endpoint와 LB traffic 포함·제외 판단 연결 |
| Liveness | 앱 프로세스 생존 상태를 그룹 repair와 grace에 연결 |
| PostgreSQL image | Docker Hub 공식 `postgres:17-bookworm` → State VM pull·실행 |
| DB | State VM endpoint → `spring.datasource.*` → DataSource → PostgreSQL |
| Disk | 소유 VM과 Disk → Attachment → 안정 device → idempotent filesystem → mount → Docker bind |
| 내부 직접 요청 | 내부 caller → 사설 NIC → VM → 앱 port 종단 일치 |
| 공개 직접 요청 | 인터넷 caller → 고정 공인 주소 → NIC → VM → 앱 port 종단 일치 |
| 공개 LB 요청 | 인터넷 caller → LB → healthy backend → VM → 앱 port 종단 일치 |

### 11.6 최종 산출물과 사용자 배포 계약

EasyDep의 사용자 인도 단위는 애플리케이션 소스 파일만이 아니라 **재현 가능한 배포
번들**이다. 소스와 IaC를 각각 내려받은 뒤 사용자가 임의의 순서로 조립하게 하지 않는다.
선택한 `ResourcePlan`, 애플리케이션–클라우드 계약, 정확한 artifact와 IaC를 하나의 번들에
보존하고, 사용자는 하나의 진입 명령으로 plan부터 기능 확인까지 실행할 수 있어야 한다.

목표 번들의 최소 구조는 다음과 같다. 실제 앱의 build tool에 따라 내부 소스 경로는 달라질
수 있지만 사용자 배포 인터페이스와 의미 파일은 고정한다.

```text
easydep-output/
├─ application/                  # 생성한 앱 소스와 테스트
│  ├─ Dockerfile
│  └─ .dockerignore
├─ infrastructure/
│  ├─ foundation/               # Network, 선택 Registry와 공통 기반
│  └─ workload/                 # VM·VM 그룹, LB, Disk와 runtime binding
├─ scripts/
│  ├─ doctor.sh / doctor.ps1
│  ├─ plan.sh / plan.ps1
│  ├─ deploy.sh / deploy.ps1
│  ├─ status.sh / status.ps1
│  └─ destroy.sh / destroy.ps1
├─ resource-plan.json
├─ easydep.lock.json
├─ terraform.tfvars.example
├─ .env.example
└─ README.md
```

`resource-plan.json`은 다이어그램과 IaC를 만든 정본 계획을 보존한다. `easydep.lock.json`은
CSP provider와 도구 버전, 앱 입력 checksum, container image reference와 확정된 digest를
기록한다. 예제 변수 파일에는 필요한 **키 이름과 설명만** 넣고 cloud credential, private key,
DB password 같은 secret 값은 번들에 기록하지 않는다.

사용자에게 보이는 기본 흐름은 다음 두 명령 중 운영체제에 맞는 하나다.

```text
./scripts/deploy.sh
.\scripts\deploy.ps1
```

두 진입점은 같은 단계와 gate를 구현한다.

| 단계 | 스크립트 책임 | 중단·완료 조건 |
|---:|---|---|
| 1 | CSP CLI 로그인, Docker, OpenTofu/Terraform, 필수 변수와 권한 확인 | 외부 변경 전에 누락 입력을 보고 |
| 2 | 정적 검증, `init`, `plan`과 생성·변경·삭제 요약 | 유효한 Plan과 예상 비용 요소를 출력 |
| 3 | 적용할 CSP·account/project·Region·리소스 목록을 보여 주고 승인 요청 | 명시적 승인 전에는 `apply`하지 않음 |
| 4 | Network와 선택 Registry 등 artifact보다 먼저 필요한 foundation 적용 | foundation ID와 Terraform state checkpoint 저장 |
| 5 | 앱 image를 한 번 build·push하고 변경 불가능한 digest 확정 | Registry에서 동일 digest를 조회 가능 |
| 6 | digest를 참조하는 VM·VM 그룹, 선택 LB·State VM·Disk 적용 | 모든 CSP 생성 참조와 guest bootstrap 완료 |
| 7 | 선택 Endpoint, health, migration·query와 재시작 후 보존 검증 | 선택한 업무 기능과 복구 주장에 맞는 evidence 생성 |
| 8 | endpoint, 리소스 ID, image digest, state와 검증 결과 출력 | `status`와 실패 단계 재개에 필요한 checkpoint 완결 |

Registry를 사용하는 목표 artifact 경로는 `foundation apply → image build/push → workload
apply`의 두 IaC 단계로 나눈다. Registry 주소는 foundation output에서 얻고, VM과 VM 그룹에는
tag가 아니라 확정된 digest를 전달한다. 이렇게 해야 자동 교체·scale-out instance도 최초
배포와 같은 앱 artifact를 실행한다. 현재 VM 직접 build 실험 경로를 유지하는 동안에도 위
번들 인터페이스는 바꾸지 않고, 단계 5의 realization만 `VM으로 checksum 고정 build 입력 전달
→ remote build → local image ID 기록`으로 대체한다.

보조 명령의 계약은 다음과 같다.

| 명령 | 역할 |
|---|---|
| `doctor` | 아무 리소스도 만들지 않고 로컬 도구·인증·입력·권한을 검사 |
| `plan` | 외부 변경 없이 Terraform Plan과 artifact 단계, 예상 생성 대상을 표시 |
| `deploy` | 승인 후 미완료 단계부터 배포하고 종단 기능 검증까지 수행 |
| `status` | checkpoint와 CSP 조회를 대조해 endpoint, VM·LB·Disk, health와 마지막 gate를 표시 |
| `destroy` | 정확한 ResourcePlan·state 대상과 보존 데이터를 먼저 표시하고 별도 승인 후 역순 정리 |

스크립트는 단순 명령 모음이 아니라 재개 가능한 배포 orchestrator다. 각 단계가 끝날 때
`run ID`, 앱 ID, ResourcePlan digest, Terraform state 식별자, 생성 리소스 ID와 image digest를
기록한다. 실패 시 전체를 처음부터 반복하지 않고 이 값들이 현재 번들과 일치하는지 확인한 뒤
실패 단계부터 재개한다. `destroy`는 workload를 먼저 내리고 Registry·Network foundation을
나중에 정리하며, 영속 데이터를 삭제하는 선택은 일반 compute 정리와 분리해 재확인한다.

기본 스크립트는 `-auto-approve`를 내장하지 않는다. 비대화형 CI 사용은 별도 명시 옵션과
승인 artifact가 있을 때만 허용한다. Terraform state는 소스 번들에 포함하지 않으며, local
state를 사용할 때에는 보관 위치와 민감성을 경고하고 팀 배포에는 locking·encryption을 갖춘
remote backend 입력을 요구한다.

## 12. 현재 지원 상태

| 항목 | 모델 | 현재 생성·배포 |
|---|---|---|
| 일반 Workload·Connection·Endpoint | 목표 | 설계의 명시적 실행 단위·연결과 외부 protocol을 ResourcePlan에 반영하고 정적으로 대조 |
| Workload replica와 VM 배치 | 목표 | 단일 App 계층과 독립 State 배치까지 부분 구현 |
| Workload 영속 Volume | 목표 | 소유 Workload의 Compute–Volume 계획·정적 attachment 검사 구현 |
| Spring Boot 단일 Workload | 지원 | 지원 |
| React를 Spring Boot에 포함 | 목표 | 미구현 |
| 자체 운영 PostgreSQL | 제한 지원 | 중립 앱 3사 E1과 수강신청 AWS 1회에서 사설 연결·별도 Volume·재기동 보존 확인 |
| CSP 맞춤 배포 다이어그램 | 목표 | ResourcePlan 기반 PlantUML 생성과 계획 노드 포함 검사 구현. 실제 배포 검증은 별도 |
| 단일 Region·다중 Zone App | 제한 지원 | 단순 배치와 관리형 장애 대응을 구분해 ResourcePlan에 투영. 관리형 E2는 단일 VM 장애만 관찰했으며 독립 VM 다중 Zone apply, Zone 장애·잔여 처리량은 미측정 |
| 다중 Region과 전역 진입 | 표현 가능 | 미지원 |
| CSP 관리형 앱 VM 그룹 HA | 제한 지원 | ResourcePlan 투영과 중립 앱 3사 1회 통과. 반복·생성 앱 E2 검증은 필요 |
| 사용자 배포 번들·진입 스크립트 | 목표 | 앱·Dockerfile·검증된 Terraform과 배포 메모는 생성하지만 Docker-on-VM용 `doctor/plan/deploy/status/destroy` 진입점, 단계 checkpoint와 OS별 wrapper는 미구현. Kubernetes 전용 script를 VM 계약으로 간주하지 않음 |
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
7. 다중 Zone 목적이 불명확하면 단순 배치와 Zone 장애 중 업무 지속 중 하나를 질문한다.
8. 단순 배치는 독립 VM을 서로 다른 Zone에 두고, App HA는 CSP 관리형 VM 그룹·Zone 둘
   이상·Load Balancer로 실현한다.
9. 다중 Region은 Region별 그룹과 전역 ingress가 필요한 별도 토폴로지이며 현재
   `unsupported`로 반환한다.
10. 영속 Workload HA 요구는 현재 `unsupported`로 반환한다.
11. 선택된 CSP의 제약으로 하나의 ResourcePlan을 만든다.
12. 같은 ResourcePlan에서 배포 다이어그램과 IaC를 생성한다.
13. 앱 소스만이 아니라 ResourcePlan·IaC·잠금 정보와 OS별 배포·상태·삭제 진입점을 하나의
    사용자 배포 번들로 인도한다.
14. IaC, runtime과 업무 기능을 서로 다른 층위로 검증한다.
15. 근거 없는 값은 추측하지 않고 미지원 기능은 축소하지 않는다.
16. 성능 때문에 VM 수 변경이 필요하면 토폴로지 재검토를 요청한다.
```

## 14. 2026-08-15~17 runtime 의존성 관찰 반영

관리형 HTTP를 선택한 ResourcePlan에는 `runtimeEvidence.managedHttpIngress`를 남긴다. AWS의
Application Load Balancer와 GCP의 External Application Load Balancer는 E2 실행에서, Azure의
Application Gateway는 2026-08-17 별도 HTTP 실행에서 listener→backend→readiness·업무 요청
경로를 관찰했다. 세 실행 모두 backend 경로가 사라지면 기능이 상실되고 복원하면 회복되는 것을
확인했으므로 해당 기능 경로만 `observed`로 기록한다. 이는 CSP별 1회 개발 관찰이며 전송 보안,
가용성 SLA와 성능은 계속 `notMeasured`다.

수강신청 E1의 세 CSP 실행은 Workload·Connection·Volume이 실제 앱 기능으로 이어지는지
확인하는 보조 사례다. Azure·GCP는 평가 harness가 직접 배포했고 AWS도 bootstrap 보정이
있었으므로, ResourcePlan→IaC 자동 생성의 성공 증거와는 분리한다.
