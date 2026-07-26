# 요구사항 분석 에이전트 — 개선 후보

`app/requirements/` 개편 후보를 모아 둔다. **아직 결정된 것은 없다** — 고르기 위한 재료다.
판단 기준은 과제 원문(`app/deployment/document/research.md`)의 목표 3개다.

- 목표 1) 요구사항 기반 산출물 **검증** 및 사용자 피드백을 위한 **AI 에이전트 갱신 과정** 설계
- 목표 2) 클라우드 환경·리소스 특성을 고려한 **가이드라인 제공**
- 목표 3) **AI 에이전트 간 연계**를 고려한 개발 단계별 산출물 생성 방법

과제가 지목한 문제 셋: ① 환각으로 요구사항에 부합하지 않는 산출물, ② 리소스 의존성·제약 반영,
③ 단계별 산출물의 일관된 기준 + 요구사항 부합 측정.

---

## 0. 지금 구조의 사실

개선안이 아니라 사실이다. 여기서 출발한다.

| 사실 | 근거 |
| --- | --- |
| 도구가 0개다. LLM은 내용만 정하고, 무엇을 찾아볼지·다음에 뭘 할지는 못 정한다 | `agent/llm.py`는 `invoke_structured` 하나뿐 |
| Cockburn 그라운딩이 프롬프트 문자열 상수로만 존재. RAG는 "향후 예정"으로 남아 있다 | `prompts.py`(시스템 프롬프트 15개), `agent/steps/step3_specifications.py:16` |
| black-box 검증이 정규식 단어 8개로 내려가 있다. 주석이 스스로 "완전목록 아님"을 인정한다 | `step3_specifications.py:57-62` |
| 흐름이 전부 컴파일 타임 고정. 되돌아갈 길이 없다 | `agent/subgraphs.py`, `agent/graph.py`의 정적 엣지 |
| 반성 루프가 단계 안에 갇혀 있고, 채택 기준이 `len(issues)` 스칼라다 | `step3_specifications.py:258` |
| `repair_stopped="no_improvement"`는 원인이 상위 단계에 있다는 신호일 때가 많은데, 올려보낼 통로가 없다 | `step3_specifications.py:259-261` |
| 상태가 평면 `AgentState` 하나. 모든 노드가 전체를 읽고 쓴다 | `agent/state.py:80` |
| 파이프라인이 두 벌이다 | `agent/graph.py` vs `runner.run_pipeline` |
| 같은 저장소의 배포 쪽은 이미 도구 기반 자율 에이전트다 | `app/deployment/nim_agent/`(agents SDK, `@function_tool` 9축, Runner agentic loop) |

**요약**: `app/requirements/`는 에이전트가 아니라 **고정 프롬프트 체인**이다. 배포 쪽과 세대가 다르다.

## 1. 과제 목표와의 간극

| 목표 | 현재 | 간극 |
| --- | --- | --- |
| 1) 산출물 검증 | 정적 lint + LLM 크리틱이 단계 안에 있음 | 검증이 생성과 같은 컨텍스트·같은 모델 호출 계보에 있다. 독립 검증자가 아니다 |
| 1) **에이전트 갱신 과정** | 피드백 게이트가 **산출물만** 고친다 | 에이전트는 갱신되지 않는다. 같은 실수를 다음 실행에서 또 한다 — 목표 1의 절반이 비어 있다 |
| 2) 가이드라인 제공 | 배포 쪽 KB에만 있음 | 요구사항 단계가 KB를 못 본다. 흡수 경로가 없다 |
| 3) 에이전트 간 연계 | 상태 dict를 순차 전달 | 순차 핸드오프 = telephone game. 뒤 단계가 앞 단계로 되돌릴 수 없다 |
| ③ 부합 측정 | `coverage`, `spec_report`, `relationship_report` | 측정은 있다. 이건 상대적으로 강한 편 |

가장 큰 구멍은 **"에이전트 갱신 과정"** 이다. 과제 문장에 명시돼 있는데 지금 구조에 대응물이 없다.

## 2. 개선 후보

### C1. 지식 접근을 도구로 — KB 검색 + 규칙 인용  *(1단계 착수 완료: `app/requirements/knowledge/`)*

**저작권 경계**: Cockburn PDF는 히스토리 전체에서 지워졌고(`d1a7ec5`) 앞으로도 저장소에
없다. 로컬 사본은 `materials/Usecase_Knowledge/`(gitignore)에 두고 **참조만** 한다 —
`Dockerfile:9`이 `materials/` 전체가 아니라 BERT 모델만 COPY하므로 이미지에도 안 들어간다.
저장소에 담는 것은 우리 표현의 규범 문장 + 인용 좌표 + 좌표 대조용 열쇠 단어뿐이고,
책에서 나온 문장은 없다.

들어간 것: 규칙 레코드 27개(결함 19 · 지침 6 · **규칙 아님** 2) · 근거 등급
(`stated` 11 / `inferred` 16) · 규칙에 묶인 결정론 검출기 5개 · **검증 프롬프트를 KB에서
조립** · 구조화 지적이 `rule_id`를 대도록 강제하고 **지식베이스에 없는 규칙을 인용한 지적은
버린다**(`semantic_status="ungrounded"`) · 도서 인용 18건을 로컬 사본과 대조하는 명령
(`python -m app.requirements.knowledge.verify_citations`, 사본 없으면 테스트가 건너뜀).

