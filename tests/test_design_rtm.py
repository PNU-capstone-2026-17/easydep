"""설계 추적 매트릭스 — 집계가 정직한가 (LLM·DB 불필요).

`build_design_rtm`은 순수 함수다. state를 넣으면 추적표가 나온다. 그래서 테스트가
실제 동작을 그대로 검사할 수 있다 — 목킹할 것이 없다.

가장 중요한 것은 **숨기지 않는가**다. 추적이 빠졌는데 "일관성 보장"을 믿게 만들면
지금보다 나쁘다. orphan과 unknown_ref가 반드시 표면에 나와야 한다.
"""
from __future__ import annotations

from app.design.rtm import (
    affected_by_element,
    build_design_rtm,
    impacted_by,
    impacted_stages,
    render_design_rtm_md,
    transitively_impacted,
)

STATE = {
    "usecase_spec": {"use_cases": [{"id": "UC1", "name": "주문하기"}, {"id": "UC2"}]},
    "extracted_bce_classes": {
        "Classes": [
            {"className": "OrderForm", "stereotype": "Boundary", "use_case_ids": ["UC1"]},
            {"className": "OrderController", "stereotype": "Control", "use_case_ids": ["UC1"]},
            {"className": "Order", "stereotype": "Entity", "use_case_ids": ["UC1", "UC2"]},
            # 출처를 안 밝힌 클래스 — orphan 으로 잡혀야 한다.
            {"className": "Mystery", "stereotype": "Control", "use_case_ids": []},
        ],
        "Relationships": [],
    },
    "sequence_diagram_model": {
        "Participants": [
            {"name": "Customer", "kind": "actor", "source_class": ""},
            {"name": "OrderForm", "kind": "boundary", "source_class": "OrderForm"},
        ],
        "Messages": [
            {"source": "Customer", "target": "OrderForm", "label": "주문()",
             "use_case_ids": ["UC1"]},
        ],
    },
    "api_spec_model": {
        "Endpoints": [
            {"path": "/orders", "method": "post", "operation_id": "createOrder",
             "source_classes": ["OrderController"], "use_case_ids": ["UC1"]},
        ],
        "Schemas": [{"name": "Order", "source_class": "Order"}],
    },
    "erd_bce_classes": {
        "Classes": [
            {"className": "Order", "stereotype": "Entity"},
            {"className": "OrderForm", "stereotype": "Boundary"},   # 테이블 아님
        ]
    },
    "deployment_diagram_model": {
        "Nodes": [
            {"name": "AppServer", "kind": "node", "source_classes": ["OrderController"]},
            {"name": "Browser", "kind": "device", "source_classes": []},   # orphan (정상)
        ],
        "Artifacts": [{"name": "order.jar", "source_classes": ["Order"]}],
        "Connections": [],
    },
}


def test_every_stage_is_covered():
    """다섯 산출물 전부가 추적표에 나온다 — 빠진 스테이지가 있으면 영향 분석이 샌다."""
    rtm = build_design_rtm(STATE)
    stages = {row["stage"] for row in rtm["rows"]}
    assert stages == {
        "class_diagram", "sequence_diagram", "api_spec", "erd", "deployment_diagram"
    }


def test_messages_are_traced_to_classes_through_their_participants():
    """`A -> B : call()` 은 A·B 클래스에 의존한다 — 모델에는 안 적혀 있다.

    참가자가 이미 source_class 를 들고 있으므로 코드로 잇는다. LLM 에게 한 번 더
    물어보면 참가자와 어긋날 기회만 생긴다.
    """
    rtm = build_design_rtm(STATE)
    message = next(r for r in rtm["rows"] if "->" in r["element"])

    assert message["sources"]["class"] == ["OrderForm"]   # 액터 Customer 는 클래스가 없다
    assert message["sources"]["use_case"] == ["UC1"]

    # 그래서 클래스를 고치면 그 클래스가 오가는 메시지도 걸린다.
    assert any("->" in t for t in impacted_by(rtm, "class", "OrderForm"))


def test_sequence_collection_is_traced_per_use_case_diagram():
    collection_state = {
        **STATE,
        "sequence_diagram_model": {
            "Diagrams": [
                {
                    "use_case_id": "UC1",
                    "use_case_name": "Create order",
                    "Participants": [
                        {
                            "name": "OrderForm",
                            "alias": "Boundary",
                            "kind": "boundary",
                            "source_class": "OrderForm",
                        }
                    ],
                    "Messages": [],
                },
                {
                    "use_case_id": "UC2",
                    "use_case_name": "Cancel order",
                    "Participants": [
                        {
                            "name": "Order",
                            "alias": "Order",
                            "kind": "entity",
                            "source_class": "Order",
                        }
                    ],
                    "Messages": [],
                },
            ]
        },
    }

    rtm = build_design_rtm(collection_state)
    sequence_rows = [
        row for row in rtm["rows"] if row["stage"] == "sequence_diagram"
    ]

    assert [row["element"] for row in sequence_rows] == ["UC1", "UC2"]
    assert sequence_rows[0]["sources"] == {
        "use_case": ["UC1"],
        "class": ["OrderForm"],
    }
    assert "sequence_diagram:UC1" in impacted_by(rtm, "class", "OrderForm")


