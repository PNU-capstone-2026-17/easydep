"""STEP 4 노드 테스트 (관계 식별 + PlantUML 다이어그램 렌더).

  1. render_diagram — LLM 없는 순수 함수. 관계 → PlantUML 라인 매핑 검증.
  2. identify_relationships — invoke_structured 목킹 + 주액터 association 결정론 보강.
  3. 라이브(RUN_LIVE_TESTS=1) — step2→step3→step4 e2e.

직접 돌려보려면:
    RUN_LIVE_TESTS=1 python -m pytest tests/test_step4.py -k live -s
"""
import os

import pytest

from app.agent.steps import step2_usecases as s2
from app.agent.steps import step3_specifications as s3
from app.agent.steps import step4_diagram as s4
from app.schemas import (
    Association,
    DerivedUseCase,
    ExtendRelation,
    GeneralizationRelation,
    IncludeRelation,
    RelationshipCritique,
    RelationshipModel,
)
from conftest import dataset_names, load_dataset


# ---------------------------------------------------------------------------
# 1. render_diagram — 결정론적 순수 함수
# ---------------------------------------------------------------------------
def test_render_diagram_association_and_include():
    state = {
        "actors": [{"name": "Registered User", "kind": "primary", "description": "x"}],
        "use_cases": [
            {"id": "UC1", "name": "Log in", "primary_actor": "Registered User"},
            {"id": "UC2", "name": "Place order", "primary_actor": "Registered User"},
        ],
        "relationships": {
            "associations": [{"actor": "Registered User", "use_case": "Log in"}],
            "includes": [{"base_use_case": "Place order", "included_use_case": "Authenticate"}],
            "extends": [],
            "generalizations": [],
            "derived_use_cases": [{"name": "Authenticate", "origin": "factored_include"}],
        },
    }
    d = s4.render_diagram(state)["diagram"]

    assert d.startswith("@startuml") and d.rstrip().endswith("@enduml")
    assert 'actor "Registered User" as Registered_User' in d
    assert 'usecase "Log in" as UC1' in d
    assert 'usecase "Authenticate" as D1' in d           # 파생 UC는 D*
    assert "Registered_User --- UC1" in d                 # association(무방향)
    assert "UC2 ..> D1 : <<include>>" in d                # include: base ..> included


def test_render_diagram_extend_and_generalization():
    state = {
        "actors": [
            {"name": "Guest", "kind": "primary", "description": "g"},
            {"name": "Member", "kind": "primary", "description": "m"},
        ],
        "use_cases": [{"id": "UC1", "name": "Browse", "primary_actor": "Guest"}],
        "relationships": {
            "associations": [],
            "includes": [],
            "extends": [{"base_use_case": "Browse", "extending_use_case": "Apply coupon", "extension_point": "3a"}],
            "generalizations": [{"parent": "Guest", "child": "Member", "kind": "actor"}],
            "derived_use_cases": [{"name": "Apply coupon", "origin": "promoted_extend"}],
        },
    }
    d = s4.render_diagram(state)["diagram"]

    # extend: 확장 UC(Apply coupon=D1)가 기반 UC(Browse=UC1)를 향함
    assert "D1 ..> UC1 : <<extend>>" in d
    # actor 일반화
    assert "Guest <|-- Member" in d


def test_render_diagram_supporting_actor_direction_and_placement():
    state = {
        "actors": [
            {"name": "Customer", "kind": "primary", "description": "shopper"},
            {"name": "Payment Gateway", "kind": "supporting", "description": "external"},
        ],
        "use_cases": [{"id": "UC1", "name": "Place order", "primary_actor": "Customer"}],
        "relationships": {
            "associations": [
                {"actor": "Customer", "use_case": "Place order"},
                {"actor": "Payment Gateway", "use_case": "Place order"},
            ],
            "includes": [], "extends": [], "generalizations": [], "derived_use_cases": [],
        },
    }
    d = s4.render_diagram(state)["diagram"]

    # 연결선은 무방향(---). 배치 순서로 primary/supporting을 구분한다:
    #  primary는 actor --- UC, supporting은 UC --- actor 순으로 렌더.
    assert "Customer --- UC1" in d
    assert "UC1 --- Payment_Gateway" in d
    assert "Payment_Gateway --- UC1" not in d
    # 배치: supporting 액터 선언은 rectangle 닫힌 뒤에 온다(오른쪽).
    assert d.index("}") < d.index('actor "Payment Gateway"')
    assert d.index('actor "Customer"') < d.index("rectangle System")