**인용 대조가 곧바로 잡은 것**(둘 다 "책이 그렇게 말했다"고 단언하던 상태였다):

| 틀린 인용 | 실제 그 페이지 | 정정 |
| --- | --- | --- |
| `p.64` — 자동결과=guarantee (규칙 **2개**가 인용) | Ch. 5 Three Named Goal Levels (보증 얘기가 없다) | Ch. 6 Preconditions, Triggers, and Guarantees / Minimal Guarantees p.83 |
| `p.207` — include가 기본 관계 | Reminder 5 "Who Has the Ball?" ("rule of thumb"이라는 말만 있다 — 그 단어로 찾다가 잘못 붙은 듯) | Ch. 10 Linking Use Cases p.114-117 |

미확인이던 3건은 페이지를 찾아 승격했다(제어토큰 p.47-49 · 전제조건 재확인 금지 p.81 ·
복귀 의미론 Ch. 8 p.106). 지금 `cockburn-unpinned`은 0건이고 테스트가 그 상태를 고정한다.

라벨도 하나 갈렸다: **`cockburn-extrapolated`**(페이지는 확인했지만 구체적 적용은 우리 것)를
`cockburn-unpinned`(페이지를 못 댐)과 분리했다. 한 라벨로 두면 프롬프트 고지가 거짓이 된다 —
"책이 말한 적 없다"와 "페이지는 댔는데 결론이 우리 것"은 반대 방향의 한계다.

남은 것: 생성 프롬프트는 아직 산문(회귀를 잡을 평가 세트가 없다) · LLM이 부르는 검색
도구는 C2와 함께.


프롬프트에 박힌 Cockburn 그라운딩을 검색 가능한 KB로 옮기고 `search_rule(topic)` /
`find_example(pattern)`을 도구로 준다. 생성은 규칙을 인용하며 쓰고, 검증은
"UI 용어 목록에 걸림" 대신 "Reminder 7, p.209 위반"을 낸다.

- **목표**: 2(가이드라인 흡수), ①(환각 — 근거 없는 주장을 못 하게)
- **근거**: 신경-심볼릭 요구사항 재사용은 KB 그라운딩 + 심볼릭 검증 2단으로 환각을 막는다.
  Agent Skills의 progressive disclosure는 전량 주입(70,000토큰) → 색인만(500토큰)으로
  같은 지식을 훨씬 싸게 다룬다.
- **깨지는 것**: `prompts.py` 대부분, `_UI_TERMS` 정규식 lint, `SPEC_VALIDATOR_SYSTEM`
- **비용**: KB 구축. 단 `app/deployment/`가 이미 9축을 만들어 뒀고, 원래 4개 에이전트에
  흡수시키려던 것이라 이게 그 첫 사례가 된다
- **검증**: 검증 지적에 규칙 인용이 붙는 비율, 인용의 실제 존재 여부(허위 인용 검출)

### C2. 되돌아가기  *(착수 완료: `app/requirements/agent/supervisor.py`)*

**LLM 라우터는 두지 않았다. 라우팅 표가 이미 지식베이스에 있었기 때문이다.** 지적 문구는
규칙 id를 들고 다니고(`Rule.tag`), 규칙은 자기를 낸 단계를 안다(`Rule.owner` — C2에서 추가).
그래서 "이 결함을 누구에게 돌려보낼지"는 **찾아보면 되는 사실**이다. 여기에 LLM을 두면
유도 가능한 판단에 비결정성을 더하는 셈이고(§4), 라우팅 품질을 잴 눈금도 아직 없다.
LLM 감독자로 바꾸려면 갈아끼울 자리는 `decide()` 하나다.

들어간 것:

- **규칙마다 `owner`**(결함을 낸 단계) 19건. 테스트가 `stages.editable_keys()`에 속하는지 지킨다 —
  되돌릴 수 없는 단계를 가리키면 그 결함은 영원히 안 고쳐진다.
- **에스컬레이션**: 그 단계가 자기 루프에서 이미 포기했으면(`repair_stopped`가 clean이 아니면)
  같은 단계로 되돌리지 않고 **위로 올린다**. `no_improvement`는 "위에 원인이 있다"는 신호인데
  올려보낼 통로가 없던 것이 애초의 진단이었다(§0).
- **되돌릴 때 결함을 지시로 들려 보낸다.** 같은 프롬프트로 다시 부르면 같은 답이 온다.
  올려보낼 때는 "아래가 스스로 못 고쳤다"는 사실까지 적는다.
- **사이클이 그래프 엣지로 보인다**(`supervise_model|specs|diagram` + 조건부 엣지). 노드 안에서
  상위 단계를 직접 부르면 `docs/graph/*.png`가 실제 흐름을 말하지 않게 된다.
- **배치 러너에도 같은 판단**(`runner.run_pipeline`). 그래프를 우회하는 경로인데, **평가 세트가
  재는 실행이 그 배치**라서 여기에 없으면 C2의 효과가 측정에 잡히지 않는다.
- 상한 `max_redo_rounds=1`(되돌릴 때마다 아래 단계 전부가 다시 돈다 — 비용이 크다).

이걸로 두 구멍이 막혔다: `review_model`이 찾은 결함을 **아무도 고치지 않던** 것(원인이
`identify_actors`에 있는데 그 단계를 부를 권한이 없었다), 그리고 `repair_stopped="no_improvement"`가
기록만 되고 **아무 행동으로도 이어지지 않던** 것.

