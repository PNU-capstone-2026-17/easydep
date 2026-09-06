# Testing 런타임 실패 증거·자동 복구 개선 계획

- 작성일: 2026-09-06
- 상태: 구현 완료
- 우선순위: P0는 runtime 실패 증거와 동일 실패 재검사, P1은 Testing 내부 fast-fail, P2는 LLM 계획·판정 신뢰성
- 관련 문서: `artifact-rtm-simplification.md`, `testing-agent-improvement.md`,
  `testing-feedback-repair.md`, `testing-repair-loop-optimization.md`,
  `testing-progress-and-results-ui-plan.md`

## 1. 결론

런타임 검사를 Implementation 단계로 옮기거나 복제하지 않는다.

- Implementation은 compile, 단위 테스트, 좁은 Spring slice, 설계·코드 계약 준수를 담당한다.
- Testing은 완성된 구현 snapshot의 앱 기동, runtime dependency, HTTP 기능 검사, 배포·IaC 검사를
  담당한다.
- HTTP 500을 포함한 runtime 결함이 Testing에서 처음 발견되는 것 자체는 정상적인 단계 분담이다.
- 현재 보완해야 할 핵심은 실패 원인을 설명할 runtime 증거가 사라지고, 구현 수리 에이전트가 상태 코드와
  넓은 파일 힌트만 받아 다시 추측한다는 점이다.

따라서 첫 구현 범위는 다음 네 가지로 제한한다.

1. 앱 기동, transport, HTTP status, response schema와 의미 assertion 실패 순간의 증거를 함께
   보존한다.
2. 같은 증거를 RTM으로 확인한 Implementation 수리 대상에 그대로 전달한다.
3. 수리 전후에 같은 계획과 입력으로 실패한 case부터 재검사한다.
4. Testing 안에서 명백한 runtime 실패를 먼저 처리하여 불필요한 후속 검사를 늦춘다.

새 feedback agent, 새 DB table, 별도 E2E framework는 추가하지 않는다. 기존 Testing report,
`workspace_commands.payload.testing_checkpoint`, command result와 Workspace event를 사용한다.

## 2. 기준 사례

앱 `55f6b38e-bd32-4772-a877-282127f4aff0`의 Testing은 다음 결과를 남겼다.

- case: `UC1`
- operation: `startCalculation`
- request: `POST /calculations`
- 입력 출처: OpenAPI enum과 고정된 LLM leaf 값
- 결과: HTTP 500
- 판정: `SUT_DEFECT`, repair owner `implementation`

이 판정 자체는 타당하다. 성공 경로로 선언된 API가 schema-valid 요청에서 500을 반환했기 때문이다.
그러나 저장된 핵심 증거는 Spring의 일반 오류 JSON뿐이고 서버 stack trace와 root cause가 없다.
그 결과 수리 작업은 controller, service, persistence와 application wiring을 넓게 다시 조사해야 했다.

이번 계획의 P0 완료 기준은 동일한 사례에서 다음 수리 입력이 만들어지는 것이다.

```text
POST /calculations -> 500
  + 실제 사용한 크기 제한 요청 요약
  + 응답 상태와 제한된 본문
  + Testing runtime profile과 DB 정보
  + 애플리케이션 로그의 root cause 및 application frame
  + operationId, use-case/requirement ref, 관련 구현 파일
  + 보존된 candidate/test input digest
```

### 2.1 500은 대표 사례이지 전체 범위가 아니다

증거 수집과 분류는 `dynamicFunctional`의 모든 비정상 종료를 대상으로 한다. 다만 모든 비정상
결과를 제품 결함으로 취급하지 않고, 다음 표처럼 판정 근거와 수리 owner를 분리한다.

