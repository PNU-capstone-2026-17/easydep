# 전역 사용자 피드백과 revision 개선안

## 1. 문서 상태와 결정

이 문서는 요구사항·설계·구현·테스트에서 발생하는 사용자 질문과 답변을 하나의 변경
계약으로 처리하기 위한 목표 설계다. 현재 운영 경로를 설명하는 문서가 아니라, 기존의
단계별 feedback gate, Workspace revision planner, RTM과 cascade를 어떤 공통 경계로
연결할지 정한다.

다음 결정을 채택한다.

1. 전역 workflow engine을 새로 만들지 않는다. 현재 고정된 파이프라인과 단계별 adapter를
   유지하고 `Question`, `Decision`, `ChangeSet` 계약만 공통화한다.
2. 클래스 설계 개선을 운영 경로에 통합하기 전에 이 공통 계약을 먼저 확정한다.
3. 피드백 시스템 전체를 별도 프로젝트로 완성한 뒤 클래스 설계로 돌아가지 않는다.
   `UC 명세 → class bundle → sequence`를 첫 실제 적용 경로로 사용한다.
4. RTM은 변경 영향의 후보와 검증 근거를 제공한다. 사용자 답변의 의미, 수정 권한과 최종
   실행 범위는 RTM만으로 결정하지 않는다.
5. 요구사항 의미 결정, 설계 선택, 생성 결함과 시스템 결함을 구분한다. 사용자에게는 실제
   제품 의미를 결정해야 할 때만 질문한다.
6. 선택지와 자유 답변은 같은 `Decision`으로 정규화한다. 모호한 답변은 승인으로 통과시키지
   않고 다시 명확화한다.
7. 변경 중간 상태는 재개할 수 있게 저장하되, 유효한 최신 산출물인 accepted head는 영향
   범위의 정합성이 확보됐을 때만 교체한다. 갱신하지 못한 downstream은 같은 변경에서
   `stale`로 표시하고 소비를 차단한다.

핵심 원칙은 **사용자 의미 결정과 생성 수리를 분리하고, 변경 전 영향 범위를 고정하며,
변경 후 추적 관계를 다시 증명하는 것**이다.

## 2. 해결하려는 문제

현재 각 단계에는 피드백을 처리하는 기능이 있지만 수명주기가 하나로 연결되어 있지 않다.

- 요구사항 feedback cascade는 상류 단계를 수정하면 뒤 단계를 새로 실행하는 선형 경로다.
- 요구사항 resource question은 유한 선택지를 결정론적으로 적용하지만, 일반적인 자유 답변을
  전역 revision으로 연결하는 공통 결정 기록은 아니다.
- Workspace revision planner는 의미 범위, 수정 권한, exact upstream link와 stale plan을
  검사하지만 모든 단계의 질문을 만들고 보존하는 주체는 아니다.
- 설계 RTM과 cascade는 정확한 설계 링크, 국소 merge와 결정론적 재투영을 지원하지만
  요구사항으로 돌아가는 변경은 Workspace의 별도 stage rewind에 의존한다.

이 상태에서 클래스 생성기만 질문 기능을 가지면 같은 문제가 반복된다.

- 클래스 단계에서 발견한 요구사항 공백을 operation repair로 덮을 수 있다.
- 같은 요구사항을 사용하는 다른 산출물에는 사용자의 결정이 반영되지 않는다.
- 사용자의 답변과 그 답변이 바꾼 authoritative artifact 사이의 계보가 남지 않는다.
- 상류 변경 뒤 어떤 checkpoint를 재사용할 수 있는지 설명하거나 검증하기 어렵다.
- 실패하면 이미 유효한 형제 작업까지 다시 호출하는 결함-수정 루프가 생긴다.

따라서 질문 UI 자체보다 먼저 답변을 변경으로 승격하는 공통 프로토콜이 필요하다.

## 3. 범위와 비범위

### 3.1 이번 개선의 범위

- 선택지와 자유 답변을 표현하는 공통 질문 계약
- 사용자 원문과 정규화된 의미를 보존하는 결정 기록
- 의미 소유권과 RTM 영향을 결합한 변경 계획
- 변경 전 revision·digest 고정과 실행 직전·publish 직전 stale 검사
- 단계별 `rebuild`, `revalidate`, `reproject`, `reuse`, `stale` 처리
- durable checkpoint, 중복 제출 방지와 멱등 재개
- accepted head와 변경 draft의 분리
- 첫 수직 경로의 요구사항→클래스→시퀀스 전파

