# WorkloadGraph 기반 배포 다이어그램 생성 기준

이 문서는 현재 코드가 배포 다이어그램을 만드는 방법과 그 산출물이 요구사항·설계·구현·IaC
단계에 연결되는 방식을 설명한다. 목표 설계안이 아니라 현재 실행 경로를 기준으로 한다.

배포 다이어그램의 구조는 코드의 Docker-on-VM 템플릿이 소유한다. 일반 프로젝트는 단일
생성 애플리케이션 템플릿으로 시작하고, 승인된 배포 계약과 사용자의 sizing 선택에 따라
기존 경우를 조합한다. LLM은 이미 선택된 컴포넌트의 표시 이름만 제안한다.
`DeploymentPlan`과 CSP별 `ResourcePlan`은 이 `WorkloadGraph`를 입력으로 결정론적으로
생성되며, runtime 다이어그램, provisioning 다이어그램과 OpenTofu는 같은 계획을 사용한다.

```text
요구사항 분석
  ├─ refined requirements / CapabilityContract
  ├─ resource intake / RESOURCE_SPEC
  └─ use-case model and specifications
                    │
                    ▼
class model → sequence model → API model → ERD model
                    │
                    ▼
deployment_diagram 설계 스테이지
  코드: 기본 템플릿·명시 계약으로 WorkloadGraph 구조 선택
  LLM: 기존 컴포넌트의 영어 표시 이름만 제안
  코드: PlanningFact 추출·graph 검증
  코드: DeploymentPlan → provider ResourcePlan → 두 PlantUML view
                    │
             저장·사용자 피드백
                    │
                    ▼
CloudDesignAdapter → 구현/테스트 → runtime contract 관찰
  → 값 binding → ResourcePlan 재투영 → OpenTofu/bootstrap
```

주요 구현 위치는 다음과 같다.

| 책임 | 코드 |
|---|---|
| 설계 스테이지 배선 | [`app/design/graphs/subgraphs.py`](../app/design/graphs/subgraphs.py), [`app/design/graphs/design_graph.py`](../app/design/graphs/design_graph.py) |
| 템플릿 WorkloadGraph 구조 | [`app/design/services/deployment_diagram/template_topology.py`](../app/design/services/deployment_diagram/template_topology.py) |
| 표시 이름 제안 | [`app/design/services/deployment_diagram/service.py`](../app/design/services/deployment_diagram/service.py) |
| facts·배치·runtime binding | [`app/design/services/deployment_diagram/planner.py`](../app/design/services/deployment_diagram/planner.py) |
| bundle 조립 | [`app/design/services/deployment_diagram/bundle.py`](../app/design/services/deployment_diagram/bundle.py) |
| CSP 리소스 폐쇄성 | [`app/design/services/deployment_diagram/provider_template.py`](../app/design/services/deployment_diagram/provider_template.py) |
| PlantUML 두 view | [`app/design/services/deployment_diagram/provider_plantuml.py`](../app/design/services/deployment_diagram/provider_plantuml.py) |
| 구현 단계 인계 | [`app/implementation/delivery/vm_delivery.py`](../app/implementation/delivery/vm_delivery.py) |
| OpenTofu 생성 | [`app/implementation/delivery/iac_renderer.py`](../app/implementation/delivery/iac_renderer.py) |

## 1. 계약과 기준 데이터

| 계층 | `schemaVersion` | 역할 |
|---|---|---|
| PlanningFact 문서 | `easydep-planning-facts` | 상류 산출물의 값·근거·digest |
| PlanningContext | `easydep-planning-context` | provider, Region, Zone 후보, 예산·용량·트래픽 |
| WorkloadGraph | `easydep-workload-graph` | 배포할 실행 단위와 통신·구조 제약 |
| DeploymentPlan | `easydep-deployment-plan` | provider-neutral compute·배치·storage·network·runtime binding |
| ResourcePlan | `easydep-resource-plan` | 한 CSP의 실제 primitive와 HCL 참조 |
| 배포 bundle | `easydep-deployment-diagram` | facts, graph, spec, provider projection의 저장 단위 |
| runtime binding 결과 | `easydep-runtime-binding` | 구현 관찰의 값 binding 또는 설계 재생성 요구 |

