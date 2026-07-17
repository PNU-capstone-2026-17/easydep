"""그래프 질의: 선행 리소스 체인(위상순) / 삭제 영향(역방향 폐포) 계산.

규모가 작아(수천 노드 이하) 별도 그래프 라이브러리 없이
dict 인접 리스트 + BFS + Kahn 위상정렬로 처리한다.
"""

from __future__ import annotations

import sys
from collections import deque

from graphkb.model import Edge, Graph, Node

# 생성 순서 제약으로 취급하는 엣지 종류 (equivalent_to는 순서와 무관)
_DEPENDENCY_EDGE_TYPES = frozenset({"references", "contained_in"})


def resolve_node(graph: Graph, name: str) -> Node:
    """이름으로 노드를 찾는다.

    정확한 id 일치를 우선하고, 아니면 display_name(또는 id의 '::' 뒤 부분)을
    대소문자 무시로 비교한다. 후보가 여럿이면 후보 목록을 담은 ValueError.

    Args:
        graph: 대상 그래프.
        name: "core::vNet", "vm", "AWS::EC2::Subnet" 같은 노드 이름.
    """
    if name in graph.nodes:
        return graph.nodes[name]

    lowered = name.lower()
    candidates = [
        node
        for node in graph.nodes.values()
        if node.display_name.lower() == lowered
        or node.id.split("::", 1)[-1].lower() == lowered
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(f"노드를 찾을 수 없습니다: {name!r}")
    ids = ", ".join(sorted(node.id for node in candidates))
    raise ValueError(f"이름이 모호합니다: {name!r} → 후보: {ids}")


def _dependency_edges(graph: Graph, *, required_only: bool = False) -> list[Edge]:
    """생성 순서에 영향을 주는 엣지만 골라낸다."""
    return [
        edge
        for edge in graph.edges
        if edge.type in _DEPENDENCY_EDGE_TYPES
        and (edge.required or not required_only)
    ]


def dependency_chain(
    graph: Graph, node_id: str, *, required_only: bool = False
) -> list[Node]:
    """node_id 생성에 필요한 선행 노드들을 위상순(선행 먼저)으로 반환한다.

    자기 자신이 마지막 원소로 포함된다. references/contained_in 엣지를 따라
    전방 폐포를 수집한 뒤 부분그래프에서 Kahn 위상정렬을 수행한다.
    사이클이 있으면 stderr 경고 후 남은 노드를 BFS 발견 순서로 덧붙인다.

    Args:
        graph: 대상 그래프.
        node_id: 시작 노드 id (resolve_node를 거친 정확한 id).
        required_only: True면 required 엣지만 제약으로 취급.
    """
    if node_id not in graph.nodes:
        raise ValueError(f"노드를 찾을 수 없습니다: {node_id!r}")

    edges = _dependency_edges(graph, required_only=required_only)
    forward: dict[str, list[str]] = {}
    for edge in edges:
        forward.setdefault(edge.from_id, []).append(edge.to_id)

    # 전방 폐포(BFS): node_id가 의존하는 모든 타입 수집
    closure: list[str] = [node_id]
    seen = {node_id}
    queue = deque([node_id])
    while queue:
        current = queue.popleft()
        for target in forward.get(current, []):
            if target not in seen and target in graph.nodes:
                seen.add(target)
                closure.append(target)
                queue.append(target)

    # 부분그래프에서 Kahn 위상정렬: 의존 대상(선행)이 먼저 나오도록
    # "선행 → 의존자" 방향의 진입 차수를 계산한다.
    out_degree = {nid: 0 for nid in seen}  # nid가 폐포 안에서 의존하는 수
    reverse: dict[str, list[str]] = {nid: [] for nid in seen}
    for edge in edges:
        if edge.from_id in seen and edge.to_id in seen:
            out_degree[edge.from_id] += 1
            reverse[edge.to_id].append(edge.from_id)

    ready = deque(sorted(nid for nid, deg in out_degree.items() if deg == 0))
    ordered: list[str] = []
    while ready:
        current = ready.popleft()
        ordered.append(current)
        for dependent in sorted(reverse[current]):
            out_degree[dependent] -= 1
            if out_degree[dependent] == 0:
                ready.append(dependent)

    if len(ordered) < len(seen):
        remaining = [nid for nid in closure if nid not in set(ordered)]
        print(
            f"경고: 의존성 사이클 감지 — {len(remaining)}개 노드를 "
            "BFS 발견 순서로 덧붙입니다.",
            file=sys.stderr,
        )
        ordered.extend(remaining)

    return [graph.nodes[nid] for nid in ordered]


def rank_types(
    graph: Graph,
    *,
    by: str = "dependencies",
    provider: str | None = None,
    required_only: bool = False,
    limit: int = 10,
) -> list[tuple[Node, int]]:
    """타입을 직접 의존 관계 수로 순위 매긴다 (많은 순).

    타입별 질의(dependency_chain 등)로는 "전체에서 가장 ~한 타입"에 답할 수 없어
    (1,600개 타입을 하나씩 조회해야 하므로) 집계 질의를 따로 제공한다.

    Args:
        graph: 대상 그래프.
        by: "dependencies"면 이 타입이 의존하는 서로 다른 타입 수(나가는 엣지),
            "dependents"면 이 타입에 의존하는 타입 수(들어오는 엣지).
        provider: "aws" | "azure" | "gcp" | "common". 지정하면 해당 프로바이더만.
        required_only: True면 required 엣지만 센다.
        limit: 반환할 상위 개수.

    Returns:
        (노드, 개수) 튜플 목록. 개수 내림차순, 동점이면 id 사전순.
    """
    if by not in ("dependencies", "dependents"):
        raise ValueError(f"by는 'dependencies' 또는 'dependents'여야 합니다: {by!r}")

    # 엣지가 아니라 **서로 다른 상대 타입 수**를 센다
    # (한 타입을 여러 프로퍼티로 참조해도 의존 대상은 하나다).
    peers: dict[str, set[str]] = {}
    for edge in _dependency_edges(graph, required_only=required_only):
        source, target = (
            (edge.from_id, edge.to_id)
            if by == "dependencies"
            else (edge.to_id, edge.from_id)
        )
        peers.setdefault(source, set()).add(target)

    ranked = [
        (graph.nodes[node_id], len(others))
        for node_id, others in peers.items()
        if node_id in graph.nodes
        and (provider is None or graph.nodes[node_id].provider == provider)
    ]
    ranked.sort(key=lambda item: (-item[1], item[0].id))
    return ranked[: max(1, limit)]


def equivalents(graph: Graph, node_id: str) -> list[Node]:
    """다른 레이어/벤더에서 같은 것을 가리키는 타입들을 반환한다.

    equivalent_to 엣지를 방향 무시(무향)로 추이적으로 따라간다.
    예: aws VPC → core vNet → azure virtualNetworks, gcp ComputeNetwork.
    자기 자신은 포함하지 않는다.
    """
    if node_id not in graph.nodes:
        raise ValueError(f"노드를 찾을 수 없습니다: {node_id!r}")

    undirected: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.type == "equivalent_to":
            undirected.setdefault(edge.from_id, []).append(edge.to_id)
            undirected.setdefault(edge.to_id, []).append(edge.from_id)

    result: list[Node] = []
    seen = {node_id}
    queue = deque([node_id])
    while queue:
        current = queue.popleft()
        for peer in sorted(undirected.get(current, [])):
            if peer not in seen and peer in graph.nodes:
                seen.add(peer)
                result.append(graph.nodes[peer])
                queue.append(peer)
    return result


def dependents(graph: Graph, node_id: str) -> list[Node]:
    """node_id를 삭제하면 영향받는(직·간접 의존하는) 노드들을 반환한다.

    역방향 폐포를 BFS로 수집하며, 자기 자신은 포함하지 않는다.
    """
    if node_id not in graph.nodes:
        raise ValueError(f"노드를 찾을 수 없습니다: {node_id!r}")

    reverse: dict[str, list[str]] = {}
    for edge in _dependency_edges(graph):
        reverse.setdefault(edge.to_id, []).append(edge.from_id)

    result: list[Node] = []
    seen = {node_id}
    queue = deque([node_id])
    while queue:
        current = queue.popleft()
        for dependent in sorted(reverse.get(current, [])):
            if dependent not in seen and dependent in graph.nodes:
                seen.add(dependent)
                result.append(graph.nodes[dependent])
                queue.append(dependent)
    return result
