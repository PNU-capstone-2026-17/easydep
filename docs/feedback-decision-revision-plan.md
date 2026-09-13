# 전역 사용자 피드백과 단계 revision 개선안

## 1. 문서 상태

이 문서는 요구사항·설계·구현·Testing에서 받은 사용자 피드백을 어느 단계가 처리할지
정하는 목표 설계다. 범용 workflow engine을 만드는 계획이 아니다.

첫 완료 범위는 다음 한 경로다.

```text
설계에서 UC 명세 공백 발견
  → 사용자 질문과 Decision
  → 별도 Requirements command에서 정확한 UC 명세만 수정·검토
  → 현재 RTM으로 Design 범위를 새로 계획하고 사용자 승인
  → 별도 Design command에서 class를 수정·검토
  → sequence를 결정론적으로 재투영
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

### 2.1 네 에이전트와 공통 하위 작업

EasyDep의 런타임 에이전트는 Requirements·Design·Implementation·Testing 네 종류로
고정한다. admission, planning, review와 validation은 별도 에이전트가 아니라 이 네 에이전트가
자기 하위 작업 안에서 수행하는 활동이다.

각 하위 작업은 기존 상태와 envelope를 사용해 다음 세 결과 중 하나만 만든다. 이를 위해 새
DB 상태나 범용 workflow schema를 추가하지 않는다.

- **계속:** 현재 문맥으로 자기 단계 산출물을 만들거나 수정한다. Implementation의 기존
  `IMPLEMENT` 결정이 여기에 해당한다.
- **사용자 피드백 필요:** 제품 의미를 사용자가 결정해야 한다. 기존 `Question`과
  `NEEDS_INPUT`/`need_feedback` 경로를 사용하며 선택지와 자유 답변을 함께 제공한다.
- **기술 실패:** provider·도구·schema·build 오류처럼 사용자가 제품 의미를 답해도 해결되지
  않는 실패다. 기존 실패·재개 경로를 사용하고 질문으로 바꾸지 않는다.

하위 작업이 상류 산출물의 변경 필요성을 발견해도 직접 수정하지 않는다. 질문에 발견 단계,
근거 artifact/element ref와 authority 후보를 담고, 사용자 답변 뒤 해당 산출물을 소유한 네
에이전트 중 하나가 별도 command에서 수정한다.

각 에이전트 프롬프트에는 장식적인 persona나 전체 대화 이력을 저장하지 않는다. 기존
TaskSpec과 checkpoint에서 현재 agent/stage, 하위 작업 목표, 현재 상태, 수정 가능 범위,
관련 RTM ref와 종료 조건만 짧은 자연어 작업 브리프로 만든다.

도메인 의미는 공통 ontology로 옮기지 않는다. 예를 들어 어떤 행위자와 엔티티 사이의 업무
관계가 빠졌다는 판단과 대안은 LLM이 UC·설계 산출물을 자연어로 비교해 질문으로 표현한다.
사용자가 선택한 뒤 owning agent가 그 의미를 기존 요구사항·UML·ERD·API·source 형식으로
구체화한다. 공통 계층은 Question·Decision·RTM ref와 stage ownership만 검증한다.

### 2.2 RTM 기반 생성 경계

`RevisionPlan`의 authority와 downstream을 모든 단계가 공유하는 고정 실행 경계로 사용한다.
별도 실행 계약이나 DB 필드는 추가하지 않는다.

1. authority target은 해당 command에서 의미를 바꿀 수 있는 항목이다.
2. downstream target은 RTM이 확정한 재투영·갱신 범위다. 같은 클래스나 파일을 참조한다는
   이유로 형제 항목까지 넓히지 않는다.
3. 단계 adapter는 이 범위를 버리거나 다시 추측하지 않고 생성기에 그대로 전달한다.
4. LLM에는 선택된 항목, 그 항목의 근거와 필요한 직접 연결만 입력한다. 응답 schema도 같은
   항목만 반환하게 하고, 병합기는 대상 밖 기존 값을 그대로 보존한다.
5. 결정론적으로 투영할 수 있는 downstream은 LLM에 보내지 않는다.
6. 갱신 뒤 RTM은 LLM이 작성하지 않고 저장 모델의 stable ID와 provenance에서 다시 계산한다.
7. 실행 중 범위 밖 변경 필요성이 발견되면 조용히 확장하지 않고 새 계획과 사용자 확인으로
   돌아간다.

적용 순서는 Design의 class → sequence → API 수직 경로를 기준 구현으로 완성한 뒤,
Requirements의 local target, Implementation의 file/task 경계, Testing의 evidence handoff를
차례로 점검하는 것이다. Testing은 구현을 직접 수정하지 않고 Implementation command에
증거와 승인 범위를 전달한다.

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

국소 Requirements revision의 검토가 끝나면 그 command에서 일반 `ADVANCE`로 기존 graph를
재개하지 않는다. revision에 사용한 plan은 소진된 것으로 보고, 현재 artifact version과 RTM에서
Design 영향 후보를 다시 읽어 새 `RevisionPlan`을 만든다. 사용자가 이 새 범위를 승인한 뒤에만
별도 Design command를 시작한다.

현재 RTM에 정확히 연결된 class operation·collaboration이 있으면 targeted class cascade를 쓰고,
새 UC 의미 때문에 아직 존재하지 않는 class 요소가 필요하면 이를 추측해 만들지 않는다. 이때는
사용자가 broad class 재생성을 별도로 승인하거나 작업을 중단한다. `start_design`은 최초 Design
생성용이며 이 feedback 경로의 묵시적 전체 재생성 action으로 사용하지 않는다.

수락된 class collaboration에서 sequence는 코드로 투영한다. sequence를 만들기 위한 별도 LLM
호출이나 sequence 단계의 class 역수정은 허용하지 않는다.

## 7. 현재까지 완료된 작업

- [x] version-pinned Question·Decision envelope의 Workspace 연결
- [x] 기존 `RevisionPlan`을 사용한 owner·stale 검증
- [x] 기존 command payload·checkpoint를 사용한 중복 제출·재개 처리
- [x] 별도 Requirements command와 기존 Design command 연결
- [x] UC 수정 결과를 class cascade 입력으로 전달하고 sequence를 투영하는 내용 계약 테스트
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
- 국소 Requirements revision 뒤 일반 `ADVANCE`를 막고 현재 RTM에서 새 downstream plan을 만든다.
- 새 plan을 사용자가 승인하면 별도 Design command로 class를 수정·검토한다.
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

### 하위 작업 G — RTM slice를 생성 경계까지 연결

수강 취소 API 피드백 실험에서 계획기는 정확한 Boundary operation과 `dropRegistration`을
찾았지만, Design 실행기가 operation을 Boundary class 전체로 확대하고 승인된 downstream을
전달하지 않아 비대상 API 두 건과 UC5 호출 부모 관계가 함께 바뀌었다. validator는 모델
무결성만 확인해 이 범위 드리프트를 clean으로 판정했다.

다음 순서로 보완한다.

1. Design 계획의 고정 downstream을 cascade에 전달한다.
2. operation → collaboration/sequence/API의 정확한 RTM 링크를 실행에서도 사용한다.
3. API 수정 입력·응답을 선택된 interaction으로 제한하고 비대상 endpoint를 보존한다.
4. operation signature 수정은 기존 call topology를 유지한 채 parameter binding만 다시 만든다.
5. 같은 수강신청 피드백으로 비대상 의미 변경 0건을 확인한 뒤 커밋한다.
6. 이 기준 구현 뒤 Requirements → Implementation → Testing 순서로 동일 경계를 얇게 점검한다.

### Requirements RTM slice 감사

현재 Requirements 경로에는 이미 쓸 수 있는 얇은 기반이 있다.

- `use_case`와 `use_case_spec`은 catalog의 정확한 ID로 선택된다.
- delivery adapter는 선택된 ID를 `FeedbackEdit(scope="local")`로 전달한다.
- 해당 owning 생성기는 대상 항목만 LLM에 보내고 같은 stage의 형제 항목을 보존한다.
- 저장 뒤 artifact trace는 산출물의 명시 ID 관계에서 다시 계산된다.

하지만 이 보장은 owning 생성기까지만 유효하다. 완료된 Requirements checkpoint를 피드백
게이트로 되돌린 뒤 다음 게이트로 진행하면 최초 `RevisionPlan.downstream_targets`가 전달되지
않고, 일반 파이프라인이 specs와 relationships를 전체 재생성한다. 또한 현재 artifact trace에는
개별 relationship node가 없어 이 전체 재생성을 정확한 영향 범위처럼 표시할 수도 없다.
`refined_requirements`의 문장 자체를 국소 수정하는 adapter도 현재는 없다.

감사 중에는 계획기가 공개 workspace의 `current_stage` 대신 존재하지 않는 `stage`를 읽어,
Design에서 Requirements owner로 돌아가는 수정도 `ready_local`로 판정하는 결함을 발견했다.
이 필드 연결은 즉시 수정했으며 같은 UC5 계획이 이제
`earlier_delivery_stage_requires_confirmation`으로 멈춘다.

따라서 다음 구현에서 새 상태기계나 범용 relationship 병합기를 먼저 만들지 않는다.

1. 첫 Requirements 기준 경로는 이미 국소 편집을 지원하는 `use_case_spec:{id}`로 제한한다.
2. 이 경로는 선택한 spec과 같은 stage 형제의 무변경을 검증하고 사용자 검토에서 멈춘다.
3. 이 검토에서는 일반 `ADVANCE`를 노출하지 않는다. revision command를 종료하고 현재 artifact와
   RTM에서 새 plan을 만드는 명시적 action만 제공한다.
4. 소진된 원래 plan의 downstream을 실행 권한처럼 재사용하지 않는다. relationships 또는 Design
   갱신은 현재 버전에서 새 `RevisionPlan`과 새 사용자 승인을 받아 각각 별도 owning-stage
   command로 실행한다. relationships를 실행할 때 현재 지원 범위는 개별 관계가 아니라 승인된
   `requirements_stage:relationships` 전체와 diagram 재생성이다.
5. 새 spec 의미가 아직 존재하지 않는 class operation을 요구해 RTM 후보가 없으면 자동으로
   추측하지 않는다. broad class 재생성을 별도로 제안하거나 사용자 입력을 기다린다.
6. `use_case` 수정 뒤 specs 전체 재생성, `refined_requirements` 문장 수정, relationship 부분
   재생성은 정확한 adapter가 생기기 전까지 자동 실행 범위로 확대하지 않는다.
7. Requirements의 기존 선형 cascade를 범용적으로 고치는 작업은 첫 수직 경로의 비범위다.

이 제한은 일관성을 포기하는 것이 아니다. downstream을 묵시적으로 전체 재생성하는 대신,
현재 RTM으로 다시 계획하고 사용자에게 다음 변경 범위를 확인받는 일반 대화형 흐름이다.

## 9. 개발 중 Codex 서브에이전트 활용

이 절의 Luna·Terra·Sol·Astra는 EasyDep 제품의 런타임 에이전트가 아니다. 네 런타임
에이전트를 구현하고 검토할 때만 사용하는 개발 보조자다.

서브에이전트는 서로 겹치지 않는 작은 파일 단위만 맡는다.

| 역할 | 맡길 작업 | 맡기지 않는 작업 |
|---|---|---|
| Luna | Question·Decision 순수 변환, action read model, 작은 fixture와 단위 테스트 | RTM 권한 판정, 저장 transaction, stage orchestration |
| Terra | 기존 Workspace command 연결, owner routing, 재개·중복 실행 통합 테스트 | LLM prompt 확장, UI 의미 추측, 새 DB schema |
| 주 에이전트 | 계약 동결, 변경 통합, 회귀 테스트와 범위 축소 | 미확정 계약의 병렬 구현 |
| Sol | 각 하위 작업 뒤 치명적 권한·stage 경계·재호출 결함 검토 | 새 기능 제안과 범위 확대 |
| Astra | 방법론이나 시스템 경계가 바뀌는 예외적 고위험 검토 | 일상적인 코드 재검토 |

개발 보조자는 파일 소유 범위가 겹치지 않는 작은 구현·검토에만 사용한다. 그 결과를 제품의
새 agent role이나 workflow 단계로 추가하지 않는다.

## 10. 비범위

- MySQL schema 변경
- workspace 내부 lock 또는 실제 MySQL 다중 process 동시성 보증
- 범용 workflow DSL
- 임의 SQL 변조와 모든 manifest 손상 방어
- 시스템 전체 accepted-head manifest와 reader/writer 교체
- post-change RTM 전체 diff와 unknown 관계 추측
- 첫 수직 경로에서 API·ERD·배포·구현을 모두 재생성하는 기능
- effect·obligation ontology 확장
- 임의의 도메인 관계를 분류하는 공통 ontology 또는 완결성 증명기
- admission·planner·reviewer를 별도 런타임 에이전트로 추가하는 것
- 모델별 latency 최적화

## 11. 첫 완료 기준

첫 구현은 다음 문장이 실제 Workspace 통합 테스트로 성립할 때 완료다.

> 설계에서 발견한 UC 명세 공백에 대한 사용자 Decision이 별도 Requirements command를
> 실행하고 정확한 UC 명세만 수정한 뒤 사용자 검토에서 멈춘다. 현재 RTM으로 새 Design plan을
> 만들고 사용자가 승인하면 별도 Design command가 class를 수정·검토하며, sequence는 LLM 호출
> 없이 투영된다. 어느 stage도 이전 stage의 산출물을 직접 수정하지 않고, 중복 답변과 실패
> 재개가 불필요한 LLM 재호출을 만들지 않는다.

이 경계가 검증된 뒤 Implementation과 Testing의 같은 경계를 차례로 점검한다.

Implementation의 첫 추가 완료 기준은 다음과 같다.

> Implementation 하위 작업이 RTM으로 연결된 UC·API·class 문맥을 보고 현재 설계를
> 그대로 구현할 수 있으면 OpenHands를 실행한다. 제품 의미를 선택해야 하면 OpenHands를
> 시작하지 않고 기존 Question UI에 선택지와 자유 답변을 노출한다. 답변이 Design 산출물을
> 바꾸면 별도 Design command로 이동한 뒤 영향받은 Implementation 하위 작업만 재개한다.
