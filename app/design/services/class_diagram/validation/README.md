# 클래스 설계 검증

이 디렉터리는 클래스 설계의 순수 검증 규칙을 소유한다. 검사는 입력을 수정하지 않고,
저장소·graph state를 직접 읽지 않으며, LLM이나 repair를 호출하지 않는다. 서비스가 typed
`ValidationReport`를 보고 어떤 부분을 다시 생성할지 결정한다.

## 공통 실행 계약

```python
CheckSpec(rule_id, pure_check_function)
run_checks(checks, artifact, context) -> ValidationReport
```

`run_checks`는 다음을 보장한다.

1. 검사는 등록 순서대로 관찰된다.
2. 한 `CheckSpec`이 자신의 `rule_id`가 아닌 finding을 반환하면 검사 오류로 기록한다.
3. 동일 finding은 안정적인 key로 한 번만 남긴다.
4. 예외가 난 규칙은 형제 규칙을 숨기지 않고 `errors`에 기록한다.
5. 검증은 기본적으로 순차 실행한다. 현재 규칙은 모델 순회 비용이 작아 별도 thread를 쓰지 않는다.

## 보고서 형태

```json
{
  "status": "findings",
  "findings": [
    {
      "rule_id": "class.collaboration.bindings",
      "message": "argument source is not available before the call",
      "location": "UC1:main:1::call:2#request",
      "requires_user_input": false,
      "origin": "deterministic"
    }
  ],
  "checked_rule_ids": ["class.collaboration.contract", "class.collaboration.order"],
  "errors": []
}
```

상태는 finding이 없으면 `clean`, 모두 사용자 결정이 필요하면 `needs_input`, 자동으로 고칠
수 있는 finding이 하나라도 있으면 `findings`, 검사기 자체가 실패하면 `error`다.

`origin`은 근거의 층을 구분한다.

- `schema`: Pydantic 저장 계약 위반
- `deterministic`: 참조, 타입, 순서처럼 코드로 완전히 판정 가능한 위반
- `semantic`: 규칙 카탈로그가 판단하는 설계 의미 위반

## 검증 lane

| 모듈 | 입력과 context | 확인하는 것 | 소비자 |
|---|---|---|---|
| `inventory.py` | inventory JSON + `ScenarioIndex` | 이름, 타입, 관계, 범위 | inventory 생성·피드백 |
| `operations.py` | fragment JSON + `OperationContext` | 타입·단계 참조, 커버리지, 결과와 값 흐름 | operation 생성·handoff |
| `collaboration.py` | collaboration JSON + `CollaborationContext` | 호출 계약, 인과 순서, parameter binding | 협업 생성·resume |
| `model.py` | `BCEModel` + `ScenarioIndex` | schema, canonical ID, 협업 커버리지와 최종 계약 | service와 graph check |
| `diagram.py` | 저장 JSON + 설계 state | BCE 관계와 전체 다이어그램 의미 규칙 | design readiness |

`model.py`와 `diagram.py`는 목적이 다르다. 전자는 생성 서비스가 소유한 상호작용 계약과
국소 repair 범위를 지킨다. 후자는 저장 산출물을 구현 단계로 넘겨도 되는지 더 넓은 의미
규칙을 검사한다. readiness finding이 생겼다는 이유로 생성 서비스의 repair 범위를 임의로
확대하지 않는다.

## Inventory 규칙

- 이름은 비어 있지 않고 class·DataType 전체에서 유일해야 한다.
- BCE stereotype과 DataType kind는 허용된 값이어야 한다.
- 필드 타입의 참조 대상이 inventory에 존재해야 한다.
- 관계 양 끝, 관계 종류와 다중성이 유효해야 한다.
- 클래스와 구조 타입의 `useCaseIds`는 입력 시나리오 범위 안에 있어야 한다.
- 모든 유스케이스가 필요한 BCE 구조에 연결되어야 한다.

Inventory finding은 `location: message` 문자열로 바뀌어 최초 candidate와 함께
`InteractionInventoryRepair`에 전달된다. LLM은 finding 일부만 반환하는 것이 아니라 전체
replacement inventory를 반환하고, 같은 규칙을 다시 통과해야 한다.

## Operation 규칙

