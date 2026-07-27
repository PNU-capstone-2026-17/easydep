"""요구사항 분석 에이전트의 시스템 프롬프트.

전 단계 영어로 작성한다 (입력/출력 영어 중심 결정 + BERT는 영어 단문 학습).

**규칙은 여기서 쓰지 않는다** — 검증도(`_validator_system`) 생성도(`_generation_system`)
`app/requirements/knowledge/rules.py`의 규칙 레코드에서 조립한다. 규칙을 산문으로 여기 적으면
같은 규칙이 프롬프트·검출기·문서에 여러 벌로 갈라진다.

이 파일에 남는 것은 **모양**뿐이다: 어떤 필드를 내는가, 번호를 어떻게 매기는가, 마크다운을
쓰지 않는다, 어느 필드에 어느 id를 채우는가. 스키마가 강제하는 것이지 판정으로 가리는 것이
아니다.

⚠ **손으로 적은 인용은 기계가 대조하지 못한다.** 이 파일이 그걸로 두 번 물렸다:
자동결과 문단이 "Cockburn: absorb system consequences into the driving goal"이라고
귀속했는데 `absorb`도 `driving goal`도 책에 없는 말이었고(로컬 사본으로 확인, 2026-07-26),
생성 산문에 남아 있던 `Cockburn p.81`·`Ch.8`·`Guideline 6`도 `verify_citations`의 사정거리
밖이었다(2026-07-27에 규칙 레코드로 옮겼다). **여기에 인용을 새로 적지 말 것** —
`tests/test_knowledge.py`가 막는다.
"""
import hashlib
from collections.abc import Sequence

from app.requirements.knowledge import concerns as _concerns
from app.requirements.knowledge import rules as _rules


def _validator_system(
    stage: str, role: str, do_not_flag: str, only: str | None = None
) -> str:
    """의미 검증자 시스템 프롬프트를 규칙 지식베이스에서 조립한다.

    네 가지를 모델에게 한꺼번에 준다:
      1. 판정 근거가 될 규칙 목록 — **이 목록 밖으로 나가지 말라**는 지시와 함께.
      2. 각 규칙의 출처와 그 성격(책이 적은 것인지 우리 판단인지).
      3. 이미 결정론으로 검사한 규칙 목록 — 두 번 지적하지 않도록.
      4. **규칙마다 한 줄씩** 판정하라는 요구 — 훑고 넘어가는 것을 막는다.

    3번이 예전에는 산문에 손으로 적혀 있었다. 검출기를 하나 늘리면 그 문장도 고쳐야 했고,
    안 고치면 검증자가 이미 잡힌 것을 다시 지적했다.

    4번은 early victory 방어다. "깨끗하다" 한 줄로 끝낼 수 있으면 검증자는 그렇게 한다 —
    규칙마다 판정을 받으면 무엇을 안 봤는지가 응답에서 드러난다(`agent/validator.py`).

    `only`를 주면 **그 규칙 하나만** 담는다. 6개를 한 번에 판정하게 하면 판정이 흔들린다는
    측정(흔들림 90%, `docs/requirements-agent-improvements.md` §7) 때문에 생긴 갈래다 —
    과제를 쪼개면 확률이 0.5에서 멀어지는지 보려는 것이다.
    """
    judged = _rules.judged_by(stage, _rules.JUDGED_VALIDATOR)
    if only is not None:
        judged = tuple(r for r in judged if r.id == only)
        if not judged:
            raise KeyError(f"{stage}에 의미 검증 규칙 {only!r}이 없다")
    n = len(judged)
    count = f"{n} verdict" if n == 1 else f"{n} verdicts"
    block = "\n".join(r.prompt_line() for r in judged)
    already = ", ".join(_rules.already_checked_names(stage)) or "(none)"
    return f"""You are a zero-tolerance {role}.

The rules below are the ONLY grounds you may judge on. Each line is: rule id, where the rule
comes from, then what it requires. Some lines add a note about how well the rule is grounded —
judge those rules just the same, but never present them as the source's own words.

[RULES YOU JUDGE]
{block}

Deterministic checks have ALREADY run for these rules — do NOT report them again:
{already}

Return **exactly {count} — one per rule above, in the same order.** Copy each rule_id
EXACTLY. Set violated=true only when the artifact really breaks that rule, and then give one
short imperative repair directive; leave the directive empty otherwise.

There are two ways to be wrong here, and both matter:
- **Skipping a rule.** Every rule gets a verdict, including the ones whose answer is obviously
  "not violated". A short list of verdicts is not a clean artifact, it is an unfinished review.
- **Inventing a violation to fill a verdict.** A well-formed artifact violates few rules or
  none. And a defect matching no rule above is not reported at all — there is no rule_id for
  it, so nothing could act on it.

Do NOT flag: {do_not_flag}

Return the structured object only."""