**남은 것**: 되돌리기가 실제로 결함을 줄이는지는 **아직 측정하지 않았다**(라이브 배치 실행이
필요하다). `evaluation`의 `score` → `diff`로 전후를 비교할 수 있다.

<details><summary>원래 후보 설명</summary>

4단계 고정 그래프를 걷고 supervisor 1개 + 단계들을 도구로 노출한다
(`refine_requirements`, `model_use_cases`, `write_specifications`, `draw_diagram`).
검증 결함에 **책임 단계** 태그를 붙여 supervisor가 그 단계를 다시 부른다.

- **목표**: 3(연계), 1(검증 결과가 실제 행동으로 이어짐)
- **근거**: orchestrator-worker는 지금 가장 흔한 상용 구조다(Claude Research: lead agent가
  subagent 3~5개를 병렬로 띄우고 인용 패스를 따로 돌린다). 단 공식 지침은 **"단일
  에이전트에 도구를 잘 주는 것부터"** 이며, 멀티 에이전트는 토큰 3~10배다.
- **깨지는 것**: `subgraphs.py` 전체, `agent/stages.py`, `feedback_gates.py`의 정적 라우팅,
  `runner.run_pipeline`(어차피 두 벌이니 합칠 기회), `@contract` 선언 11곳
- **리스크**: 재현성 주장이 약해진다(아래 §4). early victory·무한 루프 방어가 필요하다
- **검증**: 되돌린 횟수와 되돌린 뒤 결함이 실제로 줄었는지

</details>

### C3. 아티팩트 저장소(blackboard) + 추적성 1급

평면 `AgentState` → 아티팩트 저장소(요구사항/액터/UC/명세/관계) + 추적성 링크를 1급 개념으로.
각 단계는 읽을 뷰와 낼 **패치**만 선언한다.

- **목표**: 3(연계 — 순차 전달 대신 공유 작업판), ③(부합 측정을 저장소 질의로)
- **근거**: blackboard 구조는 순차 핸드오프의 정보 손실을 피한다. L2MAC은 덮어쓰지 않고
  확장·개정만 하는 영속 파일 저장소를 쓴다. PatchBoard는 스키마 기반 상태 변이로 감사 가능성을 얻는다.
- **깨지는 것**: `agent/state.py`, `@contract`의 키 기반 검사, `apply_feedback.py`
- **값어치**: `target_ids`로 흉내내는 부분 수정이 자연스러워진다. 전체 재생성 반성 루프를
  수술적 수정으로 바꿀 수 있다
- **단독으로는 값어치가 적다** — C2를 하면 상당 부분 따라온다

### C4. 검증을 전용 에이전트로 분리  *(착수 완료: `app/requirements/agent/validator.py`)*

들어간 것:

- **검증자 한 벌.** step3·step4가 각자 들고 있던 거의 같은 25줄이 `agent/validator.py` 하나로
  합쳐졌다. 단계는 "어느 단계인지"만 말하고, 무엇을 볼지는 지식베이스가 정한다.
- **black-box 경계를 테스트로 고정.** 검증자에게는 산출물만 준다 — 생성 프롬프트도 사용자
  피드백도 주지 않는다. `test_the_validator_never_sees_the_user_feedback`가 피드백 문구가
  검증자 프롬프트에 새지 않는 것을 확인한다(지시를 보여주면 "지시를 따랐는가"를 보게 된다).
- **early victory 방어.** `is_valid`+findings를 버리고 **규칙마다 한 줄씩** 판정을 받는다
  (`schemas.RuleVerdict`). 규칙 6개 중 2개만 훑고 깨끗하다고 답하면 그 사실이 응답에서
  드러나고 저하로 기록된다. `is_valid`는 파생값이라 없앴다 — 따로 두면 findings와 어긋난다.
- **2단계 검증이 생겼다**(`review_model`). 예전에는 커버리지(결정론)만 봤기 때문에 책이
  명시한 결함인 "SuD는 액터가 아니다"(p.59)조차 아무도 판정하지 않았다. 이제 `judged_by`가
  `nowhere`인 결함 규칙은 0건이고, 테스트가 그 상태를 지킨다.

**고치지는 않는다** — `review_model`은 표면화만 한다. 결함의 원인이 앞 단계(`identify_actors`)에
있어서 되돌리려면 오케스트레이션을 바꿔야 한다(C2). 지금은 리포트·응답에 실어 사람이
피드백으로 되돌린다.

<details><summary>원래 후보 설명</summary>

생성기와 계보가 다른 독립 검증자. 산출물만 받고 생성 과정은 안 본다(black-box).

- **목표**: 1(검증), ①(환각)
- **근거**: 공식 지침이 **"가장 일관되게 효과적인 패턴"** 으로 꼽는 것이 verification
  subagent다. 이유가 구조적이다 — 검증은 본질적으로 컨텍스트 이전이 거의 필요 없어서
  핸드오프 문제를 비껴간다. 피드백 루프를 주면 품질이 2~3배(OpenCode는 LSP 진단을 그 루프로 쓴다).
- **주의**: early victory 문제. "최소한만 확인하고 통과" 를 막는 명시적 성공 기준이 필요하다
- **깨지는 것**: `_semantic_findings`가 단계 밖으로 나간다
- **C1과 궁합이 좋다** — 독립 검증자가 KB를 들고 있으면 인용 기반 판정이 된다

</details>

### C5. 에이전트 갱신 = 진화하는 플레이북 ★

