"""매핑 레이어: 코어 타입 ↔ 벤더 네이티브 타입 동치(equivalent_to) 그래프.

1차 근거는 CB-Spider 드라이버 소스의 변환 로직이다. 사람이 검수한 매핑은
core_vendor_map.json 으로 번들되며(status="confirmed"), suggest()는
이름 유사도 휴리스틱으로 새 후보(status="candidate")를 생성해 사람 검수용
파일을 만든다 — 검수 후 status를 confirmed로 바꿔 --mapping-file로 넘기면
그래프에 반영된다 (반자동 파이프라인).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from app.deployment.graphkb.model import Edge, Graph, Node
from app.deployment.kbcommon.fetch import describe_source
from app.deployment.kbcommon.invariants import announce

SOURCE = "cb-spider-mapping"

_PROVIDER_LAYER_SOURCE = {
    "aws": "cloudformation-registry",
    "azure": "bicep-types-az",
    "gcp": "kcc-crd",
}


@lru_cache(maxsize=1)
def _bundled_mappings() -> list[dict]:
    path = Path(__file__).with_name("core_vendor_map.json")
    return json.loads(path.read_text(encoding="utf-8"))["mappings"]


def _core_node(core_type: str) -> Node:
    return Node(
        id=f"core::{core_type}",
        layer="core",
        provider="common",
        display_name=core_type,
        source=SOURCE,
    )


def _vendor_node(provider: str, target: str) -> Node:
    return Node(
        id=f"{provider}::{target}",
        layer="vendor",
        provider=provider,
        display_name=target,
        source=SOURCE,
    )


def build_graph(mappings: list[dict]) -> Graph:
    """confirmed 매핑 항목들을 equivalent_to 엣지 그래프로 변환한다."""
    graph = Graph()
    for entry in mappings:
        if entry.get("status") != "confirmed":
            continue
        core = entry["core"]
        provider = entry["provider"]
        target = entry["target"]
        graph.add_node(_core_node(core))
        graph.add_node(_vendor_node(provider, target))
        graph.add_edge(
            Edge(
                from_id=f"core::{core}",
                to_id=f"{provider}::{target}",
                type="equivalent_to",
                via_property="",
                required=False,
                cardinality="one",
                evidence="cb-spider-driver",
                # 드라이버 코드를 사람이 읽고 만든 매핑이라 짐작이지만, `status:
                # confirmed`인 것만 그래프에 들어오므로 검수된 짐작이다.
                reviewed=True,
            )
        )
    return graph


def load_mappings(mapping_file: Path | None = None) -> list[dict]:
    """번들 매핑에 사용자 검수 파일을 병합한다 (같은 키는 사용자 쪽 우선)."""
    merged: dict[tuple[str, str, str], dict] = {
        (m["core"], m["provider"], m["target"]): m for m in _bundled_mappings()
    }
    if mapping_file is not None:
        user = json.loads(mapping_file.read_text(encoding="utf-8"))
        for entry in user.get("mappings", []):
            merged[(entry["core"], entry["provider"], entry["target"])] = entry
    return list(merged.values())


def _name_tokens(name: str) -> set[str]:
    """타입명을 비교용 토큰으로 정규화한다.

    "AWS::EC2::SecurityGroup" → {"securitygroup"},
    "Microsoft.Network/virtualNetworks/subnets" → {"subnets", "subnet"},
    "ComputeSubnetwork" → {"computesubnetwork", "subnetwork"}.
    """
    last = re.split(r"[:/.]+", name)[-1]
    lowered = last.lower()
    tokens = {lowered}
    if lowered.endswith("ies"):
        tokens.add(lowered[:-3] + "y")
    if lowered.endswith("es"):
        tokens.add(lowered[:-2])
    if lowered.endswith("s"):
        tokens.add(lowered[:-1])
    # CamelCase 마지막 단어 ("ComputeSubnetwork" → "subnetwork")
    words = re.findall(r"[A-Z][a-z0-9]+", last)
    if len(words) >= 2:
        tokens.add(words[-1].lower())
        tokens.add("".join(words[1:]).lower())
    return tokens


def suggest(core_graph: Graph, vendor_graphs: dict[str, Graph]) -> list[dict]:
    """이름 유사도 기반으로 매핑 후보를 생성한다 (사람 검수용).

    이미 confirmed 매핑이 있는 (core, provider) 조합은 건너뛴다.
    """
    confirmed = {
        (m["core"], m["provider"])
        for m in _bundled_mappings()
        if m.get("status") == "confirmed"
    }
    candidates: list[dict] = []
    for core_node in core_graph.nodes.values():
        if core_node.layer != "core":
            continue
        core_tokens = _name_tokens(core_node.display_name)
        for provider, vendor_graph in vendor_graphs.items():
            if (core_node.display_name, provider) in confirmed:
                continue
            for vendor_node in vendor_graph.nodes.values():
                if vendor_node.provider != provider:
                    continue
                vendor_tokens = _name_tokens(vendor_node.display_name)
                if core_tokens & vendor_tokens:
                    candidates.append(
                        {
                            "core": core_node.display_name,
                            "provider": provider,
                            "target": vendor_node.display_name,
                            "note": "이름 유사도 휴리스틱 후보 — 사람 검수 필요",
                            "status": "candidate",
                        }
                    )
    return candidates


def build(output: Path, *, mapping_file: Path | None = None) -> Graph:
    """매핑 그래프를 만들어 output에 저장한다."""
    mappings = load_mappings(mapping_file)
    graph = build_graph(mappings)
    # 번들 파일이라 fetch를 거치지 않지만, 무엇을 읽었는지는 똑같이 남긴다 —
    # 사람이 손으로 고치는 파일이라 오히려 버전 추적이 더 필요하다.
    source_path = mapping_file or Path(__file__).with_name("core_vendor_map.json")
    graph.provenance = [describe_source(source_path, "cb-spider-map")]
    announce(graph.save(output), "graphkb/mapping")
    confirmed = sum(1 for m in mappings if m.get("status") == "confirmed")
    print(
        f"mapping: 매핑 {confirmed}건 → 노드 {len(graph.nodes)}개, "
        f"엣지 {len(graph.edges)}개 → {output}"
    )
    return graph


def write_candidates(
    output: Path, core_graph: Graph, vendor_graphs: dict[str, Graph]
) -> list[dict]:
    """후보 매핑을 검수용 JSON 파일로 저장한다."""
    candidates = suggest(core_graph, vendor_graphs)
    payload = {
        "description": "이름 유사도 기반 매핑 후보. 검수 후 status를 confirmed로 바꿔 "
        "`graphkb build --source mapping --mapping-file <이 파일>` 로 반영하세요.",
        "mappings": candidates,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"mapping 후보 {len(candidates)}건 → {output}")
    return candidates
