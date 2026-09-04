# Implementation runtime

`app.implementation.runtime`는 구현 작업자와 검증 작업자를 실행하는 OS/container
경계다. 프로세스 트리 종료, Docker 경로 변환, Linux runner와 scaffold 진입점을
한 곳에서 관리한다. 구현이 끝난 뒤에는 생성된 Dockerfile·Spring 설정·소스에서
port, health 경로, 환경 변수와 mount 사용을 읽어 배포 단계에 전달한다.

## 계약

- **입력:** 실행할 command, cwd/env, encoding과 timeout, 작업공간·container 경로,
  member scaffold 요청과 runtime 설정, 검증된 workload graph와 생성 애플리케이션 경로.
- **출력:** `CompletedProcess` 또는 명시적인 timeout/exit 오류, worker가 만든
  scaffold·로그·검증 artifact, 실제 생성 파일에서 확인한 workload별 실행값. 종료된
  프로세스의 stdout/stderr는 호출자가 요청한 경우에만 전달한다.
- **부수효과:** subprocess/container를 만들고 timeout 시 전체 자식 트리를 종료한다.
  scaffold와 runner는 요청받은 작업공간에 파일을 만들거나 갱신할 수 있으며,
  runtime hook은 프로세스 환경에 경로 어댑터를 설치한다.
- Linux 멤버 runner는 공용 툴체인 이미지에 저장된 예전 Python 진입점을 사용하지 않고 현재 저장소의
  `app.implementation.runtime.member_linux_runner`를 명시한다. Gradle 캐시는 Windows 공유
  경로가 아닌 `easydep-member-gradle-cache` Docker volume에 두고 여러 workflow가 재사용한다.
  volume이 처음 비어 있으면 개발 환경 준비 스크립트가 만든 `.easydep/gradle-cache`를 한 번
  복사한다. 툴체인에는 고정 Gradle이 이미 설치되어 있어 wrapper 배포본은 다시 받지 않는다.
- `EASYDEP_TOOLCHAIN_IMAGE`가 설정되면 서버가 계획한 구현 phase는 같은 API 흐름을
  유지한 채 Linux runner에서 실행한다. 이미지가 설정되지 않은 개발 환경에서만 호스트
  Python 실행 경로를 사용한다.
- Linux runner에는 Docker 소켓을 공유하지 않는다. 각 작업은 수정 범위에 맞는 빠른 검사를
  통과한 뒤 산출물을 저장하며, 전체 테스트와 Docker image·health 검사는 Testing 단계가
  같은 산출물 snapshot으로 실행한다.
- **사용하면 안 되는 import:** `app.core` 레거시 경로와 요구사항/설계 서비스 내부, Cloud KB,
  LLM 호출이나 orchestration graph에 의존하지 않는다. runtime 관찰은 전달받은 workload
  항목과 생성 파일만 읽으며 배치나 provider를 선택하지 않는다.
- **실패 조건:** command/cwd가 없거나 경로가 container 규칙과 맞지 않음,
  worker non-zero 종료, timeout 후 트리 종료 실패, scaffold 계약 위반이 있으면
  원래 원인과 함께 실패한다. 계획한 port·health 경로와 생성 파일이 서로 다를 때도
  추측해서 맞추지 않고 실패한다. 자식 프로세스가 남은 상태를 성공으로 보고하지 않는다.
