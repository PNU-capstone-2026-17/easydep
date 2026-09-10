# OpenHands 하네스 구현 검증

## 1. 확인 범위

이 기록은 `openhands-agent-harness-reliability-plan.md`에 적은 하네스를 구현한 뒤 수행한
검사 결과를 정리한다. 현재 코드는 작업 환경 사전 검사, 실제 모델 카나리, 짧은 논리 경로,
제한 도구, 오류별 중단 규칙, 진전 지문과 비교 지표 수집기를 포함한다. 독립된 최종 검사와
검사를 통과한 파일만 정식 run으로 옮기는 기존 절차는 바꾸지 않았다.

## 2. 코드에 반영한 항목

- owner 작업은 기본적으로 `file_editor`, `grep`, `run_task_check`, `finish`만 사용한다.
  기존 `terminal` 구성은 설정으로 되돌릴 수 있는 비교 기준선으로 남겼다.
- 각 병렬 작업은 `/work/<작업 키>` 경로를 사용한다. host의 긴 run 경로는 모델 입력에서
  제외하며, 심볼릭 링크를 해석한 실제 대상이 작업별 sandbox와 같은지 검사한다.
- LLM 호출 전에 owner UID·GID, 읽기·쓰기 왕복, Gradle·npm cache, `pipefail`, 편집 범위와
  읽기 전용 경로를 검사한다.
- 모델, provider, endpoint, reasoning 설정, OpenHands 버전과 도구 schema가 같은 경우에만
  카나리 결과를 재사용한다. 실패 이력에는 API key와 모델의 숨겨진 reasoning을 저장하지 않는다.
- HTTP 404·Cloudflare 코드 7003의 `Model not found` 응답은 `MODEL_NOT_FOUND`로 기록한다.
  이 오류는 같은 요청을 다시 보내도 해결되지 않는 구성 오류이므로 지수 대기와 endpoint circuit을
  적용하지 않는다. 실패 캐시를 읽을 때에도 원래 오류 코드를 유지한다.
- 도구 이름, schema, 작업 공간, 담당 범위, 환경 권한, 명령 실패와 무진전 반복을 서로 다른
  코드로 기록한다. 회복 가능한 오류도 같은 종류가 두 번 나오면 대화를 중단한다.
- 결과 JSON에는 source 해시, 변경 파일, 미구현 marker 수, 동일 조회 반복 횟수, 마지막 실패
  지문, 시간과 OpenHands token 통계를 함께 저장한다.

## 3. 자동 검사

하네스, endpoint 복원력, 구현 엔진, LLM 설정과 Linux runner 전송 경계에 관련된 회귀 검사
120개를 Windows 개발 환경에서 실행했다. 119개는 통과했고 심볼릭 링크 탈출 검사 한 건은
Windows가 링크 생성을 허용하지 않아
건너뛰었다. 전체 `pytest` 실행에서는 하네스와 무관한
`test_start_testing_persists_checkpoint_in_the_command` 한 건이 단독 실행에서도 실패했다.
이 검사는 진행 이벤트를 한 건으로 예상하지만 현재 서비스는 두 건을 기록한다. 이번 변경에서는
Workspace Testing 이벤트 코드를 수정하지 않았다. 이 알려진 한 건을 제외한 전체 test suite는
통과했다. 종료 시 torch가 닫힌 logging stream에 통계를 쓰려는 경고가 있었지만 pytest 종료
코드는 0이었다.

과거 실패 사례는 reasoning과 비밀값을 제거한 작은 fixture로 만들었다. fixture는 원본 실행 ID
`run_0b7e5a6306fe`와 이벤트 순서를 보존하며, 없는 도구, 제어 토큰, schema 오류와 외부 경로를
각 오류 코드로 분류하는지 검사한다.