| 관찰 결과 | 기본 판정 | 필요한 증거와 예외 |
|---|---|---|
| 앱 시작·health 실패 | `SUT_DEFECT` 또는 `ENVIRONMENT_DEFECT` | application exception이면 SUT, Docker daemon·image pull·runner 실패면 environment |
| 연결 거부·reset·timeout | 우선 `INCONCLUSIVE` | container 상태와 log로 앱 crash가 확인되면 SUT, 외부 실행 환경 문제면 environment |
| 예상하지 않은 5xx | `SUT_DEFECT` | 요청·응답과 application root cause를 보존한다. |
| 정상 성공 흐름의 예상하지 않은 4xx | 증거 기반 분류 | schema-valid 입력인데 구현 validation이 계약과 다르면 SUT, fixture나 문서화되지 않은 precondition 문제면 test/upstream |
| negative flow의 명시된 4xx | `PASS` 가능 | acceptance/OpenAPI가 그 status를 기대한다는 직접 근거가 있어야 한다. |
| 2xx이지만 JSON 또는 response schema 불일치 | `SUT_DEFECT` | 실제 response와 schema validation 오류를 보존한다. |
| 2xx·schema 일치지만 의미 assertion 실패 | `SUT_DEFECT` 가능 | frozen acceptance oracle이 있을 때만 제품 결함으로 확정한다. |
| 상태 변화를 관찰할 방법이 없음 | `INCONCLUSIVE` | 204 성공만으로 persistence·상태 요구사항을 PASS로 만들지 않는다. |
| plan·입력 자체가 schema를 위반 | `TEST_DEFECT` | 애플리케이션을 호출하지 않거나 제품 수리로 보내지 않는다. |
| 계약·요구사항이 충돌하거나 기대 결과 없음 | `UPSTREAM_AMBIGUITY` | Requirements 또는 Design 보완 대상으로 보낸다. |

따라서 증거 수집 trigger는 단순히 `statusCode == 500`이 아니라 dynamic case의
`gateStatus != PASS`다. 예상된 negative response처럼 case 자체가 PASS인 경우는 실패 로그 수집과
수리 대상이 아니다.

## 3. 현재 구조와 공백

### 3.1 이미 동작하는 부분

- `app/testing/runtime/app_container.py`는 앱 시작 실패 시 container log를 수집한다.
- `app/testing/utils/functional_executor.py`는 정확한 operationId를 OpenAPI path/method로 해석하고,
  status와 response schema를 결정론적으로 검사한다.
- 동적 기능 결과는 candidate plan과 제안된 입력값을 저장한다.
- `app/testing/service.py`는 finding, RTM target, file hint와 candidate plan을 blocking finding으로
  투영한다.
- `app/workspace/service.py`는 `SUT_DEFECT`를 Implementation으로 보내고 같은 command 안에서 자동
  수리와 재검사를 이어 간다.
- 입력 digest가 같은 통과 gate와 case는 선택 재검사에서 재사용한다.

### 3.2 남은 공백

1. 앱이 정상 기동한 뒤 dynamic case가 실패하면 container log를 수집하지 않는다. 이는 5xx뿐 아니라
   예상하지 않은 4xx, schema·semantic assertion 실패와 transport 오류에도 해당한다.
2. 실제 요청값 중 OpenAPI enum/default로 정한 값은 결과에 값이 남지 않고 출처만 남을 수 있다.
3. runtime 정보에는 사용 profile과 test database가 명시적으로 나타나지 않는다.
4. graph는 앱을 먼저 띄운 뒤 정적·IaC 검사를 수행하고 마지막에 HTTP 기능 검사를 한다. 앱이 정적
   검사 동안 대기하며, 명백한 HTTP 실패를 늦게 발견한다.
5. 한 기능 case가 실패해도 나머지 case를 계속 실행하므로 최초 차단 원인의 수리 시작이 늦어진다.
6. LLM 계획 validator는 operationId가 허용 목록에 있는지는 확인하지만 필수 operation의 누락과
   순서의 타당성까지 입증하지 않는다.
7. 현재 `2xx + response schema` 결과가 연결된 기능 요구사항의 의미적 성공으로 과하게 해석될 수
   있다.

## 4. 지켜야 할 경계

### 4.1 단계 책임

```text
Implementation
  compile / unit / narrow integration / conformance
        |
        v
Testing
  app launch / HTTP runtime / deployment / IaC / E2E
        |
        +-- SUT_DEFECT ----------> Implementation repair
        +-- TEST_DEFECT ---------> Testing plan/input repair
        +-- ENVIRONMENT_DEFECT --> environment retry or wait
        +-- UPSTREAM_AMBIGUITY ---> Requirements or Design
```

Testing 실패를 Implementation으로 전달하는 것은 stage 이동이 아니라 기존 자동 수리 loop다.
Implementation은 실패 증거를 받아 제품 코드를 고치지만 runtime gate의 소유권과 최종 판정은 계속
Testing에 있다.

### 4.2 판정 원칙

- schema-valid 성공 경로에서 발생한 5xx는 결정론적인 contract failure지만 유일한 runtime 실패는
  아니다.
