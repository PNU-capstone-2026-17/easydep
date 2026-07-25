"""그래프 질의: 선행 리소스 체인(위상순) / 삭제 영향(역방향 폐포) 계산.

규모가 작아(수천 노드 이하) 별도 그래프 라이브러리 없이
dict 인접 리스트 + BFS + Kahn 위상정렬로 처리한다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from app.deployment.graphkb.model import Edge, Graph, Node

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
        raise ValueError(f"Node not found: {name!r}")
    ids = ", ".join(sorted(node.id for node in candidates))
    raise ValueError(f"The name is ambiguous: {name!r} → candidates: {ids}")


def _dependency_edges(graph: Graph, *, required_only: bool = False) -> list[Edge]:
    """생성 순서에 영향을 주는 엣지만 골라낸다."""
    return [
        edge
        for edge in graph.edges
        if edge.type in _DEPENDENCY_EDGE_TYPES
        and (edge.required or not required_only)
    ]


@dataclass(frozen=True)
class ChainStep:
    """생성 순서의 한 단계. 보통 노드 하나지만, 순환이면 여럿이 한 단계를 이룬다."""

    nodes: list[Node]

    @property
    def cyclic(self) -> bool:
        """서로 참조해서 **이 단계 안에서는** 순서를 정할 수 없는지."""
        return len(self.nodes) > 1


@dataclass(frozen=True)
class ChainResult:
    """선행 체인 계산 결과 — 순환을 **한 단계로 묶어** 순서를 최대한 살린다.

    예전에는 단순 리스트 하나였다. Kahn이 못 놓은 노드를 BFS 발견 순서로 뒤에
    덧붙였는데, 대상 노드 자신이 순환에 걸리면 대상이 그 덧붙임의 첫 원소가 되어
    **진짜 선행 리소스가 대상 뒤로 밀렸다**(결함 C1: 체인 보유 3,225개 중 503개,
    15.6%). 경고도 stderr로만 나가서 반환값을 읽는 쪽은 그게 위상순이 아니란 걸
    알 수 없었다.

    순환 노드를 통째로 "순서 미상" 바구니에 던지는 것도 답이 아니다 — 순환에
    *딸린* 노드까지 함께 쓸려 들어가 멀쩡한 순서 정보를 버린다(EC2::Instance에서
    22개가 그랬다). 그래서 **SCC로 축약한 뒤 위상정렬**한다: 진짜 서로 참조하는
    것만 한 단계로 묶이고, 그 단계들 사이의 순서는 그대로 확정된다.
    """

    steps: list[ChainStep]

    @property
    def ordered(self) -> list[Node]:
        """단계를 평탄화한 목록. **대상 노드가 항상 마지막이다.**"""
        return [n for step in self.steps for n in step.nodes]

    @property
    def cyclic_steps(self) -> list[ChainStep]:
        return [s for s in self.steps if s.cyclic]

    @property
    def has_cycle(self) -> bool:
        return any(s.cyclic for s in self.steps)


def dependency_chain(
    graph: Graph, node_id: str, *, required_only: bool = False
) -> list[Node]:
    """`dependency_chain_detail(...).ordered` — 평탄화된 선행 체인.

    ⚠️ 순환이 있으면 이 평탄화는 **임의 순서를 가진 것처럼 보인다**. 어디가
    순환인지 알아야 한다면 `dependency_chain_detail`을 쓰고 `cyclic_steps`를
    함께 표시하세요.
    """
    return dependency_chain_detail(graph, node_id, required_only=required_only).ordered


def _strongly_connected(
    nodes: list[str], forward: dict[str, list[str]]
) -> dict[str, int]:
    """Tarjan SCC — 노드 → 컴포넌트 번호. 재귀 없이(깊이 수천이라 스택이 위험).

    여기서는 *묶는* 용도로만 쓰고 순서는 뒤의 Kahn이 정한다. Tarjan의 방출 순서도
    위상순이지만, 그러면 비순환 그래프의 기존 정렬 결과가 미묘하게 달라진다 —
    사전순 tie-break를 유지하려고 정렬은 Kahn에 맡긴다.
    """
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    comp: dict[str, int] = {}
    counter = 0
    ncomp = 0

    for root in nodes:
        if root in index:
            continue
        # (노드, 다음에 볼 이웃 위치) 쌍을 직접 관리하는 반복형 DFS
        work: list[tuple[str, int]] = [(root, 0)]
        while work:
            current, next_child = work[-1]
            if next_child == 0:
                index[current] = low[current] = counter
                counter += 1
                stack.append(current)
                on_stack.add(current)

            children = forward.get(current, ())
            if next_child < len(children):
                work[-1] = (current, next_child + 1)
                child = children[next_child]
                if child not in index:
                    work.append((child, 0))
                elif child in on_stack:
                    low[current] = min(low[current], index[child])
                continue

            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[current])
            if low[current] == index[current]:
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    comp[member] = ncomp
                    if member == current:
                        break
                ncomp += 1
    return comp


def dependency_chain_detail(
    graph: Graph, node_id: str, *, required_only: bool = False
) -> ChainResult:
    """node_id 생성에 필요한 선행 노드들을 위상순(선행 먼저)으로 반환한다.

    자기 자신이 `ordered`의 마지막 원소로 포함된다. references/contained_in 엣지를
    따라 전방 폐포를 수집하고, **SCC로 축약한 뒤** 그 위에서 Kahn 위상정렬을 한다.
    순환은 언제나 존재하므로(실측 2-사이클 20개) 순환 자체를 없애는 대신,
    서로 참조하는 것들을 한 단계(`ChainStep.cyclic`)로 묶어 나머지 순서를 살린다.

    Args:
        graph: 대상 그래프.
        node_id: 시작 노드 id (resolve_node를 거친 정확한 id).
        required_only: True면 required 엣지만 제약으로 취급.
    """
    if node_id not in graph.nodes:
        raise ValueError(f"Node not found: {node_id!r}")

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

    # 폐포 안의 인접 리스트 (정렬해 두면 SCC·정렬 결과가 결정적이다)
    inner: dict[str, list[str]] = {
        nid: sorted({t for t in forward.get(nid, []) if t in seen and t != nid})
        for nid in closure
    }
    comp_of = _strongly_connected(closure, inner)

    members: dict[int, list[str]] = {}
    for nid in closure:
        members.setdefault(comp_of[nid], []).append(nid)

    # 축약 DAG에서 Kahn: 컴포넌트가 의존하는 컴포넌트 수를 센다. 컴포넌트가 전부
    # 크기 1이면(=순환 없음) 예전 노드 단위 정렬과 결과가 같다 — 사전순 tie-break 포함.
    reverse: dict[int, set[int]] = {c: set() for c in members}
    dependencies: dict[int, set[int]] = {c: set() for c in members}
    for nid, targets in inner.items():
        for target in targets:
            src, dst = comp_of[nid], comp_of[target]
            if src != dst:
                dependencies[src].add(dst)
                reverse[dst].add(src)
    out_degree = {c: len(deps) for c, deps in dependencies.items()}

    def _key(comp: int) -> str:
        return min(members[comp])

    ready = sorted((c for c, deg in out_degree.items() if deg == 0), key=_key)
    order: list[int] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        newly = []
        for dependent in reverse[current]:
            out_degree[dependent] -= 1
            if out_degree[dependent] == 0:
                newly.append(dependent)
        ready = sorted(ready + newly, key=_key)

    # 대상이 속한 컴포넌트는 폐포의 유일한 소스라 항상 마지막이지만, 계약이므로
    # 방어적으로 확인한다("자기 자신이 마지막 원소" — docstring).
    target_comp = comp_of[node_id]
    order = [c for c in order if c != target_comp] + [target_comp]

    def _step(comp: int) -> ChainStep:
        ids = sorted(members[comp])
        if comp == target_comp:
            # 대상이 순환 그룹 안에 있어도 그룹 안에서 마지막에 온다.
            ids = [nid for nid in ids if nid != node_id] + [node_id]
        return ChainStep(nodes=[graph.nodes[nid] for nid in ids])

    return ChainResult(steps=[_step(comp) for comp in order])


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
        raise ValueError(f"by must be 'dependencies' or 'dependents': {by!r}")

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
        raise ValueError(f"Node not found: {node_id!r}")

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
        raise ValueError(f"Node not found: {node_id!r}")

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
