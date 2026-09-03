# EasyDep 대화형 개발 작업대

## 목적

EasyDep 작업대는 요구사항·설계·구현·테스트 화면을 따로 오가던 방식을 하나의 화면으로 통합한다. 사용자는 가운데 대화에서 현재 단계의 작업을 지시하고, 오른쪽에서 그 결과인 산출물과 검증 기록을 확인한다. 왼쪽에는 최근 앱이 남아 있어 이전 작업을 다시 열 수 있다.

이 변경은 **EasyDep 자체 화면**에만 적용된다. 구현 에이전트가 생성하는 대상 애플리케이션의 React/Vite 프론트엔드는 바꾸지 않는다.

## 화면 구조

```text
┌──────────────┬───────────────────────────┬──────────────────────┐
│ 최근 앱      │ 대화와 단계 진행          │ 개발 산출물          │
│ 새 앱        │ 요구→설계→구현→검증       │ 내용·검증·버전·근거 │
│ 실행 상태    │ 질문·승인·오류·완료 기록  │ 선택한 산출물 피드백 │
└──────────────┴───────────────────────────┴──────────────────────┘
```

- `/`: 새 앱의 자연어 요구사항, CSP, 리전을 입력한다.
- `/workspace/?app=<app-id>`: 앱 하나의 전체 개발 과정을 이어서 수행한다.
- 기존 `/requirements`, `/design`, `/implementation`, `/testing` 주소는 작업대로 이동한다.

오른쪽 산출물은 직접 편집하지 않는다. 산출물을 선택하고 대화로 수정 의견을 보내면 기존 설계 추적·부분 수정 경계를 사용한다. 현재 단계보다 앞선 산출물을 바꾸려는 경우 즉시 실행하지 않고, 이전 단계로 돌아갈지 먼저 확인한다.

## 기존 시스템과의 연결

작업대가 새로운 요구사항 분석기나 구현기를 만들지는 않는다. `app/workspace/`가 기존 단계 API를 호출하는 얇은 조정 계층이다.

```text
SvelteKit 작업대
  → Workspace 명령 API
    → 기존 요구사항 API
    → 기존 설계 세션 API
    → 기존 구현 Job API
    → 기존 테스트 Job API
```

명령의 현재 상태와 최종 결과는 `workspace_commands`에 저장한다. 화면 진행 이벤트는 서버의 bounded 메모리 버퍼에서 Server-Sent Events(SSE)로 전달하며 영구 저장하지 않는다. LLM의 사고 과정이나 토큰 조각을 중계하지 않고, 시작·질문·승인 필요·완료·실패 같은 작업 상태만 전송한다.

한 앱에서는 동시에 하나의 실행 명령만 허용한다. 질문이나 승인처럼 사용자 입력을 기다리는 상태는 새 응답 명령을 받을 수 있다. 서버가 재시작돼 실행 중이던 명령을 복원할 수 없으면 `INTERRUPTED`로 남기며, 전체 파이프라인을 자동 재실행하지 않는다. 실제 단계 복구는 기존 체크포인트가 실행 ID·앱 ID·완료 단계·산출물과 일치하는지 확인한 뒤 기존 오케스트레이터 경계에서 수행한다.

전 단계의 작업 카드, 상태별 `Retry`·`Run again`, 하류 영향 전파의 공통 설계는
[`workspace-task-reexecution-design.md`](workspace-task-reexecution-design.md)를 따른다.

## API 요약

| 메서드와 경로 | 역할 |
|---|---|
| `GET /api/workspace/apps` | 최근 앱과 마지막 명령 상태 조회 |
| `POST /api/workspace/apps` | 앱 생성 후 요구사항 분석 명령 제출 |
| `GET /api/workspace/apps/{appId}` | 단계·명령·이벤트·산출물 색인 조회 |
| `POST /api/workspace/apps/{appId}/commands` | 현재 단계 메시지, 진행, 승인, 테스트 명령 제출 |
| `GET /api/workspace/apps/{appId}/events` | 마지막 이벤트 이후의 상태를 SSE로 전달 |

