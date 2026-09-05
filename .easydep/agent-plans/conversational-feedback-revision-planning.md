# ConversationAgent 기반 대화형 피드백 및 변경 계획 개선

- 상태: 구현 완료 (실제 typed mutation 경로 기준, 지원하지 않는 경로는 fail-closed)
- 작성일: 2026-09-05
- 구현일: 2026-09-06
- 범위: Workspace 대화, 산출물 target 해석, 선행 단계 승인, RTM 기반 부분 수정과 cascade
- 주요 대상: `app/workspace/conversation`, `app/workspace/service.py`, `app/artifact_trace*`,
  `app/design/rtm.py`, `app/design/cascade.py`, 단계별 feedback service
- 관련 문서: `artifact-rtm-simplification.md`, `stage-lifecycle-consistency.md`
- 저장 정책: 새 DB 테이블이나 컬럼을 추가하지 않고 기존 artifact JSON과
  `workspace_commands.payload`를 사용한다.

이 문서는 별도의 FeedbackAgent를 추가하거나 다이어그램 생성 파이프라인을 다시 만드는 계획이
아니다. 현재 `ConversationAgent`, `ProjectTools`, 통합 `ArtifactTrace`, 설계 RTM과 단계별 revision
service를 유지하면서, 자연어 수정 요청을 안전한 변경 계획으로 바꾸는 경계를 보완한다.

## 1. 결론

대화형 피드백은 다음 두 단계를 분리한다.

```text
계획 단계: 자연어 해석 → 대상 검증 → authoritative owner 결정 → 영향 범위 계산
실행 단계: 사용자 승인 확인 → 버전 재검증 → 승인된 대상 수정 → 하위 cascade
```

LLM은 사용자의 자연어가 무엇을 뜻하는지 해석하고, 서버가 제공한 유한한 후보 중 target을
선택한다. 선행·후행 산출물 탐색, 수정 소유 단계, 버전 유효성, 실제 실행 범위는 일반 코드가
결정한다.

사용자가 보고 있는 단계보다 앞선 산출물을 수정해야 하거나, 명시한 target 밖으로 수정 권한을
넓혀야 할 때에는 실행 전에 반드시 변경 계획을 보여 주고 승인을 받는다. 승인을 받기 전에는
artifact, checkpoint, image cache를 변경하지 않는다.

선행 단계 수정이 승인되더라도 전체 stage rewind를 기본으로 삼지 않는다. 정확한 element ref와
부분 reviser가 있으면 선행 요소만 수정하고 RTM으로 확인된 하위 요소만 갱신한다. 부분 수정이
지원되지 않는 경우에만, 다시 생성될 단계와 산출물을 명시한 뒤 stage rewind 승인을 받는다.

## 2. 현재 유지할 기반

### 2.1 ConversationAgent의 제한된 역할

현재 `ConversationAgent`는 일반 답변, 프로젝트 질문, 명령과 clarification을 구분한다. 수정
요청에서는 프로젝트 전체를 모델에 맡기지 않고 `ProjectTools.search_elements()`가 만든 유한한
후보에서 ref를 선택한다. 선택한 ref는 다시 `validate_targets()`를 통과해야 한다.

이 경계는 유지한다. LLM이 stage, file, ref 또는 영향 범위를 자유롭게 만들어 내도록 바꾸지 않는다.

### 2.2 버전이 고정된 target 검증

`ProjectTools`의 element catalog는 artifact version ID와 canonical ref를 함께 제공한다. 현재
artifact에 존재하지 않거나 오래된 version을 가리키는 target은 실행할 수 없다.

새 변경 계획도 이 version 정보를 그대로 사용한다. 승인 화면을 보고 있는 동안 다른 수정이 먼저
저장되면 이전 계획을 실행하지 않고 최신 snapshot으로 다시 계획한다.

### 2.3 통합 ArtifactTrace

`ArtifactTrace`는 requirements, design, implementation, testing의 typed artifact가 이미 갖고 있는
직접 출처를 읽어 `sources`, `consumers`, `upstream`, `downstream`을 계산한다. 새 graph DB나 별도
RTM 저장소를 만들지 않는다.

다만 모든 upstream 항목을 수정 후보로 간주하지 않는다. provenance는 “이 산출물이 무엇에서
왔는가”를 설명하지만, 역방향 수정을 허가하는 정보는 아니기 때문이다. 역방향 수정은 아래에서
정의할 ownership rule과 정확한 contract link가 함께 있을 때만 계획한다.

### 2.4 설계 RTM과 부분 cascade

설계 RTM은 class 변경의 하위 영향을 전이적으로 계산하고, sequence call·API Control binding·class
operation이 정확히 일치할 때만 역방향 contract link를 만든다. `revise_and_cascade()`는 변경 전
RTM을 고정하고, LLM이 새 ref를 만들어 실행 중 수정 범위를 넓히지 못하게 한다.

또한 `merge_model()`과 `assert_untargeted_elements_preserved()`가 비대상 요소 보존을 보장한다. 이
보장은 새 planner를 도입한 뒤에도 유지한다.

## 3. 해결할 문제

### 3.1 수정 의도와 실행 계획이 같은 명령에 섞여 있다