### 3.2 이번 개선의 비범위

- 임의 노드와 동적 분기를 정의하는 범용 workflow DSL
- 모든 필드와 문장에 대한 완전한 dependency graph
- RTM 링크가 없는 관계를 LLM이나 이름 유사도로 추측하는 기능
- 클래스 effect·obligation ontology의 확장
- 사용자에게 schema·참조·타입 오류를 고치게 하는 기능
- 첫 수직 경로에서 모든 API·ERD·배포 산출물을 즉시 다시 생성하는 기능

최소 변경 단위는 이론적으로 가장 작은 필드가 아니라, 현재 추적 정보와 reviser가 안전하게
지원하는 단위 중 가장 작은 것으로 정의한다. 처음에는 `UC 명세`와 `class bundle`이면 충분하다.

## 4. 피드백이 필요한 문제의 분류

질문을 만들기 전에 finding을 다음 네 종류로 분류한다.

| 종류 | 예시 | 처리 |
|---|---|---|
| 요구사항 결정 | 철회 뒤 대기자를 자동 승급하는지 명시되지 않음 | 사용자에게 질문하고 authoritative requirements artifact를 revision |
| 설계 선택 | 같은 요구사항을 만족하는 내부 책임 배치가 여러 개임 | 설계 단계가 선택하고 근거·검증을 기록 |
| 생성 결함 | 존재하지 않는 operation ref, type mismatch, 잘못된 parent | 계약 소유 단위의 결정론 검사와 국소 repair |
| 검사기·시스템 결함 | validator 모순, schema transport 실패, checkpoint 손상 | 후보를 다시 생성하지 않고 시스템 실패로 종료·재개 |

사용자 질문은 첫 번째에만 필수다. 설계 선택을 모두 질문으로 바꾸면 사용자가 설계 validator가
되고, 생성 결함을 질문으로 바꾸면 모델의 오류 비용이 사용자에게 이전된다.

분류가 불가능하면 자동 repair하지 않고 `UNRESOLVED`로 남긴다. 분류 자체가 제품 의미에
달려 있다면 제한된 선택지로 명확화할 수 있다.

## 5. 공통 계약

### 5.1 Question

`Question`은 어느 실행에서 왜 질문이 생겼고 어떤 revision에 적용되는지를 고정한다.

```text
Question
  questionId
  questionVersion
  appId
  sourceExecutionId | draftId
  detectedAt: {stage, artifactRef, elementRef?}
  baseRevisions[]
  trigger: {category, findingRefs[], evidenceRefs[]}
  authorityCandidates[]
  prompt
  options[]
  allowFreeText
  blocking
  status
```

accepted artifact가 아직 없는 최초 생성 도중의 질문은 `sourceExecutionId` 또는 `draftId`로
출처를 식별한다. Question 상태는 `OPEN`, `ANSWERED`, `SUPERSEDED`, `DISMISSED`, `STALE`로
제한하며, ChangeSet의 실행 상태와 섞지 않는다.

각 option은 표시 문자열이 아니라 stable ID와 결정 payload를 가진다.

```text
QuestionOption
  optionId
  label
  description
  recommended
  decisionPayload
```

`decisionPayload`는 허용된 의미 schema와 target 종류를 만족하는 서버 소유 값이다. 사용자가
선택한 label을 다시 자연어 해석하지 않는다. `questionVersion`, `baseRevisions`와 적용 owner가
일치할 때만 결정론적으로 적용한다.

### 5.2 Decision

선택지와 자유 답변은 동일한 결정 기록으로 들어간다.

```text
Decision
  decisionId
  appId
  origin: question_answer | direct_feedback
  questionId?
  questionVersion?
  sourceUserMessageId
  answerMode: option | free_text
  selectedOptionId?
  rawAnswer
  normalizedMeaning
  authoritativeTargets[]
  preservedConstraints[]
  baseRevisions[]
  status
  supersedesDecisionId?
```

`direct_feedback`은 사용자가 질문을 거치지 않고 기존 산출물의 수정을 직접 요청한 경우다.
이때 가짜 Question을 만들지 않고 사용자 메시지, 명시 target과 요청 범위를 근거로 사용한다.
Decision 상태는 `RECEIVED`, `NORMALIZED`, `NEEDS_CLARIFICATION`, `SUPERSEDED`, `CANCELLED`로
관리한다.

