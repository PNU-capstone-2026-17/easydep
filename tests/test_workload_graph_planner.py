from __future__ import annotations

from copy import deepcopy

import pytest

from app.core.orchestration.adapters.cloud_design import CloudDesignAdapter
from app.design.graphs.subgraphs import _finalize_deployment_diagram
from app.design.services.deployment_diagram.bundle import (
    build_deployment_diagram_bundle,
    hydrate_deployment_diagram_bundle,
)
from app.design.services.deployment_diagram.extractor import WorkloadGraphProposal
from app.design.services.deployment_diagram.planner import (
    bind_runtime_contract,
    build_deployment_plan,
    build_provider_resource_plan,
    deployment_plan_structure_digest,
    extract_planning_facts,
    normalize_workload_graph,
    planning_inputs_stale,
    validate_provider_resource_plan,
)


def workload(
    workload_id: str,
    *,
    public: bool | None = None,
    storage: list[dict] | None = None,
    safety: str = "singleton",
    artifact: dict | None = None,
) -> dict:
    interfaces = []
    if public is not None:
        interfaces.append(
            {
                "id": "http",
                "protocol": "http",
                "exposure": "public" if public else "internal",
                "sourceRefs": [f"api:{workload_id}"],
            }
        )
    return {
        "id": workload_id,
        "name": workload_id.title(),
        "artifact": artifact or {"kind": "generatedApplication"},
        "interfaces": interfaces,
        "storage": storage or [],
        "configuration": [],
        "resourceRequirements": {},
        "replicationSafety": safety,
        "sourceRefs": [f"class:{workload_id}"],
    }


def graph(*workloads: dict, constraints: list[dict] | None = None) -> dict:
    return {
        "schemaVersion": "easydep-workload-graph",
        "workloads": list(workloads),
        "externalDependencies": [],
        "connections": [],
        "constraints": constraints or [],
        "derivations": [],
    }


def normalized(value: dict, facts: dict | None = None) -> dict:
    return normalize_workload_graph(value, planning_facts=facts)


def test_public_single_app_uses_standalone_vm_and_direct_public_ip() -> None:
    model = normalized(graph(workload("web", public=True)))
    plan = build_deployment_plan(model, {"region": "ap-northeast-2"})
    resource = build_provider_resource_plan(
        plan, model, provider="aws", region="ap-northeast-2"
    )

    assert [(item["kind"], item["replicaCount"]) for item in plan["computeUnits"]] == [
        ("standaloneVm", 1)
    ]
    assert any(item["ingressKind"] == "directPublicIp" for item in plan["networkPaths"])
    assert "elastic-ip" in {item["providerKind"] for item in resource["nodes"]}
    assert "nat-gateway" not in {item["providerKind"] for item in resource["nodes"]}


def test_private_single_app_has_no_public_ingress_and_uses_nat() -> None:
    model = normalized(graph(workload("worker", public=False)))
    plan = build_deployment_plan(model)

    assert not any(item["kind"] == "publicIngress" for item in plan["networkPaths"])
    assert any(item["kind"] == "natEgress" for item in plan["networkPaths"])


def test_compatible_workloads_colocate_and_isolation_splits_compute() -> None:
    base = graph(workload("api", public=False), workload("worker", public=False))
    colocated = build_deployment_plan(normalized(base))
    assert len(colocated["computeUnits"]) == 1
    assert {item["computeUnitRef"] for item in colocated["placements"]} == {"compute-1"}

    isolated_graph = deepcopy(base)
    isolated_graph["constraints"] = [
        {
            "id": "isolate-api-worker",
            "kind": "separate",
            "workloadRefs": ["api", "worker"],
            "value": True,
            "sourceRefs": ["requirement:NFR-7"],
        }
    ]
    isolated = build_deployment_plan(normalized(isolated_graph))
    assert len(isolated["computeUnits"]) == 2


