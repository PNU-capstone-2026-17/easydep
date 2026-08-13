# 배포 토폴로지·VM 추천·단계적 검증 통합 계획

> 상태: 다음 구현과 비교평가를 위한 결정 문서
>
> 적용 범위: AWS·Azure·GCP의 Docker-on-Linux-VM 배포
>
> 연구 배경의 진실 원천: [research.md](research.md). 이 문서는 연구 배경을 바꾸지 않고 실현 방법만 정한다.

## 1. 결정과 범위

현재 방향은 실현 가능하다. 범용 클라우드 아키텍처 설계기로 확장하지 않고 다음 경로만 완성한다.

```text
요구사항과 설계 산출물
→ 실행 계약과 Workload 배치 의도
→ 선택 CSP의 ResourcePlan
→ 같은 ResourcePlan에서 배포 구조도와 IaC 생성
→ 정적 정합성
→ Docker 업무 기능과 개발용 부하 측정
→ VM 후보 확정
→ 실제 cloud 업무 기능·비기능 검증
```

| 항목 | 결정 |
|---|---|
| 토폴로지 | 배포 단위와 클라우드 리소스의 노드·관계 그래프 |
| 사용자용 다이어그램 | 앞 단계에서 선택한 CSP에 맞는 구조도 한 종류 |
| 진실 원천 | 다이어그램이 아니라 구조화된 `ResourcePlan` |
| CSP 실현 정보 | 별도 `ProviderPlan` 없이 `ResourcePlan` 안에 포함 |
| IaC | `ResourcePlan`을 번역하고 Terraform Plan JSON과 대조 |
| VM 추천 | 도메인명이나 LLM 추측이 아니라 고정 image·부하의 측정 근거로 후보 제한 |
| 검증 | 정적 → Docker 업무 기능 → 실제 cloud 기능·NFR 순서 |
| 비교평가 | 순서 자체보다 계약·추적·CSP 근거·부분수정 능력 평가 |

`ResourcePlan`은 여러 region의 Workload·Compute 배치와 CSP가 요구하는 전역 제어 자원을 표현할 수 있게 둔다. 다만 이번 구현·주 실험은 한 region의 Workload backend만 실제 생성·검증한다. 다중 region 요구가 들어오면 단일 region으로 조용히 축소하지 않고 region별 배치 의도를 plan에 보존하면서 실행 지원 상태를 `unsupported`로 기록한다. **표현 가능성은 생성·배포 지원을 뜻하지 않는다.** 특히 권위 있는 변경 가능 상태의 region 간 복제·일관성·자동 장애조치는 모델의 배치 표현만으로 해결됐다고 보지 않는다.

그 밖의 범위는 일반 `Workload`와 영속성, VM·network·subnet·IP·firewall·disk·선택적 LB, 고정 부하에서의 제한된 VM 후보 검증이다. 자체 운영 DB 복제·자동 장애조치, autoscaling, Kubernetes, 관리형 DB, OPA·solver, TOSCA·UML 완전 준수, 모든 토폴로지 열거, 삭제 의존성, 범용 비용·성능 최적화는 제외한다.

여기서 토폴로지는 임의로 차용한 말이 아니다. TOSCA도 서비스 구조를 노드·관계·요구사항·capability의 그래프로 표현한다. 다만 `ResourcePlan`은 표준을 새로 제안하는 것이 아니라 EasyDep 범위의 내부 orchestration IR이며, 결과물도 엄격한 UML 모델이 아닌 **PlantUML 기반 배포 구조도**라고 부른다.