고정 Linux 이미지의 사전 검사도 실제로 실행했다. coordinator는 `root`(UID·GID 0), owner는
`appuser`(UID·GID 1000)로 분리됐다. owner는 `/work/preflight`에서 허용 파일을 읽고 쓸 수
있었으며 job 제어 파일은 읽지 못했다. 실패 pipeline의 종료 코드 전달, Gradle·npm cache,
Python·Java·Node·ripgrep·Gradle·OpenTofu·Trivy와 OpenAPI Generator 산출물 검사도 통과했다.
검사용 임시 디렉터리는 Python 임시 디렉터리 범위에서 만들었고 컨테이너 종료 전에 제거했다.

## 4. 실제 모델 카나리

가장 최근 실제 모델 검사는 2026년 9월 10일에 다음 조건으로 실행했다.

| 항목 | 값 |
|---|---|
| provider | Cloudflare Workers AI OpenAI 호환 endpoint |
| model | `@cf/zai-org/glm-5.3-flash` |
| reasoning | `medium` |
| OpenHands SDK/tools | `1.36.1` / `1.36.1` |
| prompt/workspace 계약 | `easydep-owner-prompt/v3` / `easydep-owner-workspace/v2` |
| canary 계약 | `easydep-openhands-tool-canary/v5` |
| 정상 왕복 요구/최대 sequence | 3 / 5 |
| 요청 retry | 최대 3회, 1→2→4초 base-2 지수 대기, 8초 cap |

세 sequence는 모두 `easydep_canary_read`, `easydep_canary_check`, `finish`를 순서대로
실행했다. 실행 시간은 각각 9,001ms, 2,776ms, 636ms였고 요청 재시도와 sequence 재시도는
없었다. 결과에는 `endpointRetryCount: 0`, `endpointHealth: healthy`가 기록됐다. 이 검사는
도구 호출 형식과 endpoint 왕복만 다루므로 실제 구현 작업의 코드 품질과 비용은 아직 비교하지
않았다. 결과 JSON은
`artifacts/openhands-harness-validation-glm53flash-workers/reports/openhands-harness/`에
저장했다.

같은 날 `zai-org/glm-5.3-flash`처럼 `@cf/` 접두사를 빼고 보낸 검사는 첫 요청에서 HTTP
404·Cloudflare 코드 7003과 `Model not found`를 반환했다. 이 실패로 일반 endpoint 장애와
영구적인 모델 ID 오류가 섞이는 결함을 찾았다. v5 회귀 검사는 이 응답을 `MODEL_NOT_FOUND`와
`misconfigured` 상태로 저장하고, 재시도·만료형 실패 캐시·endpoint circuit을 만들지 않는지
검사한다.

기존 gpt-oss endpoint 복원력 검사는 2026년 9월 9일에 다음 조건으로 실행했다.

| 항목 | 값 |
|---|---|
| provider | Cloudflare Workers AI OpenAI 호환 endpoint |
| model | `openai/gpt-oss-120b` |
| reasoning | `medium` |
| OpenHands SDK/tools | `1.36.1` / `1.36.1` |
| prompt/workspace 계약 | `easydep-owner-prompt/v3` / `easydep-owner-workspace/v2` |
| canary 계약 | `easydep-openhands-tool-canary/v4` |
| 정상 왕복 요구/최대 sequence | 3 / 5 |
| 요청 retry | 최대 3회, 1→2→4초 base-2 지수 대기, 8초 cap |

v4 실행은 세 canary sequence가 모두 읽기, marker 검사, `finish`를 정확한 순서로 완료해
통과했다. 첫 sequence 안에서 provider가 HTTP 400·코드 7003과 출력 파싱 실패를 한 번
반환했지만, 아직 client-side `ActionEvent`가 없었던 동일 LLM 요청을 SDK가 한 번 재시도하여
회복했다. 결과에는 `endpointRetryCount: 1`, `endpointHealth: recovered`가 기록됐다.

