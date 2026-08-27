"""STEP 2 노드 테스트 (액터/유스케이스 도출 + 커버리지 점검).

세 층위:
  1. check_coverage       — LLM 없이 결정론적 집합 연산 검증.
  2. identify_actors /
     identify_use_cases   — invoke_structured 목킹으로 dict 성형·id 부여 검증.
  3. 라이브(RUN_LIVE_TESTS=1) — 실제 NIM으로 tests/datasets/*.json end-to-end.

테스트 입력은 tests/datasets/*.json 에 분리 보관하며, conftest의 load_dataset/
dataset_names 로 로드한다. 결정론/목킹 테스트는 shopping_mall 세트를 고정으로 쓰고,
라이브 테스트는 모든 데이터셋을 파라미터라이즈한다(README 참고).

직접 돌려보려면:
    RUN_LIVE_TESTS=1 python -m pytest tests/test_step2.py -k live -s
    RUN_LIVE_TESTS=1 python -m pytest tests/test_step2.py -k "live and note_taking" -s
    STEP2_DATASET=shopping_mall RUN_LIVE_TESTS=1 python -m pytest tests/test_step2.py -k live -s
"""
import os

import pytest
from conftest import dataset_names, load_dataset

from app.requirements.agent.steps import step2_usecases as s2
from app.requirements.schemas import Actor, ActorResult, UseCase, UseCaseResult

# 결정론/목킹 테스트가 고정으로 쓰는 세트 (id R1..R5, N1..N2 를 이 테스트들이 참조).
SAMPLE_CLASSIFIED = load_dataset("shopping_mall")["classified"]


# ---------------------------------------------------------------------------
# 1. check_coverage — 결정론적
# ---------------------------------------------------------------------------
def test_coverage_full_with_unattached_nfr_and_unknown_ref():
    state = {
        "classified": SAMPLE_CLASSIFIED,
        "use_cases": [
            # 정상 매핑 + R9(존재하지 않는 id) 환각 참조
            {"id": "UC1", "requirement_ids": ["R1", "R3", "R4", "R9"], "nfr_ids": ["N1"]},
            {"id": "UC2", "requirement_ids": ["R2"], "nfr_ids": []},
            {"id": "UC3", "requirement_ids": ["R5"], "nfr_ids": []},
        ],
    }
    cov = s2.check_coverage(state)["coverage"]

    assert cov["fr_total"] == 5
    assert cov["covered_fr_ids"] == ["R1", "R2", "R3", "R4", "R5"]
    assert cov["orphan_fr_ids"] == []
    assert cov["coverage_ratio"] == 1.0
    # N2는 어떤 UC에도 안 붙음 → 전역 제약 후보
    assert cov["unattached_nfr_ids"] == ["N2"]
    # R9는 제공되지 않은 id → 환각 표면화
    assert cov["unknown_requirement_refs"] == ["R9"]


def test_coverage_flags_orphan_fr():
    state = {
        "classified": SAMPLE_CLASSIFIED,
        "use_cases": [{"id": "UC1", "requirement_ids": ["R1", "R2"], "nfr_ids": []}],
    }
    cov = s2.check_coverage(state)["coverage"]

    assert cov["orphan_fr_ids"] == ["R3", "R4", "R5"]
    assert cov["coverage_ratio"] == 0.4  # 2/5


def test_coverage_empty_use_cases_all_orphan():
    state = {"classified": SAMPLE_CLASSIFIED, "use_cases": []}
    cov = s2.check_coverage(state)["coverage"]

    assert cov["orphan_fr_ids"] == ["R1", "R2", "R3", "R4", "R5"]
    assert cov["coverage_ratio"] == 0.0
    assert cov["unattached_nfr_ids"] == ["N1", "N2"]


def test_coverage_no_fr_is_full():
    state = {"classified": [{"id": "N1", "text": "x", "type": "NFR"}], "use_cases": []}
    cov = s2.check_coverage(state)["coverage"]

    assert cov["fr_total"] == 0
    assert cov["coverage_ratio"] == 1.0  # FR이 없으면 공허참 → 1.0


