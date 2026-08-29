# 클래스 설계 서비스

이 패키지는 유스케이스 명세에서 BCE 클래스 구조뿐 아니라 실행 가능한 메서드 계약,
호출 트리와 parameter provenance까지 확정한다. 최종 결과인 `BCEModel`은 클래스
PlantUML, 시퀀스 생성, API와 ERD가 함께 사용하는 상호작용 기준 모델이다.

## 한눈에 보는 실행 흐름

```text
raw use-case JSON
  → ScenarioIndex
  → AcceptedInventory
  → use-case / execution-slice별 AcceptedFragment
  → 연산이 합쳐진 BCEModel skeleton
  → execution group별 CollaborationResult
  → 최종 BCEModel
  → PlantUML 및 SequenceCollection
```

각 화살표는 수락 경계다. LLM의 구조화 응답을 그대로 다음 단계로 넘기지 않고 정규화와
같은 입력에 같은 결과를 내는 코드 검사를 통과시킨다. 실패하면 finding이 가리키는 가장 작은 수정 대상만 교체하며,
숫자 상한 없이 수락될 때까지 누적 수리 이력을 다음 요청에 전달한다. 이미 거절된 후보나
동일한 실패 상태가 반복되면 `STALLED`로 끝내고, 이미 수락된 형제 단위는 유지한다.

## 공개 입력과 출력

```python
generate_class_model(
    index: ScenarioIndex, *, cache: AcceptedUnitCache | None = None,
) -> BCEModel
resume_class_model(
    index: ScenarioIndex, current: BCEModel, *, cache: AcceptedUnitCache | None = None,
) -> BCEModel
revise_class_model(
    current: BCEModel,
    index: ScenarioIndex,
    feedback: str,
    targets: AbstractSet[str],
    *,
    cache: AcceptedUnitCache | None = None,
) -> BCEModel
```

- `ScenarioIndex`는 유스케이스, 단계, include/extend 관계와 실행 그룹을 한 번 정규화한
  불변 입력이다.
- `BCEModel`은 `Classes`, `DataTypes`, `Relationships`, `Collaborations`를 저장한다.
- graph adapter가 입력 JSON을 `ScenarioIndex`/`BCEModel`로 검증하고, 결과에
  `model_dump(by_alias=True)`를 적용해 기존 체크포인트 shape를 유지한다.

축약된 저장 결과는 다음과 같다.

```json
{
  "Classes": [
    {
      "className": "OrderBoundary",
      "stereotype": "Boundary",
      "operations": [
        {
          "operationId": "OrderBoundary::submit(request:OrderRequest)",
          "name": "submit",
          "parameters": [{"name": "request", "type": "OrderRequest"}],
          "returnType": "OrderResult",
          "stepRefs": ["UC1:main:1"]
        }
      ]
    }
  ],
  "DataTypes": [],
  "Relationships": [],
  "Collaborations": [
    {
      "collaborationId": "UC1:main:1",
      "useCaseIds": ["UC1"],
      "calls": [
        {
          "callId": "UC1:main:1::call:1",
          "parentCallId": null,
          "receiverOperationId": "OrderBoundary::submit(request:OrderRequest)",
          "stepRefs": ["UC1:main:1"],
          "argumentBindings": [
            {"parameter": "request", "sourceRef": "UC1:main:1#request"}
          ]
        }
      ]
    }
  ]
}
```

## 단계별 동작

### 1. 시나리오 인덱스

`scenario.py`는 원시 명세의 단계 번호를 안정적인 ID로 바꾸고 실행 슬라이스를 계산한다.

- 주 흐름: `UC1:main:1`
- 확장 흐름: `UC1:extension:4a:4a1`
- 액터가 새 요청을 시작하는 지점은 별도 execution group의 entry가 된다.
- include 대상 단계는 호출한 그룹의 추적 범위에 포함된다.
- extend는 나중에 시퀀스 `opt` fragment로 투영할 anchor를 보존한다.

중복 유스케이스 명세는 이 경계에서 실패시킨다. 유일한 이름/ID로 해소할 수 없는 관계는
실행 관계에 포함하지 않는다. 하위 단계가 원시 JSON을 다시 해석하지 않게 하는 것이
목적이다.

### 2. 전역 인벤토리

`inventory.py`는 프로젝트 전체의 Boundary·Control·Entity, 지속 필드, 구조 타입과 정적
관계를 한 번 결정한다. LLM에는 전체 원문 대신 역할·목표·단계·관계를 압축한 payload를
보낸다.

입력 예:

