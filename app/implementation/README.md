# 구현 단계

`app.implementation`은 설계 산출물을 소스 코드, 테스트, 컨테이너 파일과 IaC로 바꾸고 필수
검사를 통과시킨다. IaC는 Terraform처럼 클라우드 자원을 코드로 정의한 파일을 뜻한다.
“코드를 한 번 생성하는 기능”이 아니라 작업 계획, 제한된 LLM 편집, 컴파일과 테스트,
실패 repair, 배포 파일 검사까지 포함하는 실행 단계다.

## 큰 흐름

```text
설계 readiness 검사
  → 구현 Job 생성
  → 설계 산출물을 job의 design-context로 고정
  → 기본 프로젝트와 공개 계약 생성
  → 구현 작업 계획
  → 허용된 파일만 볼 수 있는 작업공간에서 LLM agent 실행
  → 컴파일·단위/E2E 테스트
  → 실패 원인을 담당하는 작업만 골라 repair
  → 소스/설계 정합성 및 배포 산출물 검사
  → 결과 저장과 승인 요청
```

## 디렉터리 지도

| 디렉터리 | 책임 |
|---|---|
| `application/` | job 생성·상태 전이, prototype subprocess와 feedback 접수 |
| `domain/` | 구현 단계가 공유하는 모델과 소스 분석용 중간 표현(IR) |
| `generation/` | typed BCE Java 초기 코드, OpenAPI·프론트엔드 생성 |
| `planning/` | 설계 context를 작은 구현 task와 용량·provider 결정으로 변환 |
| `agents/` | 허용된 파일만 편집하는 LLM agent와 build·E2E·release 검사 |
| `workflows/` | 작업 조정, repair, 요구사항 추적, 완료와 설계 일치 검사 순서 |
| `delivery/` | Docker·Terraform·가상 머신 배포 파일 생성과 검사 |
| `runtime/` | subprocess, Docker 경로와 Linux runner 실행 경계 |
| `interfaces/` | FastAPI와 CLI 요청·응답 모델 |
| `tools/` | Gradle wrapper처럼 버전을 고정해야 하는 실행 도구 |

## 식별자와 디렉터리

- `app_id`: 사용자가 만든 애플리케이션.
- `job_id`: 한 번의 구현 실행.
- `run_id`: job 안에서 생성된 prototype 실행본.
- `task_id`: 한 구현 작업 또는 repair 작업.

기본 작업 위치는 `.easydep/implementation-runs/<job_id>/`다. `design-context/`는 실행 시작
시점의 입력 snapshot이고 `generated/runs/<run_id>/`가 실제 결과다. 실패를 조사할 때 서로
다른 job이나 run의 보고서를 섞지 않는다.

## 상태를 읽는 법

| 상태 | 의미 |
|---|---|
| `VALIDATING_INPUT` | 설계 산출물과 구현을 시작할 조건을 확인한다. |
| `GENERATING_SOURCES` | 기본 프로젝트와 변경 금지 계약을 만든다. |
| `PLANNING` | LLM agent가 맡을 작은 작업 단위를 계산한다. |
| `RUNNING` | 작업자와 검증기를 실행한다. |
| `AWAITING_APPROVAL` | 사람이 확인해야 할 외부 변경이나 repair가 있다. |
| `COMPLETED` | 모든 필수 검사를 통과하고 결과 저장을 마쳤다. |
| `FAILED` | 재개 지점과 원인을 상태·보고서에 남기고 중단했다. |

## 계약과 안전 규칙

- 실행 시작 때 고정한 설계 snapshot을 실행 도중 최신 버전으로 교체하지 않는다.
- agent는 할당된 파일만 수정한다. shell이나 저장소 전체 탐색 권한을 주지 않는다.
- `TODO`, `FIXME`, `UnsupportedOperationException` 같은 미완성 표식을 성공 산출물에 남기지 않는다.
- compile → test → 설계 정합성 검사 순서를 바꾸지 않는다.
- 외부 도구의 제한을 업무 규칙처럼 취급하지 않는다. EasyDep가 소유한 연결 코드에서 명시적으로
  변환하거나, 구현할 근거가 없는 계약은 설계 feedback으로 돌려보낸다.
- 실패 후에는 성공한 작업을 보존하고 실패한 작업과 영향받는 범위만 다시 실행한다.

## 검증

```powershell
python -X utf8 -m pytest -q tests/test_implementation_worker.py
python -X utf8 -m pytest -q tests/test_implementation_engine.py
python -X utf8 -m pytest -q tests/test_implementation_docker_paths.py
```
