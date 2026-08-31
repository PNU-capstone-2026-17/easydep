# 구현 단계

`app.implementation`은 설계 산출물을 소스 코드, 테스트, 컨테이너 파일과 IaC로 바꾸고 필수
검사를 통과시킨다. IaC는 Terraform처럼 클라우드 자원을 코드로 정의한 파일을 뜻한다.
구현은 파일마다 짧은 호출을 보내는 과정이 아니라, 관련 코드를 함께 읽고 수정하는 작업 단위와
그 결과를 검증·저장하는 실행 단계다.

구현을 실행하는 동안에는 작업이 끝날 때까지 기다리지 않아도 화면에서 생성 중인 소스를
볼 수 있다. 기존 구현 Job의 `run_root/application` 아래 UTF-8 text 파일만 읽으며, 현재
작업이 만들 예정인 파일은 `작성 중`으로 표시한다. 화면은 읽기 전용이고 `.env`, private key,
binary, build 결과와 1MB를 넘는 파일은 노출하지 않는다. Job이 끝나면 같은 분류 규칙으로
MySQL에 저장한 최종 소스 산출물로 자연스럽게 전환한다.

## 큰 흐름

```text
설계 readiness 검사
  → 구현 Job 생성
  → 설계 산출물을 job의 design-context로 고정
  → 기본 프로젝트와 공개 계약 생성
  → 공통 persistence 기반 구현
  → 유스케이스 묶음별 backend·API 구현
  → frontend와 wiring 구현
  → 각 작업 범위의 compile·test 검증
  → 최종 HTTP·schema·container 검사
  → 소스/설계 정합성 및 배포 산출물 검사
  → 결과 저장
```

구현 작업이 실패하면 같은 run의 source, 작업 공간과 repair 이력을 재사용해 자동으로
수정과 검증을 계속한다. 기술적인 compile·test·schema·wiring 오류에는 사용자가 다시
버튼을 누르거나 입력할 필요가 없다. 각 시도는 원인·수정 범위·실행 명령·최근 결과만
간결하게 기록한다. 요구사항이나 설계 선택이 꼭 필요하거나 외부 credential·권한이
필요할 때에만 사람의 판단을 기다린다.

OpenHands는 관련 package 또는 디렉터리 안에서 `조사 → 여러 파일 수정 → compile·test
실행 → 결과에 따른 재수정`을 반복한다. 한 요청이 너무 오래 멈추지 않도록 요청 시간과
tool turn에는 안전 한도를 둘 수 있지만, 한 run의 전체 repair 횟수에는 숫자 상한을 두지
않는다. 다음 요청에도 실패 원인과 이미 시도한 방법의 요약만 전달해 같은 작업 공간에서
이어 간다. 여러 영역이 얽힌 오류는 작업 사이를 왕복시키지 않고 관련 범위를 합친 repair
작업으로 확장한다. `auto`는 별도 수리 알고리즘이 아니라 외부 전송 승인을 자동 처리하는
선택이다.

## 작업별 소유 계약

구현 작업은 파일 하나가 아니라 기능 경계로 묶는다.

- 공통 기반 작업은 여러 유스케이스가 함께 쓰는 Entity, persistence mapping, Repository와
  schema를 구현한다.
- 유스케이스 묶음 작업은 같은 Control·Entity를 사용하는 Control, API/Boundary adapter와
  관련 테스트를 함께 구현한다.
- frontend 작업은 API client, 화면과 사용자 흐름을 함께 구현한다.
- wiring·실행 작업은 Spring 설정, 외부 연결, 실행 검사와 배포 연결을 마무리한다.

생성된 BCE·Java·OpenAPI 공개 계약은 읽기 전용이다. 작업자는 관련 package·디렉터리에서
필요한 여러 파일을 수정할 수 있지만 설계에 없는 공개 signature를 임의로 추가하거나 바꾸지
않는다. 생성된 계약의 변경이 필요하면 구현 repair가 아니라 설계 입력을 다시 만든다.

## typed sequence와 runtime 입력

구현 작업의 기준 입력은 표시용 PlantUML이 아니라 `sequenceModel`이다. 작업 context의
`sequence[]`는 유스케이스(`use_case_id`), `Participants`, `Messages`를 묶어
전달하며, 각 message의 `arguments`, `call_id`, `reply_to`, `fragments`를 그대로 보존한다.
따라서 Control·Boundary·API·Frontend가 같은 호출 인자와 호출/반환 연결을 읽는다. PlantUML은
사람이 다이어그램을 보는 용도로만 사용한다.

배포 실행 정보는 필요한 작업에만 `deployment`로 투영한다. 투영에는 `workloads[].id`,
`interfaces`, `configuration`, `storage`와 연결된 `connections`만 들어가며, 전체 CSP 계획이나
원본 bundle을 작업자에게 넘기지 않는다. 작업이 실패하면 해당 작업의 보고서와 마지막 checkpoint부터
재개하고, 새로운 workload·interface·mount가 필요하다는 runtime 관찰은 추측으로 고치지 않고
배포 설계 재생성 대상으로 남긴다.

## runtime 관찰에서 IaC까지

1. 생성된 Dockerfile·설정·소스에서 실제 port, health path, 환경변수와 mount를 관찰한다.
2. 관찰값을 기존 workload의 binding에만 붙인다. 새 workload·port·mount가 보이면 먼저
   배포 설계를 다시 만든다.