현재 `CommandIntent`의 수정 정보는 target 문자열과 instruction 중심이다. target이 검증되면
Workspace router가 owner를 고르고 기존 revision service로 전달한다. 사용자가 요청한 변경이 현재
요소에 국한되는지, 선행 contract까지 바꾸어야 하는지를 나타내는 중간 계획은 없다.

따라서 정확한 RTM link가 있더라도 사용자에게 범위를 보여 주기 전에 선행 산출물 수정이 실행될 수
있다.

### 3.2 RTM 연결과 변경 권한이 구분되지 않는다

다음 두 질문은 다르다.

1. 이 sequence message가 어느 class operation에서 왔는가?
2. sequence message 수정 요청이 그 class operation을 바꾸라는 뜻인가?

첫 번째는 RTM으로 결정론적으로 답할 수 있다. 두 번째는 요청의 의미와 산출물 소유권을 함께 봐야
한다. 단순히 upstream을 모두 순회하면 요구사항까지 과도하게 수정할 수 있고, 이름 유사성으로
고르면 근거 없는 변경이 된다.

### 3.3 design RTM만으로 네 delivery stage를 모두 되돌릴 수 없다

설계 `change_plan`은 설계 화면에서 직접 수정 가능한 요소를 대상으로 한다. 요구사항 use case는
설계 산출물의 source로 보일 수 있지만 설계 change target에는 포함되지 않는다. Implementation
file과 Testing evidence까지 포함한 전체 범위는 통합 `ArtifactTrace`를 사용해야 한다.

### 3.4 표시 문자열이 element identity 역할까지 맡는다

`class_diagram:OrderService::place(orderId:UUID)`처럼 사람이 읽는 signature가 canonical target에
포함되면 이름이나 parameter 수정 후 같은 요소를 계속 추적하기 어렵다. call 순번 기반 ID도 중간
삽입에 따라 달라질 수 있다.

현재 version 검증 덕분에 잘못된 snapshot을 수정하는 문제는 막을 수 있지만, 수정 직후의 후속 대화와
rename 결과 매핑에는 더 안정적인 ID가 필요하다.

### 3.5 전체 rewind가 부분 수정 대신 사용될 수 있다

“이전 단계로 돌아간다”는 요구를 항상 stage rewind로 구현하면 승인된 형제 요소와 관계없는 하위
산출물까지 다시 생성된다. element-scoped reviser가 있는 단계에서는 authoritative element만
수정하는 편이 맞다.

## 4. 목표와 비목표

### 4.1 목표

1. 자연어 수정 요청을 target과 의미 범위가 있는 작은 typed intent로 변환한다.
2. 현재 artifact version에 존재하는 ref만 계획 대상으로 사용한다.
3. 산출물 종류와 변경 종류에 따라 authoritative owner를 코드로 결정한다.
4. exact RTM/trace edge만 사용해 선행 후보와 하위 영향을 계산한다.
5. 선행 단계 또는 명시 범위 밖 수정은 실행 전에 사용자 승인을 받는다.
6. 승인된 계획과 실행 시점의 artifact version이 다르면 실행하지 않는다.
7. 선행 요소를 수정한 뒤에는 확인된 downstream만 다시 만들거나 오래된 상태로 표시한다.
8. 비대상 요소 보존과 batch atomicity를 유지한다.
9. 결과에서 변경한 단계, 요소, 이유와 재생성 범위를 사용자에게 보여 준다.
10. “아까 그 메서드”, “방금 바꾼 호출” 같은 후속 대화를 새 target으로 이어갈 수 있게 한다.

### 4.2 이번 계획의 비목표

- 별도의 범용 FeedbackAgent 또는 supervisor 추가
- LLM이 자유 형식 stage 이름, ref, file path나 RTM edge 생성
- graph database, 범용 query language, 정책 DSL 도입
- 모든 수정 요청을 사용자 확인 대상으로 만드는 것
- 요구사항부터 Testing까지 무조건 전체 재실행
- class/sequence LLM 출력 계약과 PlantUML renderer 전면 개편
- `fields: list[str]`, type expression, `sourceRef` 문법의 일괄 재설계
- 과거의 모든 실험 checkpoint를 위한 복잡한 데이터 migration

## 5. 책임 경계

| 판단 또는 작업 | 책임 |
|---|---|
| 발화가 질문인지 수정인지 구분 | `ConversationAgent` |
| 자연어에서 직접 target과 의미 범위 추출 | `ConversationAgent`의 제한된 structured output |
| 후보 검색과 canonical target 확정 | `ProjectTools` / `TargetResolver` |
| artifact version, 존재, editability 확인 | 일반 코드 |
| 사실의 authoritative owner 결정 | `OwnershipRegistry` |
| exact upstream 후보와 downstream 계산 | `ArtifactTrace`와 stage별 RTM |
| 선행 단계로 범위 확대 승인 | 사용자 |
| target 내용 수정 | 해당 stage의 기존 revision service |
| 비대상 보존, validator, renderer | 기존 deterministic 코드 |
| 하위 cascade 또는 stale 처리 | `RevisionExecutor`와 stage service |

