"""지목 수정 — **고친 것만 바뀌는가** (LLM·DB 불필요).

이 파일의 존재 이유는 한 문장이다: **LLM 이 지시를 어겨도 비대상은 안전한가.**

되감기는 스테이지를 통째로 다시 만들어서 사용자가 승인한 내용을 날린다. 지목 수정은
그걸 피하려는 것인데, 리바이저는 여전히 모델 **전체**를 돌려주므로 LLM 이 마음대로
다른 항목을 고칠 수 있다. 그래서 프롬프트로 부탁하지 않고 `merge_model` 이 비대상
항목에 대해 LLM 출력을 **아예 읽지 않는다.**

아래 스텁들은 일부러 **못된 LLM**이다 — 시키지 않은 것까지 고쳐서 돌려준다.
그래도 결과가 안전해야 한다.
"""
from __future__ import annotations

import pytest

import app.design.graphs.subgraphs as sg
from app.design.cascade import UnknownTarget, revise_and_cascade
from app.design.knowledge import rules

STATE = {
    "usecase_spec": {"use_cases": [{"id": "UC1"}]},
    "extracted_bce_classes": {
        "Classes": [
            {"className": "Order", "stereotype": "Entity",
             "fields": ["total: int"], "methods": [], "use_case_ids": ["UC1"]},
            {"className": "Member", "stereotype": "Entity",
             "fields": ["name: String"], "methods": [], "use_case_ids": ["UC1"]},
            {"className": "OrderForm", "stereotype": "Boundary",
             "fields": [], "methods": [], "use_case_ids": ["UC1"]},
        ],
        "Relationships": [],
    },
    "sequence_diagram_model": {
        "Participants": [{"name": "OrderForm", "kind": "boundary",
                          "source_class": "OrderForm"}],
        "Messages": [],
    },
    "api_spec_model": {
        "title": "API", "version": "1.0.0",
        "Endpoints": [
            {"path": "/orders", "method": "post", "operation_id": "createOrder",
             "responses": [], "source_classes": ["OrderForm"], "use_case_ids": ["UC1"]},
        ],
        "Schemas": [
            {"name": "Order", "fields": [{"name": "total", "type": "integer"}],
             "source_class": "Order"},
            {"name": "Member", "fields": [{"name": "name", "type": "string"}],
             "source_class": "Member"},
        ],
    },
    "erd_bce_classes": {
        "Classes": [
            {"className": "Order", "stereotype": "Entity", "fields": ["total: int"]},
            {"className": "Member", "stereotype": "Entity", "fields": ["name: String"]},
        ],
    },
    "deployment_diagram_model": {
        "Nodes": [{"name": "AppServer", "kind": "node",
                   "source_classes": ["Order", "Member", "OrderForm"]}],
        "Artifacts": [{"name": "order.jar", "source_classes": ["Order"]},
                      {"name": "member.jar", "source_classes": ["Member"]}],
        "Connections": [],
    },
}