`rawAnswer`는 변경하지 않는 사용자 원문이다. `normalizedMeaning`은 실행 가능한 제한된 의미
계약이다. 자유 답변은 LLM이 정규화 후보를 만들 수 있지만 다음 항목은 코드가 검증한다.

- 질문과 답변의 revision 대응
- target kind와 stable ID의 존재
- ownership registry가 허용하는 authoritative target
- 질문이 허용한 의미 schema와 범위
- 코드로 표현된 보존 제약

자유 답변의 의미가 하나로 좁혀지지 않거나 질문 범위를 넘어가면 `Decision`을 실행하지 않고
새 clarification question을 만든다. 모호한 문장을 사용자에게 그대로 승인받는 방식은 의미를
확정하지 못하므로 허용하지 않는다. 코드로 판정할 수 없는 비구조화 의미 충돌은 사용자 원문과
근거를 포함한 명확화 또는 제한된 검토 대상으로 남긴다. 정규화 confidence가 높다는 이유로
사용자가 승인한 의미나 변경 범위를 넓히지 않는다.

### 5.3 ChangeSet

`ChangeSet`은 하나의 결정이 어떤 revision과 downstream 유효성에 영향을 주는지 고정한다.

```text
ChangeSet
  changeSetId
  appId
  decisionId
  baseHead
  baseArtifactVersions[]
  preChangeTraceDigest
  authoritativeTargets[]
  executionUnits[]
  unresolvedImpact[]
  planVersion
  planDigest
  approvedPlanDigest?
  approvalEvidence?
  status
  checkpoints[]
  publishedHead?
```

execution unit은 계획과 checkpoint가 공통으로 참조하는 명시적 단위다.

```text
ExecutionUnit
  executionUnitId
  owner
  targets[]
  action
  producerRefs[]
  dependsOnUnitIds[]
  expectedInputDigests[]
  status
```

재계획하면 `planVersion`을 증가시키고 이전 승인과 아직 실행하지 않은 unit의 실행 예약을
무효화한다. 완료 checkpoint는 근거로 보존하며 새 계획의 input digest와 schema·prompt·validator
version을 다시 확인한 뒤에만 재사용한다. 승인이 필요한 계획은 실제 승인된 `planDigest`와
근거를 저장한다.

각 execution unit은 다음 동작 중 하나만 가진다.

| 동작 | 의미 | 완료 증거 |
|---|---|---|
| `rebuild` | 입력 의미가 바뀌어 기존 결과를 재생성 | 새 input/output digest와 validator 결과 |
| `revalidate` | 기존 결과를 새 입력에서 다시 검사 | validator version과 통과 결과 |
| `reproject` | accepted source 또는 같은 ChangeSet에서 검증된 새 source revision을 순수 변환 | source/target digest와 projection version |
| `reuse` | 입력이 동일하거나 안전성이 이미 증명됨 | 동일 input digest 또는 지원 단위의 재검증 |
| `stale` | 아직 갱신·검증되지 않아 소비할 수 없음 | stale 원인과 producer revision |

RTM에 항목이 없다는 이유만으로 `reuse`를 선택할 수 없다. 추적이 부족하면 지원되는 더 큰
단위로 확대하거나 `unresolvedImpact`와 `stale`로 기록한다.

같은 ChangeSet의 검증된 draft를 입력으로 만든 projection도 publish 전까지 draft다. 새 class
revision을 먼저 공개한 뒤 sequence를 검사하지 않는다.

## 6. RTM과 수정 권한의 경계

RTM은 별도 LLM 호출로 만들지 않는다. 각 산출물이 보유한 provenance와 exact contract link를
순수 함수로 집계하는 현재 원칙을 유지한다.

RTM이 담당하는 일은 다음과 같다.

1. 변경 전 영향 후보와 exact relation을 조회한다.
2. 사용자에게 예상되는 upstream·downstream 범위를 설명한다.
3. `ChangeSet.executionUnits`의 후보를 만든다.
4. 변경 후 orphan, unknown ref와 새 의존성을 찾는다.
5. 계획보다 영향이 넓어졌는지 검사한다.

RTM이 담당하지 않는 일은 다음과 같다.

1. 자연어 답변의 의미 결정
2. provenance를 수정 권한으로 승격
3. 여러 authority 후보 중 하나를 자동 선택
4. 누락된 edge를 이름 유사도나 LLM 추정으로 생성
5. 현재 reviser보다 더 세밀한 수정 단위를 약속

`impact`와 `authority`는 별도 개념이다. broad provenance는 forward invalidation 근거가 될 수
있지만 그 자체로 upstream 수정 권한이 되지 않는다.

