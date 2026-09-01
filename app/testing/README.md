# Testing 에이전트

이 패키지는 EasyDep 자체를 평가하는 도구가 아니다. EasyDep이 생성한 애플리케이션을
단위 테스트, 정적 분석, IaC 분석, 동적 기능 테스트로 검사한다.

## 실행 흐름

```text
완료된 Implementation 작업
  → 그 작업이 기록한 파일 묶음 ID 확인
  → 임시 application 폴더에 파일을 한 번 복원
  → backend 전체 테스트와 frontend production build
  → Trivy로 Docker·배포 설정과 IaC 검사
  → OpenTofu로 fmt/init/validate와 선택적인 plan 검사
  → test profile과 임시 H2 DB로 Docker 실행
  → health endpoint의 HTTP 2xx 확인
  → 실행 중인 애플리케이션에 동적 기능 테스트 수행
```

Testing 작업 하나는 Implementation 작업 하나만 검사한다. 검사 도중 DB의 최신 파일을
다시 읽지 않으므로, 같은 앱에서 새 구현을 시작해도 현재 Testing 작업의 입력은 바뀌지
않는다. 복원된 파일의 SHA-256이나 `implementation_job_id`가 맞지 않으면 검사를 시작하지
않는다.

## 주요 경계

| 위치 | 역할 |
| --- | --- |
| `api.py` | Testing 작업 생성, 조회, 재검사 이력 관리 |
| `schemas/testing_input.py` | 앱 ID, Implementation 작업 ID, 파일 묶음 ID 목록 |
| `utils/artifact_source.py` | DB 파일을 임시 application 폴더에 한 번 복원 |
| `runtime/adapter.py` | backend 전체 테스트와 frontend build 실행 |
| `runtime/app_container.py` | 같은 application 폴더를 Docker로 build하고 health 확인 |
| `graphs/testing_graph.py` | 정적 분석과 동적 테스트 순서 조정 |
| `utils/opentofu.py` | 임시 사본에서 OpenTofu 정적 검사와 선택적인 dry-run 실행 |

배포 이미지 안에는 Trivy와 OpenTofu가 설치되어 있어 별도 도구 컨테이너를 만들지 않는다.
로컬에서 툴체인 이미지를 쓰지 않는 경우 Trivy만 고정 Docker 이미지를 예비 경로로 사용한다.
`TESTING_IAC_PLAN=true`이면 `plan -refresh=false`까지 실행한다. 이때 `.env`의 CSP 인증값과
`TF_VAR_*`만 OpenTofu에 전달하며, `apply`는 Testing 단계에서 실행하지 않는다.

## 실패 처리

- 필수 source 또는 deployment 파일 묶음 ID가 없으면 Testing 작업을 만들지 않는다.
- 파일 묶음이 다른 Implementation 작업에 속하거나 파일 digest가 다르면 실패한다.
- Docker build, container 시작, health 확인이 실패하면 동적 테스트를 성공으로 처리하지
  않는다. 시작 직후 종료된 컨테이너는 로그를 읽은 다음 제거한다.
- 정적 검사에서 실제 설정 문제가 발견되거나 동적 기능 테스트가 실패하면 전체 Testing도
  실패한다. 적용할 IaC 폴더가 없는 경우처럼 검사 대상 자체가 없으면 미실행 사실을 별도로
  표시한다.
- 동시에 실행되는 Testing 작업은 작업 ID를 반영한 서로 다른 Docker 이름을 사용한다.

임시 폴더와 Docker image/container는 해당 Testing 실행이 끝나면 정리한다.