`OperationContext`는 검사 대상 조각이 사용할 수 있는 inventory, use case, 허용 step ID와
execution group을 고정한다.

- 연산 parameter·return type과 지역 DataType 참조가 존재해야 한다.
- operation의 `stepRefs`가 허용된 슬라이스 밖을 가리키면 안 된다.
- actor entry는 Boundary가 소유하고 Control·Entity에 중복되면 안 된다.
- 필요한 주·확장 흐름 단계가 연산에 의해 커버되어야 한다.
- 반환 타입과 지역 DTO는 실제 시그니처에서 도달 가능해야 한다.
- 하류 parameter를 만들 값이 상류 입력·호출 결과·런타임 원천에 존재해야 한다.

예를 들어 Control 메서드가 `OrderRequest`를 받지만 앞선 Boundary 입력과 반환값 어디에도
필수 필드 `total`이 없다면 `class.operation.value-flow` finding이 된다. 자유로운 기본값을
발명하지 않는다.

Finding이 있으면 현재 fragment, finding 목록과 누적 거절 이력을 `previousFragment`와 함께
전달해 교체한다. 숫자 상한은 두지 않고 동일 후보가 반복될 때 정체로 종료한다.
다른 use case의 수락된 fragment는 prompt에도 replacement 대상에도 포함하지 않는다.

## Collaboration 규칙

`CollaborationContext`는 전체 operation catalog와 정확히 한 execution group을 제공한다.

- `collaborationId`와 canonical `callId`가 그룹·호출 위치와 일치해야 한다.
- 첫 호출은 부모가 없어야 하고, 이후 호출의 부모는 반드시 앞선 호출이어야 한다.
- Boundary → Entity 직접 호출 등 금지된 BCE 방향을 허용하지 않는다.
- 그룹의 필수 단계가 정확한 receiver operation으로 커버되어야 한다.
- 각 parameter에 정확히 하나의 타입 호환 source가 있어야 한다.
- `call_result`는 현재 호출보다 앞서고 인과 경로상 사용할 수 있어야 한다.
- `derived#Type(...)`은 대상 타입의 필수 필드를 모두 채워야 한다.

정상 예:

```text
UC1:main:1::call:1#request
UC1:main:1::call:2#result
derived#OrderRequest(total=UC1:main:1#total)
runtime#currentInstant
```

실패 예:

```text
UC1:main:1::call:3#result   # 현재 call보다 뒤의 반환값
literal#unknown            # 허용 후보에 없고 타입 근거도 없음
derived#OrderRequest()     # 필수 필드 원천이 없음
```

호출 계획이나 materialize가 실패하면 예외 메시지와 이전 계획을 같은 execution group의
`InteractionCallPlanRepair`에 전달한다. 두 번째 실패는 `CollaborationResult.issue`로
반환되며 validation이 추가 LLM 호출을 시작하지 않는다.

## 최종 모델과 readiness

`validate_class_model`은 저장 schema를 먼저 확인한다. schema가 깨진 JSON이면 다른 규칙이
잘못된 shape를 순회하지 않고 `class.model.schema`만 보고한다. 유효한 모델은 canonical
operation ID, 구체적인 operation 이름, execution group과 collaboration의 정확한 커버리지,
호출 계약을 검사한다.

`class_diagram_validation_report`는 관계 endpoint, BCE 통신, stereotype, 필드 타입,
유스케이스 참조와 ERD로 넘길 의미 조건까지 검사한다. `app/design/validation.py`의 design
readiness가 이 보고서를 소비하고 transport-safe finding record로 바꾼다.

## 규칙을 추가할 때

1. 규칙 소유 모듈에 입력을 변경하지 않는 순수 함수를 만든다.
2. 함수가 반환하는 모든 finding에 등록할 하나의 `rule_id`만 사용한다.
3. `CheckSpec`을 값싼 것부터 의미 의존 순서로 등록한다.
4. 정상, 단일 위반, 형제 규칙과 함께 실행되는 경우를 테스트한다.
5. 자동 repair가 필요하면 검사기가 아니라 해당 stage service에서 finding을 수정 대상으로
   변환한다.
6. 이 README의 해당 lane과 실패 예제를 갱신한다.
