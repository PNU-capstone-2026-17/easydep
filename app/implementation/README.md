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

OpenHands는 관련 package 또는 디렉터리 안에서 `조사 → 여러 파일 수정 → run_task_check
실행 → 결과에 따른 재수정`을 같은 대화에서 반복한다. `run_task_check`는 LLM이 명령을
작성하는 shell이 아니다. EasyDep이 현재 작업에 미리 정한 focused test나 compile만 실행한다.
OpenHands가 실행한 검사가 성공했고 그 뒤 source가 바뀌지 않았다면 EasyDep은 그 결과를
재사용한다. 모든 기능이 끝난 시점의 전체 검사는 별도로 한 번 실행한다.
Testing에서 돌아온 수리는 별도 검사 종류를 가진다. `testing-static`은 Trivy,
`testing-package`는 cloud-init·Compose·script, `testing-iac`는 OpenTofu,
`testing-dynamic-functional`은 보존한 HTTP case를 실행한다. 따라서 Terraform 오류를 고친
작업이 무관한 Gradle 테스트만 통과하고 완료되는 일은 없다. 오류 원문과 파일 경로도 같은
작업에 전달되며, 수정 전후에 같은 검사를 실행한다.
검사 실패 시 JUnit XML과 Gradle HTML 전체를 에이전트에게 열어 주지 않는다. 검증기가 대표
실패와 가장 안쪽 원인을 먼저 추출해 ``run_task_check`` 결과로 돌려주며, 원본 보고서는 사람이
실행 이력을 조사할 때만 사용한다. 이렇게 하면 에이전트가 수십만 자짜리 같은 보고서를 반복해
읽지 않고 곧바로 관련 source를 고칠 수 있다.
`grep`도 현재 작업 공간의 source만 검색하며 `build`, `.gradle`, `node_modules`, `dist`와
다른 작업의 임시 폴더는 보지 못한다. 아직 만들지 않은 필수 출력 파일명을 검색하면 파일이
없다는 사실을 반복해서 확인하는 대신, 그 파일을 생성하라는 짧은 안내를 반환한다.

한 요청이 너무 오래 멈추지 않도록 요청 시간과 tool turn에는 안전 한도를 둘 수 있지만, 한
run의 전체 repair 횟수에는 숫자 상한을 두지 않는다. NIM 연결이 끊기거나 한 대화의 안전
한도에 도달했을 때만 현재 오류와 변경 파일을 짧게 요약해 같은 작업 공간에서 새 대화를
이어 간다. OpenHands SDK의 반복 감지와 대화 요약도 함께 사용해 같은 조회·편집과 오래된 전체
로그가 계속 쌓이지 않게 한다. compiler와 test가 파일을 알려 주면 그 기능 작업으로 돌아가고,
서로 다른 기능의 파일이 실제로 함께 실패한 경우에만 wiring 작업이 그 파일들을 통합해 고친다.
구현을 시작하면 계획된 작업과 수리는 사용자 승인 없이 같은 실행에서 이어진다.

## 작업별 소유 계약

구현 작업은 파일 하나가 아니라 기능 경계로 묶는다.

- Entity, persistence mapping, Repository와 schema 골격은 생성기가 먼저 만든다. 유스케이스나
  operation에 연결되지 않은 보조 Entity만을 위해 별도 LLM 작업을 만들지 않는다.
- 유스케이스 묶음 작업은 같은 Control·Entity를 사용하는 Control, API/Boundary adapter와
  관련 테스트를 함께 구현한다.
- frontend 작업은 API client, 화면과 사용자 흐름을 함께 구현한다.
- Spring 설정은 생성기가 만들고, wiring 작업은 실제 연결 오류가 생겼을 때만 수리한다.

생성된 BCE·Java·OpenAPI 공개 계약은 읽기 전용이다. 한 기능만 사용하는 package에서는
OpenHands가 새 helper 파일도 만들 수 있다. 여러 작업이 같은 package를 공유하면 각 작업에
기록된 파일만 수정해 병렬 실행 충돌을 막는다. Entity에는 기존 메서드를 보존하면서 생성자,
persistence 변환과 내부 helper를 추가할 수 있다. 생성된 계약 자체의 변경이 필요하면 구현
repair가 아니라 설계 입력을 다시 만든다.

EasyDep은 목표, 관련 설계, 편집 범위, 사용할 도구와 완료 검사를 전달한다. source 조사,
기능 코드 수정과 테스트 작성 중 무엇을 먼저 할지는 OpenHands가 현재 코드에 맞게 선택한다.
수리할 때도 단일 기능은 그 기능이 소유한 관련 파일과 전용 package를 계속 사용할 수 있다.
없애는 것은 `main/java` 전체 권한과 오류에 관계없는 다른 기능 수정이다.

## typed 설계와 runtime 입력

구현 작업의 기준 입력은 `bceModel`, `sequenceModel`, `apiModel`, `erdBceModel`과
`deploymentBundle`이다. 표시용 PlantUML과 OpenAPI는 화면·다운로드와 외부 코드 생성기에만
사용하며 구현 계획이 다시 정규식으로 읽지 않는다. 작업 context의
`sequence[]`는 유스케이스(`use_case_id`), `Participants`, `Messages`를 묶어
전달하며, 각 message의 `arguments`, `call_id`, `reply_to`, `fragments`를 그대로 보존한다.
따라서 Control·Boundary·API·Frontend가 같은 호출 인자와 호출/반환 연결을 읽는다.
각 유스케이스 작업에는 ERD의 table·column·relation 지도도 함께 제공한다. 하나의 Entity
operation이 여러 Repository를 조합해야 할 때 에이전트는 이 지도에서 후보를 고르고, 필요한
Java 선언만 조회한다.

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

