"""추적성 집계의 단일 소스 규율.

`check_coverage`(파이프라인 게이트)와 저장용 요구사항 추적이 같은 링크를
각자 굴리다가 **환각 참조의 정의가 갈렸다.** 같은 상태에서 서로 겹치지도 않는 답을 냈고,
하필 파이프라인이 쓰는 쪽이 틀린 쪽이었다.

이 파일이 지키는 것은 수치가 아니라 **한 곳에서만 센다**는 규율이다.
"""
from __future__ import annotations

from app.requirements import traceability
from app.requirements.modeling.use_cases import check_coverage
from app.requirements.traceability import build_requirement_trace


def _state(**over):
    base = {
        "classified": [
            {"id": "FR1", "text": "a", "type": "FR"},
            {"id": "NFR1", "text": "b", "type": "NFR"},
        ],
        "use_cases": [
            {"id": "UC1", "name": "x", "requirement_ids": ["FR1"], "nfr_ids": ["NFR1"]},
        ],
        "use_case_specs": [],
    }
    return base | over


def test_a_real_nfr_listed_under_requirement_ids_is_not_a_hallucination():
    """**이 파일이 생긴 이유.**

    UC가 실재하는 NFR을 `requirement_ids` 칸에 적으면, 예전 `check_coverage`는 그것을
    환각으로 신고했다 — `requirement_ids`를 **FR 목록하고만** 대조했기 때문이다. 칸을
    잘못 고른 것과 없는 id를 지어낸 것은 다른 문제다.
    """
    state = _state(use_cases=[
        {"id": "UC1", "name": "x", "requirement_ids": ["FR1", "NFR1"], "nfr_ids": []},
    ])
    assert check_coverage(state)["coverage"]["unknown_requirement_refs"] == []


def test_an_invented_id_in_nfr_ids_is_a_hallucination():
    """반대쪽 구멍: `nfr_ids`의 없는 id는 아예 대조 대상이 아니었다 — 미탐."""
    state = _state(use_cases=[
        {"id": "UC1", "name": "x", "requirement_ids": ["FR1"], "nfr_ids": ["NFR9"]},
    ])
    assert check_coverage(state)["coverage"]["unknown_requirement_refs"] == ["NFR9"]


def test_the_gate_and_the_saved_trace_agree_on_hallucinations():
    """두 소비자가 **같은 답**을 내야 한다. 갈리면 어느 쪽을 믿을지 알 수 없다."""
    state = _state(use_cases=[
        {"id": "UC1", "name": "x", "requirement_ids": ["FR1", "NFR1", "R9"],
         "nfr_ids": ["NFR9"]},
    ])
    gate = check_coverage(state)["coverage"]["unknown_requirement_refs"]
    saved_trace = build_requirement_trace(state)["unknown_refs"]
    assert gate == saved_trace == ["NFR9", "R9"]


def test_link_kinds_stay_apart():
    """`requirement_ids`(실현 주장)와 `nfr_ids`(제약 부착)는 뜻이 다르다.

    환각 판정만 둘을 합쳐 본다 — "이 id가 존재하는가"는 어느 칸에 적혔든 같은 질문이라서다.
    커버리지·부착은 칸을 구별해야 한다: NFR을 `requirement_ids`에 적었다고 FR 커버리지가
    올라가서는 안 되고, 그 NFR이 부착된 것으로 세어져서도 안 된다.
    """
    trace = traceability.index(_state(use_cases=[
        {"id": "UC1", "name": "x", "requirement_ids": ["NFR1"], "nfr_ids": []},
    ]))
    assert trace.orphan_fr_ids == ("FR1",), "FR1을 아무도 주장하지 않았다"
    assert trace.covered_fr_ids == ()
    assert trace.unattached_nfr_ids == ("NFR1",), "NFR1은 제약으로 붙지 않았다"
    assert trace.unknown_refs == (), "그래도 실재하는 id다"


def test_coverage_ratio_of_an_empty_input_is_not_a_failure():
    """FR이 하나도 없는 입력을 커버리지 0%로 읽으면 빈 실행이 실패로 보인다."""
    assert traceability.index({"classified": [], "use_cases": []}).coverage_ratio == 1.0


def test_deployment_linked_nfr_is_not_reported_as_unattached():
    state = _state(
        use_cases=[{"id": "UC1", "requirement_ids": ["FR1"], "nfr_ids": []}],
        deployment_needs={
            "https_ingress": {"requirementIds": ["NFR1"]},
        },
    )

    trace = traceability.index(state)

    assert trace.attached_nfr_ids == ("NFR1",)
    assert trace.unattached_nfr_ids == ()
    assert check_coverage(state)["coverage"]["unattached_nfr_ids"] == []