# ---------------------------------------------------------------------------
# 2. identify_actors / identify_use_cases — invoke_structured 목킹
# ---------------------------------------------------------------------------
def test_identify_actors_uses_structural_sources_and_canonical_parent(monkeypatch):
    result = ActorResult(actors=[
        Actor(name="Member", description="specialized role", parent_actor="  user ",
              source_refs=["NFR1", "FR1"]),
        Actor(name="User", description="general role", source_refs=["NFR1"]),
    ])
    monkeypatch.setattr(s2, "invoke_structured", lambda schema, messages: result)

    out = s2.identify_actors({
        "classified": [
            {"id": "FR1", "text": "A user submits a request.", "type": "FR"},
            {"id": "NFR1", "text": "Member specializes user.", "type": "NFR"},
        ],
    })

    member = next(actor for actor in out["actors"] if actor["name"] == "Member")
    assert member["parent_actor"] == "User"
    assert member["source_refs"] == ["FR1", "NFR1"]


def test_identify_actors_shapes_dicts(monkeypatch):
    result = ActorResult(
        actors=[
            Actor(name="Registered User", description="shopper", source_refs=["R1"]),
            Actor(name="Address Service", description="external", source_refs=["R3"]),
        ]
    )
    monkeypatch.setattr(s2, "invoke_structured", lambda schema, messages: result)

    out = s2.identify_actors({"classified": SAMPLE_CLASSIFIED})
    actors = out["actors"]

    assert out["phase"] == "actors"
    assert [a["name"] for a in actors] == ["Registered User", "Address Service"]
    assert actors[0]["source_refs"] == ["R1"]
    assert "kind" not in actors[0]
    assert "kind" not in actors[1]


def test_identify_actors_empty_when_no_accepted_requirements(monkeypatch):
    called = False

    def _fake(schema, messages):
        nonlocal called
        called = True
        return ActorResult(actors=[])

    monkeypatch.setattr(s2, "invoke_structured", _fake)

    out = s2.identify_actors({"classified": []})
    assert out["actors"] == []
    assert called is False  # FR이 없으면 LLM을 아예 호출하지 않음


def test_identify_use_cases_assigns_ids_and_maps_fields(monkeypatch):
    result = UseCaseResult(
        use_cases=[
            UseCase(
                name="Log in", primary_actor="Registered User",
                goal="authenticate", requirement_ids=["R1"], nfr_ids=[],
            ),
            UseCase(
                name="Place an order", primary_actor="Registered User",
                goal="buy items", requirement_ids=["R3", "R4"], nfr_ids=["N1"],
            ),
        ]
    )
    monkeypatch.setattr(s2, "invoke_structured", lambda schema, messages: result)

    out = s2.identify_use_cases(
        {"classified": SAMPLE_CLASSIFIED, "actors": [{"name": "Registered User", "kind": "primary", "description": "s"}]}
    )
    ucs = out["use_cases"]

    assert out["phase"] == "use_cases"
    assert [u["id"] for u in ucs] == ["UC1", "UC2"]
    assert ucs[0]["level"] == "user_goal"  # 스키마 기본값
    # 서브펑션 FR(R4)이 상위 UC의 requirement_ids로 흡수됨 (goal-leveling)
    assert ucs[1]["requirement_ids"] == ["R3", "R4"]
    assert ucs[1]["nfr_ids"] == ["N1"]  # NFR은 제약으로만 부착
    # step2는 시나리오를 만들지 않는다 (시나리오/확장은 step3)
    assert "main_scenario_steps" not in ucs[1]


def test_identify_use_cases_prunes_unused_actors_but_keeps_ancestors(monkeypatch):
    monkeypatch.setattr(s2, "invoke_structured", lambda schema, messages: UseCaseResult(use_cases=[
        UseCase(
            name="Submit request", primary_actor="Customer",
            supporting_actors=["Notification Provider"], goal="submit a request",
            requirement_ids=["R1"],
        )
    ]))

    out = s2.identify_use_cases({
        "classified": [{"id": "R1", "text": "A customer submits a request.", "type": "FR"}],
        "actors": [
            {"name": "Account Holder", "description": "base role", "parent_actor": None},
            {"name": "Customer", "description": "requester", "parent_actor": "Account Holder"},
            {"name": "Notification Provider", "description": "external service", "parent_actor": None},
            {"name": "Unused Role", "description": "not involved", "parent_actor": None},
        ],
    })

    assert [actor["name"] for actor in out["actors"]] == [
        "Account Holder", "Customer", "Notification Provider"
    ]


