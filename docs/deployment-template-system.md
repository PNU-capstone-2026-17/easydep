# EasyDep 배포 템플릿·다이어그램·IaC 통합 안내서

> 현재 기준: 2026-09-03, Docker-on-VM 배포

이 문서는 EasyDep의 배포 관련 로직을 한곳에서 설명한다. 어떤 입력을 받아 어떤 배포
형태를 선택하는지, AWS·Azure·GCP용 리소스를 어떻게 만드는지, 다이어그램과 OpenTofu
파일이 어떤 관계인지, 실제 클라우드 검증에서 무엇을 발견하고 고쳤는지를 순서대로 다룬다.

핵심 원칙은 간단하다.

- LLM은 배포 컴포넌트의 **영어 표시 이름만** 제안한다.
- VM 수, 네트워크, 디스크, Load Balancer, Secret 권한은 코드가 정한다.
- 다이어그램과 IaC는 같은 `ResourcePlan`에서 만든다.
- 구현이 끝난 뒤 실제 포트와 상태 검사 경로를 읽어 최종 IaC에 반영한다.
- EasyDep은 배포 파일을 만들지만 클라우드에 대신 배포하지 않는다. 최종 실행은 사용자가 한다.

---

## 1. 먼저 알아둘 용어

| 용어 | 쉬운 설명 |
|---|---|
| CSP | Cloud Service Provider의 줄임말이다. 이 문서에서는 AWS, Microsoft Azure, Google Cloud를 뜻한다. |
| workload | VM 안에서 실행되는 애플리케이션 단위다. 현재는 주로 Docker 컨테이너 하나에 대응한다. |
| VM | 클라우드에서 빌려 쓰는 가상 컴퓨터다. EasyDep은 VM 위에서 Docker 컨테이너를 실행한다. |
| Docker-on-VM | Kubernetes 대신 VM에 Docker와 Docker Compose를 설치해 애플리케이션을 실행하는 방식이다. 현재 EasyDep이 지원하는 배포 방식이다. |
| Container | 애플리케이션과 실행에 필요한 파일을 묶어 격리해서 실행하는 단위다. EasyDep은 하나의 workload를 주로 하나의 Docker container로 실행한다. |
| Container image | Container를 만들기 위한 읽기 전용 파일 묶음이다. 소스코드 자체가 아니라 빌드가 끝난 실행 결과물에 가깝다. |
| Image digest | Container image 내용으로 계산한 SHA-256 식별값이다. 같은 digest는 항상 같은 image를 가리키므로 배포 중 image가 몰래 바뀌는 일을 막는다. |
| H2 | Java 애플리케이션 안에서 사용할 수 있는 가벼운 관계형 데이터베이스다. 별도 DB 서버 없이 파일 하나에 데이터를 저장할 수 있다. EasyDep은 단일 VM 기본 구성에서 H2의 파일 저장 모드를 사용한다. |
| H2의 MySQL 호환 모드 | H2가 일부 SQL 문법을 MySQL과 비슷하게 받아들이게 하는 설정이다. H2가 MySQL로 바뀌는 것은 아니며, 실제 MySQL의 성능·복제·운영 특성을 제공하지도 않는다. |
| Spring Boot | EasyDep이 현재 생성하는 Java backend 애플리케이션의 기본 framework다. HTTP API 실행, 설정, 상태 검사 기능 등을 제공한다. |
| ERD | Entity Relationship Diagram의 줄임말이다. 데이터 표와 표 사이의 관계를 나타내며, EasyDep은 이를 보고 데이터 저장 필요 여부를 판단한다. |
| 영속 디스크 | VM이나 컨테이너를 다시 만들어도 데이터를 남길 목적으로 연결하는 클라우드 디스크다. AWS EBS, Azure Managed Disk, GCP Persistent Disk가 이에 해당한다. |
| Zone | 한 region 안에서 전원·네트워크 장애 영역이 분리된 데이터센터 단위다. 복제본을 여러 Zone에 나누면 한 Zone 장애의 영향을 줄일 수 있다. |
| Subnet | 하나의 클라우드 네트워크를 더 작은 주소 범위로 나눈 구역이다. 공개 subnet은 인터넷 진입이 가능하고, 사설 subnet은 보통 NAT를 통해서만 외부로 나간다. |
| NAT | 공개 주소가 없는 사설 VM이 Registry나 외부 API로 요청을 보낼 수 있게 중계하는 네트워크 기능이다. 외부에서 그 VM으로 직접 들어오는 길을 만들지는 않는다. |
| Load Balancer | 외부 요청을 여러 VM 복제본에 나누어 전달하고, 정상 상태인 복제본만 사용하게 돕는 클라우드 리소스다. |
| Health check | 애플리케이션이 요청을 받을 수 있는 상태인지 일정 간격으로 확인하는 검사다. 이 문서의 `/healthz`가 대표적인 상태 검사 URL이다. |
| Registry | Container image를 올리고 내려받는 저장소다. AWS ECR, Azure Container Registry, GCP Artifact Registry가 이에 해당한다. |
| Secret | password, API token처럼 소스코드나 일반 설정 파일에 저장하면 안 되는 값이다. AWS Secrets Manager, Azure Key Vault, GCP Secret Manager에서 보관한다. |
| IaC | Infrastructure as Code의 줄임말이다. VM, 네트워크, 디스크 같은 클라우드 리소스를 코드 파일로 정의하는 방식이다. |
| OpenTofu | Terraform 문법과 호환되는 오픈 소스 IaC 실행 도구다. EasyDep은 `.tf` 파일을 만들고 `tofu plan`과 `tofu apply`로 실행하게 한다. |
| OpenTofu state | OpenTofu가 자신이 만든 클라우드 리소스와 실제 리소스 ID를 기억하는 상태 파일이다. 같은 state로 `destroy`해야 해당 실행이 만든 리소스를 정확히 정리할 수 있다. |
| cloud-init | 새 VM이 처음 켜질 때 실행하는 초기 설정 스크립트다. Docker 설치, 디스크 마운트, Secret 조회, 컨테이너 시작을 담당한다. |
| Bootstrap | 새 VM이 처음 사용할 수 있는 상태가 되도록 준비하는 과정이다. 이 문서에서는 cloud-init 실행부터 Container 시작까지를 뜻한다. |
| PlantUML | 텍스트로 다이어그램을 작성하는 도구다. EasyDep은 `.puml` 원문에서 SVG·PNG 이미지를 만든다. |
| Runtime | 배포된 애플리케이션이 실제로 실행되는 상태를 뜻한다. Runtime 다이어그램은 Container와 요청 흐름을 중심으로 보여준다. |
| Provisioning | VM, 네트워크, 디스크 같은 클라우드 리소스를 만들고 연결하는 과정이다. Provisioning 다이어그램은 생성 순서와 참조 관계를 중심으로 보여준다. |
| `WorkloadGraph` | 어떤 애플리케이션이 있고 서로 어떻게 연결되는지를 나타내는 공급자 중립 JSON이다. 아직 AWS·Azure·GCP의 구체적인 리소스 이름은 없다. |
| `DeploymentPlan` | workload를 어느 VM에 놓을지, 디스크와 네트워크를 어떻게 연결할지 결정한 공급자 중립 JSON이다. |
| `ResourcePlan` | `DeploymentPlan`을 AWS·Azure·GCP의 실제 리소스 종류로 바꾼 JSON이다. 다이어그램과 IaC 생성의 공통 입력이다. |

