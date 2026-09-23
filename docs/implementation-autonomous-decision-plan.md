# 공개 계약의 상류 완결성과 구현 자율성 계획

- 작성일: 2026-09-23
- 상태: 핵심 개선 적용 완료 (실제 UI 종단 검증은 별도)
- 범위: 요구사항·설계 단계의 공개 동작 계약 검증과 구현 handoff

## 1. 목표와 원칙

구현 agent가 자율적으로 판단할 영역은 공개 계약 내부의 구현 메커니즘이다. 공개 입력, 출력,
동작 의미가 빠졌거나 서로 맞지 않으면 구현 전에 요구사항·설계 단계에서 계약을 완결한다.
구현 단계에서 공개 API를 진화시키거나 계약 결정을 새로 만드는 경로는 두지 않는다.

상류 단계는 필요한 사용자 질문과 계약 수정이 끝나면 완성된 산출물을 구현에 넘긴다. 구현 agent는
내부 클래스 협력, 기존 규약에 맞는 변환, 오류 처리 같은 메커니즘을 자율적으로 결정하고 근거를
기록한다. 구현 중 발견한 상류 누락을 이유로 요구사항과 설계를 반복 재생성하는 loop는 만들지
않는다. 계약이 불완전하면 소유 artifact를 좁게 고쳐 관련 chain을 다시 확인한 뒤 handoff한다.

## 2. 두 가지 의미 검증 gate

### 2.1 요구사항 gate: use-case 계약 리뷰

각 use case가 구현 가능한 공개 동작을 정의하는지 검토한다. 최소한 다음 항목을 확인한다.

- actor와 인증 상태
- preconditions
- 동작에 필요한 정보와 그 의미
- 관찰 가능한 결과
- identity 및 delegation policy: 누구의 권한으로 동작하는지, 대리 동작이 가능한지

LLM은 자연어의 의미를 검토하고 빠진 관계나 모순을 찾는 데 쓴다. 질문은 제품 동작에 실제로
여러 유효한 해석이 남은 경우에만 사용자에게 하나로 좁혀 묻는다. 기계적으로 확인 가능한 필드나
값의 출처를 LLM 판단으로 대체하지 않는다.

요구사항 orchestration의 현재 graph 순서는
`app/requirements/orchestration/graph.py:209-270`에 정의되어 있다. gate는 요구사항 산출물이
승인되기 전에 적용한다.

### 2.2 설계 gate: 단계별 필수값 provenance와 closure

use-case 계약의 각 필수값이 설계의 class/sequence에서 실제 API 경로까지 끊김 없이 전달되는지
확인한다. 단, 이를 마지막에 한꺼번에 판정하지 않는다. 각 단계의 소유 계약을 확인하고 통과한
산출물만 다음 단계로 넘기는 waterfall 방식으로 구성한다. 검증 경로는 다음과 같다.

`use case → class/sequence → API path/query/body 또는 trusted context → Control → result`

요구사항 명세에서 공개 동작·값 출처를 확정한다. 값 출처는 호출자가 제공하는 입력,
인증된 주체의 신뢰 가능한 문맥, 시스템이 생성하는 결과를 구분한다. 클래스 단계에서 필요한 Control 인자·반환값과
Boundary→Control 협업 출처를 확인한다. 시퀀스 단계는 승인된 클래스 협업의 호출·인자 흐름을,
API 단계는 승인된 협업마다 endpoint가 있는지와 Control 계약의 HTTP 입력·응답 투영을 확인한다.
각 값에 대해 타입, 출처,
전달 대상 및 소비 지점을 결정적으로 대조한다. actor identity처럼
인증된 주체에서 나와야 하는 값은 trusted context에서 유래해야 한다. request body의 임의 값은
명시적으로 허용된 delegation policy가 없는 한 그 identity를 증명하지 못한다. 필드 누락,
타입 불일치, 미전달, 소비되지 않는 입력은 정적 규칙으로 잡는다. 구조적 검증만으로 결론이 나지
않는 자연어 의미에 한해 LLM 검토를 보조적으로 호출한다.