def test_local_use_case_edit_prunes_actors_the_same_way(monkeypatch):
    monkeypatch.setattr(s2, "invoke_structured", lambda schema, messages: UseCaseResult(use_cases=[
        UseCase(name="Submit request", primary_actor="Customer", goal="submit a request",
                requirement_ids=["R1"])
    ]))
    actors = [
        {"name": "Account Holder", "description": "base role", "parent_actor": None},
        {"name": "Customer", "description": "requester", "parent_actor": "Account Holder"},
        {"name": "Unused Role", "description": "not involved", "parent_actor": None},
    ]

    out = s2.identify_use_cases({
        "classified": [{"id": "R1", "text": "A customer submits a request.", "type": "FR"}],
        "actors": actors,
        "use_cases": [{
            "id": "UC1", "name": "Submit request", "primary_actor": "Customer",
            "supporting_actors": [], "level": "user_goal", "goal": "submit a request",
            "requirement_ids": ["R1"], "nfr_ids": [],
        }],
    }, feedback="clarify the request", target_ids=["UC1"])

    assert [actor["name"] for actor in out["actors"]] == ["Account Holder", "Customer"]


def test_identify_use_cases_retries_a_dangling_actor_reference_once(monkeypatch):
    calls = []

    def fake(schema, messages):
        calls.append(messages[-1].content)
        primary = "Unknown actor" if len(calls) == 1 else "  customer "
        return UseCaseResult(use_cases=[
            UseCase(name="Submit request", primary_actor=primary, goal="submit a request",
                    requirement_ids=["R1"]),
        ])

    monkeypatch.setattr(s2, "invoke_structured", fake)
    out = s2.identify_use_cases({
        "classified": [{"id": "R1", "text": "A customer submits a request.", "type": "FR"}],
        "actors": [{"name": "Customer", "description": "requester", "source_refs": ["R1"]}],
    })

    assert out["use_cases"][0]["primary_actor"] == "Customer"
    assert len(calls) == 2
    assert "ACTOR IDENTITY REPAIR" in calls[1]


def test_explicit_sign_in_goal_is_kept_as_a_use_case(monkeypatch):
    result = UseCaseResult(use_cases=[
        UseCase(name="Sign in", primary_actor="User", goal="access an account",
                requirement_ids=["R1"]),
        UseCase(name="View account", primary_actor="User", goal="view account details",
                requirement_ids=["R2"]),
    ])
    monkeypatch.setattr(s2, "invoke_structured", lambda schema, messages: result)

    out = s2.identify_use_cases({
        "classified": [
            {"id": "R1", "text": "A user can sign in.", "type": "FR"},
            {"id": "R2", "text": "A user can view account details.", "type": "FR"},
        ],
        "actors": [{"name": "User", "description": "account holder", "source_refs": ["R1", "R2"]}],
    })

    assert {use_case["name"] for use_case in out["use_cases"]} == {"Sign in", "View account"}


def test_identify_use_cases_local_edit_preserves_siblings(monkeypatch):
    # target_ids로 국소 편집: 대상 UC2만 재생성, 형제 UC1과 id는 그대로 보존.
    existing = [
        {"id": "UC1", "name": "Log in", "primary_actor": "U", "level": "user_goal",
         "goal": "auth", "requirement_ids": ["R1"], "nfr_ids": []},
        {"id": "UC2", "name": "Place order", "primary_actor": "U", "level": "user_goal",
         "goal": "buy", "requirement_ids": ["R3"], "nfr_ids": []},
    ]
    captured = {}

    def fake(schema, messages):
        captured["human"] = messages[-1].content
        # 모델이 같은 개수/순서로 전체 목록 반환(UC2만 수정).
        return UseCaseResult(use_cases=[
            UseCase(name="Log in", primary_actor="U", goal="auth", requirement_ids=["R1"]),
            UseCase(name="Place order and pay", primary_actor="U", goal="buy and pay",
                    requirement_ids=["R3", "R4"]),
        ])

    monkeypatch.setattr(s2, "invoke_structured", fake)

    out = s2.identify_use_cases(
        {"classified": SAMPLE_CLASSIFIED,
         "actors": [{"name": "U", "kind": "primary", "description": "d"}],
         "use_cases": existing},
        feedback="결제를 UC2에 포함", target_ids=["UC2"],
    )
    ucs = out["use_cases"]

    assert [u["id"] for u in ucs] == ["UC1", "UC2"]          # id 위치 보존
    assert ucs[0]["name"] == "Log in"                        # 형제 그대로
    assert ucs[1]["name"] == "Place order and pay"           # 대상만 변경
    assert "UC2" in captured["human"]                        # 대상이 프롬프트에 명시됨


