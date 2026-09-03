# MySQL 구조와 설계 이유

EasyDep의 영구 저장 구조는 업무 의미가 분명한 7개 테이블만 사용한다. 개발 DB의 기존
데이터는 보존하지 않고 항상 삭제 후 재생성하므로 `schema_migrations`는 두지 않는다.
기준 정의는 ORM인 `app/db/models.py`이며 `app/db/schema.sql`은 같은 구조의 수동 확인용
DDL이다.

```text
apps
 ├─ artifact_versions ── artifact_files
 └─ workspace_commands

agent_checkpoints
agent_checkpoint_blobs
agent_checkpoint_writes
```

## 1. 구조를 단순화한 이유

기존 14개 업무·체크포인트 테이블은 수명주기와 조회 방식이 같은 데이터까지 별도 표로
나눠 관계를 따라가야 했다. 현재 구조는 다음 원칙으로 줄였다.

- 앱당 하나뿐이고 독립 이력·검색이 없는 배포 선택과 요구사항 실행 모드는 `apps`에 둔다.
- `artifacts` 포인터 표를 없애고 버전이 `(app_id, artifact_type, version_no)`로 앱을 직접
  참조한다. 최신값은 같은 키 범위의 가장 큰 버전 번호다.
- 요구사항과 설계 checkpoint는 저장 형식이 같으므로 3개 공용 표를 사용하고
  `graph_type`을 기본키 첫 열에 둬 충돌을 막는다.
- `workspace_events`는 영구 데이터가 아니다. 현재 SSE 진행 이벤트는 프로세스 메모리에
  최대 1,000개만 보관하므로 서버 재시작 뒤 과거 이벤트를 재생하지 않는다. 완료 결과와
  오류는 `workspace_commands`에 남는다.
- Testing의 고정 입력, 현재 노드와 누적 repair 이력은 해당 `workspace_commands.payload`의
  `testing_checkpoint`에 함께 저장한다. 명령과 수명주기가 같아 별도 1:1 테이블을 만들지 않으며,
  서버 재시작 뒤 checkpoint가 있는 중단 명령만 안전한 재개 대상으로 구분한다.
- 매번 DB를 비우고 재생성하므로 migration 이력 표와 증분 migration 코드는 제거했다.

이 선택으로 테이블 수, 조인 수, 외래키 수가 줄고 각 데이터의 소유자가 명확해진다. 다만
운영 데이터를 보존해야 하는 시점에는 정식 migration 도구와 이벤트 보존 정책을 다시
도입해야 한다.

## 2. 테이블 필드

### `apps` — 애플리케이션 작업의 루트

| 필드 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `app_id` | `VARCHAR(36)` | PK | API에서 사용하는 앱 UUID |
| `requirements_text` | `MEDIUMTEXT` | NULL | 사용자 원문 요구사항 |
| `resource_constraints_text` | `MEDIUMTEXT` | NULL | 원문 리소스 제약 |
| `current_stage` | `VARCHAR(32)` | NULL | 마지막으로 저장된 단계 |
| `deployment_preferences` | `JSON` | NULL | 앱의 최신 CSP·리전·예산 선택 |
| `requirements_gated` | `TINYINT(1)` | NULL | 요구사항 그래프의 gated 실행 여부 |
| `created_at` | `DATETIME(6)` | NOT NULL, 기본값 현재 시각 | 생성 시각(UTC) |

`deployment_preferences`와 `requirements_gated`는 앱당 하나이고 앱과 함께 삭제되며 단독으로
검색하거나 이력을 관리하지 않는다. 별도 1:1 테이블보다 같은 행에 두는 편이 더 직접적이다.

### `artifact_versions` — 산출물 불변 버전

| 필드 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `id` | `BIGINT` | PK, AUTO_INCREMENT | 파일 FK에 쓰는 내부 식별자 |
| `app_id` | `VARCHAR(36)` | FK → `apps`, NOT NULL, CASCADE | 소유 앱 |
| `artifact_type` | `VARCHAR(32)` | NOT NULL | 산출물 종류 |
| `version_no` | `INT` | NOT NULL, `> 0` | 종류별 1부터 증가하는 버전 |
| `content` | `LONGTEXT` | NOT NULL | 문서 본문 또는 파일 snapshot 메타데이터 |
| `syntax_valid` | `TINYINT(1)` | NULL | 문법 검증 결과 |
| `syntax_errors` | `JSON` | NULL | 검증 오류 목록 |
| `origin` | `VARCHAR(20)` | NOT NULL, 기본값 `GENERATED` | 생성·수정 출처 |
| `created_at` | `DATETIME(6)` | NOT NULL, 기본값 현재 시각 | 버전 생성 시각(UTC) |

`UNIQUE(app_id, artifact_type, version_no)`가 버전 중복을 막는 동시에 최신·이력·특정 버전
조회에 쓰인다. 종류는 단계가 늘 때마다 DDL을 바꾸지 않도록 SQL `ENUM` 대신 문자열로 둔다.
앱 행을 잠근 상태에서 다음 버전 번호를 계산하므로 여러 프로세스의 동시 저장도 직렬화된다.