**과제 목표 1의 비어 있는 절반.** 피드백을 산출물 수정에만 쓰지 않고, 반성 결과를
**다음 실행이 읽는 플레이북**에 누적한다.

- 두 갈래:
  - **ACE 방식**(Generator / Reflector / Curator): 컨텍스트를 진화하는 플레이북으로 보고
    델타 단위로 증분 갱신한다. 전면 재작성을 피하는 이유가 명확하다 — 요약이 도메인 지식을
    깎아내는 **brevity bias**, 반복 재작성이 세부를 침식하는 **context collapse**.
    오프라인(시스템 프롬프트)·온라인(런타임 메모리) 양쪽에 적용. 보고된 이득 에이전트 +10.6%.
  - **GEPA 방식**(reflective prompt evolution): 스칼라 보상이 아니라 **실행 트레이스**
    (추론 경로·도구 출력·에러)를 LLM이 읽고 프롬프트를 고친다. GRPO 대비 평균 +6%, 최대 +20%,
    롤아웃 35배 절감. 산출물이 사람이 읽고 검수할 수 있는 프롬프트 개선이다.
- **우리 쪽 연결점**: 이미 `repair_stopped` 분포와 telemetry 트레이스를 모으고 있다.
  GEPA가 먹는 입력이 정확히 그것이다. 지금은 모아서 리포트에만 싣고 버린다.
- **비용**: 평가 세트가 있어야 한다(`agent/baseline.py`·`agent/compare.py`가 씨앗)
- **검증**: 같은 요구사항 입력을 N회 돌려 결함율이 실행을 거치며 내려가는지

### C6. EARS 표기 도입

요구사항·수용기준을 `WHEN [조건] THE SYSTEM SHALL [동작]` 형태로 고정한다.

- **목표**: ③(일관된 기준 + **측정 가능성**)
- **근거**: Kiro(AWS)는 spec-driven development로 `requirements.md`(EARS 사용자 스토리) →
  `design.md` → `tasks.md` 3문서 체계를 만든다. EARS는 형식적 추론과 property-based 테스트로
  이어질 수 있는 검증 가능한 수용기준을 낸다.
- **우리 쪽 의미**: FR/NFR 분류(BERT)와 층위가 다르다 — 분류는 종류, EARS는 **문장 형식**.
  형식이 고정되면 결정론적 검증의 사정거리가 훨씬 넓어진다(지금 정규식 8단어의 대안)
- **깨지는 것**: `schemas.py`의 요구사항 표현, `classifier.py` 입력 형태
- **뒤 단계에 주는 이득**: 설계·구현 에이전트가 받는 입력이 파싱 가능해진다 → 목표 3

### C7. 파이프라인 한 벌로 통합 (정리)

`agent/graph.py`와 `runner.run_pipeline`이 같은 일을 두 벌로 한다. 어느 개편을 하든 선결 과제.
단독으로는 개편이 아니라 청소다.

---

## 3. 상용·연구 방법론 조사 (2026-07-26)

### 오케스트레이션

- **단일 에이전트 먼저.** 잘 설계된 단일 에이전트 + 좋은 도구가 기대 이상을 한다. 멀티
  에이전트는 조정 비용·실패 지점·프롬프트 유지 부담을 더한다. 화려한 멀티 구조를 짜 놓고
  단일 에이전트 프롬프트 개선으로 같은 결과가 나오는 걸 발견하는 팀이 흔하다.
- 멀티가 값어치를 하는 세 경우: **컨텍스트 보호**(서브태스크가 1000토큰 이상의 무관한
  컨텍스트를 만들 때), **병렬화**(독립 경로), **전문화**(도구 20개 초과 등).
- **분해는 작업 종류가 아니라 필요한 컨텍스트로 한다.** "한 에이전트가 기능 작성, 다른
  에이전트가 테스트" 식 분해는 실패한다. 자주 상태를 공유해야 하는 일은 **같은 에이전트 안**에 둔다.
- 실패 모드: **telephone game**(순차 분할 → 핸드오프마다 컨텍스트 손실, 실행보다 조정에
  토큰을 더 씀), **early victory**(검증 에이전트가 최소 확인 후 통과 처리).
- 비용: 동등 작업에서 토큰 3~10배. Claude Research는 채팅의 약 15배.
- 오케스트레이터가 워커 컨텍스트를 누적해 워커 4개 이상에서 컨텍스트 창을 넘기는 사례가 보고된다.

### 검증

- **verification subagent가 가장 일관되게 효과적**이다. 구조적 이유: 검증은 컨텍스트 이전이
  거의 필요 없다.
- 모델에 자기 작업을 검증할 수단을 주면 품질이 2~3배. OpenCode는 LSP 진단(미정의 변수·타입
  오류)을 그 루프에 물린다 — **결정론적 도구를 피드백 신호로 쓰는 것**이 핵심.
- 에이전트 환각은 최종 응답만이 아니라 계획·도구 사용·메모리 접근·관찰 해석 전 구간에서
  생긴다 → 전 구간 관측이 필요하다.

### 지식 접근

- **progressive disclosure 3계층**: 이름+설명만 상시 로드 → 필요할 때 SKILL.md → 그때
  참조 파일. 전량 주입 70,000토큰 vs 색인 500토큰.
- 신경-심볼릭 요구사항 재사용: 파라메트릭 기억 대신 질의·검증 가능한 심볼릭 KB에 앵커링하고,
  생성 → 심볼릭 검증 2단 파이프라인으로 KB에 없는 것을 만들어내지 못하게 한다.

