from __future__ import annotations

import json
from pathlib import Path

from app.core.cloudkb.depkb.neutral_evidence import validate_neutral_evidence


def test_neutral_models_have_primary_rationale_mapping_and_limits():
    path = Path("evaluation/research_protocol/definitions/neutral-model-evidence.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    validate_neutral_evidence(document)
    assert {item["id"] for item in document["models"]} == {
        "cloud-barista", "tosca-2.0", "occi-1.2-infrastructure",
        "camel", "crossplane-composition", "terraform-provider-model",
    }
