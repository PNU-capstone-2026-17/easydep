from __future__ import annotations

import copy
import io

import hcl2
import pytest

from app.core.orchestration.iac_renderer import (
    render_open_tofu,
    rendered_resource_types,
)
from app.design.services.deployment_diagram.bundle import build_deployment_diagram_bundle
from app.design.services.deployment_diagram.provider_plantuml import (
    deployment_bundle_provisioning_puml,
    deployment_bundle_runtime_puml,
)
from app.design.services.deployment_diagram.provider_template import (
    validate_complete_provider_template,
)
from scripts.generate_deployment_diagram_examples import (
    CASE_EXPECTATIONS,
    DEPLOYMENT_CASES,
    TARGETS,
    _graph,
    _resource_spec,
    semantic_case_id,
)


def _case_id(
    compute_kind: str,
    compute_units: int,
    replicas: int,
    zones: int,
    workload_count: int,
    persistent_workload_count: int,
    ingress_kind: str,
    *,
    colocate_relation_count: int = 0,
    separate_relation_count: int = 0,
) -> str:
    return semantic_case_id(
        compute_kind=compute_kind,
        compute_units=compute_units,
        replicas=replicas,
        zones=zones,
        workload_count=workload_count,
        persistent_workload_count=persistent_workload_count,
        colocate_relation_count=colocate_relation_count,
        separate_relation_count=separate_relation_count,
        ingress_kind=ingress_kind,
    )


STANDALONE_PRIMARY_PUBLIC = _case_id(
    "standaloneVm", 1, 1, 1, 1, 0, "directPublicIp"
)
STANDALONE_DEFAULT_TWO_WORKLOADS_ONE_PERSISTENT = _case_id(
    "standaloneVm", 1, 1, 1, 2, 1, "directPublicIp"
)
STANDALONE_SEPARATE_TWO_WORKLOADS_ONE_PERSISTENT = _case_id(
    "standaloneVm",
    2,
    1,
    1,
    2,
    1,
    "directPublicIp",
    separate_relation_count=1,
)
STANDALONE_SEPARATED_PUBLIC = _case_id(
    "standaloneVm",
    2,
    1,
    1,
    2,
    0,
    "directPublicIp",
    separate_relation_count=1,
)
MANAGED_ONE_PRIMARY_PUBLIC = _case_id(
    "managedVmGroup", 1, 1, 1, 1, 0, "loadBalancer"
)
MANAGED_TWO_ZONE_PRIMARY_PUBLIC = _case_id(
    "managedVmGroup", 1, 2, 2, 1, 0, "loadBalancer"
)


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
@pytest.mark.parametrize("case", DEPLOYMENT_CASES)
def test_all_decision_templates_have_exact_parseable_iac(provider: str, case: str) -> None:
    bundle = build_deployment_diagram_bundle(_graph(case), _resource_spec(provider))
    projection = bundle["projections"][0]
    resource_plan = projection["resourcePlan"]
    files = render_open_tofu(resource_plan)

    expected_types = sorted(
        terraform_type
        for node in resource_plan["nodes"]
        if node["handling"] == "create"
        for terraform_type in node["terraformTypes"]
    )
    assert rendered_resource_types(files) == expected_types
    assert all(node["templateRuleId"] and node["sourceRefs"] for node in resource_plan["nodes"])
    assert all(
        reference["consumerRef"]
        and reference["consumerPath"]
        and reference["producerRef"]
        and reference["producerAttribute"]
        and reference["cardinality"] in {"one", "many"}
        for reference in resource_plan["references"]
    )
    assert "edges" not in resource_plan
    assert all("bindingKind" not in item for item in resource_plan["references"])
    for name, content in files.items():
        if name.endswith(".tf"):
            assert hcl2.load(io.StringIO(content)) is not None


