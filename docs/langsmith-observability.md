# LangSmith observability

EasyDep는 LangSmith의 기본 제공 관측 항목만 사용한다. 별도 feedback score,
성공률·폴백률 같은 EasyDep 전용 metric, 또는 임의 비용 추정은 전송하지 않는다.

| 용어 | EasyDep에서의 의미 |
|---|---|
| Trace/run | 실행 하나 또는 그 하위 단계 하나 |
| Metadata | `run_id`, `agent`, `step`, `operation`처럼 trace를 필터링하는 식별 정보 |
| LangSmith 기본 metrics | trace 수, latency, error rate, LLM 호출 수, input/output/total tokens, cost |

`RunStats`는 Requirements 모듈의 로컬 실행 통계다. 그 안의 토큰·시간 수치는
내부 로그와 실험 출력에 계속 쓰지만, LangSmith에는 같은 수치를 별도 custom metric으로
다시 보내지 않는다.

`app_id`가 있는 웹 Requirements·Design·Implementation·Testing 실행은 모두 같은
`app_id` metadata를 보낸다. LangSmith의 trace 목록 또는 Dashboard에서
`metadata.app_id`로 필터하거나 Group by하면 앱별 latency, error, token, cost를 볼 수
있다. 이 기능을 쓰려면 `LANGSMITH_HIDE_METADATA`를 켜면 안 된다.

## 설정

`requirements.txt`에 `langsmith` SDK가 포함되어 있다. 기본값은 비활성화이며 아래처럼
설정하면 된다.

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=ls__replace_with_a_real_key
LANGSMITH_PROJECT=easydep

# 기본값: 원문 요구사항·프롬프트·생성물은 숨긴다.
LANGSMITH_HIDE_INPUTS=true
LANGSMITH_HIDE_OUTPUTS=true
```

키가 여러 workspace에 연결되어 있으면 `LANGSMITH_WORKSPACE_ID`를, EU 또는 self-hosted
환경이면 `LANGSMITH_ENDPOINT`를 추가한다. 키가 없거나 전송에 실패하면 trace는 전송하지
않고 에이전트 작업은 계속된다.

## 통일된 trace 구조

모든 경계는 같은 `trace_scope`를 사용하며, 원문 input/output은 전달하지 않는다.

```text
requirements run
  └─ requirements LLM calls
design graph / design LLM calls
implementation workflow
  └─ OpenHands tasks
testing unit tests / verification
```

각 trace가 LangSmith에서 자동으로 제공하는 것은 실행 수, 소요 시간, 오류 여부다.
Requirements와 Design의 LLM trace는 공급자가 반환한 토큰 수를 표준
`usage_metadata`로 보낸다. 따라서 모델 가격을 workspace에 등록하면 input/output/total
token과 cost도 LangSmith가 자동 집계한다. Design 스트리밍은 NVIDIA NIM의
`stream_options.include_usage`를 사용해 마지막 스트림 이벤트의 사용량을 받는다.

Implementation은 OpenHands `conversation.conversation_stats.get_combined_metrics()`에서
가져온 실제 prompt/completion token을 같은 `usage_metadata`로 전송한다. OpenHands가
집계하는 자체 비용·호출별 latency는 EasyDep 전용 metric으로 복제하지 않고, LangSmith는
표준 token usage와 workspace 모델 가격으로 cost를 계산한다. OpenHands가 사용량을 반환하지
않는 예외 상황에도 토큰·비용을 추정해서 채우지 않는다.

## 대시보드

첫 trace가 수집되면 `LANGSMITH_PROJECT` 프로젝트의 LangSmith Dashboard에서 기본 차트를
확인할 수 있다. 주요 항목은 trace/LLM 호출 수, latency, error rate, token usage, cost다.
[LangSmith Dashboard 문서](https://docs.langchain.com/langsmith/dashboards)

각 agent trace의 토큰·시간은 해당 trace와 하위 LLM trace에서 확인한다. `run_id`를 이미
아는 Implementation·Testing 경계는 그 metadata로 필터링할 수 있다. `core`는 변경하지
않으므로, 서로 다른 agent root trace를 하나의 부모 trace로 강제 연결하거나 한 실행의
cross-agent 토큰 합계를 자동 계산하지는 않는다. 그것이 필요하면 영구적인 상위 실행 계층이
모든 agent에 같은 `run_id`를 넘기도록 별도로 연결해야 한다.

## 운영 주의점

- `LANGSMITH_TRACING=true`여도 API key가 없으면 전송하지 않는다.
- 정확한 cost에는 LangSmith workspace의 모델 가격 설정이 필요하다.
- 입력·출력을 확인해야 할 때만 데이터 정책 승인 후 hide 설정을 끈다.
- 로컬 `RunStats`, 로그, artifact는 LangSmith 전송 실패와 무관하게 유지된다.
