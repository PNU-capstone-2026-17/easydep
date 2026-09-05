# 클라우드 결정 질문 시점 및 배포 구성 UX 개선 계획

- 상태: 계획 범위 구현 완료 (2026-09-06)
- 작성일: 2026-09-05
- 범위: CSP·리전 입력, 배포 capability 질문, 기존 workload topology 계약 연결,
  deployment target·VM SKU·replica 선택, AZ 자동 배치, 배포 단계 UI gate
- 주요 대상: `app/requirements/resources`, `app/requirements/orchestration`,
  `app/workspace`, `app/design/services/deployment_diagram`, `frontend/src/lib/components`
- 관련 문서: `deployment-sizing-cost-validation-iac-plan.md`,
  `deployment-diagram-iac-completion.md`, `conversational-feedback-revision-planning.md`
- 저장 정책: 기존 `deployment_preferences`, `deployment_planning_facts`, requirements artifact와
  deployment bundle을 사용한다. 새 DB 테이블·컬럼, 새 artifact 종류 또는 별도 topology 계약을
  추가하지 않는다.

이 문서는 사용자에게 받을 클라우드 결정의 종류를 전면 재설계하는 계획이 아니다. 사용자가
CSP·리전과 최종 VM SKU를 직접 선택하는 현재 절충안을 유지하면서, 각 질문이 실제로 필요한
시점과 화면에만 나타나도록 흐름을 정리한다. SKU 후보를 만들기 전에 배포할 compute unit이
확정되도록, 이미 존재하지만 운영 경로가 없는 `workloadContract`, `connectionContract`,
`constraintContract` 생산 경로도 함께 연결한다.

## 구현 기록 (2026-09-06)

이번 1차 구현은 흐름을 늘리지 않는 범위에서 다음을 완료했다.

- required provider·region 질문이 있을 때만 선택 카드를 표시하고, 이미 알려진 provider는 미리
  선택한다.
- 초기 카드에서 AZ 선택을 제거하고 `zones=[]`를 리전 카탈로그 기반 자동 배치로 연결한다.
- 명시된 PostgreSQL 요구를 기존 `workloadContract`·`connectionContract`에 투영한다. 기본 앱과
  ERD 기반 H2 fallback은 그대로 유지한다.
- target별 sizing 상태를 분리하고, target·SKU·replica를 한 요청으로 확정한다. 채팅의 별도 target
  확정 질문은 제거했다.
- 단일 target도 sizing 완료 전에는 design을 통과하지 않으며, 확정 뒤에는 요약을 먼저 보여 준다.
- preview structure digest가 바뀐 오래된 Apply를 거부하고, 성공한 Apply가 Workspace 대기 명령까지
  동기화한다.
- Luna는 조건부 카드·부분 prefill·완료 요약 UI를, Terra는 requirements 질문 경계·target별 projection·
  workload producer를 맡았고 메인 에이전트가 상태 전이와 통합 회귀를 검증했다.

후속 구현도 같은 날 완료했다.

- ERD 승인 직후, 별도 DB 요구는 있으나 엔진이 없는 경우에만 DB 실행 방식을 묻는다. 답은 기존
  `deployment_planning_facts`의 `dataExecutionMode` fact로 기록하고 완료된 설계 단계는 다시 만들지
  않은 채 deployment stage를 이어서 실행한다.
- PostgreSQL container를 고르면 기존 app + PostgreSQL workload/connection producer를 사용하고,
  embedded를 고르면 기존 단일 VM H2 규칙을 유지한다. ERD 존재만으로는 질문하지 않는다.
- compute unit의 최소 vCPU·메모리가 없거나 catalog 후보가 없을 때 sizing panel에서 값을 보완하고
  같은 target의 후보를 다시 조회한다.
- target·capacity·SKU·replica는 최종 요청 하나에서 모두 검증한 뒤 저장하며, capacity는
  WorkloadGraph가 아니라 선택 projection의 DeploymentPlan에만 보존한다.
- 이 후속 작업도 Terra가 결정·sizing backend를, Luna가 sizing UI를 맡고 메인 에이전트가
  checkpoint 답변 경계와 통합 회귀를 검증했다.

## 1. 결론

클라우드 관련 입력은 한 번에 모두 받지 않고, 다음 두 결정 지점으로 나눈다.

```text
요구사항 초반
  → 최초 자연어에서 CSP·리전을 먼저 추출
  → 정확한 deployment target이 부족할 때만 CSP·리전 선택 UI 표시

배포 다이어그램 단계
  → 기존 산출물에서 지원 범위의 workload topology 계약을 구성·검증
  → 모호한 실행 방식이 있을 때만 사용자 확인
  → target별 DeploymentPlan·ResourcePlan·다이어그램과 SKU 후보 생성
  → 사용자가 결과를 본 뒤 최종 target·SKU·replica를 한 번에 확정
```

AZ는 표준 사용자 질문에서 제거한다. 사용자는 CSP와 region까지만 고르고, provider projection이
설계의 `minimumZones`, replica 수와 region catalog를 사용해 AZ를 자동 배치한다. 기존 `zones`
필드는 호환을 위해 유지하되 빈 배열을 정상적인 “자동 배치” 입력으로 해석한다.

클라우드 질문이 등장하는 시점은 LLM이 정하지 않는다. 현재 artifact state와 다음 소비자가 요구하는
필드로 결정한다. LLM은 최초 자연어에서 CSP·리전·예산·가용성 표현을 추출하거나, 구조화된 상류
산출물만으로 해결되지 않는 자연어의 의미 후보를 만드는 역할까지만 맡는다. 실제 workload 추가와
연결은 코드가 기존 계약과 승인 상태를 검증한 뒤 수행한다.

## 2. 유지할 제품 결정

이번 개선에서도 다음 선택권은 사용자에게 남긴다.

1. 사용할 CSP 후보: AWS, Azure, GCP 중 1~3개
2. 각 CSP에서 사용할 region
3. 여러 후보 중 최종 deployment target
4. compute unit별 VM SKU
5. 설계 최소값 이상에서의 replica 수

다음 값은 기본 흐름에서 사용자에게 묻지 않는다.

1. Availability Zone의 구체적인 이름
2. subnet, load balancer, managed group, disk와 firewall primitive
3. provider resource의 생성 순서와 dependency
4. IaC resource 이름과 내부 reference

사용자가 직접 선택하는 값도 기술적으로 사용 가능한 후보 안에서만 고른다. CSP·리전은 region
catalog, SKU는 해당 provider·region의 sizing catalog, replica는 DeploymentPlan과 replication safety
검증 범위로 제한한다.