# 요구사항이 유스케이스 도출에 충분히 구체적인지 판단하고,
# 부족하면 clarifying questions 를, 충분하면 refined_requirements 를 만든다.
ASSESS_SYSTEM = """You are a requirements analyst for cloud-native applications.

You are given a set of user requirement statements. They may be abstract
(e.g. "I want to build a shopping mall service") or concrete
(e.g. "Users must be able to log in with email and password").

Your job in this step:
1. Decide whether the requirements are ALREADY concrete enough to identify
   actors and use cases without guessing. A requirement is concrete when its
   actor, action, and target object are clear and testable.
2. If anything is too abstract or ambiguous, produce a SHORT list of
   clarifying questions (max 4, most impactful first) that would let you
   decompose the vague requirement into concrete functional requirements.
   Ask about: target users/actors, the core features, data handled,
   and key quality attributes (performance, security, scale).
3. Once you have enough information (from the original input plus any answers
   already provided in the conversation), set is_concrete=true and populate
   refined_requirements with a clean list of CONCRETE, SINGLE-SENTENCE English
   requirements. Decompose abstract goals into specific functional and
   non-functional requirements. Keep each requirement atomic and testable.

Rules:
- Prefer concrete, verifiable statements ("The system shall ...", "Users can ...").
- Do not invent domain facts the user did not imply; ask instead.
- When the user has already answered clarifying questions, do NOT ask the same
  thing again — move forward and finalize refined_requirements.
- refined_requirements must be in English, one requirement per sentence."""

# FR/NFR 분류는 LLM이 아니라 파인튜닝 BERT가 단독 수행한다(step1 classify). 관련 프롬프트 없음.

# elaborate 단계에서 대화 이력을 요약해 최종 요구사항으로 확정할 때 쓰는 지시.
ELABORATE_SYSTEM = """You are finalizing a requirements list for a cloud-native
application. Using the original requirements and all answers the user gave to
clarifying questions, produce the definitive set of concrete, atomic,
single-sentence English requirements. Cover both functional behaviors and the
quality attributes (non-functional) the user cares about. Do not ask any more
questions."""

# STEP 2 — 액터 도출. 기능 요구사항(FR)에서만 액터(역할)를 뽑는다.
ACTORS_SYSTEM = """You identify the actors for a use-case model from a list of
FUNCTIONAL requirements (FRs).

An actor is a role (a class of user or an external system), never a specific person.
- primary: an external human or system that HAS a goal the system fulfils (initiates a
  use case).
- supporting: an external system the application must CALL to fulfil a goal (e.g. a payment
  gateway, an email/SMS provider, an external data feed).

The system under design (SuD) is the system boundary — it is never a PRIMARY or SUPPORTING
actor, so never create an actor for it (no "The System", "E-commerce System", "The
Application" actor).

Boundary litmus test: if a component would be built and deployed as PART of this application
(its own internal services, engines, stores, caches, databases), it is INTERNAL — it is not
an actor and not a supporting system. Only genuinely third-party systems outside the app
boundary are supporting actors.

Rules:
- Derive actors ONLY from the functional requirements. Deduplicate similar roles into one.
- If a requirement is written system-centric ("The system shall ..."), infer the human role
  or external actor whose goal it ultimately serves; if none exists, it is likely a
  subfunction or a non-functional concern — do NOT invent an actor for it.
- Every actor you list must own at least one use-case goal; do not list bystanders.
- If one actor is a specialization of another (e.g. a Registered Member specializes a Guest,
  inheriting its capabilities plus more), set parent_actor to the more general role's name.
  Leave parent_actor null when there is no such specialization.
- Prefer a small, clean set of well-named roles over many overlapping ones."""

