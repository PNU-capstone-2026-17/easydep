# OpenHands 에이전트 하네스 신뢰성 개선 계획

- 작성일: 2026-09-09
- 상태: 코드 반영 완료. `@cf/zai-org/glm-5.3-flash`는 v5 canary 3/3을 재시도 없이
  통과했다(`healthy`). 기존 `openai/gpt-oss-120b`의 v4 결과는 3/3 통과(`recovered`)였다. 측정 결과는
  [OpenHands 하네스 구현 검증](openhands-agent-harness-validation.md)에 기록한다.
- 대상: 구현 단계 OpenHands 실행기의 모델 연결, 도구 구성, 작업 공간, 오류 처리
- 후속 계획: [OpenHands 실행 효율 보완 계획](openhands-efficiency-improvement-plan.md)
- endpoint 정책: [OpenHands endpoint 복원력 계획](openhands-endpoint-resilience-plan.md)

## 1. 목적

이 계획은 OpenHands 에이전트가 제공되지 않은 도구를 호출하거나 작업 공간 밖을 탐색하고,
권한 오류 뒤에 같은 행동을 반복하는 문제를 줄이는 것을 목표로 한다. 모델에게 지침을 한 번 더
보내는 방식만 사용하지 않는다. 실행기가 허용된 행동을 명확히 보여 주고, 잘못된 행동은 실행
전에 거부하며, 오류 종류에 따라 재시도 여부를 결정하도록 에이전트 하네스를 보완한다.

여기서 하네스는 모델 호출을 둘러싼 실행 장치를 뜻한다. 모델과 도구 사이의 메시지 형식,
제공할 도구, 작업 경로, 파일 권한, 오류 응답, 반복 중단, 결과 검사를 포함한다. 구현 결과의
품질을 판단하는 독립 검증과 실패 후 재개는 유지한다.

## 2. 기존 효율 계획과의 관계

[기존 계획](openhands-efficiency-improvement-plan.md)은 잘못된 종료 복구, 누적 예산, 저장 대화
호환성, 반복 탐색과 검증 비용을 다룬다. 이 장치들은 실패가 길어질 때 소비를 제한하고 실패한
단계부터 다시 실행하는 데 필요하다. 그러나 잘못된 도구 이름이 생성되거나 작업 경로가
헷갈리는 원인을 실행 전에 제거하지는 않는다.

하네스 개선을 먼저 적용한 뒤 기존 효율 계획을 진행한다. 예산 제한은 잘못된 실행의 피해를
줄이지만, 모델과 도구의 메시지 형식이나 작업 공간 안내가 맞지 않는 문제를 고치지는 못한다.

이번 계획에는 OpenHands 교체, 구현 담당자 수 확대, Testing 검사 완화, 범용 인터넷 접근 추가를
포함하지 않는다. 모델 변경은 하네스 호환성 비교 실험의 한 조건으로만 다룬다.

## 3. 확인한 문제

### 3.1. 확인 수준

현재 코드와 저장된 과거 실행은 서로 다른 시점의 도구 구성을 포함한다. 아래 표는 현재 동작과
과거 사례를 구분한다. 과거 사례의 횟수를 현재 버전의 평균 실패율로 사용하지 않는다.

| 근거 | 확인한 내용 | 확인 수준 |
|---|---|---|
| `requirements-common.txt` | OpenHands SDK와 tools가 1.36.1로 고정되어 있음 | 현재 코드에 있음 |
| `agents/runtime.py` | owner 작업에 `file_editor`, `terminal`, `FinishTool`을 구성함 | 현재 코드에 있음 |
| `agents/runtime.py` | 작업 공간 안내를 OpenHands 기본 시스템 프롬프트가 아니라 작업 메시지에 덧붙임 | 현재 코드에 있음 |
| `run_0b7e5a6306fe` | 두 시도에서 terminal 호출 84회와 92회를 기록함 | 저장 결과에서 확인함 |
| `run_fa1721b147c5` | 과거 도구 구성에서 잘못된 도구 호출, 경로 오류, 재개 시 도구 불일치를 기록함 | 저장 결과에서 확인함 |

