# 전역 사용자 피드백과 단계 revision 개선안

## 1. 문서 상태

이 문서는 요구사항·설계·구현·Testing에서 받은 사용자 피드백을 어느 단계가 처리할지
정하는 목표 설계다. 범용 workflow engine을 만드는 계획이 아니다.

첫 완료 범위는 다음 한 경로다.

```text
설계에서 UC 명세 공백 발견
  → 사용자 질문과 Decision
  → 별도 Requirements command에서 UC 수정
  → 별도 `start_design` command에서 class 재생성·검토
  → 별도 진행 command에서 sequence를 결정론적으로 투영
```

현재 `Question`·`Decision`은 기존 `RevisionPlan`과 Workspace command 경로에 연결돼 있다.
운영 경로에서 사용하지 않은 범용 `ChangeSet`·전용 repository·stage adapter prototype은
삭제했다. 별도 상태기계를 추가하지 않고 기존 command payload와 단계 checkpoint만 사용한다.

## 2. 고정 원칙

1. 현재 stage는 이전 stage가 소유한 산출물을 직접 수정하지 않는다. upstream 변경이
   필요하면 해당 owning stage의 새 Workspace command를 만든다.
2. 한 command는 한 stage의 작업만 수행한다. owner stage가 끝난 뒤 사용자가 다음 stage를
   시작하며, 자동 모드는 노출된 action을 순서대로 선택할 뿐 stage를 합치지 않는다.
3. 같은 workspace에서는 한 사용자 메시지의 처리가 끝날 때까지 다음 메시지를 기다린다.
   별도 행 잠금, 분산 lock과 다중 worker 상태기계는 추가하지 않는다.
4. 기존 MySQL 스키마를 변경하지 않는다. 필요한 입력과 재개 정보는 기존 command payload와
   기존 단계 checkpoint에 저장한다.
5. RTM은 정확히 연결된 영향 후보를 찾는 근거다. RTM 링크만으로 수정 권한을 부여하거나
   링크가 없는 관계를 이름·LLM 추측으로 보완하지 않는다.
6. 사용자에게는 제품 의미를 결정해야 할 때만 질문한다. schema 오류, provider 실패와 명백한
   생성 결함은 사용자 질문으로 바꾸지 않는다.
7. 토큰 절감보다 최초 결과의 결함과 결함-수정 반복을 줄이는 것을 우선한다.
8. 환경이나 한 단계만 실패하면 전체 파이프라인을 다시 시작하지 않고 해당 owning stage의
   저장된 checkpoint에서 재개한다.

## 3. 소유권 경계

| 발견 위치 | 변경 대상 | 처리하는 stage | 처리 방식 |
|---|---|---|---|
| Design | UC 명세 | Requirements | 별도 Requirements command |
| Sequence | class·operation·call | Design의 class 단계 | class 수정 후 sequence 재투영 |
| Implementation | class·API 등 설계 계약 | Design | 별도 Design command |
| Testing | 생성된 source·test | Implementation | 별도 Implementation command |
| 임의 stage | 같은 stage가 소유한 산출물 | 현재 owning stage | 지원되는 bounded reviser |

Sequence는 class collaboration의 projection이므로 sequence 단계에서 class를 역수정하지 않는다.
Testing도 구현 파일을 직접 수정하지 않는다. 이 두 경계는 현재 코드에 반영되어 있다.

하나의 질문이 여러 owning stage를 동시에 수정해야 한다면 한 command에서 함께 실행하지
않는다. 가장 upstream인 owner부터 실행하고, 완료 결과를 입력으로 다음 stage command를
시작한다.

## 4. 최소 피드백 계약

### 4.1 Question

Question은 다음 정보만 필요로 한다.

- 질문 ID와 version
- app ID와 질문을 만든 command ID
- 질문이 근거한 artifact version
- 발견 stage와 finding 종류
- 사용자에게 보여 줄 질문
- stable option ID가 있는 선택지
- 자유 답변 허용 여부
- 코드가 검증할 수 있는 owner 후보

