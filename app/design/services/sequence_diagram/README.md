# 시퀀스 다이어그램 투영과 검증

시퀀스 단계는 별도의 LLM 생성기가 아니다. 클래스 단계에서 수락된 `BCEModel`의
`Collaborations`를 결정론적으로 `SequenceCollection`으로 투영한다. 따라서 클래스와
시퀀스가 서로 다른 메서드나 호출 순서를 주장할 수 없다.

## 입력과 출력

```python
project_sequence_model(
    index: ScenarioIndex,
    class_model: BCEModel,
    class_puml: str = "",
) -> SequenceCollection
```

입력 책임은 다음과 같다.

- `ScenarioIndex`: 유스케이스 순서, 단계와 include/extend 관계
- `BCEModel`: participant가 될 BCE 클래스, operation, collaboration과 binding
- `class_puml`: 이 투영이 어느 클래스 표현 버전에서 만들어졌는지 계산할 문자열

축약 출력 예:

```json
{
  "Diagrams": [
    {
      "use_case_id": "UC1",
      "use_case_name": "Place order",
      "Participants": [
        {"name": "Buyer", "alias": "Buyer", "kind": "actor", "source_class": ""},
        {
          "name": "OrderBoundary",
          "alias": "OrderBoundary",
          "kind": "boundary",
          "source_class": "OrderBoundary"
        }
      ],
      "Messages": [
        {
          "source": "Buyer",
          "target": "OrderBoundary",
          "label": "submit(request:OrderRequest)",
          "type": "sync",
          "call_id": "UC1:main:1::call:1",
          "reply_to": "",
          "arguments": [
            {
              "parameter": "request",
              "type": "OrderRequest",
              "source_kind": "input",
              "source_ref": "UC1:main:1#request"
            }
          ]
        },
        {
          "source": "OrderBoundary",
          "target": "Buyer",
          "label": "OrderResult",
          "type": "return",
          "call_id": "",
          "reply_to": "UC1:main:1::call:1",
          "arguments": []
        }
      ],
      "UnresolvedSteps": [],
      "NarrativeSteps": []
    }
  ],
  "class_diagram_hash": "sha256-of-class-puml",
  "MethodProposals": []
}
```

`MethodProposals`는 기존 외부 shape를 유지하지만 현재 projection은 새 메서드를 제안하지
않으므로 항상 비어 있다.

## 투영 순서

1. 저장된 operation ID로 receiver 클래스, stereotype, parameter와 반환 타입을 찾는다.
2. 각 collaboration을 유스케이스·단계·collaboration ID 순서로 안정적으로 정렬한다.
3. actor entry와 BCE receiver를 participant로 등록한다.
4. 부모 호출 관계를 따라 sync/self call을 depth-first로 펼친다.
5. 한 call의 자식 호출을 모두 투영한 뒤 정확히 한 return message를 만든다.
6. 같은 유스케이스의 여러 collaboration을 participant와 message 순서를 보존해 합친다.
7. include/extend 관계를 반영하고 입력 유스케이스 순서대로 `Diagrams`를 만든다.
8. 클래스 PlantUML의 SHA-256을 `class_diagram_hash`에 기록한다.

동일한 세 입력에는 message와 participant 순서, fragment ID와 hash까지 같은 결과가 나온다.
projection은 설정, 저장소, graph state 또는 LLM client를 읽지 않는다.

## 호출과 반환

각 `CollaborationCall`은 하나의 sync 또는 self message가 된다. receiver operation의
`returnType`으로 대응 return label을 만들고 원래 `callId`를 `reply_to`로 연결한다.

```text
call:   OrderBoundary -> OrderControl : place(request:OrderRequest)
return: OrderControl --> OrderBoundary : OrderResult
```

부모가 없는 첫 호출은 actor에서 Boundary로 들어간다. 부모가 있는 호출은 부모 operation의
소유 클래스에서 receiver 클래스로 향한다. 저장된 operation이나 부모 call을 찾지 못하면
표시를 추측하지 않고 projection을 실패시킨다.

