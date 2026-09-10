# LLM 기반 배포 토폴로지 재구성 개선 계획

- 작성일: 2026-09-10
- 상태: 구현 전 검토 계획. 아래 계약과 파일은 제안이며 현재 제품에 구현된 것으로 보지 않는다.
- 대상: 설계 단계의 `WorkloadGraph` 구성, 근거·권한 검증, 배포 토폴로지 평가와 Cloud KB 확장
- 현재 배포 범위: AWS·Azure·GCP의 Docker-on-VM
- 현재 동작 기준: [WorkloadGraph 기반 배포 다이어그램 생성 기준](logical-deployment-topology-decisions.md)

## 1. 결정 요약

현재 배포 단계에서 LLM은 이미 정해진 컴포넌트의 영어 표시 이름만 제안한다. workload 수,
배치, replica, Zone, network path, storage와 Secret 구조는 코드가 정한다. 이 구조를 한 번에
LLM 중심 생성기로 교체하지 않는다.

첫 개선은 LLM이 요구사항과 앞선 설계 산출물에서 **후보 배포 토폴로지**를 작성하게 하고,
별도 검증기가 근거·권한·원래 요구사항 보존 여부를 검사하는 것이다. 검증을 통과하거나 사용자가
승인한 후보만 기존 `WorkloadGraph → DeploymentPlan → ResourcePlan` 경로에 전달한다. CSP별
리소스 조립, Terraform 속성 참조, CIDR·주소 계산과 IaC 렌더링은 결정론적 코드에 남긴다.

Cloud KB 전체를 먼저 선언형 규칙으로 다시 작성하지 않는다. 기존 15개 의미 사례와 새로운
조합에서 반복적으로 확인된 실패를 기준으로 필요한 정책부터 외부화한다. 이 순서는 다음 두 목표를
구분하기 위한 것이다.

1. 자연어 요구사항에서 현재 지원 범위의 `WorkloadGraph`를 올바르게 구성한다.
2. CSP 조립 코드 자체를 선언형 지식과 실행기로 대체한다.

이번 계획의 우선 목표는 1번이다. 2번은 효과와 유지보수 비용을 측정한 뒤 점진적으로 진행한다.

## 2. 현재 상태와 문제

### 2.1 현재 생성 단위

현재 검증 corpus는 서로 독립된 정적 템플릿 45개가 아니다. 의미가 다른 15개
`WorkloadGraph` 사례를 AWS·Azure·GCP에 투영한 45개 배포 모듈이다. 각 모듈에서 runtime과
provisioning view를 만들기 때문에 다이어그램 원문은 90개다.

15개 사례는 다음 축을 조합한다.

- standalone VM 또는 managed VM group
- compute unit 1개 또는 2개
- replica 1개 또는 2개
- Zone 1개 또는 2개
- workload 1개 또는 2개
- 영속 workload 유무
- 기본 공동 배치 또는 `separate`
- 직접 Public IP, public Load Balancer 또는 private egress only
- 외부 Secret binding
- replica별 block storage

이 corpus는 현재 생성기의 의미 회귀 기준으로 사용할 수 있다. 그러나 기존 사례와 CSP를
그대로 나누어 학습·평가하면 LLM이 토폴로지를 이해한 것인지 사례 이름을 재현한 것인지 구분할
수 없다.

### 2.2 현재 LLM 경계

현재 [배포 다이어그램 서비스](../app/design/services/deployment_diagram/service.py)는 코드가 먼저
구조를 만들고 LLM에서 `components[].id`와 `components[].name`만 받는다. LLM은 workload,
connection, constraint, replica, storage, network path를 만들거나 수정하지 않는다.

구조 결정은 다음 코드가 나누어 담당한다.

| 책임 | 현재 위치 |
|---|---|
| 기본 애플리케이션과 capability별 보강 | `template_topology.py` |
| replica·Zone·배치·network path | `placement.py` |
| CSP별 Registry·Secret·network·compute·ingress·storage 조립 | `provider_template_generation.py` |
| ResourcePlan 완전성 검사 | `provider_template_validation.py` |
| OpenTofu와 bootstrap 렌더링 | `app/implementation/delivery/` |

이 경계는 CSP별 실행 정확성을 유지하는 데 유리하다. 개선 과정에서도 LLM이 바로 ResourcePlan이나
Terraform을 자유 형식으로 작성하게 하지 않는다.