- transport 실패, Docker daemon 문제, image pull 오류는 제품 코드 실패로 분류하지 않는다.
- 생성한 path 식별자에 필요한 fixture가 없다는 근거가 있는 404는 `TEST_DEFECT`로 유지한다.
- 일반 4xx는 OpenAPI와 precondition 근거 없이 무조건 제품 오류나 테스트 오류로 단정하지 않는다.
- `2xx + schema`는 API contract 실행 성공이지, 계산 결과나 상태 변화의 의미적 정답까지 보장하지
  않는다.
- LLM은 후보 계획과 OpenAPI만으로 정할 수 없는 입력 leaf를 제안할 수 있지만 최종 판정자가 아니다.

### 4.3 하지 않을 일

- Testing runtime을 Implementation final로 복사하지 않는다.
- 생성 애플리케이션마다 Testcontainers나 별도 E2E dependency를 추가하지 않는다.
- 이번 사례만을 겨냥한 `readOnly -> save` 문자열 규칙을 만들지 않는다.
- LLM에게 URL, HTTP method, 성공 status나 전체 request body를 만들게 하지 않는다.
- 구현 실패 뒤 test plan이나 기대 조건을 약하게 만들어 통과시키지 않는다.
- 전체 container log를 DB나 LLM prompt에 넣거나 EasyDep의 LLM/CSP credential을 생성 앱에
  주입하지 않는다.
- 오류 하나마다 전체 pytest와 전체 앱 생성 E2E를 반복하지 않는다.

## 5. 목표 흐름

### 5.1 최초 Testing

```text
고정 TestingInput과 구현 snapshot 확인
  -> candidate plan 생성·검증 및 입력 보존
  -> 앱 한 번 기동
  -> HTTP case를 순서대로 실행
       -> 첫 blocking failure에서 중단
       -> 요청/응답/runtime log 증거 확정
  -> HTTP가 통과하면 정적·배포·IaC gate 실행
  -> 전체 gate 집계
```

HTTP와 정적 검사의 순서는 Testing 안에서만 바꾼다. 구현 단계에는 새 runtime gate가 생기지 않는다.
정적·IaC만 선택 재검사하는 경우에는 앱을 띄우지 않고 기존 dynamic PASS를 재사용한다.

### 5.2 SUT 자동 수리

```text
blocking finding
  -> operationId와 frozen RTM으로 구현 target 확인
  -> 같은 request, response, log excerpt와 digest 전달
  -> Implementation agent가 제품 코드 수정
  -> 실패한 HTTP case를 같은 입력으로 재실행
       -> 실패: 새 root cause와 source digest를 비교하고 수리 지속
       -> 통과: 아직 실행하지 않은 case와 gate 진행
```

수리 candidate가 파일만 바꾸고 같은 finding을 유지하면 기존 no-improvement 정책으로 폐기한다.
동일 source와 동일 finding이 반복되면 제품 코드를 무한 수정하지 않고 EasyDep runtime/repair 경계의
시스템 오류로 종료한다.

## 6. 최소 증거 형태

새 최상위 데이터 모델을 만들지 않고 기존 dynamic finding의 evidence를 확장한다. 예시는 다음과 같다.

```json
{
  "operationId": "startCalculation",
  "stepId": "step1",
  "code": "HTTP_STATUS_NOT_SUCCESS",
  "statusCode": 500,
  "request": {
    "method": "POST",
    "path": "/calculations",
    "query": {},
    "body": {
      "firstOperand": 42.0,
      "secondOperand": 42,
      "operation": "ADDITION"
    }
  },
  "responseBody": "...limited...",
  "runtime": {
    "source": "application",
    "profile": "test",
    "database": "h2-mysql-mode"
  },
  "applicationLogExcerpt": "...root cause and application frames..."
}
```

저장 규칙:

- request와 response는 각각 크기를 제한한다.
- Testing은 격리된 임시 DB와 시뮬레이션 입력·계정을 사용하므로 범용 credential 탐지나
  redaction 계층을 추가하지 않는다. EasyDep의 LLM/CSP credential은 생성 앱 container나 기능
  요청 evidence에 전달하지 않는다.
- log는 시작 원인, 마지막 `Caused by`, 생성 앱 package의 stack frame을 우선하여 4~8KB 안으로
  제한한다.