### 산출물 규율

- **Kiro(AWS, 2025-07 출시)**: 프롬프트를 구조화된 요구사항·설계·태스크로 바꾼다.
  `requirements.md`(EARS) / `design.md`(컴포넌트·데이터 모델·인터페이스) / `tasks.md`(서로
  기반이 되는 구현 체크리스트). GitHub Spec Kit, BMAD-METHOD도 같은 계열.
- 우리 4단계(요구사항→설계→구현→테스트)와 층위가 맞는다. 참고 값어치가 크다.

### 에이전트 갱신

- **ACE**: Generator / Reflector / Curator, 델타 증분 갱신, brevity bias·context collapse
  회피, 오프라인+온라인. 에이전트 +10.6%.
- **GEPA**: 실행 트레이스 기반 reflective prompt evolution, 가중치 고정. GRPO +6~20%,
  롤아웃 35배 절감, MIPROv2 대비 +10% 이상. 결과가 사람이 읽을 수 있는 프롬프트다.
- 둘의 공통 주장: **스칼라 보상으로 트레이스를 뭉개지 말고, 트레이스를 읽어라.** 우리
  `_spec_for`의 `len(issues)` 비교가 정확히 그 스칼라 뭉개기다.

### 에이전트 간 연계

- blackboard: 공유 작업판에 중간 가설·제약·부분 결과를 누적. 순차 핸드오프의 "상태가 가장
  최근 산출물에만 존재" 문제를 피한다.
- L2MAC: 덮어쓰지 않고 확장·개정만 하는 영속 파일 저장소 + 읽기/쓰기를 관장하는 Control Unit.
- PatchBoard: 스키마 기반 상태 변이 → 신뢰성·감사 가능성.

---

## 4. 우선순위 제안

1. ~~**C1(지식 도구화)**~~ — 착수 완료(`app/requirements/knowledge/`). 인용 대조로 틀린 인용
   두 건을 잡았다.
2. ~~**C4(독립 검증자)**~~ — 착수 완료(`app/requirements/agent/validator.py`). 2단계 검증이
   생겨 판정자 없는 결함 규칙이 0건이 됐다.
3. **C5(에이전트 갱신)** ← **다음** — 과제 목표 1의 비어 있는 절반. 트레이스는 이미 모으고
   있고, C4가 `unexamined_rules`·`ungrounded_rule`까지 더해 놓았다.
   선결 과제였던 **평가 세트는 착수 완료**(`app/requirements/evaluation/`, 아래 §5).
4. ~~**C2(되돌아가기)**~~ — 착수 완료(`agent/supervisor.py`). §4의 트레이드오프가 처음 쓴 것보다
   작다는 게 밝혀진 뒤 순서를 올렸다. LLM 라우터는 두지 않았다(라우팅 표가 지식베이스에 있었다).
5. C6은 병행 가능(스키마 작업), C3·C7은 남아 있다.

### 감수해야 하는 트레이드오프 (C2) — 처음 쓴 것보다 작다

**정정(2026-07-26).** 이 절은 원래 "지금 구조는 `temperature=0`+seed로 *같은 입력 같은 출력*을
주장할 수 있고 C2가 그 주장을 약화시킨다"고 적혀 있었다. **앞의 전제가 틀렸다.**

`gpt-oss-120b`는 MoE이고, 어느 전문가로 라우팅되는지가 **함께 배치된 다른 요청들에 좌우된다.**
배치가 달라지면 부동소수 리덕션 순서도 달라져 로짓이 미세하게 갈리고, argmax가 뒤집히는 토큰이
하나만 있어도 출력이 갈라진다. seed·temperature는 분산을 줄이는 장치이지 없애는 장치가 아니다
(자세히는 `agent/llm.py`의 "재현성 — 여기서 얻을 수 없는 것").

실제로 이 세션에서 관측했다: **같은 입력을 5회 판정하니 답이 갈렸다** — 한 케이스에서 곁따라
걸린 규칙이 `1/5`, 다른 케이스에서 `2/5`, `4/5`. 같은 입력·같은 seed·`temperature=0`인데도
5회가 서로 다른 판정을 냈다는 뜻이다.

그래서 C2가 위협하는 것은 **출력 재현성이 아니다**(그건 애초에 없었다). 위협받는 것은
**실행 경로의 고정성**이고, 그건 트레이스로 대체 가능하다 — supervisor의 라우팅 결정을 남겨
"같은 출력"이 아니라 "같은 경로"를 보이면 된다. 남는 실질 비용은 토큰(3~10배)과
그림 하나로 설명되는 단순함이다.

**측정에 대한 귀결이 더 중요하다.** LLM 출력에 대한 주장은 한 번 돌려서 할 수 없다.
결정론이 필요한 판정은 결정론 층(`knowledge/detectors.py`)에 두고, LLM 판정에 대한 주장은
표본을 반복해 비율로 낸다(§5). 이건 C2와 무관하게 지금도 적용된다.

---

## 5. 평가 세트 (`app/requirements/evaluation/`, 2026-07-26)

C5의 선결 과제. "이 변경이 나아진 것이냐"에 답할 근거가 없으면 플레이북 갱신은 취향 싸움이 된다.

**착수 시점에 드러난 것**: 채점 로직(`agent/compare.py`의 `score_run`, 지표 20여 개)은 이미
있었지만 **저장소 안에서 아무도 부르지 않았다** — CLI가 `e10c527`로 report 저장소에 나갔다.
그래서 평가 세트는 새로 짜는 일이 아니라 **문을 다시 내고 해상도를 올리는** 일이었다.