```text
PlanningFact + 코드 템플릿 + LLM 표시 이름
  → WorkloadGraph
  → DeploymentPlan
  → ResourcePlan
  ├─ runtime PlantUML
  ├─ provisioning PlantUML
  └─ OpenTofu + bootstrap
```

PUML을 읽어 계획이나 IaC를 만드는 역변환은 없다. 사용자 피드백도 PUML을 직접 고치지
않고 WorkloadGraph를 고친 뒤 모든 하위 산출물을 다시 만든다.

## 2. 앞단에서 입력되는 산출물

### 2.1 요구사항 분석

`DesignAdapter`는 요구사항 결과를 설계 상태로 다음처럼 옮긴다.

| 요구사항 결과 | 설계 상태 | 배포 단계의 용도 |
|---|---|---|
| `requirements` | `refined_requirements` | workload·제약 제안의 원문 근거 |
| `capability_contract` | 같은 이름 | accepted typed constraint와 미해결 질문 |
| `resource_intake` | 같은 이름 | 사용자가 제공한 자원 값의 provenance |
| actors/use cases/specifications | `usecase_spec` | 진입점·외부 시스템·호출 후보의 근거 |
| `resource_spec` | 같은 이름 | provider와 위치·예산·용량 context |

`RESOURCE_SPEC v4`는 topology 선택표가 아니다. provider, Region, 최대 3개의 deployment
target, 후보 Zone, 월 예산, 최소 vCPU·메모리, 트래픽 형태, 규모와 데이터 지역 제약을
담는다. `workloads=["vm"]`은 Docker-on-VM 범위를 나타내는 호환 필드일 뿐, workload
개수나 VM 배치를 지시하지 않는다.

### 2.2 앞선 설계 산출물

실제 설계 순서는 다음과 같다.

```text
class_diagram → sequence_diagram → api_spec → erd → deployment_diagram
```

| 입력 | 사용하는 방법 | 자동으로 하지 않는 일 |
|---|---|---|
| 요구사항 | source reference와 이름 문맥 | 자연어를 ResourcePlan에 복사 |
| CapabilityContract | capability fact와 typed constraint | `needsQuestion`을 기본값으로 해소 |
| resource intake/spec | planningContext와 provenance fact | topology 선택 |
| 유스케이스 | 진입점·외부 시스템 후보 판단 | actor를 workload로 변환 |
| BCE 클래스 | 생성 앱 코드 경계 판단 | 클래스·Entity별 workload 분할 |
| 시퀀스 | connection 후보 판단 | protocol·port 추측 |
| API | HTTP interface 후보 fact | public exposure 판정 |
| ERD | engine 없는 persistent-data fact | DB workload·engine·disk 생성 |

모든 입력은 `inputArtifacts[]`에 artifact 이름, 선택적 version, canonical SHA-256 digest로
기록된다. `planning_inputs_stale()`은 저장된 version/digest와 현재 값을 비교한다.

### 2.3 명시적 deployment planning facts

상류에서 이미 구조적으로 결정한 값은 `deployment_planning_facts`로 추가할 수 있다.
`authority=explicit`, `status=accepted`인 다음 계약은 정규화 코드가 직접 overlay한다.

- `workloadContract`: artifact/interface/storage/replica 수
- `connectionContract`: workload connection과 endpoint/Secret binding
- `constraintContract`: 구조 제약
- capability의 `typedConstraints`: WorkloadGraph constraint

명시 계약은 LLM에 전달해 다시 해석하지 않고 코드가 직접 템플릿에 적용한다. 승인된
`persistent-block-storage`와 `load-balanced-ingress` capability도 각각 기존 block storage와
managed VM group 경우를 선택한다. 명시 계약 계열이 존재하면 source reference가 없거나
accepted fact가 허용하지 않은 구조 제약은 후보에서 제거하고 이유를 `derivations[]`에 남긴다.

## 3. deployment_diagram 서브그래프

생성 경로는 다음 세 노드다.

