"""Pinned primary sources used only after native graph freeze."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PATH = Path(__file__).with_name("neutral-model-sources.json")
MODELS = frozenset({"cloud-barista", "tosca", "occi"})
ALLOWED_HOSTS = frozenset({"github.com", "docs.oasis-open.org", "ogf.org"})


def validate_source_registry(document: dict[str, Any]) -> None:
    if document.get("schemaVersion") != "easydep-neutral-model-sources/v1":
        raise ValueError("unsupported neutral model source registry")
    sources = document.get("sources")
    if not isinstance(sources, list):
        raise TypeError("neutral model sources must be an array")
    ids: set[str] = set()
    models: set[str] = set()
    for source in sources:
        source_id = str(source.get("id") or "")
        if not source_id or source_id in ids:
            raise ValueError(f"missing or duplicate neutral source id: {source_id!r}")
        ids.add(source_id)
        model = source.get("model")
        if model not in MODELS:
            raise ValueError(f"invalid neutral source model: {model!r}")
        models.add(model)
        if not source.get("publisher") or not source.get("version") or not source.get("use"):
            raise ValueError(f"neutral source lacks provenance: {source_id}")
        url = urlparse(str(source.get("url") or ""))
        if url.scheme != "https" or url.hostname not in ALLOWED_HOSTS:
            raise ValueError(f"neutral source is not an approved primary URL: {source_id}")
        if model == "cloud-barista" and "git:" not in str(source["version"]):
            raise ValueError("Cloud-Barista source must be pinned to a git revision")
    if models != MODELS:
        raise ValueError("registry must contain Cloud-Barista, TOSCA, and OCCI")


@lru_cache(maxsize=1)
def source_registry() -> dict[str, dict[str, Any]]:
    document = json.loads(PATH.read_text(encoding="utf-8"))
    validate_source_registry(document)
    return {source["id"]: source for source in document["sources"]}
