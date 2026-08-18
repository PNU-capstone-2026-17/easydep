from __future__ import annotations

import pytest

from app.core.orchestration.adapters.cloud_design import CloudDesignAdapter
from app.design.services.deployment_diagram.topology import (
    derive_deployment_topology,
    enumerate_topology_families,
    provider_projection_policy,
)


def test_supported_family_census_is_12_logical_and_36_provider_native():
    logical = enumerate_topology_families()
    native = enumerate_topology_families(include_providers=True)

    assert len(logical) == 12
    assert len(native) == 36
    assert len({item.id for item in logical}) == 12
    assert len({item.id for item in native}) == 36


@pytest.mark.parametrize(
    "compute_profile",
    [
        "managedGroupOne",
        "managedGroupManySingleZone",
        "managedGroupManyMultiZone",
    ],
)
def test_managed_group_families_never_include_direct_or_colocated_database(
    compute_profile,
):
    families = [
        item for item in enumerate_topology_families() if item.compute_profile == compute_profile
    ]

    assert len(families) == 2
    assert {item.public_ingress for item in families} == {"loadBalanced"}
    assert {item.database_placement for item in families} == {"none", "dedicated"}


def test_standalone_family_keeps_direct_lb_and_all_database_placements():
    families = [
        item for item in enumerate_topology_families() if item.compute_profile == "standaloneOne"
    ]

    assert len(families) == 6
    assert {item.public_ingress for item in families} == {
        "direct",
        "loadBalanced",
    }
    assert {item.database_placement for item in families} == {
        "none",
        "colocated",
        "dedicated",
    }


def test_many_single_zone_is_valid_but_requires_stateless_evidence():
    topology = derive_deployment_topology(
        provider="aws",
        resource_spec={
            "computeProfile": "managedGroupManySingleZone",
            "replicaCount": 3,
            "publicIngress": "loadBalanced",
            "databasePlacement": "none",
        },
    )

    assert topology["zoneLayout"] == "singleZone"
    assert topology["replicaCount"] == 3
    assert topology["availabilityClaim"] == "none"
    assert topology["issues"] == [
        {
            "field": "applicationStateless",
            "reason": (
                "Multiple replicas require evidence that local session, uploads, "
                "singleton schedulers, and writable state are absent or externalized."
            ),
            "classification": "needsInput",
        }
    ]


def test_multi_zone_spread_requires_two_selected_zones():
    topology = derive_deployment_topology(
        provider="gcp",
        resource_spec={
            "computeProfile": "managedGroupManyMultiZone",
            "replicaCount": 2,
            "selectedZones": ["asia-northeast3-a"],
            "publicIngress": "loadBalanced",
            "databasePlacement": "none",
            "applicationStateless": True,
        },
    )

    assert any(
        item["field"] == "selectedZones" and item["classification"] == "needsInput"
        for item in topology["issues"]
    )


def test_postgresql_defaults_to_a_dedicated_vm_for_compatibility():
    topology = derive_deployment_topology(
        provider="azure",
        logical_deployment_model={
            "Nodes": [
                {"name": "Application", "kind": "executionEnvironment"},
                {"name": "Database", "kind": "database"},
            ]
        },
        persistent_storage_required=True,
    )

    assert topology["databasePlacement"] == "dedicated"
    assert topology["databasePolicy"] == {
        "engine": "postgresql",
        "instanceCount": 1,
        "publiclyReachable": False,
        "separatePersistentDisk": True,
        "secretPolicy": "callerSuppliedSecretReference",
    }
    assert topology["stateDeletionPolicy"] == "retainPersistentDisk"


def test_projection_adapter_does_not_make_an_availability_claim():
    topology = derive_deployment_topology(
        provider="aws",
        resource_spec={
            "computeProfile": "managedGroupManyMultiZone",
            "replicaCount": 3,
            "selectedZones": ["ap-northeast-2a", "ap-northeast-2c"],
            "publicIngress": "loadBalanced",
            "databasePlacement": "none",
            "applicationStateless": True,
        },
    )

    projection = provider_projection_policy(topology)

    assert projection["mode"] == "managedGroup"
    assert projection["minimumInstances"] == 3
    assert projection["minimumZones"] == 2
    assert projection["minimumIngressZones"] == 2
    assert projection["minimumSubnets"] == 2
    assert projection["minimumIngressSubnets"] == 2
    assert projection["loadBalancerRequired"] is True
    assert projection["backendHealthRequired"] is True
    assert "automaticRecovery" not in projection
    assert "highAvailabilityRequired" not in projection


def test_aws_network_load_balancer_uses_one_subnet_for_single_zone_plan():
    topology = derive_deployment_topology(
        provider="aws",
        resource_spec={
            "computeProfile": "standaloneOne",
            "replicaCount": 1,
            "selectedZones": ["ap-northeast-2a"],
            "publicIngress": "loadBalanced",
            "databasePlacement": "none",
        },
    )

    projection = provider_projection_policy(topology)

    assert projection["minimumZones"] == 1
    assert projection["minimumIngressZones"] == 1
    assert projection["minimumSubnets"] == 1
    assert projection["minimumIngressSubnets"] == 1
    assert projection["zoneSpreadRequired"] is False


def test_all_36_provider_labelled_families_reach_resource_plan_projection():
    for family in enumerate_topology_families(include_providers=True):
        many = "Many" in family.compute_profile
        spread = family.compute_profile == "managedGroupManyMultiZone"
        resource_spec = {
            "provider": family.provider,
            "region": "test-region",
            "computeProfile": family.compute_profile,
            "replicaCount": 2 if many else 1,
            "selectedZones": ["zone-a", "zone-b"] if spread else ["zone-a"],
            "databasePlacement": family.database_placement,
            "publicIngress": family.public_ingress,
            "applicationStateless": True,
        }
        if family.public_ingress == "loadBalanced":
            resource_spec["tls"] = {
                "hostname": "app.example.test",
                "certificateInputRef": "test:existing-certificate",
            }
        nodes = [{"name": "Application", "kind": "executionEnvironment"}]
        if family.database_placement != "none":
            nodes.append({"name": "Database", "kind": "database"})

        result = CloudDesignAdapter().finalize(
            requirements_result={
                "resource_spec": resource_spec,
                "deployment_needs": {},
            },
            design_result={"deployment_diagram_model": {"Nodes": nodes}},
        )

        assert result["status"] == "completed", family.id
        assert result["topology_policy"]["familyId"] == family.id
        assert result["resource_plan"]["deploymentTopology"]["availabilityClaim"] == "none"
        if family.database_placement != "none":
            assert "disk" in result["anchors"]
