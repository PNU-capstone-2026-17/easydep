# Testing 에이전트

Testing은 Implementation이 남긴 고정 `TestingInput`으로 앱을 한 번 복원한 뒤, 정적 gate와
작은 기능 계획을 실행한다. 단위 테스트와 frontend build는 여기서 다시 만들거나 반복하지 않는다.

## 실행 흐름

```text
고정된 TestingInput
  → 앱과 deployment 산출물을 한 번 복원
  → deployment·IaC 정적 gate
  → requirement → use-case → OpenAPI trace 확인
  → use-case마다 작은 FunctionalTestCase 계획 생성
  → operationId를 고정 path/method로 해석해 HTTP 실행
  → 실행된 case만 requirement coverage에 기록
```

계획에는 `case_id`, `requirement_ids`, `use_case_id`, 순서 있는 `steps`만 들어간다. 각
step은 `step_id`와 `operation_id`를 가지며, 경로·HTTP method·인증·요청 예시는 계획에 넣지
않는다. 계획 schema는 모르는 필드와 중복 step ID를 거부한다.

executor는 고정 OpenAPI의 operationId를 정확히 하나의 path와 method로 바꾼다. 요청 schema로
필수 입력을 만들고, 타입이 맞는 이전 response field가 하나뿐일 때만 다음 요청에 전달한다.
나머지는 명세에서 만든 안정적인 예시값으로 채우며 성공 response도 OpenAPI schema로 확인한다.

각 case는 한 번만 순서대로 실행한다. 한 case가 실패하면 그 case의 plan·steps·finding을
결과에 보존하고 다음 case를 다시 실행하지 않는다. 명세가 모호하거나 operationId를 찾을 수
없으면 제품 실패가 아닌 `UPSTREAM_AMBIGUITY`와 `INCONCLUSIVE`로 기록한다.

## 결과와 실패

`gateStatus`는 `PASS`, `FAIL`, `INCONCLUSIVE`, `NOT_APPLICABLE` 중 하나다. 실제로 PASS한
case의 requirement ID만 coverage에 기록한다. 실패 결과에는 plan·steps·finding과 수리
경로를 남긴다.

- `TEST_DEFECT`: Testing에서 계획을 다시 만든다.
- `SUT_DEFECT`: 같은 계획을 Implementation 수리에 전달한다.
- `ENVIRONMENT_DEFECT`: 환경을 복구한 뒤 같은 검사를 다시 실행한다.
- `UPSTREAM_AMBIGUITY`: 요구사항 또는 설계 단계에서 명세를 보완한다.

앱 실행·Docker·필수 산출물 문제로 판정할 수 없으면 성공으로 표시하지 않는다. Testing은
`tofu plan -refresh=false`만 사용하며 실제 `apply`는 하지 않는다.
