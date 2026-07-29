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
