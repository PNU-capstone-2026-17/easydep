# Requirements orchestration 경계

`app.requirements.orchestration`은 이미 승인된 요구사항 단계들을 실행 순서에 맞게
조합하고, 대화형 중단·재개와 피드백 재실행, 현재 스키마 체크포인트 및 Workspace 결과를
조율한다. 단계별 proposal·normalization·validation·repair 규칙은 이 경계가 소유하지
않는다.

## 입력

- `stage_registry.PIPELINE`의 단계·그룹·논리 key 순서
- `AnalyzeRequest`와 현재 `AgentState` 계약
- 신규 실행의 요구사항·클라우드 제약 및 재개의 `thread_id`·답변·구조화 편집
- 현재 버전 LangGraph checkpoint

## 출력

- `PIPELINE`에서 파생한 graph와 feedback cascade 실행 결과
- gate interrupt 또는 완료 상태와 지금까지의 산출물을 담은 Workspace 결과 dict
- supervisor 결정과 재실행 이력
- 현재 스키마 세션 checkpoint
- 저장소에 기록된 요구사항 단계 이름 목록

## 부수효과와 호출 범위

- graph는 gate 설정에 따라 interrupt를 만들고 같은 `thread_id`의 checkpoint를
  저장하거나 재개한다.
- 공개 Workspace의 `retry_requirements`는 실패한 command를 사용자가 다시 실행하라고
  선택했을 때만 저장된 checkpoint 다음 node부터 실행한다. checkpoint가 없으면 빈 요구사항
  실행으로 바꾸지 않고 거절한다.
- application service는 현재 단계 산출물을 공용 repository에 저장한다.
- orchestration은 단계 서비스를 정해진 순서로 호출할 뿐, 단계 내부 LLM 호출 수,
  병렬도, retry 및 progress-aware repair 범위를 늘리거나 다시 소유하지 않는다.
- 테스트는 모든 LLM·repository 경계를 대체하며 실제 NIM 호출을 하지 않는다.

## 사용하면 안 되는 import

- modeling·resources 서비스의 prompt literal, private helper 또는 repair 구현을
  import하지 않는다.
- 설계·구현 서비스(`app.design`, `app.implementation`)를 역참조하지 않는다.
- `PIPELINE` 순서를 graph·feedback에 다시 하드코딩하지 않는다.

## 실패 조건

- 필수 요구사항이나 재개용 `thread_id`가 없거나 상호 배타적인 답변 입력이 함께 오면
  요청을 거절한다.
- gate의 interrupt 또는 checkpoint가 없거나 현재 schema 검증에 실패하면 재개를
  완료된 실행처럼 처리하지 않는다.
- supervisor 재실행 budget이 소진되거나 도달할 수 없는 단계라면 범위를 확장하지 않고
  관찰 가능한 종료·degradation 결과를 남긴다.
- artifact repository의 앱이 없거나 저장이 실패하면 Workspace 명령 실패로 드러낸다.

## 공개 경로

- graph, subgraph, supervisor, feedback gate는 이 디렉터리에서 직접 import한다.
  삭제한 `app.requirements.agent` 경로는 제공하지 않는다.
- Workspace는 `service.py`의 `analyze_requirements`와 `retry_requirements_analysis`를 호출한다.
- 과거 requirements checkpoint shape 전용 MySQL parser·migration fallback은 제공하지
  않는다. `persistence`는 현재 LangGraph checkpoint schema의 save→resume만 보장한다.
