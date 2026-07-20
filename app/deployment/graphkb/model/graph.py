"""공통 데이터 모델: 리소스 타입 노드 / 의존성 엣지 / 그래프 컨테이너.

브리프 4절의 직렬화 스키마를 그대로 따르며, 저장 시 번들된
schema.json(draft 2020-12)으로 항상 검증한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

import jsonschema

NodeLayer = Literal["core", "vendor"]
EdgeType = Literal["references", "contained_in", "equivalent_to"]
Cardinality = Literal["one", "many"]


@lru_cache(maxsize=1)
def _schema() -> dict:
    """번들된 JSON Schema를 로드한다."""
    path = Path(__file__).with_name("schema.json")
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class Node:
    """리소스 타입 노드 (예: core::vNet, aws::AWS::EC2::Subnet)."""

    id: str
    layer: NodeLayer
    provider: str
    display_name: str
    source: str
    kind: str = "resource_type"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "layer": self.layer,
            "provider": self.provider,
            "kind": self.kind,
            "display_name": self.display_name,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Node:
        return cls(
            id=data["id"],
            layer=data["layer"],
            provider=data["provider"],
            display_name=data["display_name"],
            source=data["source"],
            kind=data.get("kind", "resource_type"),
        )


@dataclass(frozen=True, slots=True)
class Edge:
    """타입 간 의존성 엣지. 방향은 의존하는 쪽 → 의존 대상 (Subnet → VPC)."""

    from_id: str
    to_id: str
    type: EdgeType
    via_property: str
    required: bool
    cardinality: Cardinality
    evidence: str
    confidence: float

    @property
    def key(self) -> tuple[str, str, str, str]:
        """중복 판정 키. 같은 키의 엣지는 confidence가 높은 쪽만 남긴다."""
        return (self.from_id, self.to_id, self.type, self.via_property)

    def to_dict(self) -> dict:
        # 직렬화 키는 브리프 스키마의 "from"/"to" (파이썬 예약어 회피용 필드명 변환)
        return {
            "from": self.from_id,
            "to": self.to_id,
            "type": self.type,
            "via_property": self.via_property,
            "required": self.required,
            "cardinality": self.cardinality,
            "evidence": self.evidence,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Edge:
        return cls(
            from_id=data["from"],
            to_id=data["to"],
            type=data["type"],
            via_property=data["via_property"],
            required=data["required"],
            cardinality=data["cardinality"],
            evidence=data["evidence"],
            confidence=data["confidence"],
        )


@dataclass
class Graph:
    """노드/엣지 컨테이너. 노드는 id 멱등, 엣지는 key 기준 고신뢰 우선."""

    nodes: dict[str, Node] = field(default_factory=dict)
    edges: list[Edge] = field(default_factory=list)
    provenance: list[dict] = field(default_factory=list)
    """이 그래프가 어느 원본에서 나왔는지 (`kbcommon.fetch.describe_source`의 결과).

    산출물에 `_source`로 직렬화된다. 없으면 "어느 입력에서 나왔는지 모른다"는 뜻이고,
    그 상태로는 어떤 수치도 재현하거나 반증할 수 없다 — 그래서 빌드 경로는 항상 채운다.
    """

    _edge_index: dict[tuple[str, str, str, str], int] = field(
        default_factory=dict, repr=False
    )

    def add_node(self, node: Node) -> None:
        """같은 id가 이미 있으면 무시한다 (멱등)."""
        self.nodes.setdefault(node.id, node)

    def add_edge(self, edge: Edge) -> None:
        """엣지를 추가한다. self-loop는 거부, 중복 키는 confidence 높은 쪽 유지."""
        if edge.from_id == edge.to_id:
            return
        existing = self._edge_index.get(edge.key)
        if existing is None:
            self._edge_index[edge.key] = len(self.edges)
            self.edges.append(edge)
        elif edge.confidence > self.edges[existing].confidence:
            self.edges[existing] = edge

    def merge(self, other: Graph) -> None:
        """다른 그래프의 노드/엣지를 합친다 (query의 core+vendor 병합 로드용)."""
        for node in other.nodes.values():
            self.add_node(node)
        for edge in other.edges:
            self.add_edge(edge)
        # 병합 그래프는 여러 원본에서 온다 — 출처를 합쳐야 "이 답이 어디서 왔나"에 답할 수 있다.
        seen = {(p.get("source"), p.get("sha256")) for p in self.provenance}
        for p in other.provenance:
            if (p.get("source"), p.get("sha256")) not in seen:
                self.provenance.append(p)
                seen.add((p.get("source"), p.get("sha256")))

    def to_dict(self) -> dict:
        out: dict = {
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges],
        }
        if self.provenance:
            out["_source"] = self.provenance
        return out

    @classmethod
    def from_dict(cls, data: dict) -> Graph:
        graph = cls()
        for node_data in data.get("nodes", []):
            graph.add_node(Node.from_dict(node_data))
        for edge_data in data.get("edges", []):
            graph.add_edge(Edge.from_dict(edge_data))
        graph.provenance = list(data.get("_source") or [])
        return graph

    def validate(self) -> None:
        """직렬화 결과를 번들 스키마로 검증한다. 위반 시 ValidationError."""
        jsonschema.validate(self.to_dict(), _schema())

    def save(self, path: Path) -> None:
        """검증 후 JSON으로 저장한다."""
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
        path.write_text(payload + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Graph:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
