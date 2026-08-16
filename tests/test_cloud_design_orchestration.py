from __future__ import annotations

import pytest

from app.core.orchestration.adapters.cloud_design import CloudDesignAdapter


@pytest.mark.parametrize(
    ("provider", "region", "native_group", "native_label", "ingress_label"),
    [
        (
            "aws",
            "ap-northeast-2",
            "autoscaling-group",
            "EC2 Auto Scaling Group",
            "Application Load Balancer",
        ),
        (
            "azure",
            "koreacentral",
            "virtual-machine-scale-set",
            "Virtual Machine Scale Set",
            "Application Gateway",
        ),
        (
            "gcp",
            "asia-northeast3",
            "regional-managed-instance-group",
            "Regional Managed Instance Group",
            "Global Forwarding Rule",
        ),
    ],
)
def test_cloud_design_projects_explicit_managed_multi_zone_topology(
    provider, region, native_group, native_label, ingress_label
):
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": provider,
                "region": region,
                "workloads": ["vm"],
                "monthlyBudgetUSD": 100,
                "computeProfile": "managedGroupManyMultiZone",
                "replicaCount": 2,
                "selectedZones": ["zone-a", "zone-b"],
                    "publicIngress": "loadBalanced",
                    "applicationStateless": True,
                    "tls": {
                        "hostname": "app.example.test",
                        "certificateInputRef": "test:existing-certificate",
                    },
            },
            "deployment_needs": {},
        },
        design_result={
            "deployment_diagram_puml": "@startuml\nnode app\n@enduml",
            "deployment_diagram_model": {
                "Nodes": [{"name": "Application", "kind": "executionEnvironment"}]
            },
        },
    )

    assert result["status"] == "completed"
    assert result["anchors"] == ["vm", "loadBalancer"]
    topology = result["topology_policy"]
    projection = result["provider_projection_policy"]
    assert topology["computeProfile"] == "managedGroupManyMultiZone"
    assert topology["availabilityClaim"] == "none"
    assert topology["familyId"].endswith(
        "managedGroupManyMultiZone.none.loadBalanced"
    )
    assert projection["mode"] == "managedGroup"
    assert projection["nativeComputeGroup"] == native_group
    assert result["kb_used"] == ["depkb"]
    assert result["deferred"] == ["capacity", "performance", "price", "vm_selection"]
    assert "Application" in result["deployment_diagram_puml"]
    assert native_label in result["deployment_diagram_puml"]
    assert result["deployment_diagram_model"]["provider"] == provider
    names = {node["name"] for node in result["deployment_diagram_model"]["nodes"]}
    assert {native_label, ingress_label} <= names
    assert all(edge["evidence"] for edge in result["deployment_diagram_model"]["edges"])
    evidence = {
        item for edge in result["deployment_diagram_model"]["edges"] for item in edge["evidence"]
    }
    assert {"official-dependency", "capability-realization"} <= evidence
    assert "?" not in result["deployment_diagram_puml"]
    assert result["logical_deployment_diagram_puml"].startswith("@startuml")
    assert result["resource_plan"] is result["deployment_diagram_model"]
    assert result["resource_plan"]["schemaVersion"] == "easydep-resource-plan/v1"


@pytest.mark.parametrize(
    ("provider", "native_label"),
    [
        ("aws", "EC2 Instance"),
        ("azure", "Linux Virtual Machine"),
        ("gcp", "Compute Engine VM"),
    ],
)
def test_cloud_design_defaults_to_one_standalone_vm(
    provider, native_label
):
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {"provider": provider, "region": "test-region"},
            "deployment_needs": {},
        },
        design_result={},
    )

    assert result["anchors"] == ["vm"]
    assert result["topology_policy"]["computeProfile"] == "standaloneOne"
    assert result["topology_policy"]["replicaCount"] == 1
    assert result["topology_policy"]["availabilityClaim"] == "none"
    assert result["provider_projection_policy"]["nativeComputeGroup"] is None
    names = {node["name"] for node in result["deployment_diagram_model"]["nodes"]}
    assert native_label in names
    assert not any("Load Balancer" in name or "Application Gateway" in name for name in names)
    assert result["dependency_coverage"]["modeledInputs"][-1] == {
        "source": "deployment-topology",
        "field": "topologyFamily",
        "requirementIds": [],
        "outcome": f"{provider}.standaloneOne.none.direct",
    }


