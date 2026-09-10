# API 명세와 실행 바인딩 생성 개선안

## 1. 결정

API 생성 작업은 다음 세 산출물을 만든다.

```text
reports/design/api-spec-model.json
reports/design/api-binding-model.json
reports/design/openapi.json
```

- `ApiSpecModel`: 공개 HTTP 계약과 opaque trace reference
- `ApiBindingModel`: EasyDep 내부의 Boundary·Control·outcome 실행 연결
- `openapi.json`: `ApiSpecModel`에서 만든 표준 OpenAPI 3.1 문서

세 파일은 API 생성 작업 안에서 함께 생성·검증·수리하고 하나의 checkpoint로 원자적으로
공개한다. 하나라도 오류가 있거나 만들어지지 않으면 셋 다 공개하지 않는다. 구현·Testing·배포
단계에 API 의미 검사나 수리 책임을 넘기지 않는다.

클래스·메서드·파라미터·outcome 이름의 뜻으로 역할을 추론하지 않는다. 문자열은 canonical
reference와 선언 식별자를 정확히 찾을 때만 사용한다.

## 2. 계층 경계

기준 호출 흐름은 다음과 같다.

```text
HTTP operation
  → Boundary operation
  → 명시적인 collaboration call
  → Control operation
  → ApplicationOutcome
  → HTTP response
```

생성된 Spring Controller가 최적화로 Control을 직접 호출할 수는 있다. 그러나 이는
`Boundary → Control` collaboration을 결정론적으로 축약한 코드여야 한다. API operation을 이름이
비슷한 Control에 바로 연결해서는 안 된다.

기존 `control_binding`은 다음 책임으로 분리한다.

- `endpoint_binding`: API operation과 Boundary operation 및 HTTP 입력 연결
- `application_invocation`: Boundary operation과 Control operation 호출 연결
- `outcome_mappings`: ApplicationOutcome과 HTTP response 연결

이 세 책임은 공개 `ApiSpecModel`이 아니라 내부 `ApiBindingModel`이 소유한다.

## 3. 생성 트랜잭션과 검증 경계

```text
검증 완료된 유스케이스 + 검증 완료된 BCE/Collaboration
  → 유한 interaction/input/outcome 후보 구성
  → HTTP proposal 생성
  → ApiSpecModel 정규화
  → ApiBindingModel 투영
  → OpenAPI 투영
  → 세 산출물 교차 일관성 검사
  → 수정 가능한 오류면 같은 작업 안에서 제한 수리
  → 오류 0건일 때만 세 산출물 원자적 공개
```

수리 한도를 소진하면 API 생성 작업 자체가 실패한다. 오류가 남은 모델, 부분 OpenAPI,
`designGaps`를 저장하거나 다음 단계에 넘기지 않는다.

상위 typed 계약이 빠진 문제는 API LLM이 수리할 수 없다. 이 경우 API 작업은
`UPSTREAM_CONTRACT_INCOMPLETE`로 실패하며 API 산출물은 없는 상태를 유지한다. workflow가 상위
산출물 생성 작업을 다시 열 수는 있지만, 오류가 든 API 산출물을 먼저 공개한 뒤 사후 수리하지
않는다.

API 생성 성공 기록에는 세 파일의 hash, 입력 snapshot hash, schema version과 내부 검사
`findingCount=0`을 checkpoint metadata로 남긴다. 이 기록은 사후 검증을 다시 실행하기 위한
산출물이 아니라 생성 작업 완료의 불변 증거다.

하류 단계는 다음만 확인한다.

- checkpoint가 완료 상태인지
- 세 파일과 hash가 checkpoint에 대응하는지
- 지원하는 schema version인지

하류는 API의 의미 규칙을 다시 실행하거나 repair feedback을 만들지 않는다.

## 4. 상위 BCE 계약에 추가할 `ApplicationOutcome`

API 단계는 애플리케이션 결과나 예외를 발명하지 않는다. 클래스·상호작용 생성 작업이 Control
operation마다 stable outcome을 오류 없이 제공해야 한다.

```python
class ApplicationOutcome:
    outcome_ref: str
    category: Literal["success", "failure"]
    signal: ReturnSignal | ReturnVariantSignal | DeclaredFailureSignal
    payload_type: str | None
    step_refs: list[str]
```

signal은 자유 형식 코드나 자연어 조건이 아니다.