# STEP 2 — 유스케이스 도출. user-goal(EBP) 고도 + FR 추적성 + NFR 제약.
USECASES_SYSTEM = """You derive a use-case model from functional requirements (FRs), a
list of actors, and non-functional requirements (NFRs).

Goal altitude (Cockburn): produce use cases at the USER-GOAL level (sea level). A use
case is at user-goal level if it passes the Elementary Business Process (EBP) test: a
task a single primary actor performs in one sitting that leaves the system in a
consistent state and delivers measurable value (the job / boss / one-sitting test).

This step only IDENTIFIES use cases (name, actor, goal, which requirements they cover).
Do NOT write the main scenario steps or extensions — those are produced later (step 3).

How to build the model:
1. Group related FRs into user-goal use cases. Name each as an active-verb goal
   ("Place an order", "Reset password").
2. Requirements that are too fine-grained (subfunction level, e.g. "validate the form")
   must NOT become standalone use cases — fold them into the parent user-goal use case by
   listing their ids in that use case's requirement_ids.
3. For each use case set primary_actor (from the given actor list) and goal (one sentence
   stating the actor's intent — NOT a step-by-step scenario).
4. Traceability: list in requirement_ids EVERY FR id the use case covers (including the
   folded subfunction FRs), using only the ids provided. Aim for full coverage — every FR
   should be covered by at least one use case.
5. NFRs never become use cases. Attach each relevant NFR to the use case(s) it constrains
   via nfr_ids; cross-cutting NFRs may be left unattached.
Do not invent requirements or ids that were not provided."""

# 피드백 의도 분류 — 자연어 피드백을 {stage, scope, target_ids, instruction}로 분류.
FEEDBACK_CLASSIFY_SYSTEM = """You classify a user's natural-language feedback about a generated
requirements-analysis artifact into a structured revision intent.

Pipeline stages, each downstream of the previous: actors -> use_cases -> specs -> relationships
-> diagram. The diagram is rendered deterministically from relationships, so feedback about the
diagram targets 'relationships'.

Decide:
- stage: which artifact the feedback is about (actors / use_cases / specs / relationships).
- scope: 'local' if it concerns specific named items — put their ids in target_ids (a use case
  id like 'UC3', or an actor name); 'broad' if it asks to re-derive the whole stage.
- instruction: one concise imperative directive restating what to change.

Use the provided current-artifacts summary to resolve ids. Return the structured object only."""


# 공통 — 사용자 자연어 피드백을 재생성 지시로 프롬프트에 얹는다.
def apply_user_feedback(base_user: str, feedback: str) -> str:
    if not feedback:
        return base_user
    return (
        f"{base_user}\n\n[USER FEEDBACK — this instruction is AUTHORITATIVE. Apply exactly what "
        f"the user asks, even if it deviates from a default guideline stated above; the user's "
        f"explicit direction takes precedence over the defaults. Change ONLY what the feedback "
        f"asks for and keep everything else that was already correct unchanged; do not introduce "
        f"unrelated changes.]\n{feedback}"
    )


# STEP 2 — 유스케이스 국소 수정: 대상 UC만 지시대로 고치고 나머지는 그대로 유지.
def usecase_local_edit(base_user: str, current_listing: str, target_desc: str, feedback: str) -> str:
    return (
        f"{base_user}\n\n"
        f"[CURRENT USE CASES]\n{current_listing}\n\n"
        f"[LOCAL EDIT — apply the user feedback ONLY to these target use cases: {target_desc}. "
        f"The user's instruction is authoritative. Return the FULL use-case list in the SAME order "
        f"and the SAME count as above; copy every NON-target use case VERBATIM (identical name, "
        f"primary_actor, level, goal, requirement_ids, nfr_ids). Modify only the target(s).]\n"
        f"{feedback}"
    )


# STEP 2 — 커버리지 강제-수리: 고아 FR을 담아 유스케이스 목록을 보충 재생성.
def usecase_coverage_repair(base_user: str, orphan_listing: str, current_summary: str) -> str:
    return (
        f"{base_user}\n\n"
        f"[FUNCTIONAL REQUIREMENTS NOT YET COVERED BY ANY USE CASE]\n{orphan_listing}\n\n"
        f"[CURRENT USE CASES]\n{current_summary}\n\n"
        "Extend the use-case list so every uncovered FR above is covered — add a new "
        "user-goal use case, or attach the FR to an existing use case by listing its id in "
        "requirement_ids. Keep use cases at user-goal level; do not fragment. Return the FULL "
        "updated use-case list (existing ones plus any additions)."
    )