관련 파일은 다음과 같다.

- [OpenHands 실행기](../app/implementation/agents/runtime.py)
- [제한된 작업 검사 도구](../app/implementation/agents/task_check.py)
- [작업 공간 준비와 권한](../app/implementation/agents/workspace.py)
- [LLM 연결 설정](../app/llm_connection.py)
- [고정한 패키지 버전](../requirements-common.txt)
- 반복 실행: `.easydep/implementation-runs/b86d418472ee454e85acc5d171c61431/generated/runs/run_0b7e5a6306fe/reports/agent-executions/`
- 도구·경로 오류: `.easydep/implementation-runs/169e8e9b46d743db97d839aedc4ef7ff/generated/runs/run_fa1721b147c5/reports/agent-executions/`

### 3.2. 제공되지 않은 도구 호출

`run_fa1721b147c5`의 저장 이벤트에는 다음 호출이 남아 있다.

- 백엔드 attempt 1: `task_tracker<|channel|>commentary` 7회,
  `repo_browser.exec` 3회
- 백엔드 attempt 3: `terminal<|channel|>commentary` 1회
- 프론트엔드 attempt 1: `exec` 2회,
  `task_tracker<|channel|>commentary` 1회

`repo_browser.exec`와 `exec`는 요청에 없던 이름을 모델이 생성한 사례다. 반면 도구 이름에
`<|channel|>commentary`가 붙은 결과는 다른 원인으로 분류해야 한다. 해당 실행은
`gpt-oss-120b`를 사용했다. gpt-oss의 Harmony 형식에서 `commentary`는 함수 도구 호출에 쓰는
채널이다. 제어 토큰이 도구 이름에 포함되었다면 OpenHands, LiteLLM, OpenAI 호환 endpoint를
거치는 과정에서 메시지 경계가 올바르게 해석되지 않았을 가능성이 있다. 어느 계층이 원인인지는
같은 연결 설정으로 별도 호환성 검사를 실행하기 전에는 확정하지 않는다.

OpenAI는 gpt-oss가 Harmony 형식으로 학습되었으며 올바른 형식 사용이 필요하다고 설명한다.
OpenHands도 도구 이름과 입력 형식을 시스템 메시지에 넣고 Pydantic 모델로 검사한다. 따라서
모델의 일반 코딩 성능만으로 실제 endpoint의 도구 호출 호환성을 판단할 수 없다.

### 3.3. 작업 공간 밖 경로 사용

같은 실행에서 `Path is outside the assigned workspace`가 네 번 기록되었다. 저장 이벤트의
경로에는 긴 run ID의 일부가 바뀐 사례가 있다. EasyDep의 executor 내부에는 상대 경로를
해석하는 코드가 있지만 OpenHands 1.36.1의 `file_editor` action 검증은 executor에 도달하기 전에
절대 경로를 요구한다. 반면 EasyDep의 도구 설명은 상대 경로를 권장해 실제 계약과 충돌했다.
긴 host 경로가 아니라 짧은 `/work/<작업 키>` 절대 경로를 주어야 한다.

설치된 OpenHands 1.36.1의 기본 시스템 프롬프트는 사용자가 경로를 주면 먼저 파일 시스템에서
위치를 찾도록 안내한다. EasyDep의 작업 메시지는 sandbox 밖으로 나가지 말라고 지시한다.
기본 지침과 작업별 지침이 서로 다른 탐색 행동을 요구하므로, 작업 메시지만 더 강조하는 방식은
안정적인 해결책이 아니다.

### 3.4. 권한 오류와 잘못된 성공 신호

`run_0b7e5a6306fe` attempt 2에는 상위 경로를 대상으로 다음 명령을 실행한 기록이 있다.

```sh
grep -r "verifyAdministratorRole" -n .. | wc -l
```

`grep`은 `Permission denied`를 출력했지만 마지막 `wc`가 성공하여 명령 종료 코드는 0이었다.
OpenHands 관찰도 `is_error=false`로 저장되었다. 이 경우 모델은 작업 공간 경계를 어긴 명령에서
오류 문구와 성공 상태를 동시에 받는다. 권한 문제에 고착된 원인을 모델의 판단 실패로만 볼 수
없는 이유다.