@pytest.fixture
def naughty_llm(monkeypatch):
    """**시키지 않은 것까지 고치는** 리바이저. 병합이 막아야 한다.

    호출 기록을 남겨 "무관한 스테이지는 부르지도 않는가"를 볼 수 있게 한다.
    """
    calls: list[str] = []

    def wreck_classes(current_bce, feedback, scenario_text="", targets=None):
        calls.append("class_diagram")
        return {"Classes": [
            # 대상 — 시킨 대로 필드 추가
            {"className": "Order", "stereotype": "Entity",
             "fields": ["total: int", "orderedAt: datetime"], "methods": [],
             "use_case_ids": ["UC1"]},
            # 비대상인데 멋대로 이름과 필드를 바꿔버렸다
            {"className": "MemberRenamed", "stereotype": "Entity",
             "fields": [], "methods": [], "use_case_ids": []},
            # 비대상인데 아예 빼먹었다 (OrderForm)
        ], "Relationships": [{"source": "X", "target": "Y", "type": "Association"}]}

    def wreck_api(current_model, feedback, context_text="", targets=None):
        calls.append("api_spec")
        return {"title": "완전히 다른 API", "version": "9.9.9", "Endpoints": [
            # 비대상 엔드포인트를 갈아엎었다
            {"path": "/wrecked", "method": "get", "operation_id": "createOrder",
             "responses": [], "source_classes": [], "use_case_ids": []},
        ], "Schemas": [
            # 대상 — 시킨 대로
            {"name": "Order", "fields": [{"name": "total", "type": "integer"},
                                         {"name": "orderedAt", "type": "string"}],
             "source_class": "Order"},
            # 비대상인데 지워버렸다 (Member)
        ]}

    def wreck_deployment(current_model, feedback, context_text="", targets=None):
        calls.append("deployment_diagram")
        return {"Nodes": [], "Artifacts": [      # 노드를 통째로 날렸다
            {"name": "order.jar", "source_classes": ["Order"], "description": "고쳐짐"},
            # member.jar 을 지워버렸다
        ], "Connections": []}

    def wreck_sequence(current_model, feedback, context_text="", targets=None):
        calls.append("sequence_diagram")
        return {"Participants": [], "Messages": []}

    monkeypatch.setattr(sg, "revise_bce_classes", wreck_classes)
    monkeypatch.setattr(sg, "revise_api_spec_model", wreck_api)
    monkeypatch.setattr(sg, "revise_deployment_model", wreck_deployment)
    monkeypatch.setattr(sg, "revise_sequence_model", wreck_sequence)

    import app.design.services.common.validation as validation
    monkeypatch.setattr(validation, "check_plantuml_syntax", lambda _t: [])
    return calls


def _classes(state):
    return {c["className"]: c for c in state["extracted_bce_classes"]["Classes"]}


def test_the_target_is_actually_changed(naughty_llm):
    """시킨 것은 반영돼야 한다 — 안 그러면 병합이 과보호한 것이다."""
    out = revise_and_cascade(STATE, "class_diagram:Order", "주문일시 필드 추가")

    assert "orderedAt: datetime" in _classes(out["state"])["Order"]["fields"]


def test_untargeted_siblings_survive_a_misbehaving_model(naughty_llm):
    """**이 파일의 핵심.** 대상이 아닌 것은 글자 하나 안 바뀐다.

    스텁은 Member 를 개명하고 OrderForm 을 빼먹었다. 둘 다 무시돼야 한다.
    """
    out = revise_and_cascade(STATE, "class_diagram:Order", "주문일시 필드 추가")
    classes = _classes(out["state"])

    assert classes["Member"] == _classes(STATE)["Member"]        # 원본 그대로
    assert classes["OrderForm"] == _classes(STATE)["OrderForm"]  # 안 지워짐
    # 개명은 (삭제+추가)처럼 보인다 — 추가를 안 받으므로 개명본이 새로 끼지 않는다.
    assert "MemberRenamed" not in classes
    # 목록 길이·순서까지 그대로다. 바뀐 것은 대상의 내용뿐이다.
    assert [c["className"] for c in out["state"]["extracted_bce_classes"]["Classes"]] == [
        c["className"] for c in STATE["extracted_bce_classes"]["Classes"]
    ]


