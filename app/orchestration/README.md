# Orchestration

`app.orchestration`는 요구사항·설계·구현·테스팅 단계를 하나의 실행으로 연결하는
조정 계층이다. 각 단계의 내부 모델을 소유하지 않고, 공개 adapter와 버전이 붙은
계약을 통해 단계 순서·provider 선택·재시도·체크포인트를 관리한다.

## 계약

- **입력:** `RunRequest`(요구사항, 자원 제약, app/run 식별자, 실행 모드와
  `ProviderConfig`), 재개할 run 식별자와 체크포인트 상태.
- **출력:** `RunResult`, 단계별 `StageOutput`/`StepResult`, 진단·metrics와 실행
  artifact. 저장된 run은 동일한 schema version과 `run_id`로 재개할 수 있다.
- **부수효과:** 실행 디렉터리와 artifact를 만들고 SQLite/메모리 체크포인트를
  읽고 쓴다. 선택된 provider가 subprocess·LLM·파일 출력을 수행할 수 있으며,
  orchestration 자체는 그 결과를 계약으로 포장한다.
- **금지 의존성:** `app.core`의 이전 경로를 사용하지 않는다. 단계 내부 구현을
  직접 호출하거나 요구사항·설계·구현 모델을 조정 계층에 복제하지 않고, 공개
  `contracts`와 `adapters` 경계만 사용한다.
- **실패 조건:** 요청/ provider 설정이 계약을 위반하거나, lock·checkpoint가
  일치하지 않거나, 단계 provider가 실패/시간 초과하면 실패 상태와 원인 진단을
  남긴다. 이미 완료한 단계의 출력이나 run identity를 조용히 바꾸지 않는다.