def test_identify_use_cases_local_edit_reindexes_on_count_change(monkeypatch):
    # 국소 편집인데 개수가 바뀌면(모델이 UC 추가/삭제) 회귀 방지로 전체 id 재부여.
    existing = [
        {"id": "UC1", "name": "A", "primary_actor": "U", "level": "user_goal",
         "goal": "g", "requirement_ids": ["R1"], "nfr_ids": []},
    ]
    monkeypatch.setattr(s2, "invoke_structured", lambda schema, messages: UseCaseResult(use_cases=[
        UseCase(name="A", primary_actor="U", goal="g", requirement_ids=["R1"]),
        UseCase(name="B", primary_actor="U", goal="g2", requirement_ids=["R2"]),
    ]))

    out = s2.identify_use_cases(
        {
            "classified": SAMPLE_CLASSIFIED,
            "actors": [{"name": "U", "description": "actor", "source_refs": ["R1"]}],
            "use_cases": existing,
        },
        feedback="UC1을 둘로 쪼개줘", target_ids=["UC1"],
    )
    assert [u["id"] for u in out["use_cases"]] == ["UC1", "UC2"]


def test_identify_use_cases_empty_when_no_fr(monkeypatch):
    monkeypatch.setattr(
        s2, "invoke_structured",
        lambda schema, messages: pytest.fail("no FR면 호출되면 안 됨"),
    )
    # actors는 상류(identify_actors)가 항상 채운다 — FR이 없으면 빈 목록으로. 그 상태를
    # 그대로 준다(키를 빼면 상태 계약이 배선 오류로 잡는다).
    out = s2.identify_use_cases(
        {"classified": [{"id": "N1", "text": "x", "type": "NFR"}], "actors": []}
    )
    assert out["use_cases"] == []


def _uc_result(*id_groups):
    return UseCaseResult(use_cases=[
        UseCase(name=f"UC{i}", primary_actor="U", goal="g", requirement_ids=list(g))
        for i, g in enumerate(id_groups, 1)
    ])


def test_actor_goal_audit_can_restore_an_explicit_omitted_goal(monkeypatch):
    # 1차엔 R1만 커버(R2~R5 고아) → FR별 작업이 누락 목표를 각각 보충.
    calls = {"n": 0}

    def fake(schema, messages):
        calls["n"] += 1
        if schema is UseCaseResult:
            return _uc_result(["R1"])
        content = messages[-1].content
        requirement_id = next(
            requirement_id
            for requirement_id in ("R2", "R3", "R4", "R5")
            if f"- {requirement_id}:" in content.split(
                "[FUNCTIONAL REQUIREMENT UNDER AUDIT]", 1
            )[1].split("[OTHER ACCEPTED", 1)[0]
        )
        return s2._RequirementTraceSlice(
            requirement_id=requirement_id,
            missing_use_case=s2._MissingUseCaseCandidate(
                name=f"Handle {requirement_id}",
                primary_actor="U",
                goal=f"complete {requirement_id}",
            ),
        )

    monkeypatch.setattr(s2, "invoke_structured", fake)
    out = s2.identify_use_cases(
        {"classified": SAMPLE_CLASSIFIED, "actors": [{"name": "U", "kind": "primary", "description": "d"}]}
    )
    covered = {rid for uc in out["use_cases"] for rid in uc["requirement_ids"]}

    assert {"R1", "R2", "R3", "R4", "R5"} <= covered   # 고아 해소
    assert calls["n"] == 7  # 최초 제안 + 고아 FR 4개 + NFR trace 2개


def test_orphan_audit_maps_an_explicit_cross_cutting_fr_without_adding_a_use_case(
    monkeypatch,
):
    calls = {"n": 0}

    def fake(schema, messages):
        calls["n"] += 1
        if schema is UseCaseResult:
            return _uc_result(["R1"], ["R2"])
        return s2._RequirementTraceSlice(
            requirement_id="R3", constrains_use_case_names=["UC1", "UC2"]
        )

    monkeypatch.setattr(s2, "invoke_structured", fake)
    out = s2.identify_use_cases(
        {
            "classified": [
                {"id": "R1", "text": "A user starts the first operation.", "type": "FR"},
                {"id": "R2", "text": "A user starts the second operation.", "type": "FR"},
                {
                    "id": "R3",
                    "text": "The first and second operations preserve a shared invariant.",
                    "type": "FR",
                },
            ],
            "actors": [{"name": "U", "description": "actor", "source_refs": ["R1"]}],
        }
    )

    assert len(out["use_cases"]) == 2
    assert all("R3" not in use_case["requirement_ids"] for use_case in out["use_cases"])
    assert out["constraint_applicability"] == {"R3": ["UC1", "UC2"]}
    assert calls["n"] == 2


