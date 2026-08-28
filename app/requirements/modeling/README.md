# Requirements modeling stage 경계

`app.requirements.modeling`은 정제된 요구사항에서 actor, use case, Cockburn 명세,
관계 모델과 use-case PlantUML을 만드는 단계 서비스다. 각 단계는 typed proposal을
받아 정규화하고 결정론적으로 검증한 뒤, 진전과 미사용 전략이 있는 동안 repair하여 accepted
state patch를 반환한다. graph 순서, feedback cascade, supervisor 재실행은 이
패키지의 책임이 아니다.

## 입력

- `refinement`는 immutable `raw_requirements`와 선택적 expansion 결과를 받는다.
- `use_cases`는 분류가 끝난 `RequirementItem`과 앞 단계의 actor/use-case 결과를
  받으며, 요구사항과 actor의 stable ID만 참조한다.
- `specifications`는 accepted use case, actor, requirement trace와 선택적 stage
  feedback을 받는다.
- `relationships`는 accepted actor/use-case/specification을 받으며 candidate에
  포함되지 않은 use case나 scenario step을 새로 만들 수 없다.
- `diagram`은 accepted relationship patch만 받아 결정론적으로 PlantUML을 만든다.
  typed proposal 호출은 테스트와 평가에서 `proposal_call`로 주입할 수 있다.

## 출력

- 정제 단계는 RAW provenance를 가진 stable RR draft, constraint link와 source
  finding을 반환한다.
- actor/use-case 단계는 canonical actor와 traceable use case, coverage 및 model
  review patch를 반환한다.
- specification 단계는 입력 use-case 순서를 보존하는 `use_case_specs`와 repair
  상태를 집계한 `spec_report`를 반환한다.
- relationship 단계는 association/include/extend/generalization과 review 결과를
  담은 기존 JSON shape를 반환한다. diagram 단계의 출력은 기존 `diagram` key와
  byte-compatible PlantUML 문자열이다.

## 부수효과와 호출 범위

- proposal과 semantic validation만 structured LLM을 호출하며 파일, checkpoint,
  HTTP 또는 repository를 직접 갱신하지 않는다.
- Step 2의 독립 trace audit와 Step 3의 use-case별 명세 생성은 기존 concurrency와
  `ContextVar` 전파를 유지한다.
- specification과 relationship repair는 숫자 상한 대신 입력·finding·전략·후보 digest
  이력을 따른다. 같은 실패를 반복하지 않고 진전하거나 미사용 전략이 있는 동안 계속한다.
  semantic validator의 vote 수와 confirmation 호출은 runtime 설정을 그대로 따른다.
- 빈 입력 또는 이미 accepted된 결정론 projection은 불필요한 proposal 호출을 하지
  않는다. logical/physical 호출 집계는 `runtime.telemetry`의 기존 operation 이름과
  shape를 사용한다.

## 금지 의존성

- modeling 서비스는 requirements graph, HTTP API, runner, session/checkpoint 또는
  artifact repository를 import하지 않는다.
- `app.design`, `app.implementation`, `app.orchestration`, `app.workspace`를 역참조하지
  않는다.
- graph state 전체를 private helper로 파싱하거나 prompt literal을 외부 계약으로
  노출하지 않는다. 공개 입력은 `AgentState`/TypedDict/Pydantic 계약이고 공개 출력은
  `ModelingStagePatch` 또는 구체 typed 결과다.
- 단계 순서, batch/group, feedback cascade와 supervisor rerun 범위는
  `stage_registry` 및 orchestration 계층만 소유한다.

## 실패 조건

- proposal이 schema를 만족하지 못하면 structured runtime의 기존 JSON fallback과
  retry 정책을 따른다. 부분 응답을 accepted 결과로 저장하지 않는다.
- 알려지지 않은 requirement, actor, use-case 또는 step 참조는 정규화 과정에서
  finding/dropped reference로 표면화하며 조용히 새 ID를 만들지 않는다.
- semantic vote가 실패하거나 모든 규칙을 검사하지 못하면 clean으로 간주하지 않고
  기존 `failed`/`ungrounded`/`unexamined_rules` 상태를 보존한다.
- 같은 입력의 전략이 소진되거나 개선되지 않으면 마지막 accepted candidate와
  `repair_stopped` 사유를 반환하여 supervisor가 공개 finding만으로 재실행 범위를
  결정하게 한다.

## 호환 경계

기존 `app.requirements.agent.steps.step1_requirements`부터 `step4_diagram`까지의 import는
canonical modeling 함수를 재노출하는 얇은 facade다. HTTP, `AgentState`, 저장 JSON,
PlantUML과 stage operation 이름은 유지한다. 호출되지 않는 resource tool-agent 경로는
이 modeling 리팩터링의 삭제 대상이 아니며, 별도의 dead-path 근거 없이 제거하지
않는다.