기존 파생 산출물에서 authority를 역추적할 때는 등록된 ownership rule과 exact contract link를
사용한다. 반면 새 specification gap에는 아직 exact link가 없을 수 있다. 질문 생성 단계가
명시한 UC 명세 owner를 사용자가 의미 결정 대상으로 선택한 경우에는 해당 owner의 존재,
revision과 지원 adapter를 검증한 뒤 직접 authoritative target으로 계획할 수 있다. 이는 기존
RTM provenance를 권한으로 승격하는 경로가 아니라, typed question과 사용자 Decision에서 새
요구사항 authority를 확정하는 별도 경로다.

현재 `RevisionPlanner`에는 class specification gap을 UC 명세로 올리는 ownership rule이 없으므로
첫 수직 경로에서 `specification gap → UC spec owner` routing과 adapter를 명시적으로 추가해야
한다. 현재 planner 연결만으로 지원된다고 가정하지 않는다.

RTM의 표시 행, 영향 순회 노드와 revision adapter의 merge 단위는 서로 다를 수 있다. 요구사항
RTM은 step·guarantee·constraint 등 세밀한 provenance를 포함하고 설계 RTM에도 operation·call·
binding 행이 있지만, 그 행이 모두 독립된 전파·merge 단위라는 뜻은 아니다. 첫 수직 경로는
UC 명세와 안전한 class bundle을 revision 단위로 사용한다.

### 6.1 변경 전후 추적

계획할 때 current accepted head의 RTM, artifact versions와 trace digest를 고정한다. 변경 뒤에는
새 RTM을 만들고 다음을 비교한다.

- 삭제·rename으로 사라진 이전 edge
- 새 산출물에서 생긴 dependency
- plan에 없던 새 영향 대상
- target remap 실패와 orphan·unknown ref
- `reuse`로 분류했지만 producer digest가 달라진 항목

새 영향이 plan 범위를 넘으면 자동으로 mutation 범위를 넓히지 않는다. 안전한 revalidation만
수행할 수 있으면 추가하고, 수정 권한이나 LLM 재생성이 필요하면 `STALE` 또는 `REPLAN_REQUIRED`
상태로 전환한다.

`REPLAN_REQUIRED`에서는 실행 중인 unit을 중단하고 새 pre-change snapshot과 plan version을
만든다. 이전 계획의 승인과 미실행 unit의 실행 예약은 새 계획에 승계하지 않는다. 이미 만든
draft와 완료 checkpoint는 근거 기록으로 보존하되 새 계획의 입력 digest와 schema·prompt·
validator version이 일치하는지 다시 검증하기 전에는 재사용하지 않는다.

### 6.2 dependency 보완

현재 RTM 행을 전부 범용 그래프로 바꾸지 않는다. 재개와 재사용에 필요한 최소 메타데이터만
execution unit 또는 artifact metadata에 추가한다.

```text
DependencyRecord
  consumerUnit
  producerRef
  producerRevisionOrDigest
  relation: derives_from | uses_contract | projects
```

이 기록은 provenance를 복제하기 위한 것이 아니라, 특정 checkpoint가 어떤 입력 revision에서
검증됐는지 증명하기 위한 것이다.

## 7. 계획·확인·실행 상태

공통 상태 흐름은 다음과 같다.

```text
ISSUE_IDENTIFIED
  → QUESTION_OPEN
  → ANSWER_RECEIVED
  → DECISION_NORMALIZED
  → CHANGE_PLANNED
  → READY | NEEDS_CLARIFICATION | NEEDS_CONFIRMATION | UNSUPPORTED
  → REVISING
  → REVALIDATING_AND_REBUILDING
  → COMMITTED
```

실행 중에는 `FAILED_RETRYABLE`, `FAILED_UNRESOLVED`, `STALE`, `REPLAN_REQUIRED`, `CANCELLED`로
전환할 수 있다.

`NEEDS_CLARIFICATION`과 `NEEDS_CONFIRMATION`은 다르다.

- 의미가 하나로 정해지지 않았으면 clarification이 필요하다.
- 의미는 정해졌지만 사용자가 허용한 범위를 넘어 다른 authoritative artifact를 바꿔야 하면
  영향 범위를 보여주고 confirmation을 받는다.
- 사용자가 처음부터 upstream 변경과 관련 설계 갱신을 명시했다면 같은 범위를 다시 확인하지
  않는다.

