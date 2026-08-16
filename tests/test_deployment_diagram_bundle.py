import re
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

import app.design.graphs.subgraphs as subgraphs
from app.artifacts_api import (
    ImportRequest,
    get_deployment_diagram_view_image,
    import_stage_content,
)
from app.core.orchestration.provider_deployment import (
    validate_resource_plan_structure,
)
from app.design.graphs.subgraphs import _finalize_deployment_diagram
from app.design.services.deployment_diagram.bundle import (
    build_deployment_diagram_bundle,
    hydrate_deployment_diagram_bundle,
)
from app.design.services.deployment_diagram.provider_plantuml import (
    _PROVISIONING_RELATIONSHIPS,
    deployment_bundle_provisioning_puml,
    deployment_bundle_runtime_puml,
)
from app.design.services.deployment_diagram.topology import (
    enumerate_topology_families,
)
from scripts.generate_deployment_diagram_examples import (
    OUTPUT_ROOT,
    _expected_files,
    _relative_sources,
)
from scripts.generate_deployment_diagram_examples import (
    _logical_model as example_logical_model,
)
from scripts.generate_deployment_diagram_examples import (
    _resource_spec as example_resource_spec,
)

APP_ID = "11111111-1111-4111-8111-111111111111"


def _logical_model() -> dict:
    return {
        "Nodes": [
            {
                "name": "Application Runtime",
                "kind": "executionEnvironment",
                "source_classes": ["Application"],
            }
        ],
        "Artifacts": [
            {
                "name": "application-image",
                "deployed_on": "Application Runtime",
                "source_classes": ["Application"],
            }
        ],
        "Connections": [],
    }


def _resource_spec() -> dict:
    return {
        "schemaVersion": "3",
        "workloads": ["vm"],
        "provider": "aws",
        "region": "ap-northeast-2",
        "deploymentTargets": [
            {
                "provider": "aws",
                "region": "ap-northeast-2",
                "zones": ["ap-northeast-2a"],
            }
        ],
        "computeProfile": "standaloneOne",
        "replicaCount": 1,
        "publicIngress": "direct",
        "databasePlacement": "none",
    }


def test_minimal_aws_bundle_projects_runtime_and_creation_views() -> None:
    bundle = build_deployment_diagram_bundle(_logical_model(), _resource_spec())
    projection = bundle["projections"][0]

    assert projection["status"] == "completed"
    assert projection["topology"]["familyId"] == "aws.standaloneOne.none.direct"
    runtime = deployment_bundle_runtime_puml(bundle)
    provisioning = deployment_bundle_provisioning_puml(bundle)
    assert 'cloud "AWS"' in runtime
    assert 'node "VPC"' in runtime
    assert 'node "Application Subnet\\nap-northeast-2a"' in runtime
    assert 'node "EC2 Instance\\n1 standalone instance"' in runtime
    assert 'node "Elastic IP"' in runtime
    assert "Internet client -" not in runtime  # aliases, not display text, form edges
    assert "internet_client -[#2f6b50]-> public_endpoint : HTTP" in runtime
    assert "provision_network -[#6f7780,dashed]-> provision_subnet" in provisioning
    assert "provision_subnet -[#6f7780,dashed]-> provision_compute_instance" in provisioning
    assert "Runtime traffic is intentionally omitted." in provisioning


def test_finalizer_keeps_editable_logical_model_and_resource_plan_together() -> None:
    state = {
        "deployment_diagram_model": _logical_model(),
        "resource_spec": _resource_spec(),
    }
    result = _finalize_deployment_diagram(state)

    assert result["deployment_diagram_model"] == _logical_model()
    assert result["deployment_diagram_bundle"]["logicalModel"] == _logical_model()
    assert result["deployment_resource_plan"]["provider"] == "aws"
    assert result["deployment_topology"]["publicIngress"] == "direct"
    assert "prerequisite -> dependent" in result["deployment_diagram_provisioning_puml"]


def test_bundle_hydration_supports_legacy_logical_only_artifacts() -> None:
    hydrated = hydrate_deployment_diagram_bundle(_logical_model())

    assert hydrated["deployment_diagram_model"] == _logical_model()
    assert hydrated["deployment_diagram_bundle"]["mode"] == "legacyLogicalOnly"