@pytest.mark.parametrize(
    ("provider", "required_types"),
    [
        (
            "aws",
            {
                "aws_vpc",
                "aws_internet_gateway",
                "aws_subnet",
                "aws_route_table",
                "aws_route",
                "aws_route_table_association",
                "aws_security_group",
                "aws_instance",
                "aws_eip",
                "aws_ecr_repository",
                "aws_iam_role",
                "aws_iam_instance_profile",
                "aws_iam_role_policy_attachment",
            },
        ),
        (
            "azure",
            {
                "azurerm_resource_group",
                "azurerm_virtual_network",
                "azurerm_subnet",
                "azurerm_network_security_group",
                "azurerm_network_interface",
                "azurerm_linux_virtual_machine",
                "azurerm_public_ip",
                "azurerm_container_registry",
                "azurerm_user_assigned_identity",
                "azurerm_role_assignment",
            },
        ),
        (
            "gcp",
            {
                "google_compute_network",
                "google_compute_subnetwork",
                "google_compute_firewall",
                "google_compute_instance",
                "google_compute_address",
                "google_artifact_registry_repository",
                "google_service_account",
                "google_artifact_registry_repository_iam_member",
            },
        ),
    ],
)
def test_public_single_template_contains_provider_execution_closure(
    provider: str, required_types: set[str]
) -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(STANDALONE_PRIMARY_PUBLIC), _resource_spec(provider)
    )
    resource_plan = bundle["projections"][0]["resourcePlan"]
    actual = {
        item
        for node in resource_plan["nodes"]
        for item in node.get("terraformTypes") or []
    }
    assert required_types <= actual

    runtime = deployment_bundle_runtime_puml(bundle)
    provisioning = deployment_bundle_provisioning_puml(bundle)
    assert "immutable digest" in runtime
    assert "Docker container" in runtime
    assert "class:" not in runtime
    assert "Arrow: dependent -> prerequisite." in provisioning
    assert "class:" not in provisioning


def test_group_and_persistent_templates_include_lifecycle_closures() -> None:
    group = build_deployment_diagram_bundle(
        _graph(MANAGED_TWO_ZONE_PRIMARY_PUBLIC), _resource_spec("aws")
    )
    group_types = {
        item
        for node in group["projections"][0]["resourcePlan"]["nodes"]
        for item in node.get("terraformTypes") or []
    }
    assert {
        "aws_launch_template",
        "aws_autoscaling_group",
        "aws_lb",
        "aws_lb_listener",
        "aws_lb_target_group",
        "aws_nat_gateway",
    } <= group_types

    data = build_deployment_diagram_bundle(
        _graph(STANDALONE_DEFAULT_TWO_WORKLOADS_ONE_PERSISTENT), _resource_spec("gcp")
    )
    data_types = {
        item
        for node in data["projections"][0]["resourcePlan"]["nodes"]
        for item in node.get("terraformTypes") or []
    }
    assert {"google_compute_disk", "google_compute_attached_disk"} <= data_types

    runtime = deployment_bundle_runtime_puml(data)
    assert "Prebuilt image dependencies" in runtime
    assert "explicit prebuilt image" in runtime
    assert "PostgreSQL" not in runtime
    assert "POSTGRES_" not in runtime


@pytest.mark.parametrize(
    ("provider", "consumer_path", "producer_prefix"),
    [
        ("aws", "availability_zone", "subnet-compute-1-"),
        ("azure", "zone", "compute-1"),
        ("gcp", "zone", "compute-1"),
    ],
)
def test_singleton_disk_zone_is_a_compute_placement_reference(
    provider: str, consumer_path: str, producer_prefix: str,
) -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(STANDALONE_DEFAULT_TWO_WORKLOADS_ONE_PERSISTENT),
        _resource_spec(provider),
    )
    resource_plan = bundle["projections"][0]["resourcePlan"]
    disk = next(
        item for item in resource_plan["nodes"]
        if item["id"].startswith("data-disk-")
    )
    zone_reference = next(
        item for item in resource_plan["references"]
        if item["consumerRef"] == disk["id"]
        and item["consumerPath"] == consumer_path
    )

    assert zone_reference["producerRef"].startswith(producer_prefix)
    assert zone_reference["producerAttribute"] in {"availability_zone", "zone"}


@pytest.mark.parametrize(
    ("provider", "disk_name"),
    [
        ("aws", "EBS Volume"),
        ("azure", "Managed Disk"),
        ("gcp", "Persistent Disk"),
    ],
)
def test_runtime_shapes_distinguish_workloads_from_storage(
    provider: str, disk_name: str
) -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(STANDALONE_DEFAULT_TWO_WORKLOADS_ONE_PERSISTENT),
        _resource_spec(provider),
    )
    runtime = deployment_bundle_runtime_puml(bundle)

    assert 'component "State Service\\n<<Docker container>>' in runtime
    assert "[mount] /var/lib/easydep/state" in runtime
    assert 'database "State Service' not in runtime
    assert f'database "{disk_name}' in runtime