### H2를 기본값으로 쓰는 이유와 범위

현재 생성되는 Spring Boot 애플리케이션은 H2 드라이버를 포함한다. 다음 조건을 모두 만족하면
별도 DB 서버를 추가하지 않고도 실제 배포 가능한 가장 작은 구성을 만들 수 있다.

1. ERD가 있어 영속 데이터가 필요하다.
2. 배포 대상이 `workloads: ["vm"]`인 Docker-on-VM이다.
3. 생성 애플리케이션이 하나다.
4. 복제본이 하나다.

이 경우 H2 파일을 `/var/lib/easydep/data/easydep`에 저장하고, 그 상위 디렉터리에 영속
디스크를 연결한다. VM이나 컨테이너를 다시 만들어도 디스크를 유지하면 데이터도 남는다.

다음 경우에는 H2 파일 DB를 자동 선택하지 않는다.

- 애플리케이션 복제본이 여러 개여서 같은 파일을 안전하게 공유할 수 없는 경우
- DB를 별도 workload로 실행하려는 경우
- 관리형 DB나 외부 DB 주소를 사용하려는 경우
- 여러 workload 중 어느 것이 데이터를 소유하는지 명확하지 않은 경우

이 경우에는 명시적인 workload·connection 계약으로 DB 배치 방식을 선택해야 한다. 별도
MySQL을 포함한 수강신청 앱 구성은 아직 실배포 검증 범위에 포함되지 않았다.

---

## 2. 전체 처리 흐름

```text
요구사항과 설계 산출물
  ├─ refinedRequirements
  ├─ capabilityContract
  ├─ resourceSpec
  ├─ classModel / sequenceModel / apiSpec / erdModel
  └─ 사용자가 승인한 deploymentPlanningFacts
        ↓
PlanningFact 생성
        ↓
코드가 WorkloadGraph 구조 선택
        ↓
LLM이 기존 컴포넌트의 영어 표시 이름만 제안
        ↓
WorkloadGraph 정규화와 검사
        ↓
DeploymentPlan: VM 배치·네트워크·디스크·환경 변수 연결
        ↓
ResourcePlan: AWS / Azure / GCP 리소스로 변환
        ├─ runtime PlantUML
        ├─ provisioning PlantUML
        └─ 아직 구현값이 없으면 port·image digest 등을 나중에 받을 자리로 표시
              ↓
애플리케이션 구현 완료
        ↓
실제 소스에서 port·health path·환경 변수·mount 사용 확인
        ↓
runtime-bound ResourcePlan
        ↓
OpenTofu + cloud-init + Compose + 실행 스크립트 생성
```

PlantUML을 다시 읽어서 IaC를 만들지 않는다. 사람이 보는 다이어그램과 실행 파일은 모두
같은 구조화 JSON에서 각각 생성된다. 따라서 그림의 화살표 문구를 바꾸는 일이 클라우드
리소스를 우연히 바꾸지 않는다.

---

## 3. 입력 데이터와 실제 타입

### 3.1 `RESOURCE_SPEC v4`

요구사항 단계가 확정하는 클라우드·용량 입력이다. workload의 세부 배치는 포함하지 않는다.