```text
extract_deployment_diagram
  → finalize_deployment_diagram
  → render_deployment_diagram
```

1. extract는 코드로 WorkloadGraph 구조를 만든 뒤 LLM에서 `DeploymentComponentLabels`만
   받아 기존 컴포넌트 이름에 적용한다.
2. finalize는 PlanningFact, 정규화 WorkloadGraph, DeploymentPlan과 각 target의
   ResourcePlan을 bundle로 조립한다.
3. render는 bundle의 runtime PUML을 만들고 PlantUML 문법을 검사한다. provisioning PUML은
   finalize 결과에 함께 저장된다.

LLM 응답 스키마에는 `components[].id`와 `components[].name`만 있다. VM, VM group, subnet,
disk, Public IP, LB, NAT, firewall, Registry뿐 아니라 workload, interface, storage,
connection과 constraint도 제안할 수 없다.

피드백 경로는 다음과 같다.

```text
revise_deployment_diagram
  → finalize_deployment_diagram
  → render_deployment_diagram
  → persist → 같은 gate
```

reviser는 기존 workload와 external dependency의 표시 이름만 수정한다. 구조 관련 피드백은
요구사항의 배포 입력에서 승인해야 한다. 이름 변경 뒤 하위 계획은 다시 투영된다. 배포 스테이지에는 별도
`check_deployment_diagram` 노드가 없다. planner/provider validator가 `issues`와
`unresolved`를 만들며, PlantUML 문법 통과와 배포 결정 완료는 서로 다른 판정이다.

## 4. WorkloadGraph 정규화와 검증

`WorkloadGraph`의 구성은 다음과 같다.

- `workloads[]`: `generatedApplication` 또는 명시적 `prebuiltImage`, interface,
  storage/configuration, 최소 자원, replication safety, source refs
- `externalDependencies[]`: EasyDep가 배포하지 않는 명시적 외부 시스템
- `connections[]`: source/target, protocol과 interface reference를 가진 방향성 통신
- `constraints[]`: replica, Zone, managed replacement, colocate/separate/isolation
- `issues[]`, `derivations[]`, 입력 digest와 `structureDigest`

`connections[]`는 애플리케이션 통신 관계이고 colocate/separate는 compute 배치 제약이다.
corpus 파일명의 `relations-*`는 fixture의 배치 제약 수를 요약할 뿐 운영 모델 필드가 아니다.

`validate_workload_graph()`는 다음을 검사한다.

- workload/external dependency ID의 전역 유일성과 모든 참조의 완결성
- workload·constraint·external dependency의 source reference
- prebuilt image의 image, engine, container mode와 지원 runtime catalog
- interface protocol은 현재 HTTP 또는 내부 TCP이고 exposure가 명시됐는지
- configuration ID와 `UPPER_SNAKE_CASE` 환경변수 이름의 workload 내 유일성
- Secret 값이 graph에 저장되지 않는지
- storage 용량, retain/delete 정책, generated app의 절대 POSIX mount path
- connection endpoint/interface와 protocol
- generated source workload의 connection마다 정확히 하나의 `endpointBinding`

`invalid`, `unsupported`, `needsInput`, `unjustified`는 모두 blocking class다. 모호한 exposure,
protocol, 보존 정책과 engine을 프로젝트 기본값으로 덮지 않는다.

WorkloadGraph `structureDigest`는 image digest, 실제 port/health path, 일반 설정값과 Secret
reference를 제외한다. 이 값들은 구현·배포 단계의 binding이기 때문이다.

## 5. provider-neutral DeploymentPlan

`build_deployment_plan()`의 현재 규칙은 다음과 같다.

1. constraint가 없으면 replica 1, minimum Zone 1이다.
2. replica, Zone 집합, minimum Zone과 managed replacement가 같은 workload는 같은 compute
   signature를 가진다.
3. separate/isolation 대상은 같은 signature라도 분리한다.
4. colocate 대상의 lifecycle signature가 다르면 invalid issue를 만든다.
5. replica가 2 이상이거나 managed replacement가 참이면 `managedVmGroup`, 아니면
   `standaloneVm`이다.