OpenHands 프로젝트에도 컨테이너와 host의 UID 구성이 맞지 않아 workspace 접근이 거부된 사례가
보고되었다. EasyDep은 작업 공간 밖 접근처럼 의도된 거부와 작업 공간 안에서 발생한 환경 오류를
서로 다른 상태로 기록해야 한다.

### 3.5. 반복 감지의 범위

OpenHands stuck detector는 같은 action-observation 반복, 같은 action-error 반복, 연속된
독백과 같은 형태를 검사한다. 공식 기본 예에서는 같은 action-observation 네 번,
action-error 세 번부터 감지한다. 이 검사는 무한 반복을 막는 안전장치지만, 소스가 바뀌었는지,
컴파일 오류가 줄었는지, 같은 실패가 그대로 남았는지는 판단하지 않는다. 장시간 명령을 이어서
확인하는 정상 행동이 반복으로 분류된 upstream 사례도 있다.

## 4. 설계 원칙

1. 실제 도구 목록과 도구 설명을 한 곳에서 만든다. 프롬프트에 별도의 가상 도구를 적지 않는다.
2. 모델에게 전달할 작업 경로는 짧고 고정한다. host의 실행 ID와 제어 경로를 노출하지 않는다.
3. 작업 공간 경계와 권한은 운영체제와 executor에서 강제한다. 자연어 지침에 의존하지 않는다.
4. 예상 가능한 오류는 코드와 다음 행동을 함께 반환한다. 같은 자연어 오류를 반복해서 보내지 않는다.
5. 환경 오류와 모델 오류를 나눈다. 작업 공간 안의 쓰기 실패는 LLM 재시도로 해결하지 않는다.
6. 반복 감지는 실행 중단의 근거일 뿐 완료 판단의 근거로 사용하지 않는다.
7. 최종 성공은 수정 불가능한 위치에 저장한 독립 검증 결과로 판단한다.

## 5. 목표 실행 구조

```text
고정 설정과 checkpoint
        |
        v
환경 사전 검사 ------ 실패 ------> ENVIRONMENT_INCOMPATIBLE
        |
        v
모델·도구 canary ---- 실패 ------> MODEL_TOOL_PROTOCOL_INCOMPATIBLE
        |
        v
짧은 /work 경로 + 최소 도구로 OpenHands 실행
        |
        v
구조화된 관찰과 오류별 제한 복구
        |
        v
소스·실패 지문으로 진전 확인
        |
        v
독립 검증 ------ 실패 ------> 후보 보존 후 수리 또는 중단
        |
        v
허용된 파일만 정식 workspace로 승격
```

환경 사전 검사는 LLM을 호출하지 않는다. 모델·도구 canary는 실제 구현과 같은 endpoint를
사용하므로 외부 네트워크가 필요하다. canary 결과는 provider, 모델, OpenHands 버전, 도구
스키마 해시가 모두 같을 때만 재사용한다.

## 6. P0: 실행 전 호환성 검사

### 6.1. 환경 사전 검사

OpenHands 대화를 만들기 전에 다음 항목을 결정론적으로 검사한다.

- coordinator와 owner의 UID·GID
- owner 작업 디렉터리와 실제 경로 대응
- 일반 소스 파일 읽기와 쓰기
- 생성 계약 읽기와 쓰기 거부
- 작업 공간 밖 경로 거부
- Gradle·npm cache 읽기와 쓰기
- canonical 검증 명령 실행 가능 여부
- shell pipeline에서 앞 명령의 실패 전달
- 등록할 도구 executor 생성과 입력 스키마 직렬화

검사용 파일은 owner sandbox 안에 고유한 이름으로 만들고 검사 직후 제거한다. 작업 공간 안의
파일 쓰기가 실패하면 `ENV_WORKSPACE_PERMISSION`으로 중단한다. 작업 공간 밖 접근이 거부되는
결과는 정상으로 기록한다.

터미널을 유지하는 동안 shell에는 `pipefail`을 적용한다. 지원하지 않는 shell을 사용할 수
있으므로 단순 환경 변수 설정으로 끝내지 않고, 실제 실패 pipeline의 종료 코드를 사전 검사에서
확인한다.