LLM이 `requirements`나 `design` 같은 실행 stage를 최종 결정하지 않는다. LLM은 사용자의 표현을
`presentation`, `contract`, `behavior`, `implementation`, `test_expectation`, `unknown` 같은 작은 의미
범위로 정규화하고, 코드는 target kind와 ownership rule로 owner를 결정한다.

## 6. 목표 흐름

```text
사용자 자연어
  ↓
ConversationAgent
  - reply / question / command / clarification 분류
  - revision이면 유한한 candidate ref 선택
  - 요청의 의미 범위만 structured output으로 제안
  ↓
RevisionPlanner (read-only)
  - target/version 검증
  - authoritative owner 계산
  - exact upstream contract 후보 조회
  - frozen RTM에서 downstream 계산
  - local patch / upstream confirmation / clarification / rewind 필요 판정
  ↓
┌──────────────────────────────────────────────┐
│ local patch이며 명시 target 안쪽임           │ → 바로 실행 가능
│ 앞 단계 또는 target 밖으로 권한이 확대됨     │ → 변경 계획을 보여 주고 승인 대기
│ 의미나 exact link가 부족함                    │ → clarification
│ 부분 reviser가 없고 stage 재생성이 필요함     │ → 재생성 범위를 보여 주고 승인 대기
└──────────────────────────────────────────────┘
  ↓ 승인
RevisionExecutor
  - plan snapshot/version 재검증
  - 승인된 authoritative target만 수정
  - validator와 비대상 보존 검사
  - 확인된 downstream만 cascade
  - 새 artifact version과 target remap 반환
```

## 7. 새 계약

정확한 이름은 구현 시 기존 schema naming에 맞출 수 있지만 책임은 아래처럼 나눈다.

### 7.1 RevisionTarget

공개 수정 경계에서 문자열 ref만 전달하지 않고, 검증된 metadata를 함께 보존한다.

```python
class RevisionTarget(BaseModel):
    ref: str
    kind: str
    element_id: str
    owner: Literal["requirements", "design", "implementation", "testing"]
    artifact_type: str
    artifact_version_id: int | None
    display_label: str
```

- `ref`는 현재 `TraceRef`/project catalog와 호환되는 canonical 문자열이다.
- `element_id`는 가능한 경우 artifact가 보존하는 stable ID다.
- `display_label`은 검색과 화면 표시용이며 identity 비교에 사용하지 않는다.
- 요청에서 들어온 dict의 owner와 version을 신뢰하지 않고 catalog 값으로 다시 채운다.
- 기존 문자열 target API는 내부에서 `RevisionTarget`으로 정규화해 호환한다.

### 7.2 RevisionInterpretation

현재 두 번째 LLM 호출인 target 선택을 확장한다. 별도의 세 번째 LLM 호출은 추가하지 않는다.

```python
class RevisionInterpretation(BaseModel):
    targets: list[str]
    semantic_scope: Literal[
        "presentation",
        "contract",
        "behavior",
        "implementation",
        "test_expectation",
        "unknown",
    ]
    requested_effect: str
    clarification: str = ""
```

- `targets`는 제공한 후보 ref만 허용한다.
- `requested_effect`는 실행 문법이 아니라 사용자 요청을 짧게 보존하는 설명이다.
- `semantic_scope=unknown`이면서 owner 결정에 영향을 주면 실행하지 않고 질문한다.
- LLM이 반환한 scope는 제안일 뿐이며 ownership rule과 validator가 수용해야 한다.

### 7.3 RevisionPlan

```python
class RevisionPlan(BaseModel):
    plan_digest: str
    status: Literal[
        "ready_local",
        "needs_confirmation",
        "needs_clarification",
        "unsupported",
    ]
    requested_targets: list[RevisionTarget]
    authority_targets: list[RevisionTarget]
    upstream_candidates: list[RevisionTarget]
    downstream_targets: list[RevisionTarget]
    execution_mode: Literal["targeted_revision", "stage_rewind", "none"]
    reason_codes: list[str]
    explanation: str
    artifact_versions: dict[str, int]
    trace_digest: str
```

`plan_digest`는 instruction, canonical target, artifact version, trace digest, authority target과
execution mode로 계산한다. 사용자가 승인한 뒤 이 값이 달라지면 이전 계획을 실행하지 않는다.

계획은 기존 `workspace_commands.payload`의 action context에 저장할 수 있다. 새로운 DB schema는
필요하지 않다. 승인 또는 취소 후에는 같은 action을 다시 실행하지 못하게 기존 command 상태를
사용한다.

### 7.4 실행 결과

```python
class RevisionExecutionResult(BaseModel):
    changed_stages: list[str]
    touched_targets: dict[str, list[str]]
    regenerated_targets: dict[str, list[str]]
    stale_targets: dict[str, list[str]]
    target_remap: dict[str, str]
    artifact_versions: dict[str, int]
```

`target_remap`은 rename처럼 기존 표시 ref가 달라진 수정 뒤 후속 대화가 새 target을 이어받도록 한다.

## 8. OwnershipRegistry

RTM에 수정 권한까지 떠넘기지 않는다. 작은 코드 registry가 “어떤 사실을 어느 산출물이
소유하는가”를 정의한다.

초기 규칙은 실제로 지원하는 수정 경로만 등록한다.