| 필드 | 타입 | 필수 | 의미 |
|---|---:|:---:|---|
| `schemaVersion` | `"4"` | 예 | 현재 ResourceSpec 버전 |
| `workloads` | `["vm"]` | 예 | 현재 제품은 Docker-on-VM만 지원 |
| `provider` | `"aws" \| "azure" \| "gcp"` | 예 | 기본 CSP |
| `region` | `string` | 예 | CSP의 실제 region 코드 |
| `regionAsWritten` | `string` | 아니요 | 사용자가 처음 적은 지역 표현 |
| `deploymentTargets` | `DeploymentTarget[1..3]` | 아니요 | 비교할 CSP·region 후보 |
| `monthlyBudgetUSD` | `number > 0` | 아니요 | 월 예산 상한의 참고값 |
| `minVCpu` | `integer >= 1` | 아니요 | 최소 CPU 수 |
| `minMemoryGiB` | `number > 0` | 아니요 | 최소 메모리 GiB |
| `trafficPattern` | `"steady" \| "spiky"` | 아니요 | 일정한 트래픽인지 순간 증가형인지 |
| `scale.value` | `number > 0` | 아니요 | 예상 부하 수치 |
| `scale.unit` | `"concurrentUsers" \| "requestsPerSecond"` | 아니요 | 부하 수치 단위 |
| `dataResidency` | `string` | 아니요 | 데이터 저장 지역 제한 |
| `candidateZones` | `string[]` | 아니요 | 사용할 수 있는 Zone 후보 |

`DeploymentTarget`은 다음 모양이다.

```json
{
  "provider": "aws",
  "region": "ap-northeast-2",
  "zones": ["ap-northeast-2a", "ap-northeast-2b"]
}
```

후보가 하나면 자동 선택한다. 후보가 여러 개면 `selectedTarget`이 정해질 때까지 IaC를
만들지 않는다.

### 3.2 `WorkloadGraph`

최상위 타입은 다음과 같다.

```text
WorkloadGraph
  schemaVersion: "easydep-workload-graph"
  workloads: Workload[]
  externalDependencies: ExternalDependency[]
  connections: WorkloadConnection[]
  constraints: WorkloadConstraint[]
  derivations: object[]
```

`Workload`의 주요 필드는 다음과 같다.

| 필드 | 타입 | 의미 |
|---|---|---|
| `id` | `string` | 다른 항목이 참조하는 고정 ID |
| `name` | `string` | 다이어그램에 보이는 영어 이름. LLM이 수정할 수 있는 부분 |
| `artifact.kind` | `"generatedApplication" \| "prebuiltImage"` | EasyDep 생성 앱인지 기존 이미지인지 |
| `artifact.image` | `string \| null` | 기존 이미지의 digest가 포함된 주소 |
| `interfaces` | `WorkloadInterface[]` | HTTP·TCP 통신 입구 |
| `storage` | `WorkloadStorage[]` | workload가 사용하는 영속 디스크 |
| `configuration` | `WorkloadConfiguration[]` | 환경 변수, Secret, 다른 workload 주소 |
| `resourceRequirements.minVCpu` | `number \| null` | workload 최소 CPU |
| `resourceRequirements.minMemoryGiB` | `number \| null` | workload 최소 메모리 |
| `replicationSafety` | `"singleton" \| "interchangeable" \| "unknown"` | 같은 앱을 여러 복제본으로 실행해도 되는지 |
| `sourceRefs` | `string[]` | 이 결정의 요구사항·설계 근거 |

`WorkloadInterface`는 `protocol`, `exposure`, `port`, `healthPath`를 가진다.

- `protocol`: 현재 `http`와 `tcp` 지원
- `exposure`: `public`, `internal`, `outbound`, `unknown`
- `port`: 구현 전에는 `null`일 수 있으며 구현 후 실제 값을 결합
- `healthPath`: 예를 들어 `/healthz`; 구현 후 실제 Spring 설정에서 확인

`WorkloadStorage`는 다음 값을 가진다.

- `capacityGiB: number`
- `mountPath: string` — 컨테이너 내부의 절대 POSIX 경로
- `deletionPolicy: "retain" | "delete"`
- `replicaSemantics: "singleAttachment" | "perReplica"`

`WorkloadConfiguration.kind`는 다음 네 종류다.

| 값 | 의미 |
|---|---|
| `value` | 공개해도 되는 일반 환경 변수 값 |
| `secret` / `secretBinding` | 외부 Secret 저장소에서 읽을 값 |
| `endpointBinding` | 다른 workload나 외부 시스템의 URL 또는 host/port |

Secret의 실제 값은 WorkloadGraph에 저장하지 않는다. `secretRef`는 사용자가 이미 만든 CSP
Secret을 가리키는 참조값이며, 배포 시점 입력으로 남는다.

### 3.3 구조를 명시적으로 바꾸는 입력

기본 단일 애플리케이션보다 복잡한 구조는 `deploymentPlanningFacts`로 표현한다.

- `workloadContract`: workload, image, interface, storage, replica 수
- `connectionContract`: source와 target, protocol, endpoint 연결
- `constraintContract`: 같은 VM 배치, 분리 배치, Zone 배치 같은 조건

모든 fact는 `authority: "explicit"`, `status: "accepted"`, `sourceRefs`를 가져야 한다.
LLM이 이 구조를 새로 만들거나 수정하지 않는다.

### 3.4 구현 완료 뒤 관찰하는 값

구현 단계는 생성된 파일에서 다음 값만 읽는다.

```text
workloadId: string
interfaces[]:
  interfaceId: string
  port?: integer
  healthPath?: string
configuration[]:
  name: string
mounts[]:
  storageId: string
  mountPath: string
```

현재 Spring 설정의 `server.port`, Actuator 상태 검사 경로, Dockerfile의 `EXPOSE`, 기본값
없이 참조한 환경 변수, mount 경로 또는 그 아래 파일 사용을 확인한다. 계획에 없는 새 포트
입구·환경 변수·디스크가 나타나거나 계획한 값을 코드가 사용하지 않으면 IaC 생성을 멈춘다.

---

## 4. 템플릿 선택 규칙

### 4.1 기본 템플릿

명시적인 workload 계약이 없으면 다음 구조로 시작한다.