def test_checked_in_example_corpus_is_complete_and_matches_the_renderer() -> None:
    sources = _relative_sources()
    assert len(sources) == 72
    assert sources == _relative_sources()

    actual = {
        path.relative_to(OUTPUT_ROOT)
        for path in OUTPUT_ROOT.rglob("*")
        if path.is_file()
    }
    assert actual == _expected_files(sources)
    for relative, expected in sources.items():
        assert (OUTPUT_ROOT / relative).read_text(encoding="utf-8") == expected


def test_colocated_database_renders_inside_the_application_compute() -> None:
    logical = _logical_model()
    logical["Nodes"].append(
        {
            "name": "PostgreSQL",
            "kind": "database",
            "source_classes": ["Database"],
        }
    )
    logical["Connections"].append(
        {
            "source": "Application Runtime",
            "target": "PostgreSQL",
            "protocol": "PostgreSQL protocol",
        }
    )
    spec = {**_resource_spec(), "databasePlacement": "colocated"}
    runtime = deployment_bundle_runtime_puml(
        build_deployment_diagram_bundle(logical, spec)
    )

    assert 'database "PostgreSQL\\n<<Docker container>>"' in runtime
    assert "compute_compute_postgresql" not in runtime
    assert (
        "primary_compute ..[#6f7c73]> persistent_disk : attached block device"
        in runtime
    )


def test_provider_examples_expose_subnet_quantity_and_address_creation_order() -> None:
    sources = _relative_sources()
    aws_runtime = sources[
        Path("aws/standaloneOne.none.loadBalanced.runtime.puml")
    ]
    aws_provisioning = sources[
        Path("aws/standaloneOne.none.loadBalanced.provisioning.puml")
    ]
    gcp_provisioning = sources[
        Path("gcp/standaloneOne.none.loadBalanced.provisioning.puml")
    ]

    assert "Ingress Subnet 1\\nap-northeast-2a" in aws_runtime
    assert "Ingress Subnet 2\\nap-northeast-2b" in aws_runtime
    assert "Ingress Subnet 1\\nap-northeast-2a" in aws_provisioning
    assert "Ingress Subnet 2\\nap-northeast-2b" in aws_provisioning
    assert (
        "provision_ingress_subnet_1 -[#6f7780,dashed]-> provision_load_balancer"
        in aws_provisioning
    )
    assert (
        "provision_ingress_subnet_2 -[#6f7780,dashed]-> provision_load_balancer"
        in aws_provisioning
    )
    assert (
        "provision_public_ip -[#6f7780,dashed]-> provision_forwarding_rule "
        ": assign address"
    ) in gcp_provisioning
    azure_runtime = sources[
        Path("azure/standaloneOne.none.loadBalanced.runtime.puml")
    ]
    assert "Application Gateway Subnet\\n<<dedicated>>" in azure_runtime
    assert (
        "public_endpoint -[#2f6b50]-> public_address : accepts HTTP"
        in azure_runtime
    )
    assert (
        "public_address -[#2f6b50]-> ingress_frontend_ip_config : assigned frontend address"
        in azure_runtime
    )
    gcp_direct = sources[Path("gcp/standaloneOne.none.direct.provisioning.puml")]
    assert (
        "provision_public_ip -[#6f7780,dashed]-> "
        "provision_network_interface : assign address"
    ) in gcp_direct


def test_many_runtime_expands_the_minimum_two_vm_instances_and_flows() -> None:
    sources = _relative_sources()
    multi_zone = sources[
        Path("aws/managedGroupManyMultiZone.dedicated.loadBalanced.runtime.puml")
    ]
    single_zone = sources[
        Path("aws/managedGroupManySingleZone.none.loadBalanced.runtime.puml")
    ]

    assert 'node "EC2 Instance 1\\nap-northeast-2a"' in multi_zone
    assert 'node "EC2 Instance 2\\nap-northeast-2b"' in multi_zone
    assert "runtime_workload_application_runtime_replica_1" in multi_zone
    assert "runtime_workload_application_runtime_replica_2" in multi_zone
    assert multi_zone.count("forwards to replica") == 2
    assert multi_zone.count("PostgreSQL protocol") == 2
    assert 'node "EC2 Instance 1\\nap-northeast-2a"' in single_zone
    assert 'node "EC2 Instance 2\\nap-northeast-2a"' in single_zone


