"""Neo4j 적재 (Phase 3).

두 가지 경로를 제공한다:
- cypher_script(): Neo4j Browser나 cypher-shell에 그대로 넣을 수 있는
  텍스트 스크립트 생성 — 드라이버/서버 없이도 산출물을 만들 수 있다.
- load_to_neo4j(): 공식 파이썬 드라이버로 UNWIND 배치 적재.
  neo4j 패키지는 선택 의존성이다: `uv sync --extra neo4j` 또는 `uv add neo4j`.

데이터 모델:
- 노드: (:ResourceType {id, layer, provider, kind, display_name, source})
  + id 유니크 제약.
- 관계: REFERENCES / CONTAINED_IN / EQUIVALENT_TO
  {via_property, required, cardinality, evidence, basis}.
  관계 MERGE 키는 (양 끝, 타입, via_property) — Edge.key와 동일한 중복 기준.
"""

from __future__ import annotations

from graphkb.model import Graph

CONSTRAINT = (
    "CREATE CONSTRAINT graphkb_resource_type_id IF NOT EXISTS "
    "FOR (n:ResourceType) REQUIRE n.id IS UNIQUE"
)

REL_TYPES = {
    "references": "REFERENCES",
    "contained_in": "CONTAINED_IN",
    "equivalent_to": "EQUIVALENT_TO",
}

_NODE_MERGE = (
    "UNWIND $rows AS row "
    "MERGE (n:ResourceType {id: row.id}) "
    "SET n.layer = row.layer, n.provider = row.provider, n.kind = row.kind, "
    "n.display_name = row.display_name, n.source = row.source"
)


def _edge_merge(rel_type: str) -> str:
    return (
        "UNWIND $rows AS row "
        "MATCH (a:ResourceType {id: row.`from`}), (b:ResourceType {id: row.`to`}) "
        f"MERGE (a)-[r:{rel_type} {{via_property: row.via_property}}]->(b) "
        "SET r.required = row.required, r.cardinality = row.cardinality, "
        "r.evidence = row.evidence, r.basis = row.basis"
    )


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def cypher_script(graph: Graph) -> str:
    """cypher-shell/Browser용 적재 스크립트를 생성한다."""
    lines = [CONSTRAINT + ";"]
    for node in graph.nodes.values():
        lines.append(
            f"MERGE (n:ResourceType {{id: {_quote(node.id)}}}) "
            f"SET n.layer = {_quote(node.layer)}, "
            f"n.provider = {_quote(node.provider)}, "
            f"n.kind = {_quote(node.kind)}, "
            f"n.display_name = {_quote(node.display_name)}, "
            f"n.source = {_quote(node.source)};"
        )
    for edge in graph.edges:
        rel = REL_TYPES[edge.type]
        required = "true" if edge.required else "false"
        lines.append(
            f"MATCH (a:ResourceType {{id: {_quote(edge.from_id)}}}), "
            f"(b:ResourceType {{id: {_quote(edge.to_id)}}}) "
            f"MERGE (a)-[r:{rel} {{via_property: {_quote(edge.via_property)}}}]->(b) "
            f"SET r.required = {required}, "
            f"r.cardinality = {_quote(edge.cardinality)}, "
            f"r.evidence = {_quote(edge.evidence)}, "
            f'r.basis = "{edge.basis}";'
        )
    return "\n".join(lines) + "\n"


def load_to_neo4j(
    graph: Graph,
    *,
    uri: str = "bolt://localhost:7687",
    user: str = "neo4j",
    password: str | None = None,
    database: str = "neo4j",
) -> dict[str, int]:
    """드라이버로 그래프를 적재하고 {"nodes": n, "edges": m}를 반환한다."""
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise RuntimeError(
            "neo4j 드라이버가 설치되어 있지 않습니다. "
            "`uv sync --extra neo4j` (또는 `uv add neo4j`) 후 다시 실행하세요."
        ) from exc

    node_rows = [node.to_dict() for node in graph.nodes.values()]
    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        driver.execute_query(CONSTRAINT, database_=database)
        if node_rows:
            driver.execute_query(_NODE_MERGE, rows=node_rows, database_=database)
        edge_count = 0
        for edge_type, rel_type in REL_TYPES.items():
            rows = [e.to_dict() for e in graph.edges if e.type == edge_type]
            if rows:
                driver.execute_query(
                    _edge_merge(rel_type), rows=rows, database_=database
                )
                edge_count += len(rows)
    return {"nodes": len(node_rows), "edges": edge_count}