| 사용자가 지목한 요소와 변경 | authoritative owner | 계획 정책 |
|---|---|---|
| use case, use-case spec의 의미 변경 | Requirements의 해당 modeling stage | 해당 요소 수정 후 하위 cascade |
| actor, relationship 변경 | Requirements의 해당 modeling stage | 부분 reviser가 없어 stage rewind 범위를 보여 주고 승인 |
| refined requirement 원문 직접 변경 | 없음 | typed mutation 경로가 없어 unsupported |
| class, field, relationship, operation | class diagram | local design revision |
| sequence call topology 또는 호출 method | class collaboration/operation | 정확한 call link가 있으면 선행 수정 승인 |
| sequence의 단순 표시 속성 | 표시 속성을 저장·수정할 source가 있을 때만 local | source가 없으면 unsupported |
| ERD entity/column | BCE Entity/class field | 항상 선행 수정 승인, ERD는 재투영 |
| API endpoint/schema의 기술 계약 | API spec | local design revision, exact linked downstream 갱신 |
| API에 새 업무 동작 추가·삭제 | use case/spec 또는 class contract | 하나로 확정되지 않으면 clarification |
| 구현 내부 refactor | implementation file/task | implementation local revision |
| 외부 동작을 바꾸는 구현 요청 | use-case/design candidate | typed adapter가 있는 선행 후보와 영향 범위를 보여 주고 승인 |
| 실패 테스트의 올바른 기대 결과 | API contract | exact API evidence가 있을 때만 선행 후보 제시 |
| 테스트 코드 자체의 결함 | 없음 | typed test-source mutation 경로가 없어 unsupported |

registry는 `(target_kind, semantic_scope)`를 받아 허용되는 authority kind, reverse traversal 한계,
confirmation 정책을 반환한다. 알 수 없는 조합을 가장 가까운 upstream으로 추측하지 않는다.

## 9. RevisionPlanner의 결정론적 알고리즘

### 9.1 target 확정

1. `ProjectTools.search_elements()`가 현재 app의 후보를 만든다.
2. LLM은 후보 ref 중 직접 지목된 것만 선택한다.
3. `validate_targets()`가 canonical ref, owner, artifact version과 editability를 다시 확인한다.
4. target이 여러 delivery owner에 걸리면 하나로 임의 축약하지 않고 clarification 또는 하나의
   통합 계획으로 명시한다. 첫 구현에서는 기존 제약을 유지해 한 owner씩 처리한다.

### 9.2 authority 계산

1. `OwnershipRegistry`에서 target kind와 semantic scope에 맞는 규칙을 찾는다.
2. local owner이면 요청 target을 authority target으로 사용한다.
3. 선행 owner가 필요한 규칙이면 `ArtifactTrace.sources/upstream` 또는 설계의 정확한 contract link에서
   허용된 kind만 찾는다.
4. 후보가 하나면 `needs_confirmation` 계획을 만든다.
5. 후보가 여러 개이거나 의미 범위에 따라 owner가 달라지면 `needs_clarification`을 반환한다.
6. exact link가 없으면 이름이나 설명 유사도로 보충하지 않고 `unsupported` 또는 clarification을
   반환한다.

모든 upstream을 단순 순회한 뒤 가장 오래된 단계를 고르지 않는다. 예를 들어 sequence call이 use
case와 연결되어 있다는 사실만으로 use case 수정까지 계획하면 안 된다. ownership rule이 class
collaboration을 요구하면 그 종류에서 탐색을 멈춘다.

### 9.3 영향 범위 계산

1. 계획 시작 시점의 artifact versions와 RTM/trace digest를 고정한다.
2. authority target의 `downstream()`을 조회한다.
3. stage별 exact target mapper로 실제 수정 가능한 element ref로 변환한다.
4. projection artifact는 LLM 수정 대상이 아니라 deterministic regeneration 대상으로 표시한다.
5. 지원하지 않는 downstream은 자동 전체 재생성하지 않고 stale target으로 명시한다.
6. 같은 요소를 여러 경로로 찾으면 canonical ref 기준으로 중복 제거한다.
7. 결과를 pipeline 순서로 정렬하되, 순서는 관계 존재를 대신하지 않는다.

### 9.4 승인 판정

다음 중 하나면 `needs_confirmation`이다.

- authoritative owner가 사용자가 보고 있거나 직접 지목한 단계보다 앞선 단계다.
- 직접 선택한 target 이외의 editable upstream 요소를 수정한다.
- targeted reviser가 없어 stage rewind가 필요하다.
- rename, remove처럼 target identity나 여러 contract consumer를 바꾸는 변경이다.

authority 수정 뒤 따라오는 deterministic projection과 이미 확인된 downstream cascade는 별도의 두
번째 승인을 요구하지 않는다. 다만 첫 승인 화면에서 전체 범위를 보여 준다.

단순한 local 수정이며 사용자가 target을 명시했고 owner가 바뀌지 않으면 기존처럼 바로 실행할 수
있다.

## 10. 사용자 확인 흐름

선행 단계 수정이 필요하면 기술적인 ref 목록만 보여 주지 않고 이유와 결과를 함께 설명한다.