def test_replica_two_uses_managed_group_load_balancer_and_nat() -> None:
    model = normalized(
        graph(
            workload("web", public=True, safety="interchangeable"),
            constraints=[
                {
                    "id": "replicas",
                    "kind": "replicaCount",
                    "workloadRefs": ["web"],
                    "value": 2,
                    "sourceRefs": ["requirement:NFR-HA"],
                }
            ],
        )
    )
    plan = build_deployment_plan(model)

    assert plan["computeUnits"][0]["kind"] == "managedVmGroup"
    assert plan["computeUnits"][0]["replicaCount"] == 2
    assert any(item.get("ingressKind") == "loadBalancer" for item in plan["networkPaths"])
    assert any(item["kind"] == "natEgress" for item in plan["networkPaths"])


def test_explicit_prebuilt_data_workload_owns_storage_and_disk() -> None:
    data = workload(
        "data",
        public=False,
        artifact={
            "kind": "prebuiltImage",
            "image": "registry.example/data@sha256:abc",
            "engine": "explicit-engine",
            "deploymentMode": "container",
            "runtimeCatalogRef": "docker-on-vm/prebuilt-image",
        },
        storage=[
            {
                "id": "data-volume",
                "persistence": "persistent",
                "capacityGiB": 20,
                "mountPath": "/srv/data",
                "deletionPolicy": "retain",
                "replicaSemantics": "singleAttachment",
                "sourceRefs": ["requirement:DATA-1"],
            }
        ],
    )
    model = normalized(graph(data))
    plan = build_deployment_plan(model)
    resource = build_provider_resource_plan(plan, model, provider="gcp", region="r1")

    assert plan["storageBindings"][0]["workloadRef"] == "data"
    assert any(item["providerKind"] == "persistent-disk" for item in resource["nodes"])


def test_explicit_planning_contracts_fill_runtime_dependencies_deterministically() -> None:
    additional = [
        {
            "id": "app",
            "kind": "workloadContract",
            "value": {
                "workloadId": "app",
                "artifactKind": "generatedApplication",
                "interface": {"protocol": "http", "exposure": "public"},
                "replicaCount": 1,
            },
            "sourceRefs": ["case:app"],
            "authority": "explicit",
            "status": "accepted",
        },
        {
            "id": "db",
            "kind": "workloadContract",
            "value": {
                "workloadId": "db",
                "artifactKind": "prebuiltImage",
                "image": "postgres:16",
                "engine": "postgresql",
                "deploymentMode": "container",
                "runtimeCatalogRef": "docker-on-vm/prebuilt-image",
                "interface": {"protocol": "tcp", "exposure": "internal", "port": 5432},
                "replicaCount": 1,
                "storage": {
                    "persistence": "persistent",
                    "capacityGiB": 20,
                    "mountPath": "/var/lib/postgresql/data",
                    "deletionPolicy": "retain",
                    "replicaSemantics": "singleAttachment",
                },
            },
            "sourceRefs": ["case:db"],
            "authority": "explicit",
            "status": "accepted",
        },
        {
            "id": "app-db",
            "kind": "connectionContract",
            "value": {
                "sourceWorkloadRef": "app",
                "targetWorkloadRef": "db",
                "protocol": "tcp",
                "endpointBindingRequired": True,
                "secretBindingRequired": True,
            },
            "sourceRefs": ["case:connection"],
            "authority": "explicit",
            "status": "accepted",
        },
    ]
    facts = extract_planning_facts(
        erd_model={"tables": [{"name": "registrations"}]},
        additional_planning_facts=additional,
    )

    model = normalize_workload_graph(
        {
            "workloads": [],
            "constraints": [
                {
                    "id": "invented-isolation",
                    "kind": "resourceIsolation",
                    "workloadRefs": ["app"],
                    "sourceRefs": ["app-db"],
                },
                {
                    "id": "ungrounded-isolation",
                    "kind": "resourceIsolation",
                    "workloadRefs": ["app"],
                    "sourceRefs": [],
                },
            ],
        },
        planning_facts=facts,
    )

    assert model["issues"] == []
    assert model["workloads"][1]["storage"][0]["capacityGiB"] == 20
    assert model["connections"][0]["protocol"] == "tcp"
    assert model["connections"][0]["id"] == "app-to-db"
    assert any(
        item["kind"] == "endpointBinding"
        for item in model["workloads"][0]["configuration"]
    )
    assert not any(item["kind"] == "resourceIsolation" for item in model["constraints"])
    assert len(build_deployment_plan(model)["computeUnits"]) == 1


