"""Cypher 스크립트 내보내기 (cypher-shell / Neo4j Browser용)."""

from __future__ import annotations

from pathlib import Path

from app.deployment.graphkb.model import Graph
from app.deployment.graphkb.neo4j_load import cypher_script


def write_cypher(graph: Graph, path: Path) -> None:
    """그래프를 Cypher 적재 스크립트 파일로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cypher_script(graph), encoding="utf-8")