- `return`: 정상 반환 자체가 유일한 application outcome인 경우
- `return_variant`: 선언된 반환 타입의 field path와 enum/상수 reference로 구분하는 결과
- `declared_failure`: operation 계약에 명시된 정말 예외적인 실패 타입

예상 가능한 업무 실패는 가급적 Java 예외가 아니라 `return_variant`인 typed result로 표현한다.
예를 들어 생성과 갱신이 모두 가능하면 다음처럼 결과를 구분할 수 있어야 한다.

```text
ManageTermResult
  kind: ManageTermOutcome = CREATED | UPDATED | INVALID
  term: Optional<AcademicTerm>
```

201과 200을 함께 선언하면서 Control이 구분자 없는 `AcademicTerm` 하나만 반환하는 계약은 API
단계에 도달하기 전에 상위 산출물 생성 작업에서 실패해야 한다.

요청 schema 제약과 security policy는 BCE outcome이 아니므로 API binding 후보로 별도 제공할 수
있다.

- `request_constraint_failure`: 존재하는 schema constraint reference
- `policy_rejection`: 존재하는 security policy reference

유스케이스 extension의 자연어 outcome은 trace 근거일 뿐 실행 selector가 아니다.

## 5. API LLM에 제공할 유한 후보

현재 interaction ID만 주는 입력을 다음처럼 확장한다.

```json
{
  "interactionId": "interaction:manage-term",
  "boundaryOperationRef": "operation:admin-boundary-manage-term",
  "inputCandidates": [
    {
      "inputRef": "input:admin-manage-term:request",
      "type": "ManageTermRequest",
      "required": true
    }
  ],
  "outcomeCandidates": [
    {"outcomeRef": "outcome:term-created", "category": "success"},
    {"outcomeRef": "outcome:term-updated", "category": "success"},
    {"outcomeRef": "outcome:invalid-term", "category": "failure"}
  ]
}
```

Control operation과 내부 signal은 후보 reference의 원본 계약에 있지만 API LLM 입력에는 노출할
필요가 없다.

## 6. API LLM이 추가로 생성할 값

`ApiEndpointProposal`은 기존 path, method, summary에 입력과 response 연결을 추가한다.

```python
class ApiInputBindingProposal:
    input_ref: str
    target: PathTarget | QueryTarget | BodyTarget | BodyFieldTarget


class ApiResponseProposal:
    outcome_ref: str
    status: int
    description: str
```

입력 target은 discriminated union으로 제한한다.

```json
{"kind": "path_parameter", "name": "termId"}
{"kind": "query_parameter", "name": "page"}
{"kind": "body"}
{"kind": "body_field", "name": "name"}
```

LLM의 판단 범위는 다음뿐이다.

- resource path와 HTTP method
- 제공된 input reference의 HTTP 위치와 wire name
- 제공된 outcome reference의 HTTP status와 설명
- 공개 security requirement의 선택

LLM은 operation ID, Java 이름, DTO·Control 타입, Control 인자, provenance, 반환 selector, 내부
예외 타입이나 response body source를 생성하지 않는다.

proposal의 `input_ref`와 `outcome_ref`는 유한 후보의 exact ID다. 최종 `ApiSpecModel`에는 이 내부
reference를 넣지 않고 opaque HTTP-side reference로 바꾼다. 원래 reference와의 연결은
`ApiBindingModel`에 둔다.

## 7. 최종 `ApiSpecModel`

목표 schema version은 `easydep-api-spec/v2alpha1`이다. 이 모델에는 공개 HTTP 계약만 둔다.

```json
{
  "schema_version": "easydep-api-spec/v2alpha1",
  "title": "Academic Administration API",
  "version": "1.0.0",
  "Endpoints": [
    {
      "operation_ref": "api-operation:manage-term",
      "operation_id": "manageTerm",
      "method": "post",
      "path": "/admin/terms",
      "summary": "Create or update an academic term",
      "inputs": [
        {
          "http_input_ref": "api-input:manage-term:body",
          "target": {"kind": "body"},
          "type": "ManageTermRequest",
          "required": true
        }
      ],
      "request_schema": "ManageTermRequest",
      "responses": [
        {
          "response_ref": "api-response:manage-term:201",
          "status": 201,
          "description": "Academic term created",
          "schema_name": "AcademicTerm",
          "is_array": false
        },
        {
          "response_ref": "api-response:manage-term:200",
          "status": 200,
          "description": "Registration period updated",
          "schema_name": "AcademicTerm",
          "is_array": false
        },
        {
          "response_ref": "api-response:manage-term:400",
          "status": 400,
          "description": "Invalid term details",
          "schema_name": "ProblemDetail",
          "is_array": false
        }
      ],
      "security": [{"scheme_ref": "security:admin-session", "scopes": []}],
      "use_case_ids": ["UC10"],
      "scenario_step_refs": ["UC10:main:2", "UC10:extension:1a:1a2"]
    }
  ],
  "Schemas": []
}
```