# STEP 3 — 단일 유스케이스의 Cockburn 명세(주 시나리오 + 확장 + 사전/사후조건).
def _generation_system(stage: str, shape: str, learned: str = "") -> str:
    """생성 프롬프트를 조립한다: **모양은 `shape`, 규칙은 지식베이스, 배운 것은 맨 뒤.**

    `shape`에 남는 것은 스키마가 강제하는 것뿐이다(어떤 필드를 내는가, 번호 매김, 마크다운
    금지). 무엇이 결함인가는 `knowledge/rules.py`가 말한다.

    경위는 `docs/requirements-agent-improvements.md` §11. 그 전에는 규칙이 두 벌이었고
    실제로 갈라져 있었다.
    """
    parts = [
        shape.rstrip(),
        "",
        "[RULES YOU FOLLOW]",
        "Each line is: rule id, then what it requires. The validator judges the finished",
        "artifact against these same rules, by these same ids.",
        _rules.generation_prompt_block(stage),
    ]
    # `GUIDANCE`·`NON_RULE`은 검증 프롬프트에 안 들어간다 — 받아야 할 쪽이 판정자가 아니라
    # 쓰는 쪽이다. 특히 과적합("스텝 3~9개"를 목표로 알아듣는 것)은 생성에서 일어난다.
    non_rules = _rules.non_rules_block(stage)
    if non_rules:
        parts += [
            "",
            "[NOT RULES]",
            "These are observations we wrote down precisely so they are not mistaken for",
            "constraints. Never shape the artifact to satisfy them.",
            non_rules,
        ]
    # 배운 것은 규칙 **사이에 끼우지 않는다.** 근거가 우리 로그뿐인 문장이 책 좌표를 단
    # 문장과 같은 무게로 읽히면 `basis.py`가 그은 선이 프롬프트에서 무너진다.
    if learned:
        parts += ["", learned]
    return "\n".join(parts)


_SPEC_SHAPE = """You write a fully-dressed use-case specification (Cockburn style) for a
SINGLE use case.

You are given the use case (name, primary actor, goal), the functional requirements (FRs)
it covers, and the non-functional requirements (NFRs) that constrain it.

WRITING STYLE (applies to every sentence):
- Plain prose only. NO markdown, NO bold, NO asterisks, NO backticks, NO emphasis.
- Each step is a single action whose subject is the primary actor or 'System'.

Produce:
- preconditions: verifiable state true before the use case starts; never re-checked inside
  steps. Include any NFR that is a precondition (e.g. the actor is authenticated).
- trigger: the business event that starts the use case.
- main_scenario: the main SUCCESS scenario (happy path) as ordered steps. Number them from 1.
  Derive steps from the goal and covered FRs — every covered FR must appear in some step's
  covered_req_ids. Put the realizing FR id(s) in each step's covered_req_ids.
- extensions: exception and alternate flows. For EACH extension:
    * label: Cockburn label like '3a' (branches from step 3) or '*a' (may occur at any step).
    * branch_step: the main_scenario step_number it branches from; use null for a global
      extension (label '*a').
    * condition: the objective state that triggers it (no trailing colon).
    * handling_steps: ordered steps with hierarchical sub_step codes ('3a1', '3a2', ...).
    * outcome — choose exactly one and set resume_at_step accordingly:
        - 'resume'            → the flow rejoins the main scenario; set resume_at_step to the
                                 step_number to continue from.
        - 'alternate_success' → the use case still succeeds by another path; resume_at_step null.
        - 'fail'              → the use case aborts without the goal; resume_at_step null.
  Cover validation failures, service/dependency errors, and meaningful alternatives.
- success_guarantee: postconditions that hold when the use case succeeds.
- minimal_guarantee: what the system still guarantees even on failure (e.g. no partial order
  is persisted; relevant NFRs such as data-at-rest encryption still hold).

Keep sentences concise and testable. Do not invent requirements beyond those provided."""

#: 명세 생성 프롬프트 — **플레이북 없는 판.** 지식베이스만으로 조립된 것이고, 측정에서
#: 대조군이 되는 것도 이것이다. 플레이북을 켜면 `generation_system_for`가 여기에 절을 하나
#: 더 붙인다.
SPEC_SYSTEM = _generation_system(_rules.WRITE_SPECIFICATIONS, _SPEC_SHAPE)

#: 단계 → (역할, 안 잡을 것). 규칙 하나짜리 프롬프트를 만들 때 다시 쓴다.
_VALIDATOR_ROLES = {
    _rules.MODEL_USE_CASES: (
        "use-case model critic reviewing the actors and use cases of one system",
        "a grouping you would have done differently; a missing actor or use case (that is "
        "coverage, checked deterministically elsewhere); naming or wording preferences",
    ),
    _rules.WRITE_SPECIFICATIONS: (
        "use-case specification critic",
        'a step with more than one clause; the absence of an explicit "System validates" step; '
        "a use case having no extensions; slightly high/low goal level; wording preferences",
    ),
    _rules.DRAW_DIAGRAM: (
        "use-case-relationship critic reviewing proposed includes, extends, generalizations and "
        "derived use cases (name the offending relationship in each directive)",
        "a small, clean relationship set; an ordinary association; a use case with no relationships",
    ),
}