def test_untargeted_downstream_elements_survive_too(naughty_llm):
    """하류에서도 같다 — 스텁이 갈아엎어도 대상만 반영된다."""
    out = revise_and_cascade(STATE, "class_diagram:Order", "주문일시 필드 추가")
    api = out["state"]["api_spec_model"]
    schemas = {s["name"]: s for s in api["Schemas"]}
    endpoints = {e["operation_id"]: e for e in api["Endpoints"]}

    # 대상(Order 스키마)은 고쳐졌다.
    assert any(f["name"] == "orderedAt" for f in schemas["Order"]["fields"])
    # 비대상은 살아남았다 — 스텁은 Member 를 지우고 createOrder 를 갈아엎었다.
    assert schemas["Member"] == {
        "name": "Member", "fields": [{"name": "name", "type": "string"}],
        "source_class": "Member",
    }
    assert endpoints["createOrder"]["path"] == "/orders"

    deployment = out["state"]["deployment_diagram_model"]
    names = {a["name"] for a in deployment["Artifacts"]}
    assert names == {"order.jar", "member.jar"}       # 스텁은 member.jar 을 지웠다
    # AppServer 는 대상인데 스텁이 통째로 빠뜨렸다 — 빠뜨림은 삭제가 아니므로 살아남는다.
    assert [n["name"] for n in deployment["Nodes"]] == ["AppServer"]


def test_fields_the_spec_does_not_know_about_are_left_alone(naughty_llm):
    """목록 밖의 필드도 지켜야 한다 — 스텁은 title/version 을 갈아엎었다.

    실제로 새어나갔던 자리다: 병합의 바탕이 revised 였을 때, 스펙이 모르는 필드는
    그대로 통과했다. 바탕을 original 로 바꿔서 막았다.
    """
    out = revise_and_cascade(STATE, "class_diagram:Order", "주문일시 필드 추가")
    api = out["state"]["api_spec_model"]

    assert api["title"] == "API"          # 스텁은 "완전히 다른 API" 를 돌려줬다
    assert api["version"] == "1.0.0"      # 스텁은 "9.9.9"


def test_unaffected_stages_are_never_asked(naughty_llm):
    """영향 없는 스테이지는 리바이저를 **부르지도 않는다.**

    LLM 호출이 곧 변경 위험이다. 안 부르는 것이 가장 확실한 보존이다.
    OrderForm 은 시퀀스 참가자로만 쓰이므로 API·배포는 손댈 이유가 없다.
    """
    revise_and_cascade(STATE, "class_diagram:OrderForm", "이름을 주문화면으로")

    assert "sequence_diagram" in naughty_llm      # 참가자가 걸린다
    assert "api_spec" in naughty_llm              # createOrder 가 OrderForm 을 참조
    assert "deployment_diagram" in naughty_llm    # AppServer 가 OrderForm 을 호스팅

    naughty_llm.clear()
    # Member 는 API 스키마·ERD·배포 아티팩트에만 걸린다 — 시퀀스는 무관하다.
    revise_and_cascade(STATE, "class_diagram:Member", "이메일 추가")
    assert "sequence_diagram" not in naughty_llm


def test_erd_is_reprojected_without_the_llm(naughty_llm):
    """ERD 는 클래스 BCE 의 투영이다 — 물어볼 것이 없다."""
    out = revise_and_cascade(STATE, "class_diagram:Order", "주문일시 필드 추가")

    assert "erd" in out["changed"]
    assert "erd" not in naughty_llm                     # 리바이저를 안 불렀다
    assert "orderedAt" in out["state"]["erd_puml"]      # 그래도 반영됐다


def test_the_erd_model_is_a_copy_not_the_class_diagram_itself(naughty_llm):
    """ERD 모델이 클래스 BCE와 **다른 객체**인가.

    한동안 같은 객체였다. 리바이저가 새 dict를 돌려주고 사상도 원본을 안 건드려서 사고는
    안 났지만, `_seed_erd_model`의 docstring은 격리를 약속하고 있었다. 약속만 있고 격리가
    없으면 다음 사람이 그것을 믿고 제자리 편집을 넣고, 그 순간 ERD 수정이 클래스
    다이어그램을 조용히 오염시킨다.

    같은 객체가 아니라는 것만 고정한다 — 값이 같은 것은 정상이다(투영이니까).
    """
    out = revise_and_cascade(STATE, "class_diagram:Order", "주문일시 필드 추가")
    state = out["state"]

    assert state["erd_bce_classes"] is not state["extracted_bce_classes"]
    for erd_class, class_class in zip(
        state["erd_bce_classes"]["Classes"], state["extracted_bce_classes"]["Classes"]
    ):
        assert erd_class is not class_class


