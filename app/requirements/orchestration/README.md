# Requirements orchestration 경계

`app.requirements.orchestration`은 이미 승인된 요구사항 단계들을 실행 순서에 맞게
조합하고, 대화형 중단·재개와 피드백 재실행, 현재 스키마 체크포인트 및 HTTP 응답을
조율한다. 단계별 proposal·normalization·validation·repair 규칙은 이 경계가 소유하지
않는다.

## 입력

- `stage_registry.PIPELINE`의 단계·그룹·논리 key 순서
- `AnalyzeRequest`와 현재 `AgentState` 계약
- 신규 실행의 요구사항·클라우드 제약 및 재개의 `thread_id`·답변·구조화 편집
- 현재 버전 LangGraph checkpoint와 runner 입력 JSON

## 출력

- `PIPELINE`에서 파생한 graph, batch, feedback cascade 실행 결과
- gate interrupt 또는 완료 상태를 담은 `AnalyzeResponse` 호환 payload
- supervisor 결정과 재실행 이력
- runner 산출물 트리·manifest와 현재 스키마 세션 checkpoint
- 저장소에 기록된 요구사항 단계 이름 목록

## 부수효과와 호출 범위

- graph는 gate 설정에 따라 interrupt를 만들고 같은 `thread_id`의 checkpoint를
  저장하거나 재개한다.
- runner와 API persistence는 명시된 artifact 디렉터리 또는 repository에 현재 단계
  산출물을 저장한다.
- runner의 run ID·manifest 정책은 상위 composition root가 callback으로 주입하며,
  이 경계가 cross-stage orchestration 구현을 직접 import하지 않는다.
- orchestration은 단계 서비스를 정해진 순서로 호출할 뿐, 단계 내부 LLM 호출 수,
  병렬도, retry 및 progress-aware repair 범위를 늘리거나 다시 소유하지 않는다.
- 테스트는 모든 LLM·repository 경계를 대체하며 실제 NIM 호출을 하지 않는다.

## 사용하면 안 되는 import

- modeling·resources 서비스의 prompt literal, private helper 또는 repair 구현을
  import하지 않는다.
- 설계·구현 서비스(`app.design`, `app.implementation`)를 역참조하지 않는다.
- 상위 cross-stage 조정 계층(`app.orchestration`)을 역참조하지 않는다.
- `PIPELINE` 순서를 graph·runner·feedback에 다시 하드코딩하지 않는다.
- 현재 계약에 없는 `Any`, bare `dict` 공개 signature를 새로 만들지 않는다.

## 실패 조건

- 필수 요구사항이나 재개용 `thread_id`가 없거나 상호 배타적인 답변 입력이 함께 오면
  요청을 거절한다.
- gate의 interrupt 또는 checkpoint가 없거나 현재 schema 검증에 실패하면 재개를
  완료된 실행처럼 처리하지 않는다.
- supervisor 재실행 budget이 소진되거나 도달할 수 없는 단계라면 범위를 확장하지 않고
  관찰 가능한 종료·degradation 결과를 남긴다.
- artifact repository의 앱이 없거나 저장이 실패하면 HTTP 경계에서 오류로 드러낸다.

## 호환 경계

- 기존 `app.requirements.agent.graph/subgraphs/supervisor`,
  `app.requirements.agent.steps.feedback_gates`, `app.requirements.feedback`,
  `app.requirements.runner`, `app.requirements.api`는 canonical 공개 객체만 재노출한다.
- 과거 requirements checkpoint shape 전용 MySQL parser·migration fallback은 제공하지
  않는다. `persistence`는 현재 LangGraph checkpoint schema의 save→resume만 보장한다.
