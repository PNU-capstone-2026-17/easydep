# 구현 단계

`app.implementation`은 고정된 설계 산출물에서 실행 가능한 애플리케이션을 만들고 Testing에
전달한다. 구현 LLM은 한 run에서 백엔드 담당자 하나와 프론트엔드 담당자 하나만 사용한다.
유스케이스별 작업자, 선제 wiring 작업자, 별도 감독 LLM은 만들지 않는다.

## 실행 흐름

```text
설계 snapshot과 공개 계약 고정
  → 결정론적 backend/frontend scaffold 생성
  → Backend owner 대화
  → backend 독립 검증과 승격
  → Frontend owner 대화
  → frontend 독립 검증과 승격
  → Integration verification
  → 소스 산출물 저장
  → Testing의 정적·동적 검사
```

Backend owner는 Java production source, 설정, 테스트와 build를 함께 책임진다. Frontend owner는
React source, 생성 API client 사용, 테스트, lockfile과 build를 함께 책임진다. 두 작업은 같은
frozen OpenAPI를 읽지만 초기 실행은 `backend → frontend` 순서로 진행한다. Integration
verification은 LLM 작업이 아니라 EasyDep이 실행하는 완료 감사, 공개 계약 보존 검사와 Testing
handoff다. 전체 runtime·Arazzo 검사는 Testing 단계가 담당하므로 구현 단계에서 중복 실행하지
않는다.

## OpenHands 실행 경계

두 owner는 OpenHands의 표준 `file_editor`, `terminal`, `finish` 도구를 사용한다. 검색은 terminal의
`rg`를 사용하며, owner 경로에는 `run_task_check`나 EasyDep 전용 편집 도구를 노출하지 않는다.
OpenHands가 source 조사, 편집 순서, build와 test 명령을 선택하고, 대화 종료 뒤 EasyDep이 같은
workspace를 독립적으로 다시 검증한 후 허용된 owner source만 정식 run에 승격한다.

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

owner마다 안정적인 conversation ID와 persistence directory를 사용한다. 최초 task message는 한
번만 보내며, 같은 prompt digest의 provider 오류나 iteration 중단은 마지막 OpenHands event에서
그대로 재개한다. 새 검증 증거가 생겨 prompt digest가 바뀔 때만 짧은 repair message를 같은 대화에
추가한다. 실제 실패 기준선에서 유효한 구현은 초기 약 28개 tool action에 만들어졌지만 플랫폼 탐색이
238개 action까지 이어졌고 새 backend 구현은 61번째 action 부근에서 완성되었으므로, 한 owner 실행은
96 iteration으로 제한한다. 한도 도달 뒤에는 같은
checkpoint에서 재개할 수 있으며 OpenHands의 500 iteration 기본값을 그대로 사용하지 않는다. SDK가
typed event로 분류한 무동작 응답이 SDK 기본 monologue 임계치만큼 연속되면 같은 typed `STUCK` 상태로
종료한다.

Testing finding에는 `repair_owner`와 별도로 `implementation_owner`가 기록된다. HTTP/Arazzo가 찾은
애플리케이션 결함은 backend owner가 소유한다. Testing이 이 결함을 찾으면 새 source snapshot job을
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