오래된 artifact version에 대한 답변은 실행하지 않는다. 같은 질문을 다시 만들 때에는 기존
option의 의미를 바꾸지 않고 question version을 올린다.

### 4.2 Decision

Decision은 선택지와 자유 답변을 같은 형태로 보존한다.

- Decision ID와 원본 Question ID·version
- 사용자의 원문 또는 선택한 option ID
- 정규화된 의미와 요청 효과
- 확정된 authoritative target
- `NORMALIZED` 또는 `NEEDS_CLARIFICATION` 상태

선택지는 미리 정의된 payload를 사용한다. 자유 답변은 LLM이 정규화 후보를 만들 수 있지만,
target 존재 여부, owner, 현재 version과 허용 change type은 코드가 검증한다. 의미가 여러
가지면 실행하지 않고 다시 질문한다.

### 4.3 실행 계획

첫 운영 경로에서는 범용 execution graph를 만들지 않는다. 검증된 Decision을 기존
`RevisionPlan`과 공개 action으로 변환하여 다음 command 하나를 만든다.

- owner stage
- 대상 artifact ref와 고정 version
- 사용자 결정에서 나온 수정 지시
- source question·decision·command ID
- 다음 stage가 사용할 공개 action

## 5. 중복 실행과 재개

기존 Workspace single-flight와 단계 checkpoint를 사용해 다음 규칙만 지킨다.

- 같은 Question version의 Decision을 두 번 제출해도 새 revision을 두 번 만들지 않는다.
- 같은 Decision에서 같은 owner command를 두 번 만들지 않는다.
- command 실행 직전에 대상 artifact version이 질문의 base version과 같은지 검사한다.
- LLM 호출이 필요한 stage는 기존 단계 checkpoint와 attempt 기록을 사용한다.
- 입력 digest와 prompt·validator version이 같은 완료 checkpoint만 재사용한다.
- 실패 checkpoint는 다시 실행할 수 있지만 완료 checkpoint는 같은 입력으로 재호출하지 않는다.
- 결과 수신 여부가 불명확한 원격 호출은 성공으로 간주하지 않고 `OUTCOME_UNKNOWN`으로 남긴다.
- 오래된 base version이면 publish하지 않고 새 질문 또는 재계획으로 돌아간다.

별도의 accepted-head manifest, manifest chain과 전역 artifact validity 표는 만들지 않는다.
완료된 stage artifact와 command 상태가 현재 공개 경계다. upstream command가 실패하면 기존
완료 산출물은 그대로 유지하고 downstream command를 시작하지 않는다.

## 6. 첫 운영 수직 경로

### 6.1 질문 생성

Class 설계기가 deterministic validator로 해결할 수 없는 specification gap을 발견하면
Workspace에 질문을 반환한다. 질문은 가능한 UC 명세 target을 유한 후보로 제공하고 선택지와
자유 답변을 함께 허용한다.

Class schema·참조·타입 결함, LLM 응답 오류와 provider failure는 이 경로로 보내지 않는다.

### 6.2 Requirements로 이동

사용자 답변이 특정 UC 명세 변경으로 확정되면 Design command 안에서 UC를 수정하지 않는다.
Workspace가 별도 Requirements command를 만들고 다음 정보만 전달한다.

- UC 명세 ID
- 검증된 사용자 수정 지시
- base artifact version
- source question·decision ID

기존 requirements feedback adapter가 해당 UC를 수정하고 기존 validation·저장 경계를 그대로
사용한다. 실패하면 Requirements command만 재개한다.

### 6.3 Design 재실행

Requirements가 완료되면 `start_design`을 노출한다. 새 Design command는 수정된 UC 명세를
입력으로 설계 checkpoint를 초기화하고 class를 처음부터 생성한 뒤 기존 class 검토 gate에서
멈춘다. class bundle은 클래스 구조, operation과 Collaboration을 함께 검증하는 현재의 안전한
단위로 유지한다.

