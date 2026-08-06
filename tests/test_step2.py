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
def test_identify_actors_shapes_dicts(monkeypatch):
    result = ActorResult(
        actors=[
            Actor(name="Registered User", description="shopper"),
            Actor(name="Address Service", description="external"),
        ]
    )
    monkeypatch.setattr(s2, "invoke_structured", lambda schema, messages: result)

    out = s2.identify_actors({"classified": SAMPLE_CLASSIFIED})
    actors = out["actors"]

    assert out["phase"] == "actors"
    assert [a["name"] for a in actors] == ["Registered User", "Address Service"]
    assert "kind" not in actors[0]
    assert "kind" not in actors[1]


def test_identify_actors_empty_when_no_fr(monkeypatch):
    called = False

    def _fake(schema, messages):
        nonlocal called
        called = True
        return ActorResult(actors=[])

    monkeypatch.setattr(s2, "invoke_structured", _fake)

    out = s2.identify_actors({"classified": [{"id": "N1", "text": "x", "type": "NFR"}]})
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
        {"classified": SAMPLE_CLASSIFIED, "actors": [], "use_cases": existing},
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


def test_coverage_repair_covers_orphans(monkeypatch):
    # 1차엔 R1만 커버(R2~R5 고아) → 재생성이 나머지를 보충.
    calls = {"n": 0}

    def fake(schema, messages):
        calls["n"] += 1
        return _uc_result(["R1"]) if calls["n"] == 1 else _uc_result(["R1"], ["R2", "R3", "R4", "R5"])

    monkeypatch.setattr(s2, "invoke_structured", fake)
    out = s2.identify_use_cases(
        {"classified": SAMPLE_CLASSIFIED, "actors": [{"name": "U", "kind": "primary", "description": "d"}]}
    )
    covered = {rid for uc in out["use_cases"] for rid in uc["requirement_ids"]}

    assert {"R1", "R2", "R3", "R4", "R5"} <= covered   # 고아 해소
    assert calls["n"] == 2                              # 1회 재생성으로 완료


def test_coverage_repair_stops_at_budget(monkeypatch):
    monkeypatch.setattr(s2.settings, "max_coverage_iters", 2)
    monkeypatch.setattr(s2, "invoke_structured", lambda schema, messages: _uc_result(["R1"]))

    out = s2.identify_use_cases({"classified": SAMPLE_CLASSIFIED, "actors": []})
    covered = {rid for uc in out["use_cases"] for rid in uc["requirement_ids"]}
    assert covered == {"R1"}   # 예산 소진, 나머지는 이후 check_coverage가 고아로 표면화


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