## 3. 현재 흐름

### 3.1 CSP·리전 카드

현재 프론트엔드는 다음 조건만으로 `DeploymentPreferencesCard`를 표시한다.

```text
currentStage == requirements AND deploymentPreferences가 없음
```

이 조건은 `resource_intake`가 실제로 provider·region을 찾았는지 확인하지 않는다. 사용자가 최초
요구사항에 정확한 CSP와 region을 적었더라도 별도 preference가 저장되지 않았으면 카드가 먼저
보일 수 있다.

카드는 CSP마다 region 하나를 선택하게 하고 복수 CSP를 대안으로 저장한다. 이 부분은 유지한다.
하지만 region catalog에 Zone이 존재하면 Zone도 하나 이상 골라야 저장 버튼이 활성화된다. backend
`DeploymentTarget`은 `zones=[]`를 이미 허용하므로, Zone 필수화는 현재 frontend 정책에서 생긴다.

### 3.2 requirements resource intake

Requirements pipeline은 다음 순서로 진행한다.

```text
요구사항 확장·분류
→ analyze_cloud_inputs
→ build_resource_spec
→ actor / use case / specification
```

따라서 provider·region 부족 여부는 actor와 use case를 만들기 전에 이미 알 수 있다.
`build_resource_spec`은 최초 자연어와 구조화된 `initial_cloud_constraints`를 함께 읽고, 정확히
해소된 값은 `resource_spec`에 기록하며 누락·모호한 값은 `resource_intake.questions`에 남긴다.

이 순서는 자연스러운 클라우드 입력 gate에 적합하므로 바꾸지 않는다.

### 3.3 workload topology 확정

Deployment WorkloadGraph는 기본적으로 public HTTP `generatedApplication` 하나에서 시작한다. 기존
정규화기는 승인된 `workloadContract`, `connectionContract`, `constraintContract`를 적용해 여러
workload, 연결과 배치 제약을 만들 수 있고, app + PostgreSQL `prebuiltImage` 조합도 diagram과 IaC까지
투영할 수 있다.

그러나 현재 production 경로에서는 `ArchitectureState.deployment_planning_facts`를 읽는 소비 경로만
있고, 상류 산출물에서 이 계약을 만드는 producer가 연결되어 있지 않다. 따라서 일반 사용자 흐름은
대부분 단일 애플리케이션에 머문다. ERD가 있고 단일 VM·단일 replica인 경우에도 별도 DB workload가
아니라 애플리케이션 안의 H2 파일 DB와 영속 disk가 추가된다.

### 3.4 deployment target 확정

Deployment stage는 입력으로 받은 CSP·리전 후보마다 projection과 다이어그램을 만든다. 복수 target이
있고 `selectedTarget`이 없으면 Workspace 대화 영역이 `deployment.selectedTarget` 질문을 만들 수
있다.

동시에 Deployment Diagram artifact 화면에도 target tab이 있고, 저장된 target이 없으면 첫 target을
미리 보여 준다. 두 위치가 모두 최종 target 선택 진입점이 될 수 있다.

### 3.5 SKU·replica 선택

Deployment Diagram artifact를 열면 선택한 target 아래에 `DeploymentSizingPanel`이 표시된다.

```text
target 선택
→ GET deployment-sizing
→ compute unit별 SKU 후보·최소 용량·가격 표시
→ SKU dropdown + replica number 입력
→ Apply
→ ResourcePlan·다이어그램 재투영 및 저장
```

SKU 초기값은 이전 저장값, 후보 목록의 첫 번째 항목, 빈 값 순으로 고른다. replica 초기값은 설계가
계산한 최소 replica 수다. replication safety가 unknown인데 replica를 2개 이상 고르면 추가 확인을
요구한다.

위치는 적절하지만, panel이 단순 artifact 조회와 “지금 반드시 결정해야 하는 gate”를 구분하지
않고 항상 편집 형태로 보일 수 있다. 또한 sizing 적용이 target까지 확정하므로 대화 영역의 별도
target 선택과 역할이 겹친다.

## 4. 해결할 문제

### 4.1 정보가 충분해도 초기 카드가 나타난다

클라우드 입력 UI의 표시 여부가 실제 missing field가 아니라 stage와 저장 여부에만 의존한다.
그 결과 사용자가 이미 말한 내용을 다시 선택하거나, 요구사항을 설명하기도 전에 배포 지도를 먼저
마주칠 수 있다.

### 4.2 초기 카드가 CSP·리전보다 많은 결정을 요구한다

초기 target을 구성하는 데 필요한 값은 provider와 region이다. AZ는 WorkloadGraph와
DeploymentPlan의 replica·가용성 판단이 끝나기 전에 물어도 선택 근거가 없다.

### 4.3 SKU 전에 workload topology가 확정되지 않는다

SKU는 WorkloadGraph가 만든 compute unit별로 고른다. 현재 기본 생성 경로가 명시적인 workload
계약을 생산하지 않으므로, 지원되는 app + DB container 요구도 단일 애플리케이션으로 남거나 배포
검증에서 입력 부족으로 멈출 수 있다. topology가 불완전한 상태에서 target·SKU UI를 먼저 개선하면
사용자는 잘못된 compute unit 목록을 기준으로 선택하게 된다.

### 4.4 최종 target 선택이 비용·SKU 비교보다 먼저 올 수 있다

복수 CSP 후보를 만든 목적은 target별 다이어그램과 compute 비용을 비교하기 위해서다. 채팅에서
target을 먼저 확정한 뒤 artifact 화면에서 SKU를 고르면 비교 순서가 뒤집힌다.

### 4.5 같은 결정을 서로 다른 UI가 확정한다

채팅의 `deployment.selectedTarget` 응답과 sizing panel의 `Apply`가 모두 deployment target을
확정할 수 있다. 어느 action이 canonical한지 사용자와 코드 모두 알기 어렵다.

### 4.6 확정 이후에도 질문처럼 보인다

SKU·replica가 이미 저장된 deployment artifact를 다시 볼 때에도 편집 panel이 먼저 보이면,
사용자는 다시 답해야 하는 질문인지 단순 조회인지 구분하기 어렵다.

## 5. 결정 시점