사용자가 기존 진행 action을 선택하면 별도 command가 새 class collaboration에서 sequence를
코드로 투영한다. sequence를 만들기 위한 별도 LLM 호출이나 sequence 단계의 class 역수정은
허용하지 않는다. 이 첫 경로에 targeted class cascade용 새 진입점은 만들지 않는다.

## 7. 현재까지 완료된 작업

- [x] version-pinned Question·Decision envelope의 Workspace 연결
- [x] 기존 `RevisionPlan`을 사용한 owner·stale 검증
- [x] 기존 command payload·checkpoint를 사용한 중복 제출·재개 처리
- [x] 별도 Requirements command와 기존 Design command 연결
- [x] UC 수정 → class cascade → sequence projection 계약 테스트
- [x] sequence의 class 역수정 제거
- [x] Design에서 upstream owner로 보내는 경계 정리
- [x] Testing 수리와 Implementation을 별도 command로 분리
- [x] Testing 재실행을 별도 `start_testing` command로 분리
- [x] 운영에서 사용하지 않는 `executable_behavior` prototype 제거

## 8. 남은 구현 순서

### 하위 작업 A — 문서와 범위 동결

- 이 문서와 현재 코드의 경계를 맞춘다.
- schema 변경, lock, accepted-head manifest와 범용 상태기계를 제외한다.
- Sol에게 prototype 범위의 치명적 결함만 검토받는다.

### 하위 작업 B — 실제 Workspace 수직 연결

- specification gap Question을 기존 Workspace 응답과 action으로 노출한다.
- Decision을 검증하고 별도 Requirements command로 라우팅한다.
- Requirements 완료 뒤 기존 `start_design` command로 class를 다시 생성·검토한다.
- 기존 진행 action으로 별도 command를 만들고 sequence를 결정론적으로 투영한다.
- 기존 MySQL schema와 command payload만 사용한다.

### 하위 작업 C — 최소 통합 검증

다음 사례만 검증한다.

1. 선택지 답변이 정확한 UC owner command를 만든다.
2. 모호한 자유 답변은 실행되지 않고 다시 질문된다.
3. 오래된 question/action은 stage를 호출하지 않는다.
4. 같은 Decision의 중복 제출이 중복 revision을 만들지 않는다.
5. Requirements 또는 Design 실패는 해당 command에서만 재개된다.
6. class 변경 뒤 sequence projection에는 LLM 호출이 없다.

테스트는 먼저 Workspace service와 기존 repository 경계를 사용한다. 전체 브라우저·실제 LLM
종단 실행은 이 계약이 통과한 뒤 별도 평가로 수행한다.

검증 증거는 계층을 구분한다. Workspace 수직 테스트는 별도 command와 공개 action의 연결을
검증하고 단계 서비스는 stub으로 둔다. Requirements 수정 내용이 class 입력으로 이어지는 계약과
class에서 sequence로의 순수 투영은 기존 cascade·graph 테스트가 검증한다. 이들을 실제 LLM
종단 실행으로 표현하지 않으며, 실제 모델 비교는 하위 작업 F에서 수행한다.

### 하위 작업 D — 사용되지 않은 prototype 축소

수직 경로에서 실제 import되고 실행된 계약을 확인한다. 운영 경로가 사용하지 않는 다음 요소는
삭제 또는 격리 후보로 본다.

- 범용 RTM diff와 target remap
- accepted manifest와 전역 stale 소비 차단
- 미래 `reuse`·`revalidate` 상태
- 별도 sequence 실행 입력
- 모든 stage를 포괄하는 repository 상태기계

운영 import가 없던 범용 `ChangeSet`·전용 repository·stage adapter와 전용 테스트는 삭제한다.
UC 수정 → class → sequence 내용 계약 테스트는 운영 envelope·delivery 입력으로 바꿔 유지한다.

### 하위 작업 E — 클래스 설계 prototype 축소와 통합

