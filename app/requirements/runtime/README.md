# 요구사항 runtime 경계

`app.requirements.runtime`은 structured LLM adapter와 실행 단위 telemetry를
소유한다. 단계 프롬프트, graph 상태, 피드백·repair 범위는 소유하지
않는다.

## 입력

- `structured_llm.invoke_structured`는 Pydantic schema, 메시지 목록, 선택적
  seed override를 받는다.
- client 생성은 전역 설정의 model·endpoint·temperature·seed·reasoning·token
  상한을 사용하고 timeout 90초, `max_retries=2`를 보존한다.
- `telemetry.run_scope`, `progress_scope`, `record_llm_call`은 실행 이름과
  관찰 callback을 받는다. worker thread에는 `bind_context`로 호출 단위
  ContextVar를 전파한다.

## 출력

- native structured 응답이 유효하면 검증된 Pydantic model을 반환한다.
- native parsed 값이 없거나 요청이 실패하면 JSON mode로 한 번 fallback하고
  같은 schema로 검증한 model을 반환한다.
- telemetry는 기존 summary/event key, logical LLM 호출 수, 실패, token,
  backend fingerprint, fallback, degradation을 유지한다. fallback의 두 물리
  요청은 logical 호출 1건으로 집계한다.

## 부수효과

LLM endpoint 네트워크 호출, process-local client cache, 로그·progress event,
ContextVar 실행 집계가 있다. telemetry는 디스크·checkpoint·세션에 직접
저장하지 않는다.

## 사용하면 안 되는 import

- requirements agent 단계 구현·prompt·validator·repair loop
- stage registry·graph·runner·supervisor·feedback cascade
- HTTP API·session store·artifact repository
- design·implementation 내부 서비스

runtime은 전역 설정과 공용 metrics primitive만 참조할 수 있다.

## 실패 조건

- native structured 실패는 즉시 전체 실패가 아니며 fallback 사유로
  계측한다.
- JSON fallback 호출·JSON 추출·schema 검증까지 실패하면 예외를
  삼키지 않고 호출자에게 전파하며 telemetry failure로 남긴다.
- progress sink 실패는 본 작업을 실패시키지 않는다.