| 결정 | earliest known | 반드시 필요한 시점 | 사용자에게 보여 줄 위치 |
|---|---|---|---|
| CSP 후보 | 최초 요구사항의 cloud constraint 추출 후 | `ResourcePlan` target projection 전 | Requirements 초반의 조건부 선택 카드 |
| region | provider와 지명/코드 해소 후 | provider·region catalog 조회 전 | CSP와 같은 카드 |
| 월 예산 | 최초 입력 또는 별도 preference | 비용 경고 계산 전, 없어도 진행 가능 | 초기 카드의 선택 입력 또는 deployment 비교 화면 |
| 데이터 보존·가용성 의미 | 요구사항 분류와 capability 추출 후 | WorkloadGraph topology 확정 전 | Requirements review의 조건부 대화 질문 |
| workload 경계 | API·sequence·ERD와 승인 capability 생성 후 | DeploymentPlan 생성 전 | 자동 계약 구성, 모호할 때만 배포 준비 질문 |
| 데이터 실행 방식 | ERD와 배포 제약을 함께 확인한 후 | DB workload/storage 확정 전 | 자동 규칙 또는 배포 준비의 조건부 확인 |
| 최소 vCPU·메모리 | 명시 요구사항·reference profile·workload 분석 후 | SKU 후보 조회 전 | 부족한 경우 deployment sizing panel 안 |
| 최종 target | 모든 target의 diagram·SKU·compute cost preview 후 | 최종 ResourcePlan·IaC 생성 전 | Deployment Diagram 구성 gate |
| VM SKU | compute unit과 최소 용량 계산 후 | 최종 ResourcePlan·IaC 생성 전 | 최종 target과 같은 구성 gate |
| replica | replication safety와 minimum replica 계산 후 | 최종 ResourcePlan·IaC 생성 전 | SKU와 같은 구성 gate |
| AZ | region, minimumZones, replica 확정 후 | provider projection 시점 | 코드가 자동 배치, 일반 질문 없음 |
| 외부 주소·secret | 최종 ResourcePlan과 패키지 생성 후 | 실제 실행 직전 | deployment package handoff |

“earliest known”은 내부에서 값을 계산할 수 있는 최초 시점이고, 반드시 그때 사용자에게 물어야 한다는
뜻은 아니다. 질문은 사용자가 판단할 근거가 생긴 뒤, 다음 단계가 그 값을 실제로 필요로 하기 직전에
배치한다.

## 6. 목표 사용자 흐름

```text
1. 사용자가 최초 요구사항 입력
       ↓
2. cloud constraint 추출과 region catalog 해소
       ↓
3. 유효한 CSP·리전 target 존재 여부 확인
       ├─ 존재함: 별도 질문 없이 Requirements 계속
       └─ 부족/모호함: CSP·리전 선택 카드 표시
                         └─ 답을 RESOURCE_SPEC에 반영하고 같은 지점부터 재개
       ↓
4. Requirements capability 분석
       ├─ 데이터 보존·가용성 의미가 명확함: 계속
       └─ 의미가 모호함: 사용자 결과 중심 질문 한 번
       ↓
5. Class → Sequence → API → ERD
       - 클라우드 배치 선택 질문 없음
       ↓
6. Workload topology 계약 구성
       - 기본 generated application
       - 명시된 경우에만 별도 PostgreSQL container와 app→DB 연결
       - 기존 workload/connection/constraint contract 검증
       ├─ 완결됨: WorkloadGraph 생성
       └─ 데이터 실행 방식이 모호함: 배포 준비 질문 한 번
       ↓
7. Deployment Diagram preview
       - target별 DeploymentPlan·ResourcePlan
       - target별 runtime/provisioning diagram
       - compute unit별 SKU 후보·최소 replica·compute cost
       ↓
8. Deployment configuration gate
       - target tab으로 비교
       - 최종 target 선택
       - 해당 target의 SKU·replica 선택
       - 한 번의 Confirm
       ↓
9. AZ 자동 배치
       ↓
10. 최종 ResourcePlan·diagram·IaC 생성
       ↓
11. 확정값 요약 표시, 필요할 때만 “배포 구성 변경”
```

## 7. CSP·리전 조건부 gate

### 7.1 표시 기준

`DeploymentPreferencesCard`의 표시 여부를 frontend의 stage 추측으로 정하지 않는다. backend가
반환한 현재 pending resource question과 `resource_intake.draft`를 기준으로 한다.

개념적인 조건은 다음과 같다.

```python
missing_cloud_coordinates = any(
    question["field"] in {"provider", "region", "deploymentTargets"}
    and question["kind"] != "suggested"
    for question in resource_questions
)
```

- provider와 region이 정확히 해소되면 카드를 표시하지 않는다.
- 둘 중 하나만 부족하면 알고 있는 값을 미리 채우고 나머지만 선택하게 한다.
- 지명이 여러 CSP region과 일치하면 가능한 조합만 보여 준다.
- LLM extraction이 실패해도 필수 field missing은 결정론적으로 질문한다.
- 단순히 `deployment_preferences is None`이라는 이유만으로 표시하지 않는다.

### 7.2 UI 위치

카드를 `ChatTimeline` 끝에 상시 붙이지 않는다. `resource_intake`가 반환한 pending question을
표현하는 전용 answer widget으로 렌더한다.

```text
Assistant: 배포 후보를 만들려면 CSP와 리전이 필요합니다.
[DeploymentPreferencesCard]
```

이렇게 하면 카드가 어떤 질문에 대한 답인지 명확하고, 답을 제출하면 해당 질문과 함께 완료 상태가
된다.

프로젝트 최초 화면에서 구조화 target을 미리 선택한 사용자는 같은 값을 requirements 질문에서 다시
보지 않는다. 언제든 수정할 수 있는 “배포 대상 설정” 진입점은 별도로 둘 수 있지만, 이는 pending
question이 아니라 명시적 설정 변경이다.

### 7.3 부분 입력과 복수 후보

- 최소 완료 조건은 유효한 provider·region target 한 개다.
- 각 provider에는 region 하나만 선택하는 현재 제한을 유지한다.
- 최대 세 provider target을 비교 후보로 저장하는 기능을 유지한다.
- 하나가 완성되면 진행할 수 있고, 다른 target 추가는 사용자의 명시 선택이다.
- 월 예산과 자유 형식 resource constraint는 선택 입력이며 카드 완료를 막지 않는다.

## 8. Requirements 중 capability 질문

CSP·리전 선택과 application capability 질문을 같은 종류로 취급하지 않는다.

다음 질문은 사용자의 업무·운영 의도를 확인하므로 Requirements 단계가 적절하다.

