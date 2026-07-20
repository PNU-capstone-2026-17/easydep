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

from graphkb.model import Edge, Graph, Node
from graphkb.parsers.review import apply_review, check_freshness, load_review


def _node(id_: str) -> Node:
    return Node(id=id_, layer="vendor", provider="aws",
                display_name=id_.split("::", 1)[1], source="test")


def _edge(frm: str, to: str, via: str, **kw) -> Edge:
    return Edge(from_id=frm, to_id=to, type="references", via_property=via,
                required=kw.get("required", False), cardinality="one",
                evidence=kw.get("evidence", "heuristic"),
                confidence=kw.get("confidence", 0.5))


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


def test_shipped_review_entries_all_carry_a_reason() -> None:
    """왜 그렇게 판단했는지 없는 항목은 다음 사람이 다시 볼 수 없다."""
    review = load_review("aws")
    for section in ("rejected", "confirmed", "added"):
        for entry in review[section]:
            assert entry.get("reason"), f"{section}에 이유 없는 항목: {entry}"
            # from/to/via_property 중 최소 하나는 있어야 한다 — 셋 다 없으면
            # "모든 엣지"에 해당해 그래프가 통째로 사라진다.
            assert any(entry.get(k) for k in ("from", "to", "via_property")), entry
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