6. Zone 개수만 요구되면 `candidateZones` 앞에서 필요한 만큼 고른다.
7. 같은 compute의 알려진 CPU·메모리 하한은 합산한다. 정보가 빠지면 VM SKU는 late
   binding으로 둔다.
8. workload storage마다 block disk binding과 runtime mount binding을 만든다.
9. 다중 replica는 `replicationSafety=interchangeable`이어야 한다. block storage가 있으면
   `replicaSemantics=perReplica`도 필요하다.
10. public standalone VM은 direct Public IP, public managed group은 L4 LB를 쓴다.
11. public ingress가 없는 compute와 managed group에는 Registry pull·외부 HTTP용 NAT
    egress를 만든다.
12. WorkloadGraph connection은 internal 또는 outbound network path로 바꾼다.

결과는 `computeUnits`, `placements`, `storageBindings`, `networkPaths`, `runtimeBindings`,
`locationPlan`, `lateBindings`, `issues`, `derivations`로 구성된다.

endpoint 환경변수의 주소 전략은 배치 결과에 따라 정한다.

| 배치 | 전략 | 값의 원천 |
|---|---|---|
| 같은 compute | `containerDns` | Docker workload 이름 |
| 다른 standalone VM | `staticPrivateIp` | provider template의 예약 사설 IP |
| 다른 managed group | `internalLoadBalancer` | 내부 L4 endpoint |
| external dependency | `externalInput` | 실제 배포 입력 |

환경변수 이름은 코드 템플릿 또는 명시 contract가 정한다. endpoint 값과 Secret
값은 설계 단계에서 만들지 않는다.

## 6. CSP ResourcePlan으로 닫는 과정

provider template은 다음 순서로 완전한 리소스 집합을 합성한다.

```text
Registry/image delivery
  → Secret delivery and identity
  → network/subnet/route/NAT + compute
  → public ingress → block storage
  → internal traffic/internal LB → runtime units/bootstrap inputs
```

provider 또는 Region이 없으면 unresolved ResourcePlan을 만들고 IaC를 막는다.
`deploymentTargets[]`가 여러 개면 target별 projection을 만들며 bundle은
`mode=alternatives`가 된다. 구현 단계는 하나를 선택하기 전 `alternativesReady`로 중단한다.

ResourcePlan의 표현 단위는 다음과 같다.

- `nodes[]`: `handling=create` 또는 `referenceExisting`인 provider primitive
- `references[]`: HCL consumer가 producer attribute를 참조하는 계약
- `embeddedBlocks[]`: 독립 Terraform resource가 아닌 owner 내부 block
- `sharedValues[]`: 둘 이상의 HCL field가 함께 쓰는 Terraform local
- `bindingSlots[]`: 구현 또는 실제 배포 때 채울 typed variable
- `runtimeUnits[]`: compute별 bootstrap과 container 실행 단위

reference 방향은 `consumer → producer`, 즉 의존하는 쪽에서 전제 리소스 쪽이다. 각 항목은
`consumerRef`, `consumerPath`, `producerRef`, `producerAttribute`, `cardinality`를 가진다.
의미론적 `contains`, `uses` 관계를 IaC 원본으로 쓰지 않는다.

association·attachment·route·permission resource는 ResourcePlan/OpenTofu에 남는다.
provisioning 그림에서만 독립 노드 대신 두 끝점 사이의 방향 없는 점선으로 접는다.

provider validation은 create node의 Terraform type, 모든 derivation/source ref, reference
완결성, shared value 소비 수, CIDR 포함·비중첩, workload/runtime unit 일대일 대응,
Registry/image binding, compute의 subnet·boot image·traffic filter, ingress/NAT endpoint,
disk/attachment를 검사한다.

AWS·Azure·GCP의 service account나 IAM identity는 workload가 아니다. Registry pull 또는
Secret read에 provider API가 요구하는 권한 주체일 때 생성되는 실행 지원 리소스다.

## 7. 두 다이어그램의 의미

### runtime view

runtime view는 배포 후 애플리케이션 동작을 보여 준다.