- 서비스 재시작 뒤에도 데이터가 남아야 하는가
- 한 instance 또는 Zone 장애에도 서비스를 계속해야 하는가
- 특정 국가·지역에 데이터를 두어야 하는가
- 외부 시스템과의 연결이 필수인가

해당 의미가 최초 요구사항에서 명확하면 묻지 않는다. 질문 답은 provider primitive를 직접 만들지
않고 capability/planning fact로 남기며 Deployment stage가 topology로 변환한다.

`suggested` capacity 질문은 resource contract를 막지 않는다. Workspace presentation에서도
suggested question을 required pending input으로 승격하지 않는다. 최소 vCPU·메모리 근거가 최종
SKU 선택 시점까지 부족하면 DeploymentSizingPanel에서 해당 compute unit 옆에 보완 입력으로
표시한다.

## 9. 기존 workload topology 계약 연결

### 9.1 재사용할 계약과 상태

새로운 topology artifact나 DB schema를 만들지 않는다. 다음 기존 경계를 그대로 사용한다.

- `ArchitectureState.deployment_planning_facts`
- `workloadContract`: workload ID, artifact 종류, interface, storage, replica
- `connectionContract`: workload 간 protocol과 endpoint/secret binding
- `constraintContract`: replica, zone, colocate/separate 등 배치 제약
- `WorkloadGraph`: 승인 계약을 정규화한 canonical deployment topology

필요하면 기존 dict shape를 검증하는 내부 Pydantic 타입을 추가할 수 있다. 이는 새 영속 모델이나
외부 API 계약이 아니라 기존 planning fact의 입력 검증 adapter로만 사용한다.

### 9.2 지원할 topology 범위

이번 연결의 product 범위는 다음으로 제한한다.

1. EasyDep가 생성한 애플리케이션 workload 하나
2. 별도 PostgreSQL이 명시되거나 조건부 질문으로 승인된 경우 `prebuiltImage` workload 하나
3. 애플리케이션에서 DB로 향하는 내부 TCP connection
4. 각 workload의 기존 storage·replica·가용성 constraint

관리형 RDS, Cloud SQL, Azure Database, managed Redis·queue는 추가하지 않는다. 임의의 worker,
cron 또는 여러 `generatedApplication`을 자동 분리하는 기능도 이번 연결의 완료 조건에 포함하지
않는다. 현재 모델이 여러 workload를 담을 수 있다는 사실과, 생성·패키징까지 제품이 지원하는
범위는 구분한다.

### 9.3 결정 규칙

계약 producer는 LLM이 WorkloadGraph JSON을 직접 작성하게 하지 않고 다음 순서로 동작한다.

```text
영속 데이터 요구 없음
  → generatedApplication 하나

영속 데이터 요구 있음
  + VM 하나
  + replica 1
  + 별도 DB 요구·엔진 지정 없음
  → 기존 H2 file DB + retained disk 규칙 유지

PostgreSQL engine/image가 명시되거나 조건부 질문에서 PostgreSQL container가 승인됨
  → generatedApplication + PostgreSQL prebuiltImage
  → app→DB connectionContract 생성

별도 DB는 필요하지만 engine이 없거나, 영속 데이터 실행 방식이 확정되지 않음
  → topology를 추측하지 않고 조건부 사용자 확인
```

ERD는 논리 데이터와 schema migration 필요성을 증명하지만 DB 엔진이나 별도 process 경계를 증명하지
않는다. ERD가 있다는 이유만으로 PostgreSQL workload를 만들지 않는다. API와 sequence도 process
경계가 명시된 경우에만 별도 workload 근거로 사용한다.

### 9.4 producer와 승인 경계

producer는 `refined_requirements`, `capability_contract`, `resource_spec`, API, sequence, ERD의
구조화된 값과 source ref를 읽어 기존 planning fact 후보를 만든다.

- 명시적인 별도 workload 계약이 없으면 기존 default application template을 유지한다.
- 단일 VM H2 조건은 기존 normalization 규칙으로 적용하며 새 계약으로 중복 표현하지 않는다.
- 사용자가 별도 PostgreSQL을 명시하거나 승인하면 default seed를 대체할 완결된 app·DB workload
  계약과 connection 계약을 함께 만든다.
- 실행 방식이 모호하면 candidate만 만들고 `needsQuestion`으로 멈춘다.
- 답변은 기존 fact를 accepted로 전환한 뒤 WorkloadGraph부터 재개한다.
- source ref가 없거나 지원 범위를 벗어난 workload는 자동 추가하지 않는다.

같은 입력과 같은 승인 답은 같은 contract ID와 같은 WorkloadGraph structure digest를 만들어야 한다.
완료된 class·sequence·API·ERD를 다시 생성하지 않고 topology producer와 deployment 하위 단계만
재개한다.

### 9.5 수정 요청

현재 deployment reviser는 기존 component의 표시 이름만 바꿀 수 있으므로, “DB를 분리해 달라”처럼
topology가 바뀌는 요청을 이름 수정기로 보내지 않는다. ConversationAgent는 해당 요청을 기존
planning fact의 변경 후보로 해석하고, 영향 범위 승인 후 workload contract producer부터 다시
실행한다. SKU·replica만 바꾸는 요청은 topology 계약을 건드리지 않는다.

## 10. Deployment configuration gate

### 10.1 비교가 먼저, 확정이 나중

복수 target이 있으면 각 target에 대해 다음 preview를 먼저 보여 준다.

- CSP와 region
- runtime/provisioning diagram
- compute unit 목록과 최소 용량
- SKU 후보
- 기본 최소 replica 수
- VM compute 월 추정 소계와 가격 범위 경고
- projection issue

사용자는 이 정보를 본 뒤 final target을 고른다. 대화 영역에서 target 이름만 먼저 선택하게 하지
않는다.

### 10.2 target과 sizing의 단일 제출

현재 `apply_deployment_sizing` 요청이 이미 `targetId`와 `selections[]`를 함께 받는다. 이 경계를 최종
확정의 canonical action으로 사용한다.

```json
{
  "targetId": "aws:ap-northeast-2",
  "selections": [
    {
      "computeUnitId": "application",
      "sku": "t3.small",
      "replicaCount": 1,
      "replicationConfirmed": false
    }
  ]
}
```

한 번의 Confirm으로 다음을 수행한다.

1. final target 확정
2. 모든 compute unit의 SKU·replica 검증
3. AZ 자동 배치
4. DeploymentPlan 재계산
5. ResourcePlan과 두 다이어그램 재투영
6. 최종 deployment artifact 저장
7. design gate 재개