def test_provisioning_view_excludes_runtime_only_firewall_reachability() -> None:
    sources = _relative_sources()
    gcp = sources[
        Path("gcp/managedGroupManyMultiZone.dedicated.loadBalanced.provisioning.puml")
    ]

    assert "is reached through" not in gcp
    assert "allows traffic to" not in gcp


def test_database_free_families_never_project_an_independent_data_disk() -> None:
    sources = _relative_sources()
    for provider in ("aws", "azure", "gcp"):
        for relative, source in sources.items():
            if (
                relative.parts[0] == provider
                and ".none." in relative.name
            ):
                assert "Persistent Disk" not in source
                assert "EBS Volume" not in source
                assert "Managed Disk" not in source


def test_every_provider_relationship_is_audited_before_visualization() -> None:
    functional_only = {"allows traffic to", "is reached through"}
    observed: set[str] = set()
    for family in enumerate_topology_families(include_providers=True):
        plan = build_deployment_diagram_bundle(
            example_logical_model(family.database_placement),
            example_resource_spec(family),
        )["projections"][0]["resourcePlan"]
        provider_nodes = {
            str(node.get("id") or "")
            for node in plan["nodes"]
            if node.get("entityClass")
            in {"providerResource", "providerComponent", "externalArtifact"}
        }
        observed.update(
            str(edge.get("label") or "")
            for edge in plan["edges"]
            if edge.get("from") in provider_nodes and edge.get("to") in provider_nodes
        )

    assert observed - _PROVISIONING_RELATIONSHIPS == functional_only


def test_every_provider_creation_node_participates_in_provisioning_graph() -> None:
    for family in enumerate_topology_families(include_providers=True):
        plan = build_deployment_diagram_bundle(
            example_logical_model(family.database_placement),
            example_resource_spec(family),
        )["projections"][0]["resourcePlan"]
        provider_nodes = {
            str(node.get("id") or "")
            for node in plan["nodes"]
            if node.get("entityClass")
            in {"providerResource", "providerComponent", "externalArtifact"}
        }
        connected: set[str] = set()
        for edge in plan["edges"]:
            source = str(edge.get("from") or "")
            target = str(edge.get("to") or "")
            if (
                source in provider_nodes
                and target in provider_nodes
                and str(edge.get("label") or "") in _PROVISIONING_RELATIONSHIPS
            ):
                connected.update((source, target))

        assert provider_nodes - connected == set(), family.id


def test_every_rendered_provisioning_node_has_an_arrow() -> None:
    node_pattern = re.compile(r'^node ".*" as ([A-Za-z0-9_]+)', re.MULTILINE)
    edge_pattern = re.compile(
        r"^([A-Za-z0-9_]+) -\[[^\n]*\]-> ([A-Za-z0-9_]+)",
        re.MULTILINE,
    )
    for relative, source in _relative_sources().items():
        if not relative.name.endswith(".provisioning.puml"):
            continue
        nodes = set(node_pattern.findall(source))
        connected = {
            endpoint
            for edge in edge_pattern.findall(source)
            for endpoint in edge
        }
        assert nodes - connected == set(), str(relative)


def test_every_rendered_edge_uses_declared_aliases() -> None:
    declaration_pattern = re.compile(
        r'^\s*(?:actor|artifact|cloud|component|database|frame|interface|node) '
        r'".*" as ([A-Za-z0-9_]+)',
        re.MULTILINE,
    )
    edge_pattern = re.compile(
        r"^([A-Za-z0-9_]+) (?:-|\.\.)\[[^\n]*\](?:-|\.)?> "
        r"([A-Za-z0-9_]+)",
        re.MULTILINE,
    )
    for relative, source in _relative_sources().items():
        declared = set(declaration_pattern.findall(source))
        used = {
            endpoint
            for edge in edge_pattern.findall(source)
            for endpoint in edge
        }
        assert used - declared == set(), f"{relative}: {sorted(used - declared)}"