```json
{
  "useCases": [
    {
      "id": "UC1",
      "name": "Place order",
      "primaryActor": "Buyer",
      "steps": [
        {"stepRef": "UC1:main:1", "subject": "Buyer", "sentence": "submits an order"}
      ]
    }
  ],
  "relationships": []
}
```

응답은 `InventoryProposal`이다. 각 item은 유한한 `kind`, 필드, 식별자, 값과
`useCaseIds`를 가지며, 구조 관계도 schema로 제한된다. 코드는 이를 Java 필드 표기와 저장
alias로 정규화해 `AcceptedInventory`로 수락한다.

### 3. 연산 조각

`operations.py`는 한 유스케이스 또는 실행 슬라이스에 필요한 메서드와 지역 DTO만 생성한다.
LLM에 보내는 payload는 다음 선택 공간으로 제한된다.

```json
{
  "useCase": {"id": "UC1", "name": "Place order"},
  "executionSlice": {
    "id": "UC1:main:1",
    "steps": [{"stepRef": "UC1:main:1", "subject": "Buyer", "sentence": "submits"}]
  },
  "allowedStepRefs": ["UC1:main:1"],
  "fixedClasses": [{"className": "OrderBoundary", "stereotype": "Boundary"}],
  "fixedDataTypes": [],
  "reservedOperations": [],
  "reservedDataTypes": []
}
```

응답 `OperationFragment`는 클래스별 `OperationProposal`과 지역 `DataTypes`만 포함한다.
코드는 actor entry 소유권, 타입 도달성, canonical operation ID를 투영한 뒤 검사한다.
동일 이름·동일 정의는 병합하지만 같은 이름의 다른 시그니처는 현재 조각만 다시 생성한다.

연산 조각은 최대 두 개씩 wave로 실행한다. 완료된 wave의 연산과 타입 이름을 다음 wave의
예약 목록에 넣으므로 병렬 작업이 서로 다른 정의를 같은 이름으로 수락하지 않는다.

### 4. 호출 계획과 값 binding

`collaboration.py`는 수락된 연산 중 실제 실행 그룹이 호출할 메서드와 부모 호출만 LLM에
고르게 한다. 동적 Pydantic schema의 `receiverOperationId`가 현재 허용된 operation ID의
유한 enum이므로 새 클래스나 메서드를 발명할 수 없다.

호출 계획 입력 예:

```json
{
  "collaborationId": "UC1:main:1",
  "entryActor": "Buyer",
  "requiredStepRefs": ["UC1:main:1", "UC1:main:2"],
  "receiverOperations": [
    {
      "operationId": "OrderBoundary::submit(request:OrderRequest)",
      "className": "OrderBoundary",
      "stereotype": "boundary",
      "stepRefs": ["UC1:main:1"]
    }
  ]
}
```

응답은 다음처럼 operation과 앞선 부모의 1-based index만 가진다.

```json
{
  "calls": [
    {"receiverOperationId": "OrderBoundary::submit(request:OrderRequest)"},
    {"receiverOperationId": "OrderControl::place(request:OrderRequest)", "parentCallIndex": 1}
  ]
}
```

`callId`, `parentCallId`, `stepRefs`는 코드가 투영한다. 각 parameter의 값 출처 후보도 타입과
인과 순서로 계산한다.

- `UC1:main:1#request`: 액터 입력 또는 선행조건의 이름 있는 값
- `UC1:main:1::call:1#request`: 상위 호출이 받은 parameter
- `UC1:main:1::call:2#result`: 앞선 호출의 반환값
- `derived#OrderRequest(total=UC1:main:1#total)`: 필드별 원천에서 구성한 DTO
- `runtime#currentDateTime`: 명시적으로 지원하는 런타임 시계

후보가 하나면 코드가 선택한다. 두 개 이상일 때만 `InteractionBindingSelection`이 각
parameter의 실제 후보 enum에서 하나를 선택한다. 후보가 없으면 LLM에게 자유 문자열을
요청하지 않고 해당 collaboration을 실패시킨다.

### 5. 서비스 조율

`service.py`만 설정, 병렬 실행, 진행 이벤트와 단계 간 repair handoff를 조율한다.

- `generate`: 인벤토리와 연산 skeleton을 만든 뒤 모든 execution group의 협업을 생성한다.
- `resume`: 저장된 협업을 검사하고 누락되었거나 유효하지 않은 그룹만 다시 만든다.
- `revise`: 피드백을 inventory, operation, collaboration 중 가장 작은 소유자에 적용한다.

협업이 필요한 메서드를 찾지 못하면 해당 그룹이 추적하는 유스케이스의 연산 조각만 보완한
뒤 영향을 받은 그룹만 다시 계획한다. handoff도 누적 실패 이력을 operation prompt에 전달해
진전하는 동안 반복하며, 정체되어도 성공한 형제 협업을 버리지 않는다.

