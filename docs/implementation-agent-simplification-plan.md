# 구현 에이전트 단순화 및 재검증 계획

- 작성일: 2026-09-19
- 상태: 구조 개선 및 기존 checkpoint 전체 owner 완료, fresh 전체 기준선은 미완료
- 대상: 구현 단계의 작업 계획, OpenHands 실행, 작업별 검증, 최종 통합 검증과 Testing 인계
- 참고 이력: `archive/dev-local-demo-20260916`

## 1. 결론

아카이브 브랜치의 반복 보완에도 fresh implementation이 안정적으로 완료되지 않은 주된 이유는
코딩 모델의 능력 부족 하나가 아니다. 구현을 준비하고 통제하고 검증하는 실행기가 실제 코드
작성보다 복잡해졌고, 유스케이스 단위 작업 경계가 공유 Java source의 소유 경계와 맞지 않았다.

후보 source가 이미 존재할 때 이를 검증하고 승격하는 경로는 동작했다. 반면 새 구현에서는
에이전트가 source를 수정하기 전에 탐색을 반복하거나 focused test 준비에서 중단되었다. 이후
보완은 실패 지점을 없애기보다 `focused test 누락`, import/FQCN 복원, stable ID 충돌,
completion contract 실패처럼 다음 제어 지점으로 이동시키는 경우가 많았다.

따라서 아카이브 브랜치를 그대로 병합하지 않는다. 검증 격리와 선택적 재실행처럼 확인된
실행 원칙만 가져오고, 구현 전 독립 테스트 생성과 도메인별 특수 가드는 제외한다.

## 2. 관찰된 실패 구조

### 2.1. 작업 경계와 source 소유권의 불일치

기존 구조는 UC와 API 연결을 작은 행동 slice로 나누고 각 slice를 별도 에이전트가 구현하게 했다.
그러나 Controller, Service, Entity, Repository는 여러 UC가 공유한다. 논리적 유스케이스는 서로
구분되더라도 실제 Java 컴파일·수정 단위는 독립적이지 않다.

이 구조에서는 다음 문제가 발생한다.

- 뒤 slice가 앞 slice와 같은 production source를 다시 수정한다.
- 앞 작업의 테스트가 뒤 작업에 섞여 현재 작업과 무관한 실패를 만든다.
- 공유 클래스의 최종 정합성을 책임지는 단일 소유자가 없다.
- 작업별 검증과 전체 프로젝트 정합성을 매 작업에서 동시에 보장하려 한다.

### 2.2. 생성 시점의 타입 정보 유실

scaffold 생성기는 source를 만들 때 정확한 생성자 인자, FQCN, API 타입, collaborator 메서드와
endpoint 연결을 알고 있다. 기존 흐름은 이 정보를 최소한의 구조화된 계약으로 유지하지 않고,
후속 planning과 테스트 준비가 Java 문자열, import, RTM 및 여러 설계 산출물에서 다시 복원하게
했다.

이 때문에 wildcard import, 타입 별칭, overload, FQCN과 stable ID처럼 구현할 업무 로직과
직접 관계없는 복원 오류가 구현 진입을 막았다. 결정론적으로 이미 알고 있던 사실은 다시 LLM으로
추측하거나 source 문자열에서 역추출하지 않아야 한다.

### 2.3. 구현 전 focused test 준비의 역전

미구현 scaffold를 대상으로 정확한 JUnit을 먼저 작성하려면 fixture, 생성자, mock interaction,
반환 타입과 도메인 조건을 모두 해석해야 한다. 이 단계 자체가 또 하나의 구현 작업이 되어 실제
production source 수정 전에 실패할 수 있다.

독립 focused-test 준비는 일부 실행에서 owner가 구현을 완료하도록 도왔지만, 성공 플래그만
검사하고 영속화·인증·기간 조건을 검증하지 못한 사례도 있었다. 복잡도와 실행 비용에 비해
일반적인 품질 보장이 충분히 입증되지 않았으므로 초기 구현의 필수 gate로 두지 않는다.

### 2.4. 과도한 초기 문맥과 제어 계약

구현 owner가 코드를 쓰기 전에 RTM 근거, 설계 문맥, 수정 가능 파일, 읽기 전용 의존 source,
focused test, completion marker, 재개 조건과 여러 금지 규칙을 함께 해석했다. no-progress나 읽기
횟수 제한은 오래 걸리는 실행을 종료할 수는 있지만, 에이전트가 처음 받은 작업을 단순하게 만들지는
못한다.