- provider → Region → network → subnet → compute → Docker workload 중첩
- workload는 `component`, image는 `artifact`, block disk는 storage 도형
- request와 workload connection은 protocol이 있는 실선 화살표
- workload 내부에 `[env]`, `[secret]`, `[mount]`, `[image]` 계약 표시
- Registry, Secret, health/traffic policy와 placement/mount는 전용 선 사용

선의 원천은 WorkloadGraph connection과 DeploymentPlan network/storage/runtime binding이다.
Terraform create dependency는 runtime view에 섞지 않는다.

### provisioning view

provisioning view는 OpenTofu field 참조를 보여 준다.

- create/reference node와 shared local 표시
- 일반 화살표는 `consumer → prerequisite producer`
- 같은 node pair에 참조가 여러 개일 때만 짧은 역할 label 표시
- association/attachment/permission/route는 방향 없는 점선으로 접어 표시
- 독립 resource가 아닌 listener/health/backend 구성은 owner 내부 block으로 표시
- runtime HTTP/TCP 흐름은 표시하지 않음

정확한 HCL 할당식은 ResourcePlan에만 둔다. 그림은 참조의 존재와 방향을 보존하되 field path를
반복하지 않는다.

## 8. 저장과 사용자 피드백

저장 원본은 `deployment_diagram_bundle` 하나다. 저장소는 bundle을 읽을 때 WorkloadGraph,
단일 DeploymentPlan/ResourcePlan을 hydrate하고 두 PUML을 다시 렌더한다.

설계 그래프는 `generate/revise → persist → gate` 순으로 동작한다. 배포 단계의 revise는
표시 이름만 바꾸며 구조 변경 요청을 LLM에 맡기지 않는다. 구조 선택은 요구사항의 승인된
배포 입력 또는 sizing 선택을 바꾼 뒤 다시 투영한다. 사용자가 보는 버전과 저장된 버전은
일치하며, 앞선 설계 단계로 rewind하면 그 지점부터 다시 실행되어 낡은 bundle을 재사용하지
않는다.

bundle import API는 `schemaVersion=easydep-deployment-diagram`만 받는다. 두 view는 다음
endpoint에서 SVG/PNG로 렌더한다.

```text
GET /api/apps/{appId}/stages/deployment_diagram/views/runtime/image.svg
GET /api/apps/{appId}/stages/deployment_diagram/views/provisioning/image.svg
```

## 9. 뒷단: 구현과 IaC 연결

### 9.1 VM delivery 경계

`VmDeliveryAdapter`는 수락된 bundle에서 WorkloadGraph, DeploymentPlan과 ResourcePlan을 읽어
구현 결과와 연결한다. bundle이나 schema가 잘못됐거나 ResourcePlan에 해결되지 않은 선택이
남아 있으면 IaC 생성을 시작하지 않는다. 구현 과정에서 확인한 실행 정보가 설계와 다르면
runtime binding을 다시 적용한 뒤 같은 provider의 ResourcePlan을 다시 만든다.

이 경계는 RESOURCE_SPEC에서 topology를 새로 정하거나 DB·runtime 기본값을 임의로 넣지 않는다.

### 9.2 구현 관찰과 2단계 binding

구현 순서는 scaffold → acceptance tests → logic → VM selection → VM delivery다.
`ApplicationRuntimeContract.extensions.workloads[]`의 workload별 관찰은
`bind_runtime_contract()`로 다음 값을 채운다.

- generated image digest
- 기존 interface의 port와 health path
- 계획된 configuration의 일반 값 또는 Secret reference
- 계획된 storage의 mount 사용 여부와 path 일치

새 workload/interface/storage/configuration, exposure 변경, mount path 불일치, 계획된
환경변수·mount 미사용은 값 binding이 아니다. `requiresDeploymentDesignRegeneration`으로
중단한다. 값 binding 후 graph와 plan을 다시 생성하여 전후 structure digest가 같을 때만
같은 provider로 ResourcePlan을 재투영한다.

계정·Subscription·Project, CSP credential과 Secret 값은 bundle 밖의 실제 배포 입력이다.
VM SKU 추천은 별도 VM selection 단계에서 용량·예산으로 수행되며, 설계의 `vmSku`는 구조
digest에 포함되지 않는 late binding이다.