def test_a_shared_mandatory_action_is_realized_by_each_named_goal(monkeypatch):
    def fake(schema, _messages):
        if schema is UseCaseResult:
            return _uc_result(["R1"], ["R2"])
        return s2._RequirementTraceSlice(
            requirement_id="R3",
            realized_by_use_case_names=["UC1", "UC2"],
        )

    monkeypatch.setattr(s2, "invoke_structured", fake)
    out = s2.identify_use_cases(
        {
            "classified": [
                {"id": "R1", "text": "A user starts the first operation.", "type": "FR"},
                {"id": "R2", "text": "A user starts the second operation.", "type": "FR"},
                {
                    "id": "R3",
                    "text": "Both operations perform the same mandatory eligibility check.",
                    "type": "FR",
                },
            ],
            "actors": [{"name": "U", "description": "actor", "source_refs": ["R1"]}],
        }
    )

    assert all("R3" in use_case["requirement_ids"] for use_case in out["use_cases"])
    assert out["constraint_applicability"] == {}


def test_trace_slice_can_remove_an_unsupported_broad_multi_mapping(monkeypatch):
    def fake(schema, messages):
        if schema is UseCaseResult:
            return _uc_result(["R1", "R3"], ["R2", "R3"])
        return s2._RequirementTraceSlice(requirement_id="R3")

    monkeypatch.setattr(s2, "invoke_structured", fake)
    out = s2.identify_use_cases(
        {
            "classified": [
                {"id": "R1", "text": "A user starts the first operation.", "type": "FR"},
                {"id": "R2", "text": "A user starts the second operation.", "type": "FR"},
                {
                    "id": "R3",
                    "text": "Every protected operation must be authorized.",
                    "type": "FR",
                },
            ],
            "actors": [{"name": "U", "description": "actor", "source_refs": ["R1"]}],
        }
    )

    assert all("R3" not in use_case["requirement_ids"] for use_case in out["use_cases"])
    assert out["constraint_applicability"] == {}


def test_actor_domain_fact_is_not_mislabeled_as_a_global_constraint(monkeypatch):
    def fake(schema, _messages):
        if schema is UseCaseResult:
            return _uc_result(["R1"])
        return s2._RequirementTraceSlice(requirement_id="R2")

    monkeypatch.setattr(s2, "invoke_structured", fake)
    out = s2.identify_use_cases(
        {
            "classified": [
                {"id": "R1", "text": "A student views available courses.", "type": "FR"},
                {"id": "R2", "text": "A student is a university member.", "type": "FR"},
            ],
            "actors": [
                {"name": "Student", "description": "member", "source_refs": ["R1", "R2"]}
            ],
        }
    )

    assert out["constraint_applicability"] == {}


def test_nfr_labeled_actor_fact_is_not_mislabeled_as_a_constraint(monkeypatch):
    def fake(schema, _messages):
        if schema is UseCaseResult:
            return _uc_result(["R1"])
        return s2._RequirementTraceSlice(requirement_id="N1")

    monkeypatch.setattr(s2, "invoke_structured", fake)
    out = s2.identify_use_cases(
        {
            "classified": [
                {"id": "R1", "text": "A member views available courses.", "type": "FR"},
                {"id": "N1", "text": "A student is a university member.", "type": "NFR"},
            ],
            "actors": [{"name": "Student", "description": "member", "source_refs": ["N1"]}],
        }
    )

    assert out["constraint_applicability"] == {}