### 2.3 현재 Cloud KB의 범위

현재 Cloud KB는 배포 토폴로지 전체를 재구성하는 지식 모델이 아니다.

- `provider_primitives.py`는 현재 ResourcePlan에서 사용하는 CSP별 표시 이름과 Terraform
  리소스 타입을 제공한다.
- `depkb/claims.json`은 VM 중심의 provisioning·runtime 관계를 담지만 machine image,
  세부 route, NAT, managed group, Registry, Secret 권한과 LB 하위 구성을 완전히 다루지 않는다.
- `depkb/provider-realizations.json`은 public L4 ingress realization을 중심으로 한다.
- `costkb`와 `perfkb`는 VM 후보 비교에 사용하며 workload 분할이나 network topology를
  결정하지 않는다.

따라서 현재 45개 모듈을 만들 수 있다는 사실은 KB만으로 재구성할 수 있다는 뜻이 아니다.
필요한 조합 규칙과 Terraform 속성 관계 상당 부분이 생성 코드에 있다.

### 2.4 현재 검증만으로 부족한 부분

현재 회귀 검사는 45개 배포 모듈이 생성되고 ResourcePlan의 create node와 렌더링된 Terraform
타입이 일치하는지 확인한다. 이 검사는 기존 코드의 회귀에는 유용하지만 다음 오류를 독립적으로
찾지 못할 수 있다.

- planner와 renderer가 같은 필수 리소스를 함께 누락한 경우
- 중요한 속성값이나 embedded block은 틀렸지만 리소스 타입은 같은 경우
- 요구사항과 다른 구조를 만들었지만 자체 스키마는 통과한 경우
- LLM이 존재하는 형식의 `sourceRefs`를 새로 지어낸 경우
- 검증을 통과하려고 replica·가용성·storage 요구를 약화한 경우

ResourcePlan의 구조 digest만으로도 의미 동등성을 판정하지 않는다. 독립적으로 작성한 사례별
기대 조건과 주요 속성·참조 검사가 필요하다.

## 3. 목표와 제외 범위

### 3.1 목표

1. LLM이 상류 산출물에 근거한 후보 workload, interface, connection과 constraint를 생성한다.
2. LLM의 제안과 사용자 승인, 코드 정책으로 도출한 결정을 명확히 구분한다.
3. 후보가 원래 요구사항을 누락하거나 약화하지 않았는지 결정론적으로 검사한다.
4. 현재 CSP 투영기와 IaC 생성기의 회귀 없이 점진적으로 연결한다.
5. 기존 15개 사례뿐 아니라 미관측 조합, 충돌, 정보 부족과 미지원 사례를 평가한다.
6. 반복적으로 나타나는 실패를 근거로 필요한 정책과 CSP realization을 KB로 외부화한다.
7. 모든 결정에 입력 snapshot, 사용한 사실과 정책, 검증·수정 이력을 남긴다.

### 3.2 이번 계획에서 하지 않는 일

- LLM이 Terraform HCL, cloud-init 또는 PlantUML을 직접 생성하지 않는다.
- 기존 `DeploymentPlan`과 `ResourcePlan`을 한 번에 새 계약으로 교체하지 않는다.
- LLM이 사용자 승인이나 명시적 요구사항을 스스로 생성하지 않는다.
- 첫 구현에서 LLM 제안을 자동 승인하지 않는다.
- Kubernetes, ECS, serverless, managed database, shared filesystem, HTTPS와 multi-Region으로
  지원 범위를 넓히지 않는다.
- LLM 자기보고 confidence를 승인 근거로 사용하지 않는다.
- 15개 사례의 바이트 단위 암기를 재구성 성공으로 보지 않는다.

## 4. 설계 원칙

### 4.1 제안은 승인이 아니다

LLM 출력은 항상 `proposed` 상태다. LLM이 다음 값을 출력하거나 변경하지 못하게 한다.

- `authority: "explicit"`
- `status: "accepted"`
- 사용자가 승인한 provider, Region, replica, Zone, budget과 security 결정
- 상류 산출물의 canonical ID와 digest

현재 정규화기는 `authority=explicit`, `status=accepted`인 계약을 신뢰한다. LLM 후보를 이
형태로 직접 전달하면 추론을 사용자 결정으로 승격할 수 있으므로 별도 제안 계약을 둔다.

### 4.2 근거는 생성하지 않고 선택한다

