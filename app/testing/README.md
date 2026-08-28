# 테스팅 단계

`app.testing`은 EasyDep 저장소 자체를 테스트하는 디렉터리가 아니다. 구현 단계가 만든
애플리케이션을 별도의 입력으로 받아 정적 검사, IaC 검사와 실제 컨테이너 동작을 확인하는
파이프라인이다. 저장소 자체의 회귀 테스트는 루트 `tests/`에 있다.

## 실행 흐름

```text
구현 job 산출물
  → 구현 job이 저장한 snapshot 버전·digest를 TestingInput으로 고정
  → 같은 snapshot을 임시 application 폴더에 복원
  → 정적 소스·구성 검사
  → Terraform을 포함한 IaC 검사
  → 애플리케이션 컨테이너 빌드·기동
  → 동적 기능 검사
  → 단계별 증거와 최종 testing state 저장
```

## 디렉터리 지도

| 위치 | 역할 |
|---|---|
| `api.py` | testing job을 생성·조회하는 HTTP 경계 |
| `schemas/testing_input.py` | 한 Testing job이 끝까지 사용할 구현 작업과 snapshot 버전 |
| `graphs/testing_graph.py` | 검사 순서와 상태 전이를 정의 |
| `nodes/` | 정적·IaC·동적 검사를 한 단계씩 수행하는 graph node |
| `runtime/` | subprocess, Docker와 컨테이너를 실행하는 adapter |
| `schemas/testing_state.py` | 저장하고 이어서 실행할 testing state |
| `utils/` | artifact 선택, 요구사항 근거, 정적 분석과 보안 검사 도우미 |

## 계약

- **입력:** `COMPLETED` 상태인 구현 job ID와 그 작업이 저장한 SOURCE, DEPLOYMENT 등
  파일 snapshot의 version, digest, 생성 시각과 파일 수.
- **출력:** 검사별 상태, 명령 증거, 로그와 최종 성공/실패 판정.
- **실행하면서 바꾸는 것:** subprocess와 Docker container를 실행하고 보고서를 저장한다.
- **이 단계에서 수정하지 않는 것:** 설계나 구현 LLM의 내부 state와 이미 생성된 소스의 성공 상태.
- **주요 실패 원인:** 입력 누락, build 실패, timeout, 컨테이너 비정상 종료, 요구사항과 동작 불일치.

동적 검사 실패와 테스트 인프라 실패는 구분한다. 예를 들어 포트가 열리지 않은 것은 앱 실패일
수 있지만 Docker daemon에 접근하지 못한 것은 실행 환경 실패다. 보고서에는 재시도 여부를
결정할 수 있도록 원래 명령과 종료 원인을 남긴다.

## 구현 버전을 섞지 않는 방법

Testing API는 작업을 만들 때 구현 job 기록의 `artifact_version_ids`로 DB snapshot을 정확히
조회한다. 그 뒤 unit test, 정적 검사, IaC 검사와 Docker 실행이 모두 같은 `TestingInput`을
받는다. 검사 도중 같은 앱에서 새 구현이 완료되어 DB의 current version이 바뀌어도 이미 시작한
Testing job은 처음 고른 version만 다시 읽는다.

동적 검사를 위해 만드는 Docker image와 container 이름에는 Testing job ID에서 계산한 짧은
digest가 들어간다. 따라서 같은 앱의 서로 다른 구현 버전을 동시에 검사해도 한 작업이 다른
작업의 container를 삭제하거나 image를 덮어쓰지 않는다.

각 snapshot은 version 번호뿐 아니라 전체 digest, 파일 수, 생성 시각과
`implementation_job_id`를 다시 확인한다. 원래 구현 workspace가 서버 재시작이나 정리로
사라졌더라도 DB snapshot과 구현 job 기록이 남아 있으면 새 임시 폴더에 복원하여 테스트를
시작할 수 있다. 반대로 snapshot 출처가 다르거나 파일 SHA-256이 맞지 않으면 최신 파일이나
남아 있는 workspace로 조용히 대체하지 않고 입력 오류로 보고한다.

Docker image build나 애플리케이션 기동이 실패하면 정적 검사 결과는 그대로 남기지만 Testing
job 자체는 실패한다. 실행하지 못한 동적 검사를 `SKIPPED`로 표시했다는 이유만으로 전체 작업을
성공 처리하지 않으며, `APPLICATION_LAUNCH_FAILED` 진단에 원인을 함께 기록한다.