3. 구조 digest가 같을 때만 ResourcePlan을 다시 투영하고, 그 결과로 IaC와 health 검사를
   실행한다. 실패하면 해당 관찰 보고서와 마지막 checkpoint부터 재개한다.

## 디렉터리 지도

| 디렉터리 | 책임 |
|---|---|
| `application/` | job 생성·상태 전이, prototype subprocess와 feedback 접수 |
| `domain/` | 구현 단계가 공유하는 모델과 소스 분석용 중간 표현(IR) |
| `generation/` | typed BCE Java 초기 코드, OpenAPI·프론트엔드 생성 |
| `planning/` | 설계 context를 공통 기반·유스케이스 묶음·frontend/wiring 작업과 계약으로 변환 |
| `agents/` | 관련 package를 함께 편집하는 LLM agent와 build·E2E·release 검사 |
| `workflows/` | 작업 조정, repair, 요구사항 추적, 완료와 설계 일치 검사 순서 |
| `delivery/` | Docker·Terraform 배포 파일 생성과 검사 |
| `runtime/` | subprocess, Docker 경로와 Linux runner 실행 경계 |
| `interfaces/` | 구현 산출물 조회 HTTP와 제품 worker용 최소 CLI |
| `tools/` | Gradle wrapper처럼 버전을 고정해야 하는 실행 도구 |

## 식별자와 디렉터리

- `app_id`: 사용자가 만든 애플리케이션.
- `job_id`: 한 번의 구현 실행.
- `run_id`: job 안에서 생성된 prototype 실행본.
- `task_id`: 한 구현 작업 또는 repair 작업.

기본 작업 위치는 `.easydep/implementation-runs/<job_id>/`다. `design-context/`는 실행 시작
시점의 입력 snapshot이고 `generated/runs/<run_id>/`가 실제 결과다. 실패를 조사할 때 서로
다른 job이나 run의 보고서를 섞지 않는다.

기본 scaffold는 구현 작업이 시작되기 전에는 Gradle compile을 하지 않는다. 아직 비어 있는
method를 곧바로 OpenHands가 구현하므로 이 시점의 compile은 같은 준비 비용만 한 번 더 쓰기
때문이다. 생성기 자체를 확인해야 할 때는 `IMPLEMENTATION_VERIFY_INITIAL_COMPILE=true`로 켤 수
있다. 이 설정은 OpenHands 작업별 compile·test와 마지막 통합 검증에는 영향을 주지 않는다.

수리 대화 뒤의 Gradle 검증은 같은 sandbox의 증분 compile과 build cache를 재사용한다. 변경된
Java source는 Gradle이 자동으로 다시 compile하므로 `--rerun-tasks`로 관계없는 task까지 매번
강제 실행하지 않는다. 모든 기능 작업이 끝나면 전체 backend build와 test를 한 번 실행한다.

## 상태를 읽는 법

| 상태 | 의미 |
|---|---|
| `VALIDATING_INPUT` | 설계 산출물과 구현을 시작할 조건을 확인한다. |
| `GENERATING_SOURCES` | 기본 프로젝트와 변경 금지 계약을 만든다. |
| `PLANNING` | 설계에서 기능 단위 작업 범위를 계산한다. |
| `RUNNING` | 작업자와 검증기를 실행한다. |
| `AWAITING_APPROVAL` | 외부 전송·credential처럼 사람 승인이 필요한 작업이 있다. 기술 오류 repair는 이 상태로 보내지 않는다. |
| `COMPLETED` | 모든 필수 검사를 통과하고 결과 저장을 마쳤다. |
| `FAILED` | 재개 지점과 원인을 상태·보고서에 남기고 중단했다. |

## 계약과 안전 규칙

- 실행 시작 때 고정한 설계 snapshot을 실행 도중 최신 버전으로 교체하지 않는다.
- agent는 할당된 package·디렉터리만 수정한다. shell이나 저장소 전체 탐색 권한을 주지 않는다.
- `TODO`, `FIXME`, `UnsupportedOperationException` 같은 미완성 표식을 성공 산출물에 남기지 않는다.
- compile → test → 설계 정합성 검사 순서를 바꾸지 않는다.
- 외부 도구의 제한을 업무 규칙처럼 취급하지 않는다. EasyDep가 소유한 연결 코드에서 명시적으로
  변환하거나, 구현할 근거가 없는 계약은 설계 feedback으로 돌려보낸다.
- 실패 후에는 성공한 작업을 보존하고 실패한 작업과 영향받는 범위만 다시 실행한다.

## 검증

개발 중에는 변경된 작업 범위의 compile과 test만 실행한다. 모든 작업마다 전체 backend test,
frontend production build와 container 검사를 반복하지 않는다. 마지막 통합 지점에서 다음
필수 결과를 한 번 확인한다.

- backend·frontend build
- 생성된 Java·OpenAPI 공개 계약 보존
- DB schema와 persistence mapping 실행
- 대표 HTTP 유스케이스 흐름
- container health, 환경 변수와 mount

실패한 경우에는 현재 source와 repair 기록을 유지한 채 실패한 작업부터 재개한다.

```powershell
python -X utf8 -m pytest -q tests/test_implementation_worker.py
python -X utf8 -m pytest -q tests/test_implementation_engine.py
python -X utf8 -m pytest -q tests/test_implementation_docker_paths.py
```
