"""Extract provider-native references from a Compute discovery document.

Discovery ``$ref`` values describe the shape of a request body.  They are not
resource dependencies by themselves.  This module follows those refs and
emits candidates only for leaf properties that look like resource references.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any

_MUTATIONS = frozenset({"insert", "patch", "update"})
_REFERENCE_WORD = re.compile(
    r"\b(?:url|uri)\s+(?:of|to)\b|\b(?:a\s+)?reference\s+to\b|/projects/",
    re.IGNORECASE,
)
_WORDS = re.compile(r"[A-Za-z][A-Za-z0-9]*")
_OUTPUT_METADATA = frozenset({"creationtimestamp", "id", "kind", "name", "selflink"})


def _walk_resources(
    resources: Mapping[str, Any], prefix: str = ""
) -> Iterator[tuple[str, Mapping[str, Any]]]:
    for name, resource in sorted(resources.items()):
        if not isinstance(resource, Mapping):
            continue
        collection = f"{prefix}.{name}" if prefix else name
        yield collection, resource
        nested = resource.get("resources")
        if isinstance(nested, Mapping):
            yield from _walk_resources(nested, collection)


def _singular(value: str) -> str:
    lower = value.lower()
    if lower.endswith("ies"):
        return lower[:-3] + "y"
    if lower.endswith(("sses", "shes", "ches", "xes", "zes")):
        return lower[:-2]
    if lower.endswith("s") and not lower.endswith("ss"):
        return lower[:-1]
    return lower


def _aliases(collection: str, request_refs: set[str]) -> set[str]:
    leaf = collection.rsplit(".", 1)[-1]
    aliases = {leaf.lower(), _singular(leaf)}
    for request_ref in request_refs:
        aliases.add(request_ref.lower())
    return {alias for alias in aliases if len(alias) > 2}


def _collection_index(
    resources: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, set[str]]]:
    collections: dict[str, Mapping[str, Any]] = {}
    request_refs: dict[str, set[str]] = {}
    for collection, resource in _walk_resources(resources):
        methods = resource.get("methods")
        if not isinstance(methods, Mapping) or not any(
            name in methods for name in _MUTATIONS | {"delete"}
        ):
            continue
        collections[collection] = resource
        refs: set[str] = set()
        for method_name, method in methods.items():
            if method_name not in _MUTATIONS or not isinstance(method, Mapping):
                continue
            request = method.get("request")
            if isinstance(request, Mapping) and isinstance(request.get("$ref"), str):
                refs.add(request["$ref"])
        request_refs[collection] = refs
    return collections, {
        collection: _aliases(collection, request_refs[collection])
        for collection in collections
    }


def _resolve_target(
    property_name: str, description: str, aliases: Mapping[str, set[str]]
) -> tuple[str | None, str]:
    property_token = property_name.lower()
    text_words = {word.lower() for word in _WORDS.findall(description)}
    matches: list[tuple[int, str, str]] = []
    for collection, names in aliases.items():
        for alias in names:
            score = 0
            if property_token in {alias, f"{alias}url", f"{alias}uri", f"{alias}id"}:
                score = 3
            elif alias in text_words:
                score = 2
            elif f"/{alias}/" in description.lower():
                score = 4
            if score:
                matches.append((score, collection, alias))
    if matches:
        best_score = max(score for score, _, _ in matches)
        best = {(collection, alias) for score, collection, alias in matches if score == best_score}
        collections = {collection for collection, _ in best}
        if len(collections) == 1:
            collection = next(iter(collections))
            alias = min(alias for target, alias in best if target == collection)
            return f"compute.{collection}", alias
    return None, property_name


def _looks_like_reference(name: str, description: str) -> bool:
    lowered = name.lower()
    if lowered in _OUTPUT_METADATA:
        return False
    return bool(
        _REFERENCE_WORD.search(description)
        or lowered.endswith(("url", "uri", "reference"))
    )


def _walk_schema_leaves(
    schema_name: str,
    schemas: Mapping[str, Any],
    path: tuple[str, ...] = (),
    active: frozenset[str] = frozenset(),
) -> Iterator[tuple[tuple[str, ...], str, Mapping[str, Any]]]:
    """Yield request leaf properties while cutting only the active ref cycle."""
    if schema_name in active:
        return
    schema = schemas.get(schema_name)
    if not isinstance(schema, Mapping):
        return
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return
    next_active = active | {schema_name}
    for name, prop in sorted(properties.items()):
        if not isinstance(prop, Mapping) or prop.get("readOnly") is True:
            continue
        prop_path = (*path, name)
        nested_ref = prop.get("$ref")
        items = prop.get("items")
        if not isinstance(nested_ref, str) and isinstance(items, Mapping):
            nested_ref = items.get("$ref")
        if isinstance(nested_ref, str):
            yield from _walk_schema_leaves(nested_ref, schemas, prop_path, next_active)
        else:
            yield prop_path, schema_name, prop


def extract_gcp_reference_candidates(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return reference candidates from mutable Compute request schemas.

    ``objectNativeId`` is populated only when the target maps unambiguously to
    another discovered collection.  Otherwise ``unresolvedTarget`` retains the
    provider-native property token for later review.
    """
    resources = document.get("resources")
    schemas = document.get("schemas")
    if not isinstance(resources, Mapping) or not isinstance(schemas, Mapping):
        return []
    collections, aliases = _collection_index(resources)
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for collection, resource in collections.items():
        methods = resource.get("methods", {})
        for method_name, method in sorted(methods.items()):
            if method_name not in _MUTATIONS or not isinstance(method, Mapping):
                continue
            request = method.get("request")
            request_ref = request.get("$ref") if isinstance(request, Mapping) else None
            if not isinstance(request_ref, str):
                continue
            for path, owner_schema, prop in _walk_schema_leaves(request_ref, schemas):
                description = str(prop.get("description") or "")
                if not _looks_like_reference(path[-1], description):
                    continue
                object_id, token = _resolve_target(path[-1], description, aliases)
                if object_id == f"compute.{collection}":
                    continue
                property_path = ".".join(path)
                key = (collection, property_path, object_id or token)
                if key in seen:
                    continue
                seen.add(key)
                candidate: dict[str, Any] = {
                    "subjectNativeId": f"compute.{collection}",
                    "objectNativeId": object_id,
                    "referenceToken": property_path,
                    "form": "schemaProperty",
                    "sourceLocator": (
                        f"gcp-compute#/schemas/{owner_schema}/properties/{path[-1]}"
                    ),
                }
                if object_id is None:
                    candidate["unresolvedTarget"] = token
                candidates.append(candidate)
    return sorted(
        candidates,
        key=lambda item: (
            item["subjectNativeId"], item["referenceToken"], item.get("objectNativeId") or ""
        ),
    )
