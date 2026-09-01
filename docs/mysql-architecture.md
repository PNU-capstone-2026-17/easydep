# MySQL 구조와 설계 이유

EasyDep의 MySQL은 단순한 결과 캐시가 아니라 **업무 산출물의 기준 데이터**, Workspace의
실행 기록, 요구사항·설계 그래프의 재개 지점을 함께 보존한다. 현재 구조는 성격이 다른
데이터를 세 묶음으로 분리한다.

```text
apps
 ├─ deployment_preferences
 ├─ workspace_commands ── workspace_events
 └─ artifacts ── artifact_versions ── artifact_files

requirements_checkpoints / _blobs / _writes / requirements_sessions
design_checkpoints       / _blobs / _writes

schema_migrations
```

## 1. 업무 데이터 구조

### `apps`: 모든 업무 데이터의 경계

한 행이 사용자가 만든 애플리케이션 개발 세션 하나다. `app_id`는 API에서도 쓰는 UUID 문자열이며,
원문 요구사항과 리소스 제약을 보존한다. 정제된 요구사항만 저장하지 않는 이유는 피드백으로 첫
단계를 다시 만들 때 사용자가 실제로 입력한 문장이 필요하기 때문이다.

UUID를 `BINARY(16)`이 아닌 `VARCHAR(36)`으로 둔 것은 현재 데이터 규모에서 저장 공간보다 API,
로그, 수동 장애 분석에서의 가독성이 더 중요하기 때문이다. 수천만 앱 규모가 되면 보조 숫자 키나
`BINARY(16)` 전환을 다시 검토할 수 있다. 최근 앱 목록이 `created_at DESC`를 사용하므로
`ix_apps_created_at` 인덱스를 둔다.

### `artifacts`와 `artifact_versions`: 현재값과 이력의 분리

`artifacts`에는 `(app_id, artifact_type)`당 한 행만 존재한다. 이 행은 현재 버전 번호를
가지며 실제 내용은 `artifact_versions`에 새 행으로만 추가한다.

이렇게 나눈 이유는 다음과 같다.

- 피드백이나 재실행이 이전 결과를 덮어쓰지 않는다.
- 현재 결과 조회는 `(artifact_id, version_no)` 유일 인덱스의 point lookup으로 끝난다.
- 버전 번호는 산출물별로 1부터 단조 증가하며, 유일 제약으로 중복을 막는다.
- `artifact_type`은 SQL `ENUM` 대신 `VARCHAR`다. 구현 코드, IaC, 테스트 결과처럼 종류가 계속
  늘어날 때 뜨거운 테이블을 `ALTER ENUM`하지 않기 위해서다.

현재 버전은 별도 `current_version_id`를 두지 않고 `latest_version_no` 하나로 표현한다. 값이 0이면
아직 생성되지 않았고, 양수이면 `(artifact_id, version_no)` 유일 키로 현재 행을 읽는다. 두 포인터가
서로 어긋나는 상태와 cascade 순환을 구조에서 제거한 것이다. 저장 시에는 부모 앱 행을 잠근 뒤 번호를
1 증가시키며, CHECK 제약으로 음수를 막는다.

`artifact_versions.id`는 유지한다. `(artifact_id, version_no)`가 업무상 유일 키이지만 파일 행마다
두 열을 반복하고 테스트 작업도 두 값을 들고 다니게 하면 하위 FK와 실행 계약이 커진다. 따라서 버전
번호의 의미·중복 방지는 복합 UNIQUE가 담당하고, 파일 FK와 실행 중 snapshot 고정은 compact한 단일
BIGINT ID를 사용한다. 반대로 `artifact_files.id`는 아무 곳에서도 참조하지 않아 제거했다.

`content`가 `LONGTEXT`인 이유는 PlantUML뿐 아니라 큰 JSON 모델과 파일 snapshot 메타데이터도
담기 때문이다. JSON으로 검색하는 값은 `syntax_errors`처럼 실제 JSON 연산 가능성이 있는 열만
MySQL `JSON` 타입으로 둔다.

