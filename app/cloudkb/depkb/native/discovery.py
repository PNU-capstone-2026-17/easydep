"""Extract provider-native source packets without a neutral seed vocabulary.

The output is intentionally broad.  Inclusion in the Docker-on-VM study is a
separate reviewed decision; extraction must not silently encode that decision.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.cloudkb.depkb.fetch_azure import CACHE as AZURE_CACHE
from app.cloudkb.depkb.fetch_azure import COMMIT as AZURE_COMMIT
from app.cloudkb.depkb.fetch_azure import FILES as AZURE_FILES
from app.cloudkb.depkb.fetch_vendors import SOURCES, load

from .azure_refs import extract_reference_candidates as extract_azure_references
from .gcp_refs import extract_gcp_reference_candidates
from .model import validate_inventory

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / "discovery-protocol.json"


def _source(provider: str) -> dict[str, str]:
    if provider == "aws":
        pin = SOURCES["aws-cfn"]
        return {
            "identity": "AWS CloudFormation Resource Specification",
            "version": str(pin["version"]),
            "sha256": str(pin["sha256"]),
        }
    if provider == "gcp":
        pin = SOURCES["gcp-compute"]
        return {
            "identity": "Google Compute API Discovery Document",
            "version": str(pin["version"]),
            "sha256": str(pin["sha256"]),
        }
    return {
        "identity": "Azure REST API Specifications",
        "version": AZURE_COMMIT,
    }


def discover_aws() -> dict[str, Any]:
    spec = load("aws-cfn")
    prefixes = tuple(
        json.loads(PROTOCOL.read_text(encoding="utf-8"))["providers"]["aws"][
            "resourceTypePrefixes"
        ]
    )
    resource_types = {
        key: value
        for key, value in spec["ResourceTypes"].items()
        if key.startswith(prefixes)
    }
    elements = [
        {
            "nativeId": resource_type,
            "nativeForm": "standaloneResource",
            "sourceLocator": f"aws-cfn#/ResourceTypes/{resource_type}",
        }
        for resource_type in sorted(resource_types)
    ]
    candidates: list[dict[str, Any]] = []
    reference_name = re.compile(r"(?:Arn|Id|Ids|Name|Names|Groups|Subnets|Interfaces|Volumes)$")
    for resource_type, definition in sorted(resource_types.items()):
        for property_name, property_spec in sorted(definition.get("Properties", {}).items()):
            if not reference_name.search(property_name):
                continue
            candidates.append(
                {
                    "subjectNativeId": resource_type,
                    "objectNativeId": None,
                    "referenceToken": property_name,
                    "form": "schemaProperty",
                    "requiredInSchema": bool(property_spec.get("Required")),
                    "sourceLocator": (
                        f"aws-cfn#/ResourceTypes/{resource_type}/Properties/{property_name}"
                    ),
                }
            )
    result = {
        "schemaVersion": "easydep-native-discovery/v1",
        "provider": "aws",
        "source": _source("aws"),
        "elements": elements,
        "candidates": candidates,
    }
    validate_inventory(result)
    return result


def discover_azure() -> dict[str, Any]:
    elements: dict[str, dict[str, Any]] = {}
    documents: dict[str, dict[str, Any]] = {}
    for file_key in sorted(AZURE_FILES):
        document = json.loads(
            (AZURE_CACHE / f"{file_key}.json").read_text(encoding="utf-8")
        )
        documents[file_key] = document
        for path, path_item in sorted(document.get("paths", {}).items()):
            put = path_item.get("put") or path_item.get("PUT")
            if not isinstance(put, dict):
                continue
            native_id = f"ARM PUT {path}"
            elements[native_id] = {
                "nativeId": native_id,
                "nativeForm": "childResource" if path.count("/{") > 2 else "standaloneResource",
                "sourceLocator": f"{file_key}.json#/paths/{path}",
            }
    candidates = extract_azure_references(documents, filenames=AZURE_FILES)
    result = {
        "schemaVersion": "easydep-native-discovery/v1",
        "provider": "azure",
        "source": _source("azure"),
        "elements": [elements[key] for key in sorted(elements)],
        "candidates": candidates,
    }
    validate_inventory(result)
    return result


def _walk_gcp_resources(
    resources: dict[str, Any], prefix: str = ""
) -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    for name, resource in sorted(resources.items()):
        path = f"{prefix}.{name}" if prefix else name
        found.append((path, resource))
        found.extend(_walk_gcp_resources(resource.get("resources", {}), path))
    return found


def discover_gcp() -> dict[str, Any]:
    document = load("gcp-compute")
    elements: dict[str, dict[str, Any]] = {}
    for collection, resource in _walk_gcp_resources(document.get("resources", {})):
        operations = sorted(
            method_name
            for method_name in resource.get("methods", {})
            if method_name in {"insert", "delete", "update", "patch"}
        )
        if not operations:
            continue
        native_id = f"compute.{collection}"
        elements[native_id] = {
            "nativeId": native_id,
            "nativeForm": "standaloneResource",
            "operations": operations,
            "sourceLocator": (
                f"gcp-compute#/resources/{collection.replace('.', '/resources/')}"
            ),
        }
    candidates = extract_gcp_reference_candidates(document)
    result = {
        "schemaVersion": "easydep-native-discovery/v1",
        "provider": "gcp",
        "source": _source("gcp"),
        "elements": [elements[key] for key in sorted(elements)],
        "candidates": candidates,
    }
    validate_inventory(result)
    return result


def discover_all() -> dict[str, dict[str, Any]]:
    return {
        "aws": discover_aws(),
        "azure": discover_azure(),
        "gcp": discover_gcp(),
    }


def main() -> None:
    for provider, inventory in discover_all().items():
        target = HERE / f"{provider}-inventory.json"
        target.write_text(
            json.dumps(inventory, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(provider, "elements=", len(inventory["elements"]), "candidates=", len(inventory["candidates"]))


if __name__ == "__main__":
    main()