## LLM 호출 계약

| operation | 호출 조건 | 구조화 출력 | 검사와 후속 동작 |
|---|---|---|---|
| `InteractionInventory` | 새 전역 구조가 필요할 때 | `InventoryProposal` | inventory 검사 후 수락 |
| `InteractionInventoryRepair` | inventory finding 발생 | `InventoryProposal` | 누적 이력과 함께 전체 inventory를 교체 후 재검사 |
| `InteractionOperations` | 새 연산 조각 생성 | `OperationFragment` | 현재 조각만 검사 |
| `InteractionOperationsRepair` | 조각 finding 발생 | `OperationFragment` | 누적 이력과 함께 현재 조각을 교체 |
| `InteractionOperationCollisionRepair` | 같은 이름의 수락 연산·타입과 정의 충돌 | `OperationFragment` | 충돌한 현재 조각만 다시 검사 |
| `InteractionOperationHandoff` | 협업이 필요한 연산을 찾지 못함 | `OperationFragment` | 실패 그룹 소유 조각만 보완 |
| `InteractionOperationFeedback` | operation 소유 피드백 | `OperationFragment` | 선택 use case 조각만 교체 |
| `InteractionCallPlan` | execution group의 호출 트리 생성 | 유한 `CallPlanProposal` | materialize 후 collaboration 검사 |
| `InteractionCallPlanRepair` | 계획·materialize 실패 | 유한 `CallPlanProposal` | 누적 이력과 함께 같은 그룹만 교체 |
| `InteractionBindingSelection` | parameter 출처 후보가 복수 | 동적 유한 선택 schema | 모든 선택을 materialize에 병합 |
| `InteractionFeedbackScope` | targets와 이름으로 소유자를 확정 못함 | `FeedbackScope` | 허용 candidate ID인지 재검사 |
| `InteractionInventoryFeedback` | inventory 소유 피드백 | `InventoryProposal` | 지정되지 않은 item을 원본과 병합 후 검사 |

정확한 prompt 문구는 `inventory.py`, `operations.py`, `collaboration.py`의 상수와
`feedback.py`의 호출부다. 이 README의 예제는 shape를 설명하기 위한 축약본이며 prompt를
복제하지 않는다. collision·handoff·feedback 전용 제안에도 validation finding이 남으면
`_checked_fragment`가 같은 이름에 `Repair` 접미사를 붙여 누적 이력 기반 전체 교체를 한다.
예를 들어 `InteractionOperationHandoffRepair`는 handoff 제안의 재검사 실패에서만 호출된다.

## 검증과 repair 종료 조건

| 수정 대상 | 주요 검사 | 숫자 상한 | 종료 조건 |
|---|---|---:|---|
| Inventory | 이름·타입·관계·유스케이스 범위 | 없음 | 수락 또는 거절 후보 반복 |
| Operation fragment | 참조·단계 커버리지·실행 그룹·값 흐름 | 없음 | 수락 또는 거절 후보 반복 |
| Collaboration | 호출 계약·순서·binding provenance | 없음 | 수락 또는 거절 call-plan 반복 |
| Operation handoff | 실패 그룹이 요구한 연산 보완 | 없음 | 수락 또는 skeleton+실패 상태 반복 |
| Final model | schema·canonical ID·협업 커버리지 | 해당 없음 | revise에서는 finding 반환 |

commit 시 발견되는 이름 충돌과 collaboration 실패 뒤의 handoff는 앞선 proposal 검사의 반복이
아니라 새로운 소유 사건이다. 둘 다 현재 fragment만 대상으로 하며, 각 사건의 제안은 검사
finding과 이전 거절 후보 digest를 누적해 materially different replacement를 요청한다.

검증 구현과 rule별 의미는 [validation README](validation/README.md)를 따른다.

## 피드백 범위

피드백 대상 ID가 명시되면 다음 순서로 결정론적으로 분류한다.

1. execution group ID면 `collaboration`
2. use-case ID면 `operation`
3. 전역 클래스·구조 타입이면 `inventory`
4. 지역 DTO 이름이면 그 DTO를 소유한 use case의 `operation`

명시적 ID가 없고 지역 타입 이름도 찾지 못할 때만 LLM이 제한된 후보에서 scope를 고른다.
선택하지 않은 inventory item, 연산 조각과 collaboration은 그대로 유지한다.

## 모듈 지도

