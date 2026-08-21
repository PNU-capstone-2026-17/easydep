from __future__ import annotations

import json

import pytest

from app.core.orchestration.adapters.cloud_design import CloudDesignAdapter
from app.core.orchestration.adapters.vm_delivery import VmDeliveryAdapter
from app.core.orchestration.provider_target import resolve_resource_spec
from app.design.services.deployment_diagram.planner import (
    build_deployment_plan,
    build_provider_resource_plan,
    normalize_workload_graph,
)


def _cloud_design(
    *,
    provider: str = "aws",
    workloads: list[dict] | None = None,
    constraints: list[dict] | None = None,
) -> dict:
    graph = normalize_workload_graph(
        {
            "schemaVersion": "easydep-workload-graph",
            "workloads": workloads
            or [
                {
                    "id": "application",
                    "name": "Application",
                    "artifact": {"kind": "generatedApplication"},
                    "interfaces": [
                        {
                            "id": "http",
                            "protocol": "http",
                            "exposure": "public",
                            "sourceRefs": ["api:application"],
                        }
                    ],
                    "storage": [],
                    "configuration": [],
                    "resourceRequirements": {},
                    "replicationSafety": "singleton",
                    "sourceRefs": ["class:application"],
                }
            ],
            "externalDependencies": [],
            "connections": [],
            "constraints": constraints or [],
            "derivations": [],
        }
    )
    plan = build_deployment_plan(graph, {"region": "ap-northeast-2"})
    resource_plan = build_provider_resource_plan(
        plan, graph, provider=provider, region="ap-northeast-2"
    )
    bundle = {
        "schemaVersion": "easydep-deployment-diagram",
        "status": "completed",
        "mode": "single",
        "workloadGraph": graph,
        "projections": [
            {
                "status": "completed",
                "provider": provider,
                "region": "ap-northeast-2",
                "deploymentPlan": plan,
                "resourcePlan": resource_plan,
            }
        ],
    }
    return {
        "workload_graph": graph,
        "deployment_plan": plan,
        "resource_plan": resource_plan,
        "deployment_diagram_bundle": bundle,
    }



@pytest.mark.parametrize(
    ("explicit_text", "expected", "inferred"),
    [
        ("Deploy to Amazon Web Services.", "aws", "gcp"),
        ("Deploy to Microsoft Azure.", "azure", "aws"),
        ("Deploy to Google Cloud Platform.", "gcp", "azure"),
    ],
)
def test_explicit_cloud_constraint_overrides_inferred_provider(explicit_text, expected, inferred):
    resolved = resolve_resource_spec(
        {"provider": inferred},
        explicit_text,
    )

    assert resolved["provider"] == expected
    assert resolved["providerAnalysisMismatch"] == {
        "inferred": inferred,
        "explicit": expected,
    }


def test_multiple_explicit_cloud_targets_are_rejected():
    with pytest.raises(ValueError, match="multiple target providers"):
        resolve_resource_spec({}, "Deploy to AWS and Google Cloud.")


def test_provider_target_requires_selecting_one_saved_alternative():
    with pytest.raises(ValueError, match="Select one provider and region"):
        resolve_resource_spec(
            {
                "deploymentTargets": [
                    {"provider": "aws", "region": "ap-northeast-2"},
                    {"provider": "gcp", "region": "asia-northeast3"},
                ]
            }
        )