### `artifact_files` — 파일 트리 snapshot

| 필드 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `artifact_version_id` | `BIGINT` | PK, FK → `artifact_versions`, CASCADE | 소속 버전 |
| `file_path` | `VARCHAR(512)` | PK, NOT NULL, 빈 문자열 금지 | 버전 내부 상대 경로 |
| `content` | `LONGTEXT` | NOT NULL | UTF-8 파일 내용 |
| `sha256` | `CHAR(64)` | NOT NULL, 소문자 16진수 CHECK | 파일 무결성 해시 |

`(artifact_version_id, file_path)` 자연 복합키로 별도 숫자 ID와 중복 인덱스를 없앴다.
Linux 배포 경로와 맞도록 `file_path`만 대소문자를 구분하며, 한 버전의 파일이 함께 정렬된다.

### `workspace_commands` — 사용자 명령 상태

| 필드 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `command_id` | `VARCHAR(36)` | PK | 명령 UUID |
| `app_id` | `VARCHAR(36)` | FK → `apps`, NOT NULL, CASCADE | 소유 앱 |
| `action` | `VARCHAR(48)` | NOT NULL | 실행 동작 |
| `stage` | `VARCHAR(32)` | NOT NULL | 실행 단계 |
| `status` | `VARCHAR(24)` | NOT NULL | `QUEUED`, `RUNNING`, 완료·실패 상태 |
| `payload` | `JSON` | NOT NULL | 명령 입력 |
| `result` | `JSON` | NULL | 완료 결과 |
| `error` | `LONGTEXT` | NULL | 실패 설명 |
| `created_at` | `DATETIME(6)` | NOT NULL, 기본값 현재 시각 | 생성 시각 |
| `started_at` | `DATETIME(6)` | NULL | 시작 시각 |
| `completed_at` | `DATETIME(6)` | NULL | 종료 시각 |

실행 도중 갱신되는 현재 상태와 최종 결과만 영구 보존한다. UI용 진행 이벤트는 재생을 위한
업무 데이터가 아니므로 이 테이블과 분리된 bounded 메모리 버퍼를 쓴다.

### `agent_checkpoints` — 그래프 실행 뼈대

| 필드 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `graph_type` | `VARCHAR(16)` | PK | `requirements` 또는 `design` namespace |
| `thread_id` | `VARCHAR(128)` | PK | 실행 thread, 현재는 `app_id` |
| `checkpoint_ns` | `VARCHAR(128)` | PK, 기본값 `''` | LangGraph namespace |
| `checkpoint_id` | `VARCHAR(128)` | PK | checkpoint ID |
| `parent_checkpoint_id` | `VARCHAR(128)` | NULL | 부모 checkpoint ID |
| `checkpoint_type` | `VARCHAR(32)` | NOT NULL | 직렬화 타입 |
| `checkpoint` | `LONGBLOB` | NOT NULL | checkpoint 뼈대 |
| `metadata_type` | `VARCHAR(32)` | NOT NULL | metadata 직렬화 타입 |
| `checkpoint_metadata` | `LONGBLOB` | NOT NULL | 실행 metadata |

### `agent_checkpoint_blobs` — 채널 값

| 필드 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `graph_type` | `VARCHAR(16)` | PK | 그래프 namespace |
| `thread_id` | `VARCHAR(128)` | PK | 실행 thread |
| `checkpoint_ns` | `VARCHAR(128)` | PK | checkpoint namespace |
| `channel` | `VARCHAR(255)` | PK | 상태 채널 |
| `version` | `VARCHAR(64)` | PK | 채널 값 버전 |
| `blob_type` | `VARCHAR(32)` | NOT NULL | 직렬화 타입 |
| `blob` | `LONGBLOB` | NULL | 직렬화된 채널 값 |

### `agent_checkpoint_writes` — 미반영 pending write

| 필드 | 타입 | 제약 | 설명 |
|---|---|---|---|
| `graph_type` | `VARCHAR(16)` | PK | 그래프 namespace |
| `thread_id` | `VARCHAR(128)` | PK | 실행 thread |
| `checkpoint_ns` | `VARCHAR(128)` | PK | checkpoint namespace |
| `checkpoint_id` | `VARCHAR(128)` | PK | 대상 checkpoint |
| `task_id` | `VARCHAR(128)` | PK | LangGraph task ID |
| `idx` | `INT` | PK | task 내 write 순번 |
| `channel` | `VARCHAR(255)` | NOT NULL | 대상 채널 |
| `write_type` | `VARCHAR(32)` | NOT NULL | 직렬화 타입 |
| `blob` | `LONGBLOB` | NOT NULL | 직렬화된 값 |
| `task_path` | `VARCHAR(255)` | NOT NULL, 기본값 `''` | task 경로 |

