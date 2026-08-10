"""Query a reviewed neutral alignment backed by frozen provider-native graphs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .alignment import validate_alignment
from .native.freeze import validate_frozen_graph


@dataclass(frozen=True)
class Realization:
    schemaVersion: str
    provider: str
    region: str
    requestedConcepts: tuple[str, ...]
    mappings: tuple[dict[str, Any], ...]
    nativeNodes: tuple[dict[str, Any], ...]
    nativeEdges: tuple[dict[str, Any], ...]
    provenance: dict[str, Any]


class DerivedCatalog:
    """Read-only view; native evidence remains authoritative."""

    def __init__(self, alignment: dict[str, Any], graphs: dict[str, dict[str, Any]]) -> None:
        validate_alignment(alignment)
        if set(graphs) != {"aws", "azure", "gcp"}:
            raise ValueError("derived catalog requires all three native graphs")
        for provider, graph in graphs.items():
            validate_frozen_graph(graph)
            if graph.get("provider") != provider:
                raise ValueError(f"native graph provider mismatch: {provider}")
            expected = alignment["nativeGraphs"][provider]["sha256"]
            if graph["freeze"]["sha256"] != expected:
                raise ValueError(f"alignment references a different {provider} graph")

        native_ids = {
            provider: {node["nativeId"] for node in graph["nodes"]}
            for provider, graph in graphs.items()
        }
        for concept in alignment["concepts"]:
            for mapping in concept["providerMappings"]:
                provider = mapping["provider"]
                if mapping["kind"] == "unmatched":
                    continue
                missing = set(mapping["nativeIds"]) - native_ids[provider]
                if missing:
                    raise ValueError(
                        f"alignment concept {concept['id']} references unknown {provider} "
                        f"native ids: {sorted(missing)}"
                    )
        mapped_ids = {
            provider: {
                native_id
                for concept in alignment["concepts"]
                for mapping in concept["providerMappings"]
                if mapping["provider"] == provider and mapping["kind"] != "unmatched"
                for native_id in mapping["nativeIds"]
            }
            for provider in graphs
        }
        for provider in graphs:
            uncovered = native_ids[provider] - mapped_ids[provider]
            if uncovered:
                raise ValueError(
                    f"alignment leaves {provider} native nodes uncovered: "
                    f"{sorted(uncovered)}"
                )
        self._alignment = alignment
        self._graphs = graphs
        self._concepts = {item["id"]: item for item in alignment["concepts"]}

    def structural_corpus(self) -> tuple[dict[str, Any], ...]:
        """Generate P1-P3-independent checks over every frozen node and edge."""
        owners: dict[tuple[str, str], set[str]] = {}
        for concept in self._alignment["concepts"]:
            for mapping in concept["providerMappings"]:
                for native_id in mapping["nativeIds"]:
                    owners.setdefault((mapping["provider"], native_id), set()).add(
                        concept["id"]
                    )

        checks: list[dict[str, Any]] = []
        for provider, graph in sorted(self._graphs.items()):
            for node in graph["nodes"]:
                checks.append(
                    {
                        "kind": "nativeNodeCoverage",
                        "provider": provider,
                        "nativeId": node["nativeId"],
                        "conceptIds": sorted(owners[(provider, node["nativeId"])]),
                        "p1P2P3Derived": False,
                    }
                )
            for edge in graph["edges"]:
                checks.append(
                    {
                        "kind": "nativeEdgePreservation",
                        "provider": provider,
                        "subjectNativeId": edge["subjectNativeId"],
                        "objectNativeId": edge["objectNativeId"],
                        "subjectConceptIds": sorted(
                            owners[(provider, edge["subjectNativeId"])]
                        ),
                        "objectConceptIds": sorted(
                            owners[(provider, edge["objectNativeId"])]
                        ),
                        "relationKind": edge["relationKind"],
                        "p1P2P3Derived": False,
                    }
                )
        return tuple(checks)

    @classmethod
    def load(cls, alignment_path: Path, graph_paths: dict[str, Path]) -> DerivedCatalog:
        alignment = json.loads(alignment_path.read_text(encoding="utf-8"))
        graphs = {
            provider: json.loads(path.read_text(encoding="utf-8"))
            for provider, path in graph_paths.items()
        }
        return cls(alignment, graphs)

    def realize(self, concept_ids: list[str], provider: str, region: str) -> Realization:
        if provider not in self._graphs:
            raise KeyError(f"provider has no frozen native graph: {provider}")
        if not region.strip():
            raise ValueError("region is required for provider realization")
        unknown = sorted(set(concept_ids) - set(self._concepts))
        if unknown:
            raise KeyError(f"unknown derived concepts: {unknown}")

        mappings: list[dict[str, Any]] = []
        selected_native_ids: set[str] = set()
        for concept_id in concept_ids:
            concept = self._concepts[concept_id]
            provider_mapping = next(
                (
                    item
                    for item in concept["providerMappings"]
                    if item["provider"] == provider
                ),
                None,
            )
            if provider_mapping is None:
                mappings.append(
                    {
                        "conceptId": concept_id,
                        "status": "unmatched",
                        "nativeIds": [],
                    }
                )
                continue
            if provider_mapping["kind"] == "unmatched":
                mappings.append(
                    {
                        "conceptId": concept_id,
                        "status": "unmatched",
                        "nativeIds": [],
                        "preservedMeaning": provider_mapping["preservedMeaning"],
                        "lostOrDifferentMeaning": provider_mapping[
                            "lostOrDifferentMeaning"
                        ],
                    }
                )
                continue
            selected_native_ids.update(provider_mapping["nativeIds"])
            mappings.append(
                {
                    "conceptId": concept_id,
                    "status": provider_mapping["kind"],
                    "nativeIds": list(provider_mapping["nativeIds"]),
                    "preservedMeaning": provider_mapping["preservedMeaning"],
                    "lostOrDifferentMeaning": provider_mapping.get(
                        "lostOrDifferentMeaning", ""
                    ),
                }
            )

        graph = self._graphs[provider]
        nodes = tuple(
            node for node in graph["nodes"] if node["nativeId"] in selected_native_ids
        )
        edges = tuple(
            edge
            for edge in graph["edges"]
            if edge["subjectNativeId"] in selected_native_ids
            and edge["objectNativeId"] in selected_native_ids
        )
        return Realization(
            schemaVersion="easydep-provider-realization/v1",
            provider=provider,
            region=region,
            requestedConcepts=tuple(concept_ids),
            mappings=tuple(mappings),
            nativeNodes=nodes,
            nativeEdges=edges,
            provenance={
                "alignment": self._alignment["freeze"]["sha256"],
                "nativeGraph": graph["freeze"]["sha256"],
                "nativeGraphAuthoritative": True,
            },
        )