바로 전 v3 실행도 네 sequence 중 세 번 성공해 `recovered`로 통과했다. 그 실행에서는 한
sequence가 내부 요청 retry 세 번을 모두 소진했지만, read-only canary용 full-jitter 대기 뒤
새 sequence에서 회복했다. 이전의 엄격한 v2 배치는 2/3, 그 직전 배치는 1/3이었다. 요청에 없는
`json` 도구를 한 번 호출한 뒤 교정한 과거 사례도 남아 있다. 따라서 새 정책이 관찰된 일시적
출력 변환 실패를 흡수한다는 증거는 확보했지만 endpoint 자체를 안정적이라고 판정하지 않는다.

gpt-oss 실행 결과는 로컬 재생성 산출물인
`artifacts/openhands-harness-validation/reports/openhands-harness/`에 저장했다. 카나리 CLI는
`python -X utf8 scripts/run_openhands_canary.py`로 다시 실행할 수 있다.

## 5. GLM 실제 구현 탐색

2026년 9월 10일에 사용자의 외부 전송 승인을 받은 뒤 같은 생성 애플리케이션 사본으로
`@cf/zai-org/glm-5.3-flash` 구현 행동을 비교했다. 제품 테스트 코드는 생성하거나 수정하지
않았으며, 성공한 소형 작업은 기존 `compileJava --build-cache`만 실행했다. 실험 사본과 이벤트는
`.easydep/experiments/glm-5.3-flash-*` 아래에 보존했다.

| 조건 | 결과 | 시간 | 도구 행동 | 누적 사용량 |
|---|---|---:|---:|---:|
| 전체 backend owner, 사실상 SDK 기본 high | 중단, source 변경 없음 | 1,003초 | 152회 | prompt 747,999 / completion 6,983 |
| 단일 domain marker, medium, 추적 context만 제공 | 성공, compile 통과 | 192초 | 첫 편집 26번째, 총 28회 | prompt 168,256 / completion 5,678 / cached 18,944 |
| 같은 marker, medium, 짧은 행동 capsule 제공 | 성공, compile 통과 | 38초 | 첫 편집 5번째, 총 7회 | prompt 15,942 / completion 783 / cached 1,856 |
| UC10 전체, medium, 제한 도구 | 편집 전 중단 | 약 150초 | 읽기·검색 32회 | prompt 323,857 / completion 7,963 / cached 156,800 |
| 같은 UC10 checkpoint + `SOURCE_EVIDENCE_READY` | 편집 전 중단 | 약 120초 추가 | 2회 추가 | 중단 시도이므로 전후 snapshot만 보존 |
| UC10 전체, medium, 격리 Linux terminal | 편집 전 중단 | 약 120초 | terminal 22회 | 중단 시도이므로 최종 snapshot 없음 |
| UC10 application 단계, medium | 편집 전 중단 | 약 90초 | 읽기·검색 22회 | 중단 시도이므로 최종 snapshot 없음 |
| 같은 application 단계, low | iteration 상한, source 변경 없음 | 90초 | 읽기·검색 18회 | prompt 101,616 / completion 3,555 / cached 19,456 |
| 자동 marker capsule v1, UC10 domain | 실패, marker 잔존 | 95초 | file 10 / grep 10, 편집 0 | prompt 267,210 / completion 3,116 / cached 60,032 |
| 확장 step·구현 기본값을 보완한 자동 capsule, UC10 domain | 성공, compile 통과 | 61초 | file 6 / grep 6 / check 1 / finish 1 | prompt 68,846 / completion 2,370 / cached 27,264 |
| 다음 순차 marker `createTerm` | 성공, compile 통과 | 204초 | 첫 편집 전 탐색 24회, 총 29회 | prompt 221,908 / completion 12,271 / cached 30,784 |
| 8K 순차 marker `updateRegistrationPeriod` | 성공, compile 통과 | 125초 | file 8 / grep 3 / check 1 / finish 1 | prompt 82,126 / completion 4,740 / cached 11,328 |
| 8K 순차 marker `POST /admin/terms` adapter | 성공, compile 통과 | 183초 | file 13 / grep 7 / check 1 / finish 1 | prompt 220,687 / completion 10,655 / cached 48,640 |
| binding·outcome·계약 경로를 압축한 같은 adapter | 성공, compile 통과 | 108초 | file 7 / grep 2 / check 1 / finish 1 | prompt 68,695 / completion 3,633 / cached 1,408 |