def test_only_touched_stages_are_reported(naughty_llm):
    """무엇을 고쳤는지 화면에 정직하게 말해야 한다."""
    out = revise_and_cascade(STATE, "class_diagram:Order", "주문일시 필드 추가")

    assert out["changed"][0] == "class_diagram"
    assert out["touched"]["class_diagram"] == ["Order"]
    assert set(out["touched"]["api_spec"]) == {"Order"}   # createOrder 는 안 걸린다


def test_the_rendered_artifact_follows_the_merged_model(naughty_llm):
    """모델을 병합했으면 그림도 병합된 모델에서 나와야 한다."""
    out = revise_and_cascade(STATE, "class_diagram:Order", "주문일시 필드 추가")
    puml = out["state"]["class_diagram_puml"]

    assert "orderedAt" in puml            # 대상 반영
    assert "class Member" in puml         # 비대상 보존
    assert "MemberRenamed" not in puml    # 멋대로 바꾼 것은 안 들어옴


def test_an_unknown_target_is_refused(naughty_llm):
    """지금 산출물에 없는 것을 지목하면 조용히 넘어가지 않는다."""
    with pytest.raises(UnknownTarget):
        revise_and_cascade(STATE, "class_diagram:없는클래스", "x")
    with pytest.raises(UnknownTarget):
        revise_and_cascade(STATE, "erd:Order", "x")       # ERD 는 투영이라 대상이 아니다


# ---------------------------------------------------------------------------
# 규칙 검사가 이 경로에서도 따라오는가
# ---------------------------------------------------------------------------
#: 직전 그래프 실행이 남긴 "깨끗하다" 판정. 지목 수정이 모델을 고친 뒤에도 이것이 그대로
#: 남아 있으면, 화면은 **아무도 검사하지 않은 새 모델**에 대해 계속 통과를 보여준다.
STALE_CLEAN = {"findings": [], "repair_iters": 0, "stopped": "clean"}


def test_a_stale_clean_verdict_does_not_survive_a_revision(monkeypatch, naughty_llm):
    """고친 모델을 다시 검사한다 — 낡은 판정을 새 산출물의 보증으로 쓰지 않는다.

    이게 없으면 이 기능 전체가 막으려던 실패가 지목 수정 경로로 되돌아온다:
    "위반 없음"과 "검사하지 않았음"이 화면에서 같은 모양이 된다.
    """
    def breaks_the_target(current_bce, feedback, scenario_text="", targets=None):
        # 대상(Order)의 스테레오타입을 BCE 밖으로 바꾼다. 대상이므로 병합을 통과한다.
        return {"Classes": [
            {"className": "Order", "stereotype": "Repository",
             "fields": ["total: int"], "methods": [], "use_case_ids": ["UC1"]},
        ], "Relationships": []}

    monkeypatch.setattr(sg, "revise_bce_classes", breaks_the_target)

    out = revise_and_cascade(
        {**STATE, "class_diagram_check": STALE_CLEAN},
        "class_diagram:Order",
        "Order 를 리포지토리로 바꿔줘",
    )
    check = out["state"]["class_diagram_check"]

    assert check["stopped"] == "checked_only"
    assert any("class.stereotype-is-bce" in issue for issue in check["findings"])


def test_a_revision_that_stays_within_the_rules_reports_clean(naughty_llm):
    """위반이 없으면 `clean` 이다 — 경로가 달라도 그 뜻은 같아야 한다."""
    out = revise_and_cascade(
        {**STATE, "class_diagram_check": {"findings": ["낡은 지적"],
                                          "repair_iters": 1, "stopped": "budget"}},
        "class_diagram:Order",
        "주문일시 필드 추가",
    )
    check = out["state"]["class_diagram_check"]

    assert check["stopped"] == "clean"
    assert check["findings"] == []      # 낡은 지적도 함께 사라진다