def test_a_bad_participant_class_is_reported_once_not_twice():
    """참가자의 source_class 가 틀리면 그 참가자 행에서만 보고한다.

    메시지까지 같이 세면 오류 하나가 여러 건으로 부풀어 실제 규모를 못 읽는다.
    """
    broken = {
        **STATE,
        "sequence_diagram_model": {
            "Participants": [{"name": "Ghost", "kind": "control",
                              "source_class": "NoSuchClass"}],
            "Messages": [{"source": "Ghost", "target": "Ghost", "label": "x()",
                          "use_case_ids": []}],
        },
    }
    rtm = build_design_rtm(broken)

    bad = [u for u in rtm["unknown_refs"] if u["ref"] == "NoSuchClass"]
    assert len(bad) == 1
    assert bad[0]["element"] == "Ghost"        # 참가자 행에서만

    # 메시지는 없는 클래스를 조용히 물고 가지 않는다.
    message = next(r for r in rtm["rows"] if "->" in r["element"])
    assert "class" not in message["sources"]


def test_erd_is_traced_without_the_llm_saying_so():
    """ERD 모델에는 추적 필드가 없다 — Entity 투영이라 코드가 계산해야 한다."""
    rtm = build_design_rtm(STATE)
    erd = [r for r in rtm["rows"] if r["stage"] == "erd"]

    assert [r["element"] for r in erd] == ["Order"]      # Boundary 는 테이블이 아니다
    assert erd[0]["sources"] == {"class": ["Order"]}
    assert erd[0]["status"] == "traced"


def test_untraced_elements_are_surfaced_not_hidden():
    """출처를 안 밝힌 항목은 orphan 으로 드러나야 한다.

    조용히 넘어가면 "추적표가 있으니 일관성이 보장된다"는 잘못된 믿음이 생긴다.
    """
    rtm = build_design_rtm(STATE)
    orphans = {r["element"] for r in rtm["rows"] if r["status"] == "orphan"}

    assert "Mystery" in orphans      # 유스케이스를 안 밝힌 클래스
    assert "Customer" in orphans     # 액터라서 클래스가 없다 — 정상이지만 드러난다
    assert "Browser" in orphans      # 설계에 없는 인프라 — 정상이지만 드러난다
    assert rtm["summary"]["orphan"] == len(orphans)


def test_references_to_things_that_do_not_exist_are_reported():
    """환각이나 이름 변경의 흔적을 잡아낸다."""
    broken = {
        **STATE,
        "api_spec_model": {
            "Endpoints": [
                {"operation_id": "ghostOp", "path": "/x", "method": "get",
                 "source_classes": ["NoSuchClass"], "use_case_ids": ["UC99"]},
            ],
            "Schemas": [],
        },
    }
    rtm = build_design_rtm(broken)

    refs = {(u["kind"], u["ref"]) for u in rtm["unknown_refs"]}
    assert ("class", "NoSuchClass") in refs
    assert ("use_case", "UC99") in refs
    assert rtm["summary"]["unknown_ref_count"] == 2

    # 참조가 틀렸어도 "출처를 밝히긴 했다"이므로 orphan 은 아니다. 둘은 다른 문제다.
    ghost = next(r for r in rtm["rows"] if r["element"] == "ghostOp")
    assert ghost["status"] == "traced"


def test_impact_answers_what_breaks_when_something_changes():
    """역방향 색인이 캐스케이드의 출발점이다."""
    rtm = build_design_rtm(STATE)

    # Order 클래스를 고치면 API 스키마·ERD 테이블·배포 아티팩트가 걸린다.
    assert impacted_by(rtm, "class", "Order") == [
        "api_spec:Order", "deployment_diagram:order.jar", "erd:Order",
    ]
    # UC1 을 고치면 클래스 3개와 시퀀스 메시지, 엔드포인트가 걸린다.
    assert "class_diagram:OrderController" in impacted_by(rtm, "use_case", "UC1")
    assert "api_spec:createOrder" in impacted_by(rtm, "use_case", "UC1")

    # 아무도 안 쓰는 것을 고치면 영향이 없다.
    assert impacted_by(rtm, "class", "Mystery") == []


def test_impacted_stages_come_back_in_pipeline_order():
    """되감기 대상을 고르려면 순서가 맞아야 한다 — 가장 앞선 것부터 되감아야 한다."""
    rtm = build_design_rtm(STATE)
    assert impacted_stages(rtm, "class", "Order") == [
        "api_spec", "erd", "deployment_diagram"
    ]