### `artifact_files`: 파일 트리의 불변 snapshot

구현·테스트·IaC는 파일 하나가 아니라 서로 맞물린 전체 트리다. 따라서 한 버전 아래에 모든 파일을
저장하고 `(artifact_version_id, file_path)` 자체를 복합 기본키로 쓴다. 사용하지 않는 숫자 `id`와
중복 unique index를 두는 것보다, 한 버전의 파일이 물리적으로 함께 정렬되어 snapshot 조회에도
유리하다. Linux 파일 경로에 맞춰 `file_path`만 대소문자를 구분하는 `utf8mb4_0900_as_cs`
collation을 사용한다. `README.md`와 `readme.md`가 같은 행으로 취급되는 오류를 막기 위해서다.
`sha256`은 다시 읽은 파일의 동일성 및 전체 snapshot digest 계산에 사용한다.
고정 길이 ASCII 16진수이므로 `CHAR(64) CHARACTER SET ascii COLLATE ascii_bin`으로 저장해
가변 길이·다국어 collation 비용을 피하고 소문자 64자리 CHECK를 적용한다.

### `deployment_preferences`: 버전 산출물이 아닌 최신 초안

리전·CSP 대안·예산 선택은 요구사항 분석 중 여러 번 바뀔 수 있는 초안이다. 아직 정식 산출물이
아니므로 `artifact_versions` 이력을 늘리지 않고 `app_id`당 JSON 한 행을 upsert한다. 앱이 삭제되면
함께 삭제된다.

### `workspace_commands`와 `workspace_events`: 실행 상태와 표시 기록의 분리

`workspace_commands`는 한 사용자 명령의 상태 머신(`QUEUED`, `RUNNING`, 완료·실패·입력 대기),
입력, 결과, 오류와 실행 시간을 가진다. `workspace_events`는 SSE와 화면 타임라인을 위한 append-only
기록이다. 명령 상태 한 행을 계속 수정하는 것과 사용자에게 보여 준 과거 메시지를 분리해, 재접속
후에도 같은 타임라인을 복구한다.

명령 생성은 `apps`의 해당 행을 `SELECT ... FOR UPDATE`로 잠근 뒤 활성 명령을 검사한다. 단순히
조회 후 INSERT하면 동시에 들어온 두 요청이 모두 “활성 명령 없음”을 볼 수 있기 때문이다.

이벤트의 `command_id`는 앱 자체 이벤트를 위해 NULL을 허용한다. 값이 있을 때에는
`(app_id, command_id)` 복합 외래키가 같은 앱의 명령만 가리키도록 강제한다. 앱별 최신 명령과
SSE 증분 조회에 맞춰 각각 `(app_id, created_at)`, `(app_id, event_id)` 인덱스를 둔다.

## 2. 체크포인트 구조

요구사항과 설계는 LangGraph 실행을 서버 재시작 뒤 이어야 한다. 두 에이전트는 같은 저장 규약을
쓰지만 테이블 접두사를 분리해 운영자가 소유자를 즉시 알 수 있게 했다.

- `*_checkpoints`: checkpoint 뼈대와 metadata
- `*_checkpoint_blobs`: 채널별 값의 버전 blob
- `*_checkpoint_writes`: interrupt 시 아직 반영되지 않은 pending write
- `requirements_sessions`: 요구사항 graph가 gated topology로 시작했는지 기록

blob은 일반 `BLOB`의 약 64 KiB를 쉽게 넘으므로 MySQL에서 `LONGBLOB`을 쓴다. checkpoint 한 건을
읽을 때 `(channel, version)` 쌍을 함께 조건에 넣어 기존 복합 기본키
`(thread_id, checkpoint_ns, channel, version)`를 그대로 사용한다. 별도 blob 인덱스를 중복 생성하지
않는다. 반대로 전체 checkpoint 목록 API는 thread 선두 기본키를 사용할 수 없으므로
`checkpoint_id` 보조 인덱스를 둔다. blob을 산출물 버전에 넣지 않는 이유는 superstep마다 발생하는
고빈도 임시 이력이 영구 산출물 이력을 폭증시키기 때문이다.