@pytest.mark.parametrize(
    ("provider", "role_aliases"),
    [
        (
            "aws",
            {"ingress_listener", "ingress_backend_group", "ingress_health_check"},
        ),
        (
            "azure",
            {
                "ingress_frontend_ip_config",
                "ingress_routing_rule",
                "ingress_backend_group",
                "ingress_health_check",
            },
        ),
        (
            "gcp",
            {
                "ingress_backend_group",
                "ingress_backend_service",
                "ingress_health_check",
            },
        ),
    ],
)
def test_runtime_uses_one_shape_for_provider_configuration_roles(
    provider: str, role_aliases: set[str]
) -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(MANAGED_ONE_PRIMARY_PUBLIC), _resource_spec(provider)
    )
    runtime = deployment_bundle_runtime_puml(bundle)
    lines = runtime.splitlines()

    for alias in role_aliases:
        role_line = next(line for line in lines if f" as {alias}" in line)
        assert role_line.strip().startswith("rectangle ")
    assert any(
        line.strip().startswith("rectangle ") and "<<traffic policy>>" in line
        for line in lines
    )


@pytest.mark.parametrize(
    ("provider", "dependent_alias", "prerequisite_alias"),
    [
        (
            "aws",
            "provision_listener_compute_1_1",
            "provision_load_balancer_compute_1",
        ),
        (
            "azure",
            "provision_routing_rule_compute_1_1",
            "provision_load_balancer_compute_1",
        ),
        (
            "gcp",
            "provision_load_balancer_compute_1",
            "provision_backend_service_compute_1_1",
        ),
    ],
)
def test_provisioning_edges_keep_reference_direction_without_assignment_labels(
    provider: str,
    dependent_alias: str,
    prerequisite_alias: str,
) -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(MANAGED_ONE_PRIMARY_PUBLIC), _resource_spec(provider)
    )
    provisioning = deployment_bundle_provisioning_puml(bundle)

    assert not any(line.endswith(": contains") for line in provisioning.splitlines())
    assert "Arrow: dependent -> prerequisite." in provisioning
    assert " = " not in provisioning
    reference_line = next(
        line
        for line in provisioning.splitlines()
        if line.startswith(f"{dependent_alias} ") and f" {prerequisite_alias}" in line
    )
    assert "->" in reference_line
    assert " : " not in reference_line


def test_duplicate_references_use_only_compact_disambiguation_labels() -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(STANDALONE_PRIMARY_PUBLIC), _resource_spec("aws")
    )
    resource_plan = bundle["projections"][0]["resourcePlan"]
    original = next(
        item
        for item in resource_plan["references"]
        if item["consumerRef"] == "registry-instance-profile-compute-1"
        and item["producerRef"] == "registry-pull-identity-compute-1"
    )
    duplicate = copy.deepcopy(original)
    duplicate["id"] = f'{original["id"]}-secondary'
    duplicate["consumerPath"] = "secondary_role_id"
    resource_plan["references"].append(duplicate)

    provisioning = deployment_bundle_provisioning_puml(bundle)
    matching = [
        line
        for line in provisioning.splitlines()
        if line.startswith("provision_registry_instance_profile_compute_1 ")
        and " provision_registry_pull_identity_compute_1" in line
    ]

    assert len(matching) == 2
    assert any(line.endswith(": identity") for line in matching)
    assert any(line.endswith(": secondary role") for line in matching)
    assert all(" = " not in line for line in matching)


def test_resource_plan_rejects_incomplete_iac_references() -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(MANAGED_ONE_PRIMARY_PUBLIC), _resource_spec("aws")
    )
    resource_plan = copy.deepcopy(bundle["projections"][0]["resourcePlan"])
    resource_plan["references"][0]["cardinality"] = "invalid"
    with pytest.raises(ValueError, match="invalid cardinality"):
        validate_complete_provider_template(resource_plan)


def test_gcp_shared_network_tag_is_one_local_with_two_real_references() -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(STANDALONE_PRIMARY_PUBLIC), _resource_spec("gcp")
    )
    resource_plan = bundle["projections"][0]["resourcePlan"]
    files = render_open_tofu(resource_plan)
    shared = next(item for item in resource_plan["sharedValues"] if item["id"] == "network-tag-compute-1")
    consumers = {
        (item["consumerRef"], item["consumerPath"])
        for item in resource_plan["references"]
        if item["producerRef"] == shared["id"]
    }

    assert consumers == {
        ("compute-1", "tags[]"),
        ("traffic-filter-compute-1", "target_tags[]"),
    }
    assert 'network_tag_compute_1 = "traffic-filter-compute-1"' in files["easydep-locals.tf"]
    assert "tags = [local.network_tag_compute_1]" in files["main.tf"]
    assert "target_tags = [local.network_tag_compute_1]" in files["main.tf"]


