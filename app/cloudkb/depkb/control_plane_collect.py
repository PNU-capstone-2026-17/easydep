"""세 CSP의 control-plane API 모델에서 Native v2 원시 관측을 수집한다."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from .native_v2 import OBSERVATION_SCHEMA, validate_inventory

OBSERVABLE_FIELDS: dict[str, Any] = {
    "identityFields": [],
    "crudOperations": [],
    "parentPath": None,
    "independentlyReadable": None,
    "survivesParentUpdate": None,
    "detachable": None,
    "independentlyDeletable": None,
    "lifecycleOwner": None,
    "embeddedIn": None,
    "providerCreated": None,
    "connectionManager": None,
}


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _source(identity: str, version: str, documents: Any) -> dict[str, str]:
    return {"identity": identity, "version": version, "sha256": _digest(documents)}


def _observation(
    provider: str, native_id: str, source: dict[str, str], locator: str,
    service_family: str, channel: str, **facts: Any,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "nativeId": native_id,
        "sourceIdentity": source["identity"],
        "sourceVersion": source["version"],
        "sourceLocator": locator,
        "serviceFamily": service_family,
        "observationChannel": channel,
        **OBSERVABLE_FIELDS,
        **facts,
    }


def _anchor_index(anchors: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    output = {}
    for anchor in anchors:
        output[(anchor["serviceFamily"], anchor["operation"])] = anchor
    return output


def _require_all_anchors(
    expected: dict[tuple[str, str], dict[str, Any]], resolved: set[tuple[str, str]],
) -> None:
    missing = sorted(set(expected) - resolved)
    if missing:
        raise ValueError(f"decision anchors absent from pinned source: {missing}")


def collect_aws(
    models: dict[str, dict[str, Any]], anchors: list[dict[str, Any]], *, version: str,
) -> dict[str, Any]:
    """Botocore service models; every operation is population, anchor shapes are traversed."""
    source = _source("AWS Botocore service models", version, models)
    anchor_map = _anchor_index(anchors)
    observations: dict[str, dict[str, Any]] = {}
    decision_anchors: list[dict[str, Any]] = []
    resolved: set[tuple[str, str]] = set()
    for service, model in sorted(models.items()):
        operations = model.get("operations") or {}
        shapes = model.get("shapes") or {}
        for operation_name, operation in sorted(operations.items()):
            native_id = f"{service}.operation.{operation_name}"
            locator = f"{service}#/operations/{operation_name}"
            observations[native_id] = _observation(
                "aws", native_id, source, locator, service, "operation",
                crudOperations=[operation_name],
                inputShape=(operation.get("input") or {}).get("shape"),
                outputShape=(operation.get("output") or {}).get("shape"),
            )
            anchor = anchor_map.get((service, operation_name))
            if not anchor:
                continue
            resolved.add((service, operation_name))
            observations[native_id]["anchorCapabilityIds"] = [anchor["capabilityId"]]
            decision_anchors.append({
                "capabilityId": anchor["capabilityId"],
                "sourceLocator": locator,
                "requirementIds": anchor["requirementIds"],
                "evidenceSpans": anchor["evidenceSpans"],
            })
            pending = [
                value.get("shape") for value in (operation.get("input"), operation.get("output"))
                if isinstance(value, dict) and value.get("shape")
            ]
            seen: set[str] = set()
            while pending:
                shape_name = pending.pop()
                if shape_name in seen or shape_name not in shapes:
                    continue
                seen.add(shape_name)
                shape = shapes[shape_name]
                members = shape.get("members") or {}
                references = sorted({
                    ref for member in members.values()
                    for ref in (
                        member.get("shape"),
                        (member.get("list") or {}).get("shape"),
                        (member.get("map") or {}).get("value", {}).get("shape"),
                    )
                    if ref
                })
                pending.extend(references)
                shape_id = f"{service}.shape.{shape_name}"
                shaped = _observation(
                    "aws", shape_id, source, f"{service}#/shapes/{shape_name}",
                    service, "schema", identityFields=sorted(members),
                    schemaType=shape.get("type"), referencedNativeIds=[
                        f"{service}.shape.{ref}" for ref in references
                    ],
                    anchorCapabilityIds=[anchor["capabilityId"]],
                )
                if shape_id in observations:
                    shaped["anchorCapabilityIds"] = sorted(set(
                        observations[shape_id].get("anchorCapabilityIds", [])
                        + shaped["anchorCapabilityIds"]
                    ))
                observations[shape_id] = shaped
    _require_all_anchors(anchor_map, resolved)
    document = {
        "schemaVersion": OBSERVATION_SCHEMA,
        "provider": "aws",
        "source": source,
        "decisionAnchors": decision_anchors,
        "observations": [observations[key] for key in sorted(observations)],
    }
    validate_inventory(document)
    return document


def _gcp_methods(document: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    output: dict[tuple[str, str], dict[str, Any]] = {}

    def visit(resources: dict[str, Any], prefix: str = "") -> None:
        for family, resource in resources.items():
            path = f"{prefix}.{family}".strip(".")
            for method_name, method in (resource.get("methods") or {}).items():
                output[(path, method_name)] = method
            visit(resource.get("resources") or {}, path)

    visit(document.get("resources") or {})
    return output


def collect_gcp(
    document: dict[str, Any], anchors: list[dict[str, Any]], *, version: str,
) -> dict[str, Any]:
    source = _source("Google Compute Discovery API", version, document)
    methods = _gcp_methods(document)
    anchor_map = _anchor_index(anchors)
    included_families = {family for family, _operation in anchor_map}
    schemas = document.get("schemas") or {}
    observations: dict[str, dict[str, Any]] = {}
    decision_anchors: list[dict[str, Any]] = []
    resolved: set[tuple[str, str]] = set()
    for (family, method_name), method in sorted(methods.items()):
        if family not in included_families:
            continue
        native_id = f"compute.operation.{family}.{method_name}"
        locator = f"compute#/resources/{family}/methods/{method_name}"
        observations[native_id] = _observation(
            "gcp", native_id, source, locator, family, "operation",
            crudOperations=[method_name], httpMethod=method.get("httpMethod"),
            requestSchema=(method.get("request") or {}).get("$ref"),
            responseSchema=(method.get("response") or {}).get("$ref"),
        )
        anchor = anchor_map.get((family, method_name))
        if not anchor:
            continue
        resolved.add((family, method_name))
        observations[native_id]["anchorCapabilityIds"] = [anchor["capabilityId"]]
        decision_anchors.append({
            "capabilityId": anchor["capabilityId"], "sourceLocator": locator,
            "requirementIds": anchor["requirementIds"],
            "evidenceSpans": anchor["evidenceSpans"],
        })
        pending = [
            value.get("$ref") for value in (method.get("request"), method.get("response"))
            if isinstance(value, dict) and value.get("$ref")
        ]
        seen: set[str] = set()
        while pending:
            schema_name = pending.pop()
            if schema_name in seen or schema_name not in schemas:
                continue
            seen.add(schema_name)
            schema = schemas[schema_name]
            refs = sorted({
                ref for prop in (schema.get("properties") or {}).values()
                for ref in (prop.get("$ref"), (prop.get("items") or {}).get("$ref")) if ref
            })
            pending.extend(refs)
            schema_id = f"compute.schema.{schema_name}"
            shaped = _observation(
                "gcp", schema_id, source, f"compute#/schemas/{schema_name}",
                family, "schema",
                identityFields=sorted((schema.get("properties") or {}).keys()),
                referencedNativeIds=[f"compute.schema.{ref}" for ref in refs],
                anchorCapabilityIds=[anchor["capabilityId"]],
            )
            if schema_id in observations:
                shaped["anchorCapabilityIds"] = sorted(set(
                    observations[schema_id].get("anchorCapabilityIds", [])
                    + shaped["anchorCapabilityIds"]
                ))
            observations[schema_id] = shaped
    _require_all_anchors(anchor_map, resolved)
    result = {
        "schemaVersion": OBSERVATION_SCHEMA, "provider": "gcp", "source": source,
        "decisionAnchors": decision_anchors,
        "observations": [observations[key] for key in sorted(observations)],
    }
    validate_inventory(result)
    return result


def collect_azure(
    documents: dict[str, dict[str, Any]], anchors: list[dict[str, Any]], *, version: str,
) -> dict[str, Any]:
    """Azure REST/OpenAPI operationId population with local-schema anchor traversal."""
    source = _source("Azure REST API specifications", version, documents)
    anchor_map = _anchor_index(anchors)
    observations: dict[str, dict[str, Any]] = {}
    decision_anchors: list[dict[str, Any]] = []
    resolved: set[tuple[str, str]] = set()
    for family, document in sorted(documents.items()):
        definitions = document.get("definitions") or document.get("components", {}).get("schemas", {})
        for path, path_item in sorted((document.get("paths") or {}).items()):
            for method, operation in sorted(path_item.items()):
                if method.lower() not in {"get", "put", "post", "patch", "delete"}:
                    continue
                operation_id = operation.get("operationId")
                if not operation_id:
                    continue
                native_id = f"{family}.operation.{operation_id}"
                locator = f"{family}#/paths/{path}/{method}"
                observations[native_id] = _observation(
                    "azure", native_id, source, locator, family, "path",
                    crudOperations=[method.upper()], pathTemplate=path,
                )
                anchor = anchor_map.get((family, operation_id))
                if not anchor:
                    continue
                resolved.add((family, operation_id))
                observations[native_id]["anchorCapabilityIds"] = [anchor["capabilityId"]]
                decision_anchors.append({
                    "capabilityId": anchor["capabilityId"], "sourceLocator": locator,
                    "requirementIds": anchor["requirementIds"],
                    "evidenceSpans": anchor["evidenceSpans"],
                })
                refs = []
                for parameter in operation.get("parameters") or []:
                    ref = (parameter.get("schema") or {}).get("$ref")
                    if ref and ref.startswith("#/definitions/"):
                        refs.append(ref.rsplit("/", 1)[-1])
                pending, seen = refs, set()
                while pending:
                    schema_name = pending.pop()
                    if schema_name in seen or schema_name not in definitions:
                        continue
                    seen.add(schema_name)
                    schema = definitions[schema_name]
                    nested = sorted({
                        ref.rsplit("/", 1)[-1]
                        for prop in (schema.get("properties") or {}).values()
                        for ref in (prop.get("$ref"), (prop.get("items") or {}).get("$ref"))
                        if ref and ref.startswith("#/definitions/")
                    })
                    pending.extend(nested)
                    schema_id = f"{family}.schema.{schema_name}"
                    shaped = _observation(
                        "azure", schema_id, source,
                        f"{family}#/definitions/{schema_name}", family, "schema",
                        identityFields=sorted((schema.get("properties") or {}).keys()),
                        referencedNativeIds=[f"{family}.schema.{ref}" for ref in nested],
                        anchorCapabilityIds=[anchor["capabilityId"]],
                    )
                    if schema_id in observations:
                        shaped["anchorCapabilityIds"] = sorted(set(
                            observations[schema_id].get("anchorCapabilityIds", [])
                            + shaped["anchorCapabilityIds"]
                        ))
                    observations[schema_id] = shaped
    _require_all_anchors(anchor_map, resolved)
    result = {
        "schemaVersion": OBSERVATION_SCHEMA, "provider": "azure", "source": source,
        "decisionAnchors": decision_anchors,
        "observations": [observations[key] for key in sorted(observations)],
    }
    validate_inventory(result)
    return result
