# 구현 단계

`app.implementation`은 고정된 설계 산출물에서 실행 가능한 애플리케이션을 만들고 Testing에
전달한다. 백엔드는 설계가 명시한 UC↔API 연결요소를 작은 행동 슬라이스로 사용한다. 공유 source를
이유로 서로 다른 흐름을 전이적으로 합치지 않으며, 각 슬라이스는 별도의 GLM/OpenHands 대화가
구현한다. 프론트엔드는 생성 API client를 사용하는 하나의 owner 작업으로 유지한다. 별도 감독
LLM은 두지 않는다.

## 실행 흐름

```text
설계 snapshot과 공개 계약 고정
  → 결정론적 backend/frontend scaffold 생성
  → UC↔API 연결요소 기준 backend slice 계획
  → backend slice를 하나씩 GLM/OpenHands로 구현
  → slice별 관련 JUnit 검증과 승격
  → Frontend owner 대화
  → frontend 독립 검증과 승격
  → 완료 감사·공개 계약 검사·backend 전체 test 1회
  → 소스 산출물 저장
  → Testing의 정적·동적 검사
```

각 Backend slice는 자신의 Java production source와 하나의 관련 JUnit 시나리오 파일을 함께
책임진다. Service·Controller·Entity 파일을 여러 흐름이 공유해도 슬라이스를 합치지 않는다. 현재
promotion은 canonical application을 즉시 갱신하므로 slice는 한 runner 안에서 순차 실행한다. 후속
slice가 앞선 slice와 같은 production source를 수정하면 자신의 테스트와 앞선 관련 테스트를 함께
통과해야 한다. 모든 owner 작업 뒤에는 전체 backend test도 한 번 실행해 나머지 회귀를 잡는다.

Frontend owner는 React source, 생성 API client 사용, 테스트, lockfile과 build를 함께 책임진다.
프론트엔드는 특정 backend task 하나가 아니라 backend phase 전체 완료에 의존한다. Integration
verification은 LLM 작업이 아니라 EasyDep이 실행하는 완료 감사, 공개 계약 보존 검사와 Testing
handoff다. 실제 container·Arazzo 검사는 Testing 단계가 담당하므로 구현 단계에서 중복 실행하지
않는다.

## OpenHands 실행 경계

구현 owner의 기본 도구는 범위가 제한된 `file_editor`, `grep`, `run_task_check`, `finish`다.
OpenHands는 task context에 명시된 파일만 읽고 owner source만 수정하며, 정해진 관련 테스트를
`run_task_check`로 통과시켜야 한다. 대화 종료 뒤 EasyDep이 같은 workspace를 독립적으로 다시
검증한 후 허용된 owner source만 정식 run에 승격한다. 표준 terminal은 통제된 비교 실행에서만
명시적으로 선택한다.

표준 terminal은 고정 Linux toolchain runner에서만 활성화한다. 컨테이너에는 다음 경계만 보인다.

- EasyDep의 `app/` source: 읽기 전용
- 현재 `.easydep/implementation-runs/<job_id>/`: coordinator만 읽기·쓰기
- 이름 있는 Gradle/npm/OpenTofu cache volume

저장소 루트의 `.env`, `.git`, 사용자 홈, Docker socket과 다른 구현 job은 전달하지 않는다. LLM
연결 설정을 메모리에 적재한 뒤 terminal subprocess를 시작하기 전에 `API_KEY` 환경변수도 제거한다.
coordinator는 root로 실행하지만 owner terminal shell은 `appuser`로 강등한다. Owner는 폐기 가능한
후보 workspace 전체에서 source, build 결과와 생성 계약 사본을 일반 개발 도구처럼 다룰 수 있다.
job 상태·대화 checkpoint·설계 원본은 후보 밖의 root 전용 영역에 둔다. EasyDep은 독립 검증이 끝난
후 accepted source와 후보의 전체 manifest를 비교하여 generated contract나 다른 owner 영역의 변경을
거부하고, 허용된 추가·수정·삭제만 승격한다. RTM ref와 source path는 조사 힌트일 뿐 실행 중 파일
권한을 넓히거나 좁히지 않는다.

## 대화와 수리

