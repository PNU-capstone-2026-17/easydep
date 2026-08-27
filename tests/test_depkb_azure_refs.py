from app.cloudkb.depkb.native.azure_refs import extract_reference_candidates


def _documents():
    return {
        "network": {
            "paths": {
                "/subscriptions/{s}/providers/Microsoft.Network/virtualNetworks/{v}": {
                    "put": {"parameters": [{"in": "body", "schema": {"$ref": "#/definitions/Vnet"}}]}
                },
                "/subscriptions/{s}/providers/Microsoft.Network/networkInterfaces/{n}": {
                    "put": {"parameters": [{"in": "body", "schema": {"$ref": "#/definitions/Nic"}}]}
                },
            },
            "definitions": {
                "Vnet": {"type": "object", "properties": {"location": {"type": "string"}}},
                "Nic": {"allOf": [{"$ref": "common.json#/definitions/Tracked"}], "properties": {
                    "peer": {"$ref": "#/definitions/Vnet"},
                    "external": {"$ref": "elsewhere.json#/definitions/Thing"},
                    "routes": {"type": "array", "items": {"$ref": "#/definitions/Vnet"}},
                    "ownerId": {"type": "string", "format": "arm-id", "x-ms-arm-id-details": {
                        "allowedResources": [{"type": "Microsoft.Network/virtualNetworks"}]
                    }},
                }},
            },
        },
        "common": {"definitions": {"Tracked": {"properties": {
            "readOnlyLink": {"readOnly": True, "$ref": "network.json#/definitions/Vnet"},
            "label": {"type": "string"},
        }}}},
    }


def test_recursively_extracts_references_without_emitting_request_wrapper():
    found = extract_reference_candidates(_documents())
    tokens = {item["referenceToken"] for item in found}
    assert tokens == {"peer", "external", "routes[]", "ownerId"}
    assert all(item["referenceToken"] != "" for item in found)


def test_resolves_only_discovered_put_resources_and_preserves_unresolved_ref():
    found = extract_reference_candidates(_documents())
    by_token = {item["referenceToken"]: item for item in found}
    vnet = "ARM PUT /subscriptions/{s}/providers/Microsoft.Network/virtualNetworks/{v}"
    assert by_token["peer"]["objectNativeId"] == vnet
    assert by_token["routes[]"]["objectNativeId"] == vnet
    assert by_token["ownerId"]["objectNativeId"] == vnet
    assert by_token["external"]["objectNativeId"] is None
    assert by_token["external"]["unresolvedTarget"] == "elsewhere.json#/definitions/Thing"


def test_source_locator_points_to_the_reference_property_not_body_parameter():
    found = extract_reference_candidates(_documents())
    peer = next(item for item in found if item["referenceToken"] == "peer")
    assert peer["sourceLocator"] == "network.json#/definitions/Nic/properties/peer"