def test_functional_constraint_is_not_counted_as_a_realized_goal():
    state = _state(
        classified=[
            {"id": "FR1", "text": "A user submits a request.", "type": "FR"},
            {"id": "FR2", "text": "Concurrent requests preserve order.", "type": "FR"},
        ],
        use_cases=[
            {"id": "UC1", "name": "Submit request", "requirement_ids": ["FR1"], "nfr_ids": []},
        ],
        constraint_applicability={"FR2": ["UC1"]},
    )

    trace = traceability.index(state)

    assert trace.covered_fr_ids == ("FR1",)
    assert trace.orphan_fr_ids == ("FR2",)
    assert trace.ucs_constrained_by["FR2"] == ("UC1",)
    assert trace.accounted_ids == ("FR1", "FR2")
    assert trace.coverage_ratio == 0.5
    assert trace.accounted_ratio == 1.0
    coverage = check_coverage(state)["coverage"]
    assert coverage["unrealized_fr_ids"] == ["FR2"]
    assert coverage["orphan_fr_ids"] == []
    assert coverage["fr_realization_ratio"] == 0.5
    assert coverage["coverage_ratio"] == coverage["goal_coverage_ratio"] == 1.0


def test_constraint_edge_to_an_unknown_use_case_is_visible():
    state = _state(constraint_applicability={"FR1": ["UC9"]})

    coverage = check_coverage(state)["coverage"]

    assert coverage["unknown_use_case_refs"] == ["UC9"]


def test_explicit_global_constraint_is_accounted_without_being_forced_onto_a_use_case():
    state = _state(
        use_cases=[
            {"id": "UC1", "name": "x", "requirement_ids": ["FR1"], "nfr_ids": []}
        ],
        constraint_applicability={"NFR1": []},
    )

    trace = build_requirement_trace(state)

    assert check_coverage(state)["coverage"]["unattached_nfr_ids"] == []
    assert trace["requirements"]["NFR1"]["modeled_as_constraint"] is True
    assert trace["requirements"]["NFR1"]["constrains_use_cases"] == []


def test_use_case_trace_view_includes_functional_constraint_edges():
    state = _state(constraint_applicability={"FR1": ["UC1"]})

    trace = build_requirement_trace(state)

    assert trace["use_cases"]["UC1"]["requirements"] == ["FR1", "NFR1"]


def test_whole_model_accounting_includes_actor_and_capability_evidence():
    state = _state(
        classified=[
            {"id": "R1", "text": "A student is a university user.", "type": "NFR"},
            {"id": "R2", "text": "The system authorizes protected operations.", "type": "FR"},
        ],
        use_cases=[],
        actors=[{"name": "Student", "source_refs": ["R1"]}],
        capability_contract={
            "capabilities": [
                {"id": "authorization", "requirementIds": ["R2"], "decision": "accepted"}
            ]
        },
    )

    coverage = check_coverage(state)["coverage"]

    assert coverage["coverage_ratio"] == 0.0
    assert coverage["goal_coverage_ratio"] == 0.0
    assert coverage["accounted_coverage_ratio"] == 1.0
    assert coverage["unaccounted_requirement_ids"] == []


def test_nfr_actor_evidence_is_not_reported_as_an_unattached_constraint():
    state = _state(
        use_cases=[
            {"id": "UC1", "name": "x", "requirement_ids": ["FR1"], "nfr_ids": []}
        ],
        actors=[{"name": "Student", "source_refs": ["NFR1"]}],
    )

    assert check_coverage(state)["coverage"]["unattached_nfr_ids"] == []


def test_spec_steps_are_traced_per_step_not_just_per_use_case():
    """스텝 단위 추적이 UC 단위보다 정밀하다 — 매트릭스가 그걸 싣는다."""
    trace = traceability.index(_state(use_case_specs=[{
        "use_case_id": "UC1",
        "main_scenario": [
            {"step_number": 1, "sentence": "s", "covered_req_ids": ["FR1"]},
            {"step_number": 3, "sentence": "t", "covered_req_ids": ["FR1"]},
        ],
    }]))
    assert trace.steps_of["FR1"] == ("UC1.1", "UC1.3")


def test_both_consumers_actually_go_through_the_index(monkeypatch):
    """사본이 다시 생기지 않도록, 두 소비자가 색인을 **실제로 거치는지** 본다.

    수치 비교만으로는 부족하다 — 같은 값을 두 벌로 계산해도 통과하고, 그 두 벌이 나중에
    갈리는 것이 이 파일이 막으려는 사고다. 그렇다고 소스에 `traceability.index(`가 있는지
    문자열로 보면 안 된다(C7에서 그 방식이 양방향으로 틀린다는 걸 겪었다). 색인을 바꿔치기
    하고 **바뀐 값이 두 소비자의 출력에 나타나는지**로 확인한다.
    """
    sentinel = traceability.Traceability(
        by_id={"FR1": {"id": "FR1", "text": "a", "type": "FR"}},
        fr_ids=frozenset({"FR1"}),
        ucs_claiming={"GHOST": ("UC1",)},
    )
    monkeypatch.setattr(traceability, "index", lambda _state: sentinel)

    state = _state()
    assert check_coverage(state)["coverage"]["unknown_requirement_refs"] == ["GHOST"]
    assert build_requirement_trace(state)["unknown_refs"] == ["GHOST"]
