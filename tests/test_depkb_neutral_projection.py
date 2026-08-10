from __future__ import annotations

import pytest

from app.core.cloudkb.depkb.neutral_candidates.projection import (
    validate_provider_projection,
)


def test_projection_does_not_treat_schema_as_measured_runtime_necessity():
    crosswalk = {
        "concepts": [
            {"id": "neutral.resource"},
            {"id": "neutral.tosca-structure"},
        ]
    }
    protocol = {
        "schemaVersion": "easydep-neutral-projection-protocol/v1",
        "resourceConceptIds": ["neutral.resource"],
        "structuralConceptIds": ["neutral.tosca-structure"],
    }
    inventory = {
        "provider": "aws",
        "source": {"identity": "test", "version": "1"},
        "elements": [{"nativeId": "aws.native"}],
    }
    packet = {
        "schemaVersion": "easydep-provider-hypotheses/v1",
        "provider": "aws",
        "inventorySource": inventory["source"],
        "mappings": [
            {
                "conceptId": "neutral.resource",
                "kind": "equivalent",
                "nativeIds": ["aws.native"],
                "preservedMeaning": "The native resource preserves the hypothesis.",
                "evidence": [
                    {
                        "strength": "schemaCandidate",
                        "sourceLocator": "source#/native",
                        "supports": "The native schema exposes the resource.",
                    }
                ],
                "runtimeNecessityConfirmed": True,
            }
        ],
    }

    with pytest.raises(ValueError, match="measured evidence"):
        validate_provider_projection(
            packet,
            protocol=protocol,
            crosswalk=crosswalk,
            inventory=inventory,
        )