def test_render_diagram_empty_use_cases():
    assert s4.render_diagram({"use_cases": []})["diagram"] == "@startuml\n@enduml"


def test_check_relationships_aggregates_report():
    state = {"relationships": {
        "associations": [1, 2, 3], "includes": [1], "extends": [], "generalizations": [1],
        "derived_use_cases": [1], "orphan_actors": ["Ghost"], "dropped_refs": ["a->b"],
        "relationship_issues": ["[rel] fix"],
    }}
    report = s4.check_relationships(state)["relationship_report"]
    assert report["counts"] == {"associations": 3, "includes": 1, "extends": 0,
                                "generalizations": 1, "derived_use_cases": 1}
    assert report["orphan_actors"] == ["Ghost"]
    assert report["dropped_refs"] == ["a->b"]
    assert report["relationship_issues"] == ["[rel] fix"]


def test_san_alias():
    assert s4._san("Registered User") == "Registered_User"
    assert s4._san("123abc").startswith("n_")  # 숫자 시작 방지
    assert s4._san("A/B-C") == "A_B_C"


# ---------------------------------------------------------------------------
# 2. identify_relationships — 목킹 + 주액터 association 보강
# ---------------------------------------------------------------------------
def test_identify_relationships_augments_missing_primary_association(monkeypatch):
    # LLM은 UC1 association만 반환(UC2 주액터 association 누락).
    result = RelationshipModel(
        associations=[Association(actor="Registered User", use_case="Log in")],
        includes=[IncludeRelation(base_use_case="Place order", included_use_case="Authenticate")],
        extends=[], generalizations=[],
        # 팩토링한 include의 included_use_case는 반드시 derived_use_cases로 선언해야 가드를 통과.
        derived_use_cases=[DerivedUseCase(name="Authenticate", origin="factored_include")],
    )
    monkeypatch.setattr(s4, "invoke_structured", lambda schema, messages: result)

    state = {
        "actors": [{"name": "Registered User", "kind": "primary", "description": "x"}],
        "use_cases": [
            {"id": "UC1", "name": "Log in", "primary_actor": "Registered User"},
            {"id": "UC2", "name": "Place order", "primary_actor": "Registered User"},
        ],
    }
    rel = s4.identify_relationships(state)["relationships"]
    pairs = {(a["actor"], a["use_case"]) for a in rel["associations"]}

    assert ("Registered User", "Log in") in pairs
    assert ("Registered User", "Place order") in pairs   # 결정론 보강됨
    assert rel["includes"][0]["included_use_case"] == "Authenticate"
    assert rel["orphan_actors"] == []                     # 모든 액터가 연결됨


def test_identify_relationships_flags_orphan_actor(monkeypatch):
    # LLM이 미연결 supporting 액터를 냈는데 어떤 association에도 안 걸림 → orphan 플래그.
    result = RelationshipModel(
        associations=[], includes=[], extends=[], generalizations=[], derived_use_cases=[]
    )
    monkeypatch.setattr(s4, "invoke_structured", lambda schema, messages: result)
    state = {
        "actors": [
            {"name": "User", "kind": "primary", "description": "x"},
            {"name": "Legacy Reporting System", "kind": "supporting", "description": "unused"},
        ],
        "use_cases": [{"id": "UC1", "name": "Log in", "primary_actor": "User"}],
    }
    rel = s4.identify_relationships(state)["relationships"]
    # User는 주액터 보강으로 연결, Legacy Reporting System은 고아.
    assert rel["orphan_actors"] == ["Legacy Reporting System"]