예를 들어 UC2의 actor는 인증된 Student이고 class/sequence가 요구하는 `studentId`는 그 학생을
가리킨다. 설계는 인증된 principal과 `studentId`의 연결을 신뢰 가능한 출처로 명시해야 한다.
사용자가 body에 넣은 `studentId`를 그대로 신뢰하는 것은 delegation이 명시적으로 허용된
계약이 아닌 한 충분하지 않다. 이는 한 use case에만 적용할 예외가 아니라 일반 provenance 규칙의
사례다.

현재 설계 graph 순서는 `app/design/graphs/subgraphs.py:100-106`에 정의되어 있다. 각 단계의
미해결 finding은 해당 단계 승인 전에 처리한다. 설계를 완료한 뒤 구현을 시작할 때 같은 검증을
다시 실행하지 않는다. 앞 단계에서 이미 판정한 의미·출처를 하류 단계에서 중복 심사하지 않는다.
하류에서 처음 발견한 진짜 상류 결함에만 해당 소유 산출물을 좁게 수정하고 영향받은 투영을
재확인한다. 이를 정상적인 반복 경로로 만들지 않는다.

## 3. 재사용할 검사와 연결 지점

기존 검증을 확장·조합해 gate를 구성하고, 이미 검출 가능한 결함을 새 규칙으로 중복 구현하지
않는다.

| 현재 검사 | 역할 |
|---|---|
| `app/design/services/sequence_diagram/validation.py:820` `sequence.argument-data-flow` | sequence 인자 데이터 흐름 확인 |
| `app/design/knowledge/detectors.py:944` `api.executable-schema-fields` | 사용되었지만 비어 있는 DTO 필드 검출 |
| `app/design/knowledge/detectors.py:761` `api.control-arguments-match` | API와 Control 인자 일치 확인 |
| `app/design/knowledge/detectors.py:1121` registry | 설계 검출기 등록 |
| `app/design/validation.py:95` `design_readiness_report` | readiness 검사 집계 |

현재 검사 집합은 sequence→API→Control 중심이다. 요구사항 use-case의 필수값과 identity policy를
출발점으로 포함하되, 단계 소유권에 맞춰 class의 필수값 생산·소비, sequence의 호출 데이터 흐름,
API의 endpoint coverage 및 path/query/body/trusted context·응답 투영을 나누어 검증한다. 같은 결함을 여러 단계에서
서로 다른 규칙으로 반복 검출하지 않는다.

기존 `artifacts/checkpoint-e2e/current/e1-aws/chain/stages/07-sequence_diagram-to-api_spec/output/api-model.json`
의 빈 DTO는 2026-08-27에 만들어졌다. `api.executable-schema-fields` 규칙은 2026-09-02 커밋
`035bf05`에서 추가됐으므로, 이 저장 사례는 현재 gate를 통과했다는 증거가 아니다. 새 빈 DTO
검출기를 만들지 않고 현재 규칙을 재사용한다.

## 4. 실패 처리와 구현 handoff

검증 실패는 결함이 있는 artifact의 소유 단계에서 수정한다. 그 수정에 영향을 받는 chain만 다시
검증하고 승인한다. 명세상 의미 선택지가 실제로 해결되지 않은 경우에만 사용자 질문을 한 번
만들고, 답을 반영해 해당 artifact와 영향받은 검증을 갱신한다. 전체 요구사항·설계 생성 과정을
매번 다시 도는 반복 loop는 허용하지 않는다.

### 4.1 사용자 질문과 자발적 피드백

사용자 상호작용은 두 경로다.

1. **시스템이 묻는 경우:** 요구사항의 공개 동작이나 권한 정책에 둘 이상의 타당한 해석이 남아
   agent가 임의로 선택하면 제품 동작이 달라질 때만 질문한다. 질문은 해당 UC·근거 문장·미결정
   사항을 짧게 제시하고, 선택지마다 결과가 어떻게 달라지는지 자연어로 설명한다. 내부 메서드명,
   `flow_step`, RTM 경로 같은 구현 세부사항을 사용자에게 결정하라고 요구하지 않는다. 한 번에
   하나의 결정만 묻고, 답이 오면 그 단계의 산출물과 관련 하류 투영에만 반영한다.
