# 대화형 워크스페이스

`app.workspace`는 브라우저의 한 번의 클릭이나 메시지를 백엔드 단계 실행으로 연결한다.
요구사항·설계 알고리즘을 직접 구현하지 않고, 명령의 수명주기와 사용자에게 보여 줄 사건을
관리한다.

## 핵심 개념

- **command:** 사용자가 보낸 한 번의 요청. `QUEUED → RUNNING → COMPLETED/FAILED` 또는
  `AWAITING_INPUT` 상태를 가진다.
- **event:** 타임라인에 추가되는 읽기 전용 기록. 진행률, 질문, 결과와 오류를 표시한다.
- **artifact:** 단계가 만든 구조화된 결과나 파일.
- **live preview:** 클래스 생성처럼 오래 걸리는 작업의 중간 결과. 완료 artifact와 구분한다.

## 파일 지도

| 파일 | 역할 |
|---|---|
| `api.py` | 앱·명령·이벤트·preview·SSE(Server-Sent Events) endpoint와 요청 schema |
| `contracts.py` | 공통 대기 이유와 action offer 계약 |
| `actions.py` | action 이름·payload·단계 정책과 다음 action registry |
| `service.py` | 명령 실행, 단계 전환, 복구, 진행 event와 결과 요약 |
| `repository.py` | workspace command/event의 MySQL 읽기·쓰기 |
| `checkpoints.py` | 완료된 단계까지의 최신 산출물을 새 앱으로 복사 |
| `live_preview.py` | process-local 중간 다이어그램과 SVG cache |
| `conversation/` | 자연어 의도, 영속 대화 문맥과 읽기 전용 프로젝트 도구 |

## 명령 흐름

```text
POST /api/workspace/apps/{app_id}/commands
  → 공개 action과 payload scope 검증
  → workspace_commands에 QUEUED 저장
  → worker가 RUNNING으로 선점
  → 요구사항·설계·구현·테스팅 단계의 공개된 진입 함수 호출
  → 진행 event를 append
  → 결과를 저장하고 최종 상태 갱신
  → SSE 연결을 열어 둔 브라우저가 새 event를 표시
```

서버가 재시작되면 실행 중이던 command를 무조건 성공이나 실패로 바꾸지 않는다. 저장된
상태와 단계별 checkpoint를 확인해 이어서 실행할 수 있는 것은 다시 실행하고, 외부 process 상태를
확인할 수 없는 경우에는 명시적인 중단 원인을 남긴다.

## 자동 모드

자동 모드는 별도의 파이프라인이 아니다. UI가 현재 command 결과에 노출된 다음 선택지 중
`auto_selectable=true`인 첫 action을 payload 변경 없이 클릭하는 기능이다. 질문에 답을
발명하거나 단계·상태에서 action을 추측하지 않는다.

사용자가 한 번 `delegate_repair`를 선택하면 같은 수리 episode 안의 기계적인 finding은
백엔드가 누적 repair history를 사용해 계속 처리한다. 첫 위임, 배포 대상 선택, 요구사항의 뜻,
외부 환경 복구처럼 실제 결정이 필요한 경우에는 `AWAITING_INPUT`으로 남는다.

## 실패한 단계 다시 실행하기

요구사항 command가 `FAILED` 또는 `INTERRUPTED`로 끝나면 화면은
`retry_requirements` 버튼을 보여 준다. 이 동작은 새 요구사항 분석을 만들지 않고 같은
`app_id`의 MySQL checkpoint를 읽어 실패한 node부터 다시 실행한다. checkpoint가 없으면
빈 입력으로 새 실행을 시작하지 않고 오류로 멈춘다. 설계 단계의 `retry_design`도 같은 원칙으로
저장된 설계 checkpoint를 사용한다. UI와 자동 모드는 이 retry가 결과의 공개 action에 있을 때만
실행할 수 있다.

## 대화와 프로젝트 질문

최초 요구사항, 버튼 action과 저장된 질문의 답은 기존 전문 단계로 바로 전달한다. 그 밖의
자연어는 `Reply`, `Clarification`, `CommandIntent`로 분류한다. 일반 대화와 프로젝트 질문은
전문 단계나 artifact version을 바꾸지 않는다. 프로젝트 질문은 최신 Workspace 상태, 산출물과
RTM을 읽는 도구 결과로 답한다.

수정 명령에서 LLM은 유한한 element ref 후보만 고른다. owner, 편집 가능 여부, 현재 app과
artifact version, 하류 영향은 코드가 검증한다. 검증된 명령은 공개 action registry와 같은
실행 경로로 들어가므로 자연어 명령을 위한 별도 stage router는 없다.

## 분기와 단계 재실행

분기는 요구사항·설계·구현 중 선택한 단계까지의 최신 산출물을 새 `app_id`로 복사한다.
재실행은 같은 복사를 사용해 선택한 단계의 직전까지만 보관한 뒤 기존 단계 시작 action을
호출한다. 따라서 별도 파이프라인은 없으며 요구사항·설계·구현·테스팅의 실제 사용 경로를
그대로 탄다. 원본 앱, 과거 명령과 실행 중이던 내부 checkpoint는 바꾸거나 복사하지 않는다.

## LLM 사용량 관찰

요구사항 단계는 각 LLM 호출의 완료 event에 시간과 token 사용량을 넣는다. 설계 단계는 기존
설계 timing event를 `designLlmMetrics` progress event로 전달한다. 여기에는 호출 종류,
logical/physical 요청 구분, token, schema·의미 repair와 cache 결과가 들어가지만 prompt와
응답 원문은 들어가지 않는다. 평가 도구는 이 공개 event만 읽으므로 내부 단계 함수를 따로
호출하지 않는다.

## 계약

- **입력:** `app_id`, action, 메시지·선택 payload.
- **출력:** command snapshot, `wait_reason`, 실행 가능한 `actions`, 뒤에만 추가하는 진행 event.
- **실행하면서 바꾸는 것:** 데이터베이스 쓰기, background worker 실행, 단계 API 호출, SSE 알림.
- **이 패키지에서 직접 사용하지 않는 것:** 단계의 private helper와 단계 내부 state 모델.
- **주요 실패 원인:** 현재 stage에서 허용되지 않은 action, 오래된 `action_id`, 중복 실행, 단계 실행 오류.

## 초보자용 디버깅 순서

1. 최신 command의 `action`, `stage`, `status`를 본다.
2. `command_id`가 같은 event만 시간순으로 읽는다.
3. `AWAITING_INPUT`이면 `wait_reason`과 `actions[*].payload`를 확인한다.
4. 구현 command라면 result의 `job_id`로 구현 상태 파일을 찾는다.
5. Testing command라면 `payload.testing_checkpoint`의 고정 입력과 현재 node를 확인한다.
6. UI 오류 문구가 잘렸다면 저장된 command error보다 단계별 report의 원문을 우선한다.