- container 이름은 내부 cleanup에만 쓰고 장기 사용자 증거에는 필요할 때만 남긴다.
- finding key와 candidate digest는 log 문구 전체가 아니라 안정적인 code, operationId, status,
  정규화된 root-cause class로 계산하여 timestamp 변화가 반복 판정을 깨지 않게 한다.

`app/testing/service.py`가 현재 finding을 복사해 blocking evidence로 만들고
`app/workspace/service.py`가 exact evidence를 수리 prompt에 포함하므로, bounded evidence가 finding에
들어가면 별도 DB migration 없이 전달 경로를 재사용할 수 있다.

## 7. 구현 Wave

### Wave 0. 기준 고정

목적: 현재 동작을 바꾸기 전에 실패 증거와 재개 계약을 테스트로 고정한다.

작업:

1. HTTP 500 finding이 response만 포함하고 application log는 포함하지 않는 현재 fixture를 만든다.
2. candidate plan과 input value가 checkpoint와 repair profile에서 보존되는 기존 동작을 고정한다.
3. dynamic failure가 `SUT_DEFECT -> implementation`으로 전달되는 public service 결과를 고정한다.
4. evidence size 상한의 기대값을 먼저 정의한다.

완료 조건:

- production 변경 없이 현재 공백을 재현하는 focused test가 있다.
- private helper 문자열이나 prompt 문구가 아니라 공개 report/repair 결과를 검사한다.

### Wave 1. Runtime 실패 증거 보존과 전달

대상:

- `app/testing/runtime/app_container.py`
- `app/testing/runtime/verification.py`
- `app/testing/utils/functional_executor.py`
- `app/testing/service.py`
- 필요한 Testing·Workspace focused tests

작업:

1. 실행 중인 Testing application에 대해 bounded log excerpt를 읽는 공개 helper를 추가한다.
2. helper는 Testing이 만든 정확한 container identity만 허용한다.
3. executor가 bounded request summary를 모든 failed/inconclusive finding에 남기게 한다.
4. `_run_verification_graph`가 application context를 정리하기 전에 모든 dynamic failure에 runtime
   metadata와 log excerpt를 붙인다. 예외 stack trace가 없는 의미 실패에서는 빈 로그를 꾸미지 않고
   request, response와 oracle 근거를 중심으로 남긴다.
5. 시작 실패에는 기존 `ApplicationLaunchError` 로그를 유지하고 동일 필드 형태로 정규화한다.
6. blocking evidence와 Implementation feedback에서 필드가 유실되지 않는지 확인한다.
7. 수리 prompt 전체 크기가 계속 bounded인지 확인한다.

완료 조건:

- 시작 실패, transport 오류, 예상하지 않은 4xx/5xx, response schema 불일치 fixture에서 각각 필요한
  증거와 올바른 repair owner가 남는다.
- contract-backed semantic failure는 oracle provenance와 실제 응답이 Implementation 수리 입력까지
  전달된다.
- 외부 `target_url`을 사용해 container identity가 없으면 임의의 Docker log를 읽지 않는다.
- evidence가 없어도 기존 failure flow는 유지된다.

### Wave 2. Testing 내부 fast-fail과 실패 지점 재개

대상:

- `app/testing/graphs/testing_graph.py`
- `app/testing/nodes/dynamic_functional.py`
- `app/testing/runtime/verification.py`
- `app/testing/service.py`
- `app/workspace/service.py`는 필요한 통합 연결만 메인 에이전트가 담당

작업:

1. 최초 전체 검사에서는 dynamic functional gate를 정적·IaC보다 먼저 실행한다.
2. 첫 blocking case에서 case loop를 중단하고 `pendingCaseIds`를 남긴다.
3. 아직 실행하지 않은 정적·IaC gate는 실패 finding으로 만들지 않고 `deferredGates`로 기록한다.
4. candidate plan, 실패 case 입력, 이미 PASS한 case 결과를 checkpoint에 유지한다.
5. 구현 수리 뒤에는 같은 실패 case를 먼저 실행한다.
6. 실패 case가 통과하면 남은 case를 진행하고, dynamic 전체 통과 뒤 deferred gate를 실행한다.
7. 정적·IaC 수리에서는 기존 dynamic PASS digest가 같을 때 앱을 다시 띄우지 않는다.
8. UI에는 기존 Testing progress event를 사용해 `failed -> repairing -> rerunning -> passed` 상태만
   투영하며 별도 실시간 프로토콜을 만들지 않는다.