### 9.3 OpenTofu와 bootstrap

`render_open_tofu()`는 unresolved가 없는 ResourcePlan에서 provider/variable/main/output/
locals `.tf`, compute별 bootstrap과 `doctor.sh`, `plan.sh`, `deploy.sh`, `status.sh`,
`destroy.sh`를 만든다.

renderer는 생성된 Terraform resource type 집합과 ResourcePlan create node type 집합이
정확히 같은지 비교한다. 모든 `references[]`가 실제 렌더 과정에서 소비됐는지도 확인한다.
VM delivery는 HCL parse preflight 후 검증된 파일을 application의 `infra/`에 원자적으로
교체하고 Dockerfile의 계약 port를 검사한다.

## 10. 검증 경로

핵심 테스트는 다음과 같다.

- [`tests/test_workload_graph_planner.py`](../tests/test_workload_graph_planner.py): facts,
  graph validation, placement, digest, runtime binding
- [`tests/test_deployment_templates.py`](../tests/test_deployment_templates.py): provider closure,
  두 view와 15개 결정 corpus
- [`tests/test_vm_delivery_orchestration.py`](../tests/test_vm_delivery_orchestration.py): 구현 인계와
  구조 변경 차단
- [`tests/test_iac_binding_validation.py`](../tests/test_iac_binding_validation.py): HCL과 계획 정합성

15개 fixture는 모두 같은 생성 파이프라인을 통과한다. standalone/group,
replica·Zone·replacement, direct IP/LB/private NAT, 단일·복수 workload, 기본 동거·명시 분리,
block/per-replica storage와 Secret binding 경계를 포함한다.

case ID는 결과의 의미 signature다.

```text
{primary-compute-kind}-cu{전체 compute 수}-r{primary replica 수}-z{primary Zone 수}
-w{workload 수}-pw{persistent workload 수}.relations-{배치 제약}.{ingress}
[.bindings-{추가 binding}]
```

이 ID는 corpus용 의미 식별자이며 provider와 Region은 포함하지 않는다. 각 값은 다음처럼
정한다.

- compute 이름, `r`, `z`: 기준 workload `web`이 배치된 compute의 종류·replica·Zone 수
- `cu`, `w`, `pw`: 전체 compute 수, workload 수, persistent workload 수
- `relations`: `colocate`·`separate` constraint 수. 둘 다 없으면 `none`
- ingress: `directPublicIp`, `loadBalancer`, 또는 public path가 없는 `privateEgressOnly`
- `bindings`: Secret과 per-replica storage 수. 둘 다 없으면 suffix 생략

camelCase는 kebab-case로 바꾸며 token 순서는 고정한다. 예를 들어 compute 2개, primary
replica/Zone 1개, workload 2개 중 persistent workload 1개, 관계 없음, direct Public IP와
per-replica storage 1개는 다음 ID가 된다.

```text
standalone-vm-cu2-r1-z1-w2-pw1.relations-none.direct-public-ip.bindings-per-replica-storage1
```

테스트는 생성된 WorkloadGraph와 DeploymentPlan의 축을 다시 세어 ID와 일치하는지 확인한다.

`primary`는 corpus의 기준 workload인 `web`을 뜻하며, 공개 사례에서는 public ingress의
target이기도 하다. 따라서 primary가 standalone VM이어도 다른 workload의 replica 정책
때문에 두 번째 compute가 managed VM group일 수 있다.
`relations-none`은 colocate 제약이 있다는 뜻이 아니라, 명시적 배치 관계가 없다는 뜻이다.
같은 lifecycle signature를 가진 workload가 한 compute에 놓이는 것은 일반 배치 규칙의 결과다.

persistent 사례의 `state`는 fixture가 명시한 generic prebuilt state service다. 명시 image,
engine, runtime catalog, 내부 HTTP interface, 20 GiB retained block storage와 mount 계약을
입력한다. ERD를 보고 데이터 workload를 자동 추가한 사례가 아니다.