```text
generatedApplication 1개
  ├─ API path가 있으면 public HTTP interface 1개
  ├─ 기본 복제본 1개
  ├─ 기본 VM 1개
  └─ 이름만 LLM이 제안
```

여기에 입력에 따라 다음 항목을 추가한다.

| 입력 조건 | 추가되는 항목 |
|---|---|
| ERD + `workloads=["vm"]` + 단일 앱 + 단일 복제본 | 파일 기반 H2 설정, 10GiB 영속 디스크, `/var/lib/easydep/data` mount, `retain` 정책 |
| 인증·인가가 명시됨 | `SPRING_SECURITY_USER_NAME=easydep`, password용 CSP Secret 입력 |
| `persistent-block-storage` capability 승인 | 기존 영속 디스크 템플릿 선택 |
| `load-balanced-ingress` capability 승인 | 관리형 VM 그룹과 Load Balancer 템플릿 선택 |

단일 VM H2 설정은 다음 환경 변수로 전달한다.

```text
SPRING_DATASOURCE_URL=
  jdbc:h2:file:/var/lib/easydep/data/easydep;
  MODE=MySQL;DATABASE_TO_LOWER=TRUE;DB_CLOSE_ON_EXIT=FALSE
SPRING_DATASOURCE_USERNAME=sa
SPRING_DATASOURCE_PASSWORD=
```

빈 password는 네트워크로 공개되는 DB 계정이 아니라 같은 프로세스 안의 로컬 H2 파일에
접속하는 기본 계정이다. 인증이 필요한 웹 애플리케이션의 password는 이 값과 별개이며 CSP
Secret으로 받는다.

### 4.2 workload를 VM에 묶는 규칙

각 workload의 다음 값이 같으면 기본적으로 같은 compute unit, 즉 같은 VM 또는 같은 관리형
VM 그룹에 놓을 수 있다.

- 복제본 수
- 선택 Zone
- 관리형 교체 사용 여부
- 최소 Zone 수
- 분리 조건 유무

`separate`, `isolate`, `securityIsolation`, `resourceIsolation` 조건이 있으면 서로 다른 VM으로
나눈다. `colocate` 조건이 있으면 같은 배치 정책을 가져야 하며 같은 compute unit에 둔다.

복제본 수가 2 이상이거나 `managedReplacement=true`이면 관리형 VM 그룹을 사용한다.

- AWS: Auto Scaling Group
- Azure: Virtual Machine Scale Set
- GCP: Managed Instance Group

### 4.3 네트워크 선택 규칙

| 상황 | 연결 방식 |
|---|---|
| 공개 interface + 단일 VM | 공개 IP로 직접 진입 |
| 공개 interface + 관리형 VM 그룹 | Load Balancer를 통해 진입 |
| 같은 VM의 두 컨테이너 | Docker Compose 네트워크와 컨테이너 DNS |
| 서로 다른 단일 VM | 대상 VM의 사설 IP |
| 대상이 관리형 VM 그룹 | 내부 Load Balancer |
| EasyDep이 만들지 않는 외부 시스템 | 사용자가 endpoint 입력 |
| 공개 주소가 없는 VM | NAT를 통해 Registry와 외부 HTTP에 나감 |

같은 프로세스 안의 호출에는 가짜 URL 환경 변수를 만들지 않는다. 같은 VM의 다른
컨테이너는 host port를 불필요하게 모두 공개하지 않고 Compose 네트워크를 사용한다.

### 4.4 디스크 선택 규칙

- 단일 복제본의 일반 영속 디스크는 `singleAttachment`다.
- 여러 복제본이 각자 디스크를 가져야 하면 `perReplica`를 명시해야 한다.
- 여러 복제본이 한 block disk를 동시에 공유하는 방식은 현재 지원하지 않는다.
- `deletionPolicy=retain`이면 `destroy` 뒤에도 데이터 디스크가 남을 수 있다.
- 일회용 실배포 시험은 비용과 데이터를 남기지 않도록 시험용 graph에서 `delete`로 바꾼다.

### 4.5 템플릿 경우의 수를 만드는 축

테스트용 템플릿을 개별 코드로 15벌 작성하지 않는다. 다음 축의 조합으로 같은 생성기를
검사한다.

| 축 | 대표 값 |
|---|---|
| compute 종류 | 단일 VM, 관리형 VM 그룹 |
| compute unit 수 | 1, 2 |
| 복제본 수 | 1, 2 |
| Zone 수 | 1, 2 |
| workload 수 | 1, 2 |
| 영속 데이터를 가진 workload 수 | 0, 1, 여러 개를 추가한 실배포 사례 |
| workload 관계 | 기본 공동 배치, `colocate`, `separate` |
| 외부 진입 | 공개 IP, Load Balancer, 사설 전용 |
| Secret | 없음, 외부 Secret 1개 이상 |
| 복제본별 디스크 | 없음, `perReplica` |

현재 정적 템플릿 모음은 이 축에서 의미가 다른 15개 조합을 사용하고, AWS·Azure·GCP 각각에
runtime·provisioning 다이어그램 두 장을 만든다. 결과적으로 15 × 3 × 2 = 90개의 PlantUML
원문을 같은 코드 경로로 확인한다.

대표적인 사람이 읽기 쉬운 경우는 다음과 같다.

