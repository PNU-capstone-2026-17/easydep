# Requirements knowledge

`app.requirements.knowledge`는 요구사항 산출물을 판정하는 정적 규칙과 그 근거를
소유한다. 파이프라인 단계 자체나 클라우드·설계·구현 결과를 소유하지 않는다.

## 계약

- **입력:** 요구사항/Use Case/명세 사전, 검사할 stage와 severity, 규칙의 evidence
  식별자. 인용 검증기는 선택적으로 로컬 원문 PDF와 depkb claim 좌표를 읽는다.
- **출력:** `Rule`·`Concern` 레코드, 규칙 프롬프트 블록, 그리고 결정론적 `Finding`
  및 `ValidationReport`. 검증이 성공하면 findings는 빈 목록이다.
- **부수효과:** 일반 규칙 조회·검출은 순수하다. citation/concern 검증 CLI는
  로컬 증거를 읽고 결과를 표준 출력으로 보고할 수 있지만 제품 산출물이나 실행
  상태를 쓰지 않는다.
- **금지 의존성:** `app.requirements`의 agent/state/prompt 모듈을 import하지
  않는다. 설계·구현 서비스와 LLM 호출에도 의존하지 않는다. 규칙의 문장·근거를
  소비자 모듈에 복사하지 말고 이 패키지의 단일 레코드를 사용한다.
- **실패 조건:** 알 수 없는 rule/evidence ID, 요구사항에서 찾을 수 없는 참조,
  규칙 계약을 위반한 입력, 누락되거나 검증할 수 없는 citation/claim이 있으면
  이름 있는 finding 또는 검증 오류로 보고한다. 책 원문이 없다는 사실을 근거가
  확인된 것으로 바꾸지 않는다.