```text
요청: UC-3 시퀀스에서 reserve 호출 제거

이 시퀀스 호출은 OrderControl.reserve operation과 collaboration에서 투영됩니다.
요청을 반영하려면 클래스 collaboration을 먼저 수정해야 합니다.

수정 대상
- class diagram: OrderControl.reserve / UC-3 collaboration

따라 갱신되는 항목
- sequence diagram: UC-3
- API: reserveOrder

요구사항 UC-3의 내용은 변경하지 않습니다.
이 범위로 진행할까요?
```

사용자는 승인, 취소 또는 범위 변경을 선택할 수 있다. “실제 유스케이스 동작도 제거”처럼 범위를
넓히면 기존 계획을 덧붙여 실행하지 않고 최신 snapshot에서 새 계획을 만든다.

자유 형식 승인 발화는 `ConversationAgent`가 구조화된 `confirm_revision` intent로 분류하며, 현재
context에 `confirm_change` action이 실제로 제시되어 있을 때만 연결한다. 승인 표현 목록이나 부분 문자열
매칭은 두지 않는다. 동시에 여러 pending plan이 있거나 stale이면 다시 선택하게 한다. UI의 명시적
승인 action은 LLM을 거치지 않는다.

## 11. 실행 규칙

### 11.1 계획과 실행 분리

새 진입점은 개념적으로 다음과 같이 나눈다.

```python
plan_revision(app_id, interpretation) -> RevisionPlan
execute_revision(app_id, approved_plan) -> RevisionExecutionResult
```

`plan_revision()`은 읽기 전용이다. LLM reviser, artifact save, checkpoint persist, image invalidation을
호출하지 않는다.

`execute_revision()`은 다음 순서를 지킨다.

1. app ID, command 상태와 plan digest를 검증한다.
2. 현재 artifact version과 trace digest를 계획 값과 비교한다.
3. authority target이 여전히 존재하고 editable한지 검사한다.
4. 승인된 target set을 stage revision service에 전달한다.
5. 비대상 보존과 semantic validator를 실행한다.
6. frozen plan에 기록된 downstream만 cascade하거나 재투영한다.
7. 모든 단계가 성공한 뒤 한 번에 저장하고 checkpoint를 동기화한다.
8. 변경 결과와 target remap을 conversation context에 남긴다.

한 단계라도 실패하면 batch 전체를 저장하지 않는다. 실패 때문에 전체 파이프라인을 처음부터 다시
실행하지 않고 실패한 plan과 stage를 그대로 보고한다.

### 11.2 설계 cascade 수정

현재 `revise_and_cascade()` 안에서 수행하는 exact reverse class 수정은 planner가 먼저 드러내야
한다. 구현 방법은 둘 중 단순한 쪽을 택한다.

1. reverse target 탐색을 `plan_design_revision()`으로 추출하고 executor에는 승인된 scope를 넘긴다.
2. 기존 함수를 유지하되, 내부에서 새 upstream target을 발견하면 승인 목록에 있는지 검사하고 없으면
   저장 전에 `UnapprovedScopeExpansion`으로 중단한다.

첫 번째 방식을 우선한다. planning과 mutation 경계가 코드 구조에서도 분명해지기 때문이다.

class 수정 뒤 sequence/API/ERD/deployment로 가는 forward cascade, 변경 전 RTM 고정, 비대상 보존
검사는 그대로 유지한다.

### 11.3 stage rewind fallback

부분 reviser가 없을 때만 `execution_mode=stage_rewind`를 사용한다.

- rewind할 가장 앞선 stage
- 폐기되거나 새 version으로 대체될 artifact
- 다시 실행할 downstream stage
- 보존되는 checkpoint와 artifact version

을 승인 화면에 명시한다. “관련 요소만 수정”할 수 있는 경로가 있는데 stage rewind로 우회하지
않는다.

## 12. element ref 안정화

### 12.1 1차 적용

먼저 기존 ref를 유지하면서 `RevisionTarget`에 artifact version과 element kind를 묶는다. 이 단계만으로
stale target 실행과 서로 다른 owner 혼합을 막을 수 있다.

모든 ref parse/format은 `TraceRef`와 catalog helper를 통해 수행한다. `partition(":")`, `#`, `::`를
직접 해석하는 코드를 새로 추가하지 않는다.

### 12.2 stable ID 적용

후속 대화에서 rename 전후의 같은 요소를 추적해야 하는 종류부터 app-managed ID를 추가한다.

우선순위는 다음과 같다.

1. class operation
2. collaboration call
3. class와 DataType
4. API schema처럼 이름 변경 가능성이 큰 요소

LLM은 ID를 만들지 않는다. 최초 accept 시 코드가 발급하고, targeted revision과 merge에서 기존 ID를
보존한다. 새 요소만 새 ID를 받는다. use case ID와 API operationId처럼 이미 안정적인 명시 ID는
중복 ID를 추가하지 않는다.

기존 `stage:signature` ref는 `legacy_ref` 또는 display alias로 catalog에서 계속 검색할 수 있게 한다.
artifact JSON 안에 ID를 추가하므로 DB table 변경은 필요 없다. 구형 artifact에는 adapter가 현재
version에서만 사용할 임시 ID를 계산하고, 다음 revision 저장 시 정식 ID를 부여한다.

## 13. 구현 단계

### 서브에이전트 운용 전략