def test_erd_alone_never_creates_database_workload_or_disk() -> None:
    facts = extract_planning_facts(erd_model={"tables": [{"name": "orders"}]})
    model = normalized(graph(workload("app", public=False)), facts)
    plan = build_deployment_plan(model)
    resource = build_provider_resource_plan(plan, model, provider="azure", region="r1")

    assert [item["id"] for item in model["workloads"]] == ["app"]
    assert plan["storageBindings"] == []
    assert not any("disk" in item["providerKind"] for item in resource["nodes"])
    assert any(item["field"] == "dataExecutionMode" for item in model["issues"])


def test_reference_integrity_and_ambiguous_exposure_are_blocking() -> None:
    ambiguous = workload("app")
    ambiguous["interfaces"] = [
        {
            "id": "http",
            "protocol": "http",
            "exposure": "unknown",
            "sourceRefs": ["api:paths"],
        }
    ]
    value = graph(ambiguous)
    value["connections"] = [
        {
            "id": "bad",
            "sourceRef": "app",
            "targetRef": "missing",
            "protocol": "http",
            "sourceRefs": ["sequence:UC-1:1"],
        }
    ]
    model = normalized(value)

    assert any(item["field"].endswith("exposure") for item in model["issues"])
    assert any("targetRef" in item["field"] for item in model["issues"])


def test_generated_connection_requires_one_valid_endpoint_environment_binding() -> None:
    source = workload("web", public=False)
    target = workload("api", public=False)
    value = graph(source, target)
    value["connections"] = [
        {
            "id": "web-to-api",
            "sourceRef": "web",
            "targetRef": "api",
            "targetInterfaceRef": "http",
            "protocol": "http",
            "sourceRefs": ["sequence:CALL-API"],
        }
    ]
    missing = normalized(value)
    assert any(item["field"].endswith("endpointBinding") for item in missing["issues"])

    source["configuration"] = [
        {
            "id": "api-url",
            "name": "API_SERVICE_URL",
            "kind": "endpointBinding",
            "connectionRef": "web-to-api",
            "projection": "url",
            "sourceRefs": ["sequence:CALL-API"],
        }
    ]
    complete = normalized(value)
    assert not any(
        item["field"].endswith("endpointBinding") for item in complete["issues"]
    )
    plan = build_deployment_plan(complete)
    binding = next(item for item in plan["runtimeBindings"] if item["kind"] == "endpointEnvironment")
    assert binding["environmentName"] == "API_SERVICE_URL"
    assert binding["strategy"] == "containerDns"


def test_configuration_names_and_generated_mount_paths_are_design_contracts() -> None:
    app = workload(
        "app",
        public=False,
        storage=[
            {
                "id": "uploads",
                "persistence": "persistent",
                "capacityGiB": 5,
                "deletionPolicy": "retain",
                "sourceRefs": ["requirement:UPLOADS"],
            }
        ],
    )
    app["configuration"] = [
        {
            "id": "bad-name",
            "name": "bad-name",
            "kind": "value",
            "value": "x",
            "sourceRefs": ["requirement:CONFIG"],
        }
    ]
    model = normalized(graph(app))

    assert any(item["field"].endswith(".name") for item in model["issues"])
    assert any(item["field"].endswith(".mountPath") for item in model["issues"])


def test_persistent_storage_with_multiple_replicas_requires_per_replica_semantics() -> None:
    app = workload(
        "app",
        public=True,
        safety="interchangeable",
        storage=[
            {
                "id": "uploads",
                "persistence": "persistent",
                "capacityGiB": 5,
                "deletionPolicy": "retain",
                "sourceRefs": ["requirement:UPLOADS"],
            }
        ],
    )
    model = normalized(
        graph(
            app,
            constraints=[
                {
                    "id": "replicas",
                    "kind": "replicaCount",
                    "workloadRefs": ["app"],
                    "value": 2,
                    "sourceRefs": ["requirement:HA"],
                }
            ],
        )
    )
    plan = build_deployment_plan(model)
    assert any("perReplica semantics" in item["reason"] for item in plan["issues"])


