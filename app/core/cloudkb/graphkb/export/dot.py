"""Graphviz DOT 내보내기 (검토용). 순수 문자열 조립."""

from __future__ import annotations

from pathlib import Path

from app.core.cloudkb.graphkb.model import Graph


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_dot(graph: Graph, path: Path) -> None:
    """그래프를 DOT 파일로 저장한다.

    코어 노드는 box, 벤더 노드는 ellipse. **짐작인 엣지는 dashed**,
    contained_in 엣지는 회색으로 구분한다.
    """
    lines = ["digraph graphkb {", "  rankdir=LR;", "  node [fontsize=10];"]
    for node in graph.nodes.values():
        shape = "box" if node.layer == "core" else "ellipse"
        lines.append(
            f"  {_quote(node.id)} [label={_quote(node.display_name)}, shape={shape}];"
        )
    for edge in graph.edges:
        attrs = [f"label={_quote(edge.via_property)}"]
        if not edge.is_fact:
            attrs.append("style=dashed")
        if edge.type == "contained_in":
            attrs.append("color=gray")
        elif edge.type == "equivalent_to":
            attrs.append("dir=none")
            attrs.append("color=blue")
        lines.append(
            f"  {_quote(edge.from_id)} -> {_quote(edge.to_id)} [{', '.join(attrs)}];"
        )
    lines.append("}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