각 Wave는 메인 에이전트가 계약과 완료 조건을 먼저 고정한 뒤, Luna·Terra 또는 같은 성격의
서브에이전트에게 파일 소유권이 겹치지 않는 bounded task로 나눈다. 모델 이름은 가용성에 따라 바꿀
수 있지만 역할은 다음처럼 유지한다.

| 역할 | 권장 에이전트 | 맡길 작업 |
|---|---|---|
| 빠른 탐색·기계적 변경·집중 테스트 | Luna | 특정 call path 조사, adapter·fixture·frontend action, 단위 회귀 테스트 |
| 다중 모듈 backend 구현 | Terra | RevisionPlanner, ConversationAgent 연결, RTM/cascade와 stage adapter |
| 계약·통합 책임 | 메인 에이전트 | 공개 계약 확정, 공유 hotspot 수정, diff 통합, 전체 회귀와 최종 판단 |

Wave별 권장 분담은 다음과 같다.

- Wave 1: Terra가 `revision_planner.py`와 ownership/trace adapter를 소유하고, Luna는
  `ProjectTools` target 정규화 조사와 planner 순수 단위 테스트를 소유한다.
- Wave 2: Terra가 ConversationAgent·workspace service의 plan/approval 연결을 소유하고, Luna는
  action payload·clarification·stale/cancel 회귀 테스트를 소유한다.
- Wave 3: Terra가 design RTM reverse planning과 bounded executor를 소유하고, Luna는 비대상 보존,
  atomicity와 실제 fixture 회귀를 소유한다.
- Wave 4: delivery stage adapter는 한 번에 한 소유자만 수정한다. Terra는 requirements/design 또는
  implementation/testing 중 한 묶음을 맡고, Luna는 반대편 call path 조사와 계약 테스트를 준비한다.
- Wave 5: Terra가 stable ID·catalog·target remap을 소유하고, Luna는 legacy adapter와 round-trip,
  후속 대화 회귀를 소유한다.

병렬 실행은 다음 규칙을 지킨다.

1. 메인 에이전트가 먼저 schema, reason code, action payload와 대상 파일을 동결한다.
2. 한 Wave에서 메인 에이전트와 최대 두 서브에이전트를 기본으로 하며, 독립 작업이 없으면 억지로
   병렬화하지 않는다.
3. 모든 worker에게 다른 작업자가 같은 저장소에서 작업 중이며, 타인의 변경을 되돌리지 말고 현재
   worktree에 맞춰 구현해야 한다고 명시한다.
4. `app/workspace/service.py`, 공통 conversation 계약, graph orchestration처럼 충돌 가능성이 큰 파일은
   한 에이전트만 소유하거나 메인 에이전트가 순차 통합한다.
5. 테스트 전담 에이전트는 구현 세부를 복제하지 않고 공개 동작과 failure invariant를 검증한다.
6. 메인 에이전트는 각 결과의 diff와 실행한 테스트를 검토하고, Wave 완료 후 통합 회귀를 직접
   수행한 뒤 다음 Wave를 시작한다.

### Wave 1: read-only RevisionPlanner와 계약

1. `RevisionTarget`, `RevisionInterpretation`, `RevisionPlan`과 reason code를 추가한다.
2. `ProjectTools`가 문자열 ref를 version이 고정된 `RevisionTarget`으로 정규화하게 한다.
3. 실제 지원 대상만 담은 작은 `OwnershipRegistry`를 추가한다.
4. 통합 `ArtifactTrace`와 설계 RTM에서 exact 후보와 downstream을 읽는 planner를 구현한다.
5. plan/trace digest와 stale 검사를 추가한다.
6. planner가 artifact나 command를 수정하지 않는 단위 테스트를 작성한다.

권장 새 모듈은 `app/workspace/conversation/revision_planner.py`다. stage별 내부 모델을 이 파일로
옮기지 않고, registry와 조회 adapter만 둔다.

### Wave 2: ConversationAgent와 승인 action 연결

1. `_TargetSelection`을 `RevisionInterpretation`으로 확장해 target 선택과 의미 범위를 한 번에 받는다.
2. 수정 명령은 곧바로 owner service로 보내지 않고 planner를 통과시킨다.
3. `ready_local`만 기존 message 경로로 전달한다.
4. `needs_confirmation`은 기존 action/command payload에 plan을 저장하고 승인 질문을 반환한다.
5. `needs_clarification`은 후보별 차이를 설명하고 한 가지 질문만 한다.
6. 승인 action은 저장된 plan digest와 최신 version을 검증한 뒤 executor를 호출한다.
7. 취소와 stale plan 재계획 경로를 추가한다.

기존 대화 분류 호출 수를 늘리지 않는다. 첫 호출은 대화 종류, 수정 요청의 두 번째 호출은 candidate
선택과 semantic scope를 함께 처리한다.

### Wave 3: 설계 reverse planning과 bounded execution

1. 설계 RTM의 exact contract link를 direction과 relation을 잃지 않는 조회 함수로 노출한다.
2. sequence/API/ERD 요청에서 class authority target을 계획 단계에 계산한다.
3. `revise_and_cascade()`의 암묵적 upstream 확대를 제거하거나 승인 scope 검사로 막는다.
4. executor가 승인된 class target을 먼저 수정한 뒤 frozen forward RTM만 따라가게 한다.
5. ERD는 계속 deterministic reprojection으로만 처리한다.
6. `assert_untargeted_elements_preserved()`와 batch atomicity 회귀 테스트를 유지한다.

