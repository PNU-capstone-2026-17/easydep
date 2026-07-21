# Cockburn 도서 교차검증 — 프롬프트 지침·정적 체크의 근거

> 대상 도서: Alistair Cockburn, *Writing Effective Use Cases* (Addison-Wesley, 2001).
> 저작물이라 저장소에 포함하지 않는다. 각자 구한 사본을
> `materials/Usecase_Knowledge/`(gitignore 대상)에 두고 참고한다.
> 페이지는 **인쇄 페이지**(도서 헤더/푸터 기준. PDF 인덱스 = 인쇄 + 25).
>
> 목적: 파이프라인의 프롬프트 지침·정적 체크가 실제 Cockburn 근거에 있는지 항목별 교차검증.
> **근거 없는 단어사전·개수 상하한·ad-hoc 지침은 오버피팅**이므로 제거하거나 "예시일 뿐"으로
> 격하한다. (원칙: 정적=코드/의미=근거LLM/임의사전 금지.)

각 항목: **판정**(GROUNDED/PARTIAL/CONTRADICTED/UNGROUNDED) · 페이지 · 원문 인용 · **우리 처리**.

---

## A. 액터 / 목표 (step2)

**A1. primary actor = 시스템에 서비스를 요청하는 외부 stakeholder.** — GROUNDED (p.54)
> "The primary actor of a use case is the stakeholder that calls on the system to deliver one of its services."
- 우리 처리: 유지.

**A2. supporting actor = 외부 actor(외부 서비스). 시스템 내부 컴포넌트는 internal, actor 아님.** — GROUNDED (p.59)
> "A supporting actor ... is an external actor that provides a service to the system-under-design."
- Ch.4.5: 내부 컴포넌트 = "internal actors—the components within the SuD". 우리 처리: 유지(경계 리트머스 유효).

**A3. SuD는 액터인가?** — PARTIAL / 부분 CONTRADICTED (p.59)
> "The system under discussion is itself an actor—a special one. ... The SuD is not a primary or supporting actor for any use case, although it is an actor."
- 교정: **"SuD는 절대 액터가 아니다"는 부정확.** 정확히는 "primary/supporting actor가 아니다(시스템 경계 = special actor)".
- 우리 처리: **수정** — ACTORS_SYSTEM 문구를 "never a *primary or supporting* actor"로.

**A4. use case = sea-level 사용자 목표(한 자리에서 완료, coffee-break/EBP 테스트).** — GROUNDED (p.62)
> "the coffee break test ... the one person, one sitting test (2–20 minutes)" / "It corresponds to 'elementary business process'."
- 우리 처리: 유지(EBP 지침 근거 확인).

**A5. 서브펑션은 상위 사용자 목표로 병합.** — GROUNDED (p.66-67, p.91)
> clam 표기로 "a use case that needs to be merged with its calling use case" / Guideline 4 "Merge steps".
- 주의(인용 교정): Ch.13 "Create Clusters"(p.143)는 **대규모 유스케이스 집합 패키징**이지 서브펑션 병합이 아님 → p.66-67/p.91 인용.
- 우리 처리: 유지(인용만 정정).

**A6. actor generalization: 특화 액터가 일반 액터의 유스케이스를 상속.** — GROUNDED (p.240, p.226)
> "the specialized actor can perform every use case the general actor can."
- 주의: Member/Guest 같은 구체 이름은 도서에 없음(원리만). 우리 처리: 유지.

---

## B. 시나리오 / 스텝 (step3)

**B1. MSS는 분기 없는 단일 흐름. 조건은 확장으로.** — GROUNDED (p.87, p.206)
> "Put alternative behaviors in the extensions rather than in if statements in the main body."
- 우리 처리: 유지(정적 NO_BRANCHING 근거).

**B2. 각 스텝 = 하나의 sub-goal(Jacobson transaction, 4-part).** — GROUNDED (p.93, Fig 7.1)
> "Ivar Jacobson has described a step in a use case as representing a transaction ... four pieces of a compound interaction."
- 우리 처리: 유지.

**B3. MSS 스텝 수 "3~9".** — **GROUNDED — Cockburn 본인 문구 (p.208)**
> Reminder 6: "most use cases have three to nine steps in the main success scenario." (또 p.68 "3 to 10", p.69 "3 to 8 ... never ... longer than 11", p.91 "rarely ... more than 9")
- **핵심: "3-9"는 내가 만든 게 아니라 Cockburn 본인 범위.** 단 **경험 기반 관찰/권장이지 하드룰 아님**
  ("most use cases have..."). strict 제한 금지.
- 우리 처리: **우리 코드엔 step-count 로직이 아예 없음**(게이트도 neutral hint도 없음 = 가장 안전).
  대신 LLM 자기제한(padding/trimming) 방지를 위해 SPEC_SYSTEM에 "개수는 목표 아님, 각 스텝의 level로
  판단"(Guideline 6, p.93)을 명시.