def test_constraint_slices_attach_only_explicitly_scoped_nfrs(monkeypatch):
    def fake(schema, messages):
        if schema is UseCaseResult:
            return _uc_result(["R1"])
        content = messages[-1].content
        audited = content.split("UNDER AUDIT]", 1)[1].split("[OTHER ACCEPTED", 1)[0]
        requirement_id = "N1" if "- N1:" in audited else "N2"
        return s2._RequirementTraceSlice(
            requirement_id=requirement_id,
            constrains_use_case_names=["UC1"] if requirement_id == "N1" else [],
        )

    monkeypatch.setattr(s2, "invoke_structured", fake)
    out = s2.identify_use_cases(
        {
            "classified": [
                {"id": "R1", "text": "A user searches the catalog.", "type": "FR"},
                {
                    "id": "N1",
                    "text": "Catalog search completes within one second.",
                    "type": "NFR",
                },
                {
                    "id": "N2",
                    "text": "All stored data is durable.",
                    "type": "NFR",
                },
            ],
            "actors": [{"name": "U", "description": "actor", "source_refs": ["R1"]}],
        }
    )

    assert out["use_cases"][0]["nfr_ids"] == ["N1"]


def test_actor_goal_audit_runs_without_a_coverage_iteration_budget(monkeypatch):
    monkeypatch.setattr(s2, "invoke_structured", lambda schema, messages: _uc_result(["R1"]))

    out = s2.identify_use_cases({
        "classified": SAMPLE_CLASSIFIED,
        "actors": [{"name": "U", "description": "actor", "source_refs": ["R1"]}],
    })
    covered = {rid for uc in out["use_cases"] for rid in uc["requirement_ids"]}
    assert covered == {"R1"}   # 예산 소진, 나머지는 이후 check_coverage가 고아로 표면화


def _reviewed_state():
    return {
        "phase": "use_cases",
        "classified": [
            {"id": "R1", "text": "A user submits a request.", "type": "FR"},
        ],
        "actors": [
            {
                "name": "User",
                "description": "requester",
                "parent_actor": None,
                "source_refs": ["R1"],
            },
        ],
        "use_cases": [
            {
                "id": "UC1",
                "name": "Submit request badly",
                "primary_actor": "User",
                "supporting_actors": [],
                "level": "user_goal",
                "goal": "submit a request",
                "requirement_ids": ["R1"],
                "nfr_ids": [],
            },
        ],
    }


def _candidate_use_case(**updates):
    candidate = {
        "id": "UC1",
        "name": "Submit request",
        "primary_actor": "User",
        "supporting_actors": [],
        "level": "user_goal",
        "goal": "submit a request",
        "requirement_ids": ["R1"],
        "nfr_ids": [],
    }
    candidate.update(updates)
    return candidate


def _model_finding(rule_id, text):
    return f"[model] {text} {s2.rules.tag_of(rule_id)}"


def test_model_review_accepts_one_use_case_only_repair_when_findings_decrease(monkeypatch):
    state = _reviewed_state()
    calls = {"reviews": 0, "repairs": 0}

    def fake_review(*args, **kwargs):
        calls["reviews"] += 1
        findings = (
            [
                _model_finding("actors.sud-is-not-an-actor", "first defect"),
                    _model_finding("usecases.user-goal-level", "second defect"),
            ]
            if calls["reviews"] == 1
            else [_model_finding("usecases.user-goal-level", "remaining defect")]
        )
        return s2.validator.Review(findings=findings)

    def fake_identify(received, feedback="", target_ids=None):
        calls["repairs"] += 1
        assert received["actors"] is state["actors"]
        assert received["classified"] is state["classified"]
        assert target_ids is None
        assert "first defect" in feedback
        return {"use_cases": [_candidate_use_case()], "phase": "use_cases"}

    monkeypatch.setattr(s2.validator, "review", fake_review)
    monkeypatch.setattr(s2, "identify_use_cases", fake_identify)

    out = s2.review_model(state)

    assert calls == {"reviews": 2, "repairs": 1}
    assert out["use_cases"] == [_candidate_use_case()]
    assert "remaining defect" in out["model_review"]["issues"][0]


def test_model_review_keeps_original_when_the_single_repair_does_not_improve(monkeypatch):
    state = _reviewed_state()
    calls = {"reviews": 0, "repairs": 0}

    def fake_review(*args, **kwargs):
        calls["reviews"] += 1
        return s2.validator.Review(findings=["same defect"])

    def fake_identify(*args, **kwargs):
        calls["repairs"] += 1
        return {"use_cases": [_candidate_use_case()], "phase": "use_cases"}

    monkeypatch.setattr(s2.validator, "review", fake_review)
    monkeypatch.setattr(s2, "identify_use_cases", fake_identify)

    out = s2.review_model(state)

    assert calls == {"reviews": 2, "repairs": 1}
    assert "use_cases" not in out
    assert out["model_review"]["issues"] == ["same defect"]