OpenHands의 `run_task_check`와 마지막 확인은 같은 sandbox의 증분 compile과 build cache를
재사용한다. 변경된 Java source는 Gradle이 자동으로 다시 compile하므로 `--rerun-tasks`로
관계없는 task까지 매번 강제 실행하지 않는다. 실패한 뒤 source가 전혀 바뀌지 않았다면 같은
검사를 다시 실행하지 않고 먼저 코드를 수정하도록 안내한다. 모든 기능 작업이 끝나면 전체
backend test를 한 번 실행한다. OpenHands 안에서 같은 source로 이미 성공한 `run_task_check`는
대화 종료 직후 다시 실행하지 않으며, 배포용 `bootJar`는 마지막 Docker build가 한 번 만든다.

유스케이스 개수로 작업을 억지로 자르지 않는다. 같은 Control, Boundary, Entity, Gateway 또는
Controller를 수정하는 흐름은 한 코딩 작업이 맡는다. 실제 수정 파일과 전용 package가 겹치지
않는 작업만 기본 병렬도 2 안에서 함께 실행한다.

Spring Boot entrypoint, 운영 datasource 환경 변수, test용 H2 설정과 health endpoint는
생성기가 작성한다. 인증·인가 요구가 명시된 앱에만 Spring Security를 추가하고, 인증 근거가
없는 앱에는 기본 비밀번호와 401 응답을 만드는 Security 의존성을 넣지 않는다. Control과
adapter는 각 기능 작업이 Spring component로 완성한다. 모든 기능 작업 뒤 실제 compile·test와
HTTP 검사가 통과하면 별도의 wiring LLM 호출은 없다. Bean 충돌이나 HTTP 연결 오류가 실제로
확인된 경우에만 수리 전용 wiring 작업이 설정 package 안에서 자율적으로 수정한다.
정상 실행에서는 쓰지 않는 전체 Java 계약을 wiring prompt로 미리 만들지 않는다. 실제 오류가
생겼을 때 현재 오류, 관련 파일, 읽기 전용 계약과 최근 실패 방법만 담은 짧은 수리 지시를 만든다.
새 수리 대화는 현재 source에서 작업 전용 검사를 먼저 실행한다. 과거 검사 원문은 JSON 보고서에
보존하되 prompt에는 최근 결과와 대표 오류 한 줄만 넣는다. 따라서 이미 고친 test 이름이나 긴
Gradle 출력에 끌려가지 않으면서도 같은 접근을 반복했는지는 확인할 수 있다.

## 상태를 읽는 법

| 상태 | 의미 |
|---|---|
| `VALIDATING_INPUT` | 설계 산출물과 구현을 시작할 조건을 확인한다. |
| `GENERATING_SOURCES` | 기본 프로젝트와 변경 금지 계약을 만든다. |
| `PLANNING` | 설계에서 기능 단위 작업 범위를 계산한다. |
| `RUNNING` | 작업자와 검증기를 실행한다. |
| `COMPLETED` | 모든 필수 검사를 통과하고 결과 저장을 마쳤다. |
| `FAILED` | 재개 지점과 원인을 상태·보고서에 남기고 중단했다. |

## 계약과 안전 규칙

- 실행 시작 때 고정한 설계 snapshot을 실행 도중 최신 버전으로 교체하지 않는다.
- agent는 맡은 기능의 package·디렉터리 안에서 구현 방법, 새 파일과 테스트를 스스로 정한다.
  다른 기능의 source와 생성된 공개 계약만 수정하지 않는다.
- agent의 `run_task_check`는 현재 작업의 명령만 실행하며 `clean`이나 임의 옵션을 받지 않는다.
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

각 OpenHands 실행의 event journal에는 LLM 메시지와 도구 호출·검사 결과를 남긴다. 현재 source
본문은 실제 작업 공간에 있으므로 수리 prompt마다 전체 파일을 다시 복사하지 않는다.

OpenHands도 요구사항·설계·Testing과 같은 `app.llm_connection` 설정을 사용한다. 이 모듈이 만든
환경변수 묶음 전체를 Docker runner에 전달하며, runner 안에 provider별 변수 목록을 다시 만들지
않는다. `LLM_PROVIDER`가 선택한 제공자와 `BASE_URL`, `MODEL`은 직접 SDK, OpenHands, 하위
프로세스에서 동일하게 사용한다. OpenHands가 사용하는 LiteLLM용 접두사도 이 연결 객체에서만
계산하므로, OpenRouter를 선택했는데 NVIDIA 형식으로 요청하는 식의 어긋남을 막는다. API key는
실행 계획·manifest·로그에 기록하지 않는다.

```powershell
python -X utf8 -m pytest -q tests/test_implementation_worker.py
python -X utf8 -m pytest -q tests/test_implementation_engine.py
python -X utf8 -m pytest -q tests/test_implementation_docker_paths.py
```