체크포인트 blob은 여러 checkpoint가 같은 채널 버전을 재사용할 수 있고 pending write가 먼저
생길 수도 있어 억지 외래키로 묶지 않는다. 삭제는 saver가 thread 단위 transaction에서 writes,
blobs, checkpoints 순서로 명시적으로 수행한다.

## 3. 트랜잭션과 연결

모든 repository는 공용 `session_scope()`를 사용한다. 정상 종료는 commit, 예외는 rollback하며
항상 connection을 pool에 반환한다. 프로세스마다 Engine과 pool은 하나만 만들고, 긴 LLM 실행 뒤
끊어진 연결을 받지 않도록 `pool_pre_ping`과 `pool_recycle`을 사용한다.

MySQL `DATETIME(6)`에는 timezone 없는 UTC를 저장하고 API 경계에서 timezone을 붙인다. DB 서버의
session timezone에 따라 값이 달라지지 않게 하는 규칙이다. 문자열은 database와 연결 모두
`utf8mb4`를 사용한다.

## 4. 이번 보강 내용

기존 구조의 큰 방향은 적절했지만 다음 부분은 취약했다.

| 기존 취약점 | 보강 |
|---|---|
| `create_all()`이 이미 존재하는 표를 변경하지 않음 | `schema_migrations`와 idempotent startup migration 추가 |
| 동시 명령 두 건이 활성 상태가 될 수 있음 | 명령 생성 전에 부모 `apps` 행 잠금 |
| 첫 산출물 저장 또는 동시 버전 생성 경쟁 | 버전 번호를 읽기 전에 부모 `apps` 행 잠금 |
| 이벤트가 다른 앱의 command ID를 담을 수 있음 | `(app_id, command_id)` 복합 외래키 |
| 현재 버전을 ID와 번호 두 열로 중복 표현 | `latest_version_no` 하나와 `(artifact_id, version_no)` 유일 키로 단순화 |
| 사용하지 않는 `generation_started_at` 열 | 모델과 DDL에서 제거 |
| 파일마다 불필요한 숫자 PK와 대소문자 무시 경로 | `(artifact_version_id, file_path)` 복합 PK와 case-sensitive collation |
| `workspace_events.event_id`의 장기 범위가 INT | append-only 증가량을 고려해 BIGINT로 확대 |
| 전역 활성 명령·checkpoint 목록 인덱스 누락 | `status`, `checkpoint_id` 보조 인덱스 추가 |
| checkpoint blob 조회가 같은 version의 불필요한 채널도 읽음 | `(channel, version)` tuple 조건으로 PK range lookup |
| 요구사항·설계 checkpoint SQL 로직이 중복됨 | 테이블 이름만 단계별로 두고 saver 구현은 `app.db.checkpointer` 한 곳에서 공유 |
| 사이드바가 앱마다 최신 command를 다시 조회함 | `(app_id, created_at)` 상관 subquery join으로 N+1을 단일 SQL로 축소 |
| 앱 상태 복원이 산출물마다 버전을 다시 조회함 | `(artifact_id, version_no)` join으로 현재 산출물 전체를 단일 SQL로 복원 |
| 파일 버전 목록이 개수를 위해 `LONGTEXT` 파일 전체를 로드함 | 복합 PK 선두 열을 쓰는 correlated `COUNT`만 실행 |
| `schema.sql`이 Workspace·checkpoint 표를 누락 | 모든 현재 테이블을 포함한 기준 DDL로 갱신 |
| `latest_version_no`에 해당하는 행이 없어도 조회가 계속됨 | artifact-scoped 현재 버전이 없으면 명시적 무결성 오류 |