실행 직전에는 artifact versions와 trace digest를 다시 검사한다. 질문이 열린 뒤 입력이 바뀌면
오래된 답변을 새 상태에 적용하지 않는다. publish 직전에는 `baseHead`가 여전히 현재 head인지
compare-and-swap으로 다시 검사하여 긴 실행 중의 경쟁 변경도 차단한다.

## 8. 저장, 원자성, 재개

변경 draft와 accepted head를 구분한다.

- `Decision`, plan, checkpoint와 부분 산출물은 durable draft로 저장한다.
- draft 실패는 기존 accepted head를 변경하지 않는다.
- 영향받은 산출물이 모두 갱신되거나 명시적으로 stale 분류되면 artifact revision과 validity를
  함께 가진 manifest를 새 accepted head로 공개한다.
- 첫 구현에서 일부 downstream을 즉시 다시 만들지 않는다면, 새 upstream과 함께 같은
  transaction에서 해당 downstream을 `stale`로 표시한다.
- `stale` 산출물은 화면·export·후속 구현 단계에서 최신 입력으로 소비할 수 없다.

`baseHead` compare-and-swap, 새 artifact manifest, 각 artifact의 validity, ChangeSet의
`COMMITTED` 상태와 `publishedHead` 기록은 같은 repository transaction에서 저장한다. commit은
성공했지만 응답 전에 프로세스가 종료된 경우, 재개 경로는 `publishedHead`를 조회하여 중복
publish 없이 완료로 복원한다.

checkpoint는 완료 여부만 저장하지 않는다.

```text
Checkpoint
  changeSetId
  executionUnitId
  action
  inputDigests[]
  outputDigest?
  schemaPromptValidatorVersions
  validationResult
  attempts
  failureCategory?
  status
```

같은 `Decision` 또는 `ChangeSet`의 중복 제출은 같은 멱등성 키로 처리하여 병렬 실행과 중복
revision commit을 막는다. 재개할 때 완료가 기록된 unit의 실제 input digest와 validator
version을 다시 확인하고, 같으면 원격 호출 없이 재사용한다.

Provider가 응답한 직후 checkpoint를 저장하기 전에 프로세스가 종료되면 물리적 LLM 호출의
완료 여부를 일반적으로 증명할 수 없다. 이 구간은 `ATTEMPT_OUTCOME_UNKNOWN`으로 기록하고,
provider의 idempotency 또는 응답 회수 지원 범위에서 재개한다. 불가피한 재호출은 새 attempt로
기록하고 공유 호출·시간·token 예산에 포함한다. 원격 호출의 exactly-once를 보장한다고
표현하지 않는다.

## 9. 첫 수직 적용 경로

첫 구현은 다음 사례 하나를 완결한다.

```text
클래스 설계에서 specification gap 발견
  → 선택지와 자유 답변이 있는 질문
  → 특정 UC 명세를 authoritative owner로 확정
  → UC 명세 새 revision
  → RTM으로 관련 class bundle 영향 계산
  → 구조·operation·Collaborations rebuild 또는 revalidate
  → 같은 class revision에서 sequence를 LLM 없이 reproject
  → 나머지 영향 downstream은 재사용 근거를 검증하거나 stale 처리
  → post-change RTM·참조·보존 검사
  → accepted head publish
```

`class bundle`은 함께 수락해야 하는 클래스 구조, operation catalog와 Collaborations의 안전한
merge 단위다. RTM에 operation과 call 행이 있더라도 현재 reviser가 그 행만 독립적으로 merge할
수 없다면 더 작은 단위로 표시하지 않는다.

첫 경로에서 자동 갱신하는 산출물은 UC 명세, class bundle과 sequence로 제한한다. API 재생성
adapter까지 완성할 필요는 없지만, 기존 API·ERD·배포·구현·테스트가 있으면 영향 분석 결과에
따라 재사용 근거를 검증하거나 stale 처리한다. API는 필수 stale 테스트의 대표 사례다. 이
경로가 안정된 뒤 API, ERD, 배포, 구현과 테스트 adapter를 같은 프로토콜에 순차 연결한다.

최초 class 생성 중 gap을 발견하면 pre-change RTM에 class·call edge가 없을 수 있다. 첫 구현은
accepted UC 명세를 기준으로 하고, class bundle이 아직 없으면 생성 unit을 미완료 draft로 둔 뒤
새 UC revision에서 재개한다. 기존 accepted class가 있는 경우에만 frozen RTM을 이용한 영향
재검증과 부분 보존을 수행한다.

