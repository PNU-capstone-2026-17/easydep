from __future__ import annotations

import copy

import pytest

from app.core.cloudkb.depkb.neutral_candidates.model import validate_candidate_packet


def _packet() -> dict:
    return {
        "schemaVersion": "easydep-neutral-hypotheses/v1",
        "model": "cloud-barista",
        "sourceId": "cloud-barista.cb-tumblebug.c2c4e76",
        "candidates": [
            {
                "id": "cb.example",
                "sourceTerm": "Example",
                "definition": "A source-defined VM-connected IaaS concept.",
                "sourceLocator": "source#/Example",
                "scopeRationale": "Directly participates in VM deployment.",
                "relations": [
                    {
                        "predicate": "references",
                        "targetSourceTerm": "Other",
                        "sourceLocator": "source#/Example/other",
                    }
                ],
            }
        ],
    }


def test_neutral_hypothesis_packet_is_source_grounded_and_provider_free():
    validate_candidate_packet(_packet())


def test_provider_projection_is_forbidden_during_hypothesis_extraction():
    packet = copy.deepcopy(_packet())
    packet["candidates"][0]["aws"] = "AWS::Example"

    with pytest.raises(ValueError, match="premature provider projection"):
        validate_candidate_packet(packet)
