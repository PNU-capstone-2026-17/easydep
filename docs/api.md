# EasyDep HTTP API 명세

기본 주소는 `http://localhost:8000`이다. 서버 실행 중 `/docs`에서 같은 계약의 Swagger UI,
`/openapi.json`에서 OpenAPI JSON을 확인할 수 있다. JSON 요청에는
`Content-Type: application/json`을 사용한다.

## 공통 식별자와 오류

- `app_id`: `POST /api/apps`가 발급하는 UUID. 요구사항부터 구현 산출물까지 같은 값을 쓴다.
- `thread_id`: 대화형 요구사항 분석 세션 ID.
- `job_id`: 비동기 구현 worker job ID.
- 오류는 FastAPI 표준 `{"detail": ...}` 형식이다.
- 주요 상태 코드는 `400` 잘못된 입력, `404` 대상 없음, `409` 선행 조건/상태 충돌,
  `422` 요청 스키마 오류, `502` 외부 LLM 또는 생성 실패다.

## 상태 확인

| Method | Path | 설명 |
|---|---|---|
| `GET` | `/api/health` | 애플리케이션 상태, `{"ok": true}` |
| `GET` | `/healthz` | Kubernetes probe, `{"status": "ok"}` |

## 애플리케이션 세션

### `POST /api/apps`

요구사항·설계·구현 산출물이 공유할 애플리케이션 세션을 만든다.

```json
{
  "requirements_text": "사용자는 상품을 주문할 수 있다.",
  "resource_constraints_text": "Azure에 배포하고 MySQL을 사용한다."
}
```

응답에는 `app_id`, 현재 `artifacts`, `validation`, `artifact_status`가 포함된다.

| Method | Path | 설명 |
|---|---|---|
| `GET` | `/api/apps` | 최근 애플리케이션 목록 |
| `GET` | `/api/apps/{app_id}` | 현재 요구사항·설계 산출물 전체 조회 |

## 요구사항 분석

### `POST /api/requirements/analyze`

신규 분석:

```json
{
  "requirements": ["사용자는 상품을 주문할 수 있다."],
  "feedback_gates": true,
  "app_id": "<UUID>"
}
```

질문 또는 피드백 이후 재개:

```json
{
  "thread_id": "<기존 thread_id>",
  "answer": "비회원도 주문할 수 있어야 한다.",
  "app_id": "<UUID>"
}
```

`status`는 다음 중 하나다.

- `need_clarification`: `questions`에 답한 뒤 같은 `thread_id`로 재호출한다.
- `need_feedback`: `feedback_prompt`를 확인하고 `answer`로 재호출한다.
- `completed`: `requirements`, `actors`, `use_cases`, `use_case_specs`, `relationships`,
  `diagram` 등을 반환한다.

`app_id`가 있으면 완료된 단계가 MySQL에 버전 저장되고 `saved_stages`로 반환된다.

## 설계 산출물

사용 가능한 `stage`:

```text
refined_requirements
usecase_spec
usecase_diagram
resource_spec
class_diagram
sequence_diagram
api_spec
erd
deployment_diagram
```

`refined_requirements`, `usecase_spec`, `usecase_diagram`, `resource_spec`는 요구사항 분석
에이전트가 생성한다. 독립 `generate` 호출은 지원하지 않지만 `content`로 가져올 수 있다.

| Method | Path | 설명 |
|---|---|---|
| `POST` | `/api/apps/{app_id}/stages/{stage}/generate` | 저장된 선행 산출물로 설계 생성 |
| `POST` | `/api/apps/{app_id}/stages/{stage}/feedback` | `{"feedback":"..."}`로 현재 산출물 수정 |
| `POST` | `/api/apps/{app_id}/stages/{stage}/content` | `{"content": ...}` 외부 산출물 저장 |
| `GET` | `/api/apps/{app_id}/stages/{stage}/versions` | 버전 이력 |
| `GET` | `/api/apps/{app_id}/stages/{stage}/versions/{version_no}` | 특정 버전 내용 |
| `GET` | `/api/apps/{app_id}/stages/{stage}/image.png` | PlantUML PNG 렌더링 |
| `GET` | `/api/apps/{app_id}/stages/{stage}/image.svg` | PlantUML SVG 렌더링 |

설계 생성 순서는 `class_diagram → sequence_diagram → api_spec → erd →
deployment_diagram`이다. 선행 산출물이 없으면 `409`, 같은 산출물을 다른 요청이 생성 중이면
`409`를 반환한다.

## 시스템 구현

### `POST /api/implementation/apps/{app_id}/jobs`

저장된 설계를 읽고 비동기 구현 job을 계획한다. `202 Accepted`를 반환한다.

```json
{
  "base_package": "com.example.orders",
  "allow_assumptions": true
}
```

- `base_package`: 생성 Java package. 기본값 `com.example.generated`.
- `allow_assumptions`: 설계가 참조하지만 선언하지 않은 값 타입을 보고서에 명시된
  placeholder로 생성할지 여부. 기본값 `true`.
- 클래스 다이어그램과 API 명세가 없으면 `409`.

### `GET /api/implementation/jobs/{job_id}`

job 상태와 workflow checkpoint를 조회한다. 주요 `status`:

| Status | 의미 |
|---|---|
| `QUEUED` | worker 대기열 |
| `PLANNING` | Java 계약 생성과 phase 계획 중 |
| `AWAITING_APPROVAL` | 현재 NIM 전송 요청의 HITL 승인 대기 |
| `RUNNING` | 승인된 phase 실행 중 |
| `COMPLETED` | 완료 감사와 파일 저장 완료 |
| `NEEDS_INPUT` | 설계 계약 보완 필요. 설계 에이전트로 자동 회송하지 않음 |
| `NEEDS_PLANNER` | 현재 구현 범위 밖의 감사 backlog 또는 분류할 수 없는 후속 작업 필요 |
| `FAILED` | `error`와 workflow 증거 확인 필요 |
| `REJECTED` | 사용자가 외부 전송을 거부함 |

`AWAITING_APPROVAL`이면 `transmission_request`에 `requestId`, provider, notice, task 목록,
source hash와 허용 쓰기 경로가 포함된다. API key와 로컬 절대 source 경로는 포함되지 않는다.

### `POST /api/implementation/jobs/{job_id}/approval`

```json
{
  "request_id": "<현재 64자리 requestId>",
  "approved": true,
  "approved_by": "EasyDep user",
  "retry_failed": false
}
```

현재 요청 ID와 정확히 일치하는 승인만 `202 Accepted`로 실행된다. 거부하려면
`approved=false`를 보낸다. 이전 phase ID, 변경된 prompt/source에 대한 오래된 ID, 승인 대기
상태가 아닌 job은 `409`다.

자동 phase 순서는 다음과 같다.

```text
Control
→ Persistence / API adapter / Boundary adapter
→ Gateway outbound adapter
→ Spring wiring
→ Frontend implementation
→ E2E integration test
```

기본 `delegate_repair_approvals=true`이면 최초 승인에 현재 run ID, 입력 hash, 최초 계획 task,
최대 수리 횟수와 총 시도 횟수가 함께 고정된다. 이후 phase와 그 범위 안의 수리는 같은 승인을
재사용해 완료까지 자동 진행한다. 이 값을 `false`로 보내면 각 후속 전송 요청을 별도로 승인해야
한다.

### CLI에서 한 번 승인하고 완료까지 실행

먼저 job JSON으로 결정론적 뼈대와 실행 계획을 생성한 뒤 다음 명령을 사용한다.

```powershell
python -m app.implementation.interfaces.cli run-to-completion <run-directory> <job.json> `
  --approve-all-external-transmission `
  --approved-by "EasyDep CLI user"
```

`--approve-all-external-transmission`은 필수이며, 해당 run의 정확한 입력 hash와 최초 계획 task에만
승인 범위를 묶는다. CLI는 `COMPLETE`가 될 때까지 후속 phase 및 최대 3회의 계획된 수리를
계속 실행한다. `FAILED`, `NEEDS_INPUT`, `NEEDS_PLANNER`, 최대 50회 task 시도 또는 100회 workflow
cycle에 도달하면 범위를 임의로 넓히지 않고 종료한다. 승인 증거는
`reports/one-time-run-approval.json`에 남는다.

## 구현 파일 산출물

허용 `artifact_type`:

```text
SOURCE_CODE
FRONTEND_SOURCE_CODE
TEST_CODE
DEPLOYMENT_FILE
IAC_CODE
```

| Method | Path | 설명 |
|---|---|---|
| `GET` | `/api/implementation/apps/{app_id}/artifacts/{artifact_type}` | 현재 버전 metadata와 파일 경로/SHA-256 목록 |
| `GET` | `/api/implementation/apps/{app_id}/artifacts/{artifact_type}/versions` | 파일 트리 snapshot 버전 이력 |
| `GET` | `/api/implementation/apps/{app_id}/artifacts/{artifact_type}/files/{file_path}` | 현재 버전의 단일 UTF-8 파일 내용과 SHA-256 |

파일 버전은 전체 파일 트리를 하나의 불변 snapshot으로 저장한다. API 응답의 `file_path`는
URL path이므로 `/`를 포함할 수 있다.

### `POST /api/implementation/apps/{app_id}/frontend`

저장된 OpenAPI 명세를 OpenAPI Generator의 `typescript-fetch` generator에 전달해
React + TypeScript(Vite) 뼈대와 API client/model 계약을 만들고
`FRONTEND_SOURCE_CODE`의 새 버전으로 저장한다. 이 API는 결정론적 scaffold 단계만 수행한다.

```json
{
  "application_name": "Order Console",
  "api_base_url": "/api"
}
```

`api_base_url`은 선택값이다. 생략하면 OpenAPI `servers[0].url`과 server variable의
default를 사용하며, `servers`도 없으면 임의의 `/api` prefix를 붙이지 않고 동일 origin의
root를 사용한다.

API 명세가 없거나 지원하는 operation이 하나도 없으면 `409`를 반환한다. 생성 파일은 위의
파일 산출물 조회 API로 목록, 버전 이력, 개별 내용을 확인할 수 있다. 전체 시스템 구현 job은
같은 scaffold 이후 Class Diagram, Sequence Diagram, OpenAPI와 생성된 TypeScript 계약만
프론트엔드 구현 에이전트에 전달한다. 에이전트는 `application/frontend/src/generated`를
수정할 수 없으며 React 화면 구현 후 generated-client 사용 검사와 `npm run build`를 통과해야
다음 E2E 단계로 진행한다. 원문 요구사항은 프론트엔드 구현 prompt에 전달하지 않는다.