완료 조건:

- 첫 500 뒤 나머지 HTTP case와 정적·IaC 도구가 불필요하게 실행되지 않는다.
- 수리 후 `planDigest`와 실패 case의 `failedRequestDigest`가 바뀌지 않는다. fast-fail 당시 아직
  입력을 만들지 않은 pending case가 이후 실행되면 전체 `candidateDigest`에는 그 입력이 추가될 수 있다.
- 이미 PASS한 case와 gate는 관련 digest가 같을 때만 재사용된다.
- source가 바뀌면 영향을 받는 case는 과거 PASS를 무조건 재사용하지 않는다.
- 서버 재시작 뒤 checkpoint가 있으면 실패 case부터 재개한다.

### Wave 3. LLM 계획 순서 검증

이 Wave는 로그·복구 P0와 분리하여 진행한다. 실패 원인 전달을 고치는 데 새 plan schema는 필요 없다.

대상:

- `app/testing/nodes/dynamic_functional.py`
- `app/testing/schemas/functional_plan.py`는 필요한 최소 필드만 검토
- `app/testing/utils/functional_executor.py`
- `tests/test_functional_plan.py`

작업:

1. LLM이 선택한 operationId가 허용 목록에 있다는 현재 검사를 유지한다.
2. frozen use-case main scenario와 trace에서 필수 operation set과 명시된 선후 관계를 계산할 수 있는
   경우 누락·중복·역순을 결정론적으로 거부한다.
3. 순서를 계산할 직접 근거가 없으면 LLM의 순서를 사실로 확정하지 않고 `UPSTREAM_AMBIGUITY` 또는
   `INCONCLUSIVE`로 남긴다.
4. 이전 response를 다음 request에 연결할 때 이름·타입이 유일하다는 현재 조건을 유지하고, 여러
   후보가 있으면 추측하지 않는다.
5. invalid plan은 제품 수리가 아니라 `TEST_DEFECT` 또는 상류 모호성으로 보낸다.

완료 조건:

- 허용된 operationId만 사용했어도 필수 operation을 빠뜨리거나 명시된 순서를 뒤집으면 실행 전에
  거부된다.
- 근거 없는 LLM 순서 때문에 Implementation 코드가 수정되지 않는다.
- 단일 operation use case는 불필요한 새 계약 없이 기존 경로를 유지한다.

### Wave 4. Contract PASS와 의미적 요구사항 PASS 분리

목적: LLM이나 OpenAPI schema만으로 정답을 만들지 못하는 경우 거짓 PASS를 방지한다.

작업:

1. `2xx + response schema`를 `contract execution PASS`로 취급한다.
2. 요구사항의 acceptance condition에서 실행 가능한 expected status, response predicate 또는 state
   transition을 직접 얻을 수 있을 때만 semantic verification evidence를 만든다.
3. 나눗셈 0 같은 alternate/exception flow는 main happy path와 별도 case로 실행한다.
4. 상태 변경 API가 204만 반환하고 후속 조회나 관찰점이 없으면 해당 의미 요구사항을
   `unverifiedIds` 또는 `INCONCLUSIVE`로 남긴다.
5. LLM은 구조화되지 않은 acceptance 문장을 typed 후보로 변환할 수 있지만, 원문 ref와
   결정론적 validator가 확인하지 못한 assertion을 최종 oracle로 사용하지 않는다.
6. 구현 source를 읽고 기대 결과를 역으로 만들지 않는다.

완료 조건:

- 계산 결과가 schema-valid이지만 잘못된 값인 negative fixture가 요구사항 PASS가 되지 않는다.
- 예외 흐름을 실행하지 않은 main case가 예외 요구사항까지 검증한 것으로 표시되지 않는다.
- 실행 가능한 oracle이 없는 요구사항은 실패나 성공으로 꾸미지 않고 미검증 상태가 된다.

## 8. 서브에이전트 활용 계획

서브에이전트는 구현 시에만 사용한다. 계획서 작성이나 단순 상태 확인 때문에 자동으로 실행하지
않는다. 각 Wave는 파일 소유권을 분리하고, 공유 파일은 메인 에이전트가 통합한다.