진행 제어는 범용 상태만 사용해야 한다. UC 이름, 특정 클래스, import 형태나 과거의 한 실패를
직접 인코딩한 가드는 추가하지 않는다.

### 2.5. 오래된 checkpoint와 반복 보정

scaffold, prompt, 도구 구성 또는 작업 계약이 바뀌었는데도 이전 conversation checkpoint를
재사용하면 과거 문맥과 현재 source가 어긋난다. 같은 작업을 반복 재개하며 특수 보정을 추가하면
실패가 해결되기보다 다른 검사기로 이동한다.

재개는 prompt, source, 검증 계약과 도구 schema가 동일할 때만 허용한다. 이 중 하나라도 바뀌면
기존 후보 source를 참고할 수는 있지만 새 대화를 시작한다.

## 3. 유지할 항목과 제외할 항목

### 3.1. 아카이브에서 선택적으로 유지할 원칙

- 수정 파일을 중심으로 한 작업별 격리 컴파일과 최소 검사
- 모든 작업을 합친 뒤 전체 main source 컴파일과 최소 통합 검사 1회
- 테스트 결함, 애플리케이션 결함, 환경 결함, 상류 계약 공백의 구분
- 성공한 작업을 다시 실행하지 않는 선택적 재실행
- 작업 입력과 결과의 타입이 명시된 계약
- 동일한 source와 동일한 오류가 반복될 때 재검증을 중단하는 범용 진전 판정
- 후보 workspace에서 수정한 뒤 독립 검증에 성공한 변경만 승격하는 경계

### 3.2. 그대로 가져오지 않을 구조

- production source 수정 전에 별도 LLM이 JUnit을 작성하는 필수 단계
- UC마다 공유 Service·Controller·Entity를 중복 소유하는 세분화
- 원본 RTM과 모든 설계 산출물을 최초 prompt에 전부 포함하는 방식
- 읽기 횟수, 특정 import, 특정 클래스 또는 marker별 특수 가드
- 구현 가능성을 여러 계층에서 반복 판정하는 admission 절차
- 계약이 달라졌는데도 기존 conversation을 이어 쓰는 재개
- 작업마다 전체 프로젝트의 컴파일과 전체 테스트를 반복하는 검증

## 4. 목표 실행 구조

```text
고정된 설계 snapshot
  → scaffold와 사실 계약 생성
  → 응집된 backend 작업 계획
  → 구현 owner의 첫 수정
  → 변경 source 컴파일과 최소 계약 검사
  → 제한된 오류 수정
  → 변경 승격
  → 전체 backend 컴파일과 선택된 시나리오 검사 1회
  → Testing 인계
```

### 4.1. 사실 계약

scaffold와 함께 `GeneratedOperationContract`에 해당하는 작은 sidecar 계약을 생성한다. 이름은
구현 시 현재 모델 구조에 맞게 정하되 다음 사실만 포함한다.

- 대상 public operation과 source 위치
- 수정 가능한 production source
- 생성자 의존성과 정확한 FQCN
- 호출 가능한 collaborator operation의 정확한 인자·반환 타입
- endpoint, API 입력·출력 타입과 operation의 연결
- scaffold에 남은 미구현 위치 또는 completion marker

이 계약은 구현 방법, 도메인 정책 또는 테스트 코드를 미리 결정하지 않는다. 생성기가 이미 알고
있는 사실의 유실만 방지한다. RTM은 추가 자료를 찾는 색인으로 사용하며 세부 코드 변경 범위를
강제로 결정하지 않는다.

### 4.2. 작업 소유 단위

초기 검증에서는 UC별 slice보다 하나의 응집된 코드 소유 단위를 사용한다. 데모 앱에서는 우선
backend 전체를 한 owner가 맡거나, 변경 충돌이 없는 aggregate/module 단위로만 분리한다.

분할은 다음 조건을 모두 만족할 때만 허용한다.

- 수정 source 집합이 다른 작업과 겹치지 않거나 최종 소유자가 명확하다.
- 작업 입력만으로 구현할 public contract가 완전하다.
- 작업별 검사가 다른 작업의 미완성 source에 의존하지 않는다.

조건을 만족하지 않으면 작업 수를 늘리지 않고 더 큰 응집 단위로 합친다.

### 4.3. 구현 owner의 역할