| 경우 | 구조 |
|---|---|
| 공개 단일 VM | 앱 1개, VM 1개, 공개 IP |
| 사설 단일 VM | 앱 1개, 공개 IP 없음, NAT egress |
| 같은 VM의 두 workload | 컨테이너 2개, VM 1개, Compose DNS |
| 분리된 두 workload | 공개 앱 VM과 사설 상태 VM |
| 관리형 단일 Zone | 복제본 2개, Zone 1개, Load Balancer |
| 관리형 다중 Zone | 복제본 2개, Zone 2개, Load Balancer |
| 영속 디스크 포함 | workload와 CSP block disk 연결 |
| 복제본별 디스크 | 관리형 그룹의 각 복제본이 자기 디스크 소유 |
| Secret 포함 | VM 권한과 외부 Secret 참조를 연결 |

---

## 5. CSP별 리소스 템플릿

세 CSP는 이름이 다르지만 같은 `DeploymentPlan`의 역할을 구현한다.

| 역할 | AWS | Azure | GCP |
|---|---|---|---|
| 네트워크 | VPC | Virtual Network | VPC Network |
| subnet | Subnet | Subnet | Subnetwork |
| 방화벽 | Security Group | Network Security Group | Firewall Rule |
| 단일 VM | EC2 Instance | Linux Virtual Machine | Compute Engine VM |
| 관리형 그룹 | Auto Scaling Group + Launch Template | Virtual Machine Scale Set | Managed Instance Group + Instance Template |
| 공개 주소 | Elastic IP | Public IP | External IP Address |
| 외부 진입 | Network Load Balancer | Load Balancer | Regional Load Balancer |
| 사설 egress | NAT Gateway | NAT Gateway | Cloud NAT + Cloud Router |
| 이미지 저장소 | ECR | Container Registry | Artifact Registry |
| 영속 디스크 | EBS Volume | Managed Disk | Persistent Disk |
| 이미지 읽기 권한 | IAM Role/Profile | Managed Identity + AcrPull | Service Account + Artifact Registry Reader |
| Secret 읽기 권한 | IAM Secret read policy | Key Vault Secrets User | Secret Manager Secret Accessor |

ResourcePlan의 각 리소스는 다음 정보를 가진다.

- 고정 ID
- CSP 리소스 종류
- OpenTofu resource type
- 다른 리소스와의 참조 관계
- `sourceRefs`
- 생성할 리소스인지 기존 리소스를 참조할 것인지

provider별 이름 길이와 속성 타입 같은 제한은 이 단계에서 처리한다. 예를 들어 GCP instance
template 이름은 길이 제한 안으로 자르고, Azure와 GCP의 port 표현식은 문자열과 숫자가
뒤섞이지 않게 렌더링한다.

---

## 6. 배포 다이어그램 생성

배포 단계는 목적이 다른 그림 두 장을 만든다.

### 6.1 Runtime 다이어그램

애플리케이션이 실행될 때의 모습을 보여준다.

- workload와 컨테이너
- 같은 VM 또는 서로 다른 VM 배치
- 공개·사설 요청 흐름
- Load Balancer
- 환경 변수와 Secret 연결
- 영속 디스크 mount
- 구현 후 확인된 listen port와 health path

예를 들어 구현이 `server.port=8000`, 상태 검사 경로 `/healthz`를 사용하면 workload 안에
`[listen] http :8000`, `[health] /healthz`가 표시된다.

### 6.2 Provisioning 다이어그램

리소스를 만들 때 필요한 순서와 참조 관계를 보여준다.

- VM이 subnet과 방화벽을 참조하는 관계
- VM 그룹이 instance template을 사용하는 관계
- Load Balancer가 backend와 health check를 사용하는 관계
- 디스크가 VM에 연결되는 관계
- Secret 읽기 권한이 VM identity와 Secret 범위를 연결하는 관계
- 사설 VM이 NAT 준비를 기다리는 관계

### 6.3 이름과 구조의 책임

LLM 응답 타입은 `DeploymentComponentLabels` 하나뿐이다.

```text
components[]:
  id: string
  name: string
```

기존 ID와 일치하는 `name`만 적용한다. LLM이 workload, VM, connection, storage, replica,
CSP 리소스를 추가하거나 지울 수 없다. 사용자 피드백으로 구조를 바꾸려면 먼저 배포 입력을
변경하고 코드가 템플릿을 다시 선택해야 한다.

### 6.4 이미지 렌더링과 캐시

PlantUML 산출물을 저장한 직후 SVG와 PNG를 미리 만든다. 앱·단계·화면 종류별 최근 이미지를
메모리에 최대 1024개 보관한다. 같은 원문의 SHA cache도 재사용한다.

따라서 일반 이미지 조회 요청은 MySQL에서 전체 설계를 다시 읽거나 PlantUML을 매번 실행하지
않고 캐시된 bytes를 반환한다. 서버를 재시작해 캐시가 비어 있을 때만 저장된 산출물로 해당
단계를 복원해 다시 채운다.

---

## 7. IaC와 사용자 배포 패키지

### 7.1 생성 파일

구현이 완료되고 runtime 결합까지 통과하면 다음 디렉터리를 만든다.

```text
application/deployment/
  README.md
  tofu/
    main.tf
    variables.tf
    outputs.tf
    cloud-init.yaml.tftpl
    provider별 보조 .tf / .tftpl
    terraform.tfvars.example
  runtime/
    compose.yaml
    .env.example
    image-digests.env        # prepare-images 실행 뒤 생성
  scripts/
    doctor.sh / doctor.ps1
    prepare-images.sh / prepare-images.ps1
    plan.sh / plan.ps1
    deploy.sh / deploy.ps1
    verify.sh / verify.ps1
    destroy.sh / destroy.ps1
    smoke-test.sh / smoke-test.ps1
```

Docker 빌드 문맥에는 `/deployment`를 포함하지 않는다. OpenTofu provider 파일과 배포
산출물을 애플리케이션 이미지 안에 다시 복사하지 않아 빌드 전송량과 디스크 사용량을 줄인다.

