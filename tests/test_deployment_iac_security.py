"""Regression tests for least-privilege provider IaC rendering."""

from __future__ import annotations

from app.design.services.deployment_diagram.bundle import build_deployment_diagram_bundle
from app.implementation.delivery.iac_renderer import render_open_tofu
from scripts.generate_deployment_diagram_examples import (
    DEPLOYMENT_CASES,
    TARGETS,
    deployment_case_graph,
    deployment_resource_spec,
    semantic_case_id,
)


def _case(
    compute_kind: str = "standaloneVm",
    *,
    workloads: int = 2,
    persistent_workloads: int = 1,
    ingress: str = "directPublicIp",
    replicas: int = 1,
) -> str:
    return semantic_case_id(
        compute_kind=compute_kind,
        compute_units=2 if workloads == 2 else 1,
        replicas=replicas,
        zones=1,
        workload_count=workloads,
        persistent_workload_count=persistent_workloads,
        colocate_relation_count=0,
        separate_relation_count=1 if workloads == 2 else 0,
        ingress_kind=ingress,
    )


def _files(provider: str, graph: dict | None = None) -> tuple[dict, dict[str, str]]:
    graph = graph or deployment_case_graph(_case())
    plan = build_deployment_diagram_bundle(graph, deployment_resource_spec(provider))["projections"][0][
        "resourcePlan"
    ]
    return plan, render_open_tofu(plan)


def test_internal_rules_use_only_the_connection_target_port() -> None:
    graph = deployment_case_graph(_case())
    state = next(workload for workload in graph["workloads"] if workload["id"] == "state")
    state["interfaces"][0]["port"] = 9191

    _, aws = _files("aws", graph)
    aws_internal = aws["main.tf"].split('resource "aws_security_group" "internal_filter_', 1)[1]
    assert "from_port = 9191" in aws_internal
    assert "to_port = 9191" in aws_internal
    assert "from_port = 1" not in aws_internal
    assert "to_port = 65535" not in aws_internal

    _, gcp = _files("gcp", graph)
    gcp_internal = gcp["main.tf"].split('resource "google_compute_firewall" "internal_filter_', 1)[1]
    assert "ports = [tostring(9191)]" in gcp_internal
    assert '"1-65535"' not in gcp["main.tf"]


def test_official_corpus_never_renders_an_all_port_ingress_fallback() -> None:
    rendered_modules = 0
    for provider in TARGETS:
        for case in DEPLOYMENT_CASES:
            _, files = _files(provider, deployment_case_graph(case))
            main = files["main.tf"]
            assert 'from_port = 1; to_port = 65535; protocol = "tcp"' not in main
            assert 'ports = ["1-65535"]' not in main
            rendered_modules += 1

    assert rendered_modules == 45


def test_bootstrap_never_sources_dotenv() -> None:
    plan, files = _files("aws")
    bootstraps = [
        content for name, content in files.items() if name.startswith("bootstrap_")
    ]
    assert all(". /opt/easydep/runtime/.env" not in content for content in bootstraps)
    assert all("env_file:" in content for content in bootstraps)
    assert plan["bindingSlots"]


def test_secret_references_are_validated_and_not_treated_as_secret_values() -> None:
    graph = deployment_case_graph(_case(workloads=1, persistent_workloads=0))
    graph["workloads"][0]["configuration"] = [
        {
            "id": "api-token",
            "name": "API_TOKEN",
            "kind": "secretBinding",
            "sensitive": True,
            "sourceRefs": ["requirement:SECRET"],
        }
    ]

    _, aws = _files("aws", graph)
    assert "must be a Secrets Manager ARN" in aws["variables.tf"]
    assert "Resource = var.secret_reference_web_api_token" in aws["main.tf"]

    _, azure = _files("azure", graph)
    assert "must be an Azure Key Vault secret resource ID" in azure["variables.tf"]
    bootstrap = azure["bootstrap_compute_1.sh.tftpl"]
    assert "/resourceGroups/*/providers/Microsoft.KeyVault/vaults/*/secrets/*)" in bootstrap
    assert "https://*) SECRET_VALUE" not in bootstrap
    assert "scope = var.secret_reference_web_api_token" in azure["main.tf"]

    _, gcp = _files("gcp", graph)
    assert "must be a Secret Manager resource name" in gcp["variables.tf"]
    assert 'SECRET_RESOURCE="projects/${' not in gcp["bootstrap_compute_1.sh.tftpl"]
    assert "secret_id = var.secret_reference_web_api_token" in gcp["main.tf"]


def test_retained_replica_disks_have_provider_tracking_metadata_and_no_unconfigured_ssh() -> None:
    graph = deployment_case_graph(
        _case("managedVmGroup", ingress="loadBalancer", replicas=2)
    )
    state = next(workload for workload in graph["workloads"] if workload["id"] == "state")
    state["replicationSafety"] = "interchangeable"
    state["storage"][0]["replicaSemantics"] = "perReplica"
    graph["constraints"].append(
        {
            "id": "replicas",
            "kind": "replicaCount",
            "workloadRefs": ["state"],
            "value": 2,
            "sourceRefs": ["requirement:REPLICAS"],
        }
    )
    for provider, marker in (
        ("aws", 'device_name = "/dev/sdf"'),
        ("azure", 'name = "${var.resource_prefix}-state-volume"'),
        ("gcp", "stateful_disk {"),
    ):
        _, files = _files(provider, graph)
        assert marker in files["main.tf"]
        assert 'output "retained_replica_disk_state_volume"' in files["outputs.tf"]
        if provider == "aws":
            assert "EasyDepStorageRef" not in files["main.tf"]
            assert "delete_on_termination = false" in files["main.tf"]
            assert "autoscaling_group_name = aws_autoscaling_group.compute_2.name" in files[
                "outputs.tf"
            ]
            assert 'block_device_name = "/dev/sdf"' in files["outputs.tf"]
            assert 'lookup_strategy = "asg-instance-block-device-volume-id"' in files[
                "outputs.tf"
            ]
        if provider == "azure":
            assert 'create_option = "Empty"' in files["main.tf"]
            assert "vmss_data_disk_lun = 10" in files["outputs.tf"]
            assert 'lookup_strategy = "vmss-instance-lun-managed-disk-id"' in files[
                "outputs.tf"
            ]
        if provider == "gcp":
            assert 'device_name = "easydep-state-volume"' in files["main.tf"]
            assert 'delete_rule = "NEVER"' in files["main.tf"]
            assert "mig_resource_id = google_compute_region_instance_group_manager" in files[
                "outputs.tf"
            ]
            assert 'lookup_strategy = "mig-instance-device-source"' in files["outputs.tf"]

    graph = deployment_case_graph(_case(workloads=1, persistent_workloads=0))
    for provider in ("aws", "azure", "gcp"):
        _, files = _files(provider, graph)
        has_ssh = 'output "ssh_command_compute_1"' in files["outputs.tf"]
        assert has_ssh is (provider == "azure")