# STEP 2 — 액터·유스케이스 모델 의미 검증. 이 단계에는 예전에 의미 검증기가 아예 없었고,
# 책이 명시한 결함(SuD는 액터가 아니다, p.59)조차 아무도 보지 않았다.
MODEL_VALIDATOR_SYSTEM = _validator_system(
    _rules.MODEL_USE_CASES, *_VALIDATOR_ROLES[_rules.MODEL_USE_CASES]
)

# STEP 3 — 명세 의미 검증(정적 체크가 못 잡는 부분만). 규칙은 knowledge/rules.py에서 온다.
SPEC_VALIDATOR_SYSTEM = _validator_system(
    _rules.WRITE_SPECIFICATIONS, *_VALIDATOR_ROLES[_rules.WRITE_SPECIFICATIONS]
)

# STEP 3 — 반성(reflection) 재생성: 실패 지시를 붙여 명세를 고쳐 다시 생성.
def spec_repair_user(base_user: str, directives: list[str]) -> str:
    joined = "\n".join(f"- {d}" for d in directives)
    return (
        f"{base_user}\n\n[YOUR PREVIOUS OUTPUT FAILED THESE CHECKS — fix every one while "
        f"keeping the parts that were already correct; do not introduce new violations]\n{joined}"
    )


# STEP 4 — 관계 의미 검증. 규칙은 knowledge/rules.py에서 온다.
RELATIONSHIP_VALIDATOR_SYSTEM = _validator_system(
    _rules.DRAW_DIAGRAM, *_VALIDATOR_ROLES[_rules.DRAW_DIAGRAM]
)

#: 단계 → 검증 프롬프트. `agent/validator.py`가 단계 이름만 알고 프롬프트를 찾도록 한다 —
#: 검증자가 단계별 상수 이름을 직접 알면 단계를 하나 더 넣을 때 그쪽도 고쳐야 한다.
_VALIDATOR_SYSTEMS = {
    _rules.MODEL_USE_CASES: MODEL_VALIDATOR_SYSTEM,
    _rules.WRITE_SPECIFICATIONS: SPEC_VALIDATOR_SYSTEM,
    _rules.DRAW_DIAGRAM: RELATIONSHIP_VALIDATOR_SYSTEM,
}


def probe_system_for(stage: str, rule_id: str) -> str:
    """**어느 규칙이든** 하나만 담은 검증 프롬프트 — 측정 전용.

    `validator_system_for`는 지금 판정 대상인 규칙(`judged_by=validator`)만 담는다. 그래서
    강등된 규칙(`GUIDANCE`로 내린 것)은 그 경로로 물어볼 수 없는데, **강등이 옳았는지 다른
    표본에서 다시 재려면 물어봐야 한다.** 승격 후보를 재는 데도 같은 자리다.

    파이프라인은 이걸 쓰지 않는다 — 쓰면 강등이 강등이 아니게 된다.
    """
    rule = _rules.rule(rule_id)
    if rule.stage != stage:
        raise KeyError(f"{rule_id}는 {stage} 단계의 규칙이 아니다({rule.stage})")
    role, do_not_flag = _VALIDATOR_ROLES[stage]
    n = "1 verdict"
    already = ", ".join(_rules.already_checked_names(stage)) or "(none)"
    return f"""You are a zero-tolerance {role}.

The rule below is the ONLY ground you may judge on.

[RULE YOU JUDGE]
{rule.prompt_line()}

Deterministic checks have ALREADY run for these rules — do NOT report them again:
{already}

Return **exactly {n}** for that rule. Copy the rule_id EXACTLY. Set violated=true only when
the artifact really breaks it, and then give one short imperative repair directive.

Do NOT flag: {do_not_flag}

Return the structured object only."""


def _generation_shapes() -> dict[str, str]:
    """단계 → 그 단계의 "모양" 산문. 함수인 이유는 배치다(`_RELATIONSHIPS_SHAPE`가 아래에 있다)."""
    return {
        _rules.WRITE_SPECIFICATIONS: _SPEC_SHAPE,
        _rules.DRAW_DIAGRAM: _RELATIONSHIPS_SHAPE,
    }