### Wave 4: 네 delivery stage 연결

1. requirements target은 기존 `FeedbackEdit`와 modeling cascade로 연결한다.
2. design target은 element-scoped revision을 우선하고 broad feedback은 별도 승인 대상으로 둔다.
3. implementation target은 검증된 task/file ref와 source feedback 경로로 연결하고, RTM에 연결된
   파일 집합을 실제 허용 쓰기 범위로 전달한다.
4. testing target은 test defect와 expectation/contract 변경을 구분해 기존 repair owner 규칙을 따른다.
5. implementation/testing에서 behavior 변경을 요구하면 ArtifactTrace의 exact use-case/API/design
   근거와 ownership rule을 사용해 선행 후보를 제시한다.
6. 선행 단계 수정 뒤 downstream version과 Testing evidence의 stale 여부를 일관되게 갱신한다.

### Wave 5: stable ID와 후속 대화

1. operation/call부터 app-managed stable ID를 artifact JSON에 추가한다.
2. project catalog와 `TraceRef` projection이 stable ID를 canonical identity로 사용하게 한다.
3. 기존 signature ref를 alias로 유지한다.
4. 실행 결과의 `target_remap`을 recent decision/context에 저장한다.
5. “아까 그 메서드”가 rename 뒤에도 새 ref로 이어지는 대화 테스트를 추가한다.
6. 구형 artifact adapter와 새 artifact round-trip을 검증한다.

Wave 5는 Wave 1~4의 안전한 planning과 승인을 막지 않으면 분리해서 진행할 수 있다. 다만 장기 대화와
rename UX를 완료 조건에 포함하려면 최종적으로 적용한다.

## 14. 대표 검증 사례

| 사례 | 기대 결과 |
|---|---|
| `Order`에 필드 추가 | class local plan, 연결된 API/ERD만 하위 갱신, 선행 승인 없음 |
| UC-3 sequence에서 특정 호출 이름 변경 | exact class operation/collaboration 후보를 보여 주고 승인 전 무수정 |
| sequence 호출과 연결된 class operation이 없음 | 이름으로 추측하지 않고 clarification 또는 unsupported |
| ERD column 타입 변경 | BCE Entity field 선행 수정 계획과 영향 범위를 보여 주고 승인 요청 |
| API 설명 문구 수정 | API local plan, class나 requirements를 수정하지 않음 |
| API에 새로운 업무 기능 추가 | use case 변경인지 기술 endpoint 추가인지 질문 |
| 구현 내부 refactor | implementation file/task만 수정하고 requirements/design으로 되돌리지 않음 |
| 구현에서 사용자 동작 삭제 요청 | 연결된 requirement/design 후보와 downstream을 보여 주고 승인 요청 |
| 실패 테스트 자체의 문법 오류 | test-source mutation 경로가 없으므로 unsupported, 자동 우회 수정 없음 |
| 올바른 테스트 기대 결과를 바꾸라는 요청 | exact API contract 변경 가능성을 질문하고 자동 수정하지 않음 |
| 승인 대기 중 다른 artifact version 저장 | stale plan 거부 후 최신 snapshot으로 재계획 |
| LLM이 후보에 없는 ref 반환 | target validation 실패, 실행 없음 |
| LLM 수정 결과가 형제 요소까지 변경 | 비대상 보존 검사 실패, batch 저장 없음 |
| 사용자가 선행 계획 취소 | artifact/checkpoint/image cache 모두 그대로 유지 |
| rename 완료 후 “그 메서드에 파라미터 추가” | target remap 또는 stable ID로 새 요소를 선택 |
| 부분 reviser 없는 broad stage 변경 | rewind 범위를 먼저 보여 주고 승인 후에만 재실행 |

## 15. 테스트 전략

### 15.1 순수 단위 테스트

- 같은 target, instruction, artifact versions, trace에서 같은 plan digest가 나온다.
- delivery stage 순서만으로 upstream을 추측하지 않는다.
- ownership rule에 없는 kind 조합은 unsupported다.
- direct source가 여러 개면 하나를 임의 선택하지 않는다.
- downstream은 중복 없이 pipeline 순서로 정렬된다.
- projection target은 editable target이 아니라 regeneration으로 분류된다.
- planner 호출 전후 repository write가 0회다.

### 15.2 ConversationAgent 테스트

- 제공하지 않은 ref와 stage를 모델 출력에서 제거한다.
- target과 semantic scope를 두 번째 structured call 하나로 받는다.
- 모호한 behavior/representation 요청은 clarification을 반환한다.
- pending plan의 자유 형식 승인 발화를 구조화된 intent와 실제 제공 action으로 연결한다.
- pending plan action이 없을 때 승인 발화를 임의 수정 승인으로 해석하지 않는다.

### 15.3 service 통합 테스트