```
python -m app.requirements.evaluation seeded              # 검사기 눈금 (LLM·키 없이)
python -m app.requirements.evaluation score <run_dir>     # 채점표 (LLM 없이)
python -m app.requirements.evaluation diff <a> <b>        # 규칙별 증감
```

**① 규칙 단위 채점**(`scorecard.py`). `spec_validation_issues: 8 → 8`은 아무 정보가 아니다 —
scope creep 3건이 줄고 hidden branching 3건이 늘어도 같은 수다. 그래서 규칙별로 센다. 두 수를
따로 내는 것이 핵심이다:

- `static_now` — **오늘의** 검출기로 다시 검증한 결과. 코드가 바뀌어도 같은 잣대라 예전 실행과
  나란히 놓을 수 있다.
- `as_recorded` — 그 실행이 기록한 결함(의미 검증 포함). 검증 설정이 같았을 때만 비교할 수 있고,
  `diff`가 한쪽이라도 `disabled`면 **경고를 내고 그 비교를 무효라고 말한다.**

꼬리표에서 규칙 id를 못 찾은 지적은 `(untagged)`로 센다 — 조용히 버리면 규칙별 합과 전체 합이
어긋나는데 아무 표시가 없다.

**② 심어 둔 결함**(`seeded.py`). 채점표만으로는 "scope creep 0건"이 **정말 없는 것**인지
**검사기가 못 잡는 것**인지 구별할 수 없다. 그래서 규칙마다 결함을 하나씩 심어 두고 잡는지
본다. 지금 **5/5 검출 · 오탐 0 · 심어 두지 않은 검출기 규칙 0**. 대조군(`CLEAN`)은 아무것도
나오지 않아야 하고, 케이스마다 자기 규칙만 걸려야 한다(둘을 동시에 어기면 그 케이스로는 눈금을
읽을 수 없다). `tests/test_evaluation.py`가 이 넷을 CI 게이트로 고정한다 — **API 키 없이
돌아야 하므로** `scorecard`는 `agent.compare`를 함수 안에서 import한다(상단으로 올리면 LLM
스택이 딸려 와 import만으로 죽는다). 그 성질도 테스트가 지킨다.

**③ 의미 규칙 눈금**(`semantic.py`, 라이브). LLM 판정은 결정론이 아니라 **CI 게이트가 될 수
없다** — 한 번 실패한 것이 코드 잘못이라고 말할 수 없다. 그렇다고 안 재면 의미 규칙에 대한
모든 "0건"이 근거 없는 0이 된다. 그래서 케이스마다 N회 돌려 **검출률**을 본다. 의미 규칙
12건 전부에 케이스가 있고(명세 6 · 관계 5 · 모델 1), 단계별 대조군으로 오탐률도 잰다.

```
RUN_LIVE_TESTS=1 python -m app.requirements.evaluation semantic --repeats 3
RUN_LIVE_TESTS=1 python -m pytest tests/test_live_evaluation.py -s
```

테스트가 주장하는 것은 정확도가 아니라 **눈금이 살아 있는지**다(0/N인 규칙이 없는지,
대조군 오탐률이 절반을 넘지 않는지). 검출률 2/3과 3/3의 차이는 표본이 작아 잡음이므로,
프롬프트 전후를 비교하려면 같은 N을 키워 두 번 돌려야 한다.

**눈금을 만들다 구조적 구멍이 나왔다.** `spec.no-scope-creep`은 "주어진 요구사항에 없는 기능을
만들지 말라"인데, 검증자에게 넘기는 payload에 **요구사항이 없었다** — 근거 없이 판정하라고
시키고 있었다. `_semantic_findings`가 이제 해당 UC의 FR·NFR 문장을 함께 넘긴다. 요구사항은
지시가 아니라 잣대이므로 black-box 위반이 아니고, 그 경계(지시는 안 준다 / 잣대는 준다)를
`tests/test_validator.py`가 양쪽으로 고정한다.

### 첫 측정 결과 (2026-07-26 · `openai/gpt-oss-120b` · N=3)

**의미 규칙 12건 전부 눈금이 살아 있다** — 심어 둔 결함을 3/3으로 잡았다. 죽은 눈금은 없다.

**그런데 첫 측정의 진짜 소득은 대조군에서 나왔다.** 명세 단계 대조군의 오탐률이 **100%**였고,
`spec.no-scope-creep`과 `spec.remerge-re-establishes-state`가 결함 없는 명세에도 3/3으로
걸렸다. 그러면 그 둘의 "100% 검출"은 정보가 아니다 — 아무거나 다 잡는 눈금이다.

원인을 보니 **검증자가 옳고 내 대조군이 오염돼 있었다**:

- 확장이 "대체품 제안"을 하는데 내가 준 요구사항에 그 기능이 없었다 → 진짜 scope creep
- 대체 확정 상태를 세우지 않고 주문 기록 스텝으로 복귀했다 → 진짜 복귀 결함

요구사항에 FR4를 넣고 확장이 교체를 확정하게 고친 뒤 **오탐률 100% → 33%(N=3)** 로 떨어졌고,
seed 격리(아래)까지 끝낸 최종 상태는 **12/12 규칙 100% 검출 · 세 단계 대조군 오탐 0%**(N=3)다.