def test_runtime_values_preserve_structure_digest_but_new_interface_requires_regeneration() -> None:
    model = normalized(graph(workload("web", public=True)))
    plan = build_deployment_plan(model)
    before = deployment_plan_structure_digest(plan)
    bound = bind_runtime_contract(
        model,
        plan,
        {
            "workloads": [
                {
                    "workloadId": "web",
                    "imageDigest": "sha256:123",
                    "interfaces": [
                        {"interfaceId": "http", "port": 8080, "healthPath": "/health"}
                    ],
                }
            ]
        },
    )

    assert bound["status"] == "bound"
    assert bound["structureDigest"] == before
    assert deployment_plan_structure_digest(bound["deploymentPlan"]) == before

    changed = bind_runtime_contract(
        model,
        plan,
        {"workloads": [{"workloadId": "web", "interfaces": [{"id": "admin"}]}]},
    )
    assert changed["status"] == "requiresDeploymentDesignRegeneration"


def test_runtime_contract_must_implement_planned_environment_and_mount_contracts() -> None:
    app = workload(
        "web",
        public=True,
        storage=[
            {
                "id": "uploads",
                "persistence": "persistent",
                "capacityGiB": 5,
                "mountPath": "/srv/uploads",
                "deletionPolicy": "retain",
                "replicaSemantics": "singleAttachment",
                "sourceRefs": ["requirement:UPLOADS"],
            }
        ],
    )
    app["configuration"] = [
        {
            "id": "mode",
            "name": "APP_MODE",
            "kind": "value",
            "value": "production",
            "sourceRefs": ["requirement:MODE"],
        }
    ]
    model = normalized(graph(app))
    plan = build_deployment_plan(model)

    missing = bind_runtime_contract(
        model, plan, {"workloads": [{"workloadId": "web"}]}
    )
    assert missing["status"] == "requiresDeploymentDesignRegeneration"

    bound = bind_runtime_contract(
        model,
        plan,
        {
            "workloads": [
                {
                    "workloadId": "web",
                    "mounts": [
                        {"storageId": "uploads", "mountPath": "/srv/uploads"}
                    ],
                    "configuration": [
                        {"name": "APP_MODE", "value": "production"}
                    ],
                }
            ]
        },
    )
    assert bound["status"] == "bound"


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_provider_resource_plan_references_and_provenance_are_complete(provider: str) -> None:
    model = normalized(graph(workload("web", public=True)))
    plan = build_deployment_plan(model)
    resource = build_provider_resource_plan(plan, model, provider=provider, region="r1")

    validate_provider_resource_plan(resource)
    ids = {item["id"] for item in resource["nodes"]}
    consumer_ids = ids | {item["id"] for item in resource["embeddedBlocks"]}
    producer_ids = (
        ids
        | {item["id"] for item in resource["sharedValues"]}
        | {item["id"] for item in resource["bindingSlots"]}
    )
    assert all(
        reference["consumerRef"] in consumer_ids
        and reference["producerRef"] in producer_ids
        for reference in resource["references"]
    )
    assert all(item["sourceRefs"] for item in resource["nodes"])
    assert all(item["sourceRefs"] for item in resource["references"])
    assert all(item["sourceRefs"] for item in resource["embeddedBlocks"])
    assert all(item["sourceRefs"] for item in resource["sharedValues"])


def test_input_digest_marks_upstream_change_stale() -> None:
    facts = extract_planning_facts(api_spec={"paths": {"/a": {"get": {}}}})
    report = planning_inputs_stale(
        facts, api_spec={"paths": {"/b": {"post": {}}}}
    )
    assert report["stale"] is True
    assert {item["artifact"] for item in report["changedArtifacts"]} == {"apiSpec"}


def test_accepted_typed_capability_constraint_is_applied_deterministically() -> None:
    facts = extract_planning_facts(
        capability_contract={
            "capabilities": [
                {
                    "id": "replication",
                    "decision": "accepted",
                    "origin": "explicit",
                    "necessity": "required",
                    "requirementIds": ["NFR-1"],
                    "evidenceSpans": ["two replicas"],
                    "typedConstraints": [
                        {
                            "kind": "replicaCount",
                            "workloadRefs": ["web"],
                            "value": 2,
                        }
                    ],
                }
            ]
        }
    )
    model = normalized(graph(workload("web", public=True, safety="interchangeable")), facts)
    plan = build_deployment_plan(model)

    assert plan["computeUnits"][0]["kind"] == "managedVmGroup"
    assert plan["computeUnits"][0]["replicaCount"] == 2