### 7.2 OpenTofu 변수

항상 또는 조건에 따라 다음 변수를 만든다.

| 변수 | 생성 조건 | 값의 출처 |
|---|---|---|
| `resource_prefix` | 항상 | 사용자가 정하는 리소스 이름 접두사 |
| `runtime_env` | 항상 | 비밀이 아닌 추가 runtime 환경 변수 |
| `vm_sku` | 항상 | 기본값 또는 sizing 단계에서 선택한 VM 종류 |
| `image_digest_<workload>` | 생성 앱마다 | `prepare-images`가 Registry push 뒤 기록한 SHA-256 digest |
| `container_port_<workload>_<interface>` | 구현에서 port를 아직 확인하지 못한 경우 | 사용자 입력 |
| `secret_reference_<workload>_<config>` | Secret 설정마다 | 기존 CSP Secret의 ARN·리소스 ID·이름 |
| 외부 endpoint 변수 | EasyDep 밖의 시스템에 연결할 때 | 사용자가 제공한 주소 |
| `boot_image_id` | AWS | 명시적인 AMI ID |
| `ssh_public_key` | AWS·Azure | 사용자 SSH 공개 키 |
| `subscription_id` | Azure | Azure subscription ID |
| `project_id` | GCP | GCP project ID |

구현 단계에서 포트와 health path를 확인하면 해당 값은 ResourcePlan에 직접 들어간다. 이 경우
임의의 기본 포트를 사용자에게 다시 묻지 않는다.

### 7.3 실행 순서

```text
doctor
  → 로컬 Docker, OpenTofu, CSP CLI와 인증 상태 확인

prepare-images
  → Registry 리소스만 먼저 생성
  → 애플리케이션 이미지 build
  → Registry push
  → 변경할 수 없는 image digest 기록

plan
  → runtime env와 image digest를 넣어 tofu plan 생성

deploy
  → 사용자가 검토한 plan을 그대로 apply

verify
  → 생성된 공개 health URL이 정상 응답할 때까지 확인

destroy
  → 같은 OpenTofu state가 소유한 리소스 정리
```

EasyDep 서버가 CSP 인증 정보나 Secret 실제 값을 보관하지 않는다. 사용자가 자신의 PC 또는
배포용 실행 환경에서 CSP CLI로 로그인한 뒤 스크립트를 실행한다.

### 7.4 Secret 전달

인증이 필요한 기본 Spring 앱을 예로 들면 다음 순서다.

1. ResourcePlan이 `secret-reference-application-security-password` 입력을 만든다.
2. 사용자가 기존 Secret의 참조값을 `terraform.tfvars`에 넣는다.
3. OpenTofu가 해당 Secret 하나만 읽는 최소 권한을 VM identity에 연결한다.
4. cloud-init이 VM identity로 Secret 값을 읽는다.
5. 값을 `SPRING_SECURITY_USER_PASSWORD` 환경 변수로 내보낸다.
6. Docker Compose가 같은 환경 변수를 애플리케이션 컨테이너에 전달한다.

CSP별 참조 형식은 다음과 같다.

- AWS: Secrets Manager Secret ARN
- Azure: Key Vault Secret의 Azure 리소스 ID
- GCP: Secret Manager Secret 이름 또는 `projects/.../secrets/...` 경로

---

## 8. 검사 단계

### 8.1 구조 검사

다음 오류는 IaC 생성 전에 막는다.

- 중복되거나 비어 있는 workload·interface·storage ID
- 존재하지 않는 workload나 interface 참조
- 근거가 없는 workload·constraint
- `unknown`으로 남은 공개 여부
- 지원하지 않는 protocol이나 prebuilt runtime
- 잘못된 mount 경로·용량·삭제 정책
- 여러 복제본이 `singleAttachment` 디스크를 공유하는 구조
- 외부 endpoint 값 누락
- 아직 답하지 않은 capability 질문

### 8.2 생성 파일 검사

- ResourcePlan schema와 모든 reference 확인
- 생성한 `.tf`의 HCL parsing
- `tofu fmt`, `tofu init`, `tofu validate`, `tofu plan`
- cloud-init과 Compose 연결 확인
- public health output과 실행 스크립트 확인
- CSP Secret 권한 범위와 변수 연결 확인

정적 검사 통과는 실제 배포 성공과 다르다. CSP API, 권한, VM 부팅, Registry push, guest의
디스크 이름 같은 문제는 실제 리소스를 만들어야 확인할 수 있다.

### 8.3 실배포 검사

실배포 runner는 별도 배포 구현을 만들지 않고 사용자가 받는 `doctor → prepare-images → plan
→ deploy → verify → destroy` 스크립트를 그대로 호출한다. 각 실행은 고유한
`easydep-live-<provider>-<id>` 접두사를 사용하고, 실패하더라도 기본적으로 같은 state로
정리한다.

---

## 9. 실배포로 확인한 경우