@pytest.mark.parametrize(
    ("provider", "block_path"),
    [
        ("aws", "health_check"),
        ("azure", "frontend_ip_configuration"),
        ("gcp", "backend"),
    ],
)
def test_inline_provider_configuration_is_nested_not_a_resource_node(
    provider: str, block_path: str
) -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(MANAGED_ONE_PRIMARY_PUBLIC), _resource_spec(provider)
    )
    resource_plan = bundle["projections"][0]["resourcePlan"]
    provisioning = deployment_bundle_provisioning_puml(bundle)

    block = next(item for item in resource_plan["embeddedBlocks"] if item["blockPath"] == block_path)
    assert block["ownerRef"] in {item["id"] for item in resource_plan["nodes"]}
    assert block["id"] not in {item["id"] for item in resource_plan["nodes"]}
    assert f'as provision_{block["id"].replace("-", "_")} <<inline block>>' in provisioning


def test_terraform_association_is_folded_into_an_undirected_semantic_line() -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(STANDALONE_PRIMARY_PUBLIC), _resource_spec("azure")
    )
    provisioning = deployment_bundle_provisioning_puml(bundle)

    assert "Terraform: azurerm_network_interface_security_group_association" not in provisioning
    assert "<<connection resource>>" not in provisioning
    association_line = next(
        line
        for line in provisioning.splitlines()
        if line.endswith(": applies network security group")
    )
    assert "->" not in association_line
    assert "-[#c47713,dashed]-" in association_line

    resource_plan = copy.deepcopy(bundle["projections"][0]["resourcePlan"])
    resource_plan["references"][0]["consumerPath"] = ""
    with pytest.raises(ValueError, match="reference is incomplete"):
        validate_complete_provider_template(resource_plan)


@pytest.mark.parametrize("case", DEPLOYMENT_CASES)
@pytest.mark.parametrize(
    ("provider", "provider_name"),
    [("aws", "AWS"), ("azure", "Microsoft Azure"), ("gcp", "Google Cloud")],
)
def test_diagrams_match_reference_visual_grammar_without_family_contract(
    case: str, provider: str, provider_name: str
) -> None:
    bundle = build_deployment_diagram_bundle(_graph(case), _resource_spec(provider))
    original_bundle = copy.deepcopy(bundle)
    runtime = deployment_bundle_runtime_puml(bundle)
    provisioning = deployment_bundle_provisioning_puml(bundle)
    region = TARGETS[provider][0]
    assert bundle == original_bundle
    assert f"title Runtime deployment - {provider_name} / {region}" in runtime
    assert f'cloud "{provider_name}" as provider_boundary' in runtime
    assert f'frame "Region: {region}" as region_boundary' in runtime
    assert "<<Docker container>>" in runtime
    assert "|= Line |= Meaning |" in runtime
    assert f"title Provisioning dependencies - {provider_name} / {region}" in provisioning
    assert "Arrow: dependent -> prerequisite." in provisioning
    assert "Terraform attribute" not in provisioning
    assert "class:" not in runtime
    assert "class:" not in provisioning
    assert "Application Subnet\\nApplication" not in provisioning
    assert "Subnetwork\\nApplication" not in provisioning