### 6.2. 모델·도구 canary

실제 작업 전 작은 대화를 실행하여 다음 순서를 확인한다.

1. 제공된 읽기 도구를 정확한 이름과 입력 형식으로 한 번 호출한다.
2. 도구 결과를 받은 뒤 허용된 검사 도구를 호출한다.
3. 성공 결과 뒤 `FinishTool`을 호출한다.
4. 모든 assistant 응답에서 제어 토큰 누출, 잘못된 tool name, schema 오류를 검사한다.

canary는 구현 코드를 수정하지 않는 전용 workspace에서 수행한다. 다음 조건은 일반 provider
재시도 대상에서 제외한다.

- 도구 이름이나 인수에 Harmony 제어 토큰이 포함됨
- 요청에 없는 도구를 교정 뒤 다시 호출함
- 같은 schema validation 오류가 두 번 발생함
- tool 결과를 다음 요청에서 정상적으로 연결하지 못함

결과 manifest에는 provider, endpoint 식별자, 모델, reasoning 설정, OpenHands SDK·tools 버전,
도구 이름 목록, 도구 스키마 해시, canary 시점과 결과를 저장한다. API key와 전체 응답의 숨겨진
reasoning은 저장하지 않는다.

### 6.3. 저장 대화 호환성

OpenHands 공식 복원 계약은 Agent 종류와 도구 이름의 정확한 일치를 요구한다. 도구는 시스템
프롬프트의 일부이므로 대화 중간에 제거하거나 추가할 수 없다. 기존 효율 계획의 checkpoint
검사를 확장하여 canary 결과와 도구 스키마 해시도 비교한다.

도구가 달라진 대화는 LLM 호출 전에 중단한다. 새로운 도구 구성으로 계속해야 한다면 기존 후보
소스와 마지막 검증 증거를 넘긴 새 대화를 명시적으로 만든다. 이전 대화를 새 대화로 조용히
바꾸지 않는다.

## 7. P0: 작업 공간과 도구 인터페이스

### 7.1. 짧은 작업 경로

owner에게 보이는 작업 공간을 `/work`와 같이 짧은 고정 경로로 통일한다. host의 실제 경로는
runner가 bind mount 또는 동등한 격리 방식으로 연결한다. `file_editor`에는 action schema가
요구하는 `/work/<작업 키>/...` 절대 경로를 사용하고, terminal과 grep은 현재 작업 공간 안의
경로만 사용한다.

다음 조건을 검사한다.

- `/work/application`이 실제 후보 application과 대응함
- symlink와 `..`를 해석한 최종 경로가 `/work` 안에 있음
- Windows host 경로와 Linux runner 경로를 한 메시지에 함께 노출하지 않음
- checkpoint 재개 뒤에도 같은 대화가 같은 논리 경로를 사용함

### 7.2. 최소 도구 구성

초기 비교 대상은 다음 두 구성으로 제한한다.

| 구성 | 도구 | 목적 |
|---|---|---|
| 기준선 | `file_editor`, `terminal`, `FinishTool` | 현재 owner 구성 유지 |
| 제한 구성 | 파일 읽기·검색·편집, `run_task_check`, `FinishTool` | 범용 shell 사용과 임의 검증 명령 축소 |

제한 구성의 도구 이름과 기능이 겹치지 않게 한다. 예를 들어 `grep`, `search_code`, terminal 안의
`rg`를 동시에 권장하지 않는다. 파일 편집 도구 설명은 설치된 OpenHands action schema와 같은
절대 경로 계약을 말하고, executor도 해석 뒤의 실제 경계가 작업 공간 안인지 검사한다.

범용 터미널이 필요한 작업을 먼저 수집한 뒤 허용 기능을 결정한다. package 설치나 서버 실행이
필요하다는 가정만으로 모든 owner에게 shell을 제공하지 않는다. 반대로 실제 구현에 필요한
명령이 제한 도구에 없으면 조용히 막지 않고 `UNSUPPORTED_REQUIRED_ACTION`으로 기록한다.

