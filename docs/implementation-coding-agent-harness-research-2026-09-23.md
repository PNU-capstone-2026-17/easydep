# GLM-5.3-Flash 코딩 에이전트 하네스 조사

조사 기준일: 2026-09-23

이 문서는 현재 EasyDep의 GLM-5.3-Flash 프로필과 OpenHands 실행 기록을 공식 모델 문서, provider API 문서, 그리고 별도 코딩 에이전트 사례와 대조한다. 제공자별 매개변수 차이를 모델 능력 차이로 해석하지 않는다. 여기서 말하는 우선순위는 조사 자료와 기존 실측으로부터 얻은 구현 제안이며, 새 실행으로 검증한 결과가 아니다.

## 출처 수준과 설정 차이

| 수준 | 출처가 직접 확인하는 내용 | EasyDep에 적용할 때의 한계 |
|---|---|---|
| 모델 전용 권장 | [Z.ai의 GLM-5.3-Flash 안내](https://docs.z.ai/guides/vlm/glm-5.3-flash)는 모델 코드 `glm-5.3-flash`, 입력 context 1M, 최대 출력 128K, `temperature=1`, `top_p=0.95`, `reasoning_effort=max`를 권장한다. `thinking.type`은 `enabled`만 허용하고, `thinking.clear_thinking=false`를 권장한다. 스트리밍에서는 `stream=true`와 `tool_stream=true`를 함께 권한다. | Z.ai API/모델 문서의 권장이다. Cloudflare가 이 모든 필드를 같은 의미로 지원하거나 전달한다고 보장하지 않는다. |
| 모델 카드의 reasoning 설명 | [Z.ai의 Hugging Face 모델 카드](https://huggingface.co/zai-org/GLM-5.3-Flash)는 `reasoning_effort` 값 `low`, `high`, `max`를 설명하고, 생략하거나 다른 값을 보내면 `max`를 기본으로 쓴다고 한다. benchmark 재현에는 기본 `max`를 유지하라고 안내한다. 채팅 사용 시 `clear_thinking=true`를 명시하라는 설명도 있다. | Z.ai 안내 페이지의 `clear_thinking=false` 권장과 모델 카드의 채팅 사용 지침은 용도가 다를 수 있어 상충을 임의로 통합하지 않는다. EasyDep은 Cloudflare 사용이므로 별도 검증 없이 이 설정을 전송하면 안 된다. |
| Cloudflare 호환 API 표면 | [Workers AI 모델 페이지](https://developers.cloudflare.com/workers-ai/models/glm-5.3-flash/)의 실제 모델 ID는 `@cf/zai-org/glm-5.3-flash`; function calling, reasoning, vision 지원과 context 1,310,720 tokens를 표시한다. 호환 Chat Completions schema에는 `reasoning_effort` enum `low/medium/high`, `parallel_tool_calls=true` 기본값, `tool_choice` `none/auto/required`, `max_completion_tokens` 상한, SSE 스트리밍 필드가 있다. | schema의 `reasoning_effort` 설명은 일반 reasoning-model 파라미터다. Flash에서 `medium`이 Z.ai의 `max`와 동등하다거나, Cloudflare가 Z.ai 모델 고유 `max`를 지원한다고 문서가 말하지 않는다. 페이지는 최대 출력 토큰 수를 명시하지 않는다. 따라서 128K는 Z.ai 측 모델 안내 값이고, Cloudflare에서 허용되는 상한으로 간주할 수 없다. |

Z.ai 문서는 1M context 및 128K 최대 출력, Cloudflare 모델 페이지는 1,310,720-token context를 각각 표기한다. 이는 provider별 서빙 문서의 수치다. Cloudflare AI Search 문서에는 다른 1,048,576 수치도 나타나지만, 이 문서의 provider 비교에는 Workers AI 개별 모델 페이지의 1,310,720을 쓴다. 문서 표면 간 숫자를 단일 모델 상수로 합치지 않는다.

Cloudflare 모델 페이지는 Workers AI binding `env.AI.run("@cf/zai-org/glm-5.3-flash", ...)`, REST API, OpenAI 호환 `/v1/chat/completions`를 안내한다. Z.ai API는 모델 코드 `glm-5.3-flash` 및 자사 API 기준 권장값을 안내한다. 둘은 별도 endpoint/API 표면이다. Cloudflare schema가 `parallel_tool_calls` 기본 true를 표기하더라도 실제 OpenHands 도구 실행 동시성은 OpenHands 설정이 결정한다.

## Z.ai의 일반 코딩 에이전트 지침

[Z.ai Coding Agent Best Practice](https://docs.z.ai/devpack/resources/best-practice)는 잘 쓴 프롬프트 트릭보다 구체적인 작업 문맥을 강조한다. 유효한 입력에는 네 요소를 넣으라고 한다.

- **Goal:** 구현·수정할 결과를 명시한다.
- **Context:** 관련 파일, 함수, 오류, 문서, 예시를 지정한다.
- **Constraints:** 코드 규약, 아키텍처, 보안 요구, 의존성 제한을 쓴다.
- **Done when:** 테스트 통과, 기대 동작, 재현되지 않음 등 완료 판정 방법을 쓴다.

복잡한 작업은 변경 전에 계획을 작성하고, 반복 규칙은 대화마다 되풀이하기보다 프로젝트 지침이나 재사용 가능한 설정에 둔다고 설명한다. 이 페이지는 Z.ai의 일반 coding-agent 지침이지 GLM-5.3-Flash 전용 모델 요구는 아니다.

이를 EasyDep의 한 owner에 적용한 작업 입력 예시는 다음과 같다. 이는 Z.ai 문구의 복제가 아니라 위 네 요소를 우리 실행 단위에 맞춘 제안이다.

- Goal: `RegistrationControl.registerStudent(offeringId:String)`의 승인된 등록 동작을 구현한다.
- Context: 현재 수정 대상 Java source, UC2와 연결된 요구사항·시나리오, 실제 공개 타입·메서드 시그니처를 제공한다.
- Constraints: 공개 시그니처와 쓰기 범위를 지킨다. 학생 식별자의 출처가 계약에 없으면 임의로 만들지 않는다.
- Done when: 수정한 source의 집중 검사가 통과하거나, 구현에 필요한 누락 결정을 출처와 함께 피드백 경로로 보고한다.

## 다른 하네스의 구체적인 설계 근거

| 사례 | 문서가 직접 보여주는 설계 | EasyDep와 연결되는 점 / 차이 |
|---|---|---|
| [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent) | 약 100줄의 agent class, bash 하나의 도구, 선형 대화 이력, 각 명령을 독립 `subprocess.run`으로 실행하는 간결한 구조를 설명한다. | trajectory를 선형으로 보존하면 읽기·수정·관찰의 순서를 재구성하기 쉽다. EasyDep은 제한된 file/grep/check 도구와 작업 이벤트를 가진 OpenHands 기반이므로 동일 구조는 아니다. |
| [Aider repo map](https://aider.chat/docs/repomap.html) | 저장소 전체의 중요 심볼·시그니처를 지도 형태로 제공하고, 그래프 순위와 활성 token 예산으로 관련 부분만 보낸다. 기본 map budget은 1K tokens라고 문서화한다. | 파일을 무작정 넓게 여는 대신 symbol map으로 탐색 시작점을 압축하는 근거다. 작업 소유자가 읽을 권한·범위를 결정하는 기능은 아니다. |
| [OpenHands stuck detector](https://docs.openhands.dev/sdk/guides/agent-stuck-detector) | 기본 활성화된 detector가 반복 action-observation을 감지한다. 예시는 4개의 동일 쌍 뒤 stuck 상태를 표시하고, 도구 이름·action 내용·thought, observation 내용을 비교한다. | 같은 종류의 탐색 반복을 중지하는 데 직접 참고할 수 있다. `registerStudent` 사례처럼 action은 읽기지만 근거가 범위 밖이라는 의미 오류를 이 detector 하나로 분류한다고 문서는 보장하지 않는다. |
| [OpenHands parallel tool execution](https://docs.openhands.dev/sdk/guides/parallel-tool-execution) | `tool_concurrency_limit=1` 기본값은 한 응답의 여러 도구 호출을 순차 실행한다. 모델이 단일 응답에서 여러 호출을 요청하는 일은 여전히 가능하며, concurrency limit을 올리면 실행을 병렬화할 수 있다. 기능은 experimental로 표시된다. | 단일 응답의 호출 개수와 실제 동시 실행은 별개의 지표다. 아래 사례에서 최대 8개 호출을 한 응답에 요청했지만, 실제 병렬 실행 여부는 확인되지 않았다. |
| [Anthropic, Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) | 2024년 글은 고정된 순서의 workflow와 모델이 도구 사용을 동적으로 지휘하는 agent를 구분한다. routing, 독립 하위 작업의 parallelization, orchestrator-workers, evaluator-optimizer 등을 별도 패턴으로 제시하며 단순하고 조합 가능한 패턴을 강조한다. 글은 이후 도구 환경이 바뀌었다고 직접 주의를 덧붙인다. | 작업 단계를 순차 실행할지, 독립적인 부분만 병렬화할지 선택할 때 유용한 개념 근거다. 특정 모델의 권장 설정이나 EasyDep 구현 성능을 증명하지 않는다. |
| [OpenAI, Harness engineering](https://openai.com/index/harness-engineering/) | 저장소 안의 구조화된 지식, 짧은 진입점과 점진적 공개, 계획 및 결정 기록, lint/CI 검증, agent가 막힐 때 도구·가드레일·문서를 개선하는 피드백 루프를 사례로 설명한다. | EasyDep의 설계 근거를 저장소 파일에 고정하고 반복 실패를 하네스 수정 신호로 삼는 방향과 맞는다. 기사 속 완전 자율 성과는 해당 저장소와 도구에 특화됐다고 글 자체가 제한한다. |

공통분모는 더 많은 원문을 한꺼번에 주는 것이 아니라, 목표·근거·제약·완료 조건을 명시하고 필요한 맥락을 선택하며, 반복을 관찰하고, 실제 검증 결과를 다음 개선에 반영하는 것이다. 차이는 이를 모델 tool loop, repository map, 실행기 중단 규칙, 또는 저장소 지식 체계 중 어디에 넣는가에 있다. 이 공통분모는 하네스 설계 관찰이지 모든 사례가 동일 설정을 권장한다는 뜻은 아니다.

## EasyDep 현재 프로필 및 과거 관측

현재 [`app/llm_profiles.py`](../app/llm_profiles.py)의 `zai-org/glm-5.3-flash` 프로필은 `temperature=0.2`, `top_p=None`, `reasoning_effort=medium`, 기본 completion 상한 8,192, 내부 최대 16,384다. 프로필 설명상 이는 모델 선택이 아니라 모델별 요청 모양을 정하는 값이다. `@cf/` 접두사는 canonical model ID를 만들 때 제거되므로 저장된 모델 이름은 접두사 없는 `zai-org/glm-5.3-flash`가 될 수 있고, 실제 Cloudflare 요청 ID에는 `@cf/zai-org/glm-5.3-flash`가 필요하다.

[`docs/openhands-agent-harness-validation.md`](openhands-agent-harness-validation.md)는 2026-09-10 Cloudflare Workers AI OpenAI 호환 endpoint에서 GLM-5.3-Flash, `medium`, OpenHands 1.36.1을 사용한 canary 3회가 각기 읽기·검사·finish 순서로 완료됐다고 기록한다. 이는 도구 왕복 형식 확인이며 구현 품질 평가가 아니라고 문서가 명시한다.

같은 검증 문서의 구현 실험 표는 `medium`, 8K 설정에서 작업 범위에 따라 읽기 탐색만 하고 중단한 실행과 편집·compile까지 완료한 실행 모두를 기록한다. 이를 근거로 해당 문서는 과거 확인 기본 후보를 `temperature=0.2`, `reasoning_effort=medium`, 8K로 요약한다. Z.ai가 자사 API에 권하는 `temperature=1`, `top_p=0.95`, `reasoning_effort=max`는 다른 API 표면의 값이다. 따라서 이 자료만으로 EasyDep의 성공값을 덮어쓰거나, 과거 성공을 Z.ai 권장값이 틀렸다는 증거로 볼 수 없다. 비교 시 endpoint/provider, prompt, 도구, context, 상한을 고정해야 한다.

## `registerStudent` 사례에 적용할 우선순위

최근 `RegistrationControl.registerStudent(offeringId:String)`의 owner replay는 소스 수정 0건, 수정 없는 읽기 최대 20회, 최종 종료 사유 `READ_OUTSIDE_TASK_EVIDENCE`였다. 이벤트에는 첫 두 모델 응답에서 각각 파일 읽기 5개, 복구 메시지 이후의 응답에서 파일 읽기 8개, 마지막 응답에서 검색 2개가 기록됐다. 당시 실험한 8회 읽기 한도는 중단을 요청했지만 이미 제출된 호출과 복구 대화를 막지 못해 효과가 없어 되돌렸다. 이는 도구가 병렬 실행됐다는 증거가 아니다. 해당 메서드의 인자는 `offeringId`뿐이고, 등록 기록에 필요한 학생 식별자를 얻는 계약은 제공되지 않았다는 점도 함께 확인됐다.

현재 runtime은 `READ_OUTSIDE_TASK_EVIDENCE`를 별도로 분류한다. 이 사례에서 필요한 것은 근거 없는 학생 식별자 소스를 구현기가 추측하지 않게 하고, 빠진 공개 결정을 기존 사용자 피드백 경로에 전달하는 것이다. 저장소의 다른 곳에 이미 계약이 있다면 해당 근거를 좁게 제공하면 된다. 둘 중 어느 쪽인지는 사례별로 판단해야 한다.

1. **입력 계약의 공백부터 판별한다.** Z.ai가 제안한 Goal·Context·Constraints·Done when을 현재 operation 단위로 제공한다. `registerStudent`처럼 학생 식별자의 출처가 없으면 기존 피드백 경로에서 사용자 결정을 요청한다. 허용된 근거에 이미 출처가 있다면 해당 근거만 작업에 연결한다. 명시되지 않은 출처를 프롬프트가 정답처럼 만들어서는 안 된다.
2. **상충하는 작업 지침을 정리한다.** 현재 실행 지침에는 “첫 합법적 수정을 시작하라”와 “관련 소스 읽기를 가능한 적은 도구 호출로 묶어라”가 함께 있다. 실제 모델은 다중 읽기를 먼저 요청했다. 두 지침의 동작을 짧은 owner replay에서 한 번에 하나씩 비교하고, 효과가 없는 지침을 제거한다. 새 호출 수 가드부터 추가할 근거는 이번 실행에서 얻지 못했다.
3. **읽을 자료의 시작점을 압축한다.** Aider의 symbol map처럼 정확한 공개 메서드·타입 시그니처와 현재 source를 먼저 제시할 수 있다. 다만 map은 탐색 안내일 뿐 승인된 증거의 대체물이 아니다. 기존 method context·source index의 정보가 중복되는지 확인한 뒤 필요한 부분만 사용한다.
4. **기존 진전 신호로 판정한다.** 소스 해시 변경, 첫 수정까지 걸린 시간, 집중 검사 결과, 반복 오류 지문, 범위 밖 읽기 수를 사용한다. OpenHands의 동일 행동 반복 탐지와 EasyDep의 scope 오류 분류를 함께 보되, 새로운 추상 지표나 UC별 규칙은 실제 필요가 확인되기 전에는 늘리지 않는다.
5. **같은 조건에서 한 번씩 비교한다.** 모델·provider·프로필·작업 입력을 고정한 owner replay에서 첫 수정 도달 여부와 시간, 도구 읽기 수, 수정 파일, 검사 결과, 종료 사유, prompt/completion/cached tokens를 기록한다. 좁은 검증이 통과한 뒤에만 전체 구현 실행을 한 번 수행한다.

이 순위는 낮은 read 횟수 자체를 목표로 하지 않는다. `registerStudent`에서는 학생 식별자의 출처가 미정인 계약과 수정 없는 탐색을 분리해 다뤄야 한다. 어떤 문맥 또는 지침 변경이 실제 첫 수정을 앞당기는지는 아직 검증되지 않았다.

## 2026-09-23 소규모 실제 모델 재생 결과

`app/implementation/agents/runtime.py`에서 관련 소스 읽기를 한 도구 호출로 묶으라는 지침 한 줄만 제거했다. 모델은 두 재생 모두 Cloudflare `glm-5.3-flash`였고, 새 지침이 실제 프롬프트에 포함된 것을 실행 기록으로 확인했다. 전체 구현 실행은 하지 않았다.

| Fresh owner replay | 결과 | 소스 수정 | 수정 전 최대 읽기 | 종료 사유 |
|---|---|---:|---:|---|
| `CourseOfferingControl.getPublishedOfferings()` | 성공, `compileJava` 통과 | 3건 | 5회 | 정상 종료 |
| `RegistrationControl.registerStudent(offeringId:String)` | 실패 | 0건 | 18회 | `NO_PROGRESS_READ_BUDGET` |

비교 기준인 이전 `registerStudent` 재생도 소스 수정 0건, 최대 읽기 20회였으며 `READ_OUTSIDE_TASK_EVIDENCE`로 중단됐다. 이번 재생에서는 범위 밖 읽기 대신 예산 종료로 실패 위치만 바뀌었다. 모델은 수정 가능한 `RegistrationControlService`와 인터페이스·결과 타입을 읽은 다음 Entity 5개, Repository 5개, grep 3회를 수행했지만 첫 수정이나 `report_upstream_gap`을 호출하지 않았다. 누적 모델 사용량은 prompt 69,340 tokens, completion 6,585 tokens이고 약 230초가 걸렸다. 따라서 지침 충돌 제거는 정상 owner의 성공을 유지했지만, 학생 식별자 출처가 빠진 계약을 판별하는 문제에는 효과가 입증되지 않았다. 같은 owner에 새 읽기 가드나 문구 패치를 반복하지 않는다.

실행 증거: [성공 결과](../.easydep/owner-replay-results/owner-replay-result-f5f937028b054136be55f4676c30f182.json), [실패 결과](../.easydep/owner-replay-results/owner-replay-result-7e3043f4e93e43839ff0e075f769a9f5.json), [이전 실패 결과](../.easydep/owner-replay-results/owner-replay-result-98920e739fe542e7bb03e4a0bf57ba86.json). 다음 실험은 전체 구현 재실행이 아니라, 작업 전 입력·결정의 누락 여부를 좁은 계약으로 판별할 수 있는지 검증하는 방향으로 분리한다.

## 참조

조사 대상의 직접 출처는 위 본문 링크를 따른다. 요약상 문서 성격은 다음과 같다.

- 모델 및 모델 전용 파라미터: [Z.ai GLM-5.3-Flash 문서](https://docs.z.ai/guides/vlm/glm-5.3-flash), [Z.ai Hugging Face 모델 카드](https://huggingface.co/zai-org/GLM-5.3-Flash).
- Provider API 표면: [Cloudflare Workers AI GLM-5.3-Flash 모델 페이지](https://developers.cloudflare.com/workers-ai/models/glm-5.3-flash/).
- 일반 코딩 에이전트 입력 지침: [Z.ai Coding Agent Best Practice](https://docs.z.ai/devpack/resources/best-practice).
- 별도 harness 사례: [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent), [Aider repo map](https://aider.chat/docs/repomap.html), [OpenHands stuck detector](https://docs.openhands.dev/sdk/guides/agent-stuck-detector), [OpenHands parallel tool execution](https://docs.openhands.dev/sdk/guides/parallel-tool-execution), [Anthropic agent patterns](https://www.anthropic.com/engineering/building-effective-agents), [OpenAI harness engineering](https://openai.com/index/harness-engineering/).
- EasyDep 내부 기준: [`app/llm_profiles.py`](../app/llm_profiles.py), [`docs/openhands-agent-harness-validation.md`](openhands-agent-harness-validation.md).