LLM에는 이번 실행에서 사용할 수 있는 `allowedEvidenceRefs`를 제공한다. 후보의 모든
`sourceRefs`는 이 목록의 값을 선택해야 하며 새로운 문자열을 만들 수 없다. 검증기는 다음을
확인한다.

- 참조한 ID가 입력 snapshot에 존재한다.
- 참조 대상의 version과 digest가 현재 실행과 일치한다.
- 참조한 내용이 해당 결정과 관련 있는 typed field를 실제로 가진다.
- 같은 ID를 인용했더라도 원문 의미와 반대되는 결정을 만들지 않았다.

문자열 존재 여부만 검사하지 않고 `결정 필드 → 허용 근거 종류` 대응을 검사한다. 예를 들어
replica 결정은 명시된 scale·availability constraint 또는 사용자 승인 fact를 요구하고, 클래스
이름만으로 replica 수를 정당화하지 않는다.

### 4.3 요구사항을 보존한다

후보 검증기는 입력에서 보호해야 할 조건을 별도 `ProtectedDeploymentRequirements`로 만든다.
다음 변경은 repair 과정에서도 금지한다.

- 요구된 replica나 minimum Zone 감소
- public 또는 internal exposure의 임의 변경
- persistent storage 제거 또는 `retain`을 `delete`로 변경
- `separate`·security isolation 제거
- 인증·Secret 요구 제거
- 명시된 external dependency 또는 connection 제거
- 선택된 provider와 Region 변경

지원할 수 없는 조건은 약화하지 않고 `needsInput` 또는 `unsupported`로 반환한다.

### 4.4 공급자 투영은 결정론적으로 유지한다

LLM은 공급자 중립 의도까지만 다룬다. 다음 값은 기존 코드 또는 이후의 검증된 선언형 실행기가
정한다.

- Terraform resource type과 속성 경로
- consumer·producer reference
- CIDR, subnet과 사설 IP
- NAT와 route closure
- Registry pull 및 Secret read 권한
- Load Balancer 하위 리소스와 embedded block
- disk attachment와 replica별 생성 수
- late binding과 runtime binding

### 4.5 실패는 질문·거부·수정으로 구분한다

모든 실패를 LLM repair로 보내지 않는다.

| 분류 | 처리 |
|---|---|
| 형식 오류 또는 존재하는 근거의 잘못된 연결 | 제한된 repair 후보 |
| provider, Region, replica, storage 방식처럼 구현을 바꾸는 정보 부족 | 사용자 질문 |
| 보호 요구와 후보의 충돌 | 후보 거부. 요구를 약화하는 repair 금지 |
| 현재 Docker-on-VM 범위 밖 | `unsupported` |
| CSP 투영기 또는 renderer 결함 | 코드 결함으로 분류. LLM repair 금지 |

repair 횟수와 예산은 평가 시작 전에 고정하며, 후보가 바뀔 때마다 입력 digest와 변경 필드를
기록한다.

## 5. 목표 구조

```text
상류 산출물과 사용자 승인 fact
        ↓
ProposalContext 생성
  ├─ typed deployment signals
  ├─ allowedEvidenceRefs
  ├─ protected requirements
  └─ 현재 제품 지원 범위
        ↓
LLM: DeploymentTopologyProposal 생성
        ↓
제안 검증
  ├─ schema·ID·참조 검증
  ├─ 근거 allowlist·의미 검증
  ├─ 권한 상승 차단
  ├─ 요구사항 보존 검사
  └─ 지원 범위·조합 검사
        ↓
검토 가능한 후보
  ├─ 승인된 항목 → 기존 planning fact로 변환
  ├─ 정보 부족 → 사용자 질문
  └─ 오류 → 제한된 repair 또는 거부
        ↓
기존 WorkloadGraph 정규화
        ↓
기존 DeploymentPlan → ResourcePlan
        ↓
기존 다이어그램·OpenTofu 생성
```

## 6. 제안 계약

새 계약 이름은 구현 시 기존 모델 이름과 충돌하지 않는지 확인한 뒤 결정한다. 이 문서에서는
설명을 위해 `DeploymentTopologyProposal`을 사용한다.

```text
DeploymentTopologyProposal
  schemaVersion: "easydep-deployment-topology-proposal/v1"
  inputDigest: string
  proposalId: string
  workloads: ProposedWorkload[]
  connections: ProposedConnection[]
  constraints: ProposedConstraint[]
  unresolvedQuestions: ProposedQuestion[]
  decisionEvidence: DecisionEvidence[]
```