`operation_ref`, `http_input_ref`, `response_ref`는 외부 문서와 내부 binding을 연결하는 opaque ID다.
`ApiSpecModel`에는 다음을 넣지 않는다.

- Boundary·Control 클래스나 operation reference
- collaboration call과 파라미터 provenance
- application outcome reference
- 반환 variant selector와 field path
- 내부 failure/exception 타입과 policy 구현

## 8. 최종 `ApiBindingModel`

목표 schema version은 `easydep-api-binding/v1alpha1`이다.

```json
{
  "schema_version": "easydep-api-binding/v1alpha1",
  "bindings": [
    {
      "api_operation_ref": "api-operation:manage-term",
      "endpoint_binding": {
        "boundary_operation_ref": "operation:admin-boundary-manage-term",
        "inputs": [
          {
            "boundary_input_ref": "input:admin-manage-term:request",
            "http_input_ref": "api-input:manage-term:body"
          }
        ]
      },
      "application_invocation": {
        "collaboration_ref": "collaboration:admin-manage-term",
        "control_operation_ref": "operation:term-control-manage-term",
        "arguments": [
          {
            "control_parameter_ref": "parameter:term-control-manage-term:command",
            "provenance_ref": "call:admin-to-term-control#request",
            "source": {
              "http_input_ref": "api-input:manage-term:body",
              "field_path": []
            }
          }
        ]
      },
      "outcome_mappings": [
        {
          "application_outcome_ref": "outcome:term-created",
          "response_ref": "api-response:manage-term:201",
          "signal": {
            "kind": "return_variant",
            "field_path": ["kind"],
            "equals_ref": "enum-value:ManageTermOutcome:CREATED"
          },
          "body_source": {"kind": "return_field", "field_path": ["term"]}
        },
        {
          "application_outcome_ref": "outcome:term-updated",
          "response_ref": "api-response:manage-term:200",
          "signal": {
            "kind": "return_variant",
            "field_path": ["kind"],
            "equals_ref": "enum-value:ManageTermOutcome:UPDATED"
          },
          "body_source": {"kind": "return_field", "field_path": ["term"]}
        },
        {
          "application_outcome_ref": "outcome:invalid-term",
          "response_ref": "api-response:manage-term:400",
          "signal": {
            "kind": "return_variant",
            "field_path": ["kind"],
            "equals_ref": "enum-value:ManageTermOutcome:INVALID"
          },
          "body_source": {"kind": "problem_detail", "mapping_ref": "mapping:invalid-term"}
        }
      ]
    }
  ]
}
```

`signal`은 BCE `ApplicationOutcome`을 내부 실행기가 바로 소비할 수 있게 투영한 snapshot이다.
`application_outcome_ref`가 source of truth이며 API 생성 작업 내부 검사는 두 값의 일치를 보장한다.
예상 가능한 400 응답도 위 예시처럼 typed result variant를 우선한다. 정말 예외적인 흐름만
`declared_failure` signal을 사용한다.

## 9. 최종 OpenAPI 3.1

OpenAPI에는 표준 path, parameter, requestBody, response, schema와 security만 투영한다. 내부
Control·selector·예외 정보는 넣지 않는다.

EasyDep 연결에는 opaque reference 하나만 사용한다.

```json
{
  "paths": {
    "/admin/terms": {
      "post": {
        "operationId": "manageTerm",
        "x-easydep-operation-ref": "api-operation:manage-term",
        "requestBody": {},
        "responses": {
          "200": {},
          "201": {},
          "400": {}
        }
      }
    }
  }
}
```

기존 `x-easydep-control`은 v2 소비자가 `ApiBindingModel`로 전환되는 동안 읽기 호환용으로만
유지한다. 새 selector나 내부 타입을 그 extension에 추가하지 않고 전환 완료 후 제거한다.

## 10. API 생성 작업 내부 불변조건