## include와 extend

- include 대상 collaboration은 호출한 유스케이스의 추적 범위에 이미 포함되어 있다.
  별도 diagram이 필요한 include 유스케이스는 같은 수락 협업을 해당 유스케이스 범위로
  좁혀 투영한다.
- extend 유스케이스의 message는 base 유스케이스의 anchor step 직후에 삽입한다.
  모든 삽입 message 앞에 동일한 `opt` fragment와 확장 조건을 붙인다.
- anchor를 찾지 못하면 임의 위치에 끼워 넣지 않고 결정된 fallback 순서를 사용하며,
  validation이 참조·커버리지 문제를 별도로 보고한다.

## 값 출처 표현

`SequenceArgument`는 클래스 collaboration의 binding을 표시 가능한 종류로 분류한다.

| `source_kind` | 예 | 의미 |
|---|---|---|
| `input` | `UC1:main:1#request` | 액터 입력 단계의 값 |
| `precondition` | `UC1:precondition:1#account` | 선행조건에서 보장된 값 |
| `call_parameter` | `call:1#request` | 상위 호출이 받은 parameter |
| `call_result` | `call:2#result` | 앞선 호출의 반환값 |
| `state` | `runtime#currentInstant` | 명시적으로 지원하는 런타임 또는 상태 원천 |
| `literal` | `literal#approved` | 호환 저장본의 이미 승인된 literal 원천 |

표현은 읽기용 분류이며 provenance 원문인 `source_ref`를 변경하지 않는다. 현재 클래스
materialize는 자유 literal을 발명하지 않으므로 새 projection에서 `literal`은 생성되지 않는다.

## 검증

`validate_sequence_model`은 저장 shape에 따라 두 lane을 사용한다.

- 현재 `Diagrams` 컬렉션은 projection 계약을 검사한다.
  - 모든 call에 정확히 한 matching return이 있는가
  - 현재 클래스 PlantUML hash와 같은가
  - 입력 유스케이스가 정확히 한 diagram으로 커버되는가
  - 입력에 없는 유스케이스를 참조하지 않는가
- 이전 단일 diagram shape는 기존 rule catalog 순서의 detector 집합으로 검사한다.
  participant, BCE 방향, method signature, 인과 순서, 값 흐름, 단계 커버리지와 fragment
  조건 등을 그대로 검증해 이전 체크포인트 표시를 유지한다.

검사는 `ValidationReport`를 반환하며 모델을 수정하지 않는다. renderer 앞의 가벼운
`sequence_findings`도 Pydantic schema, participant 참조와 call/return 쌍만 확인하고 repair를
시작하지 않는다.

## 피드백 처리

시퀀스는 독립적인 의미 원본이 아니므로 message JSON을 직접 수정하지 않는다.

```text
sequence feedback
  → 대상 execution group/use case 판정
  → revise_class_model로 BCE operation 또는 collaboration 수정
  → 클래스 PlantUML 재생성·검증
  → project_sequence_model 재실행
```

따라서 피드백 때문에 새 LLM 호출이 필요하다면 클래스의 inventory/operation/collaboration
operation 이름으로 기록된다. `SequenceRepair` 같은 별도 자유형 호출은 없다.

## 모듈 지도

| 모듈 | 역할 |
|---|---|
| `projection.py` | typed 모델, collaboration 투영, include/extend 병합, 계약 finding |
| `validation.py` | 현재 collection과 이전 diagram의 결정론적 규칙 |
| `plantuml.py` | `SequenceCollection`을 PlantUML 문자열로 표현 |
| `methods.py` | method label·parameter·return 표기의 공통 파서 |
| `__init__.py` | projection 공개 API만 노출 |

클래스 생성과 repair의 상세는 [클래스 설계 README](../class_diagram/README.md), 공통 validation
계약은 [클래스 validation README](../class_diagram/validation/README.md)를 참고한다.