현재 개발 데이터는 보존 대상이 아니므로 빈 schema 재생성을 기준으로 한다. migration은 이후
동일 revision의 중복 적용을 막기 위해 성공한 revision만 `schema_migrations`에 기록하며, 각 제약과
인덱스의 존재 여부를 확인한 뒤 필요한 DDL만 실행한다. 운영 데이터 보존이 요구되는 시점부터는
별도 backup·rollback 절차와 대용량 online DDL 검증을 release 과정에 추가해야 한다.

## 5. 남은 의도적 절충

- 산출물 본문 전체 검색은 현재 제품 요구가 아니므로 `LONGTEXT`에 전문검색 인덱스를 두지 않았다.
- 완료 이력을 영구 보존하므로 자동 retention/partition은 아직 없다. 데이터량을 측정한 뒤
  checkpoint TTL과 오래된 artifact 보관 정책을 별도로 정해야 한다.
- 상태·산출물 종류는 확장 속도가 빨라 `ENUM`이나 강한 CHECK 목록으로 닫지 않았다. 허용 값은
  서비스 계층이 검증한다.
- 테스트 job 자체는 아직 프로세스 메모리에 있어 MySQL 영속화 대상이 아니다. 이는 현재 시스템의
  별도 한계이며 artifact/checkpoint 스키마에 억지로 섞지 않는다.

현재 ORM 기준은 `app/db/models.py`와 각 checkpoint 모델이고, 전체 수동 설치 DDL은
`app/db/schema.sql`, 기존 DB의 증분 변경은 `app/db/migrations.py`가 담당한다.

## 6. 인덱스 설계표

인덱스는 “검색할 가능성이 있다”는 이유로 추가하지 않고 현재 repository의 동등 조건,
범위 조건, 정렬 순서에 맞춘다. 복합 인덱스는 왼쪽 열부터 사용된다는 MySQL 규칙을 기준으로
열 순서를 정했다.

| 테이블 | 인덱스 또는 키 | 사용하는 쿼리 | 선택 이유 |
|---|---|---|---|
| `apps` | PK `(app_id)` | 앱 단건 조회·행 잠금 | 모든 하위 데이터의 경계이자 가장 빈번한 point lookup |
| `apps` | `(created_at)` | 최근 앱 `ORDER BY created_at DESC LIMIT 50` | 전체 정렬을 피하고 인덱스 끝에서 필요한 수만 읽음 |
| `artifacts` | UNIQUE `(app_id, artifact_type)` | 앱 전체 산출물 및 종류별 단건 조회 | 중복 방지와 조회를 한 키로 해결; `app_id`만 쓰는 조회도 leftmost prefix 사용 |
| `artifact_versions` | UNIQUE `(artifact_id, version_no)` | 이력 정렬·특정 버전 조회 | 산출물별 번호 중복을 막고 별도 sort 없이 순차 이력 조회 |
| `artifact_files` | PK `(artifact_version_id, file_path)` | 한 snapshot의 전체 파일 조회 | 파일을 버전별·경로순으로 clustering하고 중복 secondary index 제거 |
| `workspace_commands` | `(app_id, created_at)` | 앱의 최신 명령 | 앱으로 좁힌 뒤 최신 한 행을 인덱스 순서로 조회 |
| `workspace_commands` | `(app_id, status)` | 새 명령 전 앱별 활성 명령 확인 | 부모 앱 잠금 안에서 `QUEUED/RUNNING` 존재 여부를 빠르게 확인 |
| `workspace_commands` | `(status)` | 서버 시작 시 모든 활성 명령 중단 처리 | 앱 조건이 없는 전역 조회는 앞 인덱스의 `app_id` 선두를 사용할 수 없으므로 분리 |
| `workspace_events` | `(app_id, event_id)` | SSE의 `event_id > :after ORDER BY event_id LIMIT :n` | 동등 조건 다음에 범위·정렬 열을 배치한 keyset pagination |
| `workspace_events` | `(app_id, command_id)` | command 복합 FK 검사 | MySQL이 암묵 인덱스를 만들게 두지 않고 이름과 목적을 schema에 명시 |
| `*_checkpoints` | PK `(thread_id, checkpoint_ns, checkpoint_id)` | thread의 최신·특정 checkpoint | 실행 재개의 기본 접근 경로를 그대로 키로 사용 |
| `*_checkpoints` | `(checkpoint_id)` | saver의 전역 `list(before=...)` | thread 조건이 없는 관리 경로는 복합 PK를 사용할 수 없어 보조 |
| `*_checkpoint_blobs` | PK `(thread_id, checkpoint_ns, channel, version)` | 필요한 `(channel, version)` 값 복원 | tuple 조건을 사용해 PK만으로 정확한 blob만 읽음 |