def generation_system_for(stage: str) -> str:
    """이 실행이 쓸 생성 프롬프트. 플레이북이 꺼져 있으면 상수와 **같은 문자열**이다.

    꺼짐이 곧 대조군이라 한 글자도 달라지면 안 된다(`tests/test_playbook.py`가 지킨다).
    파일을 못 읽으면 끈 것과 같이 간다 — 배우는 층이 실행을 세울 이유는 아니다.
    """
    from app.requirements.config import settings

    shape = _generation_shapes().get(stage)
    if shape is None:  # pragma: no cover - 배선 오류
        raise KeyError(f"{stage}: 생성 프롬프트가 없다")
    if not settings.playbook_enabled:
        return _generation_system(stage, shape)

    from app.requirements.agent import playbook

    try:
        learned = playbook.render(
            playbook.load(settings.playbook_path),
            stage,
            playbook.load_feedback(settings.playbook_path),
        )
    except (OSError, ValueError, KeyError):
        learned = ""
    return _generation_system(stage, shape, learned)


def _concern_linker_system() -> str:
    """클라우드 관심사 링크 프롬프트를 관심사 지식베이스에서 조립한다.

    **판정 방향이 검증 프롬프트와 반대다.** 검증자는 산출물을 보고 위반을 찾지만, 여기서는
    요구사항을 보고 **다뤄진 것**을 찾는다. 그래서 지시의 무게 중심도 반대다 — 검증자에게는
    "없는 결함을 만들지 말라"고 하고, 여기서는 "느슨하게 갖다 붙이지 말라"고 해야 한다.
    링크는 붙이기가 쉽고, 하나라도 붙으면 그 관심사는 '다뤄졌다'가 되어 인계에서 사라진다.

    모양만 여기 있다. 무엇을 묻는지는 `knowledge/concerns.py`가 정한다.
    """
    return f"""You map requirements onto cloud-native concerns.

You are given a list of requirements (each with an id) and a list of concerns. For EACH
concern, list the ids of the requirements that actually address it. If none do, return an
empty list for that concern — that is a normal and useful answer, not a failure.

Rules of the mapping:
- Answer for EVERY concern in the list, once each, using the concern id exactly as given.
- Use ONLY requirement ids that appear in the given list. Never invent an id.
- A requirement addresses a concern only if it CONSTRAINS or DECIDES it. A requirement that
  merely operates in the same area does not. "The system stores user profiles" does not
  address where data must reside; "user data must remain in Korea" does.
- Do not judge whether the requirement is well written, complete, or correct. You are only
  saying what it is about.
- Being a cloud or web application does not by itself address any concern.

Concerns:
{_concerns.prompt_block()}
"""


#: 관심사 링크 프롬프트. `fingerprint()`가 해싱하므로 모듈 로드 시 한 번 조립한다.
CONCERN_LINKER_SYSTEM = _concern_linker_system()


#: 측정 경로 이름 — 무엇을 실제로 쓰는 실행인가.
GENERATION = "generation"
VALIDATION = "validation"
CONCERNS = "concerns"
#: 규칙 하나짜리 프로브(`probe_system_for`). **검증과 다른 프롬프트다** —
#: `_VALIDATOR_SYSTEMS`는 여러 규칙을 한 번에 담고, 프로브는 하나만 담는다.
PROBE = "probe"

PATHS = (GENERATION, VALIDATION, PROBE, CONCERNS)


def _digest(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:12]


def _probe_digest() -> str:
    """프로브가 실제로 던지는 프롬프트 전부의 해시.

    규칙마다 프롬프트가 다르므로 전부 이어 붙여 해싱한다. 규칙 문장·근거 고지
    (`basis.prompt_note`)·이미 검사한 규칙 목록이 바뀌면 여기서 잡힌다.
    """
    parts = []
    for stage in sorted(_VALIDATOR_SYSTEMS):
        for rule in _rules.rules_for(stage):
            if rule.severity == _rules.NON_RULE:
                continue
            parts.append(probe_system_for(stage, rule.id))
    return _digest(*parts)


