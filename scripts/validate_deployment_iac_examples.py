"""Validate all deterministic ResourcePlan OpenTofu examples.

One system-temporary workspace is reused per provider so provider downloads and
initialization are not repeated for every scenario. The directory and child
processes are owned by this invocation and removed on exit.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.cloudkb.depkb.provider_cache import (  # noqa: E402
    provider_cache_environment,
)
from app.design.services.deployment_diagram.bundle import (  # noqa: E402
    build_deployment_diagram_bundle,
)
from app.implementation.delivery.iac_renderer import render_open_tofu  # noqa: E402
from scripts.generate_deployment_diagram_examples import (  # noqa: E402
    CASE_EXPECTATIONS,
    DEPLOYMENT_CASES,
    TARGETS,
    _graph,
    _resource_spec,
    semantic_case_id,
)

PUBLIC_SINGLE = semantic_case_id(
    compute_kind="standaloneVm",
    compute_units=1,
    replicas=1,
    zones=1,
    workload_count=1,
    persistent_workload_count=0,
    colocate_relation_count=0,
    separate_relation_count=0,
    ingress_kind="directPublicIp",
)
PRIVATE_SINGLE = semantic_case_id(
    compute_kind="standaloneVm",
    compute_units=1,
    replicas=1,
    zones=1,
    workload_count=1,
    persistent_workload_count=0,
    colocate_relation_count=0,
    separate_relation_count=0,
    ingress_kind="privateEgressOnly",
)
ZONE_SPREAD = semantic_case_id(
    compute_kind="managedVmGroup",
    compute_units=1,
    replicas=2,
    zones=2,
    workload_count=1,
    persistent_workload_count=0,
    colocate_relation_count=0,
    separate_relation_count=0,
    ingress_kind="loadBalancer",
)
DEFAULT_TWO_WORKLOADS_ONE_PERSISTENT = semantic_case_id(
    compute_kind="standaloneVm",
    compute_units=1,
    replicas=1,
    zones=1,
    workload_count=2,
    persistent_workload_count=1,
    colocate_relation_count=0,
    separate_relation_count=0,
    ingress_kind="directPublicIp",
)

PLAN_CASES = frozenset(
    {
        PUBLIC_SINGLE,
        PRIVATE_SINGLE,
        ZONE_SPREAD,
        DEFAULT_TWO_WORKLOADS_ONE_PERSISTENT,
        next(
            case
            for case, expectation in CASE_EXPECTATIONS.items()
            if expectation["perReplicaStorageCount"] == 1
        ),
    }
)
VALIDATION_CASES = DEPLOYMENT_CASES


def _run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=240,
    )
    if result.returncode:
        detail = (result.stdout + "\n" + result.stderr).strip()
        raise RuntimeError(f"{' '.join(command)} failed in {cwd.name}:\n{detail[-6000:]}")


def _azure_mock_plan_file(resource_plan: dict) -> str:
    values = {
        "subscription_id": '"00000000-0000-0000-0000-000000000000"',
        "ssh_public_key": '"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g easydep-static"',
    }
    for workload in resource_plan.get("workloads") or []:
        workload_key = str(workload.get("id") or "").replace("-", "_")
        if (workload.get("artifact") or {}).get("kind") == "generatedApplication":
            values[f"image_digest_{workload_key}"] = '"sha256:' + "0" * 64 + '"'
        for interface in workload.get("interfaces") or []:
            if not isinstance(interface.get("port"), int):
                interface_key = str(interface.get("id") or "").replace("-", "_")
                values[f"container_port_{workload_key}_{interface_key}"] = "8080"
    assignments = "\n".join(f"    {name} = {value}" for name, value in values.items())
    subscription = "/subscriptions/00000000-0000-0000-0000-000000000000"
    resource_group = f"{subscription}/resourceGroups/easydep-rg"
    network_base = f"{resource_group}/providers/Microsoft.Network"
    mock_ids = {
        "azurerm_resource_group": resource_group,
        "azurerm_container_registry": (
            f"{resource_group}/providers/Microsoft.ContainerRegistry/registries/easydep"
        ),
        "azurerm_user_assigned_identity": (
            f"{resource_group}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/easydep"
        ),
        "azurerm_virtual_network": f"{network_base}/virtualNetworks/easydep",
        "azurerm_subnet": f"{network_base}/virtualNetworks/easydep/subnets/easydep",
        "azurerm_public_ip": f"{network_base}/publicIPAddresses/easydep",
        "azurerm_nat_gateway": f"{network_base}/natGateways/easydep",
        "azurerm_network_security_group": f"{network_base}/networkSecurityGroups/easydep",
        "azurerm_network_interface": f"{network_base}/networkInterfaces/easydep",
        "azurerm_linux_virtual_machine": (
            f"{resource_group}/providers/Microsoft.Compute/virtualMachines/easydep"
        ),
        "azurerm_linux_virtual_machine_scale_set": (
            f"{resource_group}/providers/Microsoft.Compute/virtualMachineScaleSets/easydep"
        ),
        "azurerm_lb": f"{network_base}/loadBalancers/easydep",
        "azurerm_lb_backend_address_pool": (
            f"{network_base}/loadBalancers/easydep/backendAddressPools/easydep"
        ),
        "azurerm_lb_probe": f"{network_base}/loadBalancers/easydep/probes/easydep",
        "azurerm_managed_disk": (
            f"{resource_group}/providers/Microsoft.Compute/disks/easydep"
        ),
    }
    mock_resources = "\n".join(
        "  mock_resource \""
        + resource_type
        + "\" { defaults = { id = \""
        + resource_id
        + "\""
        + (
            ', principal_id = "00000000-0000-0000-0000-000000000001"'
            if resource_type == "azurerm_user_assigned_identity"
            else ""
        )
        + " } }"
        for resource_type, resource_id in mock_ids.items()
    )
    return (
        'mock_provider "azurerm" {\n'
        f"{mock_resources}\n"
        "}\n\n"
        'run "static_plan" {\n'
        "  command = plan\n"
        "  variables {\n"
        f"{assignments}\n"
        "  }\n"
        "}\n"
    )


def validate(
    *, include_plan: bool, selected_provider: str | None = None, selected_case: str | None = None
) -> None:
    tofu = shutil.which("tofu") or shutil.which("terraform")
    if not tofu:
        raise RuntimeError("OpenTofu or Terraform is required")
    with tempfile.TemporaryDirectory(prefix="easydep-iac-validation-") as temp_name:
        root = Path(temp_name)
        environment = provider_cache_environment()
        validated = 0
        planned = 0
        providers = [selected_provider] if selected_provider else list(TARGETS)
        cases = [selected_case] if selected_case else list(VALIDATION_CASES)
        for provider in providers:
            provider_root = root / provider
            provider_root.mkdir()
            generated_names: set[str] = set()
            initialized = False
            for case in cases:
                for name in generated_names:
                    path = provider_root / name
                    if path.is_file():
                        path.unlink()
                bundle = build_deployment_diagram_bundle(
                    _graph(case), _resource_spec(provider)
                )
                resource_plan = bundle["projections"][0]["resourcePlan"]
                files = render_open_tofu(resource_plan)
                generated_names = set(files)
                for name, content in files.items():
                    (provider_root / name).write_text(content, encoding="utf-8", newline="\n")
                _run([tofu, "fmt"], cwd=provider_root, environment=environment)
                if not initialized:
                    init_command = [
                        tofu,
                        "init",
                        "-backend=false",
                        "-input=false",
                        "-no-color",
                    ]
                    _run(
                        init_command,
                        cwd=provider_root,
                        environment=environment,
                    )
                    initialized = True
                _run([tofu, "validate", "-no-color"], cwd=provider_root, environment=environment)
                validated += 1
                if include_plan and case in PLAN_CASES:
                    if provider == "azure":
                        test_file = provider_root / "static.tftest.hcl"
                        test_file.write_text(
                            _azure_mock_plan_file(resource_plan),
                            encoding="utf-8",
                            newline="\n",
                        )
                        try:
                            _run(
                                [tofu, "test", "-no-color"],
                                cwd=provider_root,
                                environment=environment,
                            )
                        finally:
                            if test_file.is_file():
                                test_file.unlink()
                        planned += 1
                        continue
                    plan_environment = dict(environment)
                    if provider == "aws":
                        plan_environment.update(
                            {
                                "AWS_ACCESS_KEY_ID": "testing",
                                "AWS_SECRET_ACCESS_KEY": "testing",
                                "AWS_EC2_METADATA_DISABLED": "true",
                                "TF_VAR_offline_validation": "true",
                                "TF_VAR_boot_image_id": "ami-0123456789abcdef0",
                            }
                        )
                    elif provider == "azure":
                        plan_environment.update(
                            {
                                "ARM_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000000",
                                "ARM_TENANT_ID": "00000000-0000-0000-0000-000000000000",
                                "ARM_CLIENT_ID": "00000000-0000-0000-0000-000000000000",
                                "ARM_CLIENT_SECRET": "testing",
                                "TF_VAR_subscription_id": "00000000-0000-0000-0000-000000000000",
                                "TF_VAR_ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g easydep-static",
                            }
                        )
                    else:
                        plan_environment.update(
                            {
                                "GOOGLE_OAUTH_ACCESS_TOKEN": "testing",
                                "TF_VAR_project_id": "easydep-static-validation",
                            }
                        )
                    plan_environment.setdefault(
                        "TF_VAR_ssh_public_key",
                        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8g easydep-static",
                    )
                    for workload in resource_plan.get("workloads") or []:
                        workload_key = str(workload.get("id") or "").replace("-", "_")
                        if (workload.get("artifact") or {}).get("kind") == "generatedApplication":
                            plan_environment[f"TF_VAR_image_digest_{workload_key}"] = (
                                "sha256:" + "0" * 64
                            )
                        for interface in workload.get("interfaces") or []:
                            if not isinstance(interface.get("port"), int):
                                interface_key = str(interface.get("id") or "").replace(
                                    "-", "_"
                                )
                                plan_environment[
                                    f"TF_VAR_container_port_{workload_key}_{interface_key}"
                                ] = "8080"
                    _run(
                        [
                            tofu,
                            "plan",
                            "-refresh=false",
                            "-input=false",
                            "-lock=false",
                            "-no-color",
                        ],
                        cwd=provider_root,
                        environment=plan_environment,
                    )
                    planned += 1
        print(f"Validated {validated} modules; planned {planned} representative modules.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--provider", choices=sorted(TARGETS))
    parser.add_argument("--case", choices=sorted(VALIDATION_CASES))
    arguments = parser.parse_args()
    validate(
        include_plan=arguments.plan,
        selected_provider=arguments.provider,
        selected_case=arguments.case,
    )


if __name__ == "__main__":
    main()