### 15개 결정 사례

| 번호 | case ID | 입력과 예상 결과 |
|---:|---|---|
| 1 | `standalone-vm-cu1-r1-z1-w1-pw0.relations-none.direct-public-ip` | 공개 HTTP `web` 하나. replica·교체 제약이 없으므로 단일 standalone VM에 배치하고 직접 Public IP를 연결한다. 가장 작은 공개 배포의 기준 사례다. |
| 2 | `standalone-vm-cu1-r1-z1-w2-pw1.relations-none.direct-public-ip` | 공개 `web`과 명시적 persistent `state`가 있고 `web → state` HTTP connection이 있다. lifecycle 정책이 같고 분리 제약이 없어 한 standalone VM에 함께 배치한다. `STATE_SERVICE_URL`은 container DNS로 주입하고 state disk를 attach/mount한다. |
| 3 | `standalone-vm-cu2-r1-z1-w2-pw1.relations-separate1.direct-public-ip` | 2번과 같은 두 workload에 명시적 separate 제약을 추가한다. 두 standalone VM으로 분리하고 web만 직접 Public IP를 가진다. `STATE_SERVICE_URL`은 state VM의 고정 사설 IP로 바뀌며 disk는 state VM에만 연결된다. |
| 4 | `managed-vm-group-cu1-r1-z1-w1-pw0.relations-none.load-balancer` | 공개 `web` 하나에 replica 증가는 없지만 managed replacement가 요구된다. replica 1의 managed VM group과 L4 LB를 생성한다. VM group 선택이 replica 수만이 아니라 lifecycle 요구로도 결정됨을 검증한다. |
| 5 | `managed-vm-group-cu2-r1-z1-w2-pw1.relations-separate1.load-balancer` | managed replacement가 필요한 공개 `web`과 persistent `state`를 명시적으로 분리한다. web은 replica 1 managed group과 L4 LB, state는 standalone VM과 retained disk를 사용한다. web은 state의 고정 사설 IP를 주입받는다. |
| 6 | `managed-vm-group-cu1-r2-z1-w1-pw0.relations-none.load-balancer` | interchangeable `web`의 replica를 2로 고정하고 한 Zone을 사용한다. 하나의 managed VM group과 L4 LB를 생성하며 두 replica에 같은 workload runtime contract를 적용한다. |
| 7 | `managed-vm-group-cu2-r2-z1-w2-pw1.relations-separate1.load-balancer` | 한 Zone의 web replica 2개와 persistent state를 분리한다. web managed group은 L4 LB 뒤에 있고 state는 standalone VM과 disk를 가진다. web replica들은 state의 고정 사설 IP를 공통 endpoint로 사용한다. |
| 8 | `managed-vm-group-cu1-r2-z2-w1-pw0.relations-none.load-balancer` | interchangeable `web` replica 2개에 minimum Zone 2를 요구한다. 두 후보 Zone을 선택해 하나의 managed group을 분산하고 L4 LB로 공개한다. Zone 수와 replica 수의 정합성을 검증한다. |
| 9 | `managed-vm-group-cu2-r2-z2-w2-pw1.relations-separate1.load-balancer` | 8번의 multi-Zone web group과 persistent state를 분리한다. web은 두 Zone의 replica와 L4 LB, state는 별도 standalone VM과 retained disk를 사용한다. 공개 HA compute와 단일 persistent compute가 함께 존재하는 사례다. |
| 10 | `standalone-vm-cu1-r1-z1-w1-pw0.relations-none.private-egress-only` | internal HTTP interface만 가진 `web` 하나. Public IP와 공인 ingress를 만들지 않고 Registry pull·외부 호출용 NAT egress만 만든다. |
| 11 | `standalone-vm-cu2-r1-z1-w2-pw1.relations-none.direct-public-ip.bindings-per-replica-storage1` | public web은 standalone VM, state는 interchangeable replica 2개와 `perReplica` storage를 요구한다. 명시 separate 제약은 없지만 replica signature가 달라 자동으로 compute가 분리된다. state는 managed group·내부 LB·replica별 disk를 가지며 web은 내부 LB endpoint를 주입받는다. |
| 12 | `standalone-vm-cu1-r1-z1-w1-pw0.relations-none.direct-public-ip.bindings-secret1` | 1번의 공개 단일 web에 `API_TOKEN` Secret binding을 추가한다. Secret 값은 저장하지 않고 기존 Secret reference, VM identity의 읽기 권한과 runtime 환경변수 주입을 생성한다. |
| 13 | `standalone-vm-cu1-r1-z1-w2-pw0.relations-none.direct-public-ip` | 공개 `web`과 비공개 `worker`가 있지만 connection과 분리 제약은 없다. 동일한 기본 lifecycle signature이므로 한 standalone VM에 함께 배치하며 public ingress는 web만 대상으로 한다. |
| 14 | `standalone-vm-cu2-r1-z1-w2-pw0.relations-separate1.direct-public-ip` | 13번의 web과 worker에 separate 제약을 추가한다. 각각 standalone VM을 사용하고 web compute만 직접 Public IP를 가진다. workload 간 connection이 없으므로 endpoint 환경변수는 만들지 않는다. |
| 15 | `managed-vm-group-cu1-r2-z1-w1-pw0.relations-none.private-egress-only` | internal web replica 2개를 한 Zone의 managed group에 둔다. 공개 LB나 Public IP 없이 NAT egress만 생성한다. 복제와 managed lifecycle이 공인 ingress를 자동 의미하지 않음을 검증한다. |