`DecisionEvidence`는 다음 필드를 가진다.

```json
{
  "decisionRef": "constraint:web-replicas",
  "decisionKind": "replicaCount",
  "sourceRefs": ["requirement:NFR-HA"],
  "derivationKind": "directEvidence"
}
```

`derivationKind`의 초기 값은 다음으로 제한한다.

| 값 | 의미 | 자동 적용 |
|---|---|:---:|
| `directEvidence` | 상류 typed field를 그대로 옮김 | 검증 통과 후 가능 |
| `policyDerived` | 등록된 정책 ID로 결정론적으로 도출 | 가능 |
| `llmProposed` | 여러 해석 중 LLM이 제안 | 불가, 검토 필요 |
| `needsInput` | 구현을 바꾸는 입력이 없음 | 불가, 질문 필요 |

LLM 출력에는 `accepted`, `explicit`, 임의 confidence와 CSP credential을 두지 않는다. 실제
Secret 값과 사용자 입력 원문 전체도 prompt와 proposal에 넣지 않는다.

## 7. 독립 검증 계약

### 7.1 사례별 의미 oracle

기존 15개 사례마다 생성 코드에서 독립된 oracle을 작성한다. oracle은 전체 ResourcePlan
JSON을 복제하지 않고 필수·금지 의미 조건을 표현한다.

```yaml
caseId: private-managed-two-replicas
required:
  primaryComputeKind: managedVmGroup
  replicaCount: 2
  publicIngressCount: 0
  egressKind: natEgress
  registryDelivery: true
forbidden:
  - directPublicIp
  - publicLoadBalancer
```

최소 검사 항목은 다음과 같다.

- workload·compute unit 수와 workload별 placement
- compute 종류, replica와 Zone 수
- public·internal·outbound network path
- endpoint 전략과 runtime binding
- persistent·per-replica storage 및 deletion policy
- Registry·Secret identity와 permission
- CSP별 필수 리소스와 금지 리소스
- 주요 HCL reference와 embedded block
- binding slot의 타입과 근거
- 원래 요구사항 보존 결과

oracle의 예상값을 생성된 ResourcePlan에서 역산하지 않는다.

### 7.2 두 평가 경로의 분리

다음 두 평가를 별도 지표로 기록한다.

1. **합성기 평가:** 사람이 작성한 정답 `WorkloadGraph`를 넣어 기존
   `DeploymentPlan → ResourcePlan → IaC`가 올바른지 검사한다.
2. **LLM 해석 평가:** 자연어 요구사항과 설계 산출물을 주고 LLM이 정답 graph 또는 올바른
   질문·거부를 만드는지 검사한다.

첫 평가가 실패하면 provider 합성기 문제이고, 두 번째만 실패하면 proposal 생성·근거 연결
문제로 분류한다.

### 7.3 미관측·충돌 사례

평가 split은 의미 사례 단위로 나눈다. 같은 토폴로지의 AWS를 예제로 제공하고 Azure·GCP만
holdout으로 두지 않는다. 하나의 의미 사례에 속한 세 CSP 투영은 같은 split에 둔다.

미관측 정상 조합에는 다음 후보를 포함한다.

- Secret과 persistent storage를 동시에 사용하는 단일 VM
- private managed group과 per-replica storage
- 다중 Zone managed group과 internal dependency
- 공개 web, 내부 worker와 persistent state의 세 workload 배치
- 둘 이상의 internal connection
- HTTP와 내부 TCP interface를 함께 가진 workload

충돌·정보 부족·미지원 사례에는 다음 후보를 포함한다.

- replica 2와 `singleAttachment` block disk 동시 요구
- `colocate`와 `separate`가 같은 workload 쌍에 적용됨
- replica 2인데 replication safety가 명시되지 않음
- provider 또는 Region이 없음
- public exposure가 필요한데 public ingress가 금지됨
- managed database 또는 shared filesystem이 필수임

### 7.4 지표