| 모듈 | 역할 |
|---|---|
| `scenario.py` | 원시 입력 정규화와 실행 그룹 계산 |
| `proposals.py` | 저장되지 않는 LLM 구조화 응답 schema |
| `models.py` | 단계 사이의 frozen 수락 단위 |
| `inventory.py` | 전역 구조 제안·정규화 |
| `operations.py` | 연산 조각 생성·충돌 처리·조립 |
| `collaboration.py` | 호출 트리·parameter provenance |
| `feedback.py` | 피드백 소유자 판정과 국소 교체 |
| `service.py` | generate/resume/revise 오케스트레이션 |
| `projections.py` | 호출 의존선 등 화면용 결정론 투영 |
| `plantuml.py` | 저장 BCE 모델의 클래스 PlantUML 표현 |
| `validation/` | 순수 규칙과 typed 검증 보고서 |

## 부작용과 실패 조건

- LLM 호출과 진행 이벤트 발행은 stage 모듈과 service에서만 발생한다.
- validation, projection, PlantUML 생성은 저장소나 graph state를 직접 읽지 않는다.
- Pydantic schema 위반, 목록 밖 ID, provenance 후보 부재, 순서·타입 위반은 명시적으로
  실패하며 임의 문자열 보정이나 빈 placeholder를 만들지 않는다.
- 기본 긴 호출 병렬도는 2이며 설정을 통해서만 조정한다.

## 최적화 cache와 관측 계약

`cache.py`의 `AcceptedUnitCache`는 이미 Pydantic·결정론 검사를 통과한 단위만 재사용하는
프로세스 경계다. 기본 구현 `ProcessLocalAcceptedUnitCache`는 최대 256개를 보관하는
thread-safe LRU다. 서비스에서 `cache=`를 생략하면 기존처럼 cache를 우회하며,
graph adapter가 application-scope 인스턴스를 명시적으로 주입한다.

- 입력: 정규화 slice, 고정 inventory, feedback/findings, prompt/schema digest, endpoint를
  포함한 provider identity와 model, seed/temperature/reasoning 설정, completion cap을 포함한
  canonical cache key다. collaboration은 call-plan뿐 아니라 동적 binding selector의
  prompt/schema-template/low-reasoning/effective-cap도 key에 포함한다.
- 출력: `CacheResult(value, status, key)`이며 status는 `hit`, `miss`, `coalesced` 중 하나다.
- 부작용: 같은 key의 동시 계산은 single-flight로 합치고, 성공한 accepted value만 깊은
  복사해 LRU에 저장한다. 용량을 넘으면 가장 오래 사용하지 않은 완료 항목을 제거한다.
- 금지: raw LLM response, validation finding, partial repair 후보, credential 또는 prompt의
  비공개 추론을 저장하지 않는다. cache hit도 typed schema와 결정론 검사를 다시 수행한다.
- 실패: producer 예외는 저장하지 않고 모든 대기 호출에 전달한다. hit 재검증 실패는
  `cached ... is invalid` 오류로 반환하며 오래된 값을 조용히 채택하지 않는다.
- warm 검증 전에 process-local cache를 seal한다. seal 이후 miss는 producer를 실행하지
  않고 실패하므로, warm 확인 자체가 새 provider 요청을 만들 수 없다.

`generate`, `resume`, `revise` 모두 같은 cache 경계를 받는다. 같은 입력으로 generate를
다시 실행하거나 누락된 collaboration을 resume할 때 accepted inventory/operation/call-plan
hit이면 외부 LLM physical request는 발생하지 않는다. revise는 소유 operation 또는
collaboration slice와 feedback을 key에 포함해 영향받은 단위만 교체한다.

### Reasoning과 timing

inventory, operation, call-plan은 각각 `design_class_inventory_reasoning_effort`,
`design_class_operation_reasoning_effort`, `design_class_call_plan_reasoning_effort`를
읽는다. 값이 없는 구버전 설정은 기존 `design_reasoning_effort`로 fallback한다. 이 설정은
수리 종료 조건, handoff 범위와 병렬도 정책을 바꾸지 않는다.
`design_class_compact_operation_payload`의 운영 기본값은 평가 채택 전까지 `false`이며,
live protocol의 compact/candidate cell에서만 명시적으로 활성화한다.

모든 호출은 `capture_llm_timings()`의 invocation별 ContextVar 수집기를 사용한다. worker는
`bind_context`로 collector를 전달하고, concurrent event는 유실 없이 합쳐진다. cache event는
`observationScope=logicalOnly`, `physicalRequest=False`와 `cacheStatus/cacheKey`를 갖는다.
따라서 provider `llm_calls`에는 logical cache event를 세지 않으며, request event에는
input/output digest·token, repair attempt, handoff owner와 execution-slice metadata를
남긴다. `DesignAdapter.start` 후 `resume`은 세션에 누적된 목록이 아니라 해당 invocation의
event만 반환한다.