def test_topology_profile_cannot_be_replaced_by_a_load_balancer_component() -> None:
    for family in enumerate_topology_families(include_providers=True):
        plan = build_deployment_diagram_bundle(
            example_logical_model(family.database_placement),
            example_resource_spec(family),
        )["projections"][0]["resourcePlan"]
        expected = (
            "compute-group"
            if family.compute_profile.startswith("managedGroup")
            else "compute-instance"
        )
        assert plan["computeNodeId"] == expected, family.id


def test_gcp_load_balancer_uses_the_selected_compute_topology() -> None:
    families = {
        family.id: family
        for family in enumerate_topology_families(include_providers=True)
    }
    standalone = build_deployment_diagram_bundle(
        example_logical_model("dedicated"),
        example_resource_spec(
            families["gcp.standaloneOne.dedicated.loadBalanced"]
        ),
    )["projections"][0]["resourcePlan"]
    managed = build_deployment_diagram_bundle(
        example_logical_model("dedicated"),
        example_resource_spec(
            families["gcp.managedGroupOne.dedicated.loadBalanced"]
        ),
    )["projections"][0]["resourcePlan"]

    standalone_nodes = {str(node["id"]): node for node in standalone["nodes"]}
    managed_nodes = {str(node["id"]): node for node in managed["nodes"]}
    standalone_edges = {
        (str(edge["from"]), str(edge["to"]), str(edge["label"]))
        for edge in standalone["edges"]
    }
    managed_edges = {
        (str(edge["from"]), str(edge["to"]), str(edge["label"]))
        for edge in managed["edges"]
    }

    assert standalone_nodes["compute-instance"]["name"] == "Compute Engine VM"
    assert standalone_nodes["backend-group"]["name"] == "Unmanaged Instance Group"
    assert "compute-group" not in standalone_nodes
    assert ("backend-group", "compute-instance", "contains instance") in standalone_edges
    assert managed_nodes["compute-group"]["name"] == "Zonal Managed Instance Group"
    assert "backend-group" not in managed_nodes
    assert ("backend-service", "compute-group", "uses backend") in managed_edges
    assert ("firewall", "network", "belongs to") in managed_edges


def test_runtime_views_preserve_provider_native_load_balancer_chains() -> None:
    sources = _relative_sources()
    aws = sources[Path("aws/standaloneOne.none.loadBalanced.runtime.puml")]
    azure = sources[Path("azure/standaloneOne.none.loadBalanced.runtime.puml")]
    gcp = sources[Path("gcp/standaloneOne.none.loadBalanced.runtime.puml")]

    assert "public_ingress -[#2f6b50]-> ingress_listener" in aws
    assert "ingress_listener -[#2f6b50]-> ingress_backend_group" in aws
    assert "ingress_backend_group -[#2f6b50]-> runtime_workload" in aws
    assert "public_address -[#2f6b50]-> ingress_frontend_ip_config" in azure
    assert "ingress_frontend_ip_config -[#2f6b50]-> ingress_listener" in azure
    assert "ingress_frontend_port ..[#8a6d3b]> ingress_listener" in azure
    assert "ingress_listener -[#2f6b50]-> ingress_routing_rule" in azure
    assert "ingress_routing_rule -[#2f6b50]-> ingress_backend_group" in azure
    assert "public_ingress -[#2f6b50]-> ingress_target_proxy" in gcp
    assert "ingress_target_proxy -[#2f6b50]-> ingress_url_map" in gcp
    assert "ingress_url_map -[#2f6b50]-> ingress_backend_service" in gcp
    assert "ingress_backend_service -[#2f6b50]-> ingress_backend_group" in gcp
    assert "Global Forwarding Rule\\n<<global>>" in gcp
    assert "VPC Network\\n<<global>>" in gcp


def test_load_balanced_http_plan_has_no_tls_resources() -> None:
    for family in enumerate_topology_families(include_providers=True):
        if family.public_ingress != "loadBalanced":
            continue
        plan = build_deployment_diagram_bundle(
            example_logical_model(family.database_placement),
            example_resource_spec(family),
        )["projections"][0]["resourcePlan"]
        kinds = {node.get("providerKind") for node in plan["nodes"]}
        assert "certificate" not in kinds, family.id
        assert "target-https-proxy" not in kinds, family.id
        endpoint = next(node for node in plan["nodes"] if node.get("group") == "endpoint")
        assert endpoint["protocol"] == "http", family.id
        assert "tlsTermination" not in endpoint, family.id


