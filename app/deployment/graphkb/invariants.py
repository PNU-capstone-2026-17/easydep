"""graphkb 산출물의 레코드 간 불변식. 얼개는 `kbcommon/invariants.py` 참고."""

from __future__ import annotations

import collections
from collections.abc import Iterable

from kbcommon.invariants import Invariant, Violation, one_confidence_per_evidence


def _no_casing_duplicates(dataset: dict) -> Iterable[Violation]:
    """대소문자만 다른 노드가 둘 이상 있는가.

    id는 KB 사이의 **조인 키**다. 같은 것이 두 표기로 갈리면 한쪽으로 들어온 질문이
    다른 쪽 데이터를 못 찾는다 — 데이터가 없어서가 아니라 철자가 달라서다.

    Azure가 이 함정을 만든다: ARM 타입명은 대소문자를 구분하지 않아서 Azure 자신도
    API 버전마다 다르게 적는다(`Microsoft.Compute` vs `microsoft.Compute`, 71종).
    그래서 id를 만들 때 반드시 `kbcommon/type_ids.py`로 대표 표기를 거쳐야 한다.
    이 검사는 그걸 빼먹었을 때 울린다.
    """
    nodes = dataset.get("nodes")
    ids = list(nodes) if isinstance(nodes, dict) else [
        n.get("id") for n in nodes or [] if isinstance(n, dict)
    ]
    groups: dict[str, set[str]] = collections.defaultdict(set)
    for node_id in ids:
        if node_id:
            groups[node_id.lower()].add(node_id)
    for lowered, spellings in sorted(groups.items()):
        if len(spellings) > 1:
            yield Violation(
                where=lowered,
                detail=f"같은 타입이 {sorted(spellings)}로 갈려 있습니다",
            )


def _edges_point_at_real_nodes(dataset: dict) -> Iterable[Violation]:
    """엣지의 양 끝이 실재하는 노드인가.

    없는 노드를 가리키는 엣지는 조용히 무시되거나 조회 시점에 터진다. 실제로
    설명문 정규식이 `gcp::service`라는 없는 종류를 만들어 낸 적이 있다.
    """
    nodes = dataset.get("nodes")
    known = set(nodes) if isinstance(nodes, dict) else {
        n.get("id") for n in nodes or [] if isinstance(n, dict)
    }
    for edge in dataset.get("edges") or []:
        for side in ("from", "to"):
            node_id = edge.get(side)
            if node_id and node_id not in known:
                yield Violation(
                    where=f"{edge.get('from')} → {edge.get('to')}",
                    detail=f"{side} 쪽 노드가 그래프에 없습니다: {node_id}",
                )


INVARIANTS = (
    Invariant(
        name="no-casing-duplicate-ids",
        question="대소문자만 다른 노드가 하나로 합쳐졌는가?",
        severity="error",
        check=_no_casing_duplicates,
    ),
    Invariant(
        name="edges-point-at-real-nodes",
        question="엣지의 양 끝이 실재하는 노드인가?",
        severity="error",
        check=_edges_point_at_real_nodes,
    ),
    Invariant(
        name="one-confidence-per-evidence",
        question="같은 근거 라벨에 신뢰도가 하나만 붙는가?",
        severity="report",
        check=one_confidence_per_evidence("edges"),
    ),
)