다음 인덱스는 의도적으로 두지 않았다.

- `artifact_versions.version_no` 단독 인덱스: 모든 조회가 먼저 `artifact_id`로 범위를 한정하므로
  `(artifact_id, version_no)` 유일 인덱스로 충분하다.
- JSON·`LONGTEXT` 열: 현재는 문서 전체를 원자적으로 읽고 쓰며 내부 속성으로 필터링하지 않는다.
  사용하지 않는 generated column이나 전문검색 인덱스는 쓰기 비용만 늘린다.
- checkpoint blob의 `(thread_id, checkpoint_ns, version)` 중복 인덱스: 쿼리를
  `(channel, version)` tuple로 고쳐 기존 PK가 더 적은 행을 읽도록 했다.

운영 데이터가 커지면 `EXPLAIN ANALYZE`에서 예상 key, 실제 rows, sort 발생 여부를 확인한다.
인덱스 이름만 존재하는 것으로 성능 개선을 판정하지 않으며, 느린 쿼리 로그의 호출 빈도와 쓰기
증가량까지 함께 비교한다.

## 7. 보고서용 설계 결정 요약

다음 표는 설계 보고서의 “대안 비교 및 선정 근거”에 바로 사용할 수 있는 결정 기록이다.

| 결정 항목 | 검토한 대안 | 선택 | 선정 근거와 절충 |
|---|---|---|---|
| 산출물 갱신 | 최신 행 덮어쓰기 / 버전 append | `artifacts.latest_version_no` + `artifact_versions` append | 현재 조회와 감사·피드백 이력을 모두 만족한다. 행 수는 늘지만 복구 가능성을 우선했다. |
| 설계 JSON 저장 | 모든 클래스·필드를 관계형 분해 / 문서 단위 저장 | 버전별 원자적 문서 | 각 단계가 전체 모델을 함께 검증·교체하고 내부 필드 SQL 검색이 없으므로 원자성이 더 중요하다. |
| 파일 snapshot 키 | 숫자 `id` + `(version,path)` UNIQUE / 자연 복합키 | `(artifact_version_id,file_path)` PK | 참조되지 않는 숫자 키와 중복 인덱스를 제거하고 버전별 파일 locality를 높인다. |
| 파일 경로 collation | DB 기본 대소문자 무시 / 대소문자 구분 | 경로 열만 `utf8mb4_0900_as_cs` | 실제 Linux 배포 파일 시스템의 동일성 규칙과 맞춘다. |
| 종류·상태 타입 | SQL `ENUM` / 문자열 | `VARCHAR` + 서비스 검증 | 파이프라인 확장이 잦아 종류 추가 때 hot-table DDL을 피한다. 잘못된 값 방지는 서비스 계약이 담당한다. |
| 현재 버전 표현 | 별도 버전 ID 포인터 / 최신 번호 / 매번 `MAX()` | `latest_version_no` + artifact-scoped 유일 키 | 포인터 중복과 교차 artifact 참조 가능성을 없애면서 매 조회의 집계 비용도 피한다. |
| 버전 기본키 | `(artifact_id, version_no)` 복합 PK / 숫자 PK | BIGINT PK + 업무 복합 UNIQUE | 하위 파일 FK와 테스트 snapshot 참조는 작게 유지하고 산출물별 번호 의미는 UNIQUE로 보존한다. |
| checkpoint 저장 | 산출물 이력과 통합 / 전용 표 | 전용 skeleton·blob·write 표 | 고빈도 실행 상태가 영구 산출물 이력을 오염시키지 않고 LangGraph 재개 계약을 그대로 보존한다. |
| 동시 쓰기 제어 | 애플리케이션 메모리 lock / DB 부모 행 lock | `apps` 행 `FOR UPDATE` | 여러 process·replica에서도 같은 앱의 명령과 버전 번호 생성을 직렬화한다. 앱 간 쓰기는 병렬로 유지된다. |
| 스키마 변경 | 시작 때 `create_all()`만 사용 / 버전 migration | ORM + `schema_migrations` | 신규 설치와 기존 DB 업그레이드를 모두 지원하고 적용 이력을 감사할 수 있다. |
| 이벤트 ID | `INT` / `BIGINT` | `BIGINT AUTO_INCREMENT` | append-only 이벤트의 장기 누적에서 약 21억 한계를 제거하며 추가 인덱스 폭 증가는 허용 가능한 수준이다. |

