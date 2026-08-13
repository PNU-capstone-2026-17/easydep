# 애플리케이션–클라우드 계약 설계

> 상태: 설계안 v1 · 2026-08-08  
> 범위: AWS·Azure·GCP의 Docker-on-VM 생성 및 검증

> **명칭과 소유권:** 아래 세 계약의 이름, 필드, 진단 코드는 특정 외부 표준에서 채택한
> 규격이 아니다. 이 문서에 정의한 형태는 모두 EasyDep이 제안하는 내부 연구 모델이다.
> TOSCA·OAM·Kubernetes·CUE는 설계 근거와 비교 대상이며, EasyDep이 해당 표준을 구현하거나
> 호환된다는 뜻이 아니다.

## 현재 구현 상태

2026-08-08 기준 v1의 최소 실행 경계를 구현했다.

- 세 계약은 고정된 DB·프레임워크·CSP 필드 대신 `facts[].id`, `kind`, `attributes`,
  `extensions`를 안정 코어로 사용한다. 알 수 없는 fact와 capability를 보존하므로 기술 추가가
  곧 스키마 버전 변경을 뜻하지 않는다.
- 기존 `deployment_needs`의 수락 항목은 이름을 해석하지 않고 cloud fact로 옮긴다.
- 앱 소스와 설정에서 관측한 기술 신호는 스키마가 아닌 교체 가능한 탐지 규칙으로
  `build.dependency` fact를 만든다. 현재 Java/Spring 평가 경로에 필요한 JPA, SQLite, H2
  규칙만 있으며 모두 `hypothesis`다.
- scaffold와 logic 하위 작업이 끝날 때마다 관측 fact와 계획 binding을 새로 계산하고 앱 내부
  일관성을 검사한다. 과거 `observed.*`와 `planned.*` 항목은 그대로 누적하지 않는다.
- 앱 HTTP 포트는 cloud backend port에, 영속 데이터 접근 경로는 cloud mount target에
  identity binding으로 연결한다. VM 전달 프롬프트와 생성 Dockerfile이 이 값을 소비하며,
  기존 Dockerfile의 `EXPOSE`가 다르면 `BIND-PORT-001`로 거부한다.
- 테스트 환경값은 파일 내용을 다시 검색해 SQLite를 추측하지 않는다. 앱 계약에 선언된
  `runtime.environment`의 `testValueTemplate`만 사용하고 실행별 임시 경로를 종료 시 정리한다.
- Java 파일 I/O API와 외부설정의 절대 경로가 함께 관측되면 DB 제품명과 무관하게
  `node-filesystem` 상태 fact를 만든다. 다중 영역 요구와 결합되면 공유·복제 전략을 추측하지
  않고 `BIND-STATE-HA-001`로 사용자에게 묻는다.
- 사용자 선택에 따른 수정은 임시 앱 snapshot에서 검증하고, 재검증 실패나 예외가 나면 원본
  checkpoint 파일을 복원한다.
- 요구사항에서 명시적으로 수락된 앱 상태 제약은 `intent.*`, 생성 코드에서 찾은 사실은
  `observed.*` fact로 분리한다. 열린 need 이름은 유지하고 명시된 상태 축만 투영하며,
  생성 에이전트 선언이 요구사항 소유 intent를 덮을 수 없다.

HTTP backend와 영속 mount binding은 자동 생성하며, 생성 HCL·부트스트랩에서 벤더 리소스
타입에 의존하지 않는 강한 관측 신호를 다시 추출해 대조한다. 다만 health·TLS·방화벽은 배포
구성에 따라 정당한 값이 여러 개일 수 있어 아직 자동 실패 규칙으로 승격하지 않았다. DB 탐지
규칙도 현재 평가 기술에 한정된다.
따라서 현 단계의 주장은 “확장 가능한 계약 운반과 최소 불일치 조기 탐지가 작동한다”까지이며,
전체 앱–클라우드 정합성이나 외부 표준 호환성을 주장하지 않는다.

## 1. 문제와 원칙

요구사항, 소스, 빌드, 컨테이너 설정, IaC를 서로 다른 에이전트가 만들기 때문에 각 산출물이
단독으로 유효해도 함께 실행되지 않을 수 있다. P2-Azure에서는 소스가 JPA와 SQLite를
선택했지만 빌드가 그 의존성을 제공하지 않아 클라우드 정적 게이트 뒤 컴파일에 실패했다.

이를 사례별 문자열 분기로 고치지 않고 다음 계약으로 분리한다.

