from __future__ import annotations

import copy

import pytest

from app.core.cloudkb.depkb.neutral_candidates.crosswalk import validate_crosswalk


def _packet(model: str, source_id: str, candidate_id: str) -> dict:
    return {
        "schemaVersion": "easydep-neutral-hypotheses/v1",
        "model": model,
        "sourceId": source_id,
        "candidates": [
            {
                "id": candidate_id,
                "sourceTerm": "Term",
                "definition": "Source definition.",
                "sourceLocator": "source#/term",
                "scopeRationale": "VM-connected IaaS scope.",
                "relations": [],
            }
        ],
    }


def _packets() -> dict:
    return {
        "cloud-barista": _packet(
            "cloud-barista", "cloud-barista.cb-tumblebug.c2c4e76", "cb.term"
        ),
        "tosca": _packet("tosca", "oasis.tosca.2.0.csd07", "tosca.term"),
        "occi": _packet("occi", "ogf.occi.infrastructure.gfd224", "occi.term"),
    }


def _crosswalk() -> dict:
    return {
        "schemaVersion": "easydep-neutral-crosswalk/v1",
        "concepts": [
            {
                "id": "candidate-concept",
                "definition": "A definition synthesized only from source models.",
                "derivation": "Definitions and lifecycle boundaries were compared.",
                "unresolvedDifferences": "Provider realization has not been examined.",
                "sourceMembers": [
                    {
                        "model": model,
                        "candidateId": packet["candidates"][0]["id"],
                        "kind": "partial",
                        "rationale": "The source preserves part of the definition.",
                    }
                    for model, packet in _packets().items()
                ],
            }
        ],
        "excludedSourceCandidates": [],
        "sourceRelationCoverage": [],
    }


def test_crosswalk_classifies_every_source_candidate_without_provider_projection():
    validate_crosswalk(_crosswalk(), _packets())


def test_crosswalk_rejects_unclassified_source_candidate_and_provider_projection():
    document = _crosswalk()
    document["concepts"][0]["sourceMembers"].pop()
    with pytest.raises(ValueError, match="every neutral source candidate"):
        validate_crosswalk(document, _packets())

    contaminated = copy.deepcopy(_crosswalk())
    contaminated["concepts"][0]["aws"] = "resource"
    with pytest.raises(ValueError, match="provider projections"):
        validate_crosswalk(contaminated, _packets())