def test_load_balanced_provisioning_includes_explicit_egress_path() -> None:
    sources = _relative_sources()
    aws = sources[Path("aws/standaloneOne.none.loadBalanced.provisioning.puml")]
    azure = sources[Path("azure/standaloneOne.none.loadBalanced.provisioning.puml")]
    gcp = sources[Path("gcp/standaloneOne.none.loadBalanced.provisioning.puml")]

    for label in (
        "Internet Gateway",
        "NAT Gateway",
        "Elastic IP / NAT Gateway",
        "Route Table / application",
        "Route Table / ingress",
    ):
        assert label in aws
    for label in (
        "Application Gateway Subnet",
        "NAT Gateway",
        "Public IP / NAT Gateway",
        "azurerm_subnet_nat_gateway_association",
    ):
        assert label in azure
    assert "Cloud Router" in gcp
    assert "Cloud NAT" in gcp


def test_every_direct_runtime_view_has_an_end_to_end_request_path() -> None:
    for relative, source in _relative_sources().items():
        if not relative.name.endswith(".direct.runtime.puml"):
            continue
        direct_to_workload = re.search(
            r"public_address -\[#2f6b50\]-> runtime_workload[^\n]* : HTTP",
            source,
        )
        assert direct_to_workload, str(relative)


def test_resource_plan_structure_rejects_dangling_and_disconnected_nodes() -> None:
    family = next(
        item
        for item in enumerate_topology_families(include_providers=True)
        if item.id == "aws.standaloneOne.none.direct"
    )
    plan = build_deployment_diagram_bundle(
        example_logical_model(family.database_placement),
        example_resource_spec(family),
    )["projections"][0]["resourcePlan"]

    dangling = deepcopy(plan)
    dangling["edges"][0]["to"] = "missing-node"
    with pytest.raises(ValueError, match="dangling endpoint"):
        validate_resource_plan_structure(dangling)

    disconnected = deepcopy(plan)
    disconnected["nodes"].append(
        {
            "id": "orphan-resource",
            "entityClass": "providerResource",
            "handling": "create",
            "terraformTypes": ["aws_s3_bucket"],
        }
    )
    with pytest.raises(ValueError, match="disconnected provider nodes"):
        validate_resource_plan_structure(disconnected)


def test_provisioning_labels_read_in_prerequisite_to_dependent_direction() -> None:
    for relative, source in _relative_sources().items():
        if not relative.name.endswith(".provisioning.puml"):
            continue
        assert " : forwards to" not in source, str(relative)
        assert " : routes to" not in source, str(relative)
        assert " : uses backend" not in source, str(relative)


def test_provider_nodes_have_executable_or_owned_handling() -> None:
    for family in enumerate_topology_families(include_providers=True):
        plan = build_deployment_diagram_bundle(
            example_logical_model(family.database_placement),
            example_resource_spec(family),
        )["projections"][0]["resourcePlan"]
        ids = {str(node["id"]) for node in plan["nodes"]}
        for node in plan["nodes"]:
            if node.get("handling") == "create":
                assert node.get("terraformTypes"), (family.id, node["id"])
            if node.get("handling") == "configureInsideOwner":
                assert node.get("ownerRef") in ids, (family.id, node["id"])


def test_managed_network_ownership_and_egress_edges_match_provider_models() -> None:
    families = {
        family.id: family
        for family in enumerate_topology_families(include_providers=True)
    }
    plans = {}
    for provider in ("aws", "azure", "gcp"):
        family = families[f"{provider}.managedGroupOne.none.loadBalanced"]
        plans[provider] = build_deployment_diagram_bundle(
            example_logical_model(family.database_placement),
            example_resource_spec(family),
        )["projections"][0]["resourcePlan"]

    aws_edges = {
        (edge["from"], edge["to"], edge["label"])
        for edge in plans["aws"]["edges"]
    }
    assert ("compute-template", "security-group", "uses") in aws_edges
    assert not any(
        source == "compute-group" and target == "security-group"
        for source, target, _label in aws_edges
    )

    azure_nic = next(
        node for node in plans["azure"]["nodes"] if node["id"] == "network-interface"
    )
    assert azure_nic["handling"] == "configureInsideOwner"
    assert azure_nic["ownerRef"] == "compute-group"
    assert azure_nic["terraformTypes"] == []

    gcp_edges = {
        (edge["from"], edge["to"], edge["label"])
        for edge in plans["gcp"]["edges"]
    }
    assert ("cloud-router", "network", "belongs to") in gcp_edges
    assert ("cloud-nat", "cloud-router", "uses") in gcp_edges
    assert ("cloud-nat", "subnet", "selects subnetwork") in gcp_edges
    assert not any(
        source == "cloud-router" and target == "subnet"
        for source, target, _label in gcp_edges
    )