slice마다 안정적인 conversation ID와 persistence directory를 사용한다. 최초 task message는 한
번만 보내며, 같은 prompt digest의 provider 오류나 iteration 중단은 마지막 OpenHands event에서
그대로 재개한다. HTTP 제한과 별도로 streaming·내부 재시도 전체를 하나의 LLM turn 제한으로
감싸므로 응답 조각만 계속 오는 경우에도 작업이 무기한 멈추지 않는다. 이 경우 source 결함으로
분류하거나 자동 수리하지 않고 `INTERRUPTED`로 멈춘다. 사용자가 재개하면 같은 checkpoint에서
이어간다. 설계 의미가 부족할 때만 `NEEDS_INPUT`, 실제 source·검증 결함일 때만 `FAILED`다.

새 검증 증거가 생겨 prompt digest가 바뀔 때만 짧은 repair message를 같은 대화에 추가한다.
실제 실패 기준선에서 유효한 구현은 초기 약 28개 tool action에 만들어졌지만 플랫폼 탐색이
238개 action까지 이어졌고 새 backend 구현은 61번째 action 부근에서 완성되었으므로, 한 owner 실행은
96 iteration으로 제한한다. 한도 도달 뒤에는 같은
checkpoint에서 재개할 수 있으며 OpenHands의 500 iteration 기본값을 그대로 사용하지 않는다. SDK가
typed event로 분류한 무동작 응답이 SDK 기본 monologue 임계치만큼 연속되면 같은 typed `STUCK` 상태로
종료한다.

Testing finding에는 `repair_owner`와 별도로 `implementation_owner`가 기록된다. HTTP/Arazzo가 찾은
애플리케이션 결함은 관련 backend slice가 소유한다. Testing이 이 결함을 찾으면 새 source snapshot job을
만들지 않고 원래 implementation job의 repair plan에 증거를 추가한다. 실제 OpenHands `base_state`가
없으면 새 대화로 조용히 바꾸지 않고 재개를 거부한다. 해당 owner task만 다시 실행하고, 성공했던
다른 owner는 재생성하지 않는다. 수정 뒤 같은 implementation job에 새 artifact version을 저장하고
기존 Arazzo 계획과 Testing 이력을 사용해 영향을 받은 gate부터 이어간다.

Trivy 설정, deployment package와 IaC 검사는 EasyDep이 결정론적으로 생성한 delivery 산출물을
검사한다. 이 실패를 backend source owner에게 보내면 finalize가 파일을 다시 생성해 수정이 사라지므로,
platform 결함 또는 platform/design 경계로 보고한다. 생성된 배포 파일을 LLM이 직접 고치게 하지 않는다.

provider의 구조화된 tool-validation 400 응답은 OpenHands의
`FunctionCallValidationError` 복구 경로로 전달한다. 특정 잘못된 도구 이름을 alias로 만들거나
prompt에 오류 사례를 하드코딩하지 않는다. 직렬화가 필요한 legacy focused-check 도구 정의도 모듈
수준 클래스이므로 owner checkpoint 역직렬화를 방해하지 않는다.

## 상태와 산출물

구현 화면은 실제 실행 구조와 같은 세 구간만 표시한다.

- Backend implementation
- Frontend implementation
- Integration verification

내부 scaffold와 planning checkpoint는 로그에는 남지만 별도 사용자 단계로 노출하지 않는다.
task manifest와 실행 결과에는 owner, conversation ID, checkpoint 위치, event/tool 통계와 검증 증거를
저장한다. 완료된 파일은 backend source, frontend source, test, deployment, IaC snapshot으로 나눠
기존 artifact 저장소에 저장하므로 DB migration은 필요하지 않다.

주요 디렉터리는 다음과 같다.

| 디렉터리 | 책임 |
|---|---|
| `application/` | job 상태, checkpoint 재개, 산출물 저장 |
| `generation/` | scaffold와 owner task 생성 |
| `planning/` | frozen 설계에서 owner context와 RTM hint 투영 |
| `agents/` | OpenHands adapter, workspace, owner별 독립 검증 |
| `workflows/` | backend→frontend→integration 조정과 owner repair routing |
| `runtime/` | 고정 Linux runner, 경로·환경 전달 경계 |
| `delivery/` | container와 IaC 산출물 생성·검사 |

## 개발 검증

```powershell
python -X utf8 -m pytest -q tests/test_implementation_engine.py
python -X utf8 -m pytest -q tests/test_implementation_worker.py
python -X utf8 -m pytest -q tests/test_linux_runner_transport.py
python -X utf8 -m pytest -q tests/test_workspace_service.py
```