## 10. 클래스 설계 개선과의 관계

[클래스 설계 실행 증거와 원자적 수락 개선안](class-design-executable-behavior-contract-plan.md)의
격리 프로토타입은 폐기하지 않는다. 다음 요소는 공통 피드백 프로토콜의 첫 소비자에 필요한
기반이므로 유지한다.

- 클래스 구조와 Collaborations의 동일 revision 수락
- schema·type·parent·binding의 결정론 검사
- owner-scoped patch와 무관한 요소 보존
- input/output digest, validator version과 checkpoint
- 생성 결함, specification gap, provider failure와 시스템 결함의 구분
- accepted behavior에서 sequence를 만드는 순수 projection
- typed evidence와 finding ledger

다음 작업은 피드백 수직 경로가 확정될 때까지 보류한다.

- effect·obligation ontology의 추가 확장
- 클래스 생성기 내부의 별도 범용 질문·revision 체계
- 모든 execution group을 독립적으로 봉인하기 위한 추가 추상화
- 불안정한 운영 구조를 전제로 한 모델별 세부 latency 최적화
- 의미가 불명확한 값을 기본값이나 repair 규칙으로 채우는 기능

클래스 개선은 중단되는 것이 아니라, 공통 `Question`, `Decision`, `ChangeSet`의 producer와
reviser로 운영 통합 순서를 조정한다.

## 11. 구현 순서

1. `Question`, `Decision`, `ChangeSet`, execution action과 accepted/stale 의미를 확정한다.
2. 현재 capability choice, requirements gate, revision planner와 design cascade의 대응표를 만들고
   `specification gap → UC spec owner` ownership rule·adapter를 추가하며 중복 상태·명령 경로를
   제거한다. 현재 capability answer의 pending 검사는 재사용하되, 새 question version과 base
   revision 검증은 version-pinned envelope에서 추가한다.
3. 질문·결정·변경 계획을 durable하게 저장하고 stable option ID와 중복 제출을 검사한다.
4. 자유 답변 정규화와 deterministic target·authority·version 검증을 연결한다.
5. frozen pre-change RTM에서 execution unit을 계획하고 각 unit을
   `rebuild/revalidate/reproject/reuse/stale`로 분류한다.
6. scripted producer 또는 격리 adapter로 `UC 명세 → class bundle → sequence` 프로토콜과
   checkpoint 재개를 검증한다.
7. post-change RTM 비교, target remap, orphan·unknown ref와 plan extension을 검사한다.
8. compare-and-swap accepted publish와 stale downstream 소비 차단을 구현한다.
9. crash, 중복 제출, stale answer, 부분 실패와 경쟁 revision 테스트를 통과시킨다.
10. API adapter부터 나머지 단계로 확대한다.
11. 검증된 프로토콜 adapter 뒤에 격리 클래스 프로토타입을 실제 운영 생성·피드백 경로로
    연결하고, 기존 운영 클래스 생성기를 대체한 뒤 품질·호출 실험을 재개한다.

## 12. Luna·Terra 서브에이전트 활용

서브에이전트는 모델별로 전체 기능을 나누지 않고, 변경 책임과 파일 소유권이 겹치지 않는
검증 가능한 단위로 사용한다. 공통 계약이 바뀌는 동안 여러 에이전트가 planner, repository와
단계별 adapter를 동시에 수정하지 않는다.

### 12.1 역할 분리

| 역할 | 주 책임 | 직접 수정하지 않는 경계 |
|---|---|---|
| Luna | 질문·답변 envelope, 기존 stage 입력으로의 순수 adapter, UI read model, fixture와 단위 테스트 | ownership 계산, RTM 순회, DB migration, checkpoint 저장, CAS publish |
| Terra | ChangeSet·ExecutionUnit·manifest 계약, ownership routing, RTM pre/post diff, durable repository, transaction과 수직 통합 테스트 | 클래스 생성 세부, stage별 LLM prompt·repair, UI 표시 로직 |
| 주 에이전트 | 공통 필드와 파일 소유권 확정, handoff 승인, 변경 통합과 전체 회귀 검사 | 계약 미확정 상태에서 서로 의존하는 작업의 동시 위임 |
| Astra | 계약 확정 전 반례 검토와 수직 경로 완료 후 불변식 감사 | 구현 파일의 상시 공동 소유 |