checkpoint는 고빈도 실행 상태이고 산출물과 수명주기가 다르므로 산출물 버전에 섞지 않는다.
세 표는 LangGraph의 checkpoint·channel value·pending write 계약을 그대로 나타낸다. 요구사항과
설계는 구조가 완전히 같아 `graph_type`으로만 분리하며, 기존 6개 checkpoint 표를 3개로 줄였다.

## 3. 인덱스 설계

| 테이블 | 키·인덱스 | 사용하는 조회 | 선택 이유 |
|---|---|---|---|
| `apps` | PK `(app_id)` | 앱 조회·동시 쓰기 잠금 | 모든 데이터의 소유 경계 |
| `apps` | `(created_at)` | 최근 앱 목록 | 전체 정렬 방지 |
| `artifact_versions` | UNIQUE `(app_id, artifact_type, version_no)` | 최신·이력·특정 버전 | 중복 방지와 조회를 하나의 키로 해결 |
| `artifact_files` | PK `(artifact_version_id, file_path)` | snapshot 전체·단일 파일 | 버전별 파일 locality와 경로 중복 방지 |
| `workspace_commands` | `(app_id, created_at)` | 앱의 최신 명령 | 앱 조건 뒤 최신 순서 조회 |
| `workspace_commands` | `(app_id, status)` | 앱별 활성 명령 검사 | 중복 실행 방지 |
| `workspace_commands` | `(status)` | 서버 시작 시 전역 활성 명령 정리 | 앱 조건 없는 조회 지원 |
| `agent_checkpoints` | PK `(graph_type, thread_id, checkpoint_ns, checkpoint_id)` | 그래프·thread 재개 | 격리 키와 기본 접근 순서 일치 |
| `agent_checkpoints` | `(graph_type, checkpoint_id)` | 그래프별 checkpoint 목록 | thread 조건 없는 cursor 조회 지원 |
| `agent_checkpoint_blobs` | 복합 PK | 필요한 `(channel, version)` 복원 | 중복 보조 인덱스 없이 정확한 값 조회 |

JSON과 `LONGTEXT`에는 현재 내부 속성 검색이나 전문 검색이 없어 인덱스를 두지 않는다. 사용하지
않는 인덱스는 쓰기·메모리 비용만 늘리므로 실제 repository 쿼리가 있는 열에만 적용했다.

## 4. 재생성 및 검증 방식

SQLAlchemy `create_all()`은 없는 테이블만 만들며 기존 테이블의 열을 바꾸지 않는다. 따라서 이번
구조는 Workbench의 기존 `easydep` 스키마를 새로고침하는 것만으로 반영되지 않는다. 데이터베이스를
삭제하고 다시 만든 뒤 애플리케이션의 `init_db()` 또는 `schema.sql`을 실행해야 한다.

개발 환경에서는 `.env`의 `DB_SCHEMA_RESET_ON_START=true` 또는 실행 스크립트의
`-ResetDatabaseSchema` 옵션으로 이 절차를 자동화할 수 있다. 옵션이 켜지면 현재 접속 설정의
`DB_NAME`을 `DROP DATABASE`한 뒤 같은 이름으로 만들고 ORM의 7개 표를 생성한다. 따라서 옛 ORM에만
존재하던 테이블도 남지 않는다. 시스템 schema는 거부하지만 업무 데이터는 전부 삭제하므로 한 번
초기화한 직후 `false`로 되돌려야 한다. 해당 DB 계정에는 `DROP/CREATE DATABASE` 권한이 필요하다.

```powershell
python -X utf8 -m pytest -q tests/test_db_schema.py tests/test_db_session.py tests/test_session_store.py
python -X utf8 verify_db.py
```

`verify_db.py`는 실제 MySQL에서 7개 테이블, repository 왕복 저장, 파일 경로 대소문자,
checkpoint 재개, 주요 쿼리의 인덱스 후보와 삭제 정리를 검증한다. Workbench에서는 실행 후
SCHEMAS 영역의 새로고침 버튼을 누르면 변경된 구조가 보인다.

2026-09-01에 Docker MySQL 8.4의 `easydep` DB를 삭제·재생성해 검증했다. 실제 테이블 수는
7개였고, 임시 앱·산출물 버전 2개·파일 4개·명령·checkpoint의 저장과 복원을 통과했다.
최신 명령, 산출물 버전, 파일, checkpoint blob 조회는 위 표의 인덱스를 실제 선택했으며,
검증 종료 후 7개 테이블의 행 수가 모두 0임을 확인했다.

2026-09-01에는 로컬 MySQL `127.0.0.1:3306`에서도 기존 구형 `easydep` 스키마에
`DB_SCHEMA_RESET_ON_START=true`를 일회성 적용했다. 구형 테이블이 제거되고 현재 7개 테이블만
재생성됐으며, 동일한 repository·checkpoint·인덱스 검증과 최종 0행 정리를 통과했다.
