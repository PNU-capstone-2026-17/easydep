# OpenHands endpoint 복원력 계획

- 작성일: 2026-09-09
- 상태: 코드 반영 및 실제 endpoint canary 통과, backend 비교 실행 승인 대기
- 대상: 구현 owner와 사전 모델·도구 canary의 LLM 요청
- 관련 문서: [에이전트 하네스 안정화 계획](openhands-agent-harness-reliability-plan.md),
  [하네스 구현 검증](openhands-agent-harness-validation.md)

## 1. 목적

일시적으로 불안정한 endpoint를 사용할 수 있게 하되, 없는 도구 호출·도구 schema 불일치·작업
공간 위반 같은 결정적 에이전트 오류를 재시도로 숨기지 않는다. 재시도는 실패한 LLM 요청에만
적용하고 owner 작업, checkpoint, 이미 완료된 도구 호출을 처음부터 반복하지 않는다.

최종 canary에서 Cloudflare Workers AI OpenAI 호환 endpoint는 세 번 중 두 번 정상 왕복했고,
한 번은 HTTP 400·코드 7003과 `Parsing failed. The model generated output that could not be
parsed.`를 반환했다. 이 결과는 완전한 계약 비호환보다 확률적인 응답 변환 실패에 가깝지만,
긴 에이전트 실행에 그대로 허용할 정도로 안정적이라는 뜻은 아니다.

## 2. 실패 분류

| 분류 | 대표 오류 | 재시도 정책 |
|---|---|---|
| 전송·서비스 일시 오류 | 429, 연결 실패, 5xx, timeout, 불완전 stream | OpenHands SDK의 동일 요청 지수 백오프 |
| provider 출력 파싱 실패 | 위의 좁은 7003 문구, `ActionEvent`를 만들지 못한 응답 | `LLMNoResponseError`로 바꿔 동일 SDK 경로에서 재시도 |
| 결정적 도구 계약 오류 | 제어 토큰 누출, 없는 도구 반복, schema 오류 반복 | 백오프하지 않고 하네스 규칙대로 교정 또는 중단 |
| 작업 공간·권한 오류 | 외부 경로, 담당 범위 밖 쓰기, 할당 경로 권한 실패 | 모델 재시도 금지 또는 한 번의 경로 교정만 허용 |
| 알 수 없는 HTTP 400 | 잘못된 입력, 지원하지 않는 옵션 등 | 자동 재시도 금지 |

출력 파싱 재시도 판정은 HTTP 코드만 사용하지 않는다. 오류 문자열에 `parsing failed`,
`model generated output`, `could not be parsed`가 모두 있는 경우만 대상으로 한다. provider가
응답을 반환하지 못했으므로 해당 물리 요청의 도구는 client에서 dispatch되지 않았다. 이미
반환된 `ActionEvent`나 실행된 쓰기 도구를 replay하는 기능은 이번 범위에 포함하지 않는다.

## 3. 요청 재시도 정책

OpenHands SDK 1.36.1의 내장 retry decorator를 한 번만 사용한다. 바깥에 `conversation.run()`이나
owner 작업 전체를 반복하는 루프를 추가하지 않는다.

| 설정 | 기본값 | 의미 |
|---|---:|---|
| `IMPLEMENTATION_OPENHANDS_REQUEST_ATTEMPTS` | 3 | 각 LLM 요청의 최초 호출을 포함한 최대 물리 시도 수 |
| `IMPLEMENTATION_OPENHANDS_RETRY_MIN_WAIT_SECONDS` | 1 | 첫 재시도 최소 대기 |
| `IMPLEMENTATION_OPENHANDS_RETRY_MAX_WAIT_SECONDS` | 8 | 요청 사이 최대 대기 |
| `IMPLEMENTATION_OPENHANDS_RETRY_MULTIPLIER` | 1.0 | base-2 지수 대기의 시작 계수 |

SDK가 사용하는 429·연결·5xx·timeout retry 집합에 `LLMNoResponseError`가 이미 포함된다. EasyDep은
정확한 provider 출력 파싱 실패만 이 타입으로 변환한다. 재시도 소진 뒤에는
`PROVIDER_OUTPUT_PARSE_TRANSIENT`를 종료 사유로 남기며, 일반 bad request는 원래 오류로 유지한다.

Cloudflare AI Gateway의 자동 retry를 별도로 켜면 client retry와 곱해질 수 있다. 하나의 계층을
주 retry 소유자로 정하고, Gateway retry를 함께 사용하는 배포에서는 총 물리 시도 수와 timeout을
별도 평가한다.

## 4. canary quorum과 circuit breaker

canary는 무조건 첫 세 번이 모두 성공해야 한다는 조건 대신, 제한된 시도 안에 정상 왕복 세 번을
확보한다.

