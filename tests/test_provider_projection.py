from __future__ import annotations

import json
from pathlib import Path

from app.core.cloudkb.depkb.provider_realizations import realization_gaps, validate_realizations

MODEL = Path("app/core/cloudkb/depkb/provider-realizations.json")


def _load():
    return json.loads(MODEL.read_text(encoding="utf-8"))


def test_provider_realization_preserves_embedded_native_components():
    model = _load()
    validate_realizations(model)
    azure = model["providers"]["azure"]["components"]
    assert {item.get("ownerResourceId") for item in azure if item["representation"] == "embedded"} == {"gateway"}
    assert [
        item["id"] for item in model["providers"]["azure"]["realizations"]
    ] == ["http-application-gateway", "https-application-gateway"]


def test_provider_realization_can_use_multiple_native_resources():
    model = _load()
    gcp = model["providers"]["gcp"]["realizations"][0]
    assert gcp["composition"] == "multi-resource"
    assert len(gcp["componentIds"]) == 6


def test_realization_catalog_exposes_missing_boundaries_instead_of_hiding_them():
    gaps = realization_gaps(_load())
    assert gaps == []