| 배치 형태 | AWS | Azure | GCP | 실제 확인 내용 |
|---|---|---|---|---|
| 공개 단일 VM | 통과 | 통과 | 통과 | Registry, image push/pull, VM, 공개 IP, 방화벽, HTTP health |
| 여러 Zone의 관리형 VM 그룹 | 통과 | 통과 | 통과 | 복제본 2개, Load Balancer, health check |
| 단일 Zone의 관리형 VM 그룹 | 기존 관리형 경로로 확인 | 기존 관리형 경로로 확인 | 통과 | Zone 하나인 regional MIG와 복제본 2개 |
| 같은 VM의 두 workload | 통과 | 영속 배치에서 확인 | 영속 배치에서 확인 | Compose DNS, host port 중복 방지 |
| 서로 다른 VM의 두 workload | 통과 | 통과 | 통과 | 공개 VM에서 사설 VM으로 통신, NAT, Registry 권한 |
| 같은 VM의 두 workload + 영속 디스크 | 통과 | 통과 | 통과 | 디스크 연결·포맷·mount·컨테이너 시작 |
| 분리된 두 workload + 사설 VM 디스크 | 통과 | 통과 | 통과 | 사설 디스크와 공개→사설 연쇄 health |
| 분리된 두 VM에 각각 디스크 | 통과 | 통과 | 통과 | 독립 디스크 두 개의 파일 쓰기와 연쇄 health |
| 관리형 복제본별 디스크 | 통과 | 통과 | 통과 | 각 복제본의 별도 디스크, 내부 Load Balancer |
| 공개 주소 없는 단일 VM | 통과 | 분리 배치에서 확인 | 분리 배치에서 확인 | NAT image pull과 bootstrap |
| 외부 Secret 전달 | 통과 | 통과 | 통과 | CSP Secret 조회, 최소 읽기 권한, 앱 내부 값 확인 |
| 실제 수강신청 앱 | 통과 | AWS용 설계라 반복하지 않음 | AWS용 설계라 반복하지 않음 | React/Vite, Spring Boot, Flyway, ECR, EBS, `/healthz` |

`통과`는 OpenTofu `init`, `validate`, `plan`, `apply`, 컨테이너 기동, 상태 검사, 리소스
정리를 모두 끝냈다는 뜻이다.

16개 요구사항 수강신청 앱은 AWS에서 실제 배포했다. 파일 기반 H2를 영속 디스크에 두고
Spring Boot의 `/healthz` 응답까지 확인했으며, OpenTofu 리소스 15개와 ECR image, 로컬 image,
임시 디렉터리를 정리했다. 이 결과는 별도 MySQL 서버를 검증했다는 뜻은 아니다.

현재 템플릿을 저장된 수강신청 앱 설계와 소스에 다시 적용한 결과도 다음과 같았다.

- deployment bundle: `completed`
- runtime binding: `bound`
- AWS ResourcePlan issue: 0건
- 실제 관찰값: port 8000, health `/healthz`
- datasource 환경 변수 3개와 보안 환경 변수 2개 확인
- `/var/lib/easydep/data` mount 사용 확인
- EBS disk와 attachment node 생성 확인
- `secret-reference-application-security-password` 배포 입력 생성 확인

---

## 10. 실배포에서 발견하고 고친 내용

| 문제 | 원인 | 반영한 개선 |
|---|---|---|
| AWS 사설 VM이 외부 통신 경로보다 먼저 부팅될 수 있음 | subnet 참조만으로 private route 준비 순서를 알 수 없음 | 사설 VM·Auto Scaling Group이 private route를 기다리게 함 |
| 같은 VM의 컨테이너가 같은 host port를 요구함 | 모든 internal interface를 host에 공개 | 같은 VM은 Compose DNS를 사용하고 필요한 대상만 host port 공개 |
| Azure·GCP port 속성 타입 오류 | 숫자 표현식을 문자열 안에 중첩 | provider에 맞게 `tostring(...)` 위치 수정 |
| AWS EBS 장치를 찾지 못함 | Nitro VM에서 `/dev/sdX`가 NVMe 이름으로 보일 수 있음 | by-id, serial, xvd/sd 순서로 찾고 root가 아닌 유일한 미사용 디스크를 안전하게 선택 |
| 관리형 복제본의 EBS를 찾지 못함 | 복제본 volume ID를 plan 시점에 알 수 없음 | root·사용 중 장치를 제외한 복제본별 유일 디스크 선택 |
| AWS 내부 NLB 대상이 unhealthy | NLB health check가 상태 VM 보안 그룹을 통과하지 못함 | health port만 VPC CIDR에 허용 |
| GCP instance template 이름 제한 초과 | 전체 접두사를 그대로 사용 | `name_prefix`를 provider 제한 안으로 자름 |
| GCP health check 이름 제한 초과 | 긴 역할 이름을 그대로 연결 | 앞부분과 node ID hash를 조합해 63자 이내로 만듦 |
| GCP 사설 VM 그룹이 NAT와 동시에 생성 | 직접 의존 관계가 없음 | 사설 VM·관리형 그룹에 Cloud NAT 생성 순서 추가 |
| Azure 사설 VM이 NAT 연결과 동시에 생성 | NIC만 참조하고 NAT association을 참조하지 않음 | VM·VMSS가 subnet NAT association을 기다리게 함 |
| 공개 앱만 확인해 사설 앱 실패를 놓침 | health 시험이 공개 프로세스만 확인 | 공개 앱이 사설 앱의 health를 호출하는 연쇄 검사 추가 |
| bootstrap 실패를 늦게 발견 | 공개 URL timeout만 기다림 | 컨테이너 확인 뒤 `EASYDEP_BOOTSTRAP_OK` 표식 출력, AWS 직렬 콘솔 확인 |
| frontend image에 `tsc`·`vite`가 없을 수 있음 | `npm ci` 성공만 확인 | image build 중 실행 파일 존재 확인 |
| AWS Secret ARN이 빈 문자열로 덮임 | 빈 `terraform.tfvars` 예제가 환경 변수보다 우선 | 실배포 runner가 실행 중 만든 ARN을 시험용 tfvars에 기록 |
| Azure Secret 참조 형식이 권한과 조회에 동시에 맞지 않음 | 권한 scope와 `az keyvault` 데이터 URL 요구가 다름 | Azure 리소스 ID로 통일하고 cloud-init이 vault·Secret 이름을 추출 |
| 실제 앱 health URL이 `/actuator/health`로 생성 | 구현 전 계획에 실제 경로가 없음 | 구현 소스에서 `/healthz`를 읽어 runtime binding 후 IaC에 반영 |
| 실제 앱 Docker build context가 약 720MB | `application/deployment/tofu/.terraform`을 다시 `COPY` | `.dockerignore`에 `/deployment` 추가, 재실행 문맥 22.07KB |
| 설계가 H2·디스크를 선택해도 구현과 연결되지 않음 | datasource와 mount 계획이 서로 독립 | H2 datasource 환경 변수와 영속 디스크를 하나의 기본 템플릿으로 생성 |
| 인증 앱의 실행 password가 배포 계획에 없음 | 구현 생성기만 환경 변수를 요구 | 요구사항·OpenAPI의 인증 근거를 공통으로 읽고 CSP Secret 입력과 cloud-init 전달 생성 |