def test_unified_corpus_covers_all_decision_axes() -> None:
    assert tuple(CASE_EXPECTATIONS) == DEPLOYMENT_CASES
    assert len(DEPLOYMENT_CASES) == 15
    for case, expected in CASE_EXPECTATIONS.items():
        assert case == semantic_case_id(
            compute_kind=expected["computeKind"],
            compute_units=expected["computeUnitCount"],
            replicas=expected["replicaCount"],
            zones=expected["zoneCount"],
            workload_count=expected["workloadCount"],
            persistent_workload_count=expected["persistentWorkloadCount"],
            colocate_relation_count=expected["colocateRelationCount"],
            separate_relation_count=expected["separateRelationCount"],
            ingress_kind=expected["ingressKind"],
            secret_binding_count=expected["secretBindingCount"],
            per_replica_storage_count=expected["perReplicaStorageCount"],
        )


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
@pytest.mark.parametrize("case", DEPLOYMENT_CASES)
def test_corpus_cases_match_semantic_decision_axes(provider: str, case: str) -> None:
    expected = CASE_EXPECTATIONS[case]
    graph = _graph(case)
    bundle = build_deployment_diagram_bundle(graph, _resource_spec(provider))
    plan = bundle["projections"][0]["deploymentPlan"]
    resource_plan = bundle["projections"][0]["resourcePlan"]
    compute_by_id = {item["id"]: item for item in plan["computeUnits"]}
    placement = {item["workloadRef"]: item["computeUnitRef"] for item in plan["placements"]}
    primary = compute_by_id[placement["web"]]
    persistent_ids = {
        workload["id"]
        for workload in resource_plan["workloads"]
        if any(
            storage.get("persistence") == "persistent"
            for storage in workload.get("storage") or []
        )
    }
    public_paths = [
        path for path in plan["networkPaths"] if path.get("kind") == "publicIngress"
    ]

    assert len(plan["computeUnits"]) == expected["computeUnitCount"]
    assert primary["kind"] == expected["computeKind"]
    assert primary["replicaCount"] == expected["replicaCount"]
    assert len(primary["zones"]) == expected["zoneCount"]
    assert len(graph["workloads"]) == expected["workloadCount"]
    assert len(persistent_ids) == expected["persistentWorkloadCount"]
    assert sum(
        constraint.get("kind") == "colocate"
        for constraint in graph["constraints"]
    ) == expected["colocateRelationCount"]
    assert sum(
        constraint.get("kind") == "separate"
        for constraint in graph["constraints"]
    ) == expected["separateRelationCount"]
    if expected["ingressKind"] == "privateEgressOnly":
        assert not public_paths
    else:
        assert {path["ingressKind"] for path in public_paths} == {
            expected["ingressKind"]
        }
    for constraint in graph["constraints"]:
        if constraint.get("kind") not in {"colocate", "separate"}:
            continue
        left, right = constraint["workloadRefs"]
        if constraint["kind"] == "colocate":
            assert placement[left] == placement[right]
        else:
            assert placement[left] != placement[right]
    if expected["computeUnitCount"] == 1 and expected["workloadCount"] > 1 and not (
        expected["colocateRelationCount"] or expected["separateRelationCount"]
    ):
        assert len({placement[item["id"]] for item in graph["workloads"]}) == 1

    subnet_cidrs = [
        node["attributes"]["cidr"]
        for node in resource_plan["nodes"]
        if node.get("providerPrimitiveKind") == "subnet"
    ]
    assert len(subnet_cidrs) == len(set(subnet_cidrs))


def test_provider_template_validation_rejects_overlapping_subnets() -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(MANAGED_ONE_PRIMARY_PUBLIC), _resource_spec("aws")
    )
    resource_plan = copy.deepcopy(bundle["projections"][0]["resourcePlan"])
    subnets = [
        node
        for node in resource_plan["nodes"]
        if node.get("providerPrimitiveKind") == "subnet"
    ]
    assert len(subnets) >= 2
    subnets[1]["attributes"]["cidr"] = subnets[0]["attributes"]["cidr"]

    with pytest.raises(ValueError, match="subnets overlap"):
        validate_complete_provider_template(resource_plan)


def test_isolated_runtime_places_each_compute_in_its_own_rendered_subnet() -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(STANDALONE_SEPARATE_TWO_WORKLOADS_ONE_PERSISTENT), _resource_spec("aws")
    )
    runtime = deployment_bundle_runtime_puml(bundle)

    assert 'as provider_subnet {' in runtime
    assert runtime.count("as subnet_compute_") == 1
    assert "places compute" not in runtime
    assert "traffic_filter_internal_filter_web_to_state" in runtime


def test_separated_workloads_receive_distinct_bootstrap_contracts() -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(STANDALONE_SEPARATED_PUBLIC), _resource_spec("aws")
    )
    files = render_open_tofu(bundle["projections"][0]["resourcePlan"])
    bootstraps = {
        name: content
        for name, content in files.items()
        if name.startswith("bootstrap_")
    }

    assert set(bootstraps) == {
        "bootstrap_compute_1.sh.tftpl",
        "bootstrap_compute_2.sh.tftpl",
    }
    assert '--name "web"' in bootstraps["bootstrap_compute_1.sh.tftpl"]
    assert '--name "worker"' not in bootstraps["bootstrap_compute_1.sh.tftpl"]
    assert '--name "worker"' in bootstraps["bootstrap_compute_2.sh.tftpl"]
    assert '--name "web"' not in bootstraps["bootstrap_compute_2.sh.tftpl"]
    assert "bootstrap_compute_1.sh.tftpl" in files["main.tf"]
    assert "bootstrap_compute_2.sh.tftpl" in files["main.tf"]


