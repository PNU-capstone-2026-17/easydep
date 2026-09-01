# 구현 작업 계획

`app.implementation.planning`은 설계 산출물을 구현 작업과 프런트엔드 계약으로
정리한다. 코드를 생성하거나 배포를 실행하는 계층은 아니다.

## 계약

- **입력:** 구조화된 설계 산출물과 `JobSpec`. 선택적으로
  `requirements`/`refinedRequirements`, `useCases`/`useCaseSpecs` 입력을 받는다.
- **출력:** `TaskSpec`, 작업별 짧은 prompt·context 파일과 프런트엔드 계약.
- **부수효과:** `reports/implementation-tasks`에 작업 계약을 저장한다. 네트워크, 별도
  프로세스와 LLM은 호출하지 않는다.
- **사용하면 안 되는 import:** `app.core` 레거시 경로와 설계 서비스 내부, 배포 실행기,
  workflow 내부 상태를 import하지 않는다. 설계에 없는 작업이나 계약을 추정하지
  않는다.
- **실패 조건:** 필수 설계 입력이 없거나 작업 식별자가 잘못되면 검증 오류를
  반환한다.

## 유스케이스 작업

- planner는 `bceModel`과 `apiModel`의 `use_case_ids`를 읽는다. 같은 Control이 처리하는
  유스케이스만 한 기능 작업으로 묶는다. 같은 Boundary, Entity 또는 Controller를 공유한다는
  이유로 서로 다른 Control의 작업을 다시 합치지 않는다.
- 여러 작업이 같은 파일을 수정하면 `depends_on`으로 순서만 정한다. 공유 Controller에서는
  현재 작업에 해당하는 HTTP 메서드와 경로의 미완성 표식만 검사한다.
- 서로 다른 기능 작업은 수정 파일과 package가 겹치지 않을 때만 병렬 실행할 수 있다. 한
  기능만 사용하는 package에서는 OpenHands가 helper 파일을 추가할 수 있고, 여러 기능이
  공유하는 package에서는 계획에 기록된 파일만 수정한다.
- 각 task JSON에는 `requirement_ids`, `use_case_ids`, `required_test_paths`, 편집 파일과
  새 파일을 만들 수 있는 전용 package를 함께 남긴다.
- 각 작업 context에는 관련 요구사항, use-case artifact, typed sequence scenario를
  넣는다. 요구사항·유스케이스의 설계 수리 이력과 내부 진행 상태는 제외하고, 구현에 필요한
  본문·흐름·완료 조건과 시퀀스 호출·인자·반환 연결만 전달한다. 해당 선택 입력이 없으면 빈
  목록으로 명시하며, 설계에 없는 작업은 만들지 않는다.
- 프롬프트에는 관련 Control·Entity의 BCE 선언과 결정론적으로 만든 JPA Entity·Repository의
  정확한 선언을 넣는다. HTTP 변환은 생성된 Controller가 담당하므로 긴 OpenAPI model 구현
  전체를 다시 싣지 않는다. 대신 메서드·경로·Control 연결·응답 상태의 typed endpoint 정보만
  전달하며, 관련 없는 읽기 전용 파일을 탐색 후보로 나열하지 않는다.
- API schema와 Control 연결 준비도는 planner 전에 Workspace가 확인·수리한다. planner는
  통과한 API 계약을 DTO나 Control로 추정해 바꾸지 않는다.
- 구현과 테스트의 작성 순서는 prompt가 강제하지 않는다. OpenHands가 맡은 범위 안에서 순서를
  정하고, 끝날 때 기능 검사와 필요한 결과 파일로 완료 여부를 확인한다.
- wiring은 정상 경로에서 완료 상태로 건너뛰는 수리 전용 작업이다. 계획 시 전체 Java source를
  prompt에 복사하지 않고, 실제 오류가 생겼을 때 오류 근거와 관련 파일만 별도 지시로 만든다.
