# LangSmith observability

EasyDep는 LangSmith의 기본 제공 관측 항목만 사용한다. 별도 feedback score,
성공률·폴백률 같은 EasyDep 전용 metric, 또는 임의 비용 추정은 전송하지 않는다.

| 용어 | EasyDep에서의 의미 |
|---|---|
| Trace/run | 실행 하나 또는 그 하위 단계 하나 |
| Thread | 최초 요구사항 입력으로 생성된 앱 하나의 전체 실행 이력 |
| Metadata | `app_id`, `command_id`, `stage`, `run_id`, `agent`, `operation`처럼 trace를 필터링하는 식별 정보 |
| LangSmith 기본 metrics | trace 수, latency, error rate, LLM 호출 수, input/output/total tokens, cost |

`RunStats`는 Requirements 모듈의 로컬 실행 통계다. 그 안의 토큰·시간 수치는
내부 로그와 실험 출력에 계속 쓰지만, LangSmith에는 같은 수치를 별도 custom metric으로
다시 보내지 않는다.

Workspace에서 최초 요구사항을 입력하면 앱 하나가 만들어진다. 이 앱의 `app_id`를
LangSmith의 특별 metadata인 `thread_id`로도 보내므로 Requirements·Design·Implementation·
Testing의 여러 명령 trace가 한 Thread에 모인다. `command_id`는 사용자 피드백, 승인,
재시도처럼 따로 실행된 명령을 구분하고 `stage`는 네 에이전트 단계를 구분한다.

LangSmith의 Threads 탭에서는 `thread_id = app_id`인 전체 이력을 볼 수 있다. Traces나
Dashboard에서는 `metadata.app_id`로 특정 앱을 필터하고 `metadata.stage`로 단계별 latency,
error, token, cost를 비교할 수 있다. 이 기능을 쓰려면 `LANGSMITH_HIDE_METADATA`를 켜면
안 된다. Workspace를 거치지 않고 `app_id` 없이 에이전트를 직접 호출한 실행은 앱 Thread에
속하지 않는다. [LangSmith Threads 문서](https://docs.langchain.com/langsmith/threads)

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
Thread: app_id (최초 요구사항 입력 1건)
  ├─ easydep.workspace.requirements (command_id)
  │    └─ requirements graph / LLM calls
  ├─ easydep.workspace.design (command_id)
  │    └─ design graph / LLM calls
  ├─ easydep.workspace.implementation (command_id)
  │    └─ implementation workflow / OpenHands tasks
  └─ easydep.workspace.testing (command_id)
       └─ unit tests / verification
```

사용자 피드백이나 승인을 기다리는 동안 root trace를 장시간 열어 두지는 않는다. 각 Workspace
명령은 독립 trace이고 같은 `thread_id`로 연결된다. 명령 안에서 실행되는 스레드 풀 작업에는
현재 trace context를 복사해 가능한 경우 해당 명령의 자식 run으로 유지한다. 서버 재시작 뒤
checkpoint에서 복구된 작업처럼 부모 명령 context가 더 이상 없는 경우에도 `app_id`가 있는
run은 같은 Thread에서 조회할 수 있다.

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

Dashboard의 X축은 계속 날짜·시간이다. 요구사항 입력 건별 상세 과정은 Threads 탭에서 보고,
Dashboard에서는 `app_id` 또는 `stage` metadata로 필터하거나 Group by한다. 각 agent trace의
토큰·시간은 해당 trace와 하위 LLM trace에서 확인한다. `command_id`는 한 번의 피드백·승인·
재시도 실행을, Implementation·Testing의 `run_id`는 해당 작업 내부 실행을 좁힐 때 사용한다.
Thread 단위 token과 cost가 빠지지 않도록 EasyDep의 tracing context는 `thread_id`를 LangChain·
LangGraph가 자동 생성하는 자식 run에도 전파한다.

## 운영 주의점

- `LANGSMITH_TRACING=true`여도 API key가 없으면 전송하지 않는다.
- 정확한 cost에는 LangSmith workspace의 모델 가격 설정이 필요하다.
- 입력·출력을 확인해야 할 때만 데이터 정책 승인 후 hide 설정을 끈다.
- 로컬 `RunStats`, 로그, artifact는 LangSmith 전송 실패와 무관하게 유지된다.
