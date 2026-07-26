"""규칙 지식베이스 — 이 에이전트가 무엇을 결함이라 부르는지, 그 근거는 무엇인지.

## 무엇이 없어서 이게 필요했나

규칙이 세 군데에 흩어져 있었다.

  - `prompts.py`의 시스템 프롬프트 산문 — LLM만 읽는다
  - `step3_specifications.py`의 정규식·상수 — 코드만 읽는다
  - `docs/ARCHITECTURE.md` §4.2의 페이지 인용 — 사람만 읽는다

같은 규칙이 세 곳에 다른 형태로 적혀 있으면 갈라진다. 실제로 갈라졌다: UI 단어 목록이
"예시일 뿐 완전목록이 아니다"라는 사실은 코드 주석에만 있었고, **검증 지적에는 실리지
않았다.** 사용자는 `p.81`(책이 그렇게 적었다)과 UI 단어 8개(우리가 예시에서 일반화했다)를
같은 무게로 읽는다.

그래서 규칙을 데이터로 한 곳에 모은다. 프롬프트도 검출기도 리포트도 여기서 파생된다.

## 책 본문은 여기 없다

Cockburn의 *Writing Effective Use Cases*는 저작물이고 저장소에서 지워졌다(`d1a7ec5`,
filter-branch로 히스토리 전체에서). 그래서 이 파일에 담는 것은 **우리 표현의 규범 문장 +
인용 좌표 + 좌표를 대조할 짧은 열쇠 단어**이고, 본문은 담지 않는다.

인용은 손으로 옮겨 적는 것이라 틀린다. 그래서 `pages`·`probe`로 **기계가 대조할 수 있게**
해 두고, 로컬 사본을 가진 사람이 `verify_citations`로 확인한다. 2026-07-26에 그 검사가
틀린 인용 둘을 잡았다(`p.64`·`p.207` — 그 모듈 docstring 참고).

## 심각도가 셋인 이유

`NON_RULE`이 있는 것이 핵심이다. "스텝 3~9개"와 include 힌트 상한은 **규칙이 아니라는
사실 자체가 지켜야 할 지식**이다. 적어 두지 않으면 다음 사람이 관찰을 규칙으로
승격시킨다 — `docs/ARCHITECTURE.md`가 "임의 사전 금지(오버피팅 방지)"로 경계한 것이
정확히 그것이고, 그 경계가 지금까지 주석에만 있었다.

## 아직 없는 것

  - `refine_requirements` 단계의 규칙이 하나도 없다. 구체성 rubric 조사가 저장소 밖으로
    나갔고(`e10c527`), 근거 없는 규칙을 지어 넣지는 않는다.
  - 생성 프롬프트(`SPEC_SYSTEM`·`RELATIONSHIPS_SYSTEM`)는 아직 산문 그대로다. 검증
    프롬프트만 이 지식베이스에서 조립한다 — 인용이 산출물에 실려 값어치가 나는 곳이
    검증 쪽이고, 생성 프롬프트를 같은 판에 갈아엎으려면 회귀를 잡을 평가 세트가 먼저
    있어야 한다(그건 아직 없다).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.requirements.knowledge import basis

# --- 심각도 -----------------------------------------------------------------
#: 위반이면 검증이 지적한다.
DEFECT = "defect"
#: 생성 쪽 지침. 위반을 지적하지는 않는다(판정 기준이 되기에는 무르다).
GUIDANCE = "guidance"
#: **규칙이 아니라는 기록.** 어디에서도 강제하지 않는다. 관찰이나 공학적 가드를
#: 규칙으로 승격시키지 않기 위해 둔다.
NON_RULE = "non_rule"

SEVERITIES = (DEFECT, GUIDANCE, NON_RULE)

# --- 누가 판정하는가 ---------------------------------------------------------
# `DEFECT`인데 판정하는 곳이 없는 규칙이 실제로 있다. 그 사실을 `None` 하나로 뭉개면
# "LLM이 판정한다"와 "아무도 판정하지 않는다"가 같은 값이 된다 — 검증 프롬프트를 이
# 목록에서 조립하는 순간 후자가 전자로 조용히 승격한다.
#: `knowledge/detectors.py`의 결정론 검출기.
JUDGED_DETECTOR = "detector"
#: LLM 의미 검증자(검증 프롬프트에 규칙 문장이 들어간다).
JUDGED_VALIDATOR = "validator"
#: 단계 노드가 직접 결정론으로 구현한다(커버리지·참조 드롭).
JUDGED_STAGE = "stage_code"
#: **판정하는 곳이 없다.** 규칙은 적혀 있고 검증은 없다는 사실을 드러낸다.
JUDGED_NOWHERE = "nowhere"

JUDGES = (JUDGED_DETECTOR, JUDGED_VALIDATOR, JUDGED_STAGE, JUDGED_NOWHERE)

# --- 단계 -------------------------------------------------------------------
# 이름은 `agent/stages.py`의 `group`과 같다. import하지 않는 이유는 순환이다
# (stages → steps → knowledge). 두 목록이 맞는지는 테스트가 확인한다.
MODEL_USE_CASES = "model_use_cases"
WRITE_SPECIFICATIONS = "write_specifications"
DRAW_DIAGRAM = "draw_diagram"

_BOOK = "Writing Effective Use Cases"


@dataclass(frozen=True)
class Rule:
    """규칙 하나. **규범 문장은 우리 표현이고, 인용은 좌표다**(본문 아님)."""

    id: str
    stage: str
    severity: str
    #: 규범 문장. 검증 프롬프트에 그대로 들어가므로 영어로 쓴다.
    statement: str
    #: 확인 좌표. `Writing Effective Use Cases, p.64` 처럼.
    citation: str
    #: 근거 라벨(`basis.BASIS_OF_EVIDENCE`에 등록돼야 한다).
    evidence: str
    #: 짐작인 규칙은 반드시 있어야 한다 — **출처의 한계**를 적는다(위반의 의심이 아니다).
    caveat: str | None = None
    #: 이 규칙을 판정하는 곳. `DEFECT`는 반드시 밝힌다(없으면 `JUDGED_NOWHERE`).
    judged_by: str = JUDGED_NOWHERE
    #: 결정론 검출기 이름(`detectors.py`에 등록). `judged_by`가 검출기일 때만 있다.
    detector: str | None = None
    #: 이 결함을 **낸 단계**의 논리 이름(`agent/stages.py`의 `key`). 되돌릴 대상이다.
    #:
    #: `stage`(그룹)와 다른 사실이다. `model_use_cases` 그룹에는 되돌릴 수 있는 단계가
    #: 둘(actors·use_cases) 있고, 어느 쪽이 낸 결함인지는 규칙마다 다르다. 규칙 id 접두사로
    #: 짐작할 수도 있지만, 라우팅 표를 접두사 규약에 걸어 두면 이름을 바꾸는 순간 조용히
    #: 어긋난다. `DEFECT`만 갖는다 — 나머지는 되돌릴 대상이 아니다.
    owner: str | None = None
    #: 인용을 **기계로 확인할** 인쇄 페이지 번호. 책 근거인 규칙에만 있다.
    pages: tuple[int, ...] = ()
    #: 그 페이지에 있어야 하는 짧은 단어들(소문자). 좌표가 맞는지 보는 열쇠일 뿐,
    #: 본문을 옮겨 담는 자리가 아니다 — 저작물을 저장소에 넣지 않기 위한 경계다.
    probe: tuple[str, ...] = ()

    @property
    def hedged(self) -> bool:
        """지적할 때 출처의 한계를 함께 밝혀야 하는가."""
        return basis.needs_hedge(self.evidence)

    @property
    def from_book(self) -> bool:
        """인용이 도서 좌표인가(→ 로컬 사본으로 대조할 수 있어야 한다)."""
        return self.evidence.startswith("cockburn-")

    @property
    def short_citation(self) -> str:
        """책 제목을 뗀 좌표(`p.64`). 지적 문구에 붙이기 위한 것."""
        return self.citation.removeprefix(f"{_BOOK}, ")

    @property
    def tag(self) -> str:
        """지적 문구 꼬리표. 짐작인 규칙은 그 사실이 함께 붙는다."""
        parts = [self.id, self.short_citation]
        if self.hedged:
            parts.append("우리 판단")
        return f"[{' · '.join(parts)}]"

    def prompt_line(self) -> str:
        """검증 프롬프트 한 줄. 근거의 성격까지 모델에게 알린다.

        고지 문구는 라벨마다 다르다(`basis.prompt_note`) — "책이 말한 적 없다"와 "책의
        원칙이지만 페이지를 못 댄다"를 한 문구로 뭉개면 둘 중 하나는 거짓이 된다.
        """
        note = basis.prompt_note(self.evidence)
        source = f"{self.citation} — {note}" if note else self.citation
        return f"- {self.id} ({source}): {self.statement}"


# ---------------------------------------------------------------------------
# 규칙 목록
# ---------------------------------------------------------------------------
RULES: tuple[Rule, ...] = (
    # --- 2단계: 액터 / 유스케이스 ---------------------------------------
    Rule(
        id="actors.sud-is-not-an-actor",
        stage=MODEL_USE_CASES,
        severity=DEFECT,
        statement=(
            "The system under design is never an actor. Actors are external to it — "
            "primary actors initiate, supporting actors are called upon."
        ),
        citation=f"{_BOOK}, p.59 (Ch. 4, Stakeholders and Actors)",
        evidence="cockburn-page",
        owner="actors",
        judged_by=JUDGED_VALIDATOR,
        pages=(59,),
        probe=("actor", "system under design"),
    ),
    Rule(
        id="actors.derived-from-functional-requirements",
        stage=MODEL_USE_CASES,
        severity=GUIDANCE,
        statement="Derive actors from functional requirements only.",
        citation="easydep convention",
        evidence="project-convention",
        caveat="우리 규약이다 — 책이 액터의 출처를 요구사항 유형으로 제한한 것은 아니다.",
    ),
    Rule(
        id="usecases.user-goal-level",
        stage=MODEL_USE_CASES,
        severity=GUIDANCE,
        statement=(
            "Cluster use cases at the user-goal (elementary business process) level; "
            "absorb subfunction requirements into the use case that covers them."
        ),
        citation=f"{_BOOK}, p.62 (Ch. 5, Three Named Goal Levels)",
        evidence="cockburn-page",
        pages=(62,),
        probe=("user goal",),
    ),
    Rule(
        id="usecases.nfr-is-a-constraint",
        stage=MODEL_USE_CASES,
        severity=GUIDANCE,
        statement=(
            "A non-functional requirement is not a use case. Attach it as a constraint "
            "on the use cases it qualifies."
        ),
        citation="easydep convention",
        evidence="project-convention",
        caveat="우리 규약이다. NFR을 어디에 붙일지는 책이 정하지 않는다.",
    ),
    Rule(
        id="usecases.every-functional-requirement-covered",
        stage=MODEL_USE_CASES,
        severity=DEFECT,
        statement=(
            "Every functional requirement must be covered by at least one use case, "
            "and every requirement id referenced must exist."
        ),
        citation="easydep convention (traceability)",
        evidence="project-convention",
        caveat="추적성을 위해 우리가 정한 규칙이다. 판정은 `step2_usecases.check_coverage`가 한다.",
        owner="use_cases",
        judged_by=JUDGED_STAGE,
    ),
    # --- 3단계: 명세 ------------------------------------------------------
    Rule(
        id="spec.black-box-no-ui-mechanics",
        stage=WRITE_SPECIFICATIONS,
        severity=DEFECT,
        statement=(
            "Steps must not name user-interface mechanics (screens, fields, buttons, "
            "clicks, tabs). Say what the system does for the actor."
        ),
        citation=f'{_BOOK}, p.209 (Reminder 7: "Keep the GUI Out")',
        evidence="cockburn-example",
        caveat=(
            "단어 목록은 그가 **예로 든** UI 용어일 뿐이다. 책은 금지 단어목록을 "
            "명문화하지 않았으므로 이 목록은 완전하지 않다 — 없는 단어로도 위반할 수 있다."
        ),
        owner="specs",
        judged_by=JUDGED_DETECTOR,
        detector="ui_terms",
        pages=(209,),
        probe=("reminder 7", "gui"),
    ),
    Rule(
        id="spec.black-box-no-internal-components",
        stage=WRITE_SPECIFICATIONS,
        severity=DEFECT,
        statement=(
            "Never name an internal service, engine, store, cache, queue, or database — "
            "not even disguised in business words. Say what the system does, not which "
            "internal part does it."
        ),
        citation=f"{_BOOK}, p.41 (Ch. 3, Scope — black box)",
        evidence="cockburn-extrapolated",
        caveat=(
            "black-box 원칙과 페이지는 확인했다. 다만 금지 대상 목록(service·engine·store·"
            "cache·queue·database)은 그 원칙에서 우리가 끌어낸 것이다."
        ),
        owner="specs",
        judged_by=JUDGED_VALIDATOR,
        pages=(41,),
        probe=("black box",),
    ),
    Rule(
        id="spec.no-branching-in-a-step",
        stage=WRITE_SPECIFICATIONS,
        severity=DEFECT,
        statement=(
            "A step states one thing that happens. Branching words (if/else) do not "
            "belong in a step — split the branch into an extension."
        ),
        citation=f"{_BOOK}, Ch. 7 (Scenarios and Steps), p.88~",
        evidence="cockburn-chapter",
        owner="specs",
        judged_by=JUDGED_DETECTOR,
        detector="branch_words",
        pages=(88,),
        probe=("scenarios and steps",),
    ),
    # 이 규칙은 하루 안에 강등됐다가 복구됐다. 그 경위가 측정 방법에 대한 교훈이다:
    #   1. `toystore` 한 데이터셋에서 "안정된 판정 0건"이 나와 GUIDANCE로 내렸다.
    #   2. 도메인 5종에서 **단독 프로브**로 다시 재니 안정 4 / 흔들림 15 / 없음 6이었다.
    # 조합 프롬프트(규칙 6개를 한 번에)에서는 신호가 묻히고, 단독으로 물으면 나온다.
    # **"신호가 없다"는 주장은 측정 조건을 함께 말해야 한다** — 안 그러면 프롬프트 구성을
    # 규칙의 성질로 착각한다. (`docs/requirements-agent-improvements.md` §9)
    Rule(
        id="spec.no-hidden-branching",
        stage=WRITE_SPECIFICATIONS,
        severity=DEFECT,
        statement=(
            "A step whose behaviour depends on an unstated outcome is still a branch, "
            "even without the word 'if'. Split it into a separate extension."
        ),
        citation=f"{_BOOK}, Ch. 7 (Scenarios and Steps), p.88~",
        evidence="cockburn-chapter",
        owner="specs",
        judged_by=JUDGED_VALIDATOR,
        pages=(88,),
        probe=("scenarios and steps",),
    ),
    Rule(
        id="spec.no-control-tokens-in-prose",
        stage=WRITE_SPECIFICATIONS,
        severity=DEFECT,
        statement=(
            "Scenario-ending tokens ('Success!', 'Fail!') are not prose. Express the "
            "ending in the outcome field."
        ),
        citation=f"{_BOOK}, p.47-49 (scenario endings)",
        evidence="cockburn-page",
        owner="specs",
        judged_by=JUDGED_DETECTOR,
        detector="control_tokens",
        pages=(47, 48, 49),
        probe=("fail!",),
    ),
    Rule(
        id="spec.one-subgoal-per-step",
        stage=WRITE_SPECIFICATIONS,
        severity=GUIDANCE,
        statement="Each step carries exactly one sub-goal — one transaction.",
        citation=f'{_BOOK}, Guideline 6 ("Include a Reasonable Set of Actions"), p.93',
        evidence="cockburn-guideline",
        pages=(93,),
        probe=("guideline 6", "transaction"),
    ),
    Rule(
        id="spec.step-count-is-not-a-target",
        stage=WRITE_SPECIFICATIONS,
        severity=NON_RULE,
        statement=(
            "The 3-to-9 step range is an observation about readable use cases, not a "
            "limit. Never pad or trim steps to hit a count; judge each step's level."
        ),
        citation=f'{_BOOK}, p.208 (Reminder 6: "Get the Goal Level Right")',
        evidence="cockburn-observation",
        caveat=(
            "책이 관찰로 적은 범위다 — 규칙이 아니다. 개수로 게이트하면 개수를 맞추려고 "
            "내용을 늘리거나 자른다. **어디에서도 강제하지 않는다.**"
        ),
        pages=(208,),
        probe=("three to nine",),
    ),
    Rule(
        id="spec.consequence-is-a-guarantee",
        stage=WRITE_SPECIFICATIONS,
        # 강등 → 복구. 경위는 위 `spec.no-hidden-branching` 주석과 같다(단독 프로브
        # 4도메인에서 안정 3 / 흔들림 6 / 없음 11).
        severity=DEFECT,
        statement=(
            "Automated system consequences and cross-cutting quality concerns (logging, "
            "auditing, encrypting stored data, sending a receipt) are internal success "
            "guarantees, never main-scenario steps."
        ),
        # ⚠ 2026-07-26 정정: 예전 인용 `p.64`는 틀렸다. 그 페이지는 Ch. 5(Three Named
        # Goal Levels)이고 guarantee를 다루지 않는다. 보증은 Ch. 6이고 "Minimal
        # Guarantees" 절이 p.83이다(로컬 사본으로 확인).
        citation=f"{_BOOK}, Ch. 6 (Preconditions, Triggers, and Guarantees), p.83",
        evidence="cockburn-extrapolated",
        caveat=(
            "보증이 사후조건의 자리라는 것은 Ch. 6에서 확인했다. 다만 '자동결과(로깅·감사·"
            "암호화·확인 발송)는 스텝이 아니라 보증'이라는 구체적 적용은 우리가 끌어낸 것이다."
        ),
        owner="specs",
        judged_by=JUDGED_VALIDATOR,
        pages=(83,),
        probe=("minimal guarantee",),
    ),
    Rule(
        id="spec.no-precondition-recheck",
        stage=WRITE_SPECIFICATIONS,
        severity=DEFECT,
        statement=(
            "A precondition is a state the use case may assume. A main-scenario step "
            "must not re-verify what a precondition already guarantees."
        ),
        citation=f"{_BOOK}, p.81 (preconditions need not be checked)",
        evidence="cockburn-page",
        owner="specs",
        judged_by=JUDGED_VALIDATOR,
        pages=(81,),
        probe=("precondition", "not be checked"),
    ),
    Rule(
        id="spec.no-scope-creep",
        stage=WRITE_SPECIFICATIONS,
        severity=DEFECT,
        statement=(
            "A step, condition, or handling must not invent a capability that is absent "
            "from the given functional requirements."
        ),
        citation="easydep convention (hallucination guard)",
        evidence="project-convention",
        caveat="환각을 막기 위해 우리가 정한 규칙이다. 책의 규칙이 아니다.",
        owner="specs",
        judged_by=JUDGED_VALIDATOR,
    ),
    Rule(
        id="spec.remerge-re-establishes-state",
        stage=WRITE_SPECIFICATIONS,
        severity=DEFECT,
        statement=(
            "An extension that resumes the main scenario must actually re-establish the "
            "state the resume step assumes."
        ),
        citation=f"{_BOOK}, Ch. 8 (Extensions), rejoin at p.106",
        evidence="cockburn-extrapolated",
        caveat=(
            "확장이 주 시나리오로 복귀한다(rejoin)는 개념과 페이지는 확인했다. "
            "'복귀 지점이 가정하는 상태를 실제로 회복해야 한다'는 요구는 우리가 세운 것이다."
        ),
        owner="specs",
        judged_by=JUDGED_VALIDATOR,
        pages=(106,),
        probe=("rejoin",),
    ),
    Rule(
        id="spec.extension-reference-integrity",
        stage=WRITE_SPECIFICATIONS,
        severity=DEFECT,
        statement=(
            "Every branch_step and resume_at_step must name a step that exists in the "
            "main scenario, and resume_at_step is set only when the outcome is 'resume'."
        ),
        citation="easydep convention (schema integrity)",
        evidence="project-convention",
        caveat="우리 스키마의 무결성 규칙이다 — 위반은 결정론적으로 참이지만, 규범을 정한 것은 우리다.",
        owner="specs",
        judged_by=JUDGED_DETECTOR,
        detector="extension_refs",
    ),
    Rule(
        id="spec.contract-completeness",
        stage=WRITE_SPECIFICATIONS,
        severity=DEFECT,
        statement=(
            "A specification states its preconditions and its success guarantee; an "
            "empty contract is not a specification."
        ),
        citation="easydep convention (fully-dressed template)",
        evidence="project-convention",
        caveat="풀 템플릿의 어느 칸을 필수로 볼지는 우리가 정했다.",
        owner="specs",
        judged_by=JUDGED_DETECTOR,
        detector="contract_fields",
    ),
    # --- 4단계: 관계 / 다이어그램 -----------------------------------------
    Rule(
        id="rel.shared-authentication-is-a-precondition",
        stage=DRAW_DIAGRAM,
        severity=DEFECT,
        statement=(
            "A shared login/authentication/authorization sub-goal is a precondition set "
            "up by a prior use case, not an <<include>> drawn from every use case."
        ),
        citation=f"{_BOOK}, p.81",
        evidence="cockburn-page",
        owner="relationships",
        judged_by=JUDGED_VALIDATOR,
        pages=(81,),
        probe=("precondition", "log"),
    ),
    Rule(
        id="rel.consequence-is-not-an-include",
        stage=DRAW_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Cross-cutting internal consequences (logging, auditing, encrypting, sending "
            "confirmations) are success guarantees, never included sub-goals."
        ),
        # ⚠ 2026-07-26 정정: 예전 인용 `p.64`는 틀렸다(위 spec 쌍둥이 규칙 참고).
        citation=f"{_BOOK}, Ch. 6 (Preconditions, Triggers, and Guarantees), p.83",
        evidence="cockburn-extrapolated",
        caveat=(
            "보증이 사후조건의 자리라는 것은 Ch. 6에서 확인했다. 그것이 곧 "
            "'include로 뽑지 말라'는 뜻이라는 적용은 우리가 끌어낸 것이다."
        ),
        owner="relationships",
        judged_by=JUDGED_VALIDATOR,
        pages=(83,),
        probe=("minimal guarantee",),
    ),
    Rule(
        id="rel.include-is-the-default-relationship",
        stage=DRAW_DIAGRAM,
        severity=GUIDANCE,
        statement=(
            "<<include>> is the first rule of thumb for a genuine shared sub-goal; use "
            "extend and generalization sparingly."
        ),
        # ⚠ 2026-07-26 정정: 예전 인용 `p.207`은 틀렸다. 그 페이지는 Reminder 5
        # ("Who Has the Ball?")이고, 거기 있는 "rule of thumb"은 다른 이야기다 —
        # 아마 그 단어로 찾다가 잘못 붙었다. 관계를 다루는 곳은 Ch. 10이다.
        citation=f"{_BOOK}, Ch. 10 (Linking Use Cases), p.114-117",
        evidence="cockburn-extrapolated",
        caveat=(
            "관계를 다루는 장과 페이지는 확인했다. 다만 'include가 기본이고 나머지는 "
            "아껴 쓴다'는 우선순위는 우리가 정리한 것이다."
        ),
        pages=(117,),
        probe=("include", "extend"),
    ),
    Rule(
        id="rel.failures-stay-inline-extensions",
        stage=DRAW_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Failures, errors, and cancellations stay inline extensions of their use "
            "case. Do not promote them to <<extend>> or to a derived use case."
        ),
        citation=f"{_BOOK}, p.109 (Ch. 8, Extensions)",
        evidence="cockburn-page",
        owner="relationships",
        judged_by=JUDGED_VALIDATOR,
        pages=(109,),
        probe=("extension", "fail"),
    ),
    Rule(
        id="rel.extend-is-only-optional-interruption",
        stage=DRAW_DIAGRAM,
        severity=DEFECT,
        statement=(
            "<<extend>> is for genuinely optional, interrupting, electively triggered "
            "behaviour — not for failure cases and not for ordinary 'after A do B' order."
        ),
        citation=f"{_BOOK}, Ch. 10 (Linking Use Cases), Extension Use Cases at p.115",
        evidence="cockburn-extrapolated",
        caveat=(
            "확장 유스케이스를 다루는 절과 페이지는 확인했다. 'optional·interrupting·"
            "electively triggered에만 쓴다'는 좁힘은 우리가 세운 것이다."
        ),
        owner="relationships",
        judged_by=JUDGED_VALIDATOR,
        pages=(115,),
        probe=("extension use cases",),
    ),
    Rule(
        id="rel.generalization-keeps-meaning",
        stage=DRAW_DIAGRAM,
        severity=DEFECT,
        statement="A generalization must not invert or confuse the parent/child meaning.",
        citation="OMG UML, generalization semantics",
        evidence="uml-spec",
        owner="relationships",
        judged_by=JUDGED_VALIDATOR,
    ),
    Rule(
        id="rel.supporting-actors-on-the-right",
        stage=DRAW_DIAGRAM,
        severity=GUIDANCE,
        statement=(
            "Draw primary actors to the left of the system boundary and supporting "
            "actors to the right."
        ),
        citation=f'{_BOOK}, Guideline 18 ("Supporting Actors on the Right"), p.243',
        evidence="cockburn-guideline",
        pages=(243,),
        probe=("guideline 18", "supporting actors on the right"),
    ),
    Rule(
        id="rel.reference-integrity",
        stage=DRAW_DIAGRAM,
        severity=DEFECT,
        statement=(
            "Every relationship must reference an actor or use case that exists. A "
            "relationship naming something that does not exist is dropped, not drawn."
        ),
        citation="easydep convention (schema integrity)",
        evidence="project-convention",
        caveat="우리 규약이다. 판정은 `step4_diagram.identify_relationships`가 결정론으로 한다.",
        owner="relationships",
        judged_by=JUDGED_STAGE,
    ),
    Rule(
        id="rel.include-hint-cap",
        stage=DRAW_DIAGRAM,
        severity=NON_RULE,
        statement=(
            "Capping include hints at six is a prompt-size guard, not a limit on how "
            "many includes the model may produce."
        ),
        citation="app/requirements/agent/steps/step4_diagram.py (_MAX_INCLUDE_HINTS)",
        evidence="engineering-guard",
        caveat=(
            "순수 공학적 가드다 — 규칙이 아니고 판정 근거로 쓰면 안 된다. 출력 개수를 "
            "제한하지 않는다."
        ),
    ),
)


_BY_ID: dict[str, Rule] = {r.id: r for r in RULES}


def rule(rule_id: str) -> Rule:
    """id로 규칙 하나. 없으면 `KeyError` — 없는 규칙을 인용하는 것은 오류다."""
    return _BY_ID[rule_id]


def known_ids() -> frozenset[str]:
    """존재하는 규칙 id 전부. LLM이 댄 인용을 대조하는 데 쓴다."""
    return frozenset(_BY_ID)


def rules_for(stage: str, severity: str | None = None) -> tuple[Rule, ...]:
    """단계(+심각도)로 규칙을 고른다. 선언 순서를 유지한다."""
    return tuple(
        r for r in RULES
        if r.stage == stage and (severity is None or r.severity == severity)
    )


def rule_of(issue: str) -> str | None:
    """지적 문구가 인용한 규칙 id. 못 찾으면 None.

    꼬리표는 우리가 만든다(`Rule.tag` → `[<id> · <좌표> …]`)므로 정확히 맞춰 찾는다.
    문구를 파싱하는 대신 **아는 id로 조회**하는 방향이라, 새 규칙이 생겨도 이 함수는 그대로다.

    지식베이스에 두는 이유: 채점(`evaluation/scorecard.py`)과 되돌리기 라우팅
    (`agent/supervisor.py`)이 같은 되읽기를 필요로 한다. 두 벌이면 갈라진다.
    """
    for rule_id in _BY_ID:
        if f"[{rule_id} ·" in issue:
            return rule_id
    return None


def owner_of(issue: str) -> str | None:
    """이 지적을 낸 단계의 논리 이름. 규칙을 못 찾거나 책임 단계가 없으면 None."""
    rule_id = rule_of(issue)
    if rule_id is None:
        return None
    return _BY_ID[rule_id].owner


def tag_of(rule_id: str) -> str:
    """지적 문구에 붙일 꼬리표. 모르는 id는 그 사실을 드러낸다.

    조용히 빈 문자열을 돌려주면 **없는 규칙을 인용한 지적이 근거 있는 지적처럼**
    보인다. 그건 이 지식베이스를 두는 이유와 반대다.
    """
    found = _BY_ID.get(rule_id)
    return found.tag if found else f"[{rule_id} · 알 수 없는 규칙]"


def judged_by(stage: str, judge: str) -> tuple[Rule, ...]:
    """이 단계에서 그 판정자가 보는 결함 규칙들."""
    return tuple(r for r in rules_for(stage, DEFECT) if r.judged_by == judge)


def validator_prompt_block(stage: str) -> str:
    """의미 검증자가 판정할 규칙 목록.

    근거의 성격까지 함께 넣는다. 모델이 "책이 정한 것"과 "우리 규약"을 구별하지 못하면
    유보가 필요한 지적을 단언으로 낸다.
    """
    return "\n".join(r.prompt_line() for r in judged_by(stage, JUDGED_VALIDATOR))


def already_checked_names(stage: str) -> tuple[str, ...]:
    """검증자에게 "이건 이미 봤다"고 알려줄 규칙 id들.

    예전에는 이 목록이 프롬프트 산문에 손으로 적혀 있었다("branching words, control
    tokens, UI terms, broken step references, missing contract"). 검출기를 하나 늘리면
    그 문장도 같이 고쳐야 하고, 안 고치면 검증자가 이미 잡힌 것을 다시 지적한다.
    """
    return tuple(
        r.id for r in rules_for(stage, DEFECT)
        if r.judged_by in (JUDGED_DETECTOR, JUDGED_STAGE)
    )


def unjudged_defects() -> tuple[Rule, ...]:
    """결함이라고 적어 놓고 **아무도 판정하지 않는** 규칙들.

    비어 있는 것이 목표가 아니다 — 지금 비어 있지 않고, 그게 사실이다. 테스트가 이
    목록을 고정해 두므로 조용히 늘어나지는 않는다.
    """
    return tuple(r for r in RULES if r.severity == DEFECT and r.judged_by == JUDGED_NOWHERE)