대화형 `deployment.selectedTarget` 입력은 제거하거나, artifact의 Deployment configuration 화면을 여는
action으로 축소한다. text target 선택과 sizing Apply가 서로 다른 최종화 경로가 되지 않게 한다.

### 10.3 panel 표시 상태

DeploymentSizingPanel은 artifact 존재 여부가 아니라 deployment selection 상태에 따라 보인다.

```text
preview / needsInput
  → target tab과 editable sizing panel 표시
  → 현재 해야 할 action으로 강조

final
  → 선택한 target·SKU·replica·예상 compute cost 요약
  → “배포 구성 변경”을 눌렀을 때만 편집 panel 열기
```

후보 목록의 첫 SKU를 UI 초기값으로 사용하는 현재 동작은 유지할 수 있다. 다만 저장되기 전에는
“초기 후보” 또는 “기본 선택”으로 표시하고, 사용자가 Confirm하기 전까지 artifact의 최종 선택으로
간주하지 않는다.

SKU 후보가 없으면 Apply를 비활성화하고 이유를 보여 준다. capacity 입력이 부족한 경우에는 해당
compute unit의 vCPU·memory 보완 입력을 같은 panel에서 받고 후보를 다시 계산한다.

## 11. AZ 자동 배치

### 11.1 입력 계약

`DeploymentTarget.zones`는 다음 의미로 유지한다.

```text
[]              자동 배치
[explicit zones] 구형 artifact 또는 향후 고급 API 입력
```

표준 UI는 explicit Zone을 만들지 않는다. 초기 `DeploymentPreferencesCard`에서 Zone 선택 영역과
“Zone을 골라야 계속” 조건을 제거한다.

### 11.2 결정 규칙

provider projection은 최종 target과 compute selection을 받은 뒤 다음 순서로 Zone을 정한다.

1. WorkloadGraph/DeploymentPlan의 `minimumZones` 확인
2. 최종 replica 수 확인
3. `minimumZones <= replicaCount` 검증
4. region catalog의 지원 Zone 조회
5. 필요한 수만큼 서로 다른 Zone에 결정론적으로 배치
6. catalog Zone이 부족하면 최종화 차단 및 이유 표시

결과에는 Zone의 출처를 `catalogBased` 또는 현재 사용 중인 동등한 metadata로 표시한다. 이는 실제
계정 quota나 순간 capacity를 검증했다는 뜻이 아니다.

## 12. 상태와 저장

새로운 범용 decision lifecycle은 만들지 않는다. 다음 기존 상태를 조합한다.

- `deployment_preferences`: 사용자가 고른 CSP·리전 후보 draft
- `resource_intake.questions`: 아직 부족하거나 모호한 requirements 입력
- `resource_spec.deploymentTargets`: 검증된 target 후보
- `deployment_planning_facts`: 승인된 workload·connection·constraint 계약
- `deployment_workload_graph`: 계약을 정규화한 topology와 structure digest
- deployment bundle `projections[]`: target별 preview
- deployment bundle `selectedTarget`: 최종 target
- deployment bundle `sizing`: compute unit별 선택과 상태
- artifact version/digest: stale 요청 방지

필요한 화면 상태는 다음처럼 기존 값에서 계산한다.

```text
needsCloudCoordinates
  = required provider/region question이 남아 있음

needsWorkloadTopology
  = 지원 범위의 workload 계약이 완결되지 않았거나
    data execution mode 확인이 남아 있음

needsDeploymentConfiguration
  = workload topology가 valid하고 deployment projections가 존재하며
    (selectedTarget이 없거나 sizing이 완료되지 않음)

deploymentFinal
  = selectedTarget과 모든 compute selection이 검증되고
    final ResourcePlan이 존재함
```

선택 요청에는 현재 deployment bundle ID 또는 structure digest를 포함한다. 사용자가 preview를 보는
동안 WorkloadGraph나 target 후보가 바뀌면 오래된 SKU 선택을 적용하지 않고 다시 조회하게 한다.

## 13. 설정 변경과 재계산 범위

CSP·리전을 Requirements 이후에 변경할 수는 있지만, 이미 만들어진 산출물 전체를 되감지 않는다.

- requirements 의미와 class/sequence/API가 그대로라면 유지한다.
- deployment target projection, sizing, ResourcePlan, diagram과 IaC만 stale로 만든다.
- 구현 package가 이전 ResourcePlan을 포함하면 해당 package와 관련 Testing evidence도 downstream으로
  갱신하거나 stale 처리한다.
- 변경 전 영향 범위는 `conversational-feedback-revision-planning.md`의 RevisionPlanner와 RTM으로
  보여 주고 승인받는다.

SKU·replica만 변경한 경우에는 WorkloadGraph를 다시 생성하지 않는다. 선택 target의
DeploymentPlan·ResourcePlan·diagram·IaC만 결정론적으로 재투영한다.

workload 경계나 DB 실행 방식을 변경한 경우에는 기존 planning fact를 새 승인 값으로 교체하고
WorkloadGraph부터 하위 projection을 다시 만든다. 요구사항 의미 자체가 바뀌지 않았다면 class,
sequence, API와 ERD는 유지한다. 반대로 topology 요청이 업무 계약 변경을 포함하면 RevisionPlanner가
선행 단계 확대와 stale 범위를 먼저 보여 주고 승인받는다.

## 14. 구현 순서

### 서브에이전트 운용 전략

각 Wave는 메인 에이전트가 상태 전이와 backend/frontend 계약을 먼저 고정하고, Luna·Terra 또는
동급 서브에이전트에게 파일 소유권이 겹치지 않는 bounded task로 나눈다.

| 역할 | 권장 에이전트 | 맡길 작업 |
|---|---|---|
| 빠른 UI·fixture·집중 테스트 | Luna | Svelte component, API type 반영, 조건부 렌더링과 회귀 fixture |
| 상태·planning·projection backend | Terra | requirements gate, workload contract producer, sizing commit와 stale 처리 |
| 계약·통합 책임 | 메인 에이전트 | Wave 계약 확정, 공유 service/graph 연결, 통합 테스트와 최종 검토 |

Wave별 권장 분담은 다음과 같다.

- Wave 1: Luna가 `DeploymentPreferencesCard.svelte`와 조건부 렌더링 테스트를 소유하고, Terra가
  pending resource question metadata와 `zones=[]` backend round-trip을 소유한다.
- Wave 2: Terra가 requirements 질문 분류·재개 경계를 소유하고, Luna가 부분 prefill과
  suggested/required presentation 회귀를 소유한다.