def fingerprint(paths: Sequence[str] | None = None) -> dict[str, str]:
    """이 측정이 **실제로 쓴** 프롬프트의 판. 측정 행마다 함께 찍는다(§13).

    ## 왜 경로별인가 — 전역 판은 두 방향으로 거짓말을 했다

    처음에는 프롬프트 전부를 한 번에 찍었다. 그래서 2026-07-28에 관심사 프롬프트
    (`CONCERN_LINKER_SYSTEM`)가 생기자, **프로브가 한 글자도 쓰지 않는 프롬프트가 바뀌었다는
    이유로** 프로브 측정이 "조건이 섞였다"로 무효 처리됐다. 반대쪽 구멍도 있었다 —
    프로브가 실제로 쓰는 `probe_system_for`는 어느 키에도 안 잡혀서, **진짜 변경은 놓쳤다.**

    과잉 발동하는 조건 추적기는 과소 발동하는 것만큼 나쁘다. 곧 무시당하기 때문이다.

    ## 쓰는 법

    부르는 쪽이 **자기가 무엇을 썼는지** 말한다. `paths=None`이면 전부 낸다(사람이 읽는
    용도이지, 측정 행에 찍는 값이 아니다).

        fingerprint([PROBE])        # 단독 프로브 측정
        fingerprint([GENERATION])   # 파이프라인 실행

    ⚠ `*_SYSTEM` 상수와 프로브만 해싱한다 — `spec_repair_user`·`_spec_human`은 여전히
    판에 안 잡힌다(§15).
    """
    builders = {
        GENERATION: lambda: _digest(
            SPEC_SYSTEM, RELATIONSHIPS_SYSTEM, ACTORS_SYSTEM, USECASES_SYSTEM
        ),
        VALIDATION: lambda: _digest(*(_VALIDATOR_SYSTEMS[s] for s in sorted(_VALIDATOR_SYSTEMS))),
        PROBE: _probe_digest,
        # 클라우드 관심사 링크는 생성도 검증도 아니다 — 산출물을 만들지도, 규칙 위반을
        # 판정하지도 않고 "이 요구가 저 관심사를 건드리는가"만 묻는다.
        CONCERNS: lambda: _digest(CONCERN_LINKER_SYSTEM),
    }
    wanted = PATHS if paths is None else paths
    unknown = [p for p in wanted if p not in builders]
    if unknown:  # pragma: no cover - 배선 오류
        raise KeyError(f"모르는 측정 경로 {unknown} — 아는 것은 {list(builders)}")
    return {p: builders[p]() for p in wanted}


def validator_system_for(stage: str, only: str | None = None) -> str:
    """단계의 검증 프롬프트. 없는 단계를 조용히 넘기지 않는다 — 검증 없는 실행이 된다.

    `only`를 주면 **그 규칙 하나만** 담은 프롬프트를 만든다(`settings.validator_per_rule`).
    """
    if stage not in _VALIDATOR_SYSTEMS:  # pragma: no cover - 배선 오류
        raise KeyError(
            f"{stage}: 의미 검증 프롬프트가 없다. knowledge/rules.py에 규칙을 넣었다면 "
            "여기에도 프롬프트를 등록해야 한다."
        )
    if only is None:
        return _VALIDATOR_SYSTEMS[stage]
    role, do_not_flag = _VALIDATOR_ROLES[stage]
    return _validator_system(stage, role, do_not_flag, only=only)


# STEP 4 — 액터/유스케이스 관계 식별(다이어그램용).
_RELATIONSHIPS_SHAPE = """You identify the relationships of a UML use-case diagram from a
set of actors and use cases (with their goals and, when available, their scenarios).

Reference every actor and use case by its EXACT given name.

Identify:
- associations: which actor interacts with which use case. Besides each use case's primary
  actor, add associations for supporting actors that a scenario hands off to.
- includes: a genuine shared sub-goal that appears as an action STEP in two or more use cases
  (e.g. 'Send Notification', 'Process Payment'). Factor it into a NEW derived use case
  (origin 'factored_include') and add an include from each base use case to it. Factor only a
  meaningful, independently-nameable sub-goal, not a generic step-fragment (validating input,
  displaying results).
- extends: behaviour the goal does NOT require, that an actor or account opts into (canonical
  example: an account configured for multi-factor authentication -> "Perform Multi-Factor
  Authentication" extends "Authenticate"). Attach it to the base at an extension_point and add
  it to derived_use_cases (origin 'promoted_extend'). Returning ZERO extends is common and
  correct.
- generalizations: an actor that specializes another (e.g. Registered User -> Guest), or a
  use case that specializes another. Set kind to 'actor' or 'use_case'.
- derived_use_cases: every NEW use case you introduced for an include or extend above.

Boundary litmus test: any actor you associate must be an external human/system, not the
system under design or its internal components (if it would be built and deployed as part of
this application, it is internal — never associate it as an actor).

Copy use-case and actor names VERBATIM from the input — never rephrase, abbreviate, or invent
a name. A relationship that references a name not present in the input will be discarded.

Only assert a relationship when the evidence is clear. Prefer few, well-justified relationships.
Do not invent actors or use cases beyond those given plus the derived ones you explicitly
declare."""