2. **사용자가 먼저 고치는 경우:** 사용자는 승인 전후 어느 산출물에든 자연어 피드백을 줄 수 있다.
   시스템은 변경 의도를 해당 소유 단계에 연결하고, 영향받는 산출물만 다시 생성·검증한다.
   사용자가 공개 동작을 바꾸는 피드백을 준 경우에는 API만 고쳐 상류 계약과 어긋나게 만들지
   않고, 먼저 요구사항 또는 클래스 계약을 고친 뒤 시퀀스·API 투영을 갱신한다.

필드 누락, 타입 불일치, 선언된 값의 미전달처럼 소유 단계에서 결정적으로 고칠 수 있는 결함은
질문으로 돌리지 않는다. 반대로 인증 주체·대리 동작 여부처럼 실제 제품 정책이 빠진 경우에는
LLM이 하나를 추측해 확정하지 않는다. 질문·답변·적용된 산출물 변경은 기존 feedback 기록 경로에
남겨 사용자가 나중에 왜 달라졌는지 확인할 수 있게 한다.

예를 들어 수강신청에서 신청 대상이 **현재 인증된 학생**인지, **사용자가 지정한 다른 학생**도
허용하는지가 요구사항에 없다면 두 동작의 차이를 선택지로 묻는다. 사용자가 전자를 선택하면
클래스 단계는 typed principal의 신뢰 가능한 출처를 사용하고, API에 학생 ID 입력을 임의로
추가하지 않는다. 후자를 선택하면 대리 신청 권한·입력 정책을 요구사항에 먼저 명시한다.
이미 정책이 정해졌는데 클래스·API 표현만 틀린 경우에는 다시 사용자에게 선택을 요구하지 않는다.

두 gate에서 계약 발견사항이 해소되고 산출물이 승인된 경우에만 구현으로 handoff한다. 이 승인
상태를 구현 시작 시 재검사하지 않는다. 구현 agent는
이후 내부 메커니즘에 자율권을 갖되, 공개 동작을 바꾸는 요구나 누락을 발견하면 API를 임의로
진화시키지 않고 해당 상류 계약 finding으로 돌려보낸다. 구현 정책은 기존 상위 계약을 따른다.

## 5. 범위, 검증 및 완료 기준

초기 개선 범위는 다음과 같다.

- 요구사항 use-case semantic review와 필수 항목 확인
- 설계 artifact 간 generic required-value provenance/closure 검증
- 기존 detector 및 readiness report의 gate wiring 추적과 필요한 보완
- owning artifact만 수정하고 영향받은 chain만 재검증하는 실패 흐름
- 구현 handoff 시 공개 계약이 완결되었음을 나타내는 readiness 결과

주요 접점은 `app/requirements/orchestration/graph.py`,
`app/design/graphs/subgraphs.py`,
`app/design/services/sequence_diagram/validation.py`,
`app/design/knowledge/detectors.py`, `app/design/validation.py` 및 각각의 좁은 검증 테스트다.
구현 전 기존 테스트 구조를 확인해 테스트 위치와 구체 사례를 정한다.

완료 조건:

1. use-case 리뷰가 actor, preconditions, 필요한 정보, 결과, identity/delegation policy의 누락을
   승인 전 드러낸다.
2. design gate가 use-case 필수값을 API 입력 또는 trusted context에서 Control/result까지
   추적하고, 타입·출처·전달·소비 결함을 결정적으로 거부한다.
3. 자연어 의미가 남은 경우에만 targeted LLM 검토가 보조하며, 제품상 모호성이 해소되지 않을 때만
   단일 사용자 질문을 만든다.
4. 실패 후 소유 artifact와 영향받은 chain만 재검증하고, 상류 전체 재생성 loop에 진입하지 않는다.
5. gate 통과가 구현 handoff 조건이며, 구현 agent는 승인된 공개 계약 안에서 내부 메커니즘을
   자율적으로 선택할 수 있다.

