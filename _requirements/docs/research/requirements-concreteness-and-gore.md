# 요구사항 구체성 판정 & 목표지향 RE(GORE) 조사 정리

> 조사 목적: 요구사항 분석 에이전트 step1의 "구체화 게이트"를 LLM 단일 bool에서
> **항목별 점수화 + 임계값(τ)** 으로 개편할 근거, 그리고 step1/step2에 **목표(goal)**
> 개념을 도입할지에 대한 근거를 마련한다.
>
> 방법: deep-research 하니스 2회 실행(각도 fan-out → 소스 fetch → 주장별 3표 적대적
> 검증). 리포트1(concreteness rubric): 22소스/101주장→25검증, 24확정·1기각.
> 리포트2(GORE): 6각도, canonical 소스 만장일치 검증.
>
> ⚠️ 이 문서는 **조사 결과 아카이브**다. 실제 코드 적용은 별도 설계/구현에서 진행한다.
> (현재 확정 스코프: 축 A = concreteness 게이트만 우선 구현. 축 B = goal 포착은 보류)

---

## 리포트 1 — 요구사항 concreteness/testability 점수화 rubric

### 핵심 결론
문헌은 한 방향으로 수렴한다: **구체성/명확성/testability는 개별적으로 조작적 정의된
rubric 항목들의 집계 점수 + 임계값으로 판정하라. 단일 LLM bool 금지.**

### 1) 표준 앵커 — ISO/IEC/IEEE 29148:2018
- 개별 요구사항 품질 특성 **9개**: necessary, appropriate, unambiguous, complete,
  singular, feasible, verifiable, correct, conforming.
- 최근 NLP 연구(ASME IDETC-CIE 2024)가 이 9개를 **token/span/syntax/sentence** 4계층으로
  조작화하고 **[0,1] 단일 "well-formed requirement quality index"** 로 합산 → τ-게이트의
  정확한 템플릿.
- 주의: 이 프레임워크는 **rule-based NLP(LLM 아님)** 이고, 논문 자체는 τ 컷오프를 정하지
  않는다. correct/feasible/necessary의 완전 자동화는 도메인 맥락이 필요해 aspirational.
- 소스: researchgate 385802396 / ASME IDETC-CIE2024 V02BT02A013

### 2) 문장 구조 규칙 & 자동 측정 도구
- **EARS** (Mavin et al., RE'09): 파싱 가능한 템플릿. 5개 키워드 패턴(Ubiquitous / While /
  When / Where / If-Then), 모두 `<system name> shall <system response>` 코어 강제
  → actor+action 명확성을 자동 게이트로 강제. (+ 6번째 Complex는 조합형)
- **Femmer "requirements smells"** (JSS 2017): ISO 29148을 9개 자동탐지 언어 지표로 조작화
  — Subjective language / Ambiguous adverbs·adjectives / Loopholes / Non-verifiable terms /
  Superlatives / Comparatives / Negative statements / Vague pronouns / Uncertainty.
  Smella 도구가 POS 태깅·사전으로 탐지.
- **QuARS** (Gnesi & Trentanni): lexical(optionality/subjectivity/vagueness/weakness) +
  syntactic(implicity/multiplicity/under-specification). **actor/object 명확성 = 'implicity'
  defect**(주어·목적어가 구체 명사 대신 대명사·지시어). 문서 단위 defect rate·가독성 산출.
- **NASA ARM** (Wilson et al. 1997): 어휘 빈도 카운트. weak phrase(adequate/timely/easy/tbd)를
  모호·불완전 지표로, imperative를 강도순(shall 최강 → should 최약)으로 랭킹.
  (실제 9범주: Imperatives/Continuances/Directives/Options/Weak Phrases/Size/Text Structure/
  Specification Depth/Readability — 원조사 claim의 범주 열거에 사소한 오류 있었음)
- **QUS/INVEST** (유저스토리): 등급형(1~3) 척도. Feature Specificity/Language Clarity/
  Rationale Clarity 등, INVEST의 Testable 포함.

