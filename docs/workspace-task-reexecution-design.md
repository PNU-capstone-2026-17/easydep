# 작업 카드와 체크포인트 재실행 설계

## 1. 목적과 범위

EasyDep의 요구사항, 설계, 구현, 테스트 화면에서 사용자가 각 작업의 상태를 보고 다시 실행할
수 있게 한다. 재실행 때문에 무관한 상위 작업을 처음부터 반복해서는 안 되며, 앞 작업을 다시
실행해 입력이 달라지면 영향받는 하위 산출물을 정상 결과처럼 남겨서도 안 된다.

여기서 **작업**은 화면에 보이는 모든 함수 호출이 아니라, 저장된 상태에서 독립적으로 복구할 수
있는 최소 체크포인트 단위다. 한 작업 안의 병렬 LLM 호출과 검증기는 카드 내부 진행 목록으로
표시한다. 별도 체크포인트가 없는 내부 호출에 독립 재실행 버튼을 붙이지 않는다.

## 2. 현재 저장 경계

| 단계 | 현재 복구 단위 | 저장 위치 | 판정 |
|---|---|---|---|
| 요구사항 | 상위 LangGraph 노드 | MySQL requirements checkpoint | 작업 단위 재개 가능, 임의 되감기 API는 보완 필요 |
| 설계 | 클래스, 시퀀스, API, ERD, 배포 다이어그램 | MySQL design checkpoint | 실패 재개와 단계 되감기 가능 |
| 구현 | 생성, 계획과 동적 `taskId` | 구현 job 상태, manifest, 작업별 결과 파일 | 실패 작업 재개 가능 |
| 테스트 | 로컬 검증 job 전체 | 프로세스 메모리 | 서버 재시작을 넘는 재개 불가 |
| 배치 실험 | 4단계와 구현 하위 작업 | `RunStore`와 실행 산출물 | 실험용 복구 경로이며 대화형 UI의 직접 원본은 아님 |

따라서 새로운 실행 엔진을 하나 더 만들지 않는다. 공통 계층은 기존 저장소의 상태를 읽고 각
단계의 기존 재개 기능을 호출하는 어댑터다. 테스트 job만 먼저 영속화한다.

## 3. 사용자에게 보이는 작업 단위

### 요구사항

1. `requirements.refine` — 요구사항 정제와 분류
2. `requirements.deployment_constraints` — 배포 단서와 리소스 제약 구조화
3. `requirements.use_case_model` — 액터와 유스케이스 모델
4. `requirements.use_case_specifications` — 유스케이스 명세 묶음
5. `requirements.use_case_relationships` — 관계 검증과 다이어그램

유스케이스 명세 여러 개는 병렬 진행 상태를 카드 안에 표시한다. 현재는 명세 묶음이 하나의
체크포인트이므로 개별 유스케이스만 재실행한다고 표시하지 않는다.

### 설계

1. `design.class_diagram`
2. `design.sequence_diagram`
3. `design.api_specification`
4. `design.erd`
5. `design.deployment_diagram`

### 구현

1. `implementation.generation` — 초기 소스 생성과 검증
2. `implementation.planning` — 구현 작업 계획
3. `implementation.task:{taskId}` — manifest가 정의한 동적 구현 작업
4. `implementation.final_verification` — 최종 빌드·시험·산출물 승격

동적 작업 이름을 코드에 미리 열거하지 않는다. 구현 manifest의 `taskId`, 의존 작업과 상태를
그대로 사용한다.

설계 정합성 findings 때문에 구현이 `design-validation`에서 멈춘 경우는 구현 실패 작업을 새로
만들지 않는다. 구현 카드는 `blocked`로 표시하고, 원인을 소유한 설계 카드의 findings와
`Revise` 동작으로 이동시킨다. 설계의 결정론적 검증과 수정도 별도 생성 작업이
아니라 해당 설계 작업의 검증·수정 동작이다.

### 테스트