| 설정 | 기본값 | 의미 |
|---|---:|---|
| `IMPLEMENTATION_OPENHANDS_CANARY_REPETITIONS` | 3 | 필요한 정상 도구 왕복 수 |
| `IMPLEMENTATION_OPENHANDS_CANARY_MAX_ATTEMPTS` | 5 | 일시적 실패를 포함한 최대 canary sequence 수 |
| `IMPLEMENTATION_OPENHANDS_CANARY_TRANSIENT_TTL_SECONDS` | 600 | 실패 뒤 endpoint circuit 유지 시간 |

- 정상 왕복 세 번을 확보하면 `healthy`, 일시적 실패 뒤 확보하면 `recovered`로 기록한다.
- 결정적 계약 오류가 한 번이라도 발생하면 즉시 `incompatible`로 중단하고 계약별로 캐시한다.
- 최대 시도 안에 quorum을 확보하지 못하면 `ENDPOINT_DEGRADED`, `open`으로 기록한다.
- `open` 결과는 기본 10분만 재사용한다. TTL이 지나면 같은 계약으로 canary를 다시 실행한다.
- 결과에는 canary sequence 시도 수, 성공 수, 일시적 실패 수, sequence 사이 `backoffMs`와
  각 sequence 안에서 SDK가 수행한 LLM 요청 retry를 저장한다.
- 실제 owner 요청이 일시 오류 retry를 모두 소진해 실패해도 같은 endpoint circuit을 연다.
  이전의 성공 canary는 TTL 동안 재사용하지 않으며, TTL 뒤 새 canary를 통과해야 재개한다.

canary 도구는 marker 읽기와 비교뿐이며 구현 source를 수정하지 않는다. 따라서 실패한 canary
대화 전체를 새 임시 workspace에서 반복해도 구현 부작용이 없다.

## 5. 하위 runner 설정 전달

루트 `.env`에서 읽은 정책을 `llm_subprocess_environment()`가 고정 Linux runner에 명시적으로
전달한다. 저장소의 `.env`를 컨테이너에 mount하지 않으며 API key 외의 새로운 비밀값도 만들지
않는다. retry와 canary 값은 실행 결과의 policy와 함께 남겨 다른 조건의 캐시를 재사용하지 않는다.

## 6. 검증 결과와 적용 기준

LLM 없는 회귀 검사에서 다음을 고정한다.

- 정확한 7003 파싱 문구만 일시적 출력 실패로 분류한다.
- 일반 tool validation 400은 결정적 계약 오류로 유지한다.
- LLM 객체가 3회·1초·8초·2배 정책을 사용한다.
- canary가 한 번의 일시적 실패 뒤 세 번의 성공을 확보하면 4회 만에 통과한다.
- 일시적 실패가 계속되면 TTL circuit이 같은 run의 즉시 재호출을 차단한다.
- 결정적 canary 실패는 추가 물리 시도 없이 중단한다.
- retry와 canary 설정이 host에서 고정 Linux runner까지 전달된다.

실제 endpoint 검증은 동일 model·reasoning·도구 schema로 수행한다. `recovered` 통과 뒤 backend
후보 한 건을 실행하여 기준선과 최종 Testing 통과, 전체 시간, 총 token, provider retry 횟수,
잘못된 도구 호출과 경로 위반을 비교한다. canary 통과만으로 구현 품질이나 비용 개선을 선언하지
않는다.

2026년 9월 9일 v3 canary의 첫 실제 실행은 네 번의 canary sequence 중 세 번 성공해
`recovered`로 통과했다. 세 번째 sequence는 출력 파싱 실패에 대한 LLM 요청 retry를 모두
소진했고, 짧은 full-jitter 대기 뒤 새 read-only sequence에서 회복했다.

기본 retry 시작 계수를 1.0으로 바로잡고 내부 retry 계측을 추가한 v4 실행은 세 sequence를
모두 완료했다. 첫 sequence에서 출력 파싱 실패 한 번을 동일 LLM 요청 retry로 회복했으므로
최종 상태는 `healthy`가 아닌 `recovered`, `endpointRetryCount`는 1이다. 이는 복원력 정책이
실제로 작동했다는 증거지만 endpoint 자체가 안정적이라는 증거는 아니다.

## 7. 공식 근거

- [OpenHands SDK 오류 처리](https://docs.openhands.dev/sdk/guides/llm-error-handling): provider 오류와
  action·function 오류의 typed exception 구분
- [OpenHands LLM 설정](https://docs.openhands.dev/openhands/usage/llms/llms): retry 횟수와 최소·최대
  대기, 지수 배수 설정
- [Cloudflare AI Gateway request handling](https://developers.cloudflare.com/ai-gateway/configuration/request-handling/):
  최대 다섯 번의 요청 retry와 exponential backoff
- [Cloudflare AI Gateway fallback](https://developers.cloudflare.com/ai-gateway/configuration/fallbacks/):
  요청 실패나 timeout 뒤 다른 model·provider로 전환하는 선택지

공개 Cloudflare 문서에서 코드 7003 자체의 안정된 의미나 재시도 보장은 확인하지 못했다. 따라서
이 계획은 코드 번호 전체가 아니라 관찰한 좁은 메시지와 client-side tool 미실행 경계를 사용한다.
