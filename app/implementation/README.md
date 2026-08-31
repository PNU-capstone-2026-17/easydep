# 구현 단계

`app.implementation`은 설계 산출물을 소스 코드, 테스트, 컨테이너 파일과 IaC로 바꾸고 필수
검사를 통과시킨다. IaC는 Terraform처럼 클라우드 자원을 코드로 정의한 파일을 뜻한다.
“코드를 한 번 생성하는 기능”이 아니라 작업 계획, 제한된 LLM 편집, 컴파일과 테스트,
실패 repair, 배포 파일 검사까지 포함하는 실행 단계다.

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
  → 구현 작업 계획
  → 외부 LLM에 보낼 범위를 한 번 확인하고 위임
  → 허용된 파일만 볼 수 있는 작업공간에서 LLM agent 실행
  → 컴파일·단위/E2E 테스트
  → 실패 원인을 담당하는 작업만 골라 자동 repair
  → 소스/설계 정합성 및 배포 산출물 검사
  → 결과 저장
```

사용자가 처음 실행을 위임하면 같은 run 안의 repair는 다시 버튼을 누르지
않아도 이어진다. 각 시도는 원인·담당 작업·재검사 범위와 함께 repair 이력에
남는다. 같은 코드와 실패가 반복되어도 이전 시도를 다음 대화에 함께 전달해 다른
수정을 계속 시도한다. 요구사항이나 설계 선택이 꼭 필요하거나 외부 서비스가
응답하지 않을 때에만 사람의 판단을 기다린다.

OpenHands는 맡은 파일 안에서 `수정 → 컴파일·담당 테스트 → 같은 대화에서 재수정`을
반복한다. 한 대화가 너무 오래 멈춰 있지 않도록 도구 사용 횟수에는 안전 한도가 있지만,
전체 수리 횟수에는 숫자 상한을 두지 않는다. 대화를 새로 시작해야 할 때에도 이전 실패와
시도한 방법을 결과 보고서에서 다시 읽어 이어 간다. 오류가 다른 작업의 파일에서 났다면
현재 작업 결과는 보존하고 EasyDep가 그 파일의 담당 작업을 즉시 다시 실행한다. `auto`는
별도 수리 알고리즘이 아니라 이 위임을 화면에서 자동 승인하는 선택일 뿐이다.

## 작업별 소유 계약

구현 작업은 설계 계약을 지키면서 서로 다른 파일을 나누어 작성한다.

- `scaffold-completion`은 `TODO(EasyDep)` 타입 표식이 남은 생성 파일만 보완한다. 표식이
  없으면 이 작업은 계획하지 않는다.
- `entity`는 BCE Entity마다 하나의 Java 파일만 소유한다. 별도의 기계적 단위 테스트 파일은
  생성하지 않으며, 설계에 선언된 메서드의 본문을 작성한다. 설계에 없는 getter/setter를
  임의로 추가하거나 공개 class·field·method signature를 바꾸지 않는다.
- `control`, API adapter와 생성된 BCE/OpenAPI contract는 각각 자기 허용 경로만 쓴다. 생성된
  Control/API 파일의 바이트를 수정하지 않으며, 계약 변경은 구현 수리가 아니라 설계 입력으로
  되돌린다.
- Entity 작업이 성공한 뒤에야 persistence와 Control 작업이 실행된다. 실패하면 성공한 작업은
  재사용하고 해당 소유 작업부터 재개한다.

## typed sequence와 runtime 입력

구현 작업의 기준 입력은 표시용 PlantUML이 아니라 `sequenceModel`이다. 작업 context의
`sequence[]`는 유스케이스(`use_case_id`), `Participants`, `Messages`를 묶어
전달하며, 각 message의 `arguments`, `call_id`, `reply_to`, `fragments`를 그대로 보존한다.
따라서 Control·Boundary·API·Frontend가 같은 호출 인자와 호출/반환 연결을 읽는다. PlantUML은
사람이 다이어그램을 보는 용도로만 사용한다.

배포 실행 정보는 필요한 작업에만 `deployment`로 투영한다. 투영에는 `workloads[].id`,
`interfaces`, `configuration`, `storage`와 연결된 `connections`만 들어가며, 전체 CSP 계획이나
원본 bundle을 작업자에게 넘기지 않는다. 작업이 실패하면 해당 task의 보고서와 owner부터
재개하고, 새로운 workload·interface·mount가 필요하다는 runtime 관찰은 추측으로 고치지 않고
배포 설계 재생성 대상으로 남긴다.

## runtime 관찰에서 IaC까지

1. 생성된 Dockerfile·설정·소스에서 실제 port, health path, 환경변수와 mount를 관찰한다.
2. 관찰값을 기존 workload의 binding에만 붙인다. 새 workload·port·mount가 보이면 먼저
   배포 설계를 다시 만든다.
3. 구조 digest가 같을 때만 ResourcePlan을 다시 투영하고, 그 결과로 IaC와 health 검사를
   실행한다. 실패하면 해당 관찰 보고서와 owner task부터 재개한다.

## 디렉터리 지도

| 디렉터리 | 책임 |
|---|---|
| `application/` | job 생성·상태 전이, prototype subprocess와 feedback 접수 |
| `domain/` | 구현 단계가 공유하는 모델과 소스 분석용 중간 표현(IR) |
| `generation/` | typed BCE Java 초기 코드, OpenAPI·프론트엔드 생성 |
| `planning/` | 설계 context를 작은 구현 task와 프런트엔드 계약으로 변환 |
| `agents/` | 허용된 파일만 편집하는 LLM agent와 build·E2E·release 검사 |
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