| 역할 | 권장 모델 | 소유 범위 | 책임 |
|---|---|---|---|
| Testing backend production | Terra | `app/testing/runtime`, `app/testing/nodes`, `app/testing/utils` 중 Wave별 지정 파일 | log capture, graph/runner 동작과 checkpoint 데이터 생산 |
| 빠른 계약·회귀 검사 | Luna | `tests/test_functional_plan.py`, `tests/test_testing_agent.py`, `tests/test_testing_selective_rerun.py` 등 Wave별 지정 테스트 | 500 fixture, size, fast-fail, resume, invalid plan 회귀 검증 |
| Workspace·repair 통합 | 메인 에이전트 또는 Sol | `app/testing/service.py`, `app/workspace/service.py`처럼 충돌 위험이 큰 공유 경계 | evidence 전달, RTM target, command/checkpoint lifecycle 통합 |
| 읽기 전용 사전 감사 | Explorer 또는 Luna | 코드 수정 없음 | 기존 계획과 실제 public contract 차이, 중복 코드와 삭제 후보 확인 |

운영 규칙:

1. Terra와 Luna가 같은 production 파일을 동시에 수정하지 않는다.
2. worker에게 “다른 작업자와 같은 worktree를 사용하며 다른 변경을 되돌리지 않는다”는 조건을
   명시한다.
3. Wave 1은 Terra가 production을, Luna가 테스트 fixture를 맡고 메인 에이전트가 public evidence
   shape를 확정한다.
4. Wave 2의 `app/workspace/service.py`는 메인 에이전트가 직접 통합하여 자동 수리 loop 충돌을
   피한다.
5. Wave 3~4는 계약 판단이 포함되므로 메인 에이전트가 validator 정책을 먼저 확정한 뒤 Terra와
   Luna에 서로 겹치지 않는 구현·테스트 범위를 준다.
6. 각 worker 결과는 메인 에이전트가 diff와 실제 focused test 결과를 확인한 뒤에만 커밋한다.
7. 단순 문서 문자열, private helper 호출 순서, mock 내부 구현을 고정하는 테스트는 받지 않는다.

권장 실행 예:

```text
Wave 1
  Terra: app_container + runtime verification + executor evidence
  Luna: HTTP 500/evidence size/propagation tests
  Main: service/workspace 연결 검토, 통합 테스트, commit

Wave 2
  Terra: graph conditional routing + checkpoint production
  Luna: selective rerun/restart fixtures
  Main/Sol: Workspace automatic repair lifecycle 통합, commit

Wave 3~4
  Main: plan/oracle 정책 확정
  Terra: validator와 report production
  Luna: wrong-order/wrong-answer/absence-of-oracle negative controls
  Main: 실제 snapshot smoke와 최종 회귀, commit
```

## 9. 검증 전략

### Wave별 focused 검사

- `tests/test_functional_plan.py`
- `tests/test_testing_agent.py`
- `tests/test_testing_selective_rerun.py`
- `tests/test_testing_job_persistence.py`
- `tests/test_workspace_service.py`의 Testing repair 관련 사례
- `tests/test_workspace_http_flow.py`의 command/event 공개 계약
- 수정 Python 모듈 `compileall`, scoped Ruff와 `git diff --check`

### 대표 통합 fixture

1. 앱이 기동 전에 종료되고 application root cause가 있는 경우
2. 앱은 기동하지만 첫 POST가 예상하지 않은 4xx 또는 5xx인 경우
3. connection reset/timeout 뒤 container 상태로 SUT와 environment를 구분하는 경우
4. 2xx지만 JSON 또는 response schema가 잘못된 경우
5. 실패 응답과 로그가 길어 evidence 상한을 넘는 경우
6. 첫 case 실패 뒤 나머지 case와 static gate가 deferred되는 경우
7. 구현 수리 뒤 같은 request digest로 실패 case가 PASS하는 경우
8. static-only 수리에서 dynamic PASS와 앱 기동을 재사용하지 않는 경우
9. 허용 operationId이지만 필수 호출 누락 또는 순서가 잘못된 LLM plan
10. 2xx와 schema는 맞지만 acceptance 결과는 잘못된 응답
11. oracle이나 관찰 endpoint가 없어 의미 검증이 불가능한 204 상태 변경

실제 Docker 검사는 매 작은 수정마다 실행하지 않는다. Wave 1과 Wave 2 완료 시 대표 500 fixture로
한 번씩 실행하고, 최종 통합에서 기존 계산기 또는 다른 저장 snapshot 하나로 실패→수리→재개의
종단 흐름을 확인한다. 외부 네트워크가 필요한 image pull이나 LLM 호출은 처음부터 권한을 구분하여
실행하고, 연결 실패를 제품 오류로 기록하지 않는다.

