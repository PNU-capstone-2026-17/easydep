from __future__ import annotations

import json
from pathlib import Path

from app.core.cloudkb.depkb.projection_model import projection_gaps, validate_projection

MODEL = Path("app/core/cloudkb/depkb/provider-projections.json")


def _load():
    return json.loads(MODEL.read_text(encoding="utf-8"))


def test_projection_supports_one_native_resource_mapping_to_multiple_concepts():
    model = _load()
    validate_projection(model)
    azure = model["providers"]["azure"]["mappings"]
    assert len({item["neutralConceptId"] for item in azure}) > 1
    assert {item.get("ownerResourceId") for item in azure if item["representation"] == "embedded"} == {"gateway"}
    assert [
        item["id"] for item in model["providers"]["azure"]["realizations"]
    ] == ["http-application-gateway", "https-application-gateway"]


def test_projection_supports_one_neutral_realization_using_multiple_resources():
    model = _load()
    gcp = model["providers"]["gcp"]["realizations"][0]
    assert gcp["composition"] == "multi-resource"
    assert len(gcp["mappingIds"]) == 6


def test_projection_exposes_missing_boundaries_instead_of_hiding_them():
    gaps = projection_gaps(_load())
    assert gaps == []
