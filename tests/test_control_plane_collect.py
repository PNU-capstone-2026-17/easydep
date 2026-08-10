from __future__ import annotations

import pytest

from app.core.cloudkb.depkb.control_plane_collect import (
    collect_aws,
    collect_gcp,
)

ANCHOR = {
    "serviceFamily": "ec2",
    "operation": "RunInstances",
    "capabilityId": "compute-runtime",
    "requirementIds": ["P1-aws:R5"],
    "evidenceSpans": ["Docker"],
}


def test_aws_collector_keeps_all_operations_but_traverses_only_anchor_shapes():
    model = {
        "operations": {
            "RunInstances": {"input": {"shape": "RunInput"}},
            "Unrelated": {"input": {"shape": "OtherInput"}},
        },
        "shapes": {
            "RunInput": {"type": "structure", "members": {"Network": {"shape": "Network"}}},
            "Network": {"type": "structure", "members": {}},
            "OtherInput": {"type": "structure", "members": {}},
        },
    }

    result = collect_aws({"ec2": model}, [ANCHOR], version="1")
    ids = {item["nativeId"] for item in result["observations"]}

    assert "ec2.operation.Unrelated" in ids
    assert "ec2.shape.RunInput" in ids
    assert "ec2.shape.Network" in ids
    assert "ec2.shape.OtherInput" not in ids
    assert all("nativeForm" not in item for item in result["observations"])
    assert all("independentlyReadable" in item for item in result["observations"])
    assert all("connectionManager" in item for item in result["observations"])


def test_gcp_collector_uses_discovery_methods_as_population():
    document = {
        "resources": {"instances": {"methods": {
            "insert": {"httpMethod": "POST", "request": {"$ref": "Instance"}},
            "list": {"httpMethod": "GET", "response": {"$ref": "InstanceList"}},
        }}},
        "schemas": {
            "Instance": {"properties": {"network": {"$ref": "NetworkInterface"}}},
            "NetworkInterface": {"properties": {}},
            "InstanceList": {"properties": {}},
        },
    }
    anchor = ANCHOR | {"serviceFamily": "instances", "operation": "insert"}

    result = collect_gcp(document, [anchor], version="1")
    ids = {item["nativeId"] for item in result["observations"]}

    assert "compute.operation.instances.list" in ids
    assert "compute.schema.Instance" in ids
    assert "compute.schema.NetworkInterface" in ids
    assert "compute.schema.InstanceList" not in ids


def test_gcp_population_is_capsulated_to_anchor_service_families():
    document = {
        "resources": {
            "instances": {"methods": {"insert": {"httpMethod": "POST"}}},
            "vpnGateways": {"methods": {"insert": {"httpMethod": "POST"}}},
        },
        "schemas": {},
    }
    anchor = ANCHOR | {"serviceFamily": "instances", "operation": "insert"}

    result = collect_gcp(document, [anchor], version="1")
    ids = {item["nativeId"] for item in result["observations"]}

    assert "compute.operation.instances.insert" in ids
    assert "compute.operation.vpnGateways.insert" not in ids


def test_collector_rejects_anchor_absent_from_pinned_source():
    model = {"operations": {}, "shapes": {}}

    with pytest.raises(ValueError, match="anchors absent"):
        collect_aws({"ec2": model}, [ANCHOR], version="1")