def test_the_cascade_never_runs_the_repair_loop(monkeypatch, naughty_llm):
    """검사는 하되 재생성은 하지 않는다.

    재생성은 `targets=set()`(전체 수정)으로 리바이저를 부른다. 지목 수정 경로에서 그걸
    돌리면 **"지목한 항목만 바뀐다"는 이 경로의 보장을 스스로 깬다** — 사용자가 Order 만
    고쳐달라고 했는데 다른 클래스가 조용히 바뀐다.

    그래서 위반이 남아도 리바이저 호출은 지목 수정 몫 한 번뿐이어야 한다.
    """
    seen_targets: list[set] = []

    def breaks_the_target(current_bce, feedback, scenario_text="", targets=None):
        seen_targets.append(targets)
        return {"Classes": [
            {"className": "Order", "stereotype": "Repository",
             "fields": ["total: int"], "methods": [], "use_case_ids": ["UC1"]},
        ], "Relationships": []}

    monkeypatch.setattr(sg, "revise_bce_classes", breaks_the_target)

    out = revise_and_cascade(STATE, "class_diagram:Order", "Order 를 리포지토리로")

    assert out["state"]["class_diagram_check"]["findings"], "위반이 남아 있어야 하는 상황"
    assert out["state"]["class_diagram_check"]["repair_iters"] == 0
    # 한 번만, 그리고 언제나 지목된 대상으로만 불렀다.
    assert seen_targets == [{"Order"}]


def test_only_stages_without_rules_get_no_check_verdict(naughty_llm):
    """규칙이 없는 스테이지에만 판정을 쓰지 않는다.

    빈 결과를 써 두면 "검사했고 깨끗하다"로 읽힌다. 검사할 규칙이 아직 없다는 사실은
    **값이 없는 것**으로 드러나야 한다.
    """
    out = revise_and_cascade(STATE, "class_diagram:Order", "주문일시 필드 추가")

    # **기대값을 규칙 목록에서 뽑는다.** 스테이지 이름을 손으로 적어 두면, 규칙을
    # 추가하고 배선을 잊었을 때 이 테스트가 그 사실을 못 잡는다.
    #
    # **다시 그려진 스테이지만 본다.** 안 그려진 스테이지에 판정이 없는 것은 규칙이
    # 없어서가 아니라 아예 안 돌아서다 — 그건 이 테스트가 묻는 것이 아니다
    # (`test_unaffected_stages_are_never_asked`가 맡는다).
    with_rules = {r.stage for r in rules.RULES if r.severity == rules.DEFECT}
    for stage in out["changed"]:
        assert (f"{stage}_check" in out["state"]) is (stage in with_rules), stage

    # 규칙이 없는 스테이지는 **다시 그려졌는데도** 판정이 없다 — 그것이 요점이다.
    assert "deployment_diagram" in out["changed"]
    assert "deployment_diagram_check" not in out["state"]


def test_the_reprojected_erd_is_checked_too(naughty_llm):
    """ERD 는 물어보지 않고 다시 그리지만, 다시 그린 것도 **검사한다.**

    클래스 다이어그램이 통과했다는 것이 ERD 의 보증이 아니다 — 두 스테이지가 보는 규칙이
    다르다(다중도 없는 관계·이름으로 가리킨 참조는 ERD 쪽에서만 결함이다). 검사를 빼면
    클래스 수정이 ERD 를 망가뜨려도 화면은 아무 말을 안 한다.

    재생성은 하지 않는다(`checked_only`) — 이 경로의 보장은 "지목한 것만 바뀐다"이고,
    ERD 는 애초에 물어보지 않고 다시 그리는 자리다.
    """
    out = revise_and_cascade(STATE, "class_diagram:Order", "주문일시 필드 추가")

    assert "erd" in out["changed"]
    verdict = out["state"]["erd_check"]
    assert verdict["stopped"] in {"clean", "checked_only"}
    assert verdict["repair_iters"] == 0