## 10. 커밋 단위

권장 순서:

1. `test: capture runtime failure evidence contract`
2. `feat: preserve application logs for testing failures`
3. `perf: fail fast and resume testing cases`
4. `fix: validate functional operation ordering`
5. `fix: separate contract and semantic verification`
6. `docs: align testing runtime and recovery flow`

Wave 1과 Wave 2를 먼저 완료하고 실제 운영 효과를 확인한다. Wave 3과 Wave 4는 테스트 신뢰성
개선이지만 acceptance 근거의 품질에 영향을 주므로 별도 커밋과 실제 negative control을 요구한다.

## 11. 최종 완료 기준

- runtime HTTP/E2E 소유권이 Testing에 남아 있다.
- Implementation 정상 생성 경로에 중복 앱 기동 검사가 추가되지 않았다.
- 모든 failed/inconclusive dynamic finding에 유형별로 필요한 크기 제한 요청, 응답, runtime과 가능한
  root-cause log가 포함된다.
- 시작·transport·4xx·5xx·schema·semantic 실패가 같은 오류로 뭉개지지 않고 올바른 owner로 분류된다.
- 같은 증거가 RTM으로 확인된 Implementation repair에 손실 없이 전달된다.
- 수리 전후 plan과 실패 case request digest가 동일하다. pending case의 입력이 처음 생성되며 전체
  candidate digest가 확장되는 것은 별도로 구분한다.
- 첫 blocking failure 뒤 나머지 검사를 deferred하고, 수리 후 그 지점부터 재개한다.
- LLM이 누락·역순 계획이나 근거 없는 정답을 최종 PASS로 만들 수 없다.
- `2xx + schema`와 의미적 요구사항 검증 결과가 구분된다.
- 새 DB schema, 새 feedback agent, 생성 앱별 새 테스트 framework가 없다.
- 변경 뒤 문서와 `app/testing/README.md`의 실제 흐름이 일치한다.

## 12. 구현 결과

- 새 DB table이나 feedback agent 없이 기존 Testing report와 Workspace checkpoint를 확장했다.
- Terra가 runtime log/evidence와 dynamic-first graph를, Luna가 공개 report 중심 회귀 fixture를
  담당했고 메인 에이전트가 service/checkpoint, API scenario trace와 정책을 통합했다.
- 모든 dynamic non-PASS 경로는 실제 테스트 request/response, runtime profile과 가능한 application
  log excerpt를 크기 제한된 finding으로 전달한다. Testing 데이터가 격리된 시뮬레이션 값이라는
  경계에 맞춰 범용 credential 탐지·redaction은 넣지 않았다.
- 첫 차단 case 뒤에는 `pendingCaseIds`와 `deferredGates`를 남기고, 같은 구현 checkpoint 재개 시
  PASS case와 실패 case 우선순위를 복원한다. 새 구현 snapshot에서는 이전 PASS case를 다시 쓴다는
  근거가 없으므로 재실행한다.
- API endpoint에 기존 Boundary/root-call `stepRefs`를 투영하고, 이 직접 근거가 있는 경우에만 다중
  operation의 필수 집합·순서를 검증한다. 근거 없는 다중 호출 계획은 HTTP 실행 전에
  `UPSTREAM_AMBIGUITY`로 차단한다.
- 현재 upstream 산출물에 typed acceptance oracle이 없으므로 HTTP 성공은 `contractIds`에만 기록하고
  의미 검증 `ids`는 비운다. 전체 기능 요구사항은 `unverifiedIds`로 남겨 schema-valid 오답과 관찰점
  없는 204를 의미적 PASS로 만들지 않는다.
- focused Testing/API/checkpoint 검사 61개와 Design/Implementation/Workspace 연계 검사 136개가
  통과했고, 수정 범위의 `compileall`, Ruff, `git diff --check`가 통과했다.
- 저장된 생성 앱을 Docker에서 실제 실행해 5개 HTTP 기능 case의 PASS와 500 실패 증거 전달을
  각각 확인했다. Docker 상태 확인 자체가 멈춘 경우에는 raw subprocess 오류를 노출하지 않고
  `ENVIRONMENT_DEFECT`로 분류하도록 보완했다.
