"""Validation for provider-native discovery artifacts."""

from __future__ import annotations

from collections import Counter
from typing import Any

PROVIDERS = frozenset({"aws", "azure", "gcp"})
NATIVE_FORMS = frozenset(
    {
        "standaloneResource",
        "childResource",
        "nestedConfiguration",
        "implicitProviderObject",
    }
)
CANDIDATE_FORMS = frozenset(
    {
        "schemaProperty",
        "typedSchemaReference",
        "requestSchema",
        "pathContainment",
    }
)


def validate_inventory(inventory: dict[str, Any]) -> None:
    """Reject normalized vocabulary and incomplete native source coordinates."""
    if inventory.get("schemaVersion") != "easydep-native-discovery/v1":
        raise ValueError("unsupported native discovery schemaVersion")
    provider = inventory.get("provider")
    if provider not in PROVIDERS:
        raise ValueError(f"unsupported provider: {provider!r}")
    source = inventory.get("source")
    if not isinstance(source, dict) or not source.get("version") or not source.get("identity"):
        raise ValueError("native discovery requires a pinned source identity and version")

    elements = inventory.get("elements")
    candidates = inventory.get("candidates")
    if not isinstance(elements, list) or not elements:
        raise ValueError("native discovery has no elements")
    if not isinstance(candidates, list):
        raise TypeError("native discovery candidates must be an array")

    ids: list[str] = []
    for element in elements:
        native_id = str(element.get("nativeId") or "")
        if not native_id or not element.get("sourceLocator"):
            raise ValueError("native element requires nativeId and sourceLocator")
        if element.get("nativeForm") not in NATIVE_FORMS:
            raise ValueError(f"invalid nativeForm for {native_id}")
        if "neutralId" in element or "capability" in element:
            raise ValueError(f"premature neutral classification on {native_id}")
        ids.append(native_id)
    duplicates = sorted(key for key, count in Counter(ids).items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate native elements: {duplicates[:5]}")

    for candidate in candidates:
        if candidate.get("form") not in CANDIDATE_FORMS:
            raise ValueError(f"invalid native candidate form: {candidate.get('form')!r}")
        if not candidate.get("subjectNativeId") or not candidate.get("sourceLocator"):
            raise ValueError("native candidate requires subjectNativeId and sourceLocator")
        if "neutralSubject" in candidate or "neutralObject" in candidate:
            raise ValueError("native discovery candidate contains a cross-provider projection")
