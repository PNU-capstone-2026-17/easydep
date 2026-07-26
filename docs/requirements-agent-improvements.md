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

### C2. 흐름을 supervisor에게 — 되돌아가기

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

### C4. 검증을 전용 에이전트로 분리

생성기와 계보가 다른 독립 검증자. 산출물만 받고 생성 과정은 안 본다(black-box).

- **목표**: 1(검증), ①(환각)
- **근거**: 공식 지침이 **"가장 일관되게 효과적인 패턴"** 으로 꼽는 것이 verification
  subagent다. 이유가 구조적이다 — 검증은 본질적으로 컨텍스트 이전이 거의 필요 없어서
  핸드오프 문제를 비껴간다. 피드백 루프를 주면 품질이 2~3배(OpenCode는 LSP 진단을 그 루프로 쓴다).
- **주의**: early victory 문제. "최소한만 확인하고 통과" 를 막는 명시적 성공 기준이 필요하다
- **깨지는 것**: `_semantic_findings`가 단계 밖으로 나간다
- **C1과 궁합이 좋다** — 독립 검증자가 KB를 들고 있으면 인용 기반 판정이 된다

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

1. **C1(지식 도구화)** — 산출물 품질에 바로 닿고(지금 검증이 가장 원시적인 지점), C2의 도구
   배선을 미리 깐다. 배포 KB 흡수의 첫 사례라 과제 목표 2와도 맞는다.
2. **C4(독립 검증자)** — C1과 궁합. 공식 권장 패턴 중 비용 대비 효과가 가장 확실하다.
3. **C5(에이전트 갱신)** — 과제 목표 1의 비어 있는 절반. 트레이스는 이미 모으고 있다.
4. **C2(supervisor)** — 가장 큰 개편. 아래 트레이드오프를 감수할지가 실제 결정 포인트.
5. C6은 C1~C4와 병행 가능(스키마 작업), C3·C7은 C2에 딸려간다.

### 감수해야 하는 트레이드오프 (C2)

지금 구조는 `temperature=0` + seed 고정으로 "같은 입력 같은 출력"을 주장할 수 있다
(`agent/llm.py`의 재현성 주석, `system_fingerprint` 수집). LLM이 흐름을 정하면 그 주장이
약해진다. 졸업작품 심사에서 파이프라인을 그림 하나로 설명할 수 있는 것도 지금 구조의 장점이다.

완화안: supervisor의 라우팅 결정을 트레이스에 남겨 **"실행 경로 자체를 재현 근거로"** 삼는다
(같은 출력이 아니라 같은 경로를 보인다). C5의 트레이스 수집과 같은 인프라를 쓴다.

---

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
