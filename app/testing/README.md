# Testing 에이전트

Testing은 Implementation이 남긴 고정 `TestingInput`으로 앱을 한 번 복원한 뒤, 작은 기능 계획과
정적 gate를 실행한다. 단위 테스트와 frontend build는 여기서 다시 만들거나 반복하지 않는다.

## 실행 흐름

```text
고정된 TestingInput
  → 앱과 deployment 산출물을 한 번 복원
  → requirement → use-case → OpenAPI trace 확인
  → use-case마다 작은 FunctionalTestCase 계획 생성
  → operationId를 고정 path/method로 해석해 HTTP 실행
  → 동적 검사가 통과하면 공용 toolchain에서 deployment·IaC 정적 gate
  → contract 실행 범위와 의미적으로 검증된 requirement를 분리해 기록
```

계획에는 `case_id`, `requirement_ids`, `use_case_id`, 순서 있는 `steps`만 들어간다. 각
step은 `step_id`와 `operation_id`를 가지며, 경로·HTTP method·인증·요청 예시는 계획에 넣지
않는다. 계획 schema는 모르는 필드와 중복 step/operation ID를 거부한다. 여러 operation이
필요한 경우에는 설계의 canonical `stepRefs`가 OpenAPI
`x-easydep-scenario-step-refs`로 전달됐을 때만 필수 집합과 순서를 확정한다. 직접 근거가 없으면
LLM이 정한 배열 순서를 사실로 취급하지 않고 실행 전 `UPSTREAM_AMBIGUITY`로 남긴다.

executor는 고정 OpenAPI의 operationId를 정확히 하나의 path와 method로 바꾼다. 요청 schema로
필수 입력을 만들고, 타입이 맞는 이전 response field가 하나뿐일 때만 다음 요청에 전달한다.
나머지는 명세에서 만든 안정적인 예시값으로 채우며 성공 response도 OpenAPI schema로 확인한다.
OpenAPI가 본문 없는 성공 응답을 선언한 경우에는 `204` 같은 빈 응답도 정상 처리한다.

입력값은 OpenAPI의 `const`, `enum`, `example`, `default`, `format`, 숫자 범위와 이전 응답값을
먼저 사용한다. 이 정보만으로 정상 흐름의 값을 정할 수 없는 leaf만 LLM에 묻는다. LLM은 요청
본문 전체가 아니라 `operationId`, 입력 위치, 해당 leaf schema와 짧은 operation 설명만 받고 값
하나만 반환한다. 반환값은 같은 OpenAPI schema로 검사한 뒤 사용한다. 사용한 값은 기능 계획과
함께 저장하므로 구현 수리 전후에 테스트 입력이 바뀌지 않는다.

각 case는 한 번만 순서대로 실행한다. 첫 blocking case에서 중단하고 아직 실행하지 않은 case를
`pendingCaseIds`로 남긴다. 정적·IaC gate도 `DEFERRED/NOT_APPLICABLE`로 기록해 현재 동적 실패를
가리지 않는다. 수리 뒤에는 같은 계획과 입력으로 실패 case를 먼저 실행하고, 통과한 뒤 남은
case와 정적 gate를 진행한다. 명세가 모호하거나 operationId를 찾을 수 없으면 제품 실패가 아닌
`UPSTREAM_AMBIGUITY`와 `INCONCLUSIVE`로 기록한다.

LLM 또는 OpenAPI가 만든 입력이 포함됐다는 이유만으로 모든 `4xx` 응답을 테스트 문제로
돌리지는 않는다. 예를 들어 요청 본문을 보낸 `POST`가 `400`을 반환하면 구현 실패로 남긴다.
선행 생성 단계 없이 임의로 만든 path 식별자로 기존 리소스를 `GET`했고 `404`가 난 경우처럼,
정상 fixture가 없다는 근거가 분명할 때만 테스트 입력 문제로 분류한다.

## 결과와 실패

`gateStatus`는 `PASS`, `FAIL`, `INCONCLUSIVE`, `NOT_APPLICABLE` 중 하나다. `2xx + response
schema`는 HTTP contract 실행 성공이며 계산 결과나 상태 변화의 의미적 정답은 아니다. 현재 고정
산출물에는 실행 가능한 acceptance oracle이 없으므로 `requirements.ids`는 비우고, 실행에 성공한
연결 범위는 `contractIds`, 의미 검증이 남은 기능 요구사항은 `unverifiedIds`로 기록한다. 204 뒤
관찰 endpoint가 없는 상태 변화도 의미적으로 PASS 처리하지 않는다.