**B4. 스텝은 의도를 보이고 UI 동작이 아님. 주어=actor/System.** — GROUNDED (p.92)
> Guideline 5: "Show the Actor's Intent, Not the Movements".
- 우리 처리: 유지.

**B5. "Validate, don't check whether".** — GROUNDED (p.95)
> Guideline 7. "Replace 'checks whether the password is correct' with 'verifies that the password is correct'."
- 우리 처리: 유지(precondition 재검증 금지와 연결).

---

## C. Black-box / GUI (step3) — **오버피팅 위험 최대 지점**

**C1. UI 용어 배제.** — 원칙 GROUNDED / **명문 단어목록은 PARTIAL** (p.209, p.91-92)
> Reminder 7 "Keep the GUI Out"의 나쁜 예: "displays the Login **screen** with **fields** ... **clicks** OK ... displays the Main **screen**".
- **중요: Cockburn은 금지 단어 체크리스트를 만들지 않음.** 예시 안에 등장하는 단어는 **screen, field(s),
  click(s)/click OK(=button), types, tab key, Enter button** 정도. **"page", "menu", "form"은 그의 회피
  예시에 없음**(오히려 종이 "form"은 UI-design 논의 p.177에서 긍정적으로 등장).
- 우리 처리: **수정** — `_UI_TERMS`를 **Cockburn 예시 단어로 한정**(screen/field/button/click/type/tab/enter)하고,
  코드 주석에 "Cockburn 예시일 뿐 명문 목록 아님(p.209)"으로 격하. page/menu/form/window/dialog/checkbox/link 제거.

**C2. black-box: 내부 설계/저장/프로토콜 미언급.** — 원칙 GROUNDED / **기술어 목록 UNGROUNDED** (p.174, p.59)
> "Use cases provide all and only black-box behavioral requirements ..." / "we treat the system ... as a black box ... Internal actors are purposely not mentioned."
- **중요: Cockburn은 Database/SQL/HTTP/cache/server 같은 금지 기술어 목록을 두지 않음.**
- 우리 처리: **유지(단 코드 사전 없음)** — 우리는 내부컴포넌트 누출을 **정적 사전이 아니라 의미 LLM validator**로 판정
  하므로 규율에 부합. SPEC_SYSTEM의 예시("order store" 등)는 프롬프트 예시일 뿐(코드 규칙 아님) → OK.

---

## D. 확장 (step3)

**D1. 확장 조건 = 시스템이 detect한 것.** — GROUNDED (p.102, Guideline 11). 우리 처리: 유지.

**D2. Rollup(스텝 범위/전역 마커).** — GROUNDED (p.105, p.103 "2–5a", "*a"). 우리 처리: 유지(branch_step/label).

**D3. 확장 종료 유형.** — GROUNDED (p.106-107)
> "the scenario fragment ends in one of the following four ways": ①분기 스텝이 고쳐져 **재합류** ②**재시도** ③**전체 실패** ④**다른 경로로 성공(alternative success)**.
- 우리의 `outcome: resume|alternate_success|fail`은 ①/④/③에 대응(②retry는 resume의 변형). 우리 처리: 유지.

**D4. 없는 복구능력 지어내지 말 것.** — PARTIAL (p.107, p.100)
> "notify user, terminates user session"는 있으나, Cockburn은 오히려 **올바른 대응을 조사**하고 필요시 복구
> 유스케이스/규칙을 추가하라고 함(p.100 "System Restarts after Network Failure").
- 우리 처리: **완화** — "지어내지 마라"를 약하게. (프롬프트/validator에서 강한 금지는 근거 약함.)

**D5. 실패는 인라인 확장으로. 재사용/가독성 때만 별도 유스케이스로.** — GROUNDED (p.109-110)
> "Keeping an extension inside the use case generally makes better economic sense. Two situations will drive you to create a new use case ... used in several places ... hard to read."
- 우리 처리: 유지(step4 실패-승격 금지 근거 확인).

---

## E. 관계 (step4) — **가장 큰 교정**

**E1. INCLUDE: base의 액션 스텝이 공유 sub-use-case 이름을 호출. 방향 base→included.** — GROUNDED (p.234, p.117)
> "A base use case includes an included use case if an action step in the base use case calls out the included use case's name."
- 우리 처리: 유지(include는 "스텝으로 나타나는 명명된 sub-goal").

**E2. 공유 인증/로그인 = PRECONDITION + 별도 선행 유스케이스. include/extend 아님.** — **GROUNDED — 결정적 (p.81)**
> "The user has already logged on and has been validated. Generally, a precondition indicates that some other use case has already run to set it up. ... Place an Order relies on a precondition, being logged on. I ... create a higher-level use case that mentions Place an Order and Log On."
- **핵심: 여러 goal이 공유하는 인증은 각 goal에서 그은 `<<include>>`가 아니라 precondition(선행 Log On 유스케이스)이다.**
  → "인증을 include로 팩토링"은 **Cockburn 안티패턴.**
- 우리 처리: **수정** — RELATIONSHIPS 프롬프트에서 "Authenticate include" 예시 제거, "공유 로그인/인증은
  precondition(선행 Log On), include/extend 금지" 명시.

