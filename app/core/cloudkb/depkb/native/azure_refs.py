"""Extract provider-native ARM resource-reference candidates from Swagger PUTs.

The request body schema is a traversal root, not itself a dependency.  This
module follows local and cached cross-file ``$ref`` values and reports only
reference-shaped properties encountered below that root.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_EXTERNAL_REF = re.compile(r"^(?:\./)?([^/]+\.json)(#/.*)$")


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _body_schema(operation: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for parameter in operation.get("parameters", []):
        if isinstance(parameter, Mapping) and parameter.get("in") == "body":
            schema = parameter.get("schema")
            if isinstance(schema, Mapping):
                return schema
    return None


def _resolve_ref(
    ref: str,
    current_file: str,
    documents: Mapping[str, Mapping[str, Any]],
    basenames: Mapping[str, str],
) -> tuple[str, str, Mapping[str, Any]] | None:
    file_key = current_file
    pointer = ref
    match = _EXTERNAL_REF.match(ref)
    if match:
        file_key = basenames.get(match.group(1), "")
        pointer = match.group(2)
    if not file_key or not pointer.startswith("#/") or file_key not in documents:
        return None
    node: Any = documents[file_key]
    for raw_token in pointer[2:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, Mapping) or token not in node:
            return None
        node = node[token]
    if not isinstance(node, Mapping):
        return None
    return file_key, pointer, node


def _arm_type(path: str) -> str | None:
    """Return ``Microsoft.X/type[/childType]`` from a discovered ARM path."""
    segments = [segment for segment in path.split("/") if segment]
    try:
        start = next(i for i, part in enumerate(segments) if part.lower() == "providers")
        namespace = segments[start + 1]
    except (StopIteration, IndexError):
        return None
    types = [segments[i] for i in range(start + 2, len(segments), 2)]
    return "/".join([namespace, *types]) if types else None


def extract_reference_candidates(
    documents: Mapping[str, Mapping[str, Any]],
    *,
    filenames: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Walk PUT request schemas and return reproducible native candidates.

    ``documents`` is keyed by cache key. ``filenames`` may map those keys to
    their original basename; absent that, ``<key>.json`` is assumed.
    """
    names = filenames or {key: f"{key}.json" for key in documents}
    basenames = {name.rsplit("/", 1)[-1]: key for key, name in names.items()}

    roots: dict[tuple[str, str], str] = {}
    arm_types: dict[str, str] = {}
    puts: list[tuple[str, str, Mapping[str, Any]]] = []
    for file_key, document in sorted(documents.items()):
        for path, path_item in sorted(document.get("paths", {}).items()):
            if not isinstance(path_item, Mapping):
                continue
            put = path_item.get("put") or path_item.get("PUT")
            if not isinstance(put, Mapping):
                continue
            native_id = f"ARM PUT {path}"
            puts.append((file_key, path, put))
            resource_type = _arm_type(path)
            if resource_type:
                arm_types[resource_type.lower()] = native_id
            schema = _body_schema(put)
            ref = schema.get("$ref") if schema else None
            if isinstance(ref, str):
                hit = _resolve_ref(ref, file_key, documents, basenames)
                if hit:
                    roots[(hit[0], hit[1])] = native_id

    candidates: list[dict[str, Any]] = []
    emitted: set[tuple[str, str, str]] = set()

    for file_key, path, put in puts:
        subject = f"ARM PUT {path}"
        schema = _body_schema(put)
        if schema is None:
            continue
        queue: list[tuple[str, Mapping[str, Any], str, str, frozenset[tuple[str, str]]]] = [
            (file_key, schema, "", f"{file_key}.json#/paths/{_pointer_token(path)}/put/parameters", frozenset())
        ]
        while queue:
            owner, node, trail, locator, ancestors = queue.pop()
            ref = node.get("$ref")
            if isinstance(ref, str):
                hit = _resolve_ref(ref, owner, documents, basenames)
                if hit is None:
                    if trail:
                        key = (subject, trail, locator)
                        if key not in emitted:
                            emitted.add(key)
                            candidates.append({
                                "subjectNativeId": subject,
                                "objectNativeId": None,
                                "referenceToken": trail,
                                "unresolvedTarget": ref,
                                "form": "typedSchemaReference",
                                "sourceLocator": locator,
                            })
                    continue
                coordinate = (hit[0], hit[1])
                target = roots.get(coordinate)
                if target is not None and trail and target != subject:
                    candidates.append({
                        "subjectNativeId": subject,
                        "objectNativeId": target,
                        "referenceToken": trail,
                        "targetSchemaRef": ref,
                        "form": "typedSchemaReference",
                        "sourceLocator": locator,
                    })
                    continue
                if coordinate not in ancestors:
                    queue.append((hit[0], hit[2], trail, f"{hit[0]}.json{hit[1]}", ancestors | {coordinate}))
                continue

            for index, part in enumerate(node.get("allOf", [])):
                if isinstance(part, Mapping):
                    queue.append((owner, part, trail, f"{locator}/allOf/{index}", ancestors))
            properties = node.get("properties", {})
            if isinstance(properties, Mapping):
                for name, prop in properties.items():
                    if not isinstance(prop, Mapping) or prop.get("readOnly") is True:
                        continue
                    prop_trail = f"{trail}.{name}" if trail else str(name)
                    prop_locator = f"{locator}/properties/{_pointer_token(str(name))}"
                    allowed = (prop.get("x-ms-arm-id-details") or {}).get("allowedResources", [])
                    allowed_types = [item.get("type") for item in allowed if isinstance(item, Mapping) and item.get("type")]
                    is_arm_id = prop.get("format") in {"arm-id", "resource-id"} or bool(allowed_types)
                    if is_arm_id:
                        targets = {arm_types[t.lower()] for t in allowed_types if t.lower() in arm_types}
                        target = next(iter(targets)) if len(targets) == 1 else None
                        candidate = {
                            "subjectNativeId": subject,
                            "objectNativeId": target,
                            "referenceToken": prop_trail,
                            "form": "schemaProperty",
                            "sourceLocator": prop_locator,
                        }
                        if target is None:
                            candidate["unresolvedTarget"] = allowed_types or str(prop.get("format"))
                        candidates.append(candidate)
                        continue
                    target_node = prop
                    target_locator = prop_locator
                    if prop.get("type") == "array" and isinstance(prop.get("items"), Mapping):
                        target_node = prop["items"]
                        prop_trail += "[]"
                        target_locator += "/items"
                    queue.append((owner, target_node, prop_trail, target_locator, ancestors))

    return sorted(candidates, key=lambda item: (item["subjectNativeId"], item["referenceToken"], item["sourceLocator"]))
