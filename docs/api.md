# HTTP API 길잡이

이 문서는 초보 개발자가 EasyDep API의 큰 구조를 찾기 위한 안내서다. 필드별 최신 계약은
실행 중인 FastAPI의 `/docs`가 기준이며, 이 문서는 endpoint의 책임과 호출 순서를 설명한다.

## 공통 원칙

- 기본 prefix는 `/api`다.
- 애플리케이션 식별자는 `app_id`, 한 번의 사용자 명령은 `command_id`로 구분한다.
- 오래 걸리는 작업은 대부분 “요청을 접수했다”는 뜻의 `202` 상태 코드와 명령·작업의 현재 상태를 먼저 반환한다.
- 진행 상황은 계속 같은 주소를 조회하는 방법뿐 아니라 브라우저 실시간 알림 연결로도 받을 수 있다.
- 오류 응답의 영어 `detail`은 클라이언트 계약일 수 있으므로 문서 번역을 이유로 변경하지 않는다.

## 워크스페이스 중심 사용 순서

```text
POST /api/workspace/apps
  → app_id와 첫 요구사항 command 수신

GET /api/workspace/apps/{app_id}
  → 현재 단계, 최신 명령과 산출물 요약 조회

GET /api/workspace/apps/{app_id}/events
  → 진행 event 스트림 구독

POST /api/workspace/apps/{app_id}/commands
  → advance, start_design, start_implementation 같은 다음 행동 요청
```

프론트엔드는 생성·수정·승인·테스트를 모두 Workspace command로 요청한다. 그래야 DB command
기록, 자동 모드와 재시작 복구가 동일하게 적용된다.

## Router 지도

| prefix/영역 | 코드 | 역할 |
|---|---|---|
| `/api/workspace` | `app/workspace/api.py` | 앱, 대화 명령, event, live preview |
| `/api/implementation` | `app/implementation/interfaces/http.py` | 구현 파일·버전·ZIP 조회 |
| 산출물 | `app/artifacts_api.py` | 저장 JSON과 생성 파일 조회 |

요구사항·설계·테스팅 실행은 별도 HTTP router를 두지 않는다. Workspace 서비스가 각 단계의
application service를 직접 호출한다.

## 상태 코드 해석

| 상태 코드 | 의미 |
|---:|---|
| `200` | 조회 또는 동기 작업 성공 |
| `202` | 비동기 command/job이 접수됨 |
| `404` | 앱, 명령, job 또는 산출물이 없음 |
| `409` | 현재 단계·상태와 요청 action이 충돌함 |
| `422` | 요청한 데이터의 모양 또는 값 제한을 만족하지 못함 |
| `500` | 내부 실행 실패. command/job report에서 원문 원인을 확인해야 함 |

## 호환성과 변경

웹 요청·응답 JSON, 저장 산출물과 중간 실행 상태는 서로 연결되어 있다. 필드를 바꿀 때는 웹 요청 형식만
수정하지 말고 repository, 프론트엔드 타입, 재개 코드와 계약 테스트를 함께 확인한다. 내부
Python import 경로나 private helper는 외부 API 계약이 아니다.
