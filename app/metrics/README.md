# Metrics bounded context

`app.metrics`는 단계나 도메인 산출물을 만들지 않고, EasyDep 실행의 선택적 관찰과
장기 LLM 요청의 endpoint 상태 확인만 소유한다.

## 입력

- `llm_stall_probe.start_stall_probe`는 관찰할 operation 이름과 전역 probe 설정·환경
  변수를 사용한다.
- `langsmith.trace_scope`와 `trace_metadata`는 trace 이름, 비민감 metadata 및
  provider가 보고한 token 수를 받는다.

## 출력

- stall probe는 호출자가 완료를 알릴 `threading.Event`와 시작·종료 JSON event를 낸다.
- tracing 경계는 선택적 `TraceRun`을 제공하며, 비활성 또는 초기화 실패 시 같은
  context-manager 계약의 no-op 결과를 낸다.

## 부수효과

설정이 활성화된 경우에만 별도 daemon thread와 짧은 LLM probe 요청 또는 LangSmith trace
전송을 수행한다. probe는 retry 0과 독립 timeout을 사용하며, 관찰 실패를 본 작업으로
전파하지 않는다.

## 금지 의존성

- requirements·design·implementation 단계 서비스와 graph state
- artifact repository, checkpoint 및 workspace
- production prompt, validation·repair·supervisor 정책

metrics는 전역 설정과 공급자 관찰 SDK만 참조하며 단계별 산출물 shape를 소유하지 않는다.

## 실패 조건

- 설정값이 없으면 네트워크나 thread를 만들지 않고 no-op으로 끝난다.
- probe·trace endpoint 오류와 429는 관찰 결과나 warning으로 남기되 주 실행을 실패시키지
  않는다.
- 잘못된 숫자형 probe 환경 변수는 설정 오류로 즉시 드러내며 임의 기본값으로 숨기지 않는다.