def test_capacity_instance_floor_does_not_become_a_high_availability_claim():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {"provider": "aws", "region": "ap-northeast-2"},
            "deployment_needs": {
                "capacity_floor": {
                    "decision": "accepted",
                    "metadata": {"minimum_instances": 3},
                }
            },
        },
        design_result={},
    )

    assert result["topology_policy"]["computeProfile"] == "standaloneOne"
    assert result["topology_policy"]["availabilityClaim"] == "none"
    assert result["anchors"] == ["vm"]


def test_multi_zone_profile_stops_until_two_zones_are_selected():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": "aws",
                "region": "ap-northeast-2",
                "computeProfile": "managedGroupManyMultiZone",
                "replicaCount": 2,
                "selectedZones": ["ap-northeast-2a"],
                "publicIngress": "loadBalanced",
                "applicationStateless": True,
                "tls": {
                    "hostname": "app.example.test",
                    "certificateInputRef": "test:existing-certificate",
                },
            },
            "deployment_needs": {},
        },
        design_result={},
    )

    assert result["status"] == "needsInput"
    assert result["open_questions"][0]["field"] == "selectedZones"


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_many_single_zone_is_a_valid_managed_group_family(provider):
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": provider,
                "region": "test-region",
                "computeProfile": "managedGroupManySingleZone",
                "replicaCount": 3,
                "selectedZones": ["zone-a"],
                "publicIngress": "loadBalanced",
                "applicationStateless": True,
                "tls": {
                    "hostname": "app.example.test",
                    "certificateInputRef": "test:existing-certificate",
                },
            },
            "deployment_needs": {},
        },
        design_result={},
    )

    topology = result["topology_policy"]
    plan = result["resource_plan"]
    assert result["status"] == "completed"
    assert result["anchors"] == ["vm", "loadBalancer"]
    assert topology["zoneLayout"] == "singleZone"
    assert topology["replicaCount"] == 3
    assert topology["availabilityClaim"] == "none"
    assert next(item for item in plan["allocations"] if item["computeRef"] == "compute-group")[
        "replicas"
    ] == 3


def test_managed_group_one_is_distinct_from_standalone_vm():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": "gcp",
                "region": "asia-northeast3",
                "computeProfile": "managedGroupOne",
                "replicaCount": 1,
                "publicIngress": "loadBalanced",
            },
            "deployment_needs": {},
        },
        design_result={},
    )

    assert result["topology_policy"]["computeProfile"] == "managedGroupOne"
    assert result["topology_policy"]["replicaCount"] == 1
    assert result["topology_policy"]["availabilityClaim"] == "none"
    assert result["anchors"] == ["vm", "loadBalancer"]


def test_multi_region_is_preserved_as_unsupported_instead_of_lowered_to_zones():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {"provider": "azure", "region": "koreacentral"},
            "deployment_needs": {
                "regional_placement": {
                    "required": True,
                    "decision": "accepted",
                    "requirementIds": ["NFR-REGION"],
                    "metadata": {"placementScope": "multiRegion"},
                }
            },
        },
        design_result={},
    )

    assert result["status"] == "unsupported"
    assert result["reason"] == "multi-region-out-of-scope"


def test_high_availability_preference_does_not_force_managed_resources():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {"provider": "aws", "region": "ap-northeast-2"},
            "deployment_needs": {
                "availability_preference": {
                    "required": False,
                    "decision": "accepted",
                    "metadata": {"high_availability": True},
                }
            },
        },
        design_result={},
    )

    assert result["topology_policy"]["computeProfile"] == "standaloneOne"
    assert result["topology_policy"]["availabilityClaim"] == "none"
    assert result["anchors"] == ["vm"]


def test_logical_postgresql_workload_gets_its_required_persistent_disk():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": "aws",
                "region": "ap-northeast-2",
            }
        },
        design_result={
            "deployment_diagram_model": {"Nodes": [{"name": "Database", "kind": "database"}]}
        },
    )

    assert result["anchors"] == ["vm", "disk"]