1. `ApplicationRuntimeContract/v1`: 앱이 빌드되고 기능을 수행하기 위해 필요한 것
2. `CloudCapabilityContract/v1`: 배포 환경이 제공해야 하는 벤더 중립 능력
3. `DeploymentBindingContract/v1`: 앱 요구를 컨테이너·VM·CSP 구성에 연결한 결과

혼동을 막기 위해 직렬화된 모든 계약은 다음 출처 정보를 반드시 포함한다.

```yaml
modelProvenance:
  owner: EasyDep
  status: research-proposal
  standardsCompliant: []
  inspiredBy: [OASIS-TOSCA-2.0, OAM, Kubernetes, CUE]
```

클라우드 생성 가능성과 앱 기능 성공은 별도 게이트다. `tofu validate`나 `apply` 성공만으로
기능 성공을 주장하지 않고, 컨테이너 기능 성공만으로 IaC 관계 타당성을 주장하지 않는다.

## 2. 외부 근거와 채택 한계

- OASIS TOSCA 2.0의 노드 requirement, capability, relationship에서 **요구–제공–관계**
  구조를 차용한다. 전체 TOSCA 실행 언어는 구현하지 않는다.
  [TOSCA 2.0 공식 명세](https://docs.oasis-open.org/tosca/TOSCA/v2.0/TOSCA-v2.0.html)
- Open Application Model의 component와 운영 trait 분리는 앱 자체와 배포 환경을 나누는
  근거로 참고한다. 해당 객체를 호환 API로 채택하지는 않는다.
  [OAM 공식 사양 저장소](https://github.com/oam-dev/spec)
- Kubernetes의 Service `targetPort`, 컨테이너 포트, volume/`volumeMount`, readiness
  probe는 포트·경로·상태검사가 교차 산출물 관계임을 보여준다.
  [Service](https://kubernetes.io/docs/concepts/services-networking/service/),
  [Volume](https://kubernetes.io/docs/concepts/storage/volumes/),
  [Probe](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-probes/)
- CUE의 unification처럼 계약 합성은 한쪽 값으로 덮어쓰지 않고 제약의 교집합으로 정의한다.
  v1 구현은 현재 기술 스택에 맞춰 Pydantic과 결정적 검사기로 시작한다.
  [CUE 공식 명세](https://cuelang.org/docs/reference/spec/)

이 모델들은 추상화 원칙의 근거이지 정답 리소스 목록이 아니다. 한 능력이 여러 벤더 리소스로,
여러 능력이 한 리소스로 구현될 수 있어 projection은 `1:1`, `1:N`, `N:1`, `N:M`을 허용한다.

### 2.1 근거 등급

각 필드와 규칙에는 다음 중 하나의 `provenanceClass`를 붙인다.

| 등급 | 의미 | 논문에서 가능한 표현 |
|---|---|---|
| `adopted` | 외부 표준·공식 API에 직접 대응하는 개념 | 공식 모델을 근거로 채택했다 |
| `adapted` | 외부 개념을 Docker-on-VM 연구 범위에 맞게 변형 | 외부 개념에서 변형해 제안했다 |
| `hypothesis` | 직접 대응 근거가 없는 EasyDep 고유 설계 선택 | 실험으로 유효성을 평가한다 |

`adopted`도 해당 외부 표준과의 문법·실행 호환을 뜻하지 않는다. 직접 대응하는 **개념적
근거**가 있다는 뜻만 가진다. `hypothesis` 필드는 근거가 없는 사실처럼 서술하지 않고 절제
또는 오류 주입 실험의 독립변수로 다룬다.

### 2.2 필드 수준 추적표

| EasyDep 계약·필드 | 외부 대응 개념 | 등급 | 채택·변형 이유와 주장 한계 |
|---|---|---|---|
| 세 계약의 분리 자체 | OAM component/workload/trait 분리 | `adapted` | 앱과 운영 관심사 분리에서 착안했지만 EasyDep의 3분할은 자체 제안 |
| `CloudCapabilityContract.capabilities` | TOSCA node requirement/capability | `adapted` | 필요와 제공 가능성을 분리하되 TOSCA 타입·문법과 호환되지 않음 |
| `DeploymentBindingContract.bindings` | TOSCA relationship 및 requirement assignment | `adapted` | 소비–제공 연결 구조만 차용; EasyDep binding 문법은 자체 설계 |
| `projections[].resourceRefs`와 cardinality | TOSCA generic type의 vendor specialization | `adapted` | 벤더 사영 필요성의 근거이며 1:N/N:M 표기는 EasyDep 가설 |
| `listenPorts` ↔ network binding | Kubernetes Service `targetPort` ↔ container port | `adapted` | Docker-on-VM의 방화벽·LB까지 확장한 결합 규칙 |
| `storageConsumers.accessPath` ↔ `mountTarget` | Kubernetes volume ↔ `volumeMount.mountPath` | `adapted` | 명시적 경로 결합을 VM mount에 적용 |
| `healthEndpoint` ↔ cloud health check | Kubernetes readiness/startup probe | `adapted` | 트래픽 투입 전 준비 상태 확인을 CSP LB 상태검사까지 확장 |
| 계약 충돌 시 실패 | CUE constraint unification의 bottom | `adapted` | CUE 문법을 쓰지 않으며 Pydantic·결정적 검사로 별도 구현 |
| `dependencies`, DB driver/URL/dialect 일치 | 직접 채택한 중립 표준 없음 | `hypothesis` | P2 관측에서 도출한 앱 내부 일관성 규칙; 오류 주입 실험 필요 |
| `stateModel`과 다중 active 제약 | 직접 채택한 단일 모델 없음 | `hypothesis` | 상태 공유 없이는 기능 보장이 어렵다는 연구 가설로 검증 |
| `intent.*`와 `observed.*` 분리 | TOSCA requirement/capability, Kubernetes PVC/PV | `adapted` | 요구와 제공 사실의 분리만 차용; 문법·API 호환성 없음 |
| 진단 코드와 부분 재실행 라우팅 | 직접 대응 표준 없음 | `hypothesis` | EasyDep의 핵심 에이전트 복구 기여로 절제실험 대상 |
| 계약 해시 기반 하류 무효화 | 직접 대응 표준 없음 | `hypothesis` | 전체 재실행 대비 비용·성공률 효과를 측정할 구현 가설 |

표에 없는 새 필드는 기본적으로 `hypothesis`다. `adopted`나 `adapted`로 승격하려면 공식
명세의 정확한 절·API와 변형 이유를 evidence registry에 추가해야 한다.

## 3. 세 계약

### 3.1 ApplicationRuntimeContract/v1 — EasyDep 제안

| 영역 | 핵심 필드 | 판정 예 |
|---|---|---|
| 빌드 | `language`, `runtimeVersion`, `buildSystem`, `dependencies` | import가 선언 의존성으로 해석되는가 |
| 실행 | `command`, `listenPorts`, `healthEndpoint` | 프로세스가 선언 포트에서 준비되는가 |
| 기능 | `functionalOracles` | CRUD·오류·업무 규칙이 요구와 맞는가 |
| 상태 | `stateModel`, `storageConsumers` | 상태가 휘발·영속·외부 중 무엇인가 |
| 설정 | `environment`, `secrets` | 필수 값, 형식, 민감도와 기본값이 맞는가 |

`storageConsumers`는 `purpose`, `mode`, `engine`, `accessPath`, `durability`, `sharing`을
가진다. `engine`은 앱 선택이다. SQLite는 P2의 고정 평가 워크로드일 뿐
`persistent-volume`에서 자동 추론하지 않는다. `engine=sqlite`일 때만 드라이버, dialect,
JDBC URL과 파일 경로의 일관성을 검사한다.

### 3.2 CloudCapabilityContract/v1 — EasyDep 제안

기존 `CapabilityContract/v1`의 근거·질문·보류를 보존하고 수락된 결정을 설계 가능한 형태로
정규화한다.

| 영역 | 주요 필드 |
|---|---|
| 계산 | `replicaCount`, `zones`, `cpu`, `memory`, `architecture` |
| 네트워크 | `ingress`, `egress`, `publicReachability`, `loadBalancing` |
| 저장소 | `volumes[].durability`, `sizeGiB`, `attachMode`, `filesystem`, `mountTarget` |
| 보안 | `identity`, `secretDelivery`, `tlsTermination`, `allowedSources` |
| 운영 | `healthCheck`, `failureTolerance`, `observability` |
| 제약 | `provider`, `region`, `budget`, `evidence`, `unresolved` |

각 결정에 `sourceRequirementIds`, `necessity`, `decision`, `confidence`, `evidence`를 둔다.
불명확하거나 충돌하는 필수값은 기본값으로 메우지 않고 `needsQuestion`/`abstained`로 남긴다.

### 3.3 DeploymentBindingContract/v1 — EasyDep 제안

```yaml
schemaVersion: DeploymentBindingContract/v1
modelProvenance:
  owner: EasyDep
  status: research-proposal
bindings:
  - id: app-http
    kind: network
    consumes: app.ports.http
    provides: container.port.http
    projections:
      - provider: azure
        resourceRefs: [app_gateway_backend_setting, nsg_ingress, vm_nic]
        cardinality: 1:N
  - id: notes-data
    kind: storage
    consumes: app.storage.notes.accessPath
    provides: cloud.volumes.notes.mountTarget
    invariant: consumes.path == provides.path
verification:
  static: [dependency, configuration, iacSchema, binding]
  dynamic: [build, containerStartup, functional, restartPersistence]
```

각 binding은 `consumes`, `provides`, `transform`, `invariants`, `projections`, `evidenceRefs`를
가진다. CSP 리소스는 배열과 명시적 간선으로 표현해 다대다 관계를 보존한다. 중립 능력은
벤더별 세부 요소를 대체하지 않고 그 사영을 묶는 비교 단위다.

## 4. 결정적 불일치 규칙과 게이트

LLM은 계약 후보와 수정안을 만들 수 있지만 판정은 코드가 수행한다.

| 코드 | 불일치 | 처리 |
|---|---|---|
| `APP-DEP-001` | 소스 사용 API에 대응하는 빌드 의존성 없음 | 빌드 전 차단 |
| `APP-DB-001` | 드라이버·URL·dialect·engine 불일치 | 앱 계약 실패 |
| `APP-DB-002` | 데이터베이스와 ORM 조합에 필요한 런타임 설정 없음 | 앱 계약 실패 |
| `APP-DB-003` | 설정한 ORM 확장 클래스와 이를 제공하는 모듈이 불일치 | 앱 계약 실패 |
| `BIND-PORT-001` | listen/container/firewall/LB 포트 단절 | binding 실패 |
| `BIND-STORAGE-001` | 앱 데이터 경로가 영속 mount 아래가 아님 | binding 실패 |
| `BIND-HEALTH-001` | health path/port/protocol 불일치 | binding 실패 |
| `BIND-TLS-001` | TLS 종료 위치와 앱 기대가 모순 | binding 실패 |
| `BIND-STATE-HA-001` | 노드 파일 상태와 다중 영역 요구 사이의 공유·복제 전략 미정 | 사용자 질문, 자동 수정 금지 |
| `CLOUD-PROJ-001` | 수락 능력의 CSP 사영이나 필수 관계 누락 | IaC 실패 |

정적 검사는 스키마 → 앱 내부 → 클라우드 사영 → 교차 binding 순서로 수행한다. 통과 후
빌드 → 컨테이너 시작 → HTTP 기능 → 재시작 영속성 동적 검사를 수행한다. 실제 클라우드
실험은 `validate`, `plan`, `apply`, 기능 oracle을 각각 기록하고 종료 시 리소스를 정리한다.

## 5. 부분 복구 계약

실패는 전체 run을 반복하지 않는다. 진단 코드의 소유 작업으로 회송하고 같은 `runId`에서
그 작업과 영향받은 하류 게이트만 재실행한다.

| 진단 | 수정 소유자 | 재검증 시작점 |
|---|---|---|
| 의존성·빌드 | scaffold/build 하위 작업 | 앱 정적 검사 |
| 앱 설정·DB | runtime config 하위 작업 | 앱 정적 검사 |
| API·업무 기능 | implementation logic | 빌드 |
| 데이터 경로·mount | binding/cloud delivery | binding 검사 |
| 포트·방화벽·LB | cloud design/delivery | cloud 사영 검사 |
| 모호성·논리 충돌 | 사용자 질문/보류 | 해당 계약 결정 |

체크포인트에 입력·산출물 해시, 검증기 버전, 진단 코드, 수정 횟수, 하류 의존 목록을 저장한다.
상류 계약 해시가 바뀐 경우에만 영향을 받는 작업을 무효화한다. 수정 전후 결과를 모두 보존한다.

## 6. 구현 및 평가 순서

1. ~~Pydantic 스키마와 기존 산출물용 읽기 어댑터를 둔다.~~ 완료
2. ~~P2 하드코딩 SQLite/영속 볼륨 연동을 계약 생성과 검사로 옮긴다.~~ 최소 경계 완료
3. ~~dependency, DB, port, storage path 네 규칙부터 구현한다.~~ 합성 검사 완료
4. ~~진단별 부분 복구와 하류 무효화를 오케스트레이션에 연결한다.~~ 앱·binding·cloud 진단의
   최소 라우팅 완료
5. 합성 fixture로 규칙의 참·거짓을 검증한 뒤 P1~P3를 실행한다.
6. `full`, `no-depkb`, `no-consistency-validator`에서 조기 탐지율, 오수정률, 기능 성공률,
   복구 범위, 시간·토큰을 비교한다.

구현 전에 위 추적표를 기계 판독 가능한 evidence registry로 옮기고 각 스키마 필드가 근거 ID를
참조하게 한다. 외부 표준 호환성은 현재 연구 주장에 포함하지 않는다. 향후 실제 TOSCA/OAM
문서를 import/export하는 별도 적합성 시험을 통과하기 전에는 `TOSCA-compatible` 또는
`OAM-compatible`이라는 표현을 사용하지 않는다.

가설은 계약이 항상 성공시킨다는 것이 아니다. 무계약 조건보다 불일치를 일찍 탐지하고 더
작은 범위로 복구하며 최종 기능 성공률을 높이는지 평가한다. P1~P3은 종단 과제이고 인과
효과는 단일 불일치 주입 과제와 절제실험으로 별도 판정한다.

### 현재 부분 복구 규칙

testing 단계의 구조화 진단에만 역방향 회송을 적용한다. 구현 단계에서 실패했다면 기존처럼
그 실패 하위 작업을 같은 체크포인트에서 다시 실행한다.

| 진단 | 회송 작업 | 함께 무효화되는 하류 작업 |
|---|---|---|
| `APP-DEP-001`, `APP-DB-001`, `APP-DB-002`, `APP-DB-003` | `implementation.logic` | VM 선택, VM 전달 |
| `BIND-PORT-001`, `BIND-STORAGE-001` | `implementation.vm_delivery` | VM 전달 |
| `BIND-HEALTH-001`, `BIND-TLS-001` | `implementation.vm_delivery` | VM 전달 |
| `CLOUD-PROJ-001` | `implementation.vm_delivery` | VM 전달 |

일반 `APPLICATION_TESTS_FAILED`는 원인이 특정되지 않았으므로 자동으로 상류를 무효화하지 않고
testing만 다시 실행한다. retry history에는 원 진단, 수정 소유 작업, 무효화 목록을 기록한다.

### IaC binding 관측 정책

IaC 검사기는 CSP 리소스 타입이나 평가 사례 ID를 사용하지 않는다. HCL 속성 경로와 실행
명령에서 다음 신호만 관측한다.

- `backend_port`, `target_port`, `container_port`
- backend·target group·health check·probe 문맥의 리터럴 `port`
- `docker run --publish`의 컨테이너 포트
- `mount`, Docker `--mount target=`, volume `-v source:target`의 대상 경로

외부 HTTPS listener의 443처럼 역할이 다른 포트는 앱 포트 후보에서 제외한다. 강한 리터럴
증거가 계약과 충돌하면 `BIND-PORT-001` 또는 `BIND-STORAGE-001`로 실패하고 한 번만 IaC 수정을
허용한다. 변수·template 때문에 값을 확정할 수 없으면 성공으로 가장하지 않고
`BIND-*-UNRESOLVED`로 기록한다. health·TLS·방화벽은 기존 종단 의미 평가에서 관측하되 이
런타임 binding 게이트에서는 자동 실패시키지 않는다. 충분한 provider-neutral 판정 근거가
생기기 전에는 실패 규칙을 추가하지 않는다.
## 사용자 결정으로 상위 요구를 수정하는 경로

앱–클라우드 불일치가 구현만으로 안전하게 해소되지 않을 때 사용자는 원 요구를 유지하고 상태
외부화를 요청하거나, 활성 요구사항 자체를 수정할 수 있다. 후자를 선택하면 구현 에이전트가
`multiZone=false` 같은 값을 몰래 덮어쓰지 않는다. 사용자가 전체 수정 요구를 제출해야 하며,
시스템은 원본과 수정본을 함께 기록한 뒤 같은 run에서 requirements 이후 단계만 다시 실행한다.

이 경로는 특정 데이터베이스나 HA 사례를 위한 자동 패치가 아니다. 어느 상위 요구 변경에도
적용할 수 있는 명시적 계약 교체 경계이며, 요구가 바뀌지 않은 일반 구현 실패에는 사용하지 않는다.