def test_identify_relationships_drops_unknown_references(monkeypatch):
    # LLM이 존재하지 않는 UC/액터를 참조 → 결정론 가드가 제거하고 dropped_refs에 기록.
    result = RelationshipModel(
        associations=[
            Association(actor="User", use_case="Log in"),          # 정상
            Association(actor="Ghost", use_case="Log in"),         # 없는 액터
            Association(actor="User", use_case="Nonexistent UC"),  # 없는 UC
        ],
        includes=[IncludeRelation(base_use_case="Log in", included_use_case="Phantom")],  # 없는 included
        extends=[], generalizations=[], derived_use_cases=[],
    )
    monkeypatch.setattr(s4, "invoke_structured", lambda schema, messages: result)
    state = {
        "actors": [{"name": "User", "kind": "primary", "description": "x"}],
        "use_cases": [{"id": "UC1", "name": "Log in", "primary_actor": "User"}],
    }
    rel = s4.identify_relationships(state)["relationships"]

    pairs = {(a["actor"], a["use_case"]) for a in rel["associations"]}
    assert ("User", "Log in") in pairs
    assert ("Ghost", "Log in") not in pairs        # 없는 액터 제거
    assert ("User", "Nonexistent UC") not in pairs  # 없는 UC 제거
    assert rel["includes"] == []                    # 없는 included 제거
    assert len(rel["dropped_refs"]) == 3


def test_mine_include_candidates():
    ucs = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
    specs = {
        "A": {"main_scenario": [{"step_number": 1, "sentence": "System validates the payment"}]},
        "B": {"main_scenario": [{"step_number": 1, "sentence": "System validates the payment"}]},
        "C": {"main_scenario": [{"step_number": 1, "sentence": "System ships the goods"}]},
    }
    cand = s4._mine_include_candidates(ucs, specs)
    assert cand["system validates the payment"] == ["A", "B"]   # ≥2 UC 공유
    assert "system ships the goods" not in cand                 # 1 UC뿐 → 후보 아님


def test_identify_relationships_augments_parent_actor_generalization(monkeypatch):
    # LLM이 일반화를 안 냈어도 parent_actor로부터 결정론 보강.
    monkeypatch.setattr(
        s4, "invoke_structured",
        lambda schema, messages: RelationshipModel(
            associations=[], includes=[], extends=[], generalizations=[], derived_use_cases=[]
        ),
    )
    state = {
        "actors": [
            {"name": "Guest", "kind": "primary", "description": "g", "parent_actor": None},
            {"name": "Member", "kind": "primary", "description": "m", "parent_actor": "Guest"},
        ],
        "use_cases": [
            {"id": "UC1", "name": "Browse", "primary_actor": "Guest"},
            {"id": "UC2", "name": "Order", "primary_actor": "Member"},
        ],
    }
    rel = s4.identify_relationships(state)["relationships"]
    gens = {(g["parent"], g["child"]) for g in rel["generalizations"]}
    assert ("Guest", "Member") in gens


def test_scenarios_and_hints_fed_to_relationship_agent(monkeypatch):
    captured = {}

    def fake(schema, messages):
        captured["human"] = messages[1].content
        return RelationshipModel(
            associations=[], includes=[], extends=[], generalizations=[], derived_use_cases=[]
        )

    monkeypatch.setattr(s4, "invoke_structured", fake)
    state = {
        "actors": [{"name": "User", "kind": "primary", "description": "d", "parent_actor": None}],
        "use_cases": [
            {"id": "UC1", "name": "Place order", "primary_actor": "User"},
            {"id": "UC2", "name": "Write review", "primary_actor": "User"},
        ],
        "use_case_specs": [
            {"name": "Place order", "main_scenario": [{"step_number": 1, "sentence": "System authenticates the user"}]},
            {"name": "Write review", "main_scenario": [{"step_number": 1, "sentence": "System authenticates the user"}]},
        ],
    }
    s4.identify_relationships(state)
    human = captured["human"]
    assert "main success scenarios" in human               # 시나리오 주입
    assert "System authenticates the user" in human
    assert "possible include" in human                     # 공유 스텝 힌트 주입


