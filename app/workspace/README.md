# 대화형 워크스페이스

`app.workspace`는 브라우저의 한 번의 클릭이나 메시지를 백엔드 단계 실행으로 연결한다.
요구사항·설계 알고리즘을 직접 구현하지 않고, 명령의 수명주기와 사용자에게 보여 줄 사건을
관리한다.

## 핵심 개념

- **command:** 사용자가 보낸 한 번의 요청. `PENDING → RUNNING → COMPLETED/FAILED` 또는
  `AWAITING_INPUT` 상태를 가진다.
- **event:** 타임라인에 추가되는 읽기 전용 기록. 진행률, 질문, 결과와 오류를 표시한다.
- **artifact:** 단계가 만든 구조화된 결과나 파일.
- **live preview:** 클래스 생성처럼 오래 걸리는 작업의 중간 결과. 완료 artifact와 구분한다.

## 파일 지도

| 파일 | 역할 |
|---|---|
| `api.py` | 앱·명령·이벤트·preview·SSE(Server-Sent Events) endpoint와 요청 schema |
| `service.py` | 명령 실행, 단계 전환, 복구, 진행 event와 결과 요약 |
| `repository.py` | workspace command/event의 MySQL 읽기·쓰기 |
| `live_preview.py` | process-local 중간 다이어그램과 SVG cache |

## 명령 흐름

```text
POST /api/workspace/apps/{app_id}/commands
  → 요청 action과 현재 stage 검증
  → workspace_commands에 PENDING 저장
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
이미 허용된 선택을 클릭하는 기능이다. 질문에 답을 발명하거나 실패를 성공으로 바꾸지 않는다.
LLM에게 repair를 맡기는 선택지가 있을 때만 `delegate_repair`를 보낼 수 있다.

## 계약

- **입력:** `app_id`, action, 메시지·선택·승인 payload.
- **출력:** command snapshot, 기존 내용을 수정하지 않고 뒤에만 추가하는 event, UI에 표시할 진행·오류 정보.
- **실행하면서 바꾸는 것:** 데이터베이스 쓰기, background worker 실행, 단계 API 호출, SSE 알림.
- **이 패키지에서 직접 사용하지 않는 것:** 단계의 private helper와 단계 내부 state 모델.
- **주요 실패 원인:** 현재 stage에서 허용되지 않은 action, 오래된 `action_id`, 중복 실행, 단계 실행 오류.

## 초보자용 디버깅 순서

1. 최신 command의 `action`, `stage`, `status`를 본다.
2. `command_id`가 같은 event만 시간순으로 읽는다.
3. `AWAITING_INPUT`이면 result에 실제 선택지가 있는지 확인한다.
4. 구현 command라면 result의 `job_id`로 구현 상태 파일을 찾는다.
5. UI 오류 문구가 잘렸다면 저장된 command error보다 단계별 report의 원문을 우선한다.