owner에게 처음부터 제공하는 내용은 scaffold, 사실 계약, 수정 가능 파일, 완료 조건과 한 개의
검증 명령으로 제한한다. owner는 필요한 경우에만 RTM 기반 검색으로 관련 설계나 source를 추가
조회한다.

owner가 판단할 사항은 다음과 같다.

- 자연어·설계 계약을 업무 로직으로 옮기는 방법
- 필요한 관련 클래스를 읽는 범위
- 주어진 소유 경계 안에서 컴파일 오류를 고치는 방법
- collaborator를 호출하고 결과를 조합하는 방법

시스템은 수정 가능 경계, 초기 사실, 검사 명령, 실제 source 변경 여부와 중단 시점만 관리한다.

### 4.4. 검증과 수정

작업 검증은 다음 두 계층으로 제한한다.

1. 작업별 검사
   - 변경된 main source를 컴파일한다.
   - 기계적으로 확인 가능한 공개 계약 또는 작은 smoke check만 실행한다.
   - 같은 source에서 같은 오류를 다시 실행하지 않는다.
   - 작업 범위 밖 오류는 해당 owner가 전체 프로젝트를 고치지 않고 통합 단계에 전달한다.
2. 최종 통합 검사
   - 승격된 전체 main source를 한 번 컴파일한다.
   - 대표 시나리오와 필요한 통합 테스트를 한 번 실행한다.
   - 실패 시 원인이 연결된 작업만 다시 연다.

검증 실패에 대한 owner 수정은 우선 1~2회로 제한한다. 새 source 변경 없이 동일 오류가 반복되면
추가 가드를 만들지 않고 해당 작업을 실패 증거와 함께 종료한다.

### 4.5. 테스트 작성 시점

생성기가 결과를 기계적으로 알 수 있는 계약 smoke test는 scaffold와 함께 만들 수 있다. 반면
업무 정책을 검증하는 테스트는 production 구현 이후 공개 계약과 완성된 source를 바탕으로
작성한다. 초기 production 구현을 시작하기 위한 선행 gate로 사용하지 않는다.

Testing의 Arazzo 방식처럼 후보 operation과 연결을 코드가 유한 집합으로 제공하고 LLM이 선택한
뒤 코드가 표준 산출물을 컴파일하는 구조는 유지할 수 있다. 다만 Testing의 성공이 임의 Java
구현에도 같은 세분화가 적절하다는 근거는 아니다.

## 5. 범용 상태 머신

도메인별 복구 분기 대신 다음 상태만 사용한다.

```text
READY
  → EDITING
  → VERIFYING
  → CORRECTING
  → SUCCEEDED | FAILED | NEEDS_INPUT | INTERRUPTED
```

범용 가드는 세 가지로 제한한다.

1. 수정 전 탐색 정체: 일정 실행 구간 동안 source 변경이 없으면 수정하거나 정보 부족을 보고한다.
2. 동일 실패 반복: source hash와 진단 지문이 같으면 같은 검사를 다시 실행하지 않는다.
3. 범위 밖 문제 전달: 오류 원인이 수정 불가 파일이나 상류 계약이면 현재 owner가 확장 수정하지
   않고 구조화된 증거를 상위 단계에 전달한다.

호출 횟수 자체보다 source 변경, 진단 변화와 검증 결과를 진전의 기준으로 사용한다.

## 6. 단계별 적용 계획

### 단계 1. 기준선 고정

- 현재 개선된 요구사항·설계 산출물로 fresh reference app을 하나 생성한다.
- demo validation bypass와 보존된 후보 source를 사용하지 않는다.
- 현재 구현에서 첫 수정 전 실패 위치, 소요 시간과 tool action을 기준선으로 기록한다.

### 단계 2. 입력 계약 축소

- scaffold 생성 시점의 사실을 sidecar 계약으로 보존한다.
- Java import와 여러 설계 산출물에서 같은 타입 정보를 다시 복원하는 경로를 제거한다.
- 최초 prompt에는 구현에 직접 필요한 사실만 포함한다.

### 단계 3. 작업 경계 재구성

- 첫 검증은 backend 전체 또는 응집된 단일 모듈 owner로 실행한다.
- 공유 source가 겹치는 UC slice 계획은 사용하지 않는다.
- 한 owner가 너무 커질 경우 실제 source 소유권이 분리되는 지점에서만 나눈다.

### 단계 4. 최소 구현 루프 적용

- 구현 전 독립 focused-test LLM gate를 제거한다.
- 첫 source 수정, 변경 source 컴파일, 최소 검사와 제한 수정만 수행한다.
- 전체 backend 검증은 모든 작업 승격 뒤 한 번 수행한다.