**두 번째 소득: seed가 격리돼 있지 않았다.** N=5에서 `spec.no-scope-creep`이 다른 케이스에
5/5·5/5로 곁따라 걸렸는데, 원인은 내 seed 문장이 목표 결함과 **함께** 범위 밖 기능을 들여온
것이었다("다시 고르게 한다", "항목을 지운다", "로그인을 확인한다"). 요구사항 FR5~FR7로 그
곁가지를 범위 안에 넣으니 hidden-branching 5/5 → **0**, precondition-recheck 2/5 → **0**.

남은 곁따라 걸림 둘은 **설명되는 것**이라 그대로 둔다:

- consequence 케이스 → scope creep 4/5: 감사 기록을 일부러 범위 밖에 뒀다. 범위 안에 넣으면
  "그 스텝이 정당하다"가 되어 consequence 눈금 자체가 죽는다.
- remerge 케이스 → scope creep 5/5: seed가 **시스템이** 전 항목을 지우게 만든다(FR6은 회원이
  지우는 것). 더 맞추려 문구를 다듬는 것은 모델에 fixture를 과적합시키는 일이다.

**교훈 셋.** ① "대조군이 깨끗하다"도 측정해야 아는 사실이다. ② seed가 규칙 하나만 어기는지도
측정해야 아는 사실이다. ③ 이 수들은 전부 **비결정 과정의 표본**이다(같은 대조군이 0/5·1/5로
갈렸다) — 그래서 테스트는 비율의 느슨한 성질만 주장한다(0/N인 규칙이 없는지, 오탐률이 절반을
넘지 않는지). 전후 비교로 프롬프트 효과를 주장하려면 같은 N을 키워 두 번 돌려야 한다.

## 6. C2 측정 — 되돌아가기는 아직 값을 못 냈다 (2026-07-26)

`toystore`(FR 15개), `MAX_REDO_ROUNDS` 0 대 1, **조건별 2회**. 두 번 돌린 이유는 한 쌍만 보면
비결정성과 구별할 수 없기 때문이다(§4).

| 조건 | 기록된 결함 합 | 재검증(static) | clean/no_improvement | LLM 호출 |
| --- | --- | --- | --- | --- |
| redo=0 | 8 · 7 | 0 · 3 | 4/7 · 5/6 | 53 · 49 |
| redo=1 | 6 · 9 | 1 · 1 | 7/5 · 4/7 | 95 · 95 |

**개선이 보이지 않는다.** 기저 7~8, 처리 6~9로 **처리 범위가 기저 범위를 감싼다.** 결정론
재검증은 기저가 0과 3으로 갈려(같은 조건·같은 입력) 조건 간 차이를 읽을 수 있는 상태가
아니었다. 반면 비용은 확실하다 — **호출 1.9배, 벽시계 1.9배.**

그래서 **`max_redo_rounds` 기본값을 0으로 되돌렸다.** 기계장치는 그대로 둔다(`MAX_REDO_ROUNDS=1`).
개선이 안 보이는데 비용이 두 배인 것을 기본으로 둘 수는 없다.

### 왜 안 먹었나 — 가설 (아직 시험하지 않음)

**에스컬레이션이 문장 수준 지시를 상위 단계로 올린다.** 이번 실행에서 발동한 경로는 전부
"specs가 `no_improvement`로 포기 → use_cases로 올림"이었다. 그런데 올려보낸 지시는
`spec.black-box-no-ui-mechanics`("스텝 3에 'screen'이 있다") 같은 **문장 수준 결함**이다.
`identify_use_cases`가 그걸로 무엇을 다르게 할 수 있는지가 없다 — 실행 불가능한 지시다.

즉 지금 정책은 두 가지를 뭉개고 있다:

- **문장 수준 결함**(UI 용어·내부컴포넌트 누출)은 specs가 낸 것이고 specs가 고칠 수 있다.
  위로 올리는 것이 오히려 틀렸다.
- **구조 수준 결함**(복귀 의미론·UC 범위)은 위에 원인이 있을 수 있다.

