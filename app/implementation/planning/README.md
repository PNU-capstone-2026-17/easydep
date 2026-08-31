# Implementation planning

`app.implementation.planning`은 설계 산출물을 구현 작업과 프런트엔드 계약으로
정리한다. 코드를 생성하거나 배포를 실행하는 계층은 아니다.

## 계약

- **입력:** 구조화된 설계 산출물과 `JobSpec`. 선택적으로
  `requirements`/`refinedRequirements`, `useCases`/`useCaseSpecs` 입력을 받는다.
- **출력:** `ImplementationTask`와 프런트엔드 계약.
- **부수효과:** 계획 계산은 파일, 네트워크, 별도 프로세스, LLM을 호출하지 않는
  메모리 작업이다.
- **사용하면 안 되는 import:** `app.core` 레거시 경로와 설계 서비스 내부, 배포 실행기,
  workflow 내부 상태를 import하지 않는다. 설계에 없는 작업이나 계약을 추정하지
  않는다.
- **실패 조건:** 필수 설계 입력이 없거나 작업 식별자가 잘못되면 검증 오류를
  반환한다.

## 유스케이스 작업

- planner는 `bceModel`과 `apiModel`의 `use_case_ids`를 읽는다. 같은 Control을 공유하는
  유스케이스를 먼저 연결하고, 남은 단일 작업만 Entity 관계로 합친다. 한 작업에는 최대
  세 개만 넣는다.
- 같은 Controller나 Entity source를 여러 작업이 고치면 뒤 작업의
  `depends_on`에 앞 작업을 기록한다. 각 task JSON에는 `requirement_ids`,
  `use_case_ids`, `required_test_paths`도 함께 남긴다.
- 각 작업 context에는 관련 요구사항, use-case artifact, typed sequence scenario를
  넣는다. 해당 선택 입력이 없으면 빈 목록으로 명시하며, 설계에 없는 작업은 만들지
  않는다.
- API schema와 Control 연결 준비도는 planner 전에 Workspace가 확인·수리한다. planner는
  통과한 API 계약을 DTO나 Control로 추정해 바꾸지 않는다.