보고서 서술의 핵심은 “정규화를 많이 하는 것” 자체가 목적이 아니라는 점이다. 앱·산출물·버전·파일처럼
독립 생명주기와 무결성 경계가 있는 데이터는 관계형으로 분리했고, 한 단계에서 항상 전체를 교체하는
가변 설계 모델은 문서로 보존했다. 즉, 접근 패턴과 변경 단위를 기준으로 관계형 정규화와 문서 저장을
혼합한 구조다.

현재 `app_id`는 데이터 분리 키이지 사용자 권한 모델은 아니다. 인증을 도입할 때에는 `tenant_id`나
`owner_id`를 `apps`에 추가하고 모든 접근에서 권한 조건을 강제해야 한다.

## 8. 실제 MySQL 검증 결과

2026-08-31에 Docker의 MySQL 8.4 컨테이너에서 `easydep` 데이터베이스를 비운 뒤 현재 소스로
재생성해 검증했다. 검증은 ORM 선언만 비교하지 않고 실제 repository와 checkpoint saver를 통해
다음 순서로 수행했다.

1. 앱·명령·이벤트를 저장한다.
2. `README.md`와 `readme.md`를 함께 가진 파일 snapshot 두 버전을 저장한다.
3. 최신 버전, 버전 이력, 파일 내용과 대소문자 구분을 다시 읽어 확인한다.
4. 요구사항 checkpoint를 저장하고 채널 blob을 복원한다.
5. 주요 조회 다섯 건에 `EXPLAIN`을 실행해 의도한 키가 `possible_keys`에 포함되는지 확인한다.
6. checkpoint thread와 앱을 삭제해 외래키 cascade 및 명시적 checkpoint 정리를 확인한다.

검증 시 실제로 저장된 행은 앱 1, 산출물 1, 버전 2, 파일 4, 명령 1, 이벤트 1이었다. 최신 명령,
산출물 버전, 파일 snapshot, checkpoint blob 쿼리는 의도한 인덱스를 실제 선택했다. 이벤트 표는
검증 행이 한 건뿐이라 옵티마이저가 같은 `app_id` 선두의 command FK 인덱스를 선택했지만,
SSE용 `(app_id, event_id)`도 후보 키에 포함됐다. 작은 표의 선택 결과만으로 인덱스를 실패로
판정하지 않고, 운영 데이터가 쌓이면 `EXPLAIN ANALYZE`의 실제 rows와 실행 시간을 다시 측정한다.

검증 종료 후 `schema_migrations`의 적용 revision 한 행을 제외한 14개 업무·checkpoint 테이블의
행 수가 모두 0임을 확인했다. 재현 명령은 프로젝트 루트의 `verify_db.py`이며 접속 환경 변수를
설정한 뒤 `python -X utf8 verify_db.py`로 실행한다.