에스컬레이션 여부는 **"그 단계가 포기했는가"만이 아니라 규칙이 상위 원인을 가질 수 있는
종류인가**에 달려야 한다. 그러면 지식베이스에 사실 하나가 더 필요하다(규칙별 "상위 원인
가능성"). 그건 이번에 넣지 않았다 — 측정이 그 필요를 알려준 것까지가 이번 소득이다.

또 하나: 지배적인 결함이 `spec.no-scope-creep`과 `spec.remerge-re-establishes-state`인데,
이 둘은 §5의 눈금 작업에서 **경계 사례에 잘 걸리는** 규칙으로 드러났다. 일부는 실제 결함이
아니라 판정 잡음일 수 있고, 그렇다면 위를 고쳐도 사라지지 않는다.

### 측정하면서 고친 것

`redo_history`가 아티팩트에 저장되지 않아 **어느 단계로 왜 되돌렸는지 사후 확인이
불가능했다**(호출 수로 추정해야 했다). `redo.json` + 매니페스트 요약 + 채점표에 넣었다.

## 7. 판정 잡음 측정 — 의미 검증 층 전체가 흔들린다 (2026-07-26)

C2가 값을 못 낸 이유를 찾으려고, **실제 실행의 명세를 같은 검증자에게 여러 번 물었다**
(`evaluation semantic` 계기와 별도로 `evaluation stability`를 만들었다 — fixture 대조군은
오탐 0%인데 실제 명세에서는 두 규칙이 결함의 절반을 냈으므로, fixture로는 안 보이는 잡음이었다).

`toystore` 명세 11개 × 5회, `openai/gpt-oss-120b`:

| 규칙 | votes=1 항상/때때로 | votes=3 항상/때때로 |
| --- | --- | --- |
| `spec.no-scope-creep` | 0 / 10 (100%) | 1 / 6 (86%) |
| `spec.no-hidden-branching` | 0 / 8 (100%) | 0 / 1 (100%) |
| `spec.consequence-is-a-guarantee` | 0 / 5 (100%) | 0 / 4 (100%) |
| `spec.black-box-no-internal-components` | 2 / 3 (60%) | 2 / 1 (33%) |
| `spec.remerge-re-establishes-state` | 1 / 0 (0%) | 1 / 0 (0%) |
| **전체 흔들림** | **90%** | **75%** |

(순차 측정에서는 83%였다 — 동시성을 8로 올려도 90%로 같은 수준이므로 배치 구성 탓이 아니다.)

**이것이 C2 결과를 완전히 설명한다.** 결함 판정의 90%가 흔들리면 상위를 고쳐도 결함 수에
나타나지 않고, 실행 간 변동(7·8·6·9)은 같은 동전을 다시 던진 것이다. `no_improvement`가
명세 11개 중 5~7개인 것도 같다 — 생성 시 검증과 재생성 후 검증이 **서로 다른 결함을 본다.**

### 다수결(`validator_votes`)의 값과 한계

`validator.review`가 N번 물어 **과반으로 위반을 확정**하도록 했다(기본 1 = 예전 동작).

- **한 일**: 걸린 (명세×규칙) 쌍이 29 → 16으로 줄었다. 약한 판정이 걷혔다는 뜻이고,
  유령 결함이 반성 루프와 결함 수에 덜 들어간다.
- **못 한 일**: 살아남은 것의 75%가 여전히 흔들린다. 다수결은 한 번 판정의 확률이 0.5에서
  떨어져 있을 때만 날카로워진다(p=0.7 → 3표 과반 ≈ 0.78). **p≈0.5인 판정은 3표로도 0.5다.**
  `consequence-is-a-guarantee`(항상 0)와 `no-hidden-branching`이 그 모양이다 — 잡음이 아니라
  **신호가 없다.**

그래서 **기본값은 1로 둔다.** 90%→75%를 위해 검증 호출을 3배 쓰는 것은 근거가 약하다.
`VALIDATOR_VOTES=3`으로 켤 수 있고, 기계장치와 측정 계기는 남아 있다.

### 다음에 시험할 것 (둘 중 하나)

1. **한 호출에 규칙 하나.** 지금은 한 번에 6개 규칙을 판정하라고 요구한다. 과제를 쪼개면
   p가 0.5에서 멀어질 수 있다. 호출 수는 6배지만 응답이 짧아 호출당 시간은 준다.
2. **신호 없는 규칙을 강등한다.** `consequence-is-a-guarantee`·`no-hidden-branching`처럼
   항상-판정이 0인 규칙은 `DEFECT`로 두면 반성 루프와 결함 수를 오염시킨다. 판정할 수 있게
   되기 전까지 `GUIDANCE`로 내리는 것이 정직하다 — 못 재는 것 위에 수를 쌓지 않는다.

**측정 계획 주의**: 이 구조화 호출은 실측 **평균 10초**대다(`llm_seconds/llm_calls`).
순차로 재면 165콜이 27분이라 눈금이 있어도 안 쓰게 된다 — 그래서 측정도 병렬화했다
(`spec_concurrency`).

## 출처

- [When to use multi-agent systems (and when not to) — Anthropic](https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them)
- [How Anthropic Built a Multi-Agent Research System — ByteByteGo](https://blog.bytebytego.com/p/how-anthropic-built-a-multi-agent)
- [Agentic AI Workflows in Production: Patterns, Pitfalls, and Best Practices (2026)](https://devstarsj.github.io/2026/06/23/agentic-ai-workflows-production-patterns-2026/)
- [Agentic Harness Engineering: LLMs as the New OS](https://www.decodingai.com/p/agentic-harness-engineering)
- [Agent Skills — Claude Platform Docs](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- [Skill authoring best practices — Claude Platform Docs](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices)
- [ACE: Agentic Context Engineering (arXiv 2510.04618)](https://arxiv.org/abs/2510.04618)
- [GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning (arXiv 2507.19457)](https://arxiv.org/abs/2507.19457)
- [Neuro-Symbolic Agents for Hallucination-Free Requirements Reuse (arXiv 2605.01562)](https://arxiv.org/pdf/2605.01562)
- [LLM-Based Multi-Agent Blackboard System (arXiv 2510.01285)](https://arxiv.org/abs/2510.01285)
- [PatchBoard: Schema-Grounded State Mutation (arXiv 2605.29313)](https://arxiv.org/pdf/2605.29313)
- [What Is Spec-Driven Development and How to Implement It with Kiro](https://aws.plainenglish.io/what-is-spec-driven-development-and-how-to-implement-it-with-kiro-b5846bd55869)
- [Comprehensive Guide to SDD: Kiro, GitHub Spec Kit, BMAD-METHOD](https://medium.com/@visrow/comprehensive-guide-to-spec-driven-development-kiro-github-spec-kit-and-bmad-method-5d28ff61b9b1)
- [LLM Hallucination: A 2026 Architectural Deep Dive](https://futureagi.com/blog/llm-hallucination-deep-dive-2026/)