Luna와 Terra는 같은 파일을 공동 소유하지 않는다. 작업을 시작할 때 담당 파일과 수정 금지
파일을 명시하며, 다른 에이전트의 변경을 되돌리지 않고 고정된 계약에 맞춰 자기 구현을
조정한다.

### 12.2 Luna 작업 단위

Luna에는 입력·출력 schema가 이미 고정된 작은 작업을 맡긴다.

1. **질문·답변 envelope와 단위 테스트**
   - `QuestionOption`, `Question`과 `Decision`의 사용자 입력 부분을 순수 모델·helper로 구현한다.
   - stable option ID, question version, base revisions, raw answer 보존과 허용 target을 검사한다.
   - 선택지는 label을 재해석하지 않고 decision payload로 변환한다.
   - 자유 답변은 정규화 결과가 없거나 target/schema 범위를 벗어나면
     `NEEDS_CLARIFICATION`으로 남긴다.
   - 저장소, 현재 head와 LLM provider를 직접 호출하지 않는다.
2. **stage adapter wrapper**
   - 검증된 `Decision`과 Terra가 확정한 `ExecutionUnit`을 기존 requirements/design delivery
     payload로 바꾸는 순수 변환을 구현한다.
   - `rebuild`, `revalidate`, `reproject`, `reuse`, `stale` 중 한 동작만 허용한다.
   - target 문자열을 추측하지 않고 catalog가 제공한 kind와 stable ID만 사용한다.
   - 기존 delivery·cascade의 동작은 바꾸지 않고 wrapper와 회귀 테스트를 먼저 만든다.
3. **UI read model과 stale 표시**
   - backend 계약이 고정된 뒤 Question·Decision·ChangeSet 상태를 화면 모델로 투영한다.
   - option 제출에는 표시 label이 아니라 stable option ID를 유지한다.
   - stale artifact와 오래된 질문은 최신 결과나 실행 가능한 질문으로 표시하지 않는다.
   - UI에서 stale을 재판정하거나 유효 상태로 승격하지 않는다.

각 Luna 작업은 fixture, fail-closed 사례와 기존 회귀 테스트를 완료 산출물로 포함한다. Luna가
작업 중 ownership 또는 transaction 변경이 필요하다고 발견하면 범위를 확장하지 않고 typed
finding과 필요한 Terra 계약을 반환한다.

### 12.3 Terra 작업 단위

Terra에는 여러 stage에 걸친 정합성과 commit 경계를 맡긴다.

1. **ChangeSet 핵심 계약과 ownership routing**
   - `ChangeSet`, `ExecutionUnit`, `Checkpoint`, `AcceptedManifest`의 canonical schema를 소유한다.
   - 기존 `OwnershipRegistry`를 fail-closed로 확장하고
     `specification gap → UC spec owner` 경로를 추가한다.
   - provenance, exact link, typed question authority를 서로 다른 권한 근거로 보존한다.
2. **RTM snapshot과 pre/post diff**
   - 현행 requirements/design RTM 생성은 유지하고, frozen RTM과 artifact revision을
     ChangeSet 입력으로 고정하는 adapter를 구현한다.
   - removed/new link, orphan, unknown ref, `reuse` 아래 producer digest 변경과 계획 밖 영향을
     순수하게 계산한다.
   - 계획 밖 LLM mutation을 자동 추가하지 않고 `REPLAN_REQUIRED` 또는 `stale`로 반환한다.
3. **영속 저장과 publish**
   - Decision·ChangeSet draft, unit checkpoint와 원격 attempt 상태를 저장한다.
   - 멱등성 키, 완료 unit 재사용, `ATTEMPT_OUTCOME_UNKNOWN`과 재개 정책을 구현한다.
   - base head CAS, artifact revision·validity manifest와 `COMMITTED/publishedHead`를 하나의
     transaction에서 저장한다.
4. **첫 수직 통합**
   - Luna adapter의 검증된 결과를 받아 UC revision, class bundle, sequence projection을
     ChangeSet으로 실행한다.
   - crash, 중복 제출, stale answer, RTM plan escape와 publish 경쟁 조건을 통합 테스트한다.

Terra는 단계별 LLM 생성기나 UI를 고치지 않는다. 공통 계약이 요구하는 새 필드가 필요하면
먼저 계약 변경을 제안하고 주 에이전트가 Luna handoff와 fixture를 갱신한 뒤 통합한다.

### 12.4 순서와 병렬화