| 지표 | 의미 |
|---|---|
| 최초 후보 성공률 | repair 전 정답·유효 proposal 비율 |
| 제한 repair 후 성공률 | 고정된 repair 예산 안에서 성공한 비율 |
| 요구사항 위반률 | 보호 요구를 누락·약화·반전한 비율 |
| 근거 정확도 | decision과 실제 source field가 올바르게 대응한 비율 |
| 위험한 권한 상승 수 | LLM 추론이 explicit/accepted로 처리된 횟수 |
| 정확한 질문률 | 정보 부족 사례에서 필요한 질문을 한 비율 |
| 정확한 거부율 | 미지원·모순 사례를 안전하게 중단한 비율 |
| provider projection 완전성 | 독립 oracle의 필수 CSP 구성요소 충족률 |
| 잘못된 IaC 주장 수 | provider schema가 거부한 type·block·argument·reference 수 |
| 비용·지연 | 후보와 repair의 호출 수, token, wall time |

초기 단계에서는 자동 수락률을 목표로 삼지 않는다. 자동 수락 임계값은 개발 결과를 본 뒤
holdout 실행 전에 고정하고, 요구사항 위반과 권한 상승이 없는 조건을 먼저 만족해야 한다.

## 8. 구현 단계

### P0. 회귀 기준과 관측 가능성 확보

**목표:** LLM을 연결하기 전에 현재 15개 사례의 의미와 검증 한계를 고정한다.

작업:

1. 15개 사례의 독립 의미 oracle을 별도 fixture로 작성한다.
2. ResourcePlan digest가 포함하는 필드와 제외하는 필드를 문서화한다.
3. 주요 node attribute, embedded block, reference와 runtime binding을 canonical comparison에
   포함한다.
4. 기존 45개 module의 `fmt`, `validate`와 대표 `plan` 검사를 유지한다.
5. planner·provider projection·renderer 실패를 구분하는 결과 스키마를 추가한다.

완료 기준:

- 기존 15개 사례 × 3개 CSP가 독립 oracle을 통과한다.
- 필수 리소스 하나를 planner와 renderer에서 함께 누락시키는 mutation을 oracle이 잡는다.
- 주요 port, health path, CIDR 또는 권한 reference 변경을 검사가 잡는다.
- 이 단계에서는 제품의 LLM 동작이 바뀌지 않는다.

### P1. 후보 계약과 shadow 생성

**목표:** 현재 사용자 결과에 영향을 주지 않고 자연어→proposal 경로를 측정한다.

작업:

1. `DeploymentTopologyProposal`과 `ProposalValidationReport` typed 모델을 추가한다.
2. 상류 산출물에서 최소 typed signal, evidence allowlist와 protected requirements를 만드는
   `ProposalContext` builder를 추가한다.
3. 기존 표시 이름 prompt와 분리된 topology proposal prompt를 추가한다.
4. LLM 후보를 저장하되 현재 template WorkloadGraph를 계속 최종 결과로 사용한다.
5. input digest, 모델·prompt·schema 버전, 후보, 검증 결과와 호출 사용량을 기록한다.

초기 실행 모드는 다음 세 값으로 제한한다.

| 모드 | 동작 |
|---|---|
| `off` | 현재 경로만 실행 |
| `shadow` | 후보를 만들고 평가하지만 사용자 산출물에는 적용하지 않음 |
| `review` | 검증된 후보를 사용자에게 보여 주고 승인 뒤 적용 |

`auto` 모드는 초기 계약에 포함하지 않는다.

완료 기준:

- `shadow` 실패가 현재 배포 산출물과 단계 성공 여부를 바꾸지 않는다.
- LLM이 새로운 evidence ID, explicit authority 또는 accepted status를 넣으면 거부된다.
- 같은 입력 digest와 proposal 설정을 실행 기록에서 식별할 수 있다.
- 비밀값과 모델 내부 추론 전문을 저장하지 않는다.

### P2. 근거·권한·요구 보존 검증

**목표:** 형식상 유효하지만 의미가 잘못된 후보를 기존 planner 전에 차단한다.

작업:

1. decision field별 허용 evidence 종류를 등록한다.
2. LLM sourceRefs를 allowlist와 실제 typed source field에 대조한다.
3. protected requirements와 proposal 사이의 의미 diff를 만든다.
4. workload·connection·constraint ID와 endpoint 참조 완전성을 검사한다.
5. 현재 지원 범위와 불가능한 조합을 검사한다.
6. repair가 바꿀 수 있는 필드와 금지 필드를 분리한다.

완료 기준:

- replica, Zone, exposure, storage, isolation과 security 요구를 약화한 후보가 차단된다.
- 정보 부족은 임의 기본값이 아니라 typed question으로 남는다.
- 동일 오류의 무제한 repair가 발생하지 않는다.
- 검증 결과가 `invalid`, `needsInput`, `unsupported`, `unjustified`를 구분한다.