외부 DNS 오류, Registry의 일시적인 업로드 지연, npm `ECONNRESET`, 비활성 CSP API는 템플릿
오류와 구분했다. 이미 만들어진 리소스와 OpenTofu state가 있으면 실패한 단계부터 재개했고,
전체 배포를 무조건 처음부터 반복하지 않았다.

---

## 11. 현재 지원 범위와 남은 검증

현재 고유한 Docker-on-VM 템플릿 조합은 세 CSP에서 모두 검증했다. 다음은 아직 별도 검증이
필요하거나 이미 검증한 경로의 반복에 해당한다.

- 별도 MySQL workload 또는 관리형 DB를 사용하는 실제 수강신청 앱 배포
- AWS용으로 설계한 수강신청 앱을 Azure·GCP 단일 VM에 그대로 반복 배포
- Azure·GCP `private-single`만 따로 반복하는 시험. 두 CSP는 분리 배치의 사설 VM 경로에서
  같은 NAT·Registry pull 흐름을 이미 확인했다.
- 여러 복제본이 하나의 공유 파일시스템을 쓰는 구성. 현재 block disk 템플릿 범위가 아니다.
- Kubernetes 배포. 현재 공식 입력은 `workloads: ["vm"]`뿐이다.

---

## 12. 템플릿을 앞으로 변경할 때의 기준

1. 특정 애플리케이션 이름이나 유스케이스 문장을 기준으로 분기하지 않는다.
2. workload 수, 복제본 수, Zone 수, 공개 여부, storage, Secret처럼 일반적인 입력으로
   선택한다.
3. LLM에 구조 결정을 넘기지 않는다. 표시 이름 외의 값은 typed 입력과 코드가 정한다.
4. 다이어그램 전용 구조와 IaC 전용 구조를 따로 만들지 않는다. 같은 ResourcePlan을 사용한다.
5. 새 CSP 리소스는 ID, 참조 관계, `sourceRefs`, OpenTofu type을 함께 추가한다.
6. 정적 템플릿 조합 전체를 먼저 한 번 검사한 뒤, 실제로 다른 CSP 동작이 생기는 경우만
   실배포한다.
7. 실배포 실패는 CSP 환경 오류와 템플릿 오류를 구분하고, 실패한 단계부터 재개한다.
8. 실행 뒤에는 같은 state가 소유한 리소스, 로컬 image, 임시 디렉터리가 남지 않았는지
   확인한다.
9. 발견한 문제, 원인, 수정, 재검증 결과를 이 문서의 실배포 표에 함께 기록한다.

---

## 13. 코드 위치

| 책임 | 파일 |
|---|---|
| 기본 workload와 이름 제안 경계 | `app/design/services/deployment_diagram/template_topology.py`, `service.py` |
| WorkloadGraph 타입 | `app/design/services/deployment_diagram/models.py` |
| PlanningFact 생성 | `app/design/services/deployment_diagram/planning_facts.py` |
| 구조 정규화와 기본 H2·disk 선택 | `app/design/services/deployment_diagram/normalization.py` |
| VM 배치, 네트워크, storage, runtime binding 계획 | `app/design/services/deployment_diagram/placement.py` |
| AWS·Azure·GCP ResourcePlan 생성 | `app/design/services/deployment_diagram/provider_template_generation.py` |
| ResourcePlan 검사 | `app/design/services/deployment_diagram/provider_template_validation.py` |
| runtime·provisioning 다이어그램 | `runtime_renderer.py`, `provisioning_renderer.py`, `renderer_support.py` |
| 구현 소스의 port·health·env·mount 관찰 | `app/implementation/runtime/observations.py` |
| OpenTofu 렌더링 | `app/implementation/delivery/iac_renderer.py` |
| cloud-init·Compose·사용자 스크립트 패키지 | `app/implementation/delivery/package.py` |
| 배포 package 입구 | `app/implementation/delivery/terraform.py` |
| 이미지 사전 렌더링과 캐시 | `app/artifact_images.py` |
| 15개 정적 경우 생성 | `scripts/generate_deployment_diagram_examples.py` |
| 실제 CSP 배포 runner | `scripts/run_live_deployment_smoke.py` |
| 배포 템플릿 회귀 검사 | `tests/test_deployment_templates.py` |
| 설계→관찰→다이어그램→IaC 연결 검사 | `tests/test_deployment_workload_boundary.py` |

이 문서를 현재 배포 구조의 시작점으로 사용하고, 세부 실행 로그가 필요할 때만
`docs/live-deployment-verification.md`와 생성된 OpenTofu 산출물을 함께 본다.
