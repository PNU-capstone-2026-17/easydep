"""심어 둔 결함으로 **검사기 자체를 검사한다.**

## 왜 이게 평가 세트의 일부인가

채점표(`scorecard.py`)는 실행끼리 비교하게 해 준다. 그런데 채점의 눈금이 맞는지는 채점표가
답하지 못한다. "scope creep 0건"이 정말 없다는 뜻인지, 검사기가 못 잡는다는 뜻인지 구별할
근거가 없으면 그 0은 아무 정보가 아니다.

그래서 **결함을 알고 심은 산출물**을 두고, 검사기가 그것을 잡는지 본다. 규칙마다 하나씩,
정확히 그 규칙만 어기게 심는다. 잡으면 그 규칙의 눈금이 살아 있다는 뜻이고, 못 잡으면
그 규칙에 대한 모든 0은 근거가 없다는 뜻이다.

`CLEAN`은 대조군이다. 아무 결함이 없으므로 검사기가 아무것도 내지 않아야 한다 — 여기서
무언가 나오면 그건 오탐(false positive)이고, 오탐이 있는 검사기는 실행 비교를 오염시킨다.

## 두 종류의 눈금

  - **`SEEDED`** — 결정론 검출기(`knowledge/detectors.py`)용. LLM 없이 돌아서 CI 게이트가
    된다. 5/5여야 하고, 아니면 테스트가 깨진다.
  - **`SEEDED_SEMANTIC`** — LLM 검증자용. 판정이 결정론이 아니라 **CI에 기계적으로 넣을 수
    없다.** 그렇다고 측정을 못 하는 것은 아니다 — 케이스마다 여러 번 돌려 검출률을 보면
    된다(`evaluation/semantic.py`, `RUN_LIVE_TESTS=1`). 어떤 규칙이 0/N이면 그 규칙에 대한
    모든 "0건"은 근거가 없다.

## 심는 규칙: 하나만 어긴다

각 케이스는 **자기 규칙만** 어겨야 한다. 의미 케이스는 정적 검출기도 통과해야 한다 —
정적 위반이 섞이면 그건 정적 층이 잡은 것인지 의미 층이 잡은 것인지 알 수 없다.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass

from app.requirements.knowledge import detectors, rules

#: 결함이 없는 명세. 정적 검출기와 **의미 검증자 양쪽을** 통과해야 한다(대조군).
#:
#: UI 예시 단어(screen/field/button/click/tab)와 분기어(if/else)를 피해 쓴 문장들이고,
#: 확장은 `_REQUIREMENTS`에 있는 기능만 쓰며 복귀 지점이 가정하는 상태를 실제로 세운다.
#:
#: ⚠ 2026-07-26 첫 라이브 측정에서 이 대조군이 **오탐률 100%**를 냈다. 원인은 검증자가
#: 넘치게 잡은 것이 아니라 **대조군이 깨끗하지 않았던 것**이다:
#:   - 확장이 "대체품 제안"을 하는데 요구사항에 그 기능이 없었다 → 진짜 scope creep
#:   - 대체가 확정됐다는 상태를 세우지 않고 주문 기록 스텝으로 복귀했다 → 진짜 복귀 결함
#: 대조군이 오염되면 오탐률을 못 재고, 못 재면 "다 잡는 눈금"과 "제대로 잡는 눈금"이
#: 구별되지 않는다. 그래서 요구사항에 FR4를 넣고 복귀를 성립시켰다.
CLEAN: dict = {
    "use_case_id": "UC1",
    "name": "Place an order",
    "requirement_ids": ["FR1", "FR2", "FR3"],
    "nfr_ids": [],
    "preconditions": ["The member is signed in"],
    "trigger": "The member asks to place an order",
    "main_scenario": [
        {"step_number": 1, "sentence": "Member submits the order request",
         "covered_req_ids": ["FR1"]},
        {"step_number": 2, "sentence": "System confirms the requested items are available",
         "covered_req_ids": ["FR2"]},
        {"step_number": 3, "sentence": "System records the order",
         "covered_req_ids": ["FR3"]},
    ],
    "extensions": [
        # 복귀가 성립해야 한다: 3번 스텝(주문 기록)이 가정하는 것은 "주문할 항목이 정해져
        # 있다"이므로, 확장은 대체품 제안에서 끝나지 않고 **교체를 확정**하고 돌아간다.
        {"label": "2a", "branch_step": 2, "condition": "A requested item is not available",
         "handling_steps": [
             {"sub_step": "2a1", "sentence": "System offers the member a substitute item"},
             {"sub_step": "2a2",
              "sentence": "System replaces the unavailable item with the substitute the member accepts"},
         ],
         "outcome": "resume", "resume_at_step": 3},
    ],
    "success_guarantee": [
        {"sentence": "The order is recorded", "covered_req_ids": []}
    ],
    "minimal_guarantee": [],
    "issues": [],
    "repair_iters": 0,
}


@dataclass(frozen=True)
class Seeded:
    """규칙 하나만 어기도록 심은 산출물."""

    rule_id: str
    #: 무엇을 심었는지(사람이 읽는 설명).
    seeded: str
    spec: dict


def _variant(**changes) -> dict:
    """`CLEAN`을 깊은 복사해 일부만 바꾼다(원본 오염 방지)."""
    spec = copy.deepcopy(CLEAN)
    spec.update(changes)
    return spec


def _with_step_sentence(step_number: int, sentence: str) -> dict:
    spec = copy.deepcopy(CLEAN)
    for step in spec["main_scenario"]:
        if step["step_number"] == step_number:
            step["sentence"] = sentence
    return spec


def _with_handling_sentence(sentence: str) -> dict:
    spec = copy.deepcopy(CLEAN)
    spec["extensions"][0]["handling_steps"][0]["sentence"] = sentence
    return spec


def _with_branch_step(branch_step: int) -> dict:
    spec = copy.deepcopy(CLEAN)
    spec["extensions"][0]["branch_step"] = branch_step
    return spec


#: 결정론 검출기가 잡아야 하는 결함들. 규칙마다 하나.
SEEDED: tuple[Seeded, ...] = (
    Seeded(
        "spec.black-box-no-ui-mechanics",
        "주 시나리오 스텝에 UI 용어(screen)를 넣었다",
        _with_step_sentence(3, "System records the order on the confirmation screen"),
    ),
    Seeded(
        "spec.no-branching-in-a-step",
        "스텝 문장에 분기어(if)를 넣었다",
        _with_step_sentence(2, "System checks if the requested items are available"),
    ),
    Seeded(
        "spec.no-control-tokens-in-prose",
        "확장 처리 문장에 종결 토큰(Fail!)을 넣었다",
        _with_handling_sentence("System offers a substitute and the use case ends. Fail!"),
    ),
    Seeded(
        "spec.extension-reference-integrity",
        "확장의 branch_step을 주 시나리오에 없는 번호(9)로 바꿨다",
        _with_branch_step(9),
    ),
    Seeded(
        "spec.contract-completeness",
        "success guarantee를 비웠다",
        _variant(success_guarantee=[]),
    ),
    Seeded(
        "spec.scenario-requirement-reference-integrity",
        "accepted functional requirement is missing from the main scenario coverage",
        _variant(requirement_ids=["FR1", "FR2", "FR3", "FR4"]),
    ),
)


@dataclass(frozen=True)
class SeededSemantic:
    """의미 판정 규칙 하나만 어기도록 심은 산출물(정적 검출기는 통과한다)."""

    rule_id: str
    stage: str
    seeded: str
    #: 검증자가 실제로 받는 모양(단계마다 다르다 — 각 단계의 payload 조립부와 같아야 한다).
    artifact: dict


#: 이 UC가 다뤄야 할 요구사항. `spec.no-scope-creep`의 잣대이고, 나머지 케이스에서는
#: "범위를 벗어나지 않았다"의 근거가 된다.
_REQUIREMENTS = [
    {"id": "FR1", "text": "A member can submit an order for the items they chose."},
    {"id": "FR2", "text": "The system checks item availability before accepting an order."},
    {"id": "FR3", "text": "The system records accepted orders."},
    # FR4가 없으면 `CLEAN`의 확장(대체품 제안)이 요구사항에 없는 기능이 되어 대조군이
    # scope creep으로 걸린다 — 첫 라이브 측정에서 실제로 그랬다(`CLEAN` 주석 참고).
    {"id": "FR4", "text": "When a requested item is unavailable the system offers the "
                          "member a substitute item and uses it if the member accepts."},
    # FR5~FR7은 **seed의 곁가지를 범위 안에 넣기 위한** 것이다. N=5 측정에서
    # `spec.no-scope-creep`이 다른 케이스에 5/5·5/5로 곁따라 걸렸는데, 원인은 내 seed
    # 문장이 목표 결함과 **함께** 범위 밖 기능을 들여왔기 때문이었다("다시 고르게 한다",
    # "항목을 지운다", "로그인을 확인한다"). 곁가지가 걸리면 그 케이스로는 목표 규칙의
    # 정밀도를 읽을 수 없다 — seed는 자기 규칙만 어겨야 한다.
    {"id": "FR5", "text": "The member can choose different items when the request cannot "
                          "be accepted as submitted."},
    {"id": "FR6", "text": "The member can remove items from a submitted request."},
    {"id": "FR7", "text": "Only signed-in members can submit an order."},
]


#: 검증자가 받는 명세의 공개 필드 계약.
SPECIFICATION_REVIEW_FIELDS = (
    "trigger",
    "preconditions",
    "main_scenario",
    "extensions",
    "success_guarantee",
    "minimal_guarantee",
)


def specification_review_payload(spec: dict) -> dict:
    """검증자가 받는 모양.

    ⚠ `modeling.specifications.spec_review_payload`를 **부르지 않고** 같은 모양을 여기서
    조립한다. 그 모듈을
    import하면 설정·LLM 스택이 딸려 와, 이 파일이 **자격증명 없이 도는 성질**을 잃는다
    (`SEEDED_SEMANTIC`이 모듈 로드 시 조립되기 때문이다). 그건 CI 게이트의 전제다.

    그래서 모양이 같은지는 import가 아니라 **테스트가** 지킨다
    (`tests/test_evaluation.py::test_the_seeded_payload_matches_what_the_pipeline_sends`).
    눈금이 파이프라인과 다른 것을 보여 주면 그 수치는 파이프라인에 대한 말이 아니다.
    """
    payload = {key: spec[key] for key in SPECIFICATION_REVIEW_FIELDS}
    if spec.get("name"):
        payload["use_case_name"] = spec["name"]
    payload["requirements_it_must_cover"] = _REQUIREMENTS
    return payload


def _spec_case(rule_id: str, seeded: str, spec: dict) -> SeededSemantic:
    return SeededSemantic(
        rule_id,
        rules.WRITE_SPECIFICATIONS,
        seeded,
        specification_review_payload(spec),
    )


def _broken_remerge() -> dict:
    """확장이 요청을 비워 놓고 "주문을 기록한다"로 복귀한다 — 복귀 지점의 전제가 깨진다.

    `CLEAN`의 확장은 교체를 확정하고 돌아간다(2a2). 그 단계를 없애야 복귀가 깨지므로
    처리 스텝 목록을 통째로 바꾼다 — 첫 문장만 바꾸면 2a2가 남아 상태가 회복된다.
    """
    spec = copy.deepcopy(CLEAN)
    spec["extensions"][0]["handling_steps"] = [
        {"sub_step": "2a1", "sentence": "System removes every requested item from the request"},
    ]
    return spec


def _appended_step(sentence: str) -> dict:
    spec = copy.deepcopy(CLEAN)
    spec["main_scenario"].append(
        {"step_number": 4, "sentence": sentence, "covered_req_ids": []}
    )
    return spec


#: 관계 단계의 깨끗한 산출물(`step4._rel_findings`의 payload 모양).
CLEAN_RELATIONSHIPS: dict = {
    "includes": [],
    "extends": [],
    "generalizations": [],
    "derived_use_cases": [],
}

#: 2단계의 깨끗한 산출물(`step2.review_model`의 payload 모양).
CLEAN_MODEL: dict = {
    "requirements": [
        {"id": "FR1", "text": "A member shall place an order.", "type": "FR"},
    ],
    "actors": [
        {"name": "Member", "description": "A signed-in customer who orders items",
         "kind": "primary", "parent_actor": None},
    ],
    "use_cases": [
        {"name": "Place an order", "primary_actor": "Member", "level": "user_goal",
         "goal": "Have an order accepted and recorded"},
    ],
}


def _rel(**changes) -> dict:
    payload = copy.deepcopy(CLEAN_RELATIONSHIPS)
    payload.update(changes)
    return payload


#: LLM 검증자가 잡아야 하는 결함들. 규칙마다 하나. CI 게이트가 아니다(판정이 결정론이 아니다).
SEEDED_SEMANTIC: tuple[SeededSemantic, ...] = (
    # --- 3단계: 명세 ---
    _spec_case(
        "spec.black-box-no-internal-components",
        "스텝이 내부 저장소를 이름으로 부른다(order database)",
        _with_step_sentence(3, "System saves the order to the order database"),
    ),
    _spec_case(
        "spec.no-hidden-branching",
        "'if' 없이 결과에 따라 갈라지는 스텝을 넣었다(either ... or)",
        _with_step_sentence(
            2, "System either accepts the request or asks the member to choose again"
        ),
    ),
    _spec_case(
        "spec.consequence-is-a-guarantee",
        "감사 기록(자동 결과)을 주 시나리오 스텝으로 넣었다",
        _appended_step("System writes an audit entry for the accepted order"),
    ),
    _spec_case(
        "spec.no-precondition-recheck",
        "전제조건(로그인)을 스텝에서 다시 확인한다",
        _appended_step("System confirms the member is signed in"),
    ),
    _spec_case(
        "spec.no-scope-creep",
        "요구사항에 없는 기능(적립금 지급)을 스텝으로 만들어 넣었다",
        _appended_step("System grants the member loyalty points for the order"),
    ),
    _spec_case(
        "spec.remerge-re-establishes-state",
        "확장이 요청을 비운 뒤 주문 기록 스텝으로 복귀한다(교체 확정 단계를 없앴다)",
        _broken_remerge(),
    ),
    # --- 4단계: 관계 ---
    SeededSemantic(
        "rel.shared-authentication-is-a-precondition", rules.DRAW_DIAGRAM,
        "공유 인증을 include로 뽑았다",
        _rel(includes=[{"base_use_case": "Place an order",
                        "included_use_case": "Authenticate Member",
                        "rationale": "every use case needs it"}],
             derived_use_cases=[{"name": "Authenticate Member",
                                 "origin": "factored_include", "rationale": "shared"}]),
    ),
    SeededSemantic(
        "rel.consequence-is-not-an-include", rules.DRAW_DIAGRAM,
        "감사 로깅(횡단 결과)을 include로 뽑았다",
        _rel(includes=[{"base_use_case": "Place an order",
                        "included_use_case": "Write Audit Log",
                        "rationale": "shared step"}],
             derived_use_cases=[{"name": "Write Audit Log",
                                 "origin": "factored_include", "rationale": "shared"}]),
    ),
    SeededSemantic(
        "rel.failures-stay-inline-extensions", rules.DRAW_DIAGRAM,
        "결제 실패를 파생 유스케이스로 승격해 extend로 붙였다",
        _rel(extends=[{"base_use_case": "Place an order",
                       "extending_use_case": "Handle Payment Failure",
                       "extension_point": "payment", "rationale": "payment can fail"}],
             derived_use_cases=[{"name": "Handle Payment Failure",
                                 "origin": "failure", "rationale": "error path"}]),
    ),
    SeededSemantic(
        "rel.extend-adds-conditional-behavior", rules.DRAW_DIAGRAM,
        "단순 후속 순서(주문 후 배송)를 extend로 표현했다",
        _rel(extends=[{"base_use_case": "Place an order",
                       "extending_use_case": "Ship the order",
                       "extension_point": "after ordering",
                       "rationale": "shipping happens after ordering"}],
             derived_use_cases=[{"name": "Ship the order", "origin": "sequence",
                                 "rationale": "next step"}]),
    ),
    SeededSemantic(
        "rel.generalization-keeps-meaning", rules.DRAW_DIAGRAM,
        "일반화의 부모·자식을 뒤집었다(구체 액터가 부모)",
        _rel(generalizations=[{"parent": "Premium Member", "child": "Member",
                               "kind": "actor",
                               "rationale": "premium members are a kind of member"}]),
    ),
    # --- 2단계: 모델 ---
    SeededSemantic(
        "actors.sud-is-not-an-actor", rules.MODEL_USE_CASES,
        "설계 대상 시스템 자신을 액터로 넣었다",
        {
            "actors": [
                {"name": "Member", "description": "A signed-in customer",
                 "kind": "primary", "parent_actor": None},
                {"name": "Order Service", "description":
                 "The application being designed, which accepts and records orders",
                 "kind": "primary", "parent_actor": None},
            ],
            "use_cases": CLEAN_MODEL["use_cases"],
        },
    ),
    SeededSemantic(
        "usecases.goal-source-grounded", rules.MODEL_USE_CASES,
        "연결된 요구사항에 없는 승인 수명주기를 유스케이스 goal에 추가했다",
        {
            **copy.deepcopy(CLEAN_MODEL),
            "use_cases": [
                {
                    "name": "Place an approved order",
                    "primary_actor": "Member",
                    "level": "user_goal",
                    "goal": "Place an order that was previously approved",
                    "requirement_ids": ["FR1"],
                }
            ],
        },
    ),
)


def clean_artifacts() -> dict[str, dict]:
    """단계별 대조군(결함 없음). 오탐률을 재는 자리다."""
    return {
        rules.WRITE_SPECIFICATIONS: specification_review_payload(CLEAN),
        rules.DRAW_DIAGRAM: copy.deepcopy(CLEAN_RELATIONSHIPS),
        rules.MODEL_USE_CASES: copy.deepcopy(CLEAN_MODEL),
    }


def detection_report() -> dict:
    """검출기가 심어 둔 결함을 잡는지 센다(LLM 없음).

    `detected`가 False인 규칙은 그 규칙에 대한 모든 "0건"이 근거 없다는 뜻이다.
    `also_flagged`는 심은 것 말고 함께 걸린 규칙 — 심은 문장이 두 규칙을 동시에 어겼거나
    검출기가 넘치게 잡는다는 뜻이라, 어느 쪽이든 눈금을 못 믿는다.
    """
    cases = []
    for case in SEEDED:
        flagged = {f.rule_id for f in detectors.spec_findings(case.spec)}
        cases.append({
            "rule_id": case.rule_id,
            "seeded": case.seeded,
            "detected": case.rule_id in flagged,
            "also_flagged": sorted(flagged - {case.rule_id}),
        })

    clean_findings = [f.as_issue() for f in detectors.spec_findings(CLEAN)]
    detected = sum(1 for c in cases if c["detected"])
    return {
        "cases": cases,
        "detected": detected,
        "total": len(cases),
        # 대조군에서 나온 지적은 전부 오탐이다.
        "false_positives": clean_findings,
        # 검출기가 있는데 심어 두지 않은 규칙. 비어 있어야 한다 —
        # 심지 않은 규칙은 눈금이 살아 있는지 아무도 모른다.
        "unseeded_detector_rules": sorted(
            {r.id for r in rules.RULES if r.judged_by == rules.JUDGED_DETECTOR}
            - {c.rule_id for c in SEEDED}
        ),
    }


def unseeded_validator_rules() -> list[str]:
    """의미 판정 규칙 중 심어 두지 않은 것. 눈금을 못 재는 규칙 목록이다."""
    return sorted(
        {r.id for r in rules.RULES if r.judged_by == rules.JUDGED_VALIDATOR}
        - {c.rule_id for c in SEEDED_SEMANTIC}
    )
