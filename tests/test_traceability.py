"""추적성 집계의 단일 소스 규율.

`check_coverage`(파이프라인 게이트)와 `build_rtm`(저장 시점 매트릭스)이 같은 추적 링크를
각자 굴리다가 **환각 참조의 정의가 갈렸다.** 같은 상태에서 서로 겹치지도 않는 답을 냈고,
하필 파이프라인이 쓰는 쪽이 틀린 쪽이었다.

이 파일이 지키는 것은 수치가 아니라 **한 곳에서만 센다**는 규율이다.
"""
from __future__ import annotations

from app.requirements.agent import traceability
from app.requirements.agent.rtm import build_rtm
from app.requirements.agent.steps.step2_usecases import check_coverage


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


def test_the_gate_and_the_matrix_agree_on_hallucinations():
    """두 소비자가 **같은 답**을 내야 한다. 갈리면 어느 쪽을 믿을지 알 수 없다."""
    state = _state(use_cases=[
        {"id": "UC1", "name": "x", "requirement_ids": ["FR1", "NFR1", "R9"],
         "nfr_ids": ["NFR9"]},
    ])
    gate = check_coverage(state)["coverage"]["unknown_requirement_refs"]
    matrix = build_rtm(state)["unknown_refs"]
    assert gate == matrix == ["NFR9", "R9"]


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
    assert build_rtm(state)["unknown_refs"] == ["GHOST"]
