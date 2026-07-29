"""사람 검수 계층 (`graphkb/parsers/review.py`).

목표는 **완벽한 데이터셋**이지 완벽한 파서가 아니다. 소스에 핀을 박아 입력이 얼어
있으므로, 오탐 몇십 건을 정규식으로 잡으려 애쓰는 대신 눈으로 보고 지운다.

한때 "흔한 단어 30개는 이름 추론에서 제외"라는 규칙을 넣었었는데 되돌렸다.
그 규칙은 (a) 단어를 감으로 골랐고 (b) `TypeId`가 **진짜로** 그 부품을 가리키는
경우까지 같이 막았다. 실제로 틀린 것은 타입쌍 몇십 개뿐이라 직접 지목하는 편이 정확하다.
"""

from __future__ import annotations

import json

import pytest

from app.core.cloudkb.graphkb.model import Edge, Graph, Node
from app.core.cloudkb.graphkb.parsers.review import apply_review, check_freshness, load_review


def _node(id_: str) -> Node:
    return Node(id=id_, layer="vendor", provider="aws",
                display_name=id_.split("::", 1)[1], source="test")


def _edge(frm: str, to: str, via: str, **kw) -> Edge:
    return Edge(from_id=frm, to_id=to, type="references", via_property=via,
                required=kw.get("required", False), cardinality="one",
                evidence=kw.get("evidence", "heuristic"),
                reviewed=kw.get("reviewed", False))


@pytest.fixture()
def graph() -> Graph:
    g = Graph()
    for n in ("aws::A", "aws::B", "aws::C"):
        g.add_node(_node(n))
    g.add_edge(_edge("aws::A", "aws::B", "OnePath"))
    g.add_edge(_edge("aws::A", "aws::B", "AnotherPath"))
    g.add_edge(_edge("aws::A", "aws::C", "ThirdPath"))
    return g


def _write(tmp_path, payload: dict):
    path = tmp_path / "aws-edges.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_rejecting_a_pair_removes_every_path(graph, tmp_path) -> None:
    """`via_property` 없이 타입쌍만 적으면 그 쌍의 **모든 경로**가 사라진다.

    QuickSight의 잘못된 연결이 421개 경로에 퍼져 있어서 이 형태가 필요했다 —
    경로를 421줄 적을 수는 없다.
    """
    path = _write(tmp_path, {"rejected": [{"from": "aws::A", "to": "aws::B",
                                           "reason": "차트 내부 번호이지 부품이 아님"}]})
    stats = apply_review(graph, "aws", path=path)

    assert stats["dropped"] == 2
    assert [(e.to_id, e.via_property) for e in graph.edges] == [("aws::C", "ThirdPath")]


def test_rejecting_one_path_keeps_the_others(graph, tmp_path) -> None:
    """경로를 적으면 그 경로만 지운다 — 같은 쌍의 다른 칸은 남는다."""
    path = _write(tmp_path, {"rejected": [{"from": "aws::A", "to": "aws::B",
                                           "via_property": "OnePath", "reason": "x"}]})
    apply_review(graph, "aws", path=path)

    assert [e.via_property for e in graph.edges if e.to_id == "aws::B"] == ["AnotherPath"]


def test_confirmed_marks_edges_as_human_checked(graph, tmp_path) -> None:
    """확인한 것은 `reviewed=True`가 된다 — 짐작이라도 사람이 봤으면 사실이다."""
    path = _write(tmp_path, {"confirmed": [{"from": "aws::A", "to": "aws::C",
                                            "reason": "실제 권한 참조"}]})
    stats = apply_review(graph, "aws", path=path)

    assert stats["confirmed"] == 1
    by_target = {e.to_id: e for e in graph.edges}
    assert by_target["aws::C"].reviewed is True
    assert by_target["aws::C"].evidence == "heuristic"  # 근거는 그대로 둔다
    assert by_target["aws::B"].reviewed is False