def test_indirect_impact_is_followed_to_the_end():
    """유스케이스를 고치면 클래스가 바뀌고, **그래서** API·ERD·배포도 바뀐다.

    한 단계만 보면 두 번째 화살표를 놓친다. impact 의 키("class:Order")와 행
    이름("class_diagram:Order")이 같은 것을 가리키므로, 거기서 이어 따라가야 한다.
    """
    rtm = build_design_rtm(STATE)

    direct = set(impacted_by(rtm, "use_case", "UC1"))
    everything = set(transitively_impacted(rtm, "use_case", "UC1"))

    assert direct < everything      # 전이가 진부분집합이어야 한다

    # 클래스를 거쳐야만 닿는 것들 — 직접 경로에는 없다.
    for indirect in ("api_spec:Order", "erd:Order", "deployment_diagram:order.jar"):
        assert indirect not in direct
        assert indirect in everything


def test_transitive_walk_terminates_on_a_cycle():
    """참조가 순환해도 멈춰야 한다 — 방문 표시가 그 일을 한다."""
    looped = {
        "usecase_spec": {"use_cases": [{"id": "UC1"}]},
        "extracted_bce_classes": {
            "Classes": [
                # 서로를 가리키는 두 클래스. class_diagram 행은 참조 대상이기도 하므로
                # 별칭을 타고 A → B → A 로 돌 수 있다.
                {"className": "A", "stereotype": "Entity", "use_case_ids": ["UC1"]},
                {"className": "B", "stereotype": "Entity", "use_case_ids": ["UC1"]},
            ],
        },
        "deployment_diagram_model": {
            "Nodes": [
                {"name": "A", "source_classes": ["B"]},
                {"name": "B", "source_classes": ["A"]},
            ],
        },
    }
    rtm = build_design_rtm(looped)
    assert transitively_impacted(rtm, "class", "A") == [
        "deployment_diagram:B"
    ]  # 끝난다는 것이 요점 (무한 루프면 이 줄에 도달 못 한다)


def test_affected_by_element_answers_what_follows_a_change():
    """산출물 항목을 고치면 무엇이 따라 바뀌는가 — 지목 수정이 여기서 출발한다."""
    rtm = build_design_rtm(STATE)

    assert affected_by_element(rtm, "class_diagram", "Order") == [
        "api_spec:Order", "deployment_diagram:order.jar", "erd:Order",
    ]
    # 아무도 참조하지 않는 항목을 고쳐도 따라 바뀔 것이 없다 — 그게 정답이다.
    assert affected_by_element(rtm, "api_spec", "createOrder") == []
    assert affected_by_element(rtm, "class_diagram", "Mystery") == []


def test_change_plan_lists_design_elements_not_upstream_refs():
    """사용자는 **보고 있는 산출물의 항목**을 지목한다 — 상류 참조가 아니라."""
    rtm = build_design_rtm(STATE)
    refs = {item["ref"] for item in rtm["change_plan"]}

    assert "class_diagram:Order" in refs        # 항목 이름
    assert "class:Order" not in refs            # 상류 참조 이름이 아니라
    assert "api_spec:createOrder" in refs       # 하류 항목도 고를 수 있다


def test_change_plan_excludes_what_design_cannot_edit():
    """유스케이스는 요구사항 산출물이다. 목록에 두면 "고칠 수 있다"는 거짓말이 된다.

    ERD 도 뺀다 — 클래스 BCE 의 결정론적 투영이라 직접 고칠 것이 없다.
    """
    rtm = build_design_rtm(STATE)
    stages = {item["stage"] for item in rtm["change_plan"]}

    assert "use_case" not in stages
    assert "erd" not in stages
    assert stages <= {"class_diagram", "sequence_diagram", "api_spec", "deployment_diagram"}


def test_change_plan_agrees_with_the_function_it_summarises():
    """미리 계산해 둔 값이 그때그때 부른 결과와 같아야 한다.

    화면은 이 값만 보고 판단하므로, 여기가 어긋나면 사용자가 보는 것이 거짓이 된다.
    """
    rtm = build_design_rtm(STATE)
    for item in rtm["change_plan"]:
        assert item["affects"] == affected_by_element(rtm, item["stage"], item["element"])
        assert item["affected_stages"] == [
            s for s in ("class_diagram", "sequence_diagram", "api_spec", "erd",
                        "deployment_diagram")
            if s in {a.partition(":")[0] for a in item["affects"]}
        ]


def test_empty_state_does_not_blow_up():
    """아직 아무것도 안 만든 앱도 조회된다 — 화면이 언제든 부를 수 있어야 한다."""
    rtm = build_design_rtm({})
    assert rtm["rows"] == []
    assert rtm["impact"] == {}
    assert rtm["change_plan"] == []
    assert rtm["summary"]["trace_ratio"] == 1.0


def test_markdown_render_includes_the_warnings():
    """사람이 보는 표에도 orphan 과 환각 참조가 남아야 한다."""
    rtm = build_design_rtm(STATE)
    md = render_design_rtm_md(rtm, title="test")

    assert "설계 추적 매트릭스" in md
    assert "⚠ orphan" in md
    assert "영향 분석" in md