- Wave 3: Terra가 workload contract producer·normalization 연결을 소유하고, Luna가 app-only,
  H2, app+PostgreSQL fixture와 결정성 테스트를 소유한다. `subgraphs.py`와 workspace 승인 연결은
  메인 에이전트가 통합한다.
- Wave 4: Terra가 sizing preview·atomic apply·bundle digest backend를 소유하고, Luna가
  `ArtifactPane`·`DeploymentSizingPanel`·API type을 소유한다. 메인 에이전트가 먼저 요청·응답 계약을
  동결한 뒤 병렬 실행한다.
- Wave 5: Luna가 final summary·재편집 UI와 component test를 소유하고, Terra가 stale 처리와 선택
  projection 재투영을 소유한다.

병렬 실행은 다음 규칙을 지킨다.

1. 메인 에이전트가 각 Wave의 state transition, request/response shape와 완료 조건을 먼저 동결한다.
2. 한 Wave에서 메인 에이전트와 최대 두 서브에이전트를 기본으로 하며, 선행 계약이 정해지지 않은
   frontend/backend 작업을 동시에 시작하지 않는다.
3. 모든 worker에게 다른 작업자가 같은 저장소에서 작업 중이며, 타인의 변경을 되돌리지 말고 현재
   worktree에 맞춰 구현해야 한다고 명시한다.
4. `app/workspace/service.py`, `app/design/graphs/subgraphs.py`, `frontend/src/lib/api.ts`처럼 여러 흐름이
   만나는 파일은 한 에이전트만 소유하거나 메인 에이전트가 순차 통합한다.
5. backend와 frontend agent는 같은 fixture ID, target ID, bundle digest 예시를 사용해 계약 불일치를
   줄인다.
6. 메인 에이전트는 결과별 diff와 테스트를 검토하고, checkpoint 재개·UTF-8·실제 fixture 통합
   회귀까지 확인한 뒤 Wave를 완료한다.

### Wave 1: 초기 카드의 조건부 표시와 AZ 제거

1. `showDeploymentPreferences`를 `currentStage && !deploymentPreferences` 조건에서 제거한다.
2. backend pending `resource_questions` 중 required provider·region 여부를 화면 계약에 노출한다.
3. 질문이 있을 때만 `DeploymentPreferencesCard`를 해당 conversation event의 answer widget으로
   렌더한다.
4. `resource_intake.draft` 또는 이미 해소된 값으로 provider·region을 미리 채운다.
5. 카드 완료 조건에서 Zone 선택을 제거한다.
6. 기본 카드의 Availability Zones 영역과 Zone 개수 문구를 제거한다.
7. `zones=[]`가 request·normalization·RESOURCE_SPEC round-trip을 통과하는지 검증한다.
8. saved preferences가 적용된 뒤 같은 질문이 다시 나타나지 않게 한다.

### Wave 2: requirements 질문 경계 정리

1. 최초 자연어에서 provider·region이 정확히 추출되면 구조화 카드 없이 resource contract를
   진행한다.
2. provider만 있거나 region만 모호한 경우 부족한 field 중심으로 카드를 구성한다.
3. capability 질문과 CSP·리전 질문의 UI kind를 분리한다.
4. `suggested` capacity 질문이 Workspace에서 `awaiting_input`을 만드는 경로가 없는지 확인한다.
5. capacity가 실제로 SKU 후보 생성을 막을 때만 DeploymentSizingPanel로 보완 입력을 넘긴다.
6. 질문 답변 뒤 전체 Requirements를 반복하지 않고 `build_resource_spec`을 포함한 현재 gate부터
   재개한다.

### Wave 3: 기존 workload topology 계약 producer 연결

1. 현재 지원 범위를 generated application 하나와 선택적 PostgreSQL container 하나로 고정한다.
2. 상류 구조화 산출물에서 기존 `workloadContract`, `connectionContract`, `constraintContract` 후보를
   만드는 결정론적 producer를 추가한다.
3. 기존 단일 VM·단일 replica H2 규칙과 별도 PostgreSQL 규칙의 우선순위를 고정한다.
4. 모호한 data execution mode만 조건부 질문으로 만들고 승인 답을 기존
   `deployment_planning_facts`에 저장한다.
5. 같은 source ref에서 안정적인 contract ID를 만들고 중복 fact를 생성하지 않는다.
6. 계약 validation이 끝나기 전에는 sizing preview를 열지 않는다.
7. 답변 뒤 WorkloadGraph부터 재개하고 완료된 class·sequence·API·ERD는 재사용한다.
8. topology 수정 요청은 label reviser가 아니라 계약 producer와 승인 경로로 보낸다.

### Wave 4: target·SKU·replica 단일 gate

1. deployment bundle이 target별 diagram과 sizing preview를 모두 노출하게 한다.
2. Deployment Diagram 화면에서 target tab, sizing과 compute cost를 함께 보여 준다.
3. `apply_deployment_sizing(targetId, selections)`을 최종 deployment configuration action으로 정한다.
4. 대화형 `deployment.selectedTarget` 확정 경로를 제거하거나 화면 이동 action으로 바꾼다.
5. final target과 모든 compute selection을 하나의 요청에서 검증한다.
6. 적용 성공 뒤에만 bundle을 final로 저장하고 design gate를 재개한다.
7. 한 compute unit이라도 실패하면 target이나 일부 selection만 저장하지 않는다.

### Wave 5: 완료 후 요약과 재편집

1. final artifact에서는 sizing editor 대신 target·SKU·replica·월 compute 소계 요약을 먼저 보인다.
2. “배포 구성 변경” action으로만 editor를 다시 연다.
3. 수정 요청에 bundle/version digest를 포함해 stale selection을 거부한다.
4. CSP·리전 변경은 deployment와 downstream artifact만 재계산한다.
5. SKU·replica 변경은 선택 target projection만 재투영한다.
6. event timeline에는 raw 선택 JSON 대신 사용자가 읽을 수 있는 확정 요약을 한 번만 남긴다.

## 15. 파일별 예상 변경 범위

### Backend

- `app/requirements/resources/service.py`
  - 기존 missing/ambiguous 질문을 canonical source로 유지
  - suggested capacity가 blocking 질문으로 노출되지 않게 경계 확인
- `app/requirements/orchestration/feedback_gates.py`
  - CSP·리전 답변 뒤 현재 resource 단계부터 재개하는 동작 유지
- `app/design/services/deployment_diagram/planning_facts.py`
  - 기존 상류 artifact와 추가 planning fact의 source ref·authority 규칙 유지