@pytest.mark.parametrize(
    "candidate",
    [
        _candidate_use_case(requirement_ids=["UNKNOWN"]),
        _candidate_use_case(primary_actor="Unknown actor"),
    ],
    ids=["unknown-requirement", "new-actor-reference"],
)
def test_model_review_rejects_a_semantically_clean_repair_with_reference_regression(
    monkeypatch, candidate,
):
    state = _reviewed_state()
    reviews = iter([
        s2.validator.Review(findings=["repair this"]),
        s2.validator.Review(findings=[]),
    ])
    monkeypatch.setattr(s2.validator, "review", lambda *args, **kwargs: next(reviews))
    monkeypatch.setattr(
        s2,
        "identify_use_cases",
        lambda *args, **kwargs: {"use_cases": [candidate], "phase": "use_cases"},
    )

    out = s2.review_model(state)

    assert "use_cases" not in out
    assert out["model_review"]["issues"] == ["repair this"]


def test_model_review_rejects_a_repair_that_creates_new_coverage_gaps(monkeypatch):
    state = _reviewed_state()
    state["classified"].append(
        {"id": "R2", "text": "A user reviews the result.", "type": "FR"}
    )
    state["use_cases"][0]["requirement_ids"] = ["R1", "R2"]
    reviews = iter([
        s2.validator.Review(findings=["repair this"]),
        s2.validator.Review(findings=[]),
    ])
    monkeypatch.setattr(s2.validator, "review", lambda *args, **kwargs: next(reviews))
    monkeypatch.setattr(
        s2,
        "identify_use_cases",
        lambda *args, **kwargs: {
            "use_cases": [_candidate_use_case(requirement_ids=["R1"])],
            "phase": "use_cases",
        },
    )

    out = s2.review_model(state)

    assert "use_cases" not in out


# ---------------------------------------------------------------------------
# 3. 라이브 end-to-end — 실제 NIM (옵트인)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_TESTS") != "1",
    reason="라이브 NIM 테스트는 RUN_LIVE_TESTS=1 일 때만 실행",
)
@pytest.mark.parametrize("dataset_name", dataset_names())
def test_live_step2(dataset_name):
    """데이터셋별로 액터→유스케이스→커버리지를 실제 NIM으로 도출한다.

    출력을 보려면 -s 옵션을 붙여라. LLM 비결정성 때문에 개수/이름 대신
    구조 불변식만 검증한다. 특정 데이터셋만 돌리려면 -k 또는 STEP2_DATASET 사용.
    """
    selected = os.getenv("STEP2_DATASET")
    if selected and dataset_name not in [s.strip() for s in selected.split(",")]:
        pytest.skip(f"STEP2_DATASET={selected} 에 없는 데이터셋({dataset_name})")

    classified = load_dataset(dataset_name)["classified"]
    fr_ids = {r["id"] for r in classified if r["type"] == "FR"}
    nfr_ids = {r["id"] for r in classified if r["type"] == "NFR"}

    state = {"classified": classified}
    state.update(s2.identify_actors(state))
    state.update(s2.identify_use_cases(state))
    cov = s2.check_coverage(state)["coverage"]

    import json
    print(f"\n########## dataset: {dataset_name} ##########")
    print("== ACTORS ==")
    print(json.dumps(state["actors"], indent=2, ensure_ascii=False))
    print("== USE CASES ==")
    print(json.dumps(state["use_cases"], indent=2, ensure_ascii=False))
    print("== COVERAGE ==")
    print(json.dumps(cov, indent=2, ensure_ascii=False))

    # 구조 불변식 (데이터셋 무관)
    assert len(state["actors"]) >= 1
    assert all(a["kind"] in ("primary", "supporting") for a in state["actors"])
    assert len(state["use_cases"]) >= 1
    for uc in state["use_cases"]:
        assert uc["id"].startswith("UC")
        assert uc["level"] in ("summary", "user_goal", "subfunction")
        # 참조하는 FR/NFR id는 실제 입력 id 안에 있어야 함
        assert set(uc["requirement_ids"]) <= fr_ids
        assert set(uc["nfr_ids"]) <= nfr_ids
    # NFR은 유스케이스로 승격되지 않는다
    all_reqs = {r for uc in state["use_cases"] for r in uc["requirement_ids"]}
    assert not (all_reqs & nfr_ids)
    assert cov["fr_total"] == len(fr_ids)