def test_load_balanced_http_needs_no_tls_inputs() -> None:
    family = next(
        item
        for item in enumerate_topology_families(include_providers=True)
        if item.id == "aws.managedGroupOne.none.loadBalanced"
    )
    resource_spec = example_resource_spec(family)
    resource_spec["tls"] = {}
    projection = build_deployment_diagram_bundle(
        example_logical_model(family.database_placement),
        resource_spec,
    )["projections"][0]

    assert projection["status"] == "completed"
    assert projection["resourcePlan"]["unresolved"] == []
    runtime = deployment_bundle_runtime_puml(
        {
            "schemaVersion": "easydep-deployment-diagram/v1",
            "projections": [projection],
            "logicalModel": example_logical_model(family.database_placement),
        }
    )
    assert "HTTP" in runtime
    assert "certificate" not in runtime.lower()


def test_deployment_view_endpoint_renders_requested_semantic_view() -> None:
    with (
        patch(
            "app.artifacts_api.require_app",
            return_value={"deployment_diagram_provisioning_puml": "@startuml\n@enduml"},
        ),
        patch("app.artifacts_api.render_plantuml", return_value=b"<svg />") as render,
    ):
        response = get_deployment_diagram_view_image(
            APP_ID, "provisioning", "svg"
        )

    assert response.body == b"<svg />"
    assert response.media_type == "image/svg+xml"
    render.assert_called_once_with("@startuml\n@enduml", "svg")


def test_design_generation_subgraph_emits_provider_native_diagram(monkeypatch) -> None:
    monkeypatch.setattr(
        subgraphs,
        "extract_deployment_model",
        lambda *_args, **_kwargs: _logical_model(),
    )
    monkeypatch.setattr(
        "app.design.services.common.validation.check_plantuml_syntax",
        lambda _source: [],
    )

    result = subgraphs.DESIGN_SUBGRAPHS["deployment_diagram"]["generate"].invoke(
        {
            "usecase_spec": {"use_cases": [{"id": "UC-1", "name": "Run app"}]},
            "class_diagram_puml": "@startuml\n@enduml",
            "sequence_diagram_puml": "@startuml\n@enduml",
            "api_spec": {"openapi": "3.1.0", "paths": {}},
            "erd_puml": "@startuml\n@enduml",
            "resource_spec": _resource_spec(),
        }
    )

    assert result["deployment_diagram_bundle"]["schemaVersion"] == (
        "easydep-deployment-diagram/v1"
    )
    assert result["deployment_resource_plan"]["provider"] == "aws"
    assert 'cloud "AWS"' in result["deployment_diagram_puml"]
    assert "Every arrow means prerequisite -> dependent." in (
        result["deployment_diagram_provisioning_puml"]
    )


def test_imported_deployment_bundle_uses_state_aware_derivations() -> None:
    bundle = build_deployment_diagram_bundle(_logical_model(), _resource_spec())
    with (
        patch("app.artifacts_api.require_app_exists"),
        patch("app.artifacts_api.require_app", return_value={}),
        patch("app.artifacts_api.validate_puml_artifact", return_value={
            "syntax_valid": True,
            "syntax_errors": [],
        }),
        patch("app.artifacts_api.artifact_repository.save_stage", return_value=1) as save,
    ):
        import_stage_content(
            APP_ID,
            "deployment_diagram",
            ImportRequest(content=bundle),
        )

    saved_state = save.call_args.args[2]
    assert saved_state["deployment_diagram_bundle"] == bundle
    assert saved_state["deployment_diagram_model"] == _logical_model()
    assert 'cloud "AWS"' in saved_state["deployment_diagram_puml"]