현재 실제 대화형 실행 경계에 맞춰 `testing.local_verification` 하나만 제공한다. 사용하지 않는
레거시 테스트 그래프의 정적·동적 노드를 카드로 노출하지 않는다. Docker 기능 시험과 실제
클라우드 시험이 주 실행 경계에 연결되고 각 결과가 저장된 뒤에만 별도 작업으로 분리한다.

## 4. 공통 상태와 동작

| 상태 | 의미 | 사용자 동작 |
|---|---|---|
| `not_started` | 아직 실행하지 않음 | 선행 작업 완료 후 `Run` |
| `queued` | 실행 대기 | 중복 실행 금지 |
| `running` | 실행 중 | 중복 실행 금지, 지원 시 `Cancel` |
| `awaiting_input` | 질문·검토·승인 대기 | `Revise`, `Continue`, `Approve` |
| `completed` | 결과와 검증이 완료됨 | `Run again` |
| `failed` | 실행 실패, 체크포인트가 남음 | `Retry` |
| `interrupted` | 서버 종료 등으로 중단됨 | 체크포인트 검증 후 `Resume` |
| `stale` | 상위 입력이 바뀌어 결과를 다시 확인해야 함 | 상위 작업 승인 후 `Run` |
| `blocked` | 선행 입력 또는 사용자 결정이 필요함 | 원인을 설명하고 해당 입력으로 이동 |

`Retry`와 `Run again`은 다르다.

- `Retry`는 같은 시도의 실패 체크포인트에서 이어서 실행한다.
- `Run again`은 같은 입력으로 새 시도를 만들며 이전 결과와 이력을 보존한다.
- 사용자 입력을 바꾸는 것은 재시도가 아니라 `Revise`이며 영향 분석을 거친다.

## 5. 공통 읽기 모델

UI는 단계별 저장 형식을 직접 알지 않고 다음 의미만 받는다.

```json
{
  "taskKey": "design.sequence_diagram",
  "stage": "design",
  "label": "Sequence diagram",
  "status": "failed",
  "attempt": 1,
  "progress": {
    "summary": "Return message validation failed",
    "activeItems": []
  },
  "actions": ["retry"],
  "impact": {
    "downstreamTaskKeys": [
      "design.api_specification",
      "design.erd",
      "design.deployment_diagram"
    ]
  }
}
```

체크포인트 파일 경로, 내부 thread ID와 프롬프트는 브라우저에 노출하지 않는다. 초기에는 별도
`workspace_tasks` 테이블을 만들지 않고 다음 자료에서 읽기 모델을 투영한다.

- 요구사항·설계 checkpoint와 저장된 산출물
- 구현 job과 workflow manifest
- 영속화한 testing job
- `workspace_commands`와 `workspace_events`의 시도·진행 이력

모든 새 progress event에는 안정적인 `task_key`를 기록한다. 서버가 실행 중 명령을
`INTERRUPTED`로 바꾸면, 마지막 상태가 `running`인 해당 작업도 읽기 모델에서
`interrupted`로 판정한다.

## 6. 공통 명령 경계

UI는 설계 전용 재시도 API처럼 단계별 API를 직접 호출하지 않는다.

```text
GET  /api/workspace/apps/{app_id}/tasks
POST /api/workspace/apps/{app_id}/task-actions
```

명령 본문은 다음 최소 정보만 사용한다.

```json
{
  "task_key": "design.sequence_diagram",
  "action": "retry",
  "expected_attempt": 1
}
```

`expected_attempt`가 현재 값과 다르면 오래된 화면의 중복 요청이므로 `409`로 거절한다. 모든
변경 명령은 기존 `workspace_commands`를 통해 직렬화하고 SSE progress event를 발생시킨다.

단계별 어댑터는 다음 계약을 구현한다.

```text
inspect(app_id, task_key) -> TaskState
retry(app_id, task_key, expected_attempt) -> result
rerun(app_id, task_key, expected_attempt) -> result
impact(app_id, task_key) -> downstream task keys
```

## 7. 체크포인트 검증과 영향 전파

재사용 전에는 최소한 다음을 확인한다.

