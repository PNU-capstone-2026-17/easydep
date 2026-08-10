from __future__ import annotations

import json

import pytest

from app.core.cloudkb.depkb.native import study


def _inventory(provider: str) -> dict:
    return {
        "schemaVersion": "easydep-native-discovery/v1",
        "provider": provider,
        "source": {"identity": "test", "version": "1"},
        "elements": [
            {
                "nativeId": "native.a",
                "nativeForm": "standaloneResource",
                "sourceLocator": "source#/a",
            }
        ],
        "candidates": [],
    }


def test_prepare_reviews_refuses_to_overwrite_existing_review(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "HERE", tmp_path)
    for provider in ("aws", "azure", "gcp"):
        (tmp_path / f"{provider}-inventory.json").write_text(
            json.dumps(_inventory(provider)), encoding="utf-8"
        )
    (tmp_path / "aws-review-a.json").write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError, match="discard"):
        study.prepare_reviews(overwrite=False)


def test_study_status_is_false_until_every_review_is_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "HERE", tmp_path)
    for provider in ("aws", "azure", "gcp"):
        inventory = _inventory(provider)
        (tmp_path / f"{provider}-inventory.json").write_text(
            json.dumps(inventory), encoding="utf-8"
        )

    assert study.status() is False