```text
주 에이전트 + Astra: 최소 계약과 불변식 동결
  → Luna: question/decision envelope + fixture
  → Terra: ChangeSet/ownership/RTM pure planning
  → Terra: repository/checkpoint/CAS publish
     || Luna: 고정된 계약의 stage adapter와 UI read model
  → Terra: UC spec → class bundle → sequence 수직 통합
  → 주 에이전트: 전체 회귀·운영 경로 통합
  → Astra: 최종 불변식 감사
```

첫 두 작업은 필드 이름과 ownership 의미가 확정될 때까지 병렬로 수정하지 않는다. Terra의
backend JSON과 execution action이 동결된 뒤에는 repository 구현과 Luna의 adapter·read model을
병렬화할 수 있다.

handoff마다 다음을 기록한다.

- 사용한 contract·schema·validator version
- 담당 파일과 수정하지 않은 파일
- input fixture와 output digest
- 통과한 테스트와 아직 unsupported인 사례
- downstream이 재사용할 수 있는 산출물과 stale인 산출물

서브에이전트의 로컬 테스트 통과는 전역 수락이 아니다. Terra의 수직 통합과 주 에이전트의
전체 회귀를 통과하고 Astra가 authority·RTM·publish 불변식을 확인한 뒤에만 첫 경로를 완료로
본다.

## 13. 수용 조건

### 13.1 질문과 결정

- 선택지는 stable option ID, question version, base revision과 결정 payload를 가진다.
- 자유 답변 원문과 정규화 의미가 모두 보존된다.
- 의미가 여러 개인 답변은 revision을 실행하지 않고 명확화 질문으로 돌아간다.
- 생성 결함과 provider·검사기 실패를 사용자 의미 질문으로 잘못 전환하지 않는다.
- 해결된 질문은 서버 재시작 뒤 다시 묻지 않는다.

### 13.2 계획과 RTM

- 모든 변경 계획이 frozen artifact versions와 pre-change trace digest를 가진다.
- RTM provenance만으로 upstream mutation authority를 부여하지 않는다.
- RTM 링크 누락을 영향 없음이나 `reuse`로 해석하지 않는다.
- 삭제·rename은 pre-change RTM과 target remap으로 검사한다.
- 새 dependency가 계획 범위를 넘으면 재계획하거나 명시적으로 stale 처리한다.

### 13.3 실행과 재개

- 같은 Decision과 ChangeSet의 중복 제출이 병렬 실행이나 중복 revision commit을 만들지 않는다.
- 완료 checkpoint가 있는 unit은 재호출하지 않으며, 결과가 불명확한 원격 attempt의 재호출은
  별도 attempt와 예산으로 기록한다.
- 오래된 질문 답변과 stale plan이 현재 artifact에 적용되지 않는다.
- 완료 unit은 input digest와 validator version이 같을 때만 재사용한다.
- 실패 뒤에는 완료된 무관 unit을 다시 생성하지 않고 실패 단위부터 재개한다.
- 관련 없는 UC와 class bundle의 payload가 보존된다.
- 공유 operation 변경은 이를 사용하는 모든 Collaborations를 적어도 재검증한다.
- sequence 재투영에는 LLM 호출이 없다.

### 13.4 공개와 소비

- draft 실패가 기존 accepted head를 변경하지 않는다.
- publish 직전 base head가 달라졌으면 commit을 거부하고 재계획한다.
- accepted head는 artifact revision과 validity를 함께 보유한 manifest다.
- 새 upstream과 낡은 downstream을 모두 최신인 하나의 manifest로 공개하지 않는다.
- 갱신하지 않은 downstream은 같은 transaction에서 stale이 되며 화면·export·후속 단계가
  최신 산출물로 소비할 수 없다.
- post-change RTM의 새 unknown ref와 orphan이 성공 결과에서 조용히 무시되지 않는다.

## 14. 첫 완료 기준

첫 구현은 다음 문장이 실제 테스트로 성립할 때 완료다.

> 설계 중 받은 사용자 답변이 특정 UC 명세를 변경하면, 동일 결정을 중복 commit하지 않고 관련
> class bundle과 sequence만 새 revision으로 갱신하며, 실패 후 재개해도 무관한 산출물과 기존
> 유효 결과를 보존하고 미갱신 downstream은 명시적으로 stale 처리한다.

이 기준은 모든 피드백 문제를 일반화한 완성형 workflow를 뜻하지 않는다. 기존 고정 파이프라인의
첫 cross-stage revision을 안전하게 완결하는 최소 공통 척추다. 이 경계가 검증된 뒤에만 나머지
단계와 클래스 실행 의미 모델을 확대한다.