운영 import를 확인한 결과 `executable_behavior`는 실제 class 생성 경로와 분리된 두 번째
구현이었다. 필요한 계약은 기존 `class_diagram`과 `sequence_diagram`에 이미 있으므로 일부를
옮기거나 별도 runner로 남기지 않고 prototype 전체를 제거한다.

- `BCEModel` 하나가 class·operation·Collaboration을 함께 저장한다.
- `validate_class_model`이 schema·type·parent·binding과 operation·call 참조를 검사한다.
- schema·결정론 finding은 class 단계의 생성 결함으로만 수리한다.
- 사용자나 review가 발견한 명세 공백은 `specification_gap` Question으로 upstream owner에게
  보낸다. 현재 class validator에 의미 공백 자동 추론을 추가하지 않고 기존 피드백 envelope를
  쓴다.
- `project_sequence_model`이 수락된 class 결과를 LLM 호출 없이 투영하고 class hash를 기록한다.
- graph의 기존 class check가 핵심 결정론 finding만 검토한다.

삭제 범위에는 effect·obligation ontology, 범용 finding 상태기계, execution witness·봉인,
patch protocol, 전용 LLM 실험 runner와 그 전용 테스트가 포함된다. 이들은 운영 수직 경로의
품질을 높이지 않은 채 별도 acceptance 체계를 만들고 있었다.

### 하위 작업 F — 실제 LLM 비교 평가

수강신청 앱을 OSS 120B로 기존 경로와 개선 경로에 같은 입력으로 실행한다. 평가는 토큰량보다
다음 지표를 우선한다.

- 최초 validator 통과율
- 최초 결과의 결함 수와 종류
- 수정 LLM 호출 수
- 같은 결함의 반복 횟수
- 최종 class·sequence 무결성
- 전체 소요 시간

한 쌍의 실행으로 계측 경로를 먼저 확인하고, 차이가 불명확할 때만 반복 횟수를 늘린다.

2026-09-12 수강신청 앱(`522d73e6-3aee-42af-a787-15a22ab364b0`)을
`openai/gpt-oss-120b`로 실행한 단일 사례 결과는 다음과 같다.

| 실행 | 시간 | 실제 LLM 요청 | 입력/출력 토큰 | 결과 |
|---|---:|---:|---:|---|
| 초기 경로 | 86.0초 | 24 | 95,504 / 74,222 | 타입 alias를 다른 시그니처로 오판해 UC3 중단 |
| alias·container 정규화 뒤 | 90.3초 | 35 | 102,573 / 63,721 | UC9의 현재 행위자 ID를 공급 불가능한 인자로 만들어 중단 |
| current-actor·type-closure 지시 뒤 | 111.2초 | 38 | 105,359 / 64,978 | 클래스 생성 완료, 검토에서 같은 UUID끼리 의미가 다른 바인딩 2건 발견 |
| UC2·UC3 국소 피드백 | 42.4초 | 8 | 20,305 / 9,423 | 두 바인딩 제거, 다른 UC·구조·타입·관계 보존 |

최종 클래스는 결정론 검사와 Sol 검토를 통과했다. 이어 만든 11개 시퀀스는 LLM 호출 없이
투영됐고 unresolved/narrative step과 finding이 모두 0이었다. 따라서 관측된 결함 종류를
차단하고 국소 피드백으로 수렴시키는 효과는 확인했지만, 최초 전체 생성은 더 느리고 호출도
많았다. 한 사례만으로 결함률 일반화를 주장하지 않으며 다음 개선 초점은 검사 계층 확대가
아니라 최초 후보의 품질과 검토 뒤 대상 UC만 고치는 기존 국소 경로의 활용이다.