#: 관계 생성 프롬프트. 산문에 있던 인용 둘(`Cockburn p.81`·`Cockburn Ch.8`)이 여기서
#: 사라진 것은 규칙이 사라져서가 아니다 — 같은 규범이 `rel.shared-authentication-is-a-precondition`·
#: `rel.failures-stay-inline-extensions`에 좌표까지 달려 있고, 이제 그쪽에서 실린다.
#: **손으로 적은 인용은 대조를 못 받는다**는 것이 이 파일이 이미 한 번 물린 자리다.
RELATIONSHIPS_SYSTEM = _generation_system(_rules.DRAW_DIAGRAM, _RELATIONSHIPS_SHAPE)


# ----------------------------------------------------------------------------
# BASELINE 프롬프트 — 다단계 파이프라인의 대조군(순진한 원샷).
# 의도적으로 "유능한 엔지니어의 단순 프롬프트" 수준으로 둔다: 단계 분해·커버리지 강제·
# 정적/의미 검증·반성 루프·근거(Cockburn 페이지) 그라운딩을 넣지 않는다. 이 프롬프트만으로
# 얼마나 나오는지가 곧 baseline이며, 우리 시스템과의 차이가 "분해+검증"의 가치가 된다.
# ----------------------------------------------------------------------------
BASELINE_MODEL_SYSTEM = """You are a software analyst. You are given a list of requirements, each
with an id (R1, R2, ...). In a single pass, produce a use-case model and fully-dressed use-case
specifications directly from these requirements.

Return:
- actors: the actors (primary = external human/system with a goal; supporting = external system
  the app calls).
- use_cases: the use cases. For each, set primary_actor, goal, and requirement_ids (the ids of
  the requirements it covers). Leave nfr_ids empty.
- specs: one Cockburn-style specification per use case (set use_case_name to match). Each spec
  has preconditions, trigger, main_scenario (numbered steps), extensions (alternate/exception
  flows), success_guarantee, and minimal_guarantee.

Write plain black-box sentences (actor or System as subject). Cover the requirements as best you
can in one pass."""

BASELINE_DIAGRAM_SYSTEM = """You are a software analyst. Given the actors and use cases, produce
the relationships of a UML use-case diagram in a single pass:
associations (actor ↔ use case), includes, extends, generalizations, and any derived use cases
you introduce (e.g. a factored-out included use case).

Copy actor and use-case names verbatim from the input. Return the relationships directly."""


# 의미론적 커버리지 검증 — 결정론 집합검사는 "id가 존재/참조됨"만 보지만, 유스케이스가 그 FR을
# '실제로' 실현하는지는 판단 못 한다(LLM이 requirement_ids에 거짓으로 id를 넣을 수 있음). 이 판정으로
# 주장된 커버리지가 시나리오로 뒷받침되는지 확인한다.
COVERAGE_JUDGE_SYSTEM = """You are a strict requirements-traceability auditor. You are given ONE
use case (its goal and main-scenario steps) and a list of requirements it CLAIMS to cover.

For each claimed requirement, decide realized=true ONLY IF the use case's goal and steps genuinely
implement that requirement's FUNCTIONAL intent — the behavior described would actually satisfy it.

Judge FUNCTIONAL realization ONLY. A requirement may embed a non-functional / quality qualifier —
a response time ("within 1 second", "within 500 ms"), a load/throughput target ("under 200
concurrent sessions"), a security/encryption clause ("encrypted at rest"), a reliability/atomicity
clause ("recorded atomically"), or an availability target. These are validated separately as
non-functional constraints and are DELIBERATELY absent from the black-box scenario prose. Do NOT
set realized=false merely because such a timing/security/reliability qualifier is not restated in
the steps; assess only whether the FUNCTIONAL behavior is realized.

Set realized=false when the FUNCTIONAL behavior is not supported by the scenario (the behavior is
absent, only tangentially related, or belongs to a different use case). Do not give the benefit of
the doubt on the functional core; an unsupported functional claim is a traceability defect. Return
one verdict per claimed requirement id."""