### P3. 검토 후 기존 엔진 연결

**목표:** 검증된 후보를 사용자 승인 경계 뒤에서 기존 계획 경로에 연결한다.

작업:

1. proposal을 현재 `deploymentPlanningFacts`로 변환하는 단방향 adapter를 추가한다.
2. 사용자 승인 결과만 `authority=explicit`, `status=accepted`로 변환한다.
3. 승인 전후 proposal과 fact의 대응표를 저장한다.
4. 기존 WorkloadGraph normalizer와 provider projection을 변경 없이 우선 재사용한다.
5. 실패 시 현재 template 경로로 조용히 대체하지 않고 어떤 후보가 왜 적용되지 않았는지
   표시한다.

완료 기준:

- 승인되지 않은 proposal이 ResourcePlan에 반영되지 않는다.
- 승인된 decision과 생성된 workload·constraint를 양방향으로 추적할 수 있다.
- 현재 45개 경로가 회귀하지 않는다.
- 지원 범위 밖 제안은 Terraform 생성 전에 중단한다.

### P4. 실패 기반 KB 외부화

**목표:** 코드와 prompt에서 반복되는 배포 판단을 검증 가능한 지식으로 옮긴다.

초기 외부화 후보:

1. replica·managed replacement에 따른 compute 종류
2. public standalone과 public managed의 ingress 전략
3. private compute의 egress 요구
4. endpoint 전략: container DNS, static private IP, internal LB
5. 다중 replica block storage의 `perReplica` 제약
6. capability별 CSP 구성요소 집합

선언형 규칙에는 단순 component와 edge 외에 다음 정보를 포함한다.

- 적용 전제와 제품 지원 범위
- CSP 필수 제약, EasyDep 기본 정책, 사용자 선택의 구분
- component 생성 개수와 scope
- 공유 가능한 network·identity·LB와 공유 키
- 이름 충돌 처리
- 독립 리소스와 embedded block 구분
- 입력·출력 attribute와 late binding
- reference cardinality와 producer attribute
- 규칙 우선순위, 충돌과 종료 조건
- 미지원 조합
- 버전, 출처와 검증 상태

완료 기준:

- 외부화 전후 독립 oracle 결과가 의미상 동일하다.
- 규칙 파일을 제거하거나 손상시키면 명시적 오류로 중단한다.
- LLM이 선언형 규칙의 실행 순서나 CSP attribute를 임의로 보충하지 않는다.
- 코드 감소와 유지보수 효과가 측정되지 않으면 추가 외부화를 자동 확대하지 않는다.

### P5. 평가와 기본 경로 승격

**목표:** 기존 template 기반 경로보다 요구 해석 범위가 넓어지고 안전성이 유지되는지 확인한다.

작업:

1. 기존 사례, 미관측 정상 조합, 충돌·정보 부족·미지원 사례를 의미 단위로 분리한다.
2. LLM 해석 평가와 provider 합성기 평가를 따로 실행한다.
3. 같은 모델·prompt·KB·seed·예산에서 반복 실행한다.
4. 실패 실행도 제외하지 않고 원인과 마지막 유효 checkpoint를 남긴다.
5. 자동 수락 여부는 별도 사전 고정 기준으로 판단한다.

기본 경로 승격의 최소 조건:

- 기존 45개 의미 oracle과 IaC 검증에 회귀가 없다.
- 승인되지 않은 권한 상승이 0건이다.
- 보호 요구를 약화한 후보가 제품 ResourcePlan으로 전달된 사례가 0건이다.
- 미지원 사례가 IaC 생성까지 진행된 사례가 0건이다.
- 미관측 정상 조합에서 기존 template 선택 방식보다 측정 가능한 coverage 개선이 있다.
- 실패 원인을 proposal, validation, projection, rendering 단계로 구분할 수 있다.

## 9. 예상 변경 위치

구체적인 파일 분리는 구현 전에 현재 package 책임과 순환 import를 다시 확인한다. 초기 예상은
다음과 같다.