def test_vm_delivery_renders_deterministically_without_llm(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    captured = {}

    def invoke(prompt: str) -> str:
        captured.update(json.loads(prompt))
        return json.dumps(
            {
                "terraformFiles": {"main.tf": 'resource "aws_instance" "app" {}'},
                "deploymentNotes": ["certificate is supplied by variable"],
            }
        )

    result = VmDeliveryAdapter(invoke).generate(
        requirements_result={
            "resource_spec": {"provider": "aws"},
            "deployment_needs": {"instance_count": {"metadata": {"count": 1}}},
        },
        cloud_design_result={
            "dependency_coverage": {
                "modeledInputs": [],
                "unmodeledAcceptedNeeds": ["https_ingress"],
            },
            "infra_intent": {
                "csp": "aws",
                "region": "ap-northeast-2",
                "startResources": ["vm"],
                "resources": [
                    {
                        "id": "vm",
                        "provisioningStatus": "selectedStartResource",
                        "because": [],
                        "detail": "Korean text must not cross the boundary",
                    }
                ],
                "createOrder": ["vm"],
                "constraints": [],
                "capabilityRealizations": [
                    {
                        "id": "p3-https-alb",
                        "composition": "multi-resource",
                        "components": [{"nativePath": "listener"}],
                    }
                ],
            },
            "kb_used": ["depkb"],
            **_cloud_design(),
        },
        implementation_result={"run_root": str(tmp_path / "run")},
        application_runtime_contract={
            "facts": [
                {
                    "id": "http",
                    "kind": "runtime.port",
                    "attributes": {"port": 8181},
                }
            ]
        },
        enable_consistency_validator=False,
    )

    assert result["cloudKbProvided"] is True
    assert (application / "infra" / "main.tf").read_text(encoding="utf-8").endswith("\n")
    assert captured == {}
    assert result["method"] == "deterministic-resource-plan"
    assert result["llmCalls"] == 0
    assert result["resourcePlan"]["schemaVersion"] == "easydep-resource-plan"
    assert 'resource "aws_instance" "compute_1"' in (
        application / "infra" / "main.tf"
    ).read_text(encoding="utf-8")
    assert "8181" in (application / "infra" / "bootstrap_compute_1.sh.tftpl").read_text(
        encoding="utf-8"
    )
    assert (application / "Dockerfile").is_file()
    assert "EXPOSE 8181" in (application / "Dockerfile").read_text(encoding="utf-8")
    assert (application / ".dockerignore").is_file()
    assert result["containerFilesCreated"] == ["Dockerfile", ".dockerignore"]
    assert result["vmSelection"]["status"] == "deferred"
    assert result["resourcePlanDigest"]


def test_vm_delivery_preserves_existing_container_files(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    (application / "Dockerfile").write_text("FROM custom\nEXPOSE 8080\n", encoding="utf-8")
    adapter = VmDeliveryAdapter(
        lambda _prompt: json.dumps(
            {"terraformFiles": {"main.tf": 'resource "aws_instance" "app" {}'}}
        )
    )

    result = adapter.generate(
        requirements_result={},
        cloud_design_result=_cloud_design(),
        implementation_result={"run_root": str(tmp_path / "run")},
    )

    assert (application / "Dockerfile").read_text(encoding="utf-8") == (
        "FROM custom\nEXPOSE 8080\n"
    )
    assert result["containerFilesCreated"] == [".dockerignore"]


def test_vm_delivery_stops_before_llm_for_unsupported_resource_plan(tmp_path):
    calls = []
    adapter = VmDeliveryAdapter(lambda prompt: calls.append(prompt) or "{}")

    with pytest.raises(ValueError, match="Generate the WorkloadGraph deployment diagram"):
        adapter.generate(
            requirements_result={"resource_spec": {"provider": "aws"}},
            cloud_design_result={
                "resource_plan": {
                    "schemaVersion": "unsupported",
                    "provider": "aws",
                    "unresolved": [
                        {
                            "field": "persistenceOwner",
                            "reason": "more than one deployable workload",
                        }
                    ],
                }
            },
            implementation_result={"run_root": str(tmp_path / "run")},
        )

    assert calls == []


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
def test_unknown_logical_model_is_blocked_before_iac_generation(provider, tmp_path):
    cloud_design = CloudDesignAdapter().finalize(
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
    calls = []

    with pytest.raises(ValueError, match=r"WorkloadGraph deployment diagram"):
        VmDeliveryAdapter(lambda prompt: calls.append(prompt) or "{}").generate(
            requirements_result={
                "resource_spec": {"provider": provider, "region": "test-region"}
            },
            cloud_design_result=cloud_design,
            implementation_result={"run_root": str(tmp_path / "run")},
        )

    assert calls == []


def test_separate_persistent_workload_does_not_reuse_application_mount_contract(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    captured = {}

    def invoke(prompt: str) -> str:
        captured.update(json.loads(prompt))
        return json.dumps(
            {"terraformFiles": {"main.tf": 'resource "aws_instance" "app" {}'}}
        )

    result = VmDeliveryAdapter(invoke).generate(
        requirements_result={"resource_spec": {"provider": "aws"}},
        cloud_design_result=_cloud_design(
            workloads=[
                {
                    "id": "application",
                    "name": "Application",
                    "artifact": {"kind": "generatedApplication"},
                    "interfaces": [
                        {
                            "id": "http",
                            "protocol": "http",
                            "exposure": "internal",
                            "sourceRefs": ["api:application"],
                        }
                    ],
                    "storage": [],
                    "configuration": [],
                    "resourceRequirements": {},
                    "replicationSafety": "singleton",
                    "sourceRefs": ["class:application"],
                },
                {
                    "id": "workload-state",
                    "name": "State Store",
                    "artifact": {
                        "kind": "prebuiltImage",
                        "image": "vendor/store:1",
                        "engine": "vendor-store",
                        "deploymentMode": "container",
                        "runtimeCatalogRef": "docker-on-vm/prebuilt-image",
                    },
                    "interfaces": [],
                    "storage": [
                        {
                            "id": "state-data",
                            "persistence": "persistent",
                            "capacityGiB": 20,
                            "mountPath": "/var/lib/vendor-store",
                            "deletionPolicy": "retain",
                            "sourceRefs": ["requirement:DATA-1"],
                        }
                    ],
                    "configuration": [],
                    "resourceRequirements": {},
                    "replicationSafety": "singleton",
                    "sourceRefs": ["requirement:DATA-1"],
                },
            ],
            constraints=[
                {
                    "id": "separate-state",
                    "kind": "separate",
                    "workloadRefs": ["application", "workload-state"],
                    "value": True,
                    "sourceRefs": ["requirement:ISOLATE-1"],
                }
            ],
        ),
        implementation_result={"run_root": str(tmp_path / "run")},
        cloud_capability_contract={
            "facts": [
                {
                    "id": "mount",
                    "kind": "cloud.storage.mount",
                    "attributes": {"mountPath": "/srv/application-state"},
                }
            ]
        },
        enable_consistency_validator=False,
    )

    assert captured == {}
    assert result["method"] == "deterministic-resource-plan"
    bootstrap = (application / "infra" / "bootstrap_compute_2.sh.tftpl").read_text(encoding="utf-8")
    assert "/var/lib/vendor-store" in bootstrap
    assert "/srv/application-state" not in bootstrap


def test_vm_delivery_replaces_owned_infra_snapshot_without_stale_files(tmp_path):
    application = tmp_path / "run" / "application"
    infra = application / "infra"
    infra.mkdir(parents=True)
    (infra / "user_data.tpl").write_text("stale", encoding="utf-8")
    (infra / "old.tf").write_text("stale", encoding="utf-8")
    (infra / "README.md").write_text("keep", encoding="utf-8")
    (infra / ".terraform").mkdir()
    (infra / ".terraform" / "cache").write_text("discard", encoding="utf-8")
    adapter = VmDeliveryAdapter(
        lambda _prompt: json.dumps(
            {"terraformFiles": {"main.tf": 'resource "aws_instance" "app" {}'}}
        )
    )

    adapter.generate(
        requirements_result={},
        cloud_design_result=_cloud_design(),
        implementation_result={"run_root": str(tmp_path / "run")},
    )

    assert (infra / "main.tf").is_file()
    assert not (infra / "old.tf").exists()
    assert not (infra / "user_data.tpl").exists()
    assert not (infra / ".terraform").exists()
    assert (infra / "README.md").read_text(encoding="utf-8") == "keep"


def test_vm_delivery_rejects_existing_dockerfile_with_wrong_bound_port(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    (application / "Dockerfile").write_text("FROM custom\nEXPOSE 9090\n", encoding="utf-8")
    adapter = VmDeliveryAdapter(
        lambda _prompt: json.dumps(
            {"terraformFiles": {"main.tf": 'resource "aws_instance" "app" {}'}}
        )
    )

    with pytest.raises(ValueError, match="contracted application port 8080"):
        adapter.generate(
            requirements_result={},
            cloud_design_result=_cloud_design(),
            implementation_result={"run_root": str(tmp_path / "run")},
        )


def test_vm_delivery_no_consistency_validator_preserves_same_mismatched_output(
    tmp_path,
):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    (application / "Dockerfile").write_text("FROM custom\nEXPOSE 9090\n", encoding="utf-8")
    adapter = VmDeliveryAdapter(
        lambda _prompt: json.dumps(
            {"terraformFiles": {"main.tf": 'resource "aws_instance" "app" {}'}}
        )
    )

    result = adapter.generate(
        requirements_result={},
        cloud_design_result=_cloud_design(),
        implementation_result={"run_root": str(tmp_path / "run")},
        enable_consistency_validator=False,
    )

    assert result["preflight"]["consistencyValidatorEnabled"] is False
    assert (application / "Dockerfile").read_text(encoding="utf-8").endswith("EXPOSE 9090\n")