- `needs_confirmation` 응답 전에는 artifact version이 늘지 않는다.
- 승인 뒤 plan에 기록된 target만 revision service에 전달된다.
- stale plan은 어떤 stage service도 호출하지 않는다.
- batch 중 한 stage가 실패하면 어떤 artifact도 저장하지 않는다.
- 성공 뒤 checkpoint, artifact version, image와 conversation result가 같은 상태를 가리킨다.

### 15.4 실제 fixture 회귀

기존 수강신청처럼 class-operation-sequence-API 연결이 충분한 fixture 하나와, exact link가 일부 없는
fixture 하나를 사용한다.

- 연결이 충분한 fixture에서는 class 수정의 downstream과 sequence 수정의 class authority가
  정확히 계산되어야 한다.
- link가 없는 fixture에서는 LLM이나 문자열 유사성으로 관계를 만들어 내지 않아야 한다.
- 전체 파이프라인을 매 사례마다 재실행하지 않고 저장된 checkpoint와 artifact version을 사용한다.

## 16. 완료 조건

다음 조건을 모두 만족하면 핵심 개선이 완료된 것으로 본다.

1. 모든 자연어 수정 명령은 실행 전에 검증된 `RevisionTarget`과 `RevisionPlan`을 갖는다.
2. 선행 stage 또는 명시 target 밖의 editable 요소는 사용자 승인 전에 수정되지 않는다.
3. LLM이 ref, owner, upstream target, downstream 범위를 자유롭게 생성하는 경로가 없다.
4. 같은 snapshot과 intent에서 planner 결과가 결정론적이다.
5. exact link가 없으면 reverse cascade를 추측하지 않는다.
6. 승인 계획과 실행 artifact version이 다르면 실행을 거부한다.
7. 부분 reviser가 있으면 전체 stage rewind를 사용하지 않는다.
8. 설계 부분 수정의 비대상 보존 보장이 유지된다.
9. 요구사항, 설계, 구현, Testing의 target이 통합 trace를 통해 같은 방식으로 계획되며, 실제 typed
   mutation 경로가 없는 target은 추측 실행하지 않고 unsupported로 종료한다.
10. 사용자에게 선행 수정 이유, 실제 수정 요소와 따라 갱신되는 요소가 구분되어 보인다.
11. rename 뒤 후속 대화가 새 target을 이어받는다.
12. 새 DB 테이블이나 컬럼 없이 기존 artifact version과 command payload로 동작한다.

## 17. 구현 중 지켜야 할 판단 기준

- RTM은 관계를 증명하지만 사용자의 의미를 대신하지 않는다.
- LLM은 의미를 제안하지만 수정 권한과 범위를 결정하지 않는다.
- source가 있다는 이유만으로 source를 수정하지 않는다.
- 선행 단계 수정은 별도 사용자 승인 없이는 실행하지 않는다.
- authoritative source가 바뀌면 확인된 derived artifact는 같은 계획에서 갱신한다.
- exact target이 없으면 넓은 stage 수정으로 조용히 승격하지 않는다.
- 현재 snapshot에서 계산한 범위를 실행 중 LLM 출력으로 확대하지 않는다.
- 전체 되감기보다 element-scoped revision을 우선한다.
- 기존 RTM, ArtifactTrace, feedback service와 action 계약을 재사용하고 같은 역할의 시스템을 하나 더
  만들지 않는다.

## 18. 구현 결과와 명시적 제한

구현은 별도 FeedbackAgent나 DB schema를 추가하지 않고 완료했다. `ConversationAgent`는 자유 형식
발화를 분류하고 유한한 target 후보와 의미 범위만 제안한다. `RevisionPlanner`가 ownership, exact
trace/RTM 관계, downstream, 승인 필요 여부와 digest를 결정하며, Workspace service가 최신 snapshot을
재검증한 뒤 frozen scope만 단계별 adapter로 전달한다. 승인 계획과 실행 결과는 기존
`workspace_commands.payload/result`에 저장되고 UI에는 서로 다른 카드로 표시된다.

operation과 collaboration call에는 application-managed stable ID를 추가했다. 기존 signature/call ref는
alias로 유지하고, rename 뒤에는 `target_remap`을 대화 context에 합쳐 후속 수정 후보로 사용한다. 여러
산출물 저장은 repository batch transaction을 사용한다. 구현 job의 source/frontend/test/deployment/IaC
파일 snapshot도 한 transaction에 저장한다. 설계 checkpoint를 먼저 동기화한 뒤 artifact 저장이
실패하면 이전 checkpoint를 복원하고, 요구사항 revision도 artifact batch 저장 실패 시 진입 전
checkpoint를 보상 복원한다. broad 설계 revision은 사전 생성을 하지 않고 지정 stage의 피드백 gate에
직접 진입하므로, 피드백 실행 전에 중간 artifact version을 만들지 않는다.

현재 저장 모델에 실제 수정 함수가 없는 refined requirement 원문과 test source 자체 수정은 지원하는
척하지 않는다. 두 경로는 planner에서 `unsupported`로 종료된다. Testing finding이 production code
결함을 정확한 file/task evidence로 가리키는 경우와, 기대 동작 변경이 API contract를 정확히
가리키는 경우만 각각 implementation repair 또는 선행 contract 승인 계획으로 연결한다. 모든 새
런타임·UI 문구는 영어이며, 자연어 승인 phrase whitelist는 사용하지 않는다.