## 로컬 실행

저장소 루트에서 다음 한 명령을 실행하면 프론트엔드 빌드, 개발용 MySQL 기동, FastAPI 실행, 화면·API·DB 연동 확인까지 수행한다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-easydep.ps1 -OpenBrowser
```

중지할 때는 다음 명령을 사용한다. 백엔드 프로세스와 MySQL 컨테이너만 중지하고 DB 볼륨은 다음 실행을 위해 보존한다.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run-easydep.ps1 -Stop
```

스크립트는 `easydep-mysql-dev` 컨테이너와 `.easydep/dev/` 아래의 PID·로그 파일만 관리한다. 실행 중이던 다른 Python·Docker 프로세스를 이름만 보고 종료하지 않는다. 기본 DB 포트는 `33060`, FastAPI는 `8100`, Vite 화면은 `5173`이며 각각 `-DatabasePort`, `-Port`, `-FrontendPort`로 바꿀 수 있다.

프론트엔드 의존성은 `package-lock.json`이 바뀐 경우에만 다시 설치한다. 기본 개발 모드는 Vite hot reload만 사용하고 FastAPI는 자동 재시작 없이 유지하므로, 앱 생성 도중 Python 파일이 바뀌어도 실행 중인 작업이 끊기지 않는다. 백엔드 소스를 개발하면서 자동 재시작이 필요할 때만 `-BackendReload`를 지정한다. 이 옵션을 사용하면 실행 중인 앱 생성 작업이 중단될 수 있다. 배포와 비슷한 정적 제공을 확인할 때는 `-ProductionLike`를 사용하며, 이때 `-ForceFrontendBuild` 또는 `-SkipFrontendBuild`를 함께 지정할 수 있다.

첫 백엔드 기동은 로컬 분석 모델을 메모리에 올리느라 수십 초 걸릴 수 있으며, 준비 제한은 10분이다. 진행이 멈춘 것처럼 보이면 `.easydep/dev/server.stderr.log`에서 Uvicorn 시작 과정을 확인할 수 있다.

수동으로 실행하려면 다음 순서를 사용한다.

```powershell
cd frontend
npm install
npm run check
npm run build

cd ..
python -m uvicorn server:app --port 8100
```

백엔드 코드 수정 즉시 재시작이 필요한 짧은 개발 세션에서만 위 명령에 `--reload`를 추가한다.

개발 중에는 `frontend`에서 `npm run dev`를 실행하면 `/api` 요청을 기본적으로 `127.0.0.1:8100`으로 전달한다. 다른 포트를 쓸 때에는 `EASYDEP_API_ORIGIN`을 지정한다. 배포 이미지는 Node 22 빌드 단계에서 SvelteKit 정적 결과를 만들고, 최종 Python 이미지에는 `frontend/build`만 복사한다.

## 현재 경계

- 상태 이벤트는 영속화하지만 LLM 토큰 스트리밍은 지원하지 않는다.
- 설계 산출물은 기존 버전 API로 이력을 보여주고, 구현이 만든 백엔드·프론트엔드·테스트·Docker·Terraform 파일 스냅샷도 같은 패널에서 읽을 수 있다. 파일은 저장소 원본을 조회할 뿐 작업대에서 직접 덮어쓰지 않는다.
- 프로세스가 이미 수행 중이던 외부 구현 작업을 서버 재시작 뒤 자동으로 다시 붙잡지는 않는다. 검증되지 않은 작업을 성공처럼 보이는 것보다 중단 사실을 남기는 쪽을 택한다.
- 데이터베이스 테이블은 현재 프로젝트 방식대로 시작 시 생성된다. 운영 배포에서 별도 스키마 마이그레이션 체계를 도입한다면 두 Workspace 테이블도 그 체계로 옮겨야 한다.
