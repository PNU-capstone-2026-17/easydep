# Implementation runtime

`app.implementation.runtime`는 구현 작업자와 검증 작업자를 실행하는 OS/container
경계다. 프로세스 트리 종료, Docker 경로 변환, Linux runner와 scaffold
진입점을 한 곳에서 관리한다.

## 계약

- **입력:** 실행할 command, cwd/env, encoding과 timeout, 작업공간·container 경로,
  member scaffold 요청과 runtime 설정.
- **출력:** `CompletedProcess` 또는 명시적인 timeout/exit 오류, worker가 만든
  scaffold·로그·검증 artifact. 종료된 프로세스의 stdout/stderr는 호출자가 요청한
  경우에만 전달한다.
- **부수효과:** subprocess/container를 만들고 timeout 시 전체 자식 트리를 종료한다.
  scaffold와 runner는 요청받은 작업공간에 파일을 만들거나 갱신할 수 있으며,
  runtime hook은 프로세스 환경에 경로 어댑터를 설치한다.
- **금지 의존성:** `app.core` 레거시 경로와 요구사항/설계 서비스 내부, Cloud KB,
  LLM 호출이나 orchestration graph에 의존하지 않는다. 구현 runtime은 계획·delivery 결과를 해석하지 않고
  실행 경계만 제공한다.
- **실패 조건:** command/cwd가 없거나 경로가 container 규칙과 맞지 않음,
  worker non-zero 종료, timeout 후 트리 종료 실패, scaffold 계약 위반이 있으면
  원래 원인과 함께 실패한다. 자식 프로세스가 남은 상태를 성공으로 보고하지 않는다.