세 provider와 두 view를 조합해 90개 PUML과 90개 SVG를 생성한다.

```powershell
python scripts/generate_deployment_diagram_examples.py --check
python scripts/validate_deployment_iac_examples.py
python scripts/validate_deployment_iac_examples.py --plan
```

IaC 검사는 45 module을 `fmt`/`validate`하고 대표 15 module을 정적 `plan`한다. provider는
시스템 공용 `.easydep/provider-plugin-cache`를 사용한다.

제품 UI·DB를 우회하던 동결 체크포인트 실행기는 제거했다. 현재 종단 확인은
`python -X utf8 -m evaluation.easydep.product`가 프론트엔드와 같은 Workspace API를
호출하고, 저장된 산출물 응답을 그대로 남기는 방식으로 수행한다. 배포 변환기만 빠르게
확인할 때에는 위의 결정론적 예제 생성·검사 스크립트를 사용한다.

## 11. 현재 범위와 알려진 경계

지원 범위는 Docker-on-VM, AWS/Azure/GCP, 단일 Region, 고정 replica, HTTP와 내부 TCP,
block disk, Registry image delivery다. HTTPS, multi-Region, autoscaling, managed database,
shared filesystem, Kubernetes/ECS는 범위 밖이다.

현재 코드에는 다음 연결 지점이 아직 남아 있다.

1. `deployment_planning_facts`의 workload/connection contract는 체크포인트 E2E와 직접 상태
   주입 경로에서는 쓰지만, 일반 요구사항 API가 이를 별도 산출물로 만들지는 않는다.
2. 배포 스테이지에는 독립 semantic check/report 노드가 없다. blocking issue는 bundle과
   CloudDesign/VmDelivery에서 IaC를 막지만 설계 gate의 문법 통과와는 별개다.
3. 복수 deployment target projection은 있으나 구현 전 하나를 선택하는 사용자 흐름이
   필요하다.
4. 단순 `ApplicationRuntimeContract` fallback은 첫 generated workload와 첫 interface만
   관찰한다. 복수 workload/configuration/mount는 `extensions.workloads[]` 계약이 필요하다.
5. `connectionContract.secretBindingRequired` overlay는 target Secret 환경변수 이름을
   `POSTGRES_PASSWORD`로 만드는 잔여 규칙이 있다. provider-neutral 계약에 맞게 명시 이름
   또는 runtime catalog에서 받아야 하는 후속 정리 대상이다.

이 경계들은 새 구조를 조용히 추측할 근거가 아니다. 모호하거나 구현 관찰로 구조가 달라지면
issue 또는 배포 설계 재생성 요구로 드러내는 것이 기본 정책이다.