@pytest.mark.parametrize(
    ("provider", "login_marker"),
    [
        ("aws", "aws ecr get-login-password"),
        ("azure", "az acr login"),
        ("gcp", "oauth2accesstoken"),
    ],
)
def test_generated_application_bootstrap_authenticates_to_registry(
    provider: str, login_marker: str
) -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(STANDALONE_PRIMARY_PUBLIC), _resource_spec(provider)
    )
    files = render_open_tofu(bundle["projections"][0]["resourcePlan"])
    bootstrap = files["bootstrap_compute_1.sh.tftpl"]

    assert login_marker in bootstrap
    assert "@${image_digest_web}" in bootstrap
    deploy = files["deploy.sh"]
    assert "-target=" in deploy
    assert "docker build --pull" in deploy
    assert "docker push" in deploy
    assert "TF_VAR_image_digest_web" in deploy
    assert deploy.index("-target=") < deploy.index("docker push") < deploy.rindex(
        'tofu apply "$@"'
    )


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_persistent_workload_bootstrap_safely_formats_and_mounts_disk(
    provider: str,
) -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(STANDALONE_DEFAULT_TWO_WORKLOADS_ONE_PERSISTENT), _resource_spec(provider)
    )
    files = render_open_tofu(bundle["projections"][0]["resourcePlan"])
    data_bootstrap = next(
        content
        for name, content in files.items()
        if name.startswith("bootstrap_") and '--name "state"' in content
    )

    assert 'if ! blkid "$DISK_DEVICE"' in data_bootstrap
    assert 'mkfs.ext4 "$DISK_DEVICE"' in data_bootstrap
    assert "/mnt/easydep/state_volume/data" in data_bootstrap
    assert "-v /mnt/easydep/state_volume/data:/var/lib/easydep/state" in data_bootstrap


def test_each_deployment_target_receives_an_independent_complete_projection() -> None:
    spec = {
        "schemaVersion": "4",
        "deploymentTargets": [
            {
                "provider": provider,
                "region": region,
                "zones": list(zones),
            }
            for provider, (region, zones) in TARGETS.items()
        ],
    }
    bundle = build_deployment_diagram_bundle(_graph(STANDALONE_PRIMARY_PUBLIC), spec)

    assert bundle["mode"] == "alternatives"
    assert [item["provider"] for item in bundle["projections"]] == [
        "aws",
        "azure",
        "gcp",
    ]
    for projection in bundle["projections"]:
        assert projection["status"] == "completed"
        files = render_open_tofu(projection["resourcePlan"])
        assert rendered_resource_types(files)


@pytest.mark.parametrize(
    ("provider", "inline_marker"),
    [
        ("aws", "block_device_mappings"),
        ("azure", "data_disk"),
        ("gcp", 'device_name = "easydep-state-volume"'),
    ],
)
def test_per_replica_storage_is_embedded_in_managed_compute_model(
    provider: str, inline_marker: str
) -> None:
    graph = _graph(STANDALONE_DEFAULT_TWO_WORKLOADS_ONE_PERSISTENT)
    state = next(item for item in graph["workloads"] if item["id"] == "state")
    state["replicationSafety"] = "interchangeable"
    state["storage"][0]["replicaSemantics"] = "perReplica"
    graph["constraints"].append(
        {
            "id": "data-replicas",
            "kind": "replicaCount",
            "workloadRefs": ["state"],
            "value": 2,
            "sourceRefs": ["requirement:DATA-HA"],
        }
    )
    bundle = build_deployment_diagram_bundle(graph, _resource_spec(provider))
    resource_plan = bundle["projections"][0]["resourcePlan"]
    files = render_open_tofu(resource_plan)

    assert bundle["status"] == "completed"
    assert inline_marker in files["main.tf"]
    assert any(
        block["attributes"].get("perReplica") is True
        for block in resource_plan["embeddedBlocks"]
    )
    assert any(
        'if ! blkid "$DISK_DEVICE"' in content
        for name, content in files.items()
        if name.startswith("bootstrap_")
    )


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_internal_managed_endpoint_preserves_declared_interface_port(
    provider: str,
) -> None:
    graph = _graph(STANDALONE_SEPARATE_TWO_WORKLOADS_ONE_PERSISTENT)
    state = next(item for item in graph["workloads"] if item["id"] == "state")
    state["replicationSafety"] = "interchangeable"
    state["interfaces"][0]["port"] = 9191
    state["storage"][0]["replicaSemantics"] = "perReplica"
    graph["constraints"].append(
        {
            "id": "state-replicas",
            "kind": "replicaCount",
            "workloadRefs": ["state"],
            "value": 2,
            "sourceRefs": ["requirement:STATE-REP"],
        }
    )

    bundle = build_deployment_diagram_bundle(graph, _resource_spec(provider))
    resource_plan = bundle["projections"][0]["resourcePlan"]
    internal_nodes = [
        node
        for node in resource_plan["nodes"]
        if str(node["id"]).startswith("internal-")
    ]
    port_values = {
        value
        for node in internal_nodes
        for key, value in node["attributes"].items()
        if key in {"port", "frontendPort", "backendPort"}
        and isinstance(value, int)
    }

    assert port_values == {9191}
    assert "9191" in render_open_tofu(resource_plan)["main.tf"]