행동 capsule에는 marker가 뜻하는 검증, 상태 변경, 유지할 공개 선언을 짧게 적었다. 소스 위치는
읽기·편집 제한이 아니라 시작점으로 제공했고, main source의 주변 파일은 계속 탐색할 수 있었다.
두 소형 실행은 같은 메서드에 null 검사, 날짜 순서 검사와 필드 갱신을 구현했으므로 capsule이
단순히 더 빠른 대신 다른 동작을 만든 것은 아니었다.

자동 capsule v1 실패는 모델 비교가 아니라 생성기 결함을 드러냈다. method context가
`main_scenario`만 투영하고 `extensions`의 `handling_steps`를 누락했으며, marker 대화에 모든 원본
설계 파일을 열어 둔 결과 모델이 이미 제공된 근거를 다시 찾았다. 확장 step 투영, 허용된 구현
기본값(필수값·날짜 범위·동일 이름 상태 갱신), 대용량 원본 설계 제외를 적용한 뒤 같은 자동
domain 작업이 편집과 compile을 완료했다. 이어진 `createTerm`은 생성된 저장소를 constructor로
주입하고 UUID 식별자, JPA 저장, 도메인 매핑과 두 날짜 범위 검증을 구현했다. 보조 변경은
`AcademicTerm` 생성자와 getter에 한정됐다.

위 기본값은 실험 당시 성공 동작을 설명하는 기록일 뿐 현재 생성 규칙이 아니다. 후속 일반화
검토에서 메서드·파라미터·반환 타입 이름만으로 CRUD, 날짜 역할, Repository/Entity, UUID 전략을
정한 부분은 UC10 과적합으로 판정해 제거했다. 같은 타입이나 유사 이름으로 HTTP 인자를 고르는
fallback도 없앴으며, 승인된 Boundary→Control `sourceRef`만 API 입력 연결로 투영한다.

두 자동 성공 실행도 제품 테스트 코드를 만들거나 수정하지 않았다. 첫 자동 실패 뒤 기존
finish-recovery가 성공한 검사 증거 없이 종료 호출을 요구하는 문제도 확인했다. 이제 현재 source와
일치하는 `run_task_check` 성공 기록이 있을 때만 종료 전용 복구를 실행한다.

adapter 병목은 출력 상한이 아니라 계약 탐색이었다. 최초 capsule은 API 전체 JSON을 넣고 직접
계약 경로를 두 개만 제시해 모델이 생성 API, DTO, service, 예외 관례와 원본 설계를 다시 찾았다.
endpoint·Java service binding·대표 성공 상태·validation 상태·구현할 수 없는 성공 분기를 명시하고
정확한 generated contract 경로를 제공하자, 같은 8K adapter에서 첫 편집 전 읽기·검색은 19회에서
8회로, prompt token은 69%, 시간은 41% 줄었다. 실행 뒤 남은 grep 두 건은 반환 domain 위치
확인이었으므로 당시 실험 생성기에는 그 경로와 Java getter 표현을 직접 제공하는 후보도 반영했다.
이 마지막 정적 보완의 성능 수치는 별도로 재지 않았다.

이후 생성기는 성공 outcome의 이름이나 Control 메서드 접두사로 대표 상태를 고르지 않도록
수정했다. 2xx가 하나일 때만 해당 HTTP status를 사용하고, 여러 개면 명시적 selector가 없는 계약
공백으로 처리한다. 오류 label도 Java 예외에 연결하지 않는다. 반환 domain 경로와 getter 표현의
이름 기반 추정 역시 제거했으므로, 바로 위 성능 수치는 현재 규칙의 재측정값이 아니다.