### 단계 5. Testing 연계

- 구현 산출물이 생긴 뒤 테스트 profile과 의미 테스트를 생성한다.
- 실패는 TEST, SUT, ENVIRONMENT, UPSTREAM_CONTRACT로 분류한다.
- SUT 결함만 연결된 구현 owner에게 전달하고 성공한 나머지 작업은 재실행하지 않는다.

## 7. 효과 확인 기준

첫 평가는 과거 후보 재검증이 아니라 fresh implementation으로 수행한다. 다음 값을 반드시 남긴다.

- owner 시작부터 첫 production source 수정까지의 시간
- 첫 수정 전 파일 읽기·검색 수
- owner별 LLM turn과 tool action 수
- 실제로 수정된 production source 수
- 작업별 컴파일 및 최소 검사 결과와 시간
- 동일 진단 재실행 횟수
- correction 횟수
- 최종 전체 컴파일 및 대표 시나리오 결과
- demo fallback 또는 보존 후보 사용 여부

최소 성공 조건은 다음과 같다.

1. 첫 owner가 구현 전 테스트 준비에 막히지 않고 production source를 수정한다.
2. 변경 source가 컴파일되고 최소 계약 검사를 통과한다.
3. 두 번째 응집 작업 또는 후속 수정까지 같은 구조로 연속 성공한다.
4. 전체 backend 컴파일과 대표 시나리오 검사를 한 번 수행한다.
5. 특정 UC·클래스 이름을 위한 새 특수 가드 없이 완료한다.

이 조건이 충족된 뒤에만 16개 요구사항 수강신청 앱 전체 실행으로 확대한다. 첫 owner가 다시
실패하면 오류별 패치를 추가하지 않고 작업 크기, 사실 계약의 누락 또는 모델·도구 실행 경계 중
어느 범주가 원인인지 먼저 판정한다.

## 8. 구현 중 복잡도 제한

개선 과정에서 다음 기준을 지킨다.

- 새 모듈을 추가하면 대체하거나 제거할 기존 복원·가드 경로를 함께 명시한다.
- 같은 사실을 prompt, RTM projection, Java parser와 검증기에서 중복 계산하지 않는다.
- 특정 실패 사례 하나만 통과시키는 조건문을 추가하지 않는다.
- 실행기 코드 증가량보다 제거되거나 단순화되는 제어 경로를 우선 검토한다.
- 작은 대표 실행이 실패한 상태에서 전체 앱 반복 실행으로 넘어가지 않는다.
- 실패 위치만 이동하고 production source 수정이 시작되지 않으면 해당 접근을 중단한다.

이 문서는 과거 개선을 모두 폐기하자는 뜻이 아니다. 검증·승격·선택적 재실행처럼 효과가 확인된
경계는 유지하되, 코딩 에이전트보다 복잡해진 준비 절차를 줄이는 기준으로 사용한다.

## 9. 2026-09-19 적용 결과

이번 변경에서는 특정 UC나 클래스에 대한 보정을 추가하지 않고 다음 공통 경계를 반영했다.

- scaffold 생성 뒤 `reports/generated-operation-contracts.json`을 저장한다. 이 sidecar는 typed
  BCE 모델, 시퀀스 method projection과 API 저장 모델에서 public operation, 실제 구현 source,
  생성자 의존성, collaborator signature, API 입출력 연결과 completion marker를 기록한다.
- backend 계획은 UC/API 연결요소별 작업 대신 `implement-backend-application` 단일 owner 하나를
  만든다. 테스트 source는 이 owner의 쓰기 범위와 필수 산출물에서 제외한다.
- 구현 owner는 JUnit을 먼저 만들지 않는다. 작업 중 검증은 `compileJava --build-cache`와 기존의
  기계적 marker·공개 계약 검사만 사용한다.
- owner가 `run_task_check`를 성공시킨 경우 같은 검사를 outer runtime에서 다시 실행하지 않는다.
  저장된 성공 증거가 없을 때만 outer verifier가 fallback으로 실행한다.
- 구현 시작 전 behavior admission은 새 planning 경로에서 제거했다. integration admission과
  구현 중 명시적으로 보고된 upstream gap 처리는 유지한다.
- JUnit classname으로 과거 UC slice 소유자를 역추적하지 않는다. 명시적 task ID가 없으면 단일
  backend owner에게만 회귀 실패를 돌리고, 소유자가 여러 개라면 통합 실패로 남긴다.