@pytest.mark.parametrize(
    ("provider", "permission_type", "fetch_marker"),
    [
        ("aws", "aws_iam_role_policy", "secretsmanager get-secret-value"),
        ("azure", "azurerm_role_assignment", "az keyvault secret show"),
        (
            "gcp",
            "google_secret_manager_secret_iam_member",
            "secretmanager.googleapis.com",
        ),
    ],
)
def test_secret_binding_has_permission_and_identity_based_runtime_fetch(
    provider: str, permission_type: str, fetch_marker: str
) -> None:
    graph = _graph(STANDALONE_PRIMARY_PUBLIC)
    graph["workloads"][0]["configuration"] = [
        {
            "id": "api-token",
            "name": "API_TOKEN",
            "kind": "secretBinding",
            "sensitive": True,
            "sourceRefs": ["requirement:SECRET"],
        }
    ]
    bundle = build_deployment_diagram_bundle(graph, _resource_spec(provider))
    resource_plan = bundle["projections"][0]["resourcePlan"]
    files = render_open_tofu(resource_plan)
    bootstrap = files["bootstrap_compute_1.sh.tftpl"]

    assert permission_type in rendered_resource_types(files)
    assert fetch_marker in bootstrap
    assert "-e API_TOKEN" in bootstrap
    assert "secret_ref_web_api_token" in bootstrap
    assert 'variable "secret_reference_web_api_token"' in files["variables.tf"]


def test_colocated_connection_injects_container_dns_contract_and_renders_env_name() -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(STANDALONE_DEFAULT_TWO_WORKLOADS_ONE_PERSISTENT),
        _resource_spec("aws"),
    )
    resource_plan = bundle["projections"][0]["resourcePlan"]
    binding = next(
        item
        for item in resource_plan["runtimeBindings"]
        if item["kind"] == "endpointEnvironment"
    )
    bootstrap = render_open_tofu(resource_plan)["bootstrap_compute_1.sh.tftpl"]
    runtime = deployment_bundle_runtime_puml(bundle)

    assert binding["strategy"] == "containerDns"
    assert 'export STATE_SERVICE_URL="http://state:' in bootstrap
    assert "runtime_web -[#2f6b50]-> runtime_state : HTTP" in runtime
    assert "[env] STATE_SERVICE_URL" in runtime
    assert "persistent_disk ..[#6f7c73]> runtime_state" in runtime
    assert "[mount] /var/lib/easydep/state" in runtime


def test_runtime_labels_are_compact_and_repeated_replica_edges_are_unlabelled() -> None:
    case = _case_id(
        "managedVmGroup",
        2,
        2,
        2,
        2,
        1,
        "loadBalancer",
        separate_relation_count=1,
    )
    runtime = deployment_bundle_runtime_puml(
        build_deployment_diagram_bundle(_graph(case), _resource_spec("aws"))
    )
    arrow_lines = [line for line in runtime.splitlines() if "->" in line]

    assert sum(
        line.startswith("runtime_web") and line.endswith(": HTTP")
        for line in arrow_lines
    ) == 1
    assert not any("STATE_SERVICE_URL" in line for line in arrow_lines)
    assert "[env] STATE_SERVICE_URL" in runtime
    assert "[mount] /var/lib/easydep/state" in runtime
    assert "allows required traffic" not in runtime
    assert "places instance" not in runtime
    assert "forwards to replica" not in runtime
    assert "reaches Registry" not in runtime
    assert "mounted at" not in runtime