def test_accepted_persistent_storage_adds_disk_anchor():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": "aws",
                "region": "ap-northeast-2",
            },
            "deployment_needs": {
                "persistent_storage": {
                    "required": True,
                    "decision": "accepted",
                }
            },
        },
        design_result={
            "deployment_diagram_model": {
                "Nodes": [
                    {"name": "Application", "kind": "executionEnvironment"},
                    {"name": "Database", "kind": "database"},
                ]
            }
        },
    )

    assert result["anchors"] == ["vm", "disk"]
    assert any(
        item.get("outcome") == "disk" for item in result["dependency_coverage"]["modeledInputs"]
    )


def test_stable_capability_id_does_not_depend_on_dynamic_need_key():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": "gcp",
                "region": "asia-northeast3",
            },
            "deployment_needs": {
                "durable_storage_across_restarts": {
                    "required": True,
                    "decision": "accepted",
                    "dependencyCapabilityIds": ["persistent-block-storage"],
                }
            },
        },
        design_result={
            "deployment_diagram_model": {
                "Nodes": [{"name": "Database", "kind": "database"}]
            }
        },
    )

    assert result["anchors"] == ["vm", "disk"]
    assert result["dependency_coverage"]["unmodeledAcceptedNeeds"] == []


def test_persistent_state_semantics_drive_plan_without_a_fixed_need_key():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {"provider": "aws", "region": "ap-northeast-2"},
            "deployment_needs": {
                "arbitrary_shared_state": {
                    "required": True,
                    "decision": "accepted",
                    "requirementIds": ["NFR-DATA"],
                    "metadata": {
                        "applicationState": {
                            "durability": "persistent",
                            "accessScope": "shared-service",
                        }
                    },
                }
            },
        },
        design_result={
            "deployment_diagram_model": {
                "Nodes": [
                    {"name": "Service Runtime", "kind": "executionEnvironment"},
                    {"name": "State Runtime", "kind": "database"},
                ]
            }
        },
    )

    assert result["anchors"] == ["vm", "disk"]
    assert result["dependency_coverage"]["unmodeledAcceptedNeeds"] == []
    assert any(
        item.get("field") == "arbitrary_shared_state"
        and item.get("outcome") == "persistent-workload-with-separate-data-disk"
        for item in result["dependency_coverage"]["modeledInputs"]
    )
    disk_decision = next(
        item
        for item in result["resource_plan"]["decisions"]
        if item["field"] == "separateDataDisk"
    )
    assert disk_decision["basis"] == "project-policy:self-hosted-persistent-workload"
    assert disk_decision["sourceRefs"] == ["NFR-DATA"]


def test_https_load_balanced_requirement_is_projected_to_supported_http_lb():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": "aws",
                "region": "ap-northeast-2",
            },
            "deployment_needs": {
                "secure_public_entry": {
                    "required": True,
                    "decision": "accepted",
                    "dependencyCapabilityIds": ["https-load-balanced-ingress"],
                }
            },
        },
        design_result={},
    )

    assert result["anchors"] == ["vm", "loadBalancer"]
    realizations = result["infra_intent"]["capabilityRealizations"]
    assert [item["id"] for item in realizations] == ["http-alb"]
    assert not any(component["id"] == "certificate" for component in realizations[0]["components"])
    assert any(
        edge["from"] == "compute-instance" and edge["to"] == "backend-group"
        for edge in result["deployment_diagram_model"]["edges"]
    )


def test_azure_single_vm_load_balancer_has_a_backend_membership_path():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {"provider": "azure", "region": "koreacentral"},
            "deployment_needs": {
                "public_entry": {
                    "decision": "accepted",
                    "dependencyCapabilityIds": ["load-balanced-ingress"],
                }
            },
        },
        design_result={
            "deployment_diagram_model": {
                "Nodes": [{"name": "Database", "kind": "database"}]
            }
        },
    )

    assert result["topology_policy"]["computeProfile"] == "standaloneOne"
    assert any(
        edge["from"] == "network-interface" and edge["to"] == "backend-membership"
        for edge in result["deployment_diagram_model"]["edges"]
    )