def test_rejection_wins_over_confirmation(graph, tmp_path) -> None:
    """같은 쌍이 양쪽에 있으면 지운다 — "지웠는데 확인됨"이 남으면 안 된다."""
    path = _write(tmp_path, {
        "rejected": [{"from": "aws::A", "to": "aws::C", "reason": "x"}],
        "confirmed": [{"from": "aws::A", "to": "aws::C", "reason": "y"}],
    })
    apply_review(graph, "aws", path=path)
    assert not [e for e in graph.edges if e.to_id == "aws::C"]


def test_added_edges_are_marked_reviewed(graph, tmp_path) -> None:
    """파서가 못 뽑은 것을 손으로 넣을 수 있다."""
    path = _write(tmp_path, {"added": [{"from": "aws::B", "to": "aws::C",
                                        "via_property": "Manual", "required": True,
                                        "reason": "설명문에 있는데 파서가 못 읽음"}]})
    stats = apply_review(graph, "aws", path=path)

    added = [e for e in graph.edges if e.from_id == "aws::B"]
    assert stats["added"] == 1
    assert added[0].reviewed is True
    assert added[0].evidence == "human-review"
    assert added[0].required is True


def test_added_edge_to_unknown_node_is_skipped(graph, tmp_path, capsys) -> None:
    """없는 부품을 가리키는 항목은 조용히 넣지 않고 경고한다."""
    path = _write(tmp_path, {"added": [{"from": "aws::A", "to": "aws::NOPE",
                                        "via_property": "x", "reason": "오타"}]})
    stats = apply_review(graph, "aws", path=path)
    assert stats["added"] == 0
    assert "노드가 그래프에 없어" in capsys.readouterr().out


def test_missing_review_file_is_a_no_op(graph, tmp_path) -> None:
    before = len(graph.edges)
    stats = apply_review(graph, "aws", path=tmp_path / "없는파일.json")
    assert stats == {"dropped": 0, "confirmed": 0, "added": 0}
    assert len(graph.edges) == before


def test_freshness_warns_when_the_source_moved() -> None:
    """검수는 특정 소스 버전 기준이다. 소스가 바뀌면 알리되 버리지는 않는다."""
    review = load_review("aws")
    against = review.get("reviewed_against")
    assert against, "실제 검수 파일에 reviewed_against가 있어야 한다"

    same = check_freshness("aws", [{"source": against["source"],
                                    "sha256": against["sha256"]}])
    assert same is None

    moved = check_freshness("aws", [{"source": against["source"], "sha256": "f" * 64}])
    assert moved is not None and "다시 확인" in moved


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_shipped_review_entries_are_well_formed(provider) -> None:
    """실제 검수 파일 **전부**를 검사한다.

    aws만 보던 시절에 gcp 파일의 confirmed 항목이 대조 조건 없이 들어가 있었고,
    그게 **모든 엣지를 무조건 확인 처리**하고 있었다. 수집 범위를 넓혔을 때
    새로 생긴 짐작 121개까지 "사람이 확인함"으로 표시될 뻔했다.
    """
    review = load_review(provider)
    for section in ("rejected", "confirmed", "added"):
        for entry in review[section]:
            assert entry.get("reason"), f"{provider} {section}에 이유 없는 항목: {entry}"
            # 대조 조건이 최소 하나는 있어야 한다. 전부 없으면 "모든 엣지"에
            # 해당해서, rejected면 그래프가 통째로 사라지고 confirmed면 검수하지
            # 않은 것까지 확인됐다고 거짓말한다.
            assert any(
                entry.get(k) for k in ("from", "to", "type", "evidence", "via_property")
            ), f"{provider}: 대조 조건이 없는 항목 — {entry}"
    for entry in review["added"]:
        assert entry.get("from") and entry.get("to"), f"추가는 대상을 특정해야 한다: {entry}"


