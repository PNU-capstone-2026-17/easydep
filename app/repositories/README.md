# 저장소 API

`app.repositories`는 업무 코드와 MySQL 사이를 연결한다. 서비스는 SQLAlchemy query를
직접 만들지 않고 이곳의 함수로 앱과 산출물을 읽고 쓴다.

## `artifact_repository.py`

이 파일은 다음 개념을 다룬다.

- 애플리케이션 생성과 조회
- 요구사항·설계·구현·테스트 산출물 저장
- 산출물 버전 증가와 최신 버전 조회
- 단계별 완료 상태와 사용자 피드백 기록
- 구현 작업에 필요한 여러 설계 산출물의 일관된 snapshot 조회

## 읽기와 쓰기 규칙

1. 저장 함수는 필요한 transaction을 내부에서 끝낸다.
2. 저장 JSON의 필드 이름은 HTTP·UI 계약과 연결되므로 임의로 바꾸지 않는다.
3. 여러 산출물이 같은 실행에 속해야 하면 각각 최신값을 따로 읽지 말고 동일한 버전·앱
   문맥을 확인한다.
4. “없음”과 “빈 산출물”을 구분한다. 아직 생성하지 않은 값은 `None`, 의도적으로 빈 결과는
   해당 schema에서 정한 빈 구조를 사용한다.

## 계약

- **입력:** 검증된 `app_id`, artifact type, JSON으로 직렬화할 수 있는 payload와 feedback.
- **출력:** 저장소 바깥에서 사용할 dict snapshot 또는 명시적인 `None`.
- **실행하면서 바꾸는 것:** `app.db.session`을 통해 MySQL transaction을 수행한다.
- **이 디렉터리에서 사용하지 않는 것:** LLM, 실행 graph와 FastAPI 응답 객체. 다만 저장된
  구조화 모델을 읽을 때 PlantUML 또는 OpenAPI를 다시 만드는 결정적 변환 함수는 사용한다.
  이 변환 과정에서는 LLM을 호출하지 않으며, 같은 모델에는 항상 같은 결과를 만든다.
- **주요 실패 원인:** 존재하지 않는 앱, 중복 식별자, 알 수 없는 artifact type, 데이터베이스 오류.

HTTP 상태 코드로 바꾸는 책임은 router에 있다. 저장소는 `HTTPException`을 발생시키지 않는다.