- 비활성 UC/API slice planner와 그 전용 helper 759줄, obsolete slice 전략 테스트 694줄을
  제거했다. source index, method context, frontend·integration planning의 공용 helper는 유지했다.

### 9.1. 로컬 검증 결과

변경 범위에 대응하는 26개 focused test가 통과했다. 확인 범위는 다음과 같다.

- 단일 backend owner 계획과 production-only 쓰기 범위
- typed operation sidecar와 FQCN·endpoint binding 직렬화
- method projection 유지
- backend 작업 검증의 compile-only 명령 선택
- workflow planning, 최종 backend regression 1회와 feedback repair routing
- 동일 source 실패 재실행 차단과 성공한 task-check 재사용
- 성공 checkpoint 재사용과 후보 승격 경계

전체 회귀 테스트는 실행하지 않았다. 이번 변경과 무관한 전체 suite 실행은 계획의 검증 비용 축소
원칙에 맞지 않으므로, 직접 영향을 받는 테스트만 선택했다.

### 9.2. fresh 실행 준비와 남은 검증

새 앱 `eebd54c4-a37a-4b2b-908e-6c4d7b57e2d9`에서 command
`9eda0565-c536-4245-9f83-4cab17bb1e1b`, job
`b23a358cc0e2428683a4328a7801e523`, run `run_d01116d7cae1`으로 실제 구현을 시작했다.
모델은 Cloudflare Workers AI의 `@cf/zai-org/glm-5.3-flash`였다. Docker 권한 거부만 발생한
환경 전용 실행은 모델·계획 효과를 판단할 수 없으므로 이 비교에서 제외한다.

backend owner는 성공했다. 32개 이벤트(15개 tool action)에서 첫 production 수정은 시작 후 약
115초인 2026-09-19T17:37:14Z에 `CalculatorServiceService.java`에 적용됐다. action 구성은
`file_editor` 11회(읽기 10회, `str_replace` 1회), `grep` 2회, `run_task_check` 1회,
`finish` 1회였고 owner 총 소요 시간은 296,005ms였다. `compileJava --build-cache`는 exit code 0,
81,848ms로 통과했다. 읽기 범위를 벗어난 evidence 경계 오류 2회 뒤 즉시 수정으로 진행했으며,
재시도·stuck recovery는 없었다.

이는 이전 baseline의 backend owner가 125개 이벤트와 61개 읽기 action 동안 production 수정이나
검증을 하지 못한 경우보다 명확히 개선됐다. 다만 prompt-only 축소만으로 탐색 확장이 완전히
사라지지는 않았다. 같은 run의 frontend owner는 관찰 종료 시점에 raw design input과 generated
runtime까지 확장해 읽었고, 63개 이벤트 뒤 production 수정과 `npm run build` 성공(exit code 0)을
냈지만 아직 task finish 전이었다. 따라서 이 결과는 backend의 첫 수정과 compile 성공은 확인하지만,
모든 owner에서 읽기 확장을 일반적으로 억제했다는 증거는 아니다.

관찰 종료 시 전체 workflow/job은 여전히 `RUNNING`(backend `SUCCEEDED`, frontend `RUNNING`,
integration `PENDING`)이었다. frontend가 최대 iteration 또는 no-action 종료에 도달하기를 무기한
기다리지 않았으며, 남은 owner별 종료 지연은 별도 실행 경계 문제로 기록한다.

### 9.3. 2026-09-20 기존 checkpoint 재개 결과

기존 앱 `7f386e24-98e1-4c62-92c6-3e770b90c954`, job
`5eaae0fe78ac48f1a37212feba0297b9`, run `run_f7f9032f23bf`의 checkpoint만 재개했다. 새 job,
run 또는 E2E는 만들지 않고 기존 command의 retry로 backend, frontend, integration을 모두
완료했으며 integration은 attempt 3에서 성공했다.

- isolated precheck와 해당 job의 `node_modules`는 오염되지 않았고, owner-labeled runner는
  0개로 확인했다.
- 재개 뒤 container lifecycle과 이 작업이 시작한 서버·프로세스를 정리했다.
- focused tests, Ruff, diff check가 통과했다.

이는 기존 checkpoint의 전체 owner 완료와 재개·정리 경계를 확인한 결과다. 보존 후보와 기존
checkpoint를 사용했으므로, 7절의 fresh 전체 기준선 또는 fresh implementation 성공 증거는 아니다.