@pytest.mark.parametrize(
    ("provider", "static_marker"),
    [
        ("aws", "private_ip ="),
        ("azure", "private_ip_address_allocation = \"Static\""),
        ("gcp", "network_ip ="),
    ],
)
def test_separated_singleton_connection_injects_fixed_private_ip(
    provider: str, static_marker: str
) -> None:
    bundle = build_deployment_diagram_bundle(
        _graph(STANDALONE_SEPARATE_TWO_WORKLOADS_ONE_PERSISTENT),
        _resource_spec(provider),
    )
    resource_plan = bundle["projections"][0]["resourcePlan"]
    binding = next(
        item
        for item in resource_plan["runtimeBindings"]
        if item["kind"] == "endpointEnvironment"
    )
    files = render_open_tofu(resource_plan)
    target_node = next(
        node
        for node in resource_plan["nodes"]
        if node["id"] == "compute-2"
    )
    private_ip = target_node["attributes"]["privateIp"]

    assert binding["strategy"] == "staticPrivateIp"
    assert binding["targetComputeUnitRef"] == "compute-2"
    assert f'export STATE_SERVICE_URL="http://{private_ip}:' in files[
        "bootstrap_compute_1.sh.tftpl"
    ]
    assert static_marker in files["main.tf"]


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_managed_target_connection_injects_internal_load_balancer_endpoint(
    provider: str,
) -> None:
    graph = _graph(STANDALONE_SEPARATE_TWO_WORKLOADS_ONE_PERSISTENT)
    state = next(item for item in graph["workloads"] if item["id"] == "state")
    state["replicationSafety"] = "interchangeable"
    state["storage"][0]["replicaSemantics"] = "perReplica"
    graph["constraints"].append(
        {
            "id": "state-replicas",
            "kind": "replicaCount",
            "workloadRefs": ["state"],
            "value": 2,
            "sourceRefs": ["requirement:STATE-HA"],
        }
    )
    bundle = build_deployment_diagram_bundle(graph, _resource_spec(provider))
    resource_plan = bundle["projections"][0]["resourcePlan"]
    binding = next(
        item
        for item in resource_plan["runtimeBindings"]
        if item["kind"] == "endpointEnvironment"
    )
    files = render_open_tofu(resource_plan)

    assert binding["strategy"] == "internalLoadBalancer"
    assert any(
        str(node["id"]).startswith("internal-load-balancer-compute-2")
        for node in resource_plan["nodes"]
    )
    assert "endpoint_web_state_service_url" in files["bootstrap_compute_1.sh.tftpl"]


def test_secret_runtime_diagram_shows_permission_and_environment_injection() -> None:
    graph = _graph(STANDALONE_PRIMARY_PUBLIC)
    graph["workloads"][0]["configuration"] = [
        {
            "id": "api-token",
            "name": "API_TOKEN",
            "kind": "secretBinding",
            "sensitive": True,
            "sourceRefs": ["requirement:SECRET"],
        }
    ]
    runtime = deployment_bundle_runtime_puml(
        build_deployment_diagram_bundle(graph, _resource_spec("aws"))
    )

    assert "read permission" in runtime
    assert "[secret] API_TOKEN" in runtime
    assert " : inject" in runtime


def test_ordinary_and_external_endpoint_configuration_are_injected_without_secrets() -> None:
    graph = _graph(STANDALONE_PRIMARY_PUBLIC)
    graph["externalDependencies"] = [
        {
            "id": "payments",
            "name": "Payments API",
            "interfaces": [
                {
                    "id": "https",
                    "protocol": "http",
                    "exposure": "outbound",
                    "sourceRefs": ["requirement:PAYMENTS"],
                }
            ],
            "sourceRefs": ["requirement:PAYMENTS"],
        }
    ]
    graph["connections"] = [
        {
            "id": "web-to-payments",
            "sourceRef": "web",
            "targetRef": "payments",
            "targetInterfaceRef": "https",
            "protocol": "http",
            "sourceRefs": ["sequence:PAY"],
        }
    ]
    graph["workloads"][0]["configuration"] = [
        {
            "id": "app-mode",
            "name": "APP_MODE",
            "kind": "value",
            "value": "production",
            "sourceRefs": ["requirement:MODE"],
        },
        {
            "id": "payments-url",
            "name": "PAYMENTS_URL",
            "kind": "endpointBinding",
            "connectionRef": "web-to-payments",
            "projection": "url",
            "sourceRefs": ["sequence:PAY"],
        },
    ]
    resource_plan = build_deployment_diagram_bundle(
        graph, _resource_spec("aws")
    )["projections"][0]["resourcePlan"]
    files = render_open_tofu(resource_plan)
    bootstrap = files["bootstrap_compute_1.sh.tftpl"]

    assert 'export APP_MODE="production"' in bootstrap
    assert 'export PAYMENTS_URL="${endpoint_web_payments_url}"' in bootstrap
    assert 'variable "external_endpoint_web_to_payments"' in files["variables.tf"]
    assert "-e APP_MODE" in bootstrap
    assert "-e PAYMENTS_URL" in bootstrap