동적 실패 finding에는 크기를 제한한 실제 테스트 요청과 응답, test profile/database,
가능한 경우 Testing이 소유한 application log excerpt를 담는다. 외부 `target_url`에는 임의의
container log를 연결하지 않는다.

- `TEST_DEFECT`: Testing에서 계획을 다시 만든다.
- `SUT_DEFECT`: 같은 계획을 Implementation 수리에 전달한다.
- `ENVIRONMENT_DEFECT`: 환경을 복구한 뒤 같은 검사를 다시 실행한다.
- `UPSTREAM_AMBIGUITY`: 요구사항 또는 설계 단계에서 명세를 보완한다.

앱 실행·Docker·필수 산출물 문제로 판정할 수 없으면 성공으로 표시하지 않는다. Testing은
`tofu plan -refresh=false`만 사용하며 실제 `apply`는 하지 않는다.

## 자동 수리와 선택 재검사

최초 실행은 동적 case를 먼저 실행하고 첫 차단 실패에서 수리를 시작한다. 실패에는 요약 문장만
남기지 않고 rule ID, 대상 파일, 실행 명령, 종료 코드와 HTTP request/response 및 runtime 근거를
기록한다. 수리 에이전트는 그 파일만 수정하고 처음 실패한 같은 검사를 `run_task_check`로 다시
실행한다.

수리할 때마다 새 Workspace command를 만들지 않는다. 한 command 안에 실패, 수정, 재검사 event를
이어 붙인다. 수리 횟수에는 숫자 상한이 없지만 파일 내용과 실패 결과가 모두 같다면 새 LLM 작업을
시작하지 않고 EasyDep의 검사 도구나 담당 연결 문제로 종료한다. 파일 내용은 달라졌지만 실패가
줄지 않은 후보는 폐기하고 직전 수용본에서 다른 방법을 시도한다. 이전 작업과 내용이 같아 공유한
버전은 지우지 않는다.

수리 뒤에는 변경 범위와 연결된 gate만 다시 실행한다.

- Terraform 변경: Trivy와 OpenTofu
- cloud-init, Compose, 배포 script 변경: 해당 package 검사
- 같은 구현에서 Testing 계획/입력 수리: 이미 PASS한 case를 보존하고 실패 case부터 재개
- backend 구현 변경: source가 바뀌었으므로 과거 PASS case도 다시 실행
- 바뀌지 않은 gate: 입력과 관련 파일 digest가 같을 때 이전 결과 재사용

Trivy, OpenTofu, cloud-init, Compose, Bash와 PowerShell 검사는 host에 우연히 설치된 프로그램이
아니라 `easydep-toolchain`에서 실행한다. 도구를 시작할 수 없는 오류는 앱 파일 결함과 구분한다.
공개 Load Balancer나 직접 공개 IP처럼 선택한 ResourcePlan에 꼭 필요한 경고는 rule 전체를 끄지
않고, Trivy가 지목한 Terraform 리소스가 ResourcePlan의 공개 진입 리소스와 정확히 같은 경우에만
근거와 함께 허용한다.

산출물 화면의 추적 정보도 같은 `TestingInput`이 가리킨 SOURCE_CODE 버전, 계약과 구현 RTM을
사용한다. 파일 내용이 같아 이전 version ID를 재사용해도 현재 구현 Job의 RTM은 실행 입력에 따로
고정한다. 최신 설계나 최신 소스와 섞지 않으며, 전체 표준 출력 대신 명령·종료 코드·실행 시간·
보고서 경로만 근거로 남긴다.

## 코드 경계

Implementation은 코드를 만든 직후 compile, 단위 테스트, 작은 통합 테스트와 frontend build를
실행하고 같은 작업에서 수리한다. Testing은 이를 다시 실행하지 않는다. Testing에 남은 runtime
코드는 생성 앱 컨테이너 실행, HTTP 기능 검사, 배포 정적 검사와 IaC 검사만 담당한다.

## LLM 연결

동적 기능 계획에서 OpenAPI만으로 정할 수 없는 입력값이 있을 때만 LLM을 사용한다. 연결 정보는
`app.llm_connection`에서 한 번 만든다. 따라서 `LLM_PROVIDER`, `API_KEY`, `BASE_URL`, `MODEL`은
요구사항·설계·구현과 Testing이 함께 사용하며, Testing 코드가 NVIDIA나 특정 URL을 기본값으로
추측하지 않는다. OpenHands처럼 LiteLLM adapter가 필요한 경로만 중앙 연결이 계산한 모델 이름을
사용한다. API key는 요청에만 전달하고 테스트 계획·결과·로그에는 저장하지 않는다.