def test_mandatory_prohibition_does_not_add_disk_anchor():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": "azure",
                "region": "koreacentral",
            },
            "deployment_needs": {
                "persistent_storage": {
                    "role": "Do not allocate a persistent application disk",
                    "required": True,
                    "decision": "accepted",
                    "metadata": {"persistent_application_disk": False},
                }
            },
        },
        design_result={},
    )

    assert result["anchors"] == ["vm"]
    assert any(
        item.get("outcome") == "no_disk" for item in result["dependency_coverage"]["modeledInputs"]
    )


def test_cloud_design_reports_accepted_but_unmodeled_capabilities():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": "gcp",
                "region": "asia-northeast3",
            },
            "deployment_needs": {
                "https_ingress": {
                    "required": True,
                    "decision": "accepted",
                },
                "availability_requirement": {
                    "required": True,
                    "decision": "accepted",
                    "metadata": {"high_availability": True},
                },
                "unconfirmed_observability": {
                    "required": True,
                    "decision": "needsQuestion",
                },
            },
        },
        design_result={},
    )

    assert result["anchors"] == ["vm"]
    assert result["dependency_coverage"]["unmodeledAcceptedNeeds"] == [
        "availability_requirement",
        "https_ingress",
    ]
    assert result["dependency_coverage"]["modeledInputs"][-1] == {
        "source": "deployment-topology",
        "field": "topologyFamily",
        "requirementIds": [],
        "outcome": "gcp.standaloneOne.none.direct",
    }


def test_cloud_design_does_not_block_without_cloud_coordinates():
    logical = "@startuml\nnode app\n@enduml"
    result = CloudDesignAdapter().finalize(
        requirements_result={"resource_spec": {}},
        design_result={"deployment_diagram_puml": logical},
    )

    assert result["status"] == "skipped"
    assert result["deployment_diagram_puml"] == logical
    assert result["kb_used"] == []


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_resource_plan_keeps_independent_workloads_and_persistent_owner(provider):
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {"provider": provider, "region": "test-region"},
            "deployment_needs": {
                "durable_state": {
                    "required": True,
                    "decision": "accepted",
                    "dependencyCapabilityIds": ["persistent-block-storage"],
                },
                "container_runtime": {
                    "required": True,
                    "decision": "accepted",
                    "metadata": {"database_container_image": "postgres:16"},
                },
            },
        },
        design_result={
            "deployment_diagram_model": {
                "Nodes": [
                    {
                        "name": "Request Processor",
                        "kind": "executionEnvironment",
                        "source_classes": ["RequestControl"],
                    },
                    {"name": "Record Store", "kind": "database"},
                ],
                "Connections": [
                    {
                        "source": "Request Processor",
                        "target": "Record Store",
                        "protocol": "TCP",
                    }
                ],
            }
        },
    )

    plan = result["resource_plan"]
    assert [item["name"] for item in plan["workloads"]] == [
        "Request Processor",
        "Record Store",
    ]
    assert {item["replicas"] for item in plan["allocations"]} == {1}
    state = next(item for item in plan["workloads"] if item["name"] == "Record Store")
    state_allocation = next(
        item for item in plan["allocations"] if item["workloadRef"] == state["id"]
    )
    assert state["persistence"] == "persistent"
    assert state["runtime"]["image"] == "postgres:16"
    assert state["runtime"]["containerPort"] == 5432
    assert any(
        edge["from"] == "disk-attachment"
        and edge["to"] == state_allocation["computeRef"]
        and edge["label"] == "binds"
        for edge in plan["edges"]
    )
    assert any(
        edge["from"] == "disk-attachment"
        and edge["to"] == "disk"
        and edge["label"] == "binds"
        for edge in plan["edges"]
    )
    connection = next(edge for edge in plan["edges"] if edge.get("relation") == "connectsTo")
    assert connection["runtimeBinding"] == {
        "targetEndpoint": "runtimeDerived",
        "onTargetReplacement": "updateConfiguration",
        "applicationImageRebuildRequired": False,
        "privateNetworkPathRequired": True,
        "trafficFilterRequired": True,
        "evidenceRefs": [f"experiment:E1/{provider}"],
    }
    assert plan["runtimeEvidence"]["stateRestartPersistence"] == {
        "status": "observed",
        "evidenceRefs": [f"experiment:E1/{provider}"],
        "observedFault": "state VM restart or reset",
    }
    assert plan["runtimeEvidence"]["stateReplacementRebind"] == {
        "status": "observed",
        "evidenceRefs": [f"experiment:E3/{provider}"],
        "observedFault": (
            "state VM replacement, existing data-disk reattachment, and runtime "
            "endpoint reinjection without rebuilding the application image"
        ),
    }
    assert plan["unresolved"] == []
    assert "course" not in str(plan).lower()
    assert "enrollment" not in str(plan).lower()