def test_matching_on_target_alone_covers_every_source(graph, tmp_path) -> None:
    """`to`만 적으면 그 대상으로 가는 엣지 전부에 적용된다.

    권한(IAM::Role)처럼 110개 부품이 같은 이유로 가리키는 대상은 한 줄로 판단한다 —
    `RoleArn`이 권한을 뜻하는지는 출발이 무엇이든 답이 같기 때문이다.
    """
    graph.add_node(_node("aws::D"))
    graph.add_edge(_edge("aws::D", "aws::B", "FromAnother"))

    path = _write(tmp_path, {"confirmed": [{"to": "aws::B", "reason": "대상 단위 판단"}]})
    stats = apply_review(graph, "aws", path=path)

    assert stats["confirmed"] == 3  # A→B 두 경로 + D→B
    assert all(e.reviewed for e in graph.edges if e.to_id == "aws::B")
    assert not any(e.reviewed for e in graph.edges if e.to_id == "aws::C")


def test_rejecting_one_pair_under_a_confirmed_target(graph, tmp_path) -> None:
    """대상 전체를 확인하되 예외 몇 개만 빼는 게 실제 검수 형태다.

    EC2::Host가 그랬다 — Placement/HostId는 전용 호스트가 맞지만
    CodeConnections의 HostArn은 전혀 다른 것이었다.
    """
    path = _write(tmp_path, {
        "confirmed": [{"to": "aws::B", "reason": "대체로 맞다"}],
        "rejected": [{"from": "aws::A", "to": "aws::B",
                      "via_property": "OnePath", "reason": "이 칸만 다른 뜻"}],
    })
    apply_review(graph, "aws", path=path)

    b_edges = {e.via_property: e for e in graph.edges if e.to_id == "aws::B"}
    assert set(b_edges) == {"AnotherPath"}
    assert b_edges["AnotherPath"].reviewed is True


def test_matching_on_evidence_covers_a_whole_source(graph, tmp_path) -> None:
    """근거 종류로도 판단할 수 있다.

    AWS가 스키마에 직접 선언한 것(relationshipRef)이나 CDK 팀이 손으로 모은 것
    (cdk-oob)은 우리 짐작이 아니라 남의 검수 결과다. 개별로 보는 대신 "이 출처는
    믿을 만한가"를 한 번 판단하는 게 맞다.
    """
    graph.add_edge(_edge("aws::A", "aws::C", "Declared", evidence="relationshipRef"))
    path = _write(tmp_path, {"confirmed": [{"evidence": "relationshipRef",
                                            "reason": "AWS가 직접 선언"}]})
    apply_review(graph, "aws", path=path)

    by_ev = {(e.evidence, e.via_property): e for e in graph.edges}
    assert by_ev[("relationshipRef", "Declared")].reviewed is True
    assert by_ev[("heuristic", "OnePath")].reviewed is False


def test_review_never_matches_everything_by_accident(graph, tmp_path) -> None:
    """조건이 하나도 없는 항목은 그래프를 통째로 지운다 — 실수로 그러면 안 된다.

    이 동작 자체는 막지 않는다(의도적으로 쓸 수 있다). 대신 실제 검수 파일에
    그런 항목이 없다는 것은 test_shipped_review_entries_all_carry_a_reason이 지킨다.
    """
    path = _write(tmp_path, {"rejected": [{"reason": "조건 없음"}]})
    apply_review(graph, "aws", path=path)
    assert not graph.edges


def test_confirming_keeps_every_field(graph, tmp_path) -> None:
    """확인 표시가 다른 필드를 떨어뜨리면 안 된다.

    실제로 그랬다 — 확인 표시 코드가 Edge를 필드 나열로 다시 만드는 바람에,
    나중에 추가된 target_property가 조용히 사라졌다. AWS는 모든 엣지가 확인
    상태여서 결합 지점이 **전부** 날아갔는데 빌드는 성공했다.
    """
    graph.add_edge(Edge(from_id="aws::A", to_id="aws::C", type="references",
                        via_property="WithTarget", required=True, cardinality="many",
                        evidence="relationshipRef",
                        target_property="GroupId"))
    path = _write(tmp_path, {"confirmed": [{"to": "aws::C", "reason": "x"}]})
    apply_review(graph, "aws", path=path)

    kept = next(e for e in graph.edges if e.via_property == "WithTarget")
    assert kept.reviewed is True
    assert kept.target_property == "GroupId"
    assert kept.required is True and kept.cardinality == "many"