세 산출물을 공개하려면 같은 API 생성 작업 안에서 다음 조건이 모두 통과해야 한다.

1. proposal의 interaction/input/outcome reference가 제공된 유한 후보에 정확히 존재한다.
2. 모든 공개 API operation은 정확히 하나의 Boundary operation에 연결된다.
3. 모든 path placeholder와 path input target이 이름을 포함해 일대일로 대응한다.
4. 외부에서 받는 필수 Boundary input은 정확히 하나의 HTTP input을 가진다.
5. 모든 Control argument는 collaboration provenance를 통해 HTTP input과 field path에 연결된다.
6. 같은 이름이나 호환 타입을 argument 연결 fallback으로 사용하지 않는다.
7. `ApiSpecModel`의 모든 response는 정확히 하나의 `outcome_mapping`을 가진다.
8. 정상 outcome이 하나일 때만 무조건 `return`을 허용한다. 복수 정상 outcome은 서로 배타적인
   typed selector를 가져야 한다.
9. expected business failure는 typed result variant를 우선하고, 선언되지 않은 Java 예외를
   생성하지 않는다.
10. response body source 타입과 공개 response schema/array 여부가 구조적으로 호환된다.
11. 204 response에는 body mapping이 없고, 같은 status나 trigger가 중복되지 않는다.
12. OpenAPI projection의 `$ref`, operationId, method/path와 security reference가 모두 유효하다.
13. `ApiSpecModel`, `ApiBindingModel`, OpenAPI의 opaque operation/input/response reference가 정확히
   대응한다.

Pydantic은 각 모델의 구조를 검사한다. 위 교차 산출물 의미 검사는 API 생성 서비스 내부에서만
실행한다.

## 11. 내부 제한 수리

수리 입력에는 전체 저장소나 이전 단계 원문을 넣지 않는다. 실패한 endpoint, rule ID, 허용된
interaction/input/outcome 후보와 현재 HTTP proposal만 전달한다.

API LLM이 수정할 수 있는 값은 다음뿐이다.

- path와 method
- input target과 wire name
- outcome별 HTTP status와 description
- 공개 security requirement

누락된 ApplicationOutcome, collaboration provenance, 반환 variant와 선언 failure는 API LLM 수리
대상이 아니다. 이 경우 즉시 `UPSTREAM_CONTRACT_INCOMPLETE`로 종료한다.

## 12. 하류 소비 계약

- `java_scaffold.py`: OpenAPI extension이 아니라 `ApiBindingModel`의 Boundary→Control과 outcome
  mapping을 소비한다.
- Testing 생성기: HTTP 요청·응답은 OpenAPI, 실행 outcome과 trace는 `ApiBindingModel`을 소비한다.
- 구현 planner: Control argument, selector, body source를 `ApiBindingModel`에서 직접 읽는다.
- RTM: opaque API operation/response reference와 source outcome reference를 연결한다.
- `app/design/validation.py`: 저장된 API 의미 검사를 재실행하지 않고 API checkpoint 완료 상태와
  bundle hash만 읽는다.

## 13. 구현 순서

1. BCE `ClassOperation`에 stable `ApplicationOutcome`과 typed signal을 추가한다.
2. interaction candidate에 exact input/outcome reference를 추가한다.
3. `ApiEndpointProposal`에 input binding과 response별 `outcome_ref`를 추가한다.
4. 공개 `ApiSpecModel` v2와 내부 `ApiBindingModel` v1을 구현한다.
5. API service가 두 모델과 OpenAPI를 생성하고 내부 검증·제한 수리를 수행하도록 바꾼다.
6. 세 산출물을 임시 checkpoint에 쓴 뒤 오류 0건일 때 원자적으로 공개한다.
7. Java scaffold, Testing, planner와 RTM을 `ApiBindingModel` 소비로 전환한다.
8. graph의 API 사후 semantic check·revision과 readiness 재검사를 제거한다.
9. 기존 `control_binding.outcomes`와 `x-easydep-control` 호환 경로를 제거한다.

생성 작업의 고정 사례는 이름을 바꾼 동일 계약, 동일 타입 입력 두 개의 역순 연결, 단일 204,
selector가 있는 복수 2xx, typed failure 4xx, 누락 reference로 인한 원자적 생성 실패를 포함한다.
이는 별도 사후 검증기가 아니라 API 산출물 생성 작업 자체의 단위·통합 검사다.