1. 앱 ID와 작업 key가 일치한다.
2. 체크포인트가 가리키는 입력 산출물 버전 또는 digest가 현재 상위 산출물과 일치한다.
3. 완료 노드와 저장 산출물이 대응한다.
4. 구현 작업은 run ID, manifest의 `taskId`와 workspace가 일치한다.
5. 이미 실행 중인 동일 앱 명령이 없다.

실패 작업 재개는 정상 상위 작업을 보존한다. 완료된 상위 작업을 다시 실행하는 동안에는 하위
작업의 재실행 버튼을 잠시 막는다. 새 결과의 output digest가 이전과 같으면 하위 결과를 그대로
유지하고, 달라졌을 때만 하위 작업을 `stale`로 표시한다. 자동으로 전체 하위 파이프라인을
실행하지 않고 새 산출물의 검토 게이트에서 멈춘다.

- 요구사항 작업 재실행: 이후 요구사항 산출물과 설계·구현·테스트가 영향 대상
- 설계 작업 재실행: 해당 작업 이후 설계 산출물과 구현·테스트가 영향 대상
- 구현 동적 작업 재실행: manifest 의존 작업과 최종 검증·테스트가 영향 대상
- 테스트 재실행: 상위 산출물을 무효화하지 않음

버튼을 누르기 전에 영향받는 완료 작업이 있으면 목록을 보여주고 확인을 받는다. 실패 또는
중단된 현재 작업의 동일 체크포인트 재개는 상위 변경이 아니므로 별도 확인 없이 실행한다.

## 8. 작업 카드 UI

각 단계에는 하나의 진행 카드가 아니라 작업 카드 목록을 둔다. 현재 실행 중인 카드는 자동으로
펼치고, 완료·대기 카드는 접을 수 있다.

카드에는 다음만 기본 표시한다.

- 작업 이름과 상태
- 현재 동작 또는 결과 한 줄
- 경과 시간과 시도 횟수
- 상태에 맞는 주 동작 하나

오류 원문, 검증 findings와 체크포인트 상세는 펼쳤을 때 표시한다. 내부 리소스 제약과 경로는
기본 화면에서 숨긴다. 재실행 뒤에는 새 시도의 카드를 활성화하고 이전 시도는 이력으로 남긴다.

## 9. 구현 순서

1. 공통 `TaskDescriptor`와 단계별 읽기 어댑터를 추가하고 조회 API부터 만든다.
2. 기존 requirements/design progress metadata를 공통 `task_key`로 정규화한다.
3. testing job을 MySQL에 영속화하고 서버 재시작 시 `interrupted`로 복구한다.
4. 설계의 기존 `retry`, `rewind`를 공통 task action 뒤로 연결한다.
5. 요구사항 게이트 단위 되감기와 새 시도 생성을 추가한다.
6. 구현 workflow의 동적 `taskId`와 `retry_failed`를 공통 action에 연결한다.
7. SvelteKit에 전 단계 작업 카드 목록과 상태별 버튼을 적용한다.
8. 기존 설계 전용 버튼은 공통 경로가 검증된 뒤 제거한다.

## 10. 필수 검증

- 실패한 작업 재개 시 완료된 상위 작업 실행 횟수가 증가하지 않는다.
- 완료 작업 재실행 시 이전 결과가 보존되고 시도 번호가 증가한다.
- 앞 작업 재실행 시 영향받는 하위 작업이 `stale`로 바뀐다.
- 오래된 `expected_attempt` 요청과 동시 중복 실행은 거절된다.
- 서버 재시작 후 요구사항·설계·구현·테스트 카드 상태가 복원된다.
- 실패·중단·사용자 입력 오류가 서로 다른 상태와 행동으로 표시된다.
- 카드 진행 이벤트와 실제 checkpoint/workflow 상태가 다르면 실행을 허용하지 않는다.

이 설계는 재실행 UI를 위한 별도 오케스트레이터를 만들지 않는다. 기존 체크포인트를 진실
원천으로 유지하고, 워크스페이스는 상태 투영·사용자 확인·단계별 어댑터 호출만 담당한다.