def test_include_hints_capped(monkeypatch):
    captured = {}

    def fake(schema, messages):
        captured["human"] = messages[1].content
        return RelationshipModel(
            associations=[], includes=[], extends=[], generalizations=[], derived_use_cases=[]
        )

    monkeypatch.setattr(s4, "invoke_structured", fake)
    # 두 UC가 10개 동일 스텝을 공유 → 후보 10개지만 힌트는 상한까지만 노출.
    steps = [{"step_number": j, "sentence": f"System performs action number {j}"} for j in range(1, 11)]
    state = {
        "actors": [{"name": "U", "kind": "primary", "description": "d", "parent_actor": None}],
        "use_cases": [{"id": "UC1", "name": "UC1", "primary_actor": "U"},
                      {"id": "UC2", "name": "UC2", "primary_actor": "U"}],
        "use_case_specs": [{"name": "UC1", "main_scenario": steps}, {"name": "UC2", "main_scenario": steps}],
    }
    s4.identify_relationships(state)
    assert captured["human"].count("possible include") <= s4._MAX_INCLUDE_HINTS


def test_relationship_reflection_removes_antipattern(monkeypatch):
    # 의미검증기가 인증-include(precondition 안티패턴)를 잡아 재생성으로 제거.
    monkeypatch.setattr(s4.settings, "enable_semantic_validator", True)
    monkeypatch.setattr(s4.settings, "max_repair_iters", 1)
    calls = {"gen": 0}

    def fake(schema, messages):
        if schema is RelationshipModel:
            calls["gen"] += 1
            if calls["gen"] == 1:  # 첫 생성: 인증 include
                return RelationshipModel(
                    associations=[], extends=[], generalizations=[],
                    includes=[IncludeRelation(base_use_case="Place order", included_use_case="Authenticate")],
                    derived_use_cases=[DerivedUseCase(name="Authenticate", origin="factored_include")],
                )
            return RelationshipModel(associations=[], includes=[], extends=[], generalizations=[], derived_use_cases=[])
        # RelationshipCritique: gen 1이면 invalid(지시), 재생성(gen 2) 후 valid.
        return RelationshipCritique(
            is_valid=calls["gen"] >= 2,
            findings=[] if calls["gen"] >= 2 else ["Remove the Authenticate include — it is a precondition (Log On)."],
        )

    monkeypatch.setattr(s4, "invoke_structured", fake)
    state = {
        "actors": [{"name": "User", "kind": "primary", "description": "d", "parent_actor": None}],
        "use_cases": [{"id": "UC1", "name": "Place order", "primary_actor": "User"}],
    }
    rel = s4.identify_relationships(state)["relationships"]

    assert rel["includes"] == []                # 안티패턴 include 제거됨
    assert rel["relationship_issues"] == []      # 재생성으로 해소


def test_identify_relationships_empty_when_no_use_cases(monkeypatch):
    monkeypatch.setattr(
        s4, "invoke_structured",
        lambda schema, messages: pytest.fail("UC 없으면 호출되면 안 됨"),
    )
    out = s4.identify_relationships({"use_cases": []})
    assert out["relationships"]["associations"] == []
    assert out["relationships"]["includes"] == []


# ---------------------------------------------------------------------------
# 3. 라이브 e2e — step2 → step3 → step4 (옵트인)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_TESTS") != "1",
    reason="라이브 NIM 테스트는 RUN_LIVE_TESTS=1 일 때만 실행",
)
@pytest.mark.parametrize("dataset_name", dataset_names())
def test_live_step4(dataset_name):
    selected = os.getenv("STEP2_DATASET")
    if selected and dataset_name not in [s.strip() for s in selected.split(",")]:
        pytest.skip(f"STEP2_DATASET={selected} 에 없는 데이터셋({dataset_name})")

    state = {"classified": load_dataset(dataset_name)["classified"]}
    state.update(s2.identify_actors(state))
    state.update(s2.identify_use_cases(state))
    state.update(s3.generate_specs(state))
    state.update(s4.identify_relationships(state))
    state.update(s4.render_diagram(state))

    print(f"\n########## dataset: {dataset_name} — RELATIONSHIPS ##########")
    import json
    print(json.dumps(state["relationships"], indent=2, ensure_ascii=False))
    print("########## PlantUML ##########")
    print(state["diagram"])

    d = state["diagram"]
    assert d.startswith("@startuml") and d.rstrip().endswith("@enduml")
    # 모든 유스케이스가 노드로 선언돼야 함
    for uc in state["use_cases"]:
        assert f'as {uc["id"]}' in d
