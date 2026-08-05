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

from app.core.cloudkb.kbcommon.basis import basis_of, is_fact

#: `app`은 svcmap이 만든 **애플리케이션 개념 층**이다(관계형 DB·큐·객체 스토리지 등).
#: core 층(cb-tumblebug 스웨거 미러)에 우리 개념을 섞으면 미러가 오염되므로 층을
#: 따로 둔다. provider도 "app"이라 `core_concept`(provider=="common" 검색)에 안 걸린다.
NodeLayer = Literal["core", "vendor", "app"]
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

    basis: str = ""
    """**원본이 그렇게 적었는가(`stated`), 우리가 짐작했는가(`inferred`).**

    evidence 라벨에서 정해지므로 손으로 넘길 필요가 없다 — 비워 두면 채워진다.
    라벨 하나에 성격 하나가 규칙이고, 갈리면 라벨을 쪼갠다(`kbcommon/basis.py`).

    예전의 `confidence`(0.5~1.0)를 대신한다. 그 숫자는 척도의 정의가 없어서
    0.9와 0.95의 차이를 아무도 답할 수 없었고, 실제로 쓰이는 건 임계값 하나뿐이었다.
    """

    target_property: str = ""
    """대상 부품이 돌려주는 값 중 **어느 것을 가져다 쓰는가.**

    `via_property`가 "내 어느 칸에 적나"라면 이쪽은 "거기에 무슨 값을 적나"다.
    둘 다 있어야 실제로 조립할 수 있다:

        VPCEndpoint → VPC   via_property=VpcId
                            target_property=DefaultSecurityGroup

    "네트워크를 가리킨다"까지는 via_property로 알지만, 정작 복사해 넣을 값이
    네트워크 번호가 아니라 **그 네트워크의 기본 방화벽**이라는 건 이 필드에만 있다.
    방향도 대상도 맞고 결합 지점만 틀린 형태라 원본 대조로도 안 잡히던 누락이다.

    소스마다 이름이 다르다 — AWS는 `propertyPath`(`/properties/GroupId`),
    GCP servicemapping은 `targetField`, Azure는 ARM 관례상 항상 `id`다.
    모르면 빈 문자열이고, 그건 "이 관계에 결합 지점이 없다"가 아니라 "우리가 모른다"는 뜻이다.
    """

    reviewed: bool = False
    """**사람이 눈으로 보고 맞다고 확인했는가.**

    소스에 핀을 박아 입력이 얼어 있으므로, 손 검수 결과는 다음 빌드에서도 유효하다.
    그래서 "얼마나 확신하는가"(confidence)보다 이 값이 실질적이다 — 짐작이라도
    사람이 확인했으면 사실이고, 확인 안 했으면 여전히 짐작이다.

    검수 목록은 `graphkb/reviewed/*.json`에 있고 빌드가 마지막에 적용한다.
    """

    def __post_init__(self) -> None:
        if not self.basis:
            object.__setattr__(self, "basis", basis_of(self.evidence))

    @property
    def key(self) -> tuple[str, str, str, str]:
        """중복 판정 키. 같은 키면 **사실인 쪽**을 남긴다."""
        return (self.from_id, self.to_id, self.type, self.via_property)

    @property
    def is_fact(self) -> bool:
        """사실로 취급할 수 있는가 — 원본이 명시했거나 사람이 확인했거나."""
        return is_fact(self.basis, self.reviewed)

    def to_dict(self) -> dict:
        # 직렬화 키는 브리프 스키마의 "from"/"to" (파이썬 예약어 회피용 필드명 변환)
        out = {
            "from": self.from_id,
            "to": self.to_id,
            "type": self.type,
            "via_property": self.via_property,
            "required": self.required,
            "cardinality": self.cardinality,
            "evidence": self.evidence,
            "basis": self.basis,
        }
        if self.target_property:
            out["target_property"] = self.target_property
        if self.reviewed:
            out["reviewed"] = True
        return out

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
            basis=data.get("basis", ""),
            target_property=data.get("target_property", ""),
            reviewed=bool(data.get("reviewed", False)),
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
        """엣지를 추가한다. self-loop는 거부, 중복 키는 **사실인 쪽**을 남긴다.

        예전엔 confidence가 높은 쪽을 남겼는데, 그 숫자는 정의가 없어서 우열이
        사실상 임의였다. 지금은 기준이 하나다 — 원본이 명시했거나 사람이 확인한
        것이 짐작을 이긴다. 둘 다 사실이거나 둘 다 짐작이면 먼저 온 것을 둔다.
        """
        if edge.from_id == edge.to_id:
            return
        existing = self._edge_index.get(edge.key)
        if existing is None:
            self._edge_index[edge.key] = len(self.edges)
            self.edges.append(edge)
        elif edge.is_fact and not self.edges[existing].is_fact:
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

    def save(self, path: Path):
        """검증 후 원자적으로 저장한다.

        스키마(레코드 하나의 형태)와 불변식(레코드 사이의 정합성)을 둘 다 통과해야
        쓴다 — capacitykb·costkb·perfkb와 같은 관문이다.

        Returns:
            불변식 결과. `report` 등급 위반은 **호출자가 알려야 한다.**
        """
        from app.core.cloudkb.graphkb.invariants import INVARIANTS
        from app.core.cloudkb.kbcommon.artifact import write_dataset

        return write_dataset(path, self.to_dict(), _schema(), INVARIANTS)

    @classmethod
    def load(cls, path: Path) -> Graph:
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