### 7.3. EasyDep 전용 시스템 프롬프트

OpenHands는 `AgentContext`의 suffix와 `system_prompt_filename`을 사용한 전체 시스템 프롬프트
교체를 지원한다. EasyDep은 구현 owner용 작은 시스템 프롬프트를 별도로 둔다. 다음 내용만
포함한다.

- 작업 공간은 `/work/<작업 키>`이며 `file_editor`는 이 root 아래의 절대 경로를 사용함
- 현재 요청에 실제로 포함된 도구와 각 도구의 한 가지 용도
- 생성 계약은 읽을 수 있지만 수정할 수 없음
- 검사 실패 뒤에는 오류에 나온 파일만 우선 확인함
- 검사 성공 뒤에는 바로 `FinishTool`을 호출함
- 상위 디렉터리 탐색, `sudo`, `chmod`, test 삭제를 시도하지 않음

범용 OpenHands 지침의 Git, PR, 외부 서비스, 저장소 루트 탐색, 패키지 자동 설치 부분은 owner
프롬프트에서 제외한다. 시스템 프롬프트 변경은 저장 대화와 호환되지 않을 수 있으므로 prompt
버전을 manifest에 기록한다.

## 8. P0: 오류 분류와 제한 복구

도구 관찰에 사람이 읽을 설명과 기계가 읽을 필드를 함께 넣는다.

```json
{
  "errorCode": "PATH_OUTSIDE_WORKSPACE",
  "retryable": true,
  "workspace": "/work",
  "nextAction": "Use an absolute path rooted at the logical workspace."
}
```

초기 오류 분류와 처리 규칙은 다음과 같다.

| 오류 코드 | 의미 | 처리 |
|---|---|---|
| `TOOL_NOT_AVAILABLE` | 요청에 없는 도구 이름 | 실제 도구 목록으로 한 번 교정, 반복 시 중단 |
| `TOOL_PROTOCOL_TOKEN_LEAK` | 도구 호출에 제어 토큰 포함 | 즉시 호환성 실패 |
| `TOOL_SCHEMA_INVALID` | 도구 인수 형식 오류 | 틀린 필드와 허용값만 반환, 한 번 재시도 |
| `PATH_OUTSIDE_WORKSPACE` | 최종 경로가 `/work` 밖임 | 논리 root와 절대 경로 예시 반환, 반복 시 중단 |
| `WRITE_OUTSIDE_OWNER_SCOPE` | workspace 안이지만 담당 범위 밖임 | 수정 가능한 root 반환, 자동 권한 확대 금지 |
| `ENV_WORKSPACE_PERMISSION` | 허용 경로를 읽거나 쓸 수 없음 | LLM 재시도 없이 환경 실패 |
| `COMMAND_FAILED` | 실행한 검사나 명령이 실패함 | 종료 코드와 대표 원인 반환 |
| `NO_PROGRESS_REPEAT` | 소스와 실패 지문이 같은 상태로 반복됨 | 전략 변경 한 번 또는 예산 보존 후 중단 |

오류 응답은 전체 tool arguments나 긴 stack trace를 반복하지 않는다. 정확한 원문은 owner가
수정할 수 없는 실행 기록에 저장한다. 모델에는 현재 파일, 대표 오류, 원문 위치, 다음에 가능한
행동을 보낸다.

## 9. P1: 진전 판단과 실행 예산 연결

하네스가 다음 상태를 매 도구 호출 뒤 계산한다.

- 후보 source 해시
- 수정된 구현 파일
- 미구현 marker 수
- 마지막 검증 명령과 입력 해시
- 컴파일·test 실패 지문
- 같은 파일·같은 구간 조회 횟수
- 도구·경로·권한 오류 횟수

같은 source와 같은 실패 지문에서 조회만 반복되면 진전이 없는 상태로 기록한다. 파일 수가
늘거나 test가 삭제된 결과는 진전으로 보지 않는다. source가 바뀌었더라도 새로운 실패가 환경
오류이면 구현 수리 횟수를 늘리지 않는다.