API path 실패 입력을 같은 메시지·응답 스키마로 비교했을 때 OSS 120B는 14.4초에 응답했지만
structured request의 내부 ID를 path placeholder로 쓴 2건 때문에 정규화를 통과하지 못했다.
GLM 5.3 Flash는 같은 위반 없이 통과했지만 181.0초와 11,048 출력 토큰이 들었다. 모델을
교체하지 않고 기존 schema-repair 경계에 top-level Boundary parameter 검사를 연결한 뒤 OSS
120B는 40.8초·물리 요청 5회로 13개 endpoint를 생성했고 class·sequence·API readiness가 모두
`READY`가 됐다. 이는 단일 응답 품질 차이이며 모델 전체의 우열로 일반화하지 않는다.

후속 실험에서는 각 interaction에 path placeholder 후보를 명시했다. 최상위 Boundary 이름을
그대로 노출한 첫 시도는 12.4초·요청 1회에 schema를 통과했지만 구조 타입 자체를
`{request}`·`{criteria}`로 사용해 유효 후보 정의가 지나치게 넓음을 확인했다. 후보를 도메인
이름이 아니라 타입으로 제한해, 한 path segment로 표현 가능한 최상위 scalar·enum만 허용한
뒤 같은 CLASS v2 입력을 다시 실행했다. OSS 120B는 9.3초·요청 1회·schema repair 0회,
입력 3,064·출력 2,893 token으로 13개 endpoint를 정규화했고 구조·중첩 placeholder는 없었다.
단일 실행이므로 일반적인 성공률 결론이 아니라, 수강신청 실패를 만든 결함 종류가 사례별
예외 없이 입력 후보 제한으로 제거됐다는 근거로만 사용한다.

## 9. Luna·Terra·Sol·Astra 활용

서브에이전트는 서로 겹치지 않는 작은 파일 단위만 맡는다.

| 역할 | 맡길 작업 | 맡기지 않는 작업 |
|---|---|---|
| Luna | Question·Decision 순수 변환, action read model, 작은 fixture와 단위 테스트 | RTM 권한 판정, 저장 transaction, stage orchestration |
| Terra | 기존 Workspace command 연결, owner routing, 재개·중복 실행 통합 테스트 | LLM prompt 확장, UI 의미 추측, 새 DB schema |
| 주 에이전트 | 계약 동결, 변경 통합, 회귀 테스트와 범위 축소 | 미확정 계약의 병렬 구현 |
| Sol | 각 하위 작업 뒤 치명적 권한·stage 경계·재호출 결함 검토 | 새 기능 제안과 범위 확대 |
| Astra | 방법론이나 시스템 경계가 바뀌는 예외적 고위험 검토 | 일상적인 코드 재검토 |

각 하위 작업은 주 에이전트와 Sol의 검토를 받은 뒤 사용자 승인을 요청한다. 승인 전에는
커밋하거나 다음 하위 작업으로 넘어가지 않는다.

## 10. 비범위

- MySQL schema 변경
- workspace 내부 lock 또는 실제 MySQL 다중 process 동시성 보증
- 범용 workflow DSL
- 임의 SQL 변조와 모든 manifest 손상 방어
- 시스템 전체 accepted-head manifest와 reader/writer 교체
- post-change RTM 전체 diff와 unknown 관계 추측
- 첫 수직 경로에서 API·ERD·배포·구현을 모두 재생성하는 기능
- effect·obligation ontology 확장
- 모델별 latency 최적화

## 11. 첫 완료 기준

첫 구현은 다음 문장이 실제 Workspace 통합 테스트로 성립할 때 완료다.

> 설계에서 발견한 UC 명세 공백에 대한 사용자 Decision이 별도 Requirements command를
> 실행하고, 완료된 새 UC를 입력으로 `start_design` command가 class를 다시 생성·검토한다.
> 이후 별도 진행 command에서 sequence가 LLM 호출 없이 투영된다. 어느 stage도 이전 stage의
> 산출물을 직접 수정하지 않고, 중복 답변과 실패 재개가 불필요한 LLM 재호출을 만들지 않는다.

이 경계가 검증된 뒤에만 클래스 설계 prototype을 축소·통합하고 실제 LLM 품질 비교를
재개한다.