def test_bundle_hydrates_as_the_only_supported_deployment_schema() -> None:
    bundle = build_deployment_diagram_bundle(
        graph(workload("web", public=True)),
        {"schemaVersion": "4", "workloads": ["vm"], "provider": "aws", "region": "r1"},
    )
    assert bundle["schemaVersion"] == "easydep-deployment-diagram"

    hydrated = hydrate_deployment_diagram_bundle(bundle)
    assert hydrated["deployment_workload_graph"] == bundle["workloadGraph"]

    with pytest.raises(ValueError, match="unsupported deployment diagram schema"):
        hydrate_deployment_diagram_bundle({"schemaVersion": "unsupported"})


def test_workload_graph_schema_rejects_duplicate_deployment_node_ids() -> None:
    duplicate = workload("application")
    with pytest.raises(ValueError, match="globally unique; duplicates: application"):
        WorkloadGraphProposal.model_validate(
            graph(duplicate, deepcopy(duplicate))
        )


def test_invalid_workload_graph_becomes_reviewable_bundle_without_planning() -> None:
    duplicate = workload("application")
    bundle = build_deployment_diagram_bundle(
        graph(duplicate, deepcopy(duplicate)),
        {"schemaVersion": "4", "workloads": ["vm"], "provider": "aws", "region": "r1"},
    )

    projection = bundle["projections"][0]
    assert bundle["status"] == "needsInput"
    assert projection["status"] == "needsInput"
    assert projection["deploymentPlan"] == {}
    assert projection["resourcePlan"] == {}
    assert "Duplicate ids: application." in projection["issues"][0]["reason"]

    finalized = CloudDesignAdapter().finalize(
        requirements_result={}, design_result={"deployment_diagram_bundle": bundle}
    )
    assert finalized["status"] == "needsInput"
    assert finalized["reason"] == "deployment-diagram-needs-input"


def test_cloud_adapter_passes_current_bundle_and_blocks_unknown_schema() -> None:
    bundle = build_deployment_diagram_bundle(
        graph(workload("web", public=True)),
        {"schemaVersion": "4", "workloads": ["vm"], "provider": "aws", "region": "r1"},
    )
    completed = CloudDesignAdapter().finalize(
        requirements_result={}, design_result={"deployment_diagram_bundle": bundle}
    )
    assert completed["status"] == "completed"
    assert completed["resource_plan"]["schemaVersion"] == "easydep-resource-plan"

    blocked = CloudDesignAdapter().finalize(
        requirements_result={},
        design_result={
            "deployment_diagram_bundle": {
                "schemaVersion": "unsupported",
            }
        },
    )
    assert blocked["status"] == "needsRegeneration"


def test_deployment_subgraph_finalizer_carries_structured_upstream_models() -> None:
    result = _finalize_deployment_diagram(
        {
            "deployment_diagram_model": graph(workload("web", public=True)),
            "resource_spec": {
                "schemaVersion": "4",
                "workloads": ["vm"],
                "provider": "aws",
                "region": "r1",
            },
            "refined_requirements": [{"id": "FR-1", "text": "serve requests"}],
            "capability_contract": {"schemaVersion": "CapabilityContract", "capabilities": []},
            "resource_intake": {"provenance": []},
            "extracted_bce_classes": {"Classes": [{"className": "WebControl"}]},
            "sequence_diagram_model": {"Diagrams": []},
            "api_spec": {"paths": {"/health": {"get": {}}}},
            "erd_bce_classes": {},
        }
    )

    assert result["deployment_diagram_bundle"]["schemaVersion"] == (
        "easydep-deployment-diagram"
    )
    facts = result["deployment_diagram_bundle"]["planningFacts"]
    assert {item["artifact"] for item in facts["inputArtifacts"]} >= {
        "capabilityContract",
        "classModel",
        "sequenceModel",
        "apiSpec",
        "erdModel",
    }
