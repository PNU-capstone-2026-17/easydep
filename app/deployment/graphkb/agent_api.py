"""에이전트용 사전 정의 질의 API.

text-to-Cypher 대신 안전한 정적 질의 함수들을 제공한다 (Phase 3 결정:
LLM이 임의 Cypher를 생성하면 검증이 어렵고 Neo4j 없이는 동작하지 않으므로,
output/의 JSON 그래프를 직접 읽는 사전 정의 질의를 1차 인터페이스로 한다).

모든 함수는 에이전트(LLM)가 그대로 읽을 수 있는 한국어 텍스트를 반환하고,
실패도 예외 대신 안내 메시지로 돌려준다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from graphkb.model import Graph
from graphkb.query import (
    dependency_chain,
    dependents,
    equivalents,
    rank_types as _rank_types,
    resolve_node,
)

DEFAULT_OUTPUT_DIR = Path("output")
GRAPH_FILES = (
    "core-graph.json",
    "aws-graph.json",
    "azure-graph.json",
    "gcp-graph.json",
    "mapping-graph.json",
)

_MISSING_MESSAGE = (
    "그래프 산출물이 없습니다. 먼저 `python -m graphkb build --source "
    "tumblebug|cfn|azure|gcp|mapping` 으로 그래프를 생성하세요."
)


@lru_cache(maxsize=4)
def _load_merged_cached(output_dir: str) -> Graph | None:
    base = Path(output_dir)
    merged = Graph()
    found = False
    for name in GRAPH_FILES:
        path = base / name
        if path.exists():
            merged.merge(Graph.load(path))
            found = True
    return merged if found else None


def load_merged(output_dir: Path | str = DEFAULT_OUTPUT_DIR) -> Graph | None:
    """output/의 그래프 산출물을 모두 병합해 반환한다 (없으면 None, 캐시됨)."""
    return _load_merged_cached(str(output_dir))


def _resolve(graph: Graph, name: str):
    """resolve_node의 예외를 에이전트용 메시지로 바꾼다."""
    try:
        return resolve_node(graph, name), None
    except ValueError as exc:
        return None, str(exc)


def creation_order(
    resource_type: str,
    *,
    required_only: bool = False,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
) -> str:
    """리소스 타입 생성에 필요한 선행 타입 체인을 위상순 텍스트로 반환한다."""
    graph = load_merged(output_dir)
    if graph is None:
        return _MISSING_MESSAGE
    node, error = _resolve(graph, resource_type)
    if node is None:
        return error
    chain = dependency_chain(graph, node.id, required_only=required_only)
    if len(chain) == 1:
        return f"{node.id} 는 선행 리소스 타입이 없습니다. 바로 생성할 수 있습니다."
    lines = [f"{node.id} 생성에 필요한 선행 체인 (먼저 만들 것부터):"]
    for i, item in enumerate(chain, start=1):
        suffix = " ← 대상" if item.id == node.id else ""
        lines.append(f"{i}. {item.id}{suffix}")
    return "\n".join(lines)


def deletion_impact(
    resource_type: str, *, output_dir: Path | str = DEFAULT_OUTPUT_DIR
) -> str:
    """리소스 타입 삭제 시 영향받는(의존하는) 타입 목록을 반환한다."""
    graph = load_merged(output_dir)
    if graph is None:
        return _MISSING_MESSAGE
    node, error = _resolve(graph, resource_type)
    if node is None:
        return error
    affected = dependents(graph, node.id)
    if not affected:
        return f"{node.id} 를 삭제해도 스키마상 직접 영향받는 타입은 없습니다."
    lines = [f"{node.id} 삭제 시 영향받는 타입 {len(affected)}개:"]
    lines.extend(f"- {item.id}" for item in affected)
    return "\n".join(lines)


def equivalent_types(
    resource_type: str, *, output_dir: Path | str = DEFAULT_OUTPUT_DIR
) -> str:
    """다른 클라우드/코어 레이어에서 같은 것을 가리키는 타입들을 반환한다."""
    graph = load_merged(output_dir)
    if graph is None:
        return _MISSING_MESSAGE
    node, error = _resolve(graph, resource_type)
    if node is None:
        return error
    peers = equivalents(graph, node.id)
    if not peers:
        return (
            f"{node.id} 의 동치 타입 정보가 없습니다 "
            "(mapping-graph.json이 없거나 매핑 미등록)."
        )
    lines = [f"{node.id} 와 같은 것을 가리키는 타입:"]
    lines.extend(f"- {item.id} ({item.provider})" for item in peers)
    return "\n".join(lines)


def describe_type(
    resource_type: str, *, output_dir: Path | str = DEFAULT_OUTPUT_DIR
) -> str:
    """타입의 기본 정보와 나가는 의존 엣지(참조/포함)를 상세히 반환한다."""
    graph = load_merged(output_dir)
    if graph is None:
        return _MISSING_MESSAGE
    node, error = _resolve(graph, resource_type)
    if node is None:
        return error
    lines = [
        f"{node.id}",
        f"- 레이어: {node.layer} / 프로바이더: {node.provider} / 출처: {node.source}",
    ]
    outgoing = [e for e in graph.edges if e.from_id == node.id]
    if outgoing:
        lines.append("- 의존(나가는 엣지):")
        for edge in sorted(outgoing, key=lambda e: (-e.confidence, e.to_id)):
            required = "필수" if edge.required else "선택"
            via = f" via {edge.via_property}" if edge.via_property else ""
            lines.append(
                f"  · {edge.type} → {edge.to_id}{via} "
                f"({required}, {edge.cardinality}, {edge.evidence}, "
                f"신뢰도 {edge.confidence})"
            )
    else:
        lines.append("- 의존(나가는 엣지): 없음")
    return "\n".join(lines)


def rank_types(
    by: str = "dependencies",
    *,
    provider: str | None = None,
    limit: int = 10,
    required_only: bool = False,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
) -> str:
    """의존 관계가 가장 많은 타입 순위를 반환한다 (전체 집계 질의)."""
    graph = load_merged(output_dir)
    if graph is None:
        return _MISSING_MESSAGE
    try:
        ranked = _rank_types(
            graph, by=by, provider=provider, limit=limit, required_only=required_only
        )
    except ValueError as exc:
        return str(exc)
    if not ranked:
        scope = f" (provider={provider})" if provider else ""
        return f"순위를 낼 수 있는 타입이 없습니다{scope}."

    label = (
        "직접 의존하는 타입 수(이 타입을 만들려면 필요한 것)"
        if by == "dependencies"
        else "이 타입에 의존하는 타입 수(삭제 시 영향받는 것)"
    )
    scope = f"{provider} " if provider else ""
    lines = [f"{scope}타입 중 {label} 상위 {len(ranked)}개:"]
    for i, (node, count) in enumerate(ranked, start=1):
        lines.append(f"{i}. {node.id} — {count}개")
    return "\n".join(lines)


def search_types(
    keyword: str,
    *,
    provider: str | None = None,
    limit: int = 20,
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
) -> str:
    """키워드(부분 문자열, 대소문자 무시)로 리소스 타입을 검색한다."""
    graph = load_merged(output_dir)
    if graph is None:
        return _MISSING_MESSAGE
    lowered = keyword.lower()
    matches = [
        node
        for node in graph.nodes.values()
        if lowered in node.id.lower()
        and (provider is None or node.provider == provider)
    ]
    if not matches:
        scope = f" (provider={provider})" if provider else ""
        return f"'{keyword}'{scope} 에 해당하는 타입이 없습니다."
    total = len(matches)
    shown = sorted(matches, key=lambda n: n.id)[: max(1, limit)]
    lines = [f"'{keyword}' 검색 결과 {total}개 중 {len(shown)}개:"]
    lines.extend(f"- {node.id}" for node in shown)
    return "\n".join(lines)