### 3) LLM 단독 판단의 실증적 한계 (중요)
- zero-shot weak-word 탐지: **recall >0.98이나 precision ~0.55~0.57** — 멀쩡한 요구의
  ~45%를 오탐. (Unterbusch & Vogelsang, ICSE-NIER'26, Mercedes-Benz 벤치)
- 같은 모호 요구에 LLM은 **기능적으로 다른 해석**을 내놓고 스스로 모호성 식별·해소 못 함.
  (Di Yang et al. 2026, Orchid 벤치 1,304 태스크)
- **회복법**:
  1. 명시적 rubric/codebook 앵커링 — full codebook 시 Claude 3 Opus κ=0.87 (인간 간
     0.74~0.78 능가)
  2. 태스크 분해 (detect → classify → resolve)
  3. 인간검증 CoT few-shot **20개** → precision 0.55→**0.70** (표준 few-shot 및 320예제
     파인튜닝 BERT F1 0.709도 능가, HLC F1 0.799)
  4. human-in-the-loop 유지

### 산출 (a) — concreteness 점수화 rubric 항목 후보
개별 요구사항당 체크 → 가중 집계:
1. 명시적 named actor/system이 주어인가 (대명사·지시어 아님) — QuARS implicity, EARS
2. 단일 action 동사 + shall-강도 서법 — EARS, ARM imperative
3. 원자적(singular) — and/or·다중성 없음 — 29148 singular, QuARS multiplicity
4. 검증가능 — 측정기준 포함, non-verifiable 용어 없음 — 29148, INVEST Testable, Femmer
5. weak/vague 단어 없음 (adequate/timely/easy/tbd) — ARM, QuARS, Femmer
6. optionality 단어 없음 (can/may/optionally) — ARM options, QuARS optionality
7. 모호한 비교급·최상급 없음 — Femmer
8. loophole·불확실 서법 없음 (could/might) — Femmer uncertainty
9. complete·under-specification·TBD 없음 — 29148 complete, QuARS
10~11. (유저스토리 맥락) 언어 명료성·rationale — QUS 1~3점

→ 1~9번은 **사전/POS/정규식으로 결정론적 탐지 가능** = LLM 없이도 되는 하드 프리필터.

### 산출 (b) — 임계값 τ (가장 큰 공백)
- **어떤 소스도 concreteness 게이트용 검증된 숫자 τ를 제공하지 않는다.** 29148 논문은
  집계 메커니즘([0,1])만 주고 컷오프 미정. ARM/QuARS의 임계값은 문서 단위 aggregate(defect
  rate)이지 개별 요구 게이트가 아님. EARS/smell은 이진 패턴 존재 여부.
- 권고:
  1. 단일 항목 bool보다 **가중 집계** 사용
  2. LLM이 과오탐하므로 **rule 기반 smell/EARS 하드 프리필터를 LLM 앞에** 두고, τ는
     human-labeled 샘플로 보정하되 게이트에선 **precision 우선**(부족한 걸 통과시키는 것보다
     오탐이 안전)
  3. LLM이 스코어링에 관여하면 rubric/codebook + 인간검증 CoT few-shot ~20개 제공, τ 근처
     경계값은 human-in-the-loop 검토

### 열린 질문
- 검증된 τ 컷오프·항목 가중치는? → 프로젝트별 labeled set로 보정해야 함(소스 없음)
- 문서 단위로 검증된 지표(QuARS defect rate, ARM 카운트, 29148 index)를 개별 요구 단위로
  적용했을 때 성능은?
- rubric+CoT 개선(0.55→0.70)이 weak-word/단일기업 벤치 밖에서, 최신 모델로도 재현되나?

---

## 리포트 2 — 목표지향 요구공학(GORE) 적용 정당성

### 핵심 결론
**가볍게 적용하되 전면 도입은 하지 마라 — 조건부 찬성.** 가벼운 goal 요소(objectives 필드
+ clarify goal 질문 + 휴리스틱 완전성/추적성 체크)는 정당하나, 전면 KAOS/i* 모델링은 회피.

### 찬 — mechanism/이론은 탄탄 (canonical 소스 만장일치)
- GORE는 필요한 두 가지를 형식적으로 제공:
  - **(a) goal 기반 완전성 기준**: 스펙이 (도메인 속성과 함께) 모든 goal을 함의하면 완전
    (R, As, D ⊨ g). — Van Lamsweerde RE'01
  - **(b) why/how 수직 추적성**: goal이 requirement의 rationale, refinement tree가 전략목표↔
    기술요구를 연결. — Van Lamsweerde RE'01
- **Cockburn이 독립적으로 뒷받침**: "모든 완전성 개념은 요구를 생성한 goal을 기준으로
  놓인다", goal 계층으로 데이터를 조직. use-goal 레벨 = **EBP(elementary business process)**
  고도로, job-performance/boss/one-sitting test로 판정.
- **goal 도출은 원래 top-down/bottom-up 혼합("meet-in-the-middle")**: 이미 있는 요구에
  "WHY?"를 물어 상위 goal 복원이 GORE 정통. GBRAM(Antón)의 why/how/how-else 질문,
  CREWS-L'Ecritoire(Rolland)의 scenario→goal 역방향 discovery. → **FR-first에서 goal 복원은
  오용이 아님.**
- 최상위 goal은 **"대개 암묵적(most often implicit)"** 이라 요구 텍스트만으론 완전 복원
  불가 → **clarify에 goal 질문 1개는 정당**. 단 WHY-abstraction으로 부분 복원 가능하니
  질문은 보완재(질문이 유일 수단인 것은 아님).

### 반 — benefit의 실증은 얇음 (반드시 감안)
- GORE 논문 **91%가 새 방법 제안, 통제실험은 ~7%**. (SMS 246 top-cited GORE 논문 중
  63.4%만 평가 포함)
- goal모델 vs 유스케이스 **이해도 우위 실험은 재현 실패**(2017 replication: Tropos = Use
  Case, 유의차 없음).
- NFR goal모델링 실증은 전부 소규모. **산업 채택률 낮음** — 실무자 24명/12개사는 여전히
  순수 자연어+범용도구(MS Office). i*/KAOS는 다수 액터 상호작용에서 확장성 붕괴(산업 채택
  최상위 장벽).
- LLM 기반 goal↔req 추적성 자동화: feasible하나 미검증 — GPT-3.5 zero-shot precision 100%/
  **recall 78.5%**(링크 21% 조용히 누락), n=42 단일 자가보고 사례(Hassine, EASE'24).

### 산출 (a) — 적용 판단
**조건부 YES.** mechanism은 강하고 meet-in-the-middle로 goal 복원은 교과서적이나, outcome
benefit의 실증이 얇으므로 **최소 침습** + 완전성/추적성 노드는 **"formal verification이 아닌
LLM 휴리스틱 근사"** 로 라벨링.

### 산출 (b) — 최소 침습 요소별 근거
- **state의 `objectives` 필드** (상위 비즈니스 goal 1~2개): goal이 완전성·추적성의 척추.
  근거 = 완전성 기준 + why/how 추적성.
- **clarify의 goal 질문 1개**: 최상위 goal은 대개 암묵적이라 명시적 elicitation 정당.
  단 WHY-abstraction과 병행, 사용자가 이미 말했으면 중복될 수 있음(graceful).
- **step2의 completeness/traceability 체크**: objective마다 FR≥1? FR마다 objective 추적?
  고아 플래그. **휴리스틱**으로 라벨.

### 산출 (c) — 과잉설계 경고
전면 **KAOS/i* 형식 모델링 회피**. 확장성 붕괴 + 산업 채택 낮음 + 이해도 우위 미입증.
가벼운 NL-embedded goal 요소만.

### 열린 질문
- goal-referenced **체크**(전면 모델링 아님)가 실제로 누락 요구를 줄이거나 유스케이스
  품질을 높인다는 통제/산업 증거는? (goal모델 이해도 증거와는 별개, 아직 없음)
- 최신 모델이 FR/NFR 목록에서 상위 goal 1~2개를 얼마나 신뢰성 있게 복원하나? goal이
  암묵적일 때 hallucination(가짜 goal)률은?
- goal 질문 강제가 다운스트림 품질을 실제로 바꾸나, 아니면 사용자가 이미 암묵된 걸
  재진술할 뿐인가(질문 중복)?

---

## 종합 — 설계 방향 (프레이밍)

step1 게이트를 **직교하는 두 축**으로 분리한다:

| 축 | 무엇 | 복원가능? | 구현 요지 | 현 스코프 |
|---|---|---|---|---|
| **A. Concreteness** | 요구가 구문적으로 구체·검증가능한가 | 규칙/LLM로 복원 가능 | rule 하드필터 + LLM rubric 스코어러 → [0,1] → τ | **우선 구현** |
| **B. Goal coverage** | 이 요구들이 어떤 상위 목표를 위한가 | **사용자만 앎** | clarify goal 질문 + WHY-abstraction → `objectives` | 보류 |

코드에 박아야 할 정직한 경고 3개:
1. τ는 검증된 값 없음 → config 노출 + 프로젝트별 보정 전제, 게이트는 precision 우선.
2. 완전성/추적성 = LLM 휴리스틱 근사(formal verification 아님).
3. goal 질문은 사용자가 이미 말했으면 중복 → optional·graceful degradation.

---

## 주요 소스
**리포트 1 (concreteness)**
- ISO 29148 NLP framework: researchgate 385802396 / ASME IDETC-CIE2024
- EARS: Mavin et al. RE'09 (researchgate 224079416)
- Requirements smells: Femmer et al. arXiv 1611.08847 (JSS 2017)
- QuARS: Gnesi & Trentanni, CEUR-WS Vol-2376 NLP4RE19_paper07
- NASA ARM: Carlson & Laplante 2013 (Springer s11334-013-0225-8); Wilson et al. 1997
- QUS/INVEST + LLM: arXiv 2507.15157, 2403.09442 (ALAS)
- LLM 한계·회복: arXiv 2601.01952, 2604.21505, 2505.07270; Springer 978-3-031-95127-5_25

**리포트 2 (GORE)**
- Van Lamsweerde, "Goal-Oriented RE: A Guided Tour", RE'01 (webperso.info.ucl.ac.be/~avl/files/RE01.pdf)
- Cockburn, "Structuring Use Cases with Goals" (researchgate 242438651)
- GBRAM (Antón); CREWS-L'Ecritoire (Rolland, hal-00707618)
- RE quality SMS: Femmer/Montgomery et al. 2022 (s00766-021-00367-z, PMC9110500)
- Use Case vs Tropos 재현실패: researchgate 234816351
- 실무자 채택 인터뷰: s00766-023-00399-7; iStar 확장성: JSERD 2018 s40411-018-0055-3
- LLM 추적성: Hassine, EASE'24 (dl.acm.org/10.1145/3661167.3661261)