- [OASIS TOSCA 2.0](https://docs.oasis-open.org/tosca/TOSCA/v2.0/TOSCA-v2.0.html)
- [OMG UML 2.5.1](https://www.omg.org/spec/UML/2.5.1/)

## 2. 현재 코드와 목표의 차이

| 현재 상태 | 한계 | 이번 목표 |
|---|---|---|
| DepKB의 `dependency_plan` | 생성에 필요한 리소스 폐쇄 집합이지 전체 배포 계획은 아님 | `dependencyEvidence` 역할로 한정하고 `ResourcePlan`의 근거로 사용 |
| 설계 구조도와 IaC가 별도 판단 경로를 사용 | 서로 다른 리소스·관계를 만들 수 있음 | 한 번 만든 plan을 두 출력기가 소비 |
| 범용 UML 노드를 LLM이 추출 | 클래스나 저장소를 실행 단위로 과분할할 수 있음 | 독립 배포 산출물과 실행 계약이 있을 때만 `Workload` 확정 |
| 전역 CPU·memory 하한 하나 | 서로 다른 Compute 역할을 구분하지 못함 | Compute 자원별 capacity 상태와 근거 |
| 단일 GET·단일 동시성 개발 측정 | 실제 업무 혼합과 목표 도착률을 대표하지 못함 | 동결한 업무 profile을 열린 부하로 측정 |
| 공통 Docker 평가가 단일 컨테이너 중심 | 여러 Workload와 공유 상태 검증 불가 | 필요한 컨테이너 집합만 기동하는 평가 경계 |
| Terraform `validate` 중심 | 실제 plan의 수·종류·참조를 확인하지 못함 | 저장된 Plan JSON의 정규화 관측값 대조 |
| 설계·추적성은 파일 존재만 기술 | 순차 산출물 장점을 측정하지 못함 | 필수 연결과 불변식 전파를 공통 게이트로 평가 |
| cloud 동적 NFR 노드는 미구현 | 현재 P3의 부하 목표도 실제 검증되지 않음 | 같은 업무 profile을 실제 배포에서 재실행 |

기존 DepKB, 앱–cloud 계약, VM 카탈로그 필터, 체크포인트, Docker 업무 oracle은 새 경계 아래에서 재사용한다. 미구현 상태 자체가 문제가 아니라, 구현 순서와 각 단계가 증명하는 범위를 혼동하지 않는 것이 중요하다.

## 3. 최소 `ResourcePlan`과 도출 규칙

### 3.1 스키마보다 먼저 고정할 의미

실제 JSON 필드는 변경 가능하게 두고 다음 의미만 고정한다.

```text
ResourcePlan
├─ intent
│  ├─ workloads
│  │  └─ runtime, image/artifact, replicas, port, health,
│  │     environment, outbound dependency, persistence, mount
│  ├─ connections
│  │  └─ from, to, protocol, port, visibility
│  └─ endpoints
│     └─ target, public/private, protocol, direct/LB
├─ resources
│  └─ id, kind, providerType, scope(global|regional|zonal),
│     optional region/zone, properties,
│     logicalRef 또는 projectionRuleId
│     └─ kind=compute이면 capacity
│        └─ unresolved | provisional | validated,
│           minimumVCpu, minimumMemory, selectedSku,
│           capacityPolicyVersion, evidenceRefs
├─ allocations
│  └─ id, workloadRef, replicaIndex 또는 role, computeRef
├─ relations
│  └─ attaches | routesTo | connectsTo | belongsTo
│     | requiresForProvisioning(evidenceRef 또는 projectionRuleId)
└─ decisions
   └─ field, value, basis, sourceRefs
```

`kind`에는 현재 범위의 `compute`, `volume`, `network`, `subnet`, `firewall`, `publicIp`, `loadBalancer` 등을 쓴다. `scope`는 GCP VPC나 L7 LB의 일부처럼 전역인 native resource도 정확히 표현한다. 서로 다른 region 값을 가진 Compute와 그 `allocations`를 함께 두면 다중 region 배치 의도를 잃지 않고 표현할 수 있다. CSP 보조 리소스와 생성 의존관계는 논리 요소의 `logicalRef` 또는 공식 제약에 근거한 `projectionRuleId` 중 하나를 가져야 한다. `allocations`는 각 replica가 어느 Compute에 배치되는지를 고정하고, Volume 연결은 `attaches` 관계가 표현한다. `requiresForProvisioning`의 방향은 `의존 자원 → 먼저 필요한 자원`이다. 삭제 의존성은 제외하지만 생성 의존관계는 DepKB 효과를 검증하는 핵심이므로 유지한다.

초기 설계에서는 정확한 VM SKU를 모를 수 있다. 이때 Compute의 `capacity=unresolved`인 plan과 “사양 미확정” 구조도를 만들고, Docker 측정 뒤 같은 plan의 새 revision에서 SKU만 해소한다. 이는 다이어그램 2종이 아니라 동일 산출물의 근거 기반 갱신이다.

### 3.2 `Workload` 경계

클래스 수나 BCE의 Boundary·Control·Entity 수로 Workload를 나누지 않는다. 최소한 독립적인 실행 산출물 또는 image가 있어야 하며, 다음 중 하나 이상의 근거가 있어야 한다.

- 독립 배포 수명주기
- 독립 확장 요구
- 장애 격리 또는 별도 배치 요구
- 별도 network endpoint와 실행 계약

정적 프론트엔드를 백엔드 image가 함께 제공하면 한 Workload일 수 있다. 별도 Node 서버나 image로 배포하면 별도 Workload다. 데이터베이스도 특별한 고정 타입이 아니라 영속성과 network endpoint를 가진 Workload로 표현한다.

### 3.3 입력과 도출

| 입력 | 사용하는 사실 |
|---|---|
| 구조화 요구사항 | CSP·region, 외부 노출, 가용성 범위, 영속성, 목표 부하·지연·오류율 |
| OpenAPI | 제공 API와 통신 protocol |
| ERD 또는 논리 데이터 모델 | 권위 상태, 데이터 소유권, 공유·영속성 필요 |
| 핵심 시퀀스 | 실행 단위 사이 호출, 트랜잭션 경계, 동시성 불변식 |
| 구현 계획·RuntimeContract | 독립 image, 시작 명령, listen port, health, 환경변수, mount |
| CSP 공식 문서와 DepKB | native resource와 생성에 필요한 관계 |

도출 절차는 다음으로 제한한다.

1. 자연어와 설계에서 배포 사실과 근거 ID를 추출한다.
2. 명시되지 않은 값은 `FORBIDDEN`이 아니라 `UNSPECIFIED`로 둔다.
3. 독립 산출물과 실행 계약으로 Workload를 확정한다.
4. persistence, Connection, Endpoint, replica와 배치 요구를 만든다.
5. 일반 불변식, provider 제약, 프로젝트 정책을 구분해 기록한다.
6. 근거 없이 결정할 수 없으면 `needsInput` 또는 `unsupported`로 남긴다.
7. 선택 CSP의 근거로 실제 리소스와 관계를 만든다.

최소 규칙은 다음과 같다.

| 조건 | 파생 결정 | 근거 종류 |
|---|---|---|
| 외부 접근 요구 | public Endpoint와 IP·firewall 경로 | 사용자 요구 |
| active replica 2개 이상과 단일 진입점 | CSP 관리형 VM 그룹, LB, backend, health check와 자동 복구 | 요구와 현재 제품 정책 |
| 권위 있는 변경 가능 상태를 여러 replica가 공유 | 공유 상태 서비스 또는 검증된 조정 방식 | 앱 불변식 |
| 영속성 요구 | Volume, attachment, mount, restart 시험 | 앱–cloud 계약 |
| App VM 한 대 장애 대응 | App replica 2개 이상, 여러 Zone의 CSP 관리형 VM 그룹과 health 기반 자동 교체 | 해당 계층 가용성 요구 |
| CSP 보조 리소스 필요 | `projectionRuleId`를 가진 native resource | provider 제약 |

“다중 VM이면 항상 공유 DB”, “단일 VM이면 LB 금지” 같은 보편 규칙은 두지 않는다. 사용자 피드백이 앞 단계 변경을 요구하면 현재 단계에서 임의 패치하지 않고 `upstreamRevisionRequired`로 중지한다. 되돌아갈 단계와 영향 범위를 제시하고 승인 후 하위 산출물만 다시 만든다.

## 4. 구조도·IaC 생성과 정적 대조

### 4.1 선택 CSP 구조도 한 종류

사용자가 CSP를 앞 단계에서 선택하므로 최종 구조도는 해당 CSP의 배치와 실제 리소스를 보여주는 한 종류면 충분하다. 별도 중립 구조도는 내부 `intent`를 반복하고 정합성 대상만 늘린다.

구조도에는 다음을 표시한다.

- region과 network·subnet 경계
- VM과 `allocations`에 따른 Workload instance
- public/private Endpoint와 LB 경로
- Volume 소유·attachment 관계
- Workload 사이 protocol·port
- 필요한 CSP 보조 리소스와 각 plan ID

firewall rule과 listener의 모든 속성은 그림을 복잡하게 만들지 않고 요약 label 또는 별도 표에 둔다. 그림을 다시 해석해 IaC를 만들지 않는다.

```text
ResourcePlan ──→ PlantUML renderer ──→ 배포 구조도
       └──────→ IaC generator ──────→ Terraform
```

구조도는 미관이 아니라 렌더 성공, 표시 대상 노드·관계 누락 수, 유효하지 않은 plan ID 참조 수로 평가한다.

### 4.2 Terraform 대조

현 LLM 기반 IaC 생성은 유지할 수 있지만 LLM은 plan을 새로 설계하지 않고 HCL로 번역한다. 생성기가 내는 `resourceBindings`는 정답이 아니라 다음 검사를 받아야 하는 주장이다.

- binding의 Terraform address가 Plan JSON에 실제 존재하는가
- provider type과 plan의 `kind`가 대응하는가
- 수량·scope·속성·참조와 `requiresForProvisioning` 관계가 plan을 만족하는가

검증 순서는 다음과 같다.

1. `ResourcePlan` 구조와 불변식 검사
2. Terraform `fmt`, 고정 provider cache를 사용한 `init`, `validate`
3. 실제 변수로 저장된 `plan` 생성
4. `terraform show -json` 결과를 공통 리소스·참조 관측값으로 정규화
5. plan, binding, 관측값 대조

Plan JSON으로 정적으로 확인할 수 있는 것은 VM·disk·LB·network·security rule의 선언, 수량, scope, 속성, attachment와 참조다. `allocations`에 해당하는 bootstrap 선언도 확인할 수 있지만 컨테이너가 실제 VM에서 실행되는지, guest OS에서 mount가 성공하는지, 통신이 firewall을 실제로 통과하는지는 Docker 또는 cloud runtime gate가 증명한다.

판정은 `passed`, `failed`, `notObserved`로 분리한다. 정적으로 관측 가능한 hard constraint가 모두 통과하고 실패가 없으며, 각 `notObserved` 항목에 후속 runtime gate가 지정된 경우에만 apply할 수 있다.

Plan과 JSON에는 민감값이 포함될 수 있으므로 원문은 커밋하지 않는다. 실행 결과에는 digest와 비민감 정규화 관측값만 남긴다.

- [Terraform validate의 범위](https://developer.hashicorp.com/terraform/cli/commands/validate)
- [Terraform show와 JSON 출력](https://developer.hashicorp.com/terraform/cli/commands/show)

## 5. 측정 기반 VM 후보

### 5.1 현재 기능의 의미

현재 VM 선택기는 명시된 최소 vCPU·memory를 만족하는 CSP·region의 저가 후보를 결정론적으로 찾는다. 실제 앱을 보고 운영 사양을 자동 right-sizing하는 기능은 아니다.

현재 개발 측정은 단일 `/notes` GET, 동시성 4, 약 15초의 한 지점이다. 닫힌 부하 모델은 응답이 느려지면 새 요청 도착도 늦어지는 coordinated omission 위험이 있으므로 여기서 나온 8 vCPU 후보를 다른 앱이나 운영 부하에 일반화하면 안 된다. 목표 RPS에는 응답 완료와 독립적인 열린 부하 모델을 사용한다. [k6의 열린 모델과 닫힌 모델](https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/)

### 5.2 후보 상태와 profile

| 상태 | 근거 | 의미 |
|---|---|---|
| `unresolved` | 목표 부하·SLO 또는 신뢰할 측정 근거 없음 | 사양 결정 보류 |
| `provisional` | 고정 Docker image와 개발 부하 측정 | 이 개발 profile의 임시 후보 |
| `validated` | 같은 image·scenario·CSP·SKU의 cloud 시험 통과 | 이 고정 profile에서 확인한 후보 |

`workloadProfile`에는 image·seed data digest, 실행 옵션, operation과 비율, payload, 목표 도착률 또는 재현 가능한 사용자 흐름, warm-up·측정 시간, p95·오류율 상한, 반복 번호, host·container quota를 기록한다. “동시 사용자 100명”만 있고 사용자 흐름과 think time이 없으면 RPS를 추측하지 않고 입력을 요청하거나 보류한다.

### 5.3 최소 폐루프

1. 업무 oracle과 `workloadProfile`을 동결하고 정확한 image의 Docker 기능 검사를 통과한다.
2. 열린 부하로 목표 지점을 짧게 3회 측정하고 오류·지연·CPU·RSS와 병목 증거를 기록한다.
3. 버전이 고정된 변환 정책으로 Compute 자원별 임시 하한을 만들고 CSP 카탈로그에서 비경고 저가 후보를 고른다.
4. Compute의 capacity·SKU 근거를 갱신하고 구조도·IaC·Plan 대조만 다시 실행한다.
5. 실제 cloud에서 같은 image와 profile로 기능·NFR을 검사한다.
6. SLO 실패와 CPU·memory 포화가 함께 확인될 때만 다음 사양으로 최대 한 번 올리고, 통과 시 `validated`로 바꾼다.

앱 오류, 상태 Workload·disk·network 병목, 부하 발생기 포화를 VM 증설로 숨기지 않는다. replica나 LB 변경은 SKU 수정이 아니라 토폴로지 변경이므로 설계 단계로 돌아간다. 신뢰할 측정이 어려운 Workload는 명시 하한과 cloud 관측을 사용하되 측정 기반 추천이라고 부르지 않는다.

최초 변환 정책은 다음처럼 단순하고 결정론적으로 둔다. 각 값은 보편 법칙이 아니라 실험 전에 고정하는 `capacityPolicyVersion`의 프로젝트 안전 여유다.

```text
observedCpu = 한 Compute에 할당될 Workload 집합의 합산 CPU 시계열에서 3회 중 가장 큰 p95 cores
observedMemory = 같은 집합의 합산 working-set 시계열에서 3회 중 가장 큰 p99 GiB
minimumVCpu = ceil(observedCpu / 0.70)
minimumMemoryGiB = observedMemory × 1.25 + 고정 guest OS reserve
```

guest OS reserve는 base image별 측정값과 함께 정책에 고정하고 실행별로 바꾸지 않는다. replica 수는 `targetRps / observedRps` 같은 선형식으로 추정하지 않고 토폴로지 요구가 정한다. 측정값을 Compute별로 귀속할 수 없거나 정책 version·quota가 없으면 `provisional`로 승격하지 않고 `unresolved`를 유지한다. disk는 이번 범위에서 용량 하한만 다루며 IOPS·throughput 추천은 하지 않는다.

가격 비교 범위는 compute 목록가격이다. LB, disk, public IP, network egress를 포함하지 않으면 전체 비용 예산을 만족했다고 표현하지 않는다. 공식 지침도 실제 workload, 사전 정의 KPI, production과 유사한 조건의 반복 부하 시험을 권한다.

- [AWS Well-Architected 부하 시험](https://docs.aws.amazon.com/wellarchitected/latest/framework/perf_process_culture_load_test.html)
- [Azure Well-Architected 성능 시험](https://learn.microsoft.com/en-us/azure/well-architected/performance-efficiency/performance-test)
- [Google SRE의 용량과 부하 시험](https://sre.google/sre-book/introduction/)

## 6. 정적–Docker–cloud 검증과 부분수정

세 단계는 증명 대상과 실패 소유권을 분리하므로 적절하다.

| 단계 | 필수 gate | 통과가 뜻하는 것 | 뜻하지 않는 것 |
|---|---|---|---|
| 정적 정합성 | 요구 ID 추적, 설계 불변식, compile·unit, ResourcePlan–구조도–Plan JSON | 산출물 모순이 없고 실행 후보가 준비됨 | 업무 기능·cloud NFR 성공 |
| Docker 동적 기능 | health, black-box 업무, Workload 연결, 동시성, restart 영속성, 개발 부하 | 고정 로컬 환경에서 기능과 runtime 계약이 동작 | CSP 성능·가용성 보장 |
| 실제 cloud | apply, ready, 같은 업무 oracle, 같은 profile NFR, 요구된 장애·restart, destroy | 특정 plan·image·환경의 종단 관측 | 다른 부하·CSP·운영환경 일반화 |

cloud 시험은 다음 순서로 고정한다.

```text
apply → ready → 업무 기능 → NFR → 요구된 fault/restart
→ destroy → residual resource 0
```

NFR에는 offered RPS, achieved success RPS, dropped request, p95 latency, error rate, 시험 시간, warm-up, image·plan digest, SKU·replica 수를 남긴다. CPU·memory·network·disk I/O는 요구 threshold가 없으면 합격 기준이 아니라 원인 진단값이다. App VM 중지 시험은 연속 업무 요청, LB의 비정상 backend 제외와 CSP 관리형 VM 그룹의 자동 교체까지 확인한다. 이는 App 계층 장애 대응만 검증하며, 단일 상태 Workload가 남으면 종단 HA라고 부르지 않는다.

| 실패 | 소유 단계와 재실행 |
|---|---|
| 요구사항·NFR 모호성 | 사용자에게 되돌림 승인을 받고 영향받는 하위 단계 재생성 |
| Workload·replica·상태 불변식 | 토폴로지부터 재생성 |
| compile·unit·업무 로직 | 해당 구현 하위 작업부터 재실행 |
| port·mount·health·image binding | 구조화 진단이 지정한 구현 또는 VM delivery 하위 작업 |
| plan–IaC 불일치·provider 오류 | IaC와 후속 검증만 재실행 |
| SLO 실패와 CPU·memory 포화 | SKU revision과 구조도·IaC·Plan만 재실행 |
| 상태 Workload·disk·network 병목 또는 replica 변경 | 설계로 돌아갈지 사용자 확인 |
| quota·429·부하 발생기·Docker daemon | `censored` 또는 `environmentFailure`로 시스템 실패와 분리 |

모든 gate는 `passed`, `failed`, `notObserved`, `censored`, `environmentFailure`를 구분한다. 상위 정상 checkpoint의 digest와 재사용 여부, 수정 단계, 재실행 시간과 LLM 호출 수를 남긴다. cloud 실행은 항상 이번 실행이 만든 리소스를 정리하고 residual 0을 다음 실행 조건으로 삼는다.

## 7. 수강신청 통합 벤치마크와 비교평가

### 7.1 도메인 적합성과 범위

수강신청 자체가 cloud-native 도메인인 것은 아니다. Cloud native는 업무 분야가 아니라 반복 가능한 배포, 복원성, 관리성과 같은 구축 방식이다. 정확한 표현은 **Docker-on-VM cloud 관심사를 드러내는 합성 통합 벤치마크**다. [CNCF Cloud Native Definition](https://github.com/cncf/toc/blob/main/DEFINITION.md)

등록 시점의 집중 요청은 도메인 선택의 현실성을 보조하지만 실험 RPS의 정답은 아니다. 본 연구의 부하는 명시적으로 합성한 profile로 기록한다. [UT Austin 등록 집중 요청 기록](https://cloud.wikis.utexas.edu/wiki/spaces/registrar/pages/55643386/Absolute%2BPEAK%2BRegistration), [Taipei Medical University 사례](https://cloud.google.com/customers/tmu)

단순 CRUD면 변별력이 없고, 전체 대학 업무를 넣으면 도메인 개발이 연구를 압도한다.

| 포함 | 제외 |
|---|---|
| 강좌 조회, 신청·취소 | 인증·권한 |
| 정원 초과와 중복 신청 방지 | 선수과목·시간표 충돌 |
| 신청 상태 영속성 | 대기열·우선순위 |
| 여러 App replica의 권위 상태 공유 | 결제·알림·추천 |
| health, 외부 Endpoint, 합성 부하 목표 | 마이크로서비스, 관리형 DB, DB HA, 다중 region 실제 배포·검증 |

동시성 oracle은 구현 기법이 아니라 결과를 검사한다.

```text
남은 좌석 1개에 서로 다른 사용자 N명이 동시에 신청
→ 성공 정확히 1건, 최종 신청 1건, 정원 초과 없음

같은 사용자가 같은 강좌에 동시에 N회 신청
→ 최종 신청 행 1개 이하, 중복 성공 없음
```

### 7.2 산출물별 검증 대상

| 산출물 | 검증할 결정 |
|---|---|
| 구조화 요구사항 | 정원·중복·영속성·부하·가용성 수용 조건 |
| ERD 또는 논리 데이터 모델 | 학생–강좌–신청과 중복 방지 제약 |
| 핵심 신청 시퀀스 | 검사·갱신·신청의 트랜잭션 경계 |
| OpenAPI | 신청·취소 요청과 성공·충돌 응답 |
| RuntimeContract | port, health, 상태 연결, 환경변수, mount |
| ResourcePlan·구조도 | replica, 공유 상태, VM·Volume·LB 배치 |
| 구현·시험·IaC·추적성 | 앞 결정을 실제로 구현하고 검증했는지 |

전체 CRUD 시퀀스, 상세 UI, 모든 클래스의 배포 표시는 불필요하다. BCE 클래스 다이어그램은 구현 보조 산출물이지 Workload 경계의 직접 근거가 아니다. 합성 앱은 실제 조직을 심층 관찰한 현장 사례연구가 아니므로 논문에서는 **평가 사례** 또는 **통합 벤치마크**라고 부른다. [ACM SIGSOFT Empirical Standards](https://www2.sigsoft.org/EmpiricalStandards/docs/standards)

### 7.3 평가 사례

| ID | 고정 업무 | 배포 조건 | 목적 |
|---|---|---|---|
| E1 | 수강신청과 자체 운영 영속 Workload | App 1개, 상태 Workload 1개와 Volume | 기본 산출물 연쇄, 동시성, 영속성 |
| E2 | E1과 같은 API·업무 규칙 | CSP 관리형 앱 VM 그룹의 replica 2개, LB, 같은 상태 Workload | 공유 상태 연결과 App 계층 VM 장애 대응·자동 교체 |
| D1 | 제한 수량 예약 | E2와 같은 불변식, 다른 용어·API | 수강신청 문자열 오버피팅 탐지 |

E2의 단일 상태 Workload는 App 계층의 장애 대응만 평가한다. D1은 같은 문제군 안의 전이를 볼 뿐 모든 도메인 일반화를 증명하지 않는다. 기존 P1~P3은 DepKB 구성요소·smoke 회귀로 유지하고 E1·E2의 모델 근거로 쓰지 않는다.

### 7.4 기준군과 실행 규모

ChatDev와 MetaGPT도 설계·코딩·시험 또는 SOP 기반 중간 단계를 사용한다. EasyDep의 차별점은 순서 자체가 아니라 기계 판독 계약, 요구사항부터 cloud 리소스까지의 추적, CSP 근거, 실패 소유 단계와 checkpoint 기반 부분수정의 결합이다.

- [ChatDev 논문](https://aclanthology.org/2024.acl-long.810/)
- [MetaGPT 논문](https://proceedings.iclr.cc/paper_files/paper/2024/hash/6507b115562bb0a305f1958ccc87355a-Abstract-Conference.html)

이 문서의 E1·E2 계획은 [기존 비교실험 계획](comparison-experiment-plan.md)의 P1~P3 108회 종단 매트릭스를 후속 대체한다. 동결된 기존 suite와 결과는 회귀·과거 근거로 보존하고 수정하지 않으며, 새 suite는 별도 version으로 만든다.

| 실험 | 상한 |
|---|---:|
| E1·E2 × EasyDep·CoT·MetaGPT·ChatDev, 1회 파일럿 | 8회 |
| runner와 공통 평가기가 성공·실패를 정상 분류하면 총 3회로 확대 | 종단 총 24회 |
| D1 × 4개 시스템 개발 실행 | 4회 |
| E2의 `no-depkb`, `no-verification`; full 결과 재사용 | 추가 6회 이하 |
| 사전 지정 반복의 대표 CSP 실제 cloud 비교와 EasyDep의 나머지 CSP 투영 | 최대 6회 |

subject의 산출물 실패는 분모에 남긴다. runner·평가기·외부 환경 실패만 `censored`로 분리한다. 결과를 본 뒤 성공 실행을 고르지 않고 CSP와 반복 번호를 사전 지정한다.

모든 시스템에 같은 외부 산출물, LLM·시간·도구 예산과 평가기를 요구한다. EasyDep 내부 스키마 사용 여부가 아니라 의미 adapter로 요구 ID, 설계 결정, 구조도, code·test 연결을 정규화해 채점한다. 이 공통 평가기가 구현되기 전에는 기준군보다 산출물 연쇄가 우수하다고 주장하지 않는다.

외부 기준군은 EasyDep 전체 시스템 차이만 보여준다. DepKB 효과는 기존 동일 입력 `full`/`no-depkb` 구성요소 절제로 해석하고, E2 `no-verification`은 수정 피드백 효과만 본다. `no-verification`만으로 부분수정이 전체 재실행보다 효율적이라는 인과 주장은 할 수 없다.

### 7.5 지표와 변경 실험

하나의 가중 종합점수 대신 다음 게이트와 원시 수치를 보고한다.

| 범주 | 지표 |
|---|---|
| 산출물 연쇄 | 완결성, 요구사항 추적률, 깨진 참조, 불변식 전파율, 교차 산출물 모순 |
| 구현 기능 | build·unit·integration, 업무 기능, 동시성, restart 영속성 |
| 배포 정합성 | plan–구조도–Terraform의 노드·관계 일치 |
| cloud | apply·ready·업무 기능·NFR·App VM 장애·cleanup |
| 복구 효율 | 탐지·소유 단계, 보존 checkpoint, 재실행 범위·시간·LLM 호출 |
| 비용 | 종단 시간, token, cloud 실행 시간과 관측 가능한 비용 범위 |

변경 실험은 두 개만 둔다.

1. port 또는 mount binding 불일치를 주입하고 소유 하위 작업만 고치는지 관찰한다.
2. 뒤늦게 App VM 장애 대응 요구를 추가하고 IaC만 패치하지 않고 앞 단계로 돌아갈지 묻는지 관찰한다.

별도 full-restart 대조군이 없으면 “상위 단계 재실행 없이 복구한 사례”로만 보고하고 “재실행을 줄였다”는 인과 주장은 하지 않는다. 시스템 비교에서는 동일 VM 사양을 사용한다. VM 추천 평가는 하나의 고정 image·seed·profile로 별도 수행하여 앱 코드 효율과 VM 비용을 섞지 않는다.

## 8. 구현 순서와 주장 한계

| 단계 | 구현 | 완료 조건 |
|---|---|---|
| 0 | 기존 장황한 모델을 3절의 의미로 축소 | 스키마에 수강신청·App·DB 고정 이름이 없음 |
| 1 | RuntimeContract, 최소 ResourcePlan, 구조 검증기 | E1·E2를 같은 타입으로 표현하고 결정 근거가 있음 |
| 2 | 선택 CSP 구조도, ResourcePlan 기반 IaC, Plan JSON 대조 | 같은 plan의 정적 노드·참조가 두 출력에서 일치 |
| 3 | 다중 컨테이너 업무·동시성·영속성 평가 | E1 뒤 E2가 로컬에서 반복 가능하고 잔여 리소스 0 |
| 4 | 열린 부하와 Compute별 후보 상태 | 같은 profile 3회와 `provisional` 근거가 재현됨 |
| 5 | 사전 지정 CSP E1→E2 파일럿 | 기능·NFR·환경 실패가 분리되고 residual 0 |
| 6 | 공통 의미 평가기와 8회 비교 파일럿 | 각 runner 결과를 성공·실패·검열로 정상 분류 |
| 7 | 조건 충족 시 반복 확대·나머지 CSP·D1 | 주장마다 직접 근거와 한계가 연결됨 |

2단계 전에는 3사 apply를 반복하지 않고, 3단계 전에는 성능 추천을 연결하지 않는다. 기능 실패를 더 큰 VM으로 가리지 않는다.

목표 주장은 다음 한 문장이다.

> EasyDep은 제한된 Docker-on-VM 평가 사례에서 요구사항과 설계 산출물로부터 근거가 있는 배포 계획을 만들고, 같은 계획으로 구조도와 IaC를 생성하며, 단계별 검증 결과에 따라 실패 소유 단계부터 부분수정한다.

실험 결과가 직접 뒷받침할 때만 특정 사례의 종단 성공, 동일 입력 DepKB 절제의 관계 누락 차이, 특정 image·profile·CSP·SKU의 NFR 통과를 개별적으로 보고한다. EasyDep만 순차 개발을 지원한다거나, 멀티 에이전트 구조 자체가 원인이라거나, 모든 도메인에 일반화된다거나, 추천 VM이 생산 환경의 최적 사양이라는 주장은 하지 않는다.

이 범위가 학부 졸업과제에서 구현 가능하면서도 산출물 생성·검증, 클라우드 리소스 가이드, 단계 간 연계를 하나의 실행 가능한 흐름으로 묶는 최소안이다.