누적 시간, 토큰, 수리 횟수와 checkpoint 저장은 기존 효율 계획의 예산 정책을 사용한다.
하네스 오류 때문에 중단된 사용량도 성공당 사용량 계산에서 제외하지 않는다.

## 10. 구현 순서

| 순서 | 작업 | 주요 변경 대상 | 완료 기준 |
|---|---|---|---|
| 1 | 실행 기록 분류기와 회귀 fixture | `agents/runtime.py`, tests | 저장 사례의 도구·경로·권한 오류를 재현 |
| 2 | 환경 사전 검사 | `agents/workspace.py`, runner transport | LLM 호출 전에 내부 권한 오류와 pipeline 오류 탐지 |
| 3 | 모델·도구 canary | `agents/provider.py`, `agents/runtime.py` | 제어 토큰 누출과 도구 왕복 실패를 본 작업 전에 탐지 |
| 4 | `/work` 논리 경로 | runner, workspace, tool executors | 긴 host 경로가 모델 입력과 관찰에 나타나지 않음 |
| 5 | 최소 도구와 전용 프롬프트 | `agents/runtime.py`, prompt 파일 | 실제 도구와 설명이 일치하고 범용 탐색 지침 제거 |
| 6 | 구조화된 오류와 제한 복구 | tool executors, runtime | 오류별 재시도 상한과 중단 사유 저장 |
| 7 | 진전 판단과 누적 예산 연결 | runtime, repair, coordinator | 같은 상태의 반복 실행이 누적 예산 안에서 종료 |
| 8 | 동일 조건 비교 | 평가 실행기, 보고서 | Testing 품질과 오류율·사용량을 함께 비교 |

첫 단계에서는 과거 JSONL 전체를 테스트 저장소로 복사하지 않는다. 문제를 재현하는 최소 이벤트만
비밀값과 긴 reasoning을 제거한 fixture로 만든다. fixture에는 원본 실행 ID와 event sequence를
남겨 대응 관계를 확인할 수 있게 한다.

## 11. 검증 계획

### 11.1. LLM 없는 회귀 검사

- 등록 도구 외 이름을 `TOOL_NOT_AVAILABLE`로 분류함
- `<|channel|>`이 포함된 이름을 protocol 오류로 분류함
- `file_editor` 절대 경로가 논리 `/work/<작업 키>` 안에서 올바르게 해석됨
- `..`와 symlink를 통한 외부 접근이 거부됨
- 허용 경로의 permission 오류는 환경 실패가 됨
- 실패한 앞 명령을 포함한 pipeline이 종료 코드 0으로 저장되지 않음
- 같은 오류의 복구 횟수 상한을 넘기지 않음
- 도구 집합이 다른 checkpoint를 LLM 호출 전에 거부함

### 11.2. 실제 모델 호환성 검사

외부 네트워크를 사용하는 canary는 첫 실행부터 필요한 권한으로 실행한다. 기본 sandbox의 네트워크
차단, HTTP 429, provider timeout, stream 미종료, tool protocol 오류를 별도 원인으로 기록한다.

모델별로 정상 왕복을 최소 세 번 확보하되 표본이 작으므로 개별 결과와 범위를 함께 보고한다.
일시적 endpoint 실패의 추가 sequence, TTL circuit과 요청 retry는 endpoint 복원력 계획을
따른다. 결정적 protocol 오류가 있거나 제한 안에 정상 왕복을 확보하지 못한 모델은 전체
애플리케이션 생성 실험에 넣지 않는다. 같은 모델 이름이라도 endpoint와 adapter가 다르면 별도
조건으로 취급한다.

### 11.3. 전체 구현 비교

기준선과 보완 코드를 같은 설계 snapshot, source revision, 모델, provider, reasoning 설정,
cache 조건으로 실행한다. 다음 지표를 추가한다.