- `app/design/services/deployment_diagram/workload_contracts.py` 또는 동등한 작은 producer 모듈
  - 지원 범위의 기존 workload·connection·constraint fact 후보 생성
  - H2와 별도 PostgreSQL 실행 방식의 결정 규칙 및 안정적인 contract ID
- `app/design/services/deployment_diagram/template_topology.py`
  - producer가 승인한 기존 계약을 WorkloadGraph seed에 적용
- `app/design/services/deployment_diagram/normalization.py`
  - 기존 명시 계약 적용, H2 fallback과 endpoint/secret binding 검증 재사용
- `app/design/graphs/subgraphs.py`
  - topology 질문 답변 뒤 `deployment_planning_facts`를 전달하고 WorkloadGraph부터 재개
- `app/workspace/service.py`
  - pending field 기반 cloud input UI metadata
  - 모호한 data execution mode의 조건부 질문과 승인 action
  - 독립된 `deployment.selectedTarget` 질문 경로 정리
  - deployment configuration 완료 상태와 action 제공
- `app/workspace/api.py`
  - 기존 sizing 조회·적용 API에 bundle/version digest 검증 추가
- `app/design/service.py`
  - sizing 적용을 최종 target·SKU·replica의 canonical commit 경계로 사용
- `app/design/services/deployment_diagram/sizing.py`
  - 자동 Zone 배치와 최종 selection 검증
- `app/design/services/deployment_diagram/bundle.py`
  - preview와 final 상태를 명확히 구분

### Frontend

- `frontend/src/routes/workspace/+page.svelte`
  - stage 기반의 상시 `showDeploymentPreferences` 제거
- `frontend/src/lib/components/ChatTimeline.svelte`
  - pending resource question 위치에 CSP·리전 answer widget 표시
- `frontend/src/lib/components/DeploymentPreferencesCard.svelte`
  - 부분 입력 prefill
  - AZ 선택과 완료 조건 제거
- `frontend/src/lib/components/ArtifactPane.svelte`
  - deployment configuration gate 상태 표시
  - target 비교와 sizing의 단일 진입점
- `frontend/src/lib/components/DeploymentSizingPanel.svelte`
  - preview/final 모드 구분
  - target·SKU·replica 단일 Confirm
  - 확정 뒤 요약과 명시적 재편집
- `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`
  - pending cloud decision과 bundle/version digest 계약 반영

## 16. 대표 검증 사례

| 사례 | 기대 결과 |
|---|---|
| 최초 요구사항에 `AWS ap-northeast-2`가 정확히 있음 | CSP·리전 카드가 나타나지 않고 Requirements 계속 |
| 최초 요구사항에 CSP·리전이 없음 | resource intake 직후 선택 카드 한 번 표시 |
| `AWS 서울`처럼 catalog에서 하나로 해소됨 | AWS와 해소된 region을 사용하고 재질문 없음 |
| AWS만 있고 region 없음 | AWS가 미리 선택된 region UI만 표시 |
| `서울 리전`만 있어 CSP가 모호함 | 가능한 CSP·region 조합만 표시 |
| region에 Zone 목록이 존재하지만 Zone을 고르지 않음 | CSP·리전 target 저장 성공 |
| deployment preference 저장 후 Requirements 재개 | 같은 provider·region 질문이 반복되지 않음 |
| suggested min vCPU 질문만 남음 | Requirements 진행을 막지 않음 |
| capability에 persistent storage 의미가 모호함 | Requirements review에서 결과 중심 질문 표시 |
| 영속 데이터 요구가 없음 | generated application workload 하나만 생성 |
| ERD + 단일 VM + replica 1, 별도 DB 요구 없음 | 기존 H2 file DB와 retained disk를 앱 workload에 적용 |
| 별도 PostgreSQL이 요구사항에 명시됨 | 기존 계약으로 app + PostgreSQL workload와 내부 TCP 연결 생성 |
| 별도 DB 요구가 있으나 엔진이 없음 | PostgreSQL을 추측하지 않고 배포 준비 질문 표시 |
| 같은 topology 답변으로 재개 | contract ID와 WorkloadGraph structure digest가 동일하고 중복 fact 없음 |
| topology 질문 답변 후 재개 | class·sequence·API·ERD를 반복 생성하지 않고 WorkloadGraph부터 실행 |
| DB 분리 수정 요청 | label reviser가 아니라 계약 변경 계획과 영향 범위 승인으로 라우팅 |
| 복수 CSP target의 deployment preview 완료 | 각 target의 diagram·SKU·비용을 확정 전 비교 가능 |
| final target이 없는 preview | 채팅에서 target 이름만 먼저 고르게 하지 않고 구성 화면 안내 |
| target tab을 변경함 | 해당 target의 sizing 후보와 비용 로드, artifact final 상태는 불변 |
| SKU·replica Confirm | target과 모든 compute selection이 한 번에 저장되고 diagram·IaC 일치 |
| 한 compute unit에 SKU 후보가 없음 | Confirm 비활성화, target 일부 저장 없음 |
| replica 2 이상이고 safety unknown | 같은 sizing gate에서만 조건부 확인 요구 |
| minimumZones=2, replica=2 | 사용자가 AZ를 고르지 않아도 두 catalog Zone에 자동 배치 |
| region catalog Zone이 minimumZones보다 부족 | 최종화 차단, 다른 region 또는 replica/가용성 수정 안내 |
| 확정된 deployment artifact 재조회 | 읽기 전용 요약 우선, 질문처럼 editor가 열리지 않음 |
| 확정 뒤 SKU 변경 | “배포 구성 변경”을 눌러 선택 projection만 재생성 |
| 오래된 bundle digest로 Apply | 409/validation 오류와 최신 preview 재조회 안내 |

## 17. 테스트 전략

### 17.1 requirements 단위·graph 테스트

- 최초 자연어와 structured preferences의 provider·region 우선순위가 유지된다.
- 정확한 provider·region은 resource question을 만들지 않는다.
- missing/ambiguous field만 required question으로 남는다.
- suggested question은 requirements gate를 막지 않는다.
- 답변 뒤 완료된 이전 requirement 단계는 반복하지 않고 resource contract부터 재개한다.

### 17.2 workload topology 계약 테스트

