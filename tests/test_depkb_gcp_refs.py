from app.core.cloudkb.depkb.native.gcp_refs import extract_gcp_reference_candidates


def _document():
    return {
        "resources": {
            "instances": {
                "methods": {
                    "insert": {"request": {"$ref": "Instance"}},
                    "delete": {},
                }
            },
            "networks": {
                "methods": {"insert": {"request": {"$ref": "Network"}}, "delete": {}}
            },
            "disks": {
                "methods": {"insert": {"request": {"$ref": "Disk"}}, "delete": {}}
            },
        },
        "schemas": {
            "Instance": {
                "properties": {
                    "name": {"type": "string"},
                    "networkInterfaces": {
                        "type": "array",
                        "items": {"$ref": "NetworkInterface"},
                    },
                    "disks": {"type": "array", "items": {"$ref": "AttachedDisk"}},
                    "parent": {"$ref": "Instance"},
                }
            },
            "NetworkInterface": {
                "properties": {
                    "network": {
                        "type": "string",
                        "description": "URL of the Network resource.",
                    },
                    "generatedUrl": {
                        "type": "string",
                        "readOnly": True,
                        "description": "Output URL.",
                    },
                }
            },
            "AttachedDisk": {
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "URL to an existing Persistent Disk resource.",
                    }
                }
            },
            "Network": {"properties": {"name": {"type": "string"}}},
            "Disk": {"properties": {"name": {"type": "string"}}},
        },
    }


def test_recurses_request_shapes_and_resolves_only_discovered_collections():
    candidates = extract_gcp_reference_candidates(_document())

    assert candidates == [
        {
            "subjectNativeId": "compute.instances",
            "objectNativeId": "compute.disks",
            "referenceToken": "disks.source",
            "form": "schemaProperty",
            "sourceLocator": "gcp-compute#/schemas/AttachedDisk/properties/source",
        },
        {
            "subjectNativeId": "compute.instances",
            "objectNativeId": "compute.networks",
            "referenceToken": "networkInterfaces.network",
            "form": "schemaProperty",
            "sourceLocator": "gcp-compute#/schemas/NetworkInterface/properties/network",
        },
    ]


def test_request_schema_refs_and_cycles_are_not_dependencies():
    candidates = extract_gcp_reference_candidates(_document())

    assert all(candidate["objectNativeId"] != "Instance" for candidate in candidates)
    assert all(candidate["referenceToken"] != "parent" for candidate in candidates)


def test_preserves_an_unresolved_provider_reference_token():
    document = _document()
    document["schemas"]["Instance"]["properties"]["serviceAttachment"] = {
        "type": "string",
        "description": "URL of the Service Attachment resource.",
    }

    candidate = next(
        item
        for item in extract_gcp_reference_candidates(document)
        if item["referenceToken"] == "serviceAttachment"
    )
    assert candidate["objectNativeId"] is None
    assert candidate["unresolvedTarget"] == "serviceAttachment"


def test_deduplicates_the_same_property_seen_through_update_and_insert():
    document = _document()
    document["resources"]["instances"]["methods"]["update"] = {
        "request": {"$ref": "Instance"}
    }

    candidates = extract_gcp_reference_candidates(document)
    assert len([c for c in candidates if c["referenceToken"] == "disks.source"]) == 1
