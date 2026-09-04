# 클래스 설계 서비스

이 패키지는 유스케이스 명세에서 BCE 클래스 구조뿐 아니라 실행 가능한 메서드 계약,
호출 트리와 parameter provenance까지 확정한다. 최종 결과인 `BCEModel`은 클래스
PlantUML, 시퀀스 생성, API와 ERD가 함께 사용하는 상호작용 기준 모델이다.

## 한눈에 보는 실행 흐름

```text
raw use-case JSON
  → ScenarioIndex
  → AcceptedInventory
  → use-case별 CombinedUnitProposal
  → AcceptedFragment를 합친 BCEModel 골격
  → use-case별 Collaboration
  → 최종 BCEModel
  → PlantUML 및 SequenceCollection
```

각 화살표는 수락 경계다. LLM의 구조화 응답을 그대로 다음 단계로 넘기지 않고 정규화와
같은 입력에 같은 결과를 내는 코드 검사를 통과시킨다. 최초 생성은 한 유스케이스의
메서드와 호출 계획을 한 응답으로 받되, 둘을 각각 정규화하고 검사한다. 호출 계획만 잘못된
경우에는 수락된 메서드를 유지한 채 호출 계획만 다시 만든다. 이미 수락된 다른 유스케이스도
다시 호출하지 않는다. 같은 오류와 같은 호출 계획이 반복되면 해당 유스케이스의 메서드와
호출 계획을 `CombinedUnitProposal`로 다시 받고, 먼저 수락한 다른 유스케이스의 협업은 새
메서드 골격에서도 유효한지 코드로 다시 확인해 재사용한다.

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
      "collaborationId": "UC1",
      "useCaseIds": ["UC1"],
      "calls": [
        {
          "callId": "UC1::call:1",
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

### 3. 최초 결합 생성

`generation.py`는 한 유스케이스에 필요한 메서드와 짧은 호출 트리를 한 번에 생성한다.
입력 payload는 `operations.py`가 만들며 다음 선택 공간으로 제한된다.

```json
{
  "useCase": {"id": "UC1", "name": "Place order"},
  "executionSlice": {
    "id": "UC1",
    "steps": [{"stepRef": "UC1:main:1", "subject": "Buyer", "sentence": "submits"}]
  },
  "allowedStepRefs": ["UC1:main:1"],
  "fixedClasses": [{"className": "OrderBoundary", "stereotype": "Boundary"}],
  "fixedDataTypes": [],
  "reservedOperations": [],
  "reservedDataTypes": []
}
```

응답 `CombinedUnitProposal`은 두 부분만 가진다.

```json
{
  "fragment": {
    "DataTypes": [],
    "Classes": [
      {
        "className": "OrderBoundary",
        "operations": [
          {
            "name": "submit",
            "parameters": [],
            "returnType": "void",
            "stepRefs": ["UC1:main:1"]
          }
        ]
      }
    ]
  },
  "calls": [
    {"operationRef": "OrderBoundary.submit", "parentCallIndex": null}
  ]
}
```

`fragment`는 기존 `OperationFragment`와 같다. `calls`에는 아직 만들어지지 않은 긴 저장 ID
대신 `ClassName.methodName`과 앞선 부모 위치만 담는다. 코드는 actor entry 소유권, 타입
도달성과 canonical operation ID를 투영한 뒤 두 부분을 기존 검사로 각각 확인한다. 정규화로
메서드가 제거되면 그 호출도 제거하고 자식을 가장 가까운 남은 부모에 다시 연결한다.
존재하지 않는 짧은 참조는 임의로 보정하지 않고 호출 계획 수리로 넘긴다.

유스케이스 제안은 최대 두 개씩 병렬로 받는다. 이후 입력 순서대로 메서드를 합치며, 이미
합친 메서드나 타입과 이름이 충돌한 유스케이스만 새 예약 목록을 보고 다시 제안한다. 모든
메서드를 합친 골격이 완성된 뒤에 호출 계획을 구체화한다.

### 4. 호출 구체화와 값 binding

최초 호출 계획은 결합 응답에 들어 있다. `generation.py`가 짧은 참조를 수락된 실제
operation ID로 바꾸고, `collaboration.py`가 저장용 호출과 parameter 값을 구체화한다.
짧은 참조, 부모 관계 또는 값 출처에 문제가 있으면 operation은 그대로 두고 기존
`CallPlanProposal` 수리만 실행한다. `resume`과 `revise`도 이 분리된 수리 경로를 사용한다.

호출 계획 입력 예:

```json
{
  "collaborationId": "UC1",
  "actorEntries": [
    {"actorStepRef": "UC1:main:1", "requiredStepRefs": ["UC1:main:1", "UC1:main:2"]}
  ],
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

분리 수리 응답은 다음처럼 operation과 앞선 부모의 1-based index만 가진다.

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
- `UC1::call:1#request`: 상위 호출이 받은 parameter
- `UC1::call:2#result`: 앞선 호출의 반환값
- `derived#OrderRequest(total=UC1:main:1#total)`: 필드별 원천에서 구성한 DTO
- `runtime#currentDateTime`: 명시적으로 지원하는 런타임 시계

후보가 하나면 코드가 선택한다. 두 개 이상일 때만 `InteractionBindingSelection`이 각
parameter의 실제 후보 enum에서 하나를 선택한다. 후보가 없으면 LLM에게 자유 문자열을
요청하지 않고 해당 collaboration을 실패시킨다.

### 5. 서비스 조율

`service.py`만 설정, 병렬 실행, 진행 이벤트와 단계 간 repair handoff를 조율한다.

- `generate`: 인벤토리를 만든 뒤 유스케이스마다 operation과 호출 계획을 한 응답으로 생성한다.
- `resume`: 저장된 협업을 검사하고 누락되었거나 유효하지 않은 유스케이스만 다시 만든다.
- `revise`: 피드백을 inventory, operation, collaboration 중 가장 작은 소유자에 적용한다.

하나의 유스케이스 안에서 사용자가 시스템을 다시 호출하면 새 생성 단위로 나누지 않는다.
같은 `Collaboration` 안에 부모가 없는 새 root를 추가하며, 뒤 root는 앞 root의 호출 결과를
parameter 값으로 사용할 수 있다.

## LLM 호출 계약

| operation | 호출 조건 | 구조화 출력 | 검사와 후속 동작 |
|---|---|---|---|
| `InteractionInventory` | 새 전역 구조가 필요할 때 | `InventoryProposal` | inventory 검사 후 수락 |
| `InteractionInventoryRepair` | inventory finding 발생 | `InventoryProposal` | 누적 이력과 함께 전체 inventory를 교체 후 재검사 |
| `InteractionCombinedUnit` | 최초 유스케이스 생성 | `CombinedUnitProposal` | operation과 호출 계획을 각각 정규화·검사 |
| `InteractionCombinedUnitRepair` | operation까지 바꿔야 하는 결합 단위 오류 | `CombinedUnitProposal` | 현재 유스케이스만 전체 교체 |
| `InteractionOperations` | 피드백으로 operation만 다시 생성 | `OperationFragment` | 현재 유스케이스 조각만 검사 |
| `InteractionOperationsRepair` | 조각 finding 발생 | `OperationFragment` | 누적 이력과 함께 현재 조각을 교체 |
| `InteractionOperationCollisionRepair` | 같은 이름의 수락 연산·타입과 정의 충돌 | `OperationFragment` | 충돌한 현재 조각만 다시 검사 |
| `InteractionOperationFeedback` | operation 소유 피드백 | `OperationFragment` | 선택 use case 조각만 교체 |
| `InteractionCallPlan` | 유스케이스 호출 트리 생성 | 유한 `CallPlanProposal` | materialize 후 collaboration 검사 |
| `InteractionCallPlanRepair` | 계획·materialize 실패 | 유한 `CallPlanProposal` | 누적 이력과 함께 같은 유스케이스만 교체 |
| `InteractionBindingSelection` | parameter 출처 후보가 복수 | 동적 유한 선택 schema | 모든 선택을 materialize에 병합 |
| `InteractionFeedbackScope` | targets와 이름으로 소유자를 확정 못함 | `FeedbackScope` | 허용 candidate ID인지 재검사 |
| `InteractionInventoryFeedback` | inventory 소유 피드백 | `InventoryProposal` | 지정되지 않은 item을 원본과 병합 후 검사 |

정확한 prompt 문구는 `inventory.py`, `operations.py`, `generation.py`,
`collaboration.py`의 상수와 `feedback.py`의 호출부다. 이 README의 예제는 shape를
설명하기 위한 축약본이며 prompt를 복제하지 않는다. collision·feedback 전용 제안에도
validation finding이 남으면
`_checked_fragment`가 같은 이름에 `Repair` 접미사를 붙여 누적 이력 기반 전체 교체를 한다.

## 검증과 repair 종료 조건

| 수정 대상 | 주요 검사 | 숫자 상한 | 종료 조건 |
|---|---|---:|---|
| Inventory | 이름·타입·관계·유스케이스 범위 | 없음 | 수락할 때까지 전체 inventory 교체 |
| Operation fragment | 참조·단계 커버리지·값 흐름 | 없음 | 수락할 때까지 같은 유스케이스 조각 교체 |
| Collaboration | 호출 계약·순서·binding provenance | 없음 | 수락 또는 같은 유스케이스의 Combined 재생성 |
| Final model | schema·canonical ID·협업 커버리지 | 해당 없음 | revise에서는 finding 반환 |

이름 충돌은 다른 유스케이스의 수락 결과를 버리지 않고 충돌한 유스케이스만 다시 제안한다.
호출 계획 오류도 승인된 operation을 보존하고 이전 후보와 정확한 finding을 함께 전달한다.
동일 finding과 후보가 반복될 때에는 사용자 실패로 끝내지 않고 그 유스케이스의 Combined
제안으로 수리 범위를 넓힌다. LLM provider 오류와 구조화 응답 schema 오류는 설계 finding이
아니므로 수리 이력에 넣지 않고 호출자에게 그대로 전달한다.

검증 구현과 rule별 의미는 [validation README](validation/README.md)를 따른다.

## 피드백 범위

피드백 대상 ID가 명시되면 다음 순서로 범위를 좁힌다.

1. 전역 클래스·구조 타입이면 `inventory`
2. 지역 DTO 이름이면 그 DTO를 소유한 use case의 `operation`
3. 유스케이스 ID는 operation과 collaboration이 함께 사용하므로 피드백 문장까지 보고 고른다.

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
| `generation.py` | 최초 operation·호출 계획 결합 생성과 수락 순서 조율 |
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
  canonical cache key다. 최초 결합 단위는 수락된 operation fragment와 완성된
  collaboration을 함께 저장한다.
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
다시 실행할 때 accepted inventory/combined-unit hit이면 binding 선택을 포함한 외부 LLM
physical request는 발생하지 않는다. 누락된 collaboration을 resume할 때는 분리 call-plan
cache를 사용한다. revise는 소유 operation 또는
collaboration slice와 feedback을 key에 포함해 영향받은 단위만 교체한다.

### Reasoning과 timing

inventory, operation, call-plan은 각각 `design_class_inventory_reasoning_effort`,
`design_class_operation_reasoning_effort`, `design_class_call_plan_reasoning_effort`를
읽는다. 값이 없는 구버전 설정은 기존 `design_reasoning_effort`로 fallback한다. 이 설정은
수리 종료 조건, handoff 범위와 병렬도 정책을 바꾸지 않는다.
수강신청 UC2·UC3·UC10 실험에서 operation은 `low`로도 모두 수락됐으므로 기본값이
`low`다. `design_class_compact_operation_payload`도 기본으로 켜서 같은 main/extension
본문을 `useCase`와 `executionSlice`에 두 번 싣지 않는다. 4K 출력 상한 실험도
`finish_reason=stop`으로 끝났으며 schema repair가 없었다.

모든 호출은 `capture_llm_timings()`의 invocation별 ContextVar 수집기를 사용한다. worker는
`bind_context`로 collector를 전달하고, concurrent event는 유실 없이 합쳐진다. cache event는
`observationScope=logicalOnly`, `physicalRequest=False`와 `cacheStatus/cacheKey`를 갖는다.
따라서 provider `llm_calls`에는 logical cache event를 세지 않으며, request event에는
input/output digest·token, repair attempt, handoff owner와 execution-slice metadata를
남긴다. `DesignAdapter.start` 후 `resume`은 세션에 누적된 목록이 아니라 해당 invocation의
event만 반환한다.