def test_secondary_non_database_workload_requires_an_explicit_runtime_contract():
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {"provider": "aws", "region": "test-region"},
            "deployment_needs": {},
        },
        design_result={
            "deployment_diagram_model": {
                "Nodes": [
                    {"name": "Public API", "kind": "executionEnvironment"},
                    {"name": "Background worker", "kind": "executionEnvironment"},
                ]
            }
        },
    )

    assert result["resource_plan"]["unresolved"] == [
        {
            "field": "workloads.workload-background-worker.runtime",
            "reason": (
                "An explicit deployable workload has no supported, evidence-backed "
                "runtime contract."
            ),
        }
    ]


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_managed_group_applies_only_to_the_non_state_workload_tier(provider):
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": provider,
                "region": "test-region",
                "computeProfile": "managedGroupManySingleZone",
                "replicaCount": 2,
                "publicIngress": "loadBalanced",
                "applicationStateless": True,
            },
            "deployment_needs": {
                "durable_state": {
                    "required": True,
                    "decision": "accepted",
                    "dependencyCapabilityIds": ["persistent-block-storage"],
                },
            },
        },
        design_result={
            "deployment_diagram_model": {
                "Nodes": [
                    {"name": "API Runtime", "kind": "executionEnvironment"},
                    {"name": "State Runtime", "kind": "database"},
                ],
                "Connections": [
                    {"source": "API Runtime", "target": "State Runtime", "protocol": "TCP"}
                ],
            }
        },
    )

    plan = result["resource_plan"]
    allocations = {item["workloadRef"]: item for item in plan["allocations"]}
    app = next(item for item in plan["workloads"] if item["name"] == "API Runtime")
    state = next(item for item in plan["workloads"] if item["name"] == "State Runtime")
    assert allocations[app["id"]]["computeRef"] == "compute-group"
    assert allocations[app["id"]]["replicas"] == 2
    assert allocations[state["id"]]["computeRef"] != "compute-group"
    assert allocations[state["id"]]["replicas"] == 1
    assert result["topology_policy"]["availabilityClaim"] == "none"


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_external_connection_becomes_supported_direct_http_endpoint(provider):
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {"provider": provider, "region": "test-region"},
            "deployment_needs": {},
        },
        design_result={
            "deployment_diagram_model": {
                "Nodes": [
                    {"name": "Client", "kind": "device"},
                    {"name": "Service Runtime", "kind": "executionEnvironment"},
                ],
                "Connections": [
                    {"source": "Client", "target": "Service Runtime", "protocol": "HTTPS"}
                ],
            }
        },
    )

    plan = result["resource_plan"]
    endpoint = next(node for node in plan["nodes"] if node.get("group") == "endpoint")
    assert endpoint["mode"] == "direct"
    assert endpoint["protocol"] == "http"
    assert "tlsTermination" not in endpoint
    assert any(node.get("providerKind") == "public-ip" for node in plan["nodes"])
    assert not any(node.get("id") == "direct-tls-automation" for node in plan["nodes"])
    assert "public_endpoint" in result["deployment_diagram_puml"]
    assert "public_address -[#2f6b50]-> runtime_workload_service_runtime : HTTP" in (
        result["deployment_diagram_puml"]
    )
    assert plan["unresolved"] == []
    assert plan["runtimeEvidence"]["managedHttpIngress"] == {
        "status": "notApplicable",
        "evidenceRefs": [],
    }


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_explicit_load_balanced_topology_uses_managed_http_ingress(provider):
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": provider,
                "region": "test-region",
                "computeProfile": "managedGroupOne",
                "replicaCount": 1,
                "publicIngress": "loadBalanced",
            },
            "deployment_needs": {},
        },
        design_result={
            "deployment_diagram_model": {
                "Nodes": [
                    {"name": "Client", "kind": "device"},
                    {"name": "Service Runtime", "kind": "executionEnvironment"},
                ],
                "Connections": [
                    {"source": "Client", "target": "Service Runtime", "protocol": "HTTPS"}
                ],
            }
        },
    )

    plan = result["resource_plan"]
    endpoint = next(node for node in plan["nodes"] if node.get("group") == "endpoint")
    assert endpoint["mode"] == "loadBalanced"
    assert endpoint["protocol"] == "http"
    assert "tlsTermination" not in endpoint
    assert not any(node.get("providerKind") == "certificate" for node in plan["nodes"])
    assert result["status"] == "completed"
    assert plan["unresolved"] == []
    managed_http = plan["runtimeEvidence"]["managedHttpIngress"]
    assert managed_http["status"] == "observed"
    assert managed_http["evidenceRefs"] == [
        {
            "aws": "experiment:E2/aws",
            "azure": "experiment:managed-http/azure",
            "gcp": "experiment:E2/gcp",
        }[provider]
    ]
    assert "readiness and business probes" in managed_http["observedFunction"]
    assert "observedIntervention" in managed_http
    assert managed_http["notMeasured"] == [
        "transport security",
        "availability SLA",
        "performance",
    ]


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_tls_inputs_do_not_expand_the_http_only_scope(provider):
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {"provider": provider, "region": "test-region"},
            "deployment_needs": {
                "public_https": {
                    "required": True,
                    "decision": "accepted",
                    "requirementIds": ["NFR-TLS-001"],
                    "metadata": {
                        "tls": {
                            "hostname": "api.example.test",
                            "certificateInputRef": "secret://evaluation/tls-certificate",
                        }
                    },
                }
            },
        },
        design_result={
            "deployment_diagram_model": {
                "Nodes": [
                    {"name": "Client", "kind": "device"},
                    {"name": "Service Runtime", "kind": "executionEnvironment"},
                ],
                "Connections": [
                    {"source": "Client", "target": "Service Runtime", "protocol": "HTTPS"}
                ],
            }
        },
    )

    plan = result["resource_plan"]
    endpoint = next(node for node in plan["nodes"] if node.get("group") == "endpoint")
    assert endpoint["protocol"] == "http"
    assert "tlsTermination" not in endpoint
    assert "hostname" not in endpoint
    assert "certificateInputRef" not in endpoint
    assert plan["unresolved"] == []


def test_multiple_cloud_targets_remain_separate_until_the_user_selects_one():
    logical_diagram = "@startuml\nnode Application\n@enduml"
    result = CloudDesignAdapter().finalize(
        requirements_result={
            "resource_spec": {
                "provider": "aws",
                "region": "ap-northeast-2",
                "deploymentTargets": [
                    {
                        "provider": "aws",
                        "region": "ap-northeast-2",
                        "zones": ["ap-northeast-2a"],
                    },
                    {
                        "provider": "azure",
                        "region": "koreacentral",
                        "zones": ["1"],
                    },
                ],
            },
            "deployment_needs": {},
        },
        design_result={"deployment_diagram_puml": logical_diagram},
    )

    assert result["status"] == "alternativesReady"
    assert result["requires_target_selection"] is True
    assert result["deployment_diagram_puml"] == logical_diagram
    assert {
        (item["provider"], item["region"])
        for item in result["provider_deployments"]
    } == {("aws", "ap-northeast-2"), ("azure", "koreacentral")}
    assert result["open_questions"][0]["choices"] == [
        "aws:ap-northeast-2",
        "azure:koreacentral",
    ]
