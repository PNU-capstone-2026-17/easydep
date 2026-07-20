"""GraphML 내보내기 (yEd/Gephi 등에서 검토용).

규모가 작으므로 networkx 없이 xml.etree.ElementTree로 직접 생성한다.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from graphkb.model import Graph

_NS = "http://graphml.graphdrawing.org/xmlns"

_NODE_KEYS = [
    ("layer", "string"),
    ("provider", "string"),
    ("kind", "string"),
    ("display_name", "string"),
    ("source", "string"),
]
_EDGE_KEYS = [
    ("type", "string"),
    ("via_property", "string"),
    ("required", "boolean"),
    ("cardinality", "string"),
    ("evidence", "string"),
    ("basis", "string"),
]


def write_graphml(graph: Graph, path: Path) -> None:
    """그래프를 GraphML 파일로 저장한다."""
    ET.register_namespace("", _NS)
    root = ET.Element(f"{{{_NS}}}graphml")

    key_ids: dict[tuple[str, str], str] = {}
    for i, (name, attr_type) in enumerate(_NODE_KEYS):
        key_id = f"dn{i}"
        key_ids[("node", name)] = key_id
        ET.SubElement(
            root,
            f"{{{_NS}}}key",
            id=key_id,
            attrib={"for": "node", "attr.name": name, "attr.type": attr_type},
        )
    for i, (name, attr_type) in enumerate(_EDGE_KEYS):
        key_id = f"de{i}"
        key_ids[("edge", name)] = key_id
        ET.SubElement(
            root,
            f"{{{_NS}}}key",
            id=key_id,
            attrib={"for": "edge", "attr.name": name, "attr.type": attr_type},
        )

    graph_el = ET.SubElement(root, f"{{{_NS}}}graph", id="graphkb", edgedefault="directed")

    for node in graph.nodes.values():
        node_el = ET.SubElement(graph_el, f"{{{_NS}}}node", id=node.id)
        values = node.to_dict()
        for name, _ in _NODE_KEYS:
            data = ET.SubElement(node_el, f"{{{_NS}}}data", key=key_ids[("node", name)])
            data.text = str(values[name])

    for i, edge in enumerate(graph.edges):
        edge_el = ET.SubElement(
            graph_el,
            f"{{{_NS}}}edge",
            id=f"e{i}",
            source=edge.from_id,
            target=edge.to_id,
        )
        values = edge.to_dict()
        for name, attr_type in _EDGE_KEYS:
            data = ET.SubElement(edge_el, f"{{{_NS}}}data", key=key_ids[("edge", name)])
            value = values[name]
            data.text = str(value).lower() if attr_type == "boolean" else str(value)

    tree = ET.ElementTree(root)
    ET.indent(tree)
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(path, encoding="utf-8", xml_declaration=True)
