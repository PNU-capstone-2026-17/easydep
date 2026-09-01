# 데이터베이스 경계

`app.db`는 MySQL 연결과 테이블 구조를 소유한다. 요구사항 분석이나 설계 생성 같은 업무
규칙은 이 디렉터리에 두지 않는다. 이 계층의 목적은 “무엇을 저장할지”가 아니라 “이미
정해진 데이터를 어떻게 안전하게 저장하고 다시 읽을지”를 책임지는 것이다.

## 파일 지도

| 파일 | 역할 |
|---|---|
| `session.py` | 환경 설정으로 SQLAlchemy Engine과 transaction 단위인 Session을 만든다. |
| `models.py` | 앱, 산출물, 버전, 워크스페이스 명령·이벤트의 ORM 테이블을 선언한다. |
| `checkpointer.py` | LangGraph checkpoint를 MySQL에 저장하여 중단된 작업을 이어갈 수 있게 한다. |
| `migrations.py` | 기존 MySQL에 제약·인덱스 증분 변경을 적용하고 revision을 기록한다. |
| `schema.sql` | 운영자가 현재 데이터베이스 schema를 빠르게 확인할 수 있는 SQL 기준본이다. |

테이블 관계와 각 선택의 이유는 [MySQL 구조 문서](../../docs/mysql-architecture.md)에 정리한다.

## 연결 흐름

```text
.env의 DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME
  → database_settings()
  → SQLAlchemy Engine
  → 한 transaction의 범위를 관리하는 session_scope()
  → repository가 조회·저장
```

개발 환경의 `DB_PORT` 기본값은 `33060`이다. 백엔드는 Windows 호스트에서 이 포트로
접속하고, Docker가 요청을 MySQL 컨테이너 내부의 표준 포트 `3306`으로 전달한다. 따라서
백엔드 설정에 `3306`을 직접 적지 않는다. `run-easydep.ps1 -DatabasePort`로 공개 포트를
바꾸면 스크립트가 백엔드 환경 변수와 컨테이너의 포트 연결을 함께 바꾼다.

`session_scope()`는 정상 종료 시 commit하고 예외가 나면 rollback한다. 호출자는 Session을
직접 닫거나 중간 commit을 섞지 않는다. 긴 LLM 호출 동안 연결이 끊길 수 있으므로 Engine은
사용 전에 연결 상태를 확인하고 오래된 연결을 교체하도록 설정되어 있다.

`init_db()`는 없는 표를 만든 뒤 아직 적용하지 않은 `schema_migrations` revision을 실행한다.
개발 schema를 호환되지 않게 크게 바꿀 때에는 보존 대상이 없는지 확인하고 DB를 비운 뒤 다시
생성한다. 운영 데이터가 생긴 뒤에는 별도 backup·rollback 절차 없이 destructive 변경을 하지 않는다.

## 계약

- **입력:** 환경 변수, ORM 객체, checkpoint가 속한 thread/run 식별자.
- **출력:** SQLAlchemy Session, 조회된 ORM 객체, 저장된 checkpoint.
- **실행하면서 바꾸는 것:** MySQL 연결과 transaction을 만들고 행을 추가·수정한다.
- **이 디렉터리에서 사용하지 않는 것:** 요구사항·설계·구현 서비스와 FastAPI router.
- **주요 실패 원인:** 접속 정보 오류, schema 불일치, 제약조건 위반, JSON으로 직렬화할 수 없는 checkpoint.

## 초보자가 자주 혼동하는 점

- `app_id`는 사용자가 보는 애플리케이션 식별자이고 `command_id`는 한 번의 UI 명령,
  `run_id`나 `job_id`는 실제 단계 실행을 구분한다. 서로 대신 사용하면 재개 범위가 어긋난다.
- 산출물의 최신값만 덮어쓰지 않는다. 버전 이력은 피드백과 재실행의 근거이므로 보존한다.
- JSON 열에 넣기 전에 Pydantic 모델을 JSON 모드로 dump한다. Python 객체를 그대로 넣으면
  날짜나 enum이 데이터베이스 driver에 따라 다르게 저장될 수 있다.

## 검증

```powershell
python -X utf8 -m pytest -q tests/test_db_schema.py tests/test_session_store.py
python -X utf8 verify_db.py
```

두 번째 명령은 실제 MySQL에 임시 앱과 checkpoint를 저장해 읽기, 파일 버전, 경로 대소문자,
주요 쿼리의 인덱스 후보, cascade 삭제를 검증한 뒤 생성한 행을 모두 정리한다.