| 책임 | 예상 위치 |
|---|---|
| proposal typed 모델 | `app/design/services/deployment_diagram/proposal_models.py` |
| ProposalContext와 보호 요구 추출 | `proposal_context.py` |
| topology 전용 LLM 호출 | `proposal_service.py`, `proposal_prompts.py` |
| 근거·권한·요구 보존 검사 | `proposal_validation.py` |
| 승인 proposal→planning fact adapter | `proposal_adapter.py` |
| subgraph shadow/review 연결 | `app/design/graphs/subgraphs.py` |
| 실행 모드와 LLM profile | `app/config.py`와 기존 LLM 설정 경계 |
| 독립 의미 oracle | `tests/fixtures/deployment_topology_oracles/` |
| proposal 단위·통합 테스트 | `tests/test_deployment_topology_proposal.py` |
| 미관측·충돌 평가 | `evaluation/easydep/` 아래 별도 deployment topology suite |

기존 `service.py`의 표시 이름 계약을 topology 생성 계약으로 확장하지 않는다. 두 호출의 실패
의미와 평가 대상이 다르므로 별도 서비스로 둔다.

## 10. 저장과 재개

proposal 실행에는 다음 식별 정보를 저장한다.

- 앱·workspace·design run ID
- 상류 산출물별 version과 canonical digest
- proposal ID와 input digest
- model·provider 식별자
- prompt·schema·policy·KB 버전
- 원본 후보와 검증 보고서
- repair별 변경 필드와 실패 분류
- 사용자 승인·거절 결과와 시점
- 최종 planning fact·WorkloadGraph 대응

재개할 때 앱 ID, input digest, 완료 단계와 후보 산출물의 대응이 일치하는지 먼저 확인한다.
prompt, schema 또는 policy가 달라졌다면 이전 proposal을 그대로 승인하지 않고 재검증한다.
LLM 출력 직렬화나 기록 출력에는 UTF-8을 사용한다.

## 11. 위험과 대응

| 위험 | 대응 |
|---|---|
| LLM 추론이 사용자 결정으로 승격됨 | proposal 계약에서 authority/status 제거, 승인 adapter만 승격 가능 |
| sourceRefs 문자열만 그럴듯하게 생성 | evidence allowlist와 typed source field 대조 |
| repair가 요구사항을 낮춰 통과 | protected requirements diff와 금지 필드 적용 |
| 기존 15개 이름을 암기 | 의미 사례 단위 holdout과 새로운 조합 사용 |
| CSP별 구현 세부 환각 | ResourcePlan·Terraform 생성은 기존 코드에 유지 |
| planner·renderer의 공통 누락 | 생성 결과에서 독립된 의미 oracle 사용 |
| 선언형 KB가 또 하나의 복잡한 프로그래밍 언어가 됨 | 실패가 반복된 규칙만 외부화하고 효과를 측정 |
| shadow 호출이 비용과 지연을 증가 | opt-in 모드, 호출 수·token·wall time 기록 |
| 실패 뒤 전체 설계를 재실행 | proposal·검증·projection checkpoint를 분리해 실패 단계부터 재개 |

## 12. 구현 착수 전 결정할 항목

다음 항목은 구현을 시작하기 전에 확정한다.

1. proposal 후보를 사용자에게 보여 줄 UI와 승인 단위
2. direct evidence와 review-required proposal을 나누는 초기 정책
3. protected requirements의 canonical 입력 계약
4. 기존 15개 oracle 파일의 스키마와 소유 위치
5. shadow 평가에 사용할 모델·seed·호출 예산
6. 미관측·충돌 사례의 정답 작성 및 검토 절차
7. repair 허용 필드와 실행당 최대 repair 횟수
8. proposal 기록의 보존 기간과 비밀정보 redaction 기준

## 13. 첫 구현 묶음

첫 구현은 P0와 P1의 일부로 제한한다.

1. 기존 15개 사례의 독립 oracle 스키마와 대표 3개 사례를 작성한다.
2. `DeploymentTopologyProposal`과 validation report 모델을 추가한다.
3. evidence allowlist와 protected requirements를 포함한 `ProposalContext`를 만든다.
4. `shadow` 모드에서 proposal을 한 번 생성하고 저장한다.
5. proposal이 현재 WorkloadGraph·ResourcePlan을 변경하지 않는지 회귀 검사한다.
6. 대표 정상·정보 부족·권한 상승 시도 사례로 validator를 검증한다.

이 묶음의 결과를 본 뒤 나머지 12개 oracle, review UI, repair와 KB 외부화를 진행한다. 첫 구현
묶음에서 자연어→proposal의 실패 유형을 측정하지 못하면 선언형 KB 이전을 먼저 확대하지 않는다.