- 상류 입력이 같으면 같은 workload·connection·constraint contract가 생성된다.
- 영속 데이터가 없으면 DB workload나 disk를 만들지 않는다.
- 단일 VM H2 조건과 별도 PostgreSQL 조건이 동시에 적용되지 않는다.
- ERD만으로 DB engine 또는 process 경계를 추측하지 않는다.
- 명시적 PostgreSQL 계약이 기존 WorkloadGraph, DeploymentPlan, provider ResourcePlan과 IaC까지
  연결된다.
- source ref가 없거나 지원되지 않는 workload 후보는 accepted fact가 되지 않는다.
- topology 질문 답변 뒤 저장된 checkpoint에서 WorkloadGraph 단계만 재개한다.
- topology 변경 feedback은 이름 수정 경로로 들어가지 않는다.

### 17.3 frontend component 테스트

- pending field가 없으면 `DeploymentPreferencesCard`를 렌더하지 않는다.
- 부분 입력을 올바르게 prefill한다.
- Zone 선택 없이 save button이 활성화된다.
- final deployment에서는 summary가, preview에서는 sizing editor가 보인다.
- target tab 전환만으로 final target이 저장되지 않는다.

### 17.4 deployment service 테스트

- target별 sizing preview는 저장 artifact를 변경하지 않는다.
- final apply는 target·SKU·replica를 atomic하게 저장한다.
- 자동 Zone 배치가 replica/minimumZones와 일치한다.
- ResourcePlan, runtime/provisioning diagram과 IaC가 같은 selection digest를 사용한다.
- SKU·replica 변경은 WorkloadGraph를 바꾸지 않는다.
- stale bundle/version은 적용되지 않는다.

### 17.5 실제 fixture

저장된 단일 target fixture와 AWS·Azure·GCP 복수 target fixture를 사용한다. Requirements와 class/API
생성을 매번 반복하지 않고 기존 checkpoint에서 resource 또는 deployment 단계부터 재개한다.

- 단일 target: 조건부 CSP 질문 → deployment sizing → final IaC
- 복수 target: target별 preview → 비용·SKU 비교 → 하나의 target 확정
- 앱만 있는 topology: 단일 generated application → 단일 compute sizing
- 앱 + PostgreSQL topology: 두 workload와 connection → compute별 sizing → final IaC
- 모호한 데이터 실행 방식: topology 질문 → 승인 fact → WorkloadGraph부터 재개
- HA constraint: replica/minimumZones → AZ 자동 배치
- 부족한 capacity: sizing panel에서 보완 후 후보 재계산

## 18. 이번 범위에서 하지 않는 것

- CSP·리전·SKU의 완전 자동 선택
- “가장 좋은 클라우드” 또는 CSP 간 절대 성능 순위 제공
- 실제 account quota와 Zone 순간 capacity 검증
- 전체 청구액 예측 또는 예산 충족 보장
- Kubernetes와 RDS·Cloud SQL·Azure Database 등 managed service 지원
- 임의의 worker·cron 또는 여러 generated application 자동 분리와 별도 코드 패키징
- ERD만으로 PostgreSQL이나 다른 DB engine 자동 선택
- LLM이 WorkloadGraph 또는 provider resource topology를 직접 작성
- 실제 cloud apply
- 초기 사용자에게 AZ 이름 선택 요구
- 채팅 Agent가 임의의 provider·region·SKU를 생성하거나 확정
- 별도 CloudConversationAgent 추가

## 19. 완료 조건

1. CSP·리전 카드가 실제 required field가 부족하거나 모호할 때만 나타난다.
2. 최초 자연어에서 정확히 추출한 CSP·리전을 다시 묻지 않는다.
3. 일반 사용자는 AZ를 선택하지 않고도 deployment target을 저장할 수 있다.
4. Requirements 중에는 CSP·리전과 의미상 필요한 capability 질문만 interrupt를 만든다.
5. 기존 workload·connection·constraint contract의 production producer가 연결된다.
6. 기본 앱, 단일 VM H2와 명시적 PostgreSQL container가 고정된 규칙으로 서로 배타적으로 결정된다.
7. ERD만으로 DB engine이나 별도 DB workload를 추측하지 않는다.
8. topology가 모호할 때만 사용자 확인을 받고, 답변 뒤 WorkloadGraph부터 재개한다.
9. SKU·replica 선택은 유효한 WorkloadGraph, DeploymentPlan과 target별 diagram이 생성된 뒤에만
   나타난다.
10. 사용자는 최종 target을 고르기 전에 target별 SKU와 compute 비용을 비교할 수 있다.
11. 최종 target·SKU·replica는 하나의 canonical action으로 atomic하게 확정된다.
12. chat target 선택과 artifact sizing Apply가 서로 다른 최종화 경로로 남지 않는다.
13. AZ는 minimumZones·replica·region catalog에서 결정론적으로 계산된다.
14. 확정 뒤에는 편집 UI보다 읽기 전용 요약이 먼저 보인다.
15. 설정을 변경할 때 필요한 deployment와 downstream만 다시 계산한다.
16. 새 DB schema나 artifact 계약 없이 기존 preference, planning fact, artifact version과 deployment
    bundle로 동작한다.

## 20. 구현 중 지켜야 할 원칙

- 사용자가 고를 값의 종류와 그 값을 물을 시점은 별개의 문제다.
- 최초 자연어에서 이미 얻은 값을 구조화 UI로 다시 요구하지 않는다.
- 질문은 해당 값을 소비하는 단계보다 앞서되, 사용자가 판단할 근거가 생긴 뒤에 보여 준다.
- CSP·리전은 requirements 모델링 내용이 아니라 project deployment preference로 유지한다.
- workload topology는 CSP primitive가 아니라 상류 요구사항·설계와 승인된 planning fact의 결과다.
- ERD는 데이터 영속성의 근거이지 DB engine 또는 process 경계의 근거가 아니다.
- LLM은 workload와 connection을 발명하지 않고, 코드는 기존 계약과 source ref를 검증한다.
- WorkloadGraph가 완결되기 전에는 compute SKU 선택을 시작하지 않는다.
- SKU·replica는 추상 요구사항이 아니라 실제 DeploymentPlan을 본 뒤 결정한다.
- final target은 SKU·비용 비교보다 먼저 확정하지 않는다.
- AZ는 사용자 입력이 아니라 provider projection의 배치 결과다.
- preview 조회와 final commit을 구분한다.
- 같은 선택을 chat과 artifact UI의 두 경로에서 확정하지 않는다.
- 기존 `deployment_planning_facts`, WorkloadGraph, ResourcePlan, sizing catalog와 IaC renderer를
  재사용하고 같은 역할의 영속 모델이나 외부 계약을 추가하지 않는다.
