# 프론트엔드 워크벤치

`frontend`는 SvelteKit으로 만든 EasyDep 대화형 UI다. 브라우저는 업무 규칙을 판단하지 않고,
백엔드가 제공한 command, event, artifact와 선택지를 표시하고 사용자의 선택을 API로 보낸다.

## 화면 구성

| 위치 | 역할 |
|---|---|
| `src/routes/+page.svelte` | 새 애플리케이션 요구사항을 입력하는 시작 화면 |
| `src/routes/workspace/+page.svelte` | 타임라인, 입력창, 단계 표시와 산출물 패널을 조율하는 주 화면 |
| `src/lib/api.ts` | fetch, SSE 연결과 오류 응답 변환 |
| `src/lib/types.ts` | 백엔드 응답을 표현하는 TypeScript 타입 |
| `src/lib/auto-mode.ts` | 현재 command가 노출한 다음 행동을 자동 선택 |
| `src/lib/artifacts.ts` | 단계별 산출물 종류와 표시 메타데이터 |
| `src/lib/components/` | 타임라인, 산출물, 지도, 입력과 공통 UI 구성요소 |

## 상태 흐름

```text
페이지 진입
  → 앱·최신 command·event·artifact snapshot 조회
  → SSE 연결
  → 새 event를 받으면 필요한 snapshot만 다시 조회
  → 사용자가 action 전송
  → 백엔드가 202로 command 접수
  → command 완료 전에도 event와 live preview를 계속 표시
```

`busy`는 서버 작업 전체가 끝났다는 뜻이 아니라 UI가 같은 요청을 중복 전송하지 않도록 막는
짧은 상태다. 실제 진행 상태는 command의 `status`를 기준으로 판단한다.

## 자동 모드 원칙

- 자동 모드는 백엔드에 숨은 권한을 주지 않는다.
- `nextAutoAction()`은 사람이 볼 수 있는 선택지와 동일한 action만 반환한다.
- 질문, 변경 확인, 같은 실패가 반복된 자동 수정처럼 사람의 판단이 필요한 상태에서는 자동으로 진행하지 않는다.
- 동일 command/action 조합은 `autoActionKey`로 중복 제출을 막는다.

## 개발 명령

```powershell
Set-Location frontend
npm ci
npm run check
npm run build
```

백엔드는 `frontend/build`를 정적 파일로 서비스한다. 개발 중에는 SvelteKit dev server를 별도로
쓸 수 있지만 API 주소와 CORS 설정이 현재 환경과 일치하는지 확인한다.