Cloudflare 공개 단가를 적용하면 소형 context-only 실행은 약 USD 0.026, capsule 실행은 약
USD 0.0026이다. cached input token이 prompt token에 포함된다는 전제로 regular/cached input을
나누어 계산한 추정치이며 실제 청구 명세는 아니다. UC10 제한 도구 중단 실행은 같은 방식으로
약 USD 0.034를 이미 사용했다.

## 6. 해석과 다음 구조

canary 통과는 GLM이 OpenHands 도구 형식을 지킨다는 뜻이지 큰 구현 owner로 효율적으로
동작한다는 뜻은 아니다. 이번 실행에서는 없는 도구 호출이나 workspace 탈출보다, 충분한 근거를
읽은 뒤에도 source를 바꾸지 않고 추가 관찰을 만드는 문제가 지배적이었다. terminal은 한 명령에
여러 파일을 읽게 해 왕복 효율은 높였지만 편집 전환을 만들지 못했다. reasoning `low`도 같은
문제를 해결하지 못했으므로 확인된 기본 후보는 `temperature=0.2`, `reasoning_effort=medium`,
8K 출력 상한이다.

marker planner의 앞선 자동 성공 두 건은 과거 job에 저장된 16K 상한을 사용했다. 이후 완료된
domain·`createTerm` source를 그대로 재사용하고, 남은 `updateRegistrationPeriod`와 HTTP adapter를
8K·medium으로 순차 실행했다. 두 작업 모두 marker를 제거하고 compile을 통과했으며 endpoint
retry나 finish-recovery를 쓰지 않았다. 여기서 8K는 LLM 한 응답의 출력 상한이므로 여러 도구
왕복의 누적 completion token은 adapter 실행에서 8K를 넘을 수 있다.

다음 구현 구조는 유스케이스를 coordinator의 상위 checkpoint로 유지하되, 한 OpenHands 대화가
전체 유스케이스를 소유하게 하지 않는다. 설계에서 이미 만든 operation marker와 짧은 행동
capsule을 순서대로 공급하고, 성공한 source를 다음 marker가 이어받는다. 주변 source 읽기와
필요한 보조 변경은 허용하므로 파일 목록을 정답처럼 강제하지 않는다. 구현 병렬화는 제외한다.

초기 단계는 `DOMAIN_READY → APPLICATION_READY → ADAPTER_READY → COMPILE_READY`와 같이
의존 순서로 실행한다. 같은 source에서 읽기만 계속되면 기존 대화에 자연어 경고를 반복하지
않고 그 marker 실행을 중단한다. 사용자가 재개하거나 capsule 생성 규칙을 보완할 때에는 완료된
marker와 source를 재사용한다. GLM 조건 탐색 중에는 제품 테스트 코드를 조건마다 만들지 않고,
구조와 파라미터가 확정된 뒤 planner·하네스 회귀 검사를 한 번에 추가한다. 문자열은 exact ID
해결에만 쓰며 접두사·접미사·유사도는 역할 근거로 쓰지 않는다.

현재 코드는 `IMPLEMENTATION_BACKEND_TASK_STRATEGY=marker`일 때만 이 실험 경로를 사용한다. 표본
애플리케이션에서는 38개 작업(`DOMAIN_READY` 11, `APPLICATION_READY` 21, `ADAPTER_READY` 6)을
만들었고 모든 작업을 단일 선행 의존성으로 연결했다. 최초 계획은 manifest에 고정하므로 완료된
marker가 source에서 사라져도 남은 task ID를 다시 번호 매기지 않는다. UC10 한 흐름의 네 marker가
모두 성공한 뒤 확장 step 투영, 순차 compile-only 계획, marker 제거와 검증 후 source 불변 조건을
GLM 8K runtime profile과 함께 `tests/test_backend_marker_strategy.py`에 회귀 테스트로 고정했다.
이름과 무관한 단일 204 성공 상태 및 오류 label의 비추론 사례도 추가했다. 기본값은 대표
유스케이스를 더 확인할 때까지 아직 `owner`다.