| 지표 | 계산 방법 |
|---|---|
| 잘못된 도구 호출률 | 요청에 없는 도구 호출 수 / 전체 모델 응답 수 |
| schema 오류율 | tool validation 실패 수 / 전체 도구 호출 수 |
| protocol 누출 | 제어 토큰이 tool name·arguments에 나타난 횟수 |
| 작업 공간 위반 | 외부 경로 또는 담당 범위 밖 접근 횟수 |
| 잘못된 성공 상태 | stderr에 권한·실행 오류가 있으나 성공으로 기록된 명령 수 |
| 오류 회복 비용 | 첫 오류부터 유효한 source 변경 또는 중단까지 호출·토큰·시간 |
| 첫 유효 수정 시간 | 대화 시작부터 검증 대상 source가 처음 바뀔 때까지의 능동 시간 |
| 최종 성공률 | 기존 필수 Testing 검사를 모두 통과한 작업 비율 |

도구 오류가 줄어도 최종 Testing 통과율이 낮아지면 기본 적용하지 않는다. 반대로 성공률이 같아도
작은 표본만으로 품질이 같다고 결론 내리지 않는다.

## 12. 적용과 되돌리기

환경 사전 검사와 오류 계측을 먼저 관찰 모드로 넣는다. 명백한 내부 권한 오류와 protocol token
누출은 곧바로 실행 차단 대상으로 삼을 수 있지만, 반복 탐색 중단 기준은 저장 결과와 대조한 뒤
활성화한다.

새 하네스에는 `harnessPolicyVersion`, `promptVersion`, `workspacePathVersion`,
`toolSchemaHash`, `canaryResultId`를 기록한다. 문제가 생기면 checkpoint와 후보 source를
삭제하지 않고 정책 버전만 이전 값으로 되돌린다. 도구와 프롬프트가 바뀐 뒤 이전 대화를 다시
열 때에는 호환성 검사를 생략하지 않는다.

## 13. 완료 기준

다음 조건을 모두 충족해야 하네스 개선을 완료로 표시한다.

- 환경 권한과 도구 protocol 문제를 실제 구현 전 검사한다.
- 모델에게 보이는 작업 경로가 짧고 실행마다 같은 논리 구조를 가진다.
- 도구 설명, 시스템 프롬프트, 실제 executor가 같은 경로와 기능 규칙을 사용한다.
- 없는 도구, schema 오류, 경로 위반, 내부 권한 오류의 중단 사유가 서로 구분된다.
- 같은 하네스 오류가 제한 없이 LLM에 재전송되지 않는다.
- 도구가 달라진 checkpoint를 LLM 호출 전에 판별한다.
- 독립 검증과 승격 범위를 약화하지 않는다.
- 같은 조건의 비교 결과에 최종 성공률, 오류율, 시간과 사용량이 함께 기록된다.

## 14. 외부 근거

- [OpenHands Tool System](https://docs.openhands.dev/sdk/arch/tool-system): 도구 schema 생성,
  입력 검사와 action-observation 구조
- [OpenHands Agent API](https://docs.openhands.dev/sdk/api-reference/openhands.sdk.agent): 저장 대화
  복원 시 Agent 종류와 도구 이름 일치 조건
- [OpenHands Persistence](https://docs.openhands.dev/sdk/guides/convo-persistence): 대화 상태, 도구
  설정, 작업 공간 문맥의 저장 범위
- [OpenHands Stuck Detector](https://docs.openhands.dev/sdk/guides/agent-stuck-detector): 반복 행동과
  오류의 기본 감지 방식
- [OpenHands Agent Skills & Context](https://docs.openhands.dev/sdk/guides/skill):
  `AgentContext`와 전체 시스템 프롬프트 교체 방법
- [OpenAI Harmony format](https://github.com/openai/harmony/blob/main/docs/format.md): gpt-oss의
  역할, 채널과 함수 호출 형식
- [SWE-agent 논문](https://papers.neurips.cc/paper_files/paper/2024/file/5a7c947568c1b1328ccc5230172e1e7c-Paper-Conference.pdf):
  코딩 에이전트용 인터페이스와 간결한 환경 피드백의 설계 근거
- [OpenHands workspace 권한 이슈](https://github.com/OpenHands/OpenHands/issues/3921): UID 불일치로
  workspace 접근이 거부된 사례
- [OpenHands 장시간 명령 stuck 이슈](https://github.com/OpenHands/OpenHands/issues/10350): 정상적인
  명령 대기가 반복 감지에 포함된 사례