**E3. EXTEND: 확장 UC가 base를 이름으로 지목·조건 소유. base는 모름. sparingly.** — GROUNDED (p.235, p.114-116)
> "An extending ... use case extends a base use case by naming the base ... The base use case does not name the extending one." / "Create extension use cases only when you need to."
- 우리 처리: 유지.

**E4. 순차 "A 다음 B"·일상 실패는 extend 아님.** — GROUNDED (p.117, p.237)
> Alan Williams 원리(p.117): 트리거 책임이 base에 있으면 include, 확장 UC에 있으면 extend.
- 우리 처리: 유지(실패-승격 금지 + 순차의존은 precondition).

**E5. Generalization은 위험(hazards) — 신중히.** — GROUNDED (p.240-241)
> "the problem with the generalizes relation is that the professional community has not yet reached an understanding ..." + "closing a big deal" 예시.
- 우리 처리: 유지(parent_actor 결정론 보강은 안전한 부분집합; UC 일반화는 신중).

**E6. 레이아웃: 높은 goal 위로, extend 아래로, primary 왼쪽·supporting 오른쪽.** — GROUNDED (p.235, p.236, p.243)
> Guideline 18: "Supporting Actors on the Right — ... place all the primary actors on the left side ... leaving the right side for the supporting (secondary) actors."
- 우리 처리: 유지 — **P1 렌더링(supporting 오른쪽) 근거 확인됨.**

**E7. 자동 시스템 결과(로깅/감사/암호화/영수증)는 내부 success guarantee — 스텝도 include도 아님.** — GROUNDED (p.64)
> Cockburn: 자동 결과(영수증 발송, 로깅)는 driving goal의 내부 success guarantee이지 별도 유스케이스가 아니다.
- 발견 경위: E2 교정 후 telehealth에서 LLM이 "Record audit log entry"/"Encrypt data for storage"를
  모든 UC에서 include(20개) → 또 다른 교차절단 over-factoring(안티패턴).
- 근본 원인: NFR(감사·암호화)이 step3 시나리오에 **스텝으로 새어듦** → 마이닝이 공유스텝으로 잡음.
- 우리 처리: **수정** — (a) SPEC_SYSTEM: 자동결과·교차절단 품질(logging/audit/encryption/confirmation)은
  스텝 아닌 success_guarantee/minimal_guarantee로. (b) RELATIONSHIPS include: 교차절단 결과는 include 제외.

**F1. include vs extend 보수성.** — PARTIAL, **비대칭 (p.206-207, p.116, p.243)**
> Reminder 4: "As a first rule of thumb, always use the includes relation ... less confusion ..." / item 9: "Create extension use cases only under very selected circumstances."
- **핵심: include는 권장 기본, extend/generalize만 sparingly.** → 내 "prefer zero includes / returning zero is common"은 **틀림.**
- 우리 처리: **수정** — include는 진짜 공유 sub-goal에 적극 사용(단 인증=precondition 제외). "prefer zero includes" 삭제. extend는 sparingly 유지.

---

## 오버피팅 플래그 (근거 없음 → 격하/문서화)

| 항목 | 판정 | 처리 |
|---|---|---|
| `_MAX_INCLUDE_HINTS=6` (include 힌트 개수 상한) | 도서 근거 없음(순수 엔지니어링) | **격하** — 출력 규칙 아님(프롬프트 크기 가드)임을 주석 명시. 의미 필터(precondition 제외·nameable capability)는 프롬프트가 담당 |
| `_UI_TERMS`에 page/menu/form 등 | Cockburn 예시 아님 | **제거** — 예시 단어로 한정(C1) |
| 내부 기술어(Database/SQL...) 코드 사전 | 애초에 없음 | 유지(의미 LLM으로만 판정) |
| "prefer zero includes" | F1 모순 | **삭제** |
| "Authenticate include" 예시 | E2 안티패턴 | **삭제** |
| MSS 3-9 | Cockburn 본인(B3) | **유지**(게이트 아님) |
| 확장 outcome 3종 | D3 근거 | 유지 |
| max_repair_iters / max_coverage_iters / spec_concurrency | 프로세스 노브(유스케이스 규칙 아님) | 유지(도서 무관, 정당) |

---

## 결론 — 코드 교정 목록 (이 문서 기반)
1. **step4 RELATIONSHIPS_SYSTEM**: 인증=precondition 명시, "Authenticate include" 제거, "prefer zero includes" 삭제, include는 적극(단 precondition 제외)·extend sparingly. (E2, F1)
2. **step2 ACTORS_SYSTEM**: "never a *primary or supporting* actor" 문구. (A3)
3. **step3 `_UI_TERMS`**: Cockburn 예시 단어로 한정 + "예시일 뿐" 주석. (C1)
4. **step4 `_MAX_INCLUDE_HINTS`**: 엔지니어링 가드로 주석 격하. (오버피팅 플래그)
5. **step3 확장 프롬프트**: "없는 복구 지어내지 마라" 완화. (D4)