초기 단계에서는 넓은 feasibility LLM gate, DB schema 변경, 과거 checkpoint migration, broad
E2E를 범위에 넣지 않는다. checkpoint 원인 추적은 현재 실패가 gate wiring 또는 오래된 산출물과
관련 있는지 확인하는 데 한정한다. 초기 구현 규모는 gate 연결과 기존 검사 재사용 정도를 확인한
뒤 산정한다.

## 6. 적용 현황과 검증

- 요구사항 명세에 근거 ID·값 출처·타입·용도를 담는 `public_contract`를 추가했다. 해당 UC의
  기능 요구사항 밖 근거 인용과 중복 선언을 검사하고, 기존 명세 의미 리뷰가 actor·선행조건·
  필요한 정보·결과·identity/delegation 누락을 원문과 대조한다. 실제 GLM 5.3 Flash 호출에서
  결과값을 인증 문맥으로 잘못 분류한 사례를 확인해 `system_result` 출처를 분리했다.
- 결정론적 명세 결함을 먼저 처리한 뒤 clean UC와 연결된 요구사항 원문만 한 번의 모델 검토에
  제공한다. 제품 정책에 두 해석이 남은 경우에만 UC·근거 문장·서로 다른 결과를 담은 두 선택지를
  기존 Workspace `Question`으로 만든다. 모델 검토 결과를 단계 state에 저장해 interrupt 재개 시
  재호출하지 않는다. 답변은 해당 UC 명세의 local revision으로 전달한다. 실제 설정 모델 호출에서
  상충하는 예시 문장에는 질문을 만들지 않았고, 별도의 명확한 정책 공백 예시에는 원문에 존재하는
  근거와 정확히 두 선택지를 가진 질문을 반환했다.
- 클래스 연산 생성에는 caller input·trusted context·system result 구분을 전달한다. 승인 검사에는
  필수값을 실제 Control 호출·인자 바인딩 또는 결과에 연결하는 UC별 semantic review를 더했다.
  응답이 인용한 연산·호출·파라미터·필드의 존재, Control 전달, 인증 문맥 값의 `context#` 출처를
  결정적으로 확인한다. 모델·공개 계약 digest에 묶인 검토 근거를 `class_diagram_check`에 저장하고,
  readiness/재개에서는 모델을 다시 호출하지 않는다. 실제 설정 모델은 합성 한 UC에서 올바른
  Control 호출과 바인딩을 인용했고, 그 응답의 정적 인용 검사를 통과했다.
- API는 승인된 BCE 상호작용의 endpoint coverage, Control 인자·응답, DTO 필드 및 trusted
  context 출처를 검사한다. API가 첫 직접 Control만 표현하는 현재 계약에 맞춰 클래스 단계에서
  각 Boundary root의 직접 Control handoff를 정확히 하나로 제한했다. 다른 Control과의 협력은
  그 Control 아래에서 표현한다. 구현 시작 시 같은 계약을 다시 검사하지 않는다.
- 변경 범위의 집중 검사 49개가 통과했고, 이후 추가한 인증 문맥 출처 검사 3개,
  Workspace 질문의 버전 고정·두 선택지 변환 검사 1개 및 선택 답변의 requirements 라우팅
  검사 1개도 통과했다. 전체 E2E를 새로 만들거나 DB 스키마·과거 checkpoint 호환을 변경하지
  않았다.

## 7. 남은 증거 범위

- Workspace 질문 객체의 카탈로그 버전 고정·두 선택지 변환과 선택 답변의 requirements
  라우팅은 집중 검사로 확인했다. 다만 질문 표시→선택→영속화→해당 UC 재생성을 한 요청
  연쇄로 검증하는 통합 검사는 수행하지 못했다. 기존 local revision 경로를 재사용하지만,
  실제 앱 UI 동작까지 입증한 것은 아니다.
- 자연어 필수값과 DTO 내부 필드의 의미적 동일성은 이름 일치 규칙으로 결정하지 않는다. 클래스
  검토 모델의 판단과 그가 인용한 실제 선언·호출에 근거하며, 결정론 검사는 존재·타입·바인딩·
  provenance를 확인한다. 이 구분을 전체 자연어 의미의 형식적 증명으로 과장하지 않는다.
