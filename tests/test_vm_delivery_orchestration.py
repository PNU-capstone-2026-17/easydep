from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.core.orchestration.adapters.cloud_design import CloudDesignAdapter
from app.core.orchestration.adapters.vm_delivery import SYSTEM_PROMPT, VmDeliveryAdapter
from app.core.orchestration.app_cloud_contracts import (
    ApplicationRuntimeContract,
    CloudCapabilityContract,
    ContractFact,
    DeploymentBindingContract,
    derive_deployment_bindings,
)
from app.core.orchestration.provider_target import resolve_resource_spec


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


def test_runtime_bootstrap_preserves_application_owned_migrations_and_url_format():
    contract = ApplicationRuntimeContract(
        facts=[
            ContractFact(
                id="observed.java.flyway",
                kind="build.dependency",
                attributes={
                    "declarations": [
                        {
                            "configuration": "implementation",
                            "coordinate": "org.flywaydb:flyway-core",
                        }
                    ]
                },
            ),
            ContractFact(
                id="intent.configuration",
                kind="runtime.configuration.intent",
                attributes={
                    "requiredKeys": [
                        "DATABASE_URL",
                        "DATABASE_USER",
                        "DATABASE_PASSWORD",
                    ]
                },
            ),
            ContractFact(
                id="observed.environment.database_url",
                kind="runtime.environment",
                attributes={
                    "name": "DATABASE_URL",
                    "valuePrefix": "jdbc:postgresql://",
                },
            ),
        ]
    )

    errors = VmDeliveryAdapter._runtime_bootstrap_errors(
        {
            "user_data.sh.tftpl": (
                'docker run -e DATABASE_URL="postgresql://db:5432/app" '
                "-e DATABASE_USER=user -e DATABASE_PASSWORD=password app\n"
                "CREATE TABLE courses (id VARCHAR PRIMARY KEY);\n"
                "INSERT INTO courses VALUES ('C-1');\n"
            )
        },
        contract,
    )

    assert any("application schema or seed-data migration" in error for error in errors)
    assert any("jdbc:postgresql://" in error for error in errors)
    assert not VmDeliveryAdapter._runtime_bootstrap_errors(
        {
            "user_data.sh.tftpl": (
                'docker run -e DATABASE_URL="jdbc:postgresql://db:5432/app" '
                "-e DATABASE_USER=user -e DATABASE_PASSWORD=password app\n"
            )
        },
        contract,
    )


def test_templatefile_inputs_must_not_be_escaped_as_shell_literals():
    files = {
        "main.tf": (
            'resource "aws_instance" "app" { user_data = '
            'templatefile("bootstrap.tftpl", { image = var.image, port = 8080 }) }'
        ),
        "bootstrap.tftpl": (
            "docker run $${image}\n"
            "echo $${RUNTIME_ONLY}\n"
        ),
    }

    errors = VmDeliveryAdapter._templatefile_binding_errors(files)

    assert errors == [
        "bootstrap.tftpl: templatefile input keys are escaped into literal shell "
        "placeholders: image"
    ]
    files["bootstrap.tftpl"] = "docker run ${image}\necho $${RUNTIME_ONLY}\n"
    assert VmDeliveryAdapter._templatefile_binding_errors(files) == []


def test_runtime_bootstrap_rejects_service_reload_before_start():
    errors = VmDeliveryAdapter._runtime_bootstrap_errors(
        {
            "proxy.sh.tftpl": (
                "sudo systemctl enable nginx\n"
                "sudo systemctl reload nginx\n"
            )
        },
        ApplicationRuntimeContract(),
    )

    assert errors == [
        "proxy.sh.tftpl: systemd service nginx is reloaded before it is started."
    ]
    assert VmDeliveryAdapter._runtime_bootstrap_errors(
        {
            "proxy.sh.tftpl": (
                "sudo systemctl enable --now nginx\n"
                "sudo systemctl reload nginx\n"
            )
        },
        ApplicationRuntimeContract(),
    ) == []


def test_aws_runtime_bootstrap_requires_egress_and_stable_ebs_identity():
    contract = ApplicationRuntimeContract()
    invalid = {
        "main.tf": (
            'resource "aws_vpc" "main" {}\n'
            'resource "aws_instance" "state" { '
            "associate_public_ip_address = false }\n"
            'resource "aws_volume_attachment" "state" {}\n'
        ),
        "state.sh.tftpl": (
            "dnf -y install docker\n"
            "docker run postgres:16\n"
            'DEVICE="/dev/sdf"\n'
        ),
    }

    errors = VmDeliveryAdapter._runtime_bootstrap_errors(
        invalid,
        contract,
        "aws",
    )

    assert any("internet gateway" in error for error in errors)
    assert any("no NAT gateway" in error for error in errors)
    assert any("stable identity" in error for error in errors)

    invented_linux_symlink = {
        **invalid,
        "state.sh.tftpl": (
            invalid["state.sh.tftpl"]
            + 'VOLUME_ID="vol-123"\n'
            + 'DEVICE="/dev/disk/by-id/aws-${VOLUME_ID}"\n'
        ),
    }
    assert any(
        "/dev/disk/by-id/aws-*" in error
        for error in VmDeliveryAdapter._runtime_bootstrap_errors(
            invented_linux_symlink,
            contract,
            "aws",
        )
    )

    valid = {
        **invalid,
        "main.tf": (
            invalid["main.tf"]
            + 'resource "aws_internet_gateway" "main" {}\n'
            + 'resource "aws_route_table" "public" { '
            + 'route { cidr_block = "0.0.0.0/0" } }\n'
            + 'resource "aws_route_table_association" "public" { '
            + "route_table_id = aws_route_table.public.id }\n"
            + 'resource "aws_nat_gateway" "egress" {}\n'
        ),
        "state.sh.tftpl": (
            invalid["state.sh.tftpl"]
            + 'for device in /dev/nvme*n1; do nvme id-ctrl --vendor-specific "$device"; done\n'
            + 'echo "UUID=example /var/lib/postgresql/data ext4 defaults,nofail 0 2" '
            + ">> /etc/fstab\n"
            + "docker run -d --restart unless-stopped postgres:16\n"
        ),
    }
    assert not VmDeliveryAdapter._runtime_bootstrap_errors(valid, contract, "aws")


def test_runtime_bootstrap_rejects_container_binding_of_filesystem_mount_root():
    contract = ApplicationRuntimeContract()
    common = 'MOUNT_ROOT="/mnt/state"\nmount "$${MOUNT_ROOT}"\n'

    errors = VmDeliveryAdapter._runtime_bootstrap_errors(
        {
            "state.sh.tftpl": (
                common
                + 'docker run -d --restart unless-stopped '
                + '-v "$${MOUNT_ROOT}":/var/lib/service/data state-image\n'
            )
        },
        contract,
    )

    assert errors == [
        "state.sh.tftpl: container data bind uses the filesystem mount root "
        "$${MOUNT_ROOT} without a dedicated runtime data child directory."
    ]
    assert not VmDeliveryAdapter._runtime_bootstrap_errors(
        {
            "state.sh.tftpl": (
                common
                + 'mkdir -p "$${MOUNT_ROOT}/runtime-data"\n'
                + 'docker run -d --restart unless-stopped '
                + '-v "$${MOUNT_ROOT}/runtime-data":/var/lib/service/data state-image\n'
            )
        },
        contract,
    )
    assert not VmDeliveryAdapter._runtime_bootstrap_errors(
        {
            "state.sh.tftpl": (
                common
                + 'docker run -d --restart unless-stopped '
                + '-e DATA_PATH="$${MOUNT_ROOT}/runtime-data" '
                + '-v "$${MOUNT_ROOT}":/var/lib/service/data state-image\n'
            )
        },
        contract,
    )


def test_provider_validation_reuses_cache_and_captures_lock(tmp_path, monkeypatch):
    cache = tmp_path / "plugin-cache"
    observed_environments = []

    monkeypatch.setattr(
        "app.core.orchestration.adapters.vm_delivery.settings.easydep_tofu_plugin_cache",
        str(cache),
    )
    monkeypatch.setattr(
        "app.core.orchestration.adapters.vm_delivery.shutil.which", lambda _name: "tofu"
    )

    def run(command, *, cwd, env, **_kwargs):
        observed_environments.append(
            {
                "cache": env.get("TF_PLUGIN_CACHE_DIR"),
                "config": Path(env["TF_CLI_CONFIG_FILE"]).read_text(encoding="utf-8"),
            }
        )
        if command[1] == "init":
            (cwd / ".terraform.lock.hcl").write_text(
                'provider "registry.opentofu.org/hashicorp/azurerm" {\n  version = "5.0.1"\n}\n',
                encoding="utf-8",
            )
        if command[1] == "show":
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps(
                    {
                        "format_version": "1.2",
                        "terraform_version": "1.9.0",
                        "planned_values": {"root_module": {"resources": []}},
                    }
                ),
                "",
            )
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr("app.core.orchestration.adapters.vm_delivery.run_process_tree", run)
    result = VmDeliveryAdapter()._provider_validation({"main.tf": "terraform {}"})

    assert cache.is_dir()
    assert all(env["cache"] is None for env in observed_environments)
    assert all(str(cache.resolve().as_posix()) in env["config"] for env in observed_environments)
    assert all("direct" not in env["config"] for env in observed_environments)
    assert result["providerLock"]["selections"] == [
        {"source": "hashicorp/azurerm", "version": "5.0.1"}
    ]
    assert result["providerLock"]["sha256"]
    assert result["_lockFileContent"].startswith("provider")
    assert result["terraformPlan"]["status"] == "available"
    assert result["terraformPlan"]["resourceCounts"] == {}
    assert [item["command"] for item in result["reports"]] == [
        "init",
        "validate",
        "plan",
        "show-json",
    ]
    assert all(item["stdout"] != "ok" for item in result["reports"] if item["command"] == "plan")


def test_provider_validation_fails_when_plan_json_cannot_be_observed(tmp_path, monkeypatch):
    cache = tmp_path / "plugin-cache"
    monkeypatch.setattr(
        "app.core.orchestration.adapters.vm_delivery.settings.easydep_tofu_plugin_cache",
        str(cache),
    )
    monkeypatch.setattr(
        "app.core.orchestration.adapters.vm_delivery.shutil.which", lambda _name: "tofu"
    )

    def run(command, *, cwd, **_kwargs):
        if command[1] == "init":
            (cwd / ".terraform.lock.hcl").write_text("", encoding="utf-8")
        if command[1] == "plan":
            return subprocess.CompletedProcess(command, 1, "", "invalid AMI query")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr("app.core.orchestration.adapters.vm_delivery.run_process_tree", run)

    result = VmDeliveryAdapter()._provider_validation({"main.tf": "terraform {}"})

    assert result["status"] == "failed"
    assert result["stage"] == "terraformPlan"
    assert result["errors"] == ["invalid AMI query"]


def test_provider_contract_rejects_unpinned_or_foreign_provider_before_init(tmp_path, monkeypatch):
    cache = tmp_path / "plugin-cache"
    unexpected = cache / "registry.opentofu.org/hashicorp/aws/5.100.0/windows_amd64"
    unexpected.mkdir(parents=True)
    invoked = []
    monkeypatch.setattr(
        "app.core.orchestration.adapters.vm_delivery.settings.easydep_tofu_plugin_cache",
        str(cache),
    )
    monkeypatch.setattr(
        "app.core.orchestration.adapters.vm_delivery.shutil.which", lambda _name: "tofu"
    )
    monkeypatch.setattr(
        "app.core.orchestration.adapters.vm_delivery.run_process_tree",
        lambda *_args, **_kwargs: invoked.append(True),
    )
    adapter = VmDeliveryAdapter()
    adapter._expected_provider = "azure"

    result = adapter._provider_validation(
        {
            "main.tf": (
                'terraform { required_providers { aws = { source = "hashicorp/aws" '
                'version = "6.58.0" } } }\nresource "aws_instance" "app" {}'
            )
        }
    )

    assert result["stage"] == "providerContract"
    assert invoked == []
    assert any("hashicorp/azurerm" in error for error in result["errors"])


@pytest.mark.parametrize(
    ("expected", "source", "version", "foreign_resource"),
    [
        ("aws", "hashicorp/aws", "5.100.0", "google_compute_instance"),
        ("azure", "hashicorp/azurerm", "5.0.1", "aws_instance"),
        ("gcp", "hashicorp/google", "5.45.2", "azurerm_linux_virtual_machine"),
    ],
)
def test_provider_contract_rejects_foreign_resources_for_every_target(
    expected, source, version, foreign_resource
):
    errors = VmDeliveryAdapter._provider_contract_errors(
        {
            "main.tf": (
                "terraform { required_providers { target = { "
                f'source = "{source}" version = "{version}"'
                " } } }\n"
                f'resource "{foreign_resource}" "foreign" {{}}'
            )
        },
        expected,
    )

    assert any("foreign provider resource prefixes" in error for error in errors)


@pytest.mark.parametrize(
    ("expected", "source", "version"),
    [
        ("aws", "hashicorp/aws", "5.100.0"),
        ("azure", "hashicorp/azurerm", "5.0.1"),
        ("gcp", "hashicorp/google", "5.45.2"),
    ],
)
def test_provider_contract_rejects_implicit_auxiliary_provider_for_every_target(
    expected, source, version
):
    errors = VmDeliveryAdapter._provider_contract_errors(
        {
            "main.tf": (
                "terraform { required_providers { target = { "
                f'source = "{source}" version = "{version}"'
                " } } }\n"
                'data "template_file" "bootstrap" { template = "ok" }'
            )
        },
        expected,
    )

    assert any("selected provider namespace" in error for error in errors)


def test_provider_contract_allows_language_builtin_templatefile():
    errors = VmDeliveryAdapter._provider_contract_errors(
        {
            "main.tf": (
                "terraform { required_providers { target = { "
                'source = "hashicorp/azurerm" version = "5.0.1"'
                " } } }\n"
                'locals { bootstrap = templatefile("bootstrap.tftpl", {}) }\n'
                'resource "azurerm_resource_group" "app" {}'
            )
        },
        "azure",
    )

    assert errors == []


def test_generated_provider_blocks_are_replaced_by_the_system_owned_contract():
    normalized, events = VmDeliveryAdapter._normalize_provider_ownership(
        {
            "main.tf": (
                'terraform { required_providers { aws = { source = "hashicorp/aws" '
                'version = "~> 5.100" } } }\n'
                'provider "aws" { region = var.region }\n'
                'resource "aws_vpc" "main" { cidr_block = "10.0.0.0/16" }\n'
            )
        }
    )
    files = VmDeliveryAdapter._ensure_provider_contract(
        normalized,
        "aws",
        "ap-northeast-2",
    )

    assert 'terraform {' not in files["main.tf"]
    assert 'provider "aws"' not in files["main.tf"]
    assert 'resource "aws_vpc" "main"' in files["main.tf"]
    assert 'version = "= 5.100.0"' in files["easydep-provider.tf"]
    assert events == [
        {
            "kind": "systemProviderOwnership",
            "file": "main.tf",
            "removedBlocks": ["provider.aws", "terraform"],
        }
    ]


def test_standard_template_provider_shape_is_lowered_to_builtin_templatefile():
    normalized, events = VmDeliveryAdapter._normalize_native_templatefiles(
        {
            "main.tf": (
                'data "template_file" "bootstrap" {\n'
                '  template = file("bootstrap.tftpl")\n'
                '  vars = { image = var.image }\n'
                '}\n'
                'resource "aws_instance" "app" {\n'
                '  user_data = data.template_file.bootstrap.rendered\n'
                '}\n'
            ),
            "bootstrap.tftpl": "docker run $${image}\n",
        }
    )

    assert 'data "template_file"' not in normalized["main.tf"]
    assert 'user_data = templatefile("bootstrap.tftpl", { image = var.image })' in normalized[
        "main.tf"
    ]
    assert events == [
        {
            "kind": "nativeTemplatefileLowering",
            "file": "main.tf",
            "dataSource": "data.template_file.bootstrap",
        }
    ]


def test_non_file_template_provider_shape_is_not_rewritten():
    original = {
        "main.tf": (
            'data "template_file" "inline" { template = "hello $${name}" }\n'
            'output "rendered" { value = data.template_file.inline.rendered }\n'
        )
    }

    normalized, events = VmDeliveryAdapter._normalize_native_templatefiles(original)

    assert normalized == original
    assert events == []


@pytest.mark.parametrize(
    ("provider", "source", "version", "alias"),
    [
        ("aws", "hashicorp/aws", "5.100.0", "aws"),
        ("azure", "hashicorp/azurerm", "5.0.1", "azurerm"),
        ("gcp", "hashicorp/google", "5.45.2", "google"),
    ],
)
def test_missing_provider_contract_is_added_from_the_pinned_system_policy(
    provider, source, version, alias
):
    files = VmDeliveryAdapter._ensure_provider_contract(
        {"main.tf": f'resource "{alias}_example" "app" {{}}'}, provider
    )

    managed = files["easydep-provider.tf"]
    assert f'source  = "{source}"' in managed
    assert f'version = "= {version}"' in managed
    assert VmDeliveryAdapter._provider_contract_errors(files, provider) == []


@pytest.mark.parametrize(
    ("provider", "region", "expected"),
    [
        ("aws", "ap-northeast-2", 'provider "aws"'),
        ("azure", "koreacentral", 'provider "azurerm"'),
        ("gcp", "asia-northeast3", 'provider "google"'),
    ],
)
def test_system_managed_provider_configuration_uses_the_selected_target(
    provider, region, expected
):
    files = VmDeliveryAdapter._ensure_provider_contract(
        {"main.tf": "locals {}"}, provider, region
    )

    managed = files["easydep-provider.tf"]
    assert expected in managed
    if provider != "azure":
        assert region in managed


def test_existing_provider_configuration_is_not_duplicated_by_managed_file():
    files = VmDeliveryAdapter._ensure_provider_contract(
        {
            "main.tf": (
                'provider "aws" { region = var.region }\n'
                'variable "region" { type = string }\n'
                'resource "aws_vpc" "main" { cidr_block = "10.0.0.0/16" }'
            )
        },
        "aws",
        "ap-northeast-2",
    )

    assert 'provider "aws"' in files["main.tf"]
    assert 'provider "aws"' not in files["easydep-provider.tf"]
    assert 'source  = "hashicorp/aws"' in files["easydep-provider.tf"]


def test_system_managed_gcp_provider_binds_a_declared_project_variable():
    files = VmDeliveryAdapter._ensure_provider_contract(
        {
            "main.tf": (
                'variable "project_id" { type = string }\n'
                'resource "google_compute_network" "main" { name = "main" }'
            )
        },
        "gcp",
        "asia-northeast3",
    )

    assert "project = var.project_id" in files["easydep-provider.tf"]


def test_plan_only_required_ssh_key_uses_a_valid_public_key_shape():
    environment = VmDeliveryAdapter._plan_variable_environment(
        {
            "main.tf": (
                'variable "admin_ssh_public_key" { type = string }\n'
                'variable "password" { type = string }\n'
                'variable "ami_id" { type = string }'
            )
        }
    )

    assert environment["TF_VAR_admin_ssh_public_key"].startswith("ssh-ed25519 ")
    assert environment["TF_VAR_password"].startswith("easydep-plan-only")
    assert environment["TF_VAR_ami_id"] == "ami-00000000000000000"


def test_existing_wrong_provider_contract_is_not_silently_replaced():
    original = {
        "main.tf": (
            "terraform { required_providers { aws = { "
            'source = "hashicorp/aws" version = "6.0.0" } } }'
        )
    }

    files = VmDeliveryAdapter._ensure_provider_contract(original, "aws")

    assert files == original
    assert any(
        "5.100.0" in error
        for error in VmDeliveryAdapter._provider_contract_errors(files, "aws")
    )


def test_generator_cannot_claim_the_system_managed_provider_file_name():
    with pytest.raises(ValueError, match="system-managed provider file"):
        VmDeliveryAdapter._ensure_provider_contract(
            {
                "main.tf": 'resource "aws_instance" "app" {}',
                "easydep-provider.tf": "locals {}",
            },
            "aws",
        )


def test_provider_only_managed_name_collision_is_canonicalized():
    files = VmDeliveryAdapter._ensure_provider_contract(
        {
            "main.tf": 'resource "aws_instance" "app" {}',
            "easydep-provider.tf": (
                'terraform { required_providers { aws = { source = "hashicorp/aws" } } }'
            ),
        },
        "aws",
        "ap-northeast-2",
    )

    assert 'version = "= 5.100.0"' in files["easydep-provider.tf"]
    assert 'region = "ap-northeast-2"' in files["easydep-provider.tf"]


def test_iac_agent_prompt_does_not_promote_candidate_necessity():
    assert "Never turn `candidate` or `notAssessed` necessity" in SYSTEM_PROMPT
    assert "unmodeledAcceptedNeeds" in SYSTEM_PROMPT


def test_vm_delivery_writes_only_returned_terraform(tmp_path):
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
            "resource_plan": {
                "schemaVersion": "easydep-resource-plan/v1",
                "provider": "aws",
                "nodes": [
                    {
                        "id": "workload-api",
                        "entityClass": "runtimeElement",
                        "handling": "runtimeDerived",
                    }
                ],
                "edges": [],
            },
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
    )

    assert result["cloudKbProvided"] is True
    assert (application / "infra" / "main.tf").read_text(encoding="utf-8").endswith("\n")
    assert captured["dependencyPlan"]["resources"] == [
        {"id": "security-group"},
        {"id": "subnet"},
        {"id": "vm"},
    ]
    assert {item["to"] for item in captured["dependencyPlan"]["edges"]} == {
        "subnet",
        "security-group",
    }
    assert captured["dependencyPlan"]["evidencePolicy"]["legacyClaimsExcluded"] is True
    assert captured["dependencyPlan"]["coverage"]["unmodeledAcceptedNeeds"] == ["https_ingress"]
    assert captured["dependencyPlan"]["capabilityRealizations"] == []
    assert captured["resourcePlan"]["schemaVersion"] == "easydep-resource-plan/v1"
    assert captured["resourcePlan"]["nodes"][0]["id"] == "workload-api"
    assert all(
        len(value) == 64 for value in captured["dependencyPlan"]["knowledgeSnapshot"].values()
    )
    assert "Korean text" not in json.dumps(captured)
    assert (application / "Dockerfile").is_file()
    assert "EXPOSE 8181" in (application / "Dockerfile").read_text(encoding="utf-8")
    assert captured["applicationPort"] == 8181
    assert "ResourcePlan attachment and allocation" in captured["persistenceBoundary"]
    assert "applicationMountPath only when" in captured["persistenceBoundary"]
    assert "Format only\nwhen no filesystem exists" in captured["persistenceBoundary"]
    assert "stable provider device identity" in captured["persistenceBoundary"]
    assert "$${NAME}" in SYSTEM_PROMPT
    assert (application / ".dockerignore").is_file()
    assert result["containerFilesCreated"] == ["Dockerfile", ".dockerignore"]
    assert result["vmSelection"]["status"] == "deferred"
    assert captured["vmSelection"] == result["vmSelection"]


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
        cloud_design_result={},
        implementation_result={"run_root": str(tmp_path / "run")},
    )

    assert (application / "Dockerfile").read_text(encoding="utf-8") == (
        "FROM custom\nEXPOSE 8080\n"
    )
    assert result["containerFilesCreated"] == [".dockerignore"]


def test_vm_delivery_stops_before_llm_when_resource_plan_is_unresolved(tmp_path):
    calls = []
    adapter = VmDeliveryAdapter(lambda prompt: calls.append(prompt) or "{}")

    with pytest.raises(ValueError, match="persistenceOwner"):
        adapter.generate(
            requirements_result={"resource_spec": {"provider": "aws"}},
            cloud_design_result={
                "resource_plan": {
                    "schemaVersion": "easydep-resource-plan/v1",
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
def test_direct_endpoint_is_normalized_to_http_before_iac_generation(provider, tmp_path):
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

    with pytest.raises(ValueError, match=r"returned no terraformFiles"):
        VmDeliveryAdapter(lambda prompt: calls.append(prompt) or "{}").generate(
            requirements_result={
                "resource_spec": {"provider": provider, "region": "test-region"}
            },
            cloud_design_result=cloud_design,
            implementation_result={"run_root": str(tmp_path / "run")},
        )

    assert len(calls) == 2
    assert '"protocol": "http"' in calls[0]
    assert "letsEncryptShortLivedIp" not in calls[0]


def test_separate_persistent_workload_does_not_reuse_application_mount_contract(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    captured = {}

    def invoke(prompt: str) -> str:
        captured.update(json.loads(prompt))
        return json.dumps(
            {"terraformFiles": {"main.tf": 'resource "aws_instance" "app" {}'}}
        )

    VmDeliveryAdapter(invoke).generate(
        requirements_result={"resource_spec": {"provider": "aws"}},
        cloud_design_result={
            "resource_plan": {
                "schemaVersion": "easydep-resource-plan/v1",
                "provider": "aws",
                "computeNodeId": "compute-instance",
                "nodes": [{"id": "workload-state", "handling": "runtimeDerived"}],
                "allocations": [
                    {
                        "workloadRef": "workload-state",
                        "computeRef": "compute-state",
                    }
                ],
                "decisions": [
                    {"field": "persistenceOwner", "value": "workload-state"}
                ],
                "edges": [],
                "unresolved": [],
            }
        },
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
    )

    assert captured["persistenceOwner"] == "workload-state"
    assert captured["applicationMountPath"] is None


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
        cloud_design_result={},
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
            cloud_design_result={},
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
        cloud_design_result={},
        implementation_result={"run_root": str(tmp_path / "run")},
        enable_consistency_validator=False,
    )

    assert result["preflight"]["consistencyValidatorEnabled"] is False
    assert (application / "Dockerfile").read_text(encoding="utf-8").endswith("EXPOSE 9090\n")


def test_vm_delivery_repairs_observable_iac_binding_conflict_once(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    prompts = []

    def invoke(prompt: str) -> str:
        prompts.append(json.loads(prompt))
        port = 9090 if len(prompts) == 1 else 8080
        return json.dumps(
            {
                "terraformFiles": {
                    "main.tf": (f'resource "unknown_backend" "app" {{ backend_port = {port} }}')
                }
            }
        )

    result = VmDeliveryAdapter(
        invoke,
        lambda _files: {"status": "passed", "reports": []},
    ).generate(
        requirements_result={},
        cloud_design_result={},
        implementation_result={"run_root": str(tmp_path / "run")},
    )

    assert len(prompts) == 2
    assert prompts[1]["validationStage"] == "deploymentBinding"
    assert result["preflight"]["repaired"] is True
    assert result["preflight"]["providerValidation"]["bindingReport"]["status"] == ("passed")


@pytest.mark.parametrize("name", ["../main.tf", "nested/main.tf", "main.txt"])
def test_vm_delivery_rejects_unsafe_or_non_terraform_paths(tmp_path, name):
    (tmp_path / "run" / "application").mkdir(parents=True)
    adapter = VmDeliveryAdapter(
        lambda _prompt: json.dumps({"terraformFiles": {name: "resource {}"}})
    )

    with pytest.raises(ValueError):
        adapter.generate(
            requirements_result={},
            cloud_design_result={},
            implementation_result={"run_root": str(tmp_path / "run")},
        )


def test_vm_delivery_accepts_flat_terraform_support_templates(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    adapter = VmDeliveryAdapter(
        lambda _prompt: json.dumps(
            {
                "terraformFiles": {
                    "main.tf": 'locals { cloud_init = templatefile("cloud_init.tpl", {}) }',
                    "cloud_init.tpl": "#!/bin/sh",
                }
            }
        )
    )

    result = adapter.generate(
        requirements_result={},
        cloud_design_result={},
        implementation_result={"run_root": str(tmp_path / "run")},
    )

    assert result["files"] == ["cloud_init.tpl", "main.tf"]


def test_vm_delivery_repairs_unsafe_output_envelope_within_single_budget(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    calls = []

    def invoke(prompt):
        calls.append(json.loads(prompt))
        files = (
            {"main.tf": "terraform {}", "cloudinit.cfg": "bad"}
            if len(calls) == 1
            else {"main.tf": "terraform {}", "cloudinit.tpl": "safe"}
        )
        return json.dumps({"terraformFiles": files, "deploymentNotes": []})

    result = VmDeliveryAdapter(invoke).generate(
        requirements_result={},
        cloud_design_result={},
        implementation_result={"run_root": str(tmp_path / "run")},
    )

    assert len(calls) == 2
    assert calls[1]["validationStage"] == "outputEnvelope"
    assert "Unsafe Terraform file name" in calls[1]["validationErrors"][0]
    assert result["preflight"]["repaired"] is True
    assert result["files"] == ["cloudinit.tpl", "main.tf"]


def test_vm_delivery_does_not_spend_second_repair_after_envelope_repair(tmp_path):
    (tmp_path / "run" / "application").mkdir(parents=True)
    calls = []

    def invoke(prompt):
        calls.append(json.loads(prompt))
        files = (
            {"main.tf": "terraform {}", "cloudinit.cfg": "bad"}
            if len(calls) == 1
            else {
                "main.tf": 'output "url" { value = "a" }',
                "outputs.tf": 'output "url" { value = "b" }',
            }
        )
        return json.dumps({"terraformFiles": files})

    with pytest.raises(ValueError, match="after one repair"):
        VmDeliveryAdapter(invoke).generate(
            requirements_result={},
            cloud_design_result={},
            implementation_result={"run_root": str(tmp_path / "run")},
        )

    assert len(calls) == 2


def test_vm_delivery_rejects_invalid_hcl_after_one_repair_call(tmp_path):
    (tmp_path / "run" / "application").mkdir(parents=True)
    calls = []

    def invoke(prompt):
        calls.append(prompt)
        return json.dumps(
            {
                "terraformFiles": {
                    "main.tf": 'output "url" { value = "a" }',
                    "outputs.tf": 'output "url" { value = "b" }',
                }
            }
        )

    adapter = VmDeliveryAdapter(invoke)

    with pytest.raises(ValueError, match=r'duplicate output "url"'):
        adapter.generate(
            requirements_result={},
            cloud_design_result={},
            implementation_result={"run_root": str(tmp_path / "run")},
        )

    assert len(calls) == 2
    repair = json.loads(calls[1])
    assert repair["validationStage"] == "hclPreflight"
    assert repair["validationErrors"] == ['duplicate output "url"']


def test_hcl_preflight_rejects_variable_defaults_that_reference_other_objects():
    errors = VmDeliveryAdapter._validate_files(
        {
            "variables.tf": (
                'variable "database_url" { '
                'default = "jdbc:postgresql://${aws_instance.db.private_ip}:5432/app" }\n'
                'variable "database_user" { default = var.db_user }\n'
            )
        }
    )

    assert errors == [
        'variable "database_url" default references another Terraform object',
        'variable "database_user" default references another Terraform object',
    ]


def test_vm_delivery_repairs_hcl_preflight_before_promotion(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    calls = []

    def invoke(prompt):
        calls.append(json.loads(prompt))
        files = (
            {
                "main.tf": 'output "url" { value = "a" }',
                "outputs.tf": 'output "url" { value = "b" }',
            }
            if len(calls) == 1
            else {"main.tf": 'output "url" { value = "a" }'}
        )
        return json.dumps({"terraformFiles": files, "deploymentNotes": []})

    result = VmDeliveryAdapter(invoke).generate(
        requirements_result={},
        cloud_design_result={},
        implementation_result={"run_root": str(tmp_path / "run")},
    )

    assert len(calls) == 2
    assert calls[1]["validationStage"] == "hclPreflight"
    assert result["preflight"]["repaired"] is True
    assert result["llmCalls"] == 2
    assert [event["operation"] for event in result["timingEvents"]] == [
        "iac.generate",
        "iac.hclPreflight",
        "iac.repair",
        "iac.hclPreflight",
    ]
    assert not (application / "infra" / "outputs.tf").exists()


def test_vm_delivery_turns_parser_exception_into_one_repair(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    calls = []

    def invoke(prompt):
        calls.append(prompt)
        source = "locals { invalid = [for }" if len(calls) == 1 else "locals { valid = true }"
        return json.dumps(
            {
                "terraformFiles": {"main.tf": source},
                "deploymentNotes": [],
            }
        )

    result = VmDeliveryAdapter(invoke).generate(
        requirements_result={},
        cloud_design_result={},
        implementation_result={"run_root": str(tmp_path / "run")},
    )

    assert len(calls) == 2
    assert result["preflight"]["repaired"] is True
    assert "valid = true" in (application / "infra" / "main.tf").read_text(encoding="utf-8")


def test_vm_delivery_forbids_data_disk_when_persistence_is_not_required(tmp_path):
    (tmp_path / "run" / "application").mkdir(parents=True)
    captured = {}

    def invoke(prompt):
        captured.update(json.loads(prompt))
        return json.dumps({"terraformFiles": {"main.tf": "terraform {}"}})

    VmDeliveryAdapter(invoke).generate(
        requirements_result={"deployment_needs": {"persistent_storage": {"required": False}}},
        cloud_design_result={},
        implementation_result={"run_root": str(tmp_path / "run")},
    )

    assert captured["applicationPersistentStorageRequired"] is False


def test_vm_delivery_preserves_explicit_persistent_disk_prohibition(tmp_path):
    (tmp_path / "run" / "application").mkdir(parents=True)
    captured = {}

    def invoke(prompt):
        captured.update(json.loads(prompt))
        return json.dumps({"terraformFiles": {"main.tf": "terraform {}"}})

    VmDeliveryAdapter(invoke).generate(
        requirements_result={
            "deployment_needs": {
                "persistent_storage": {
                    "required": True,
                    "decision": "accepted",
                    "metadata": {"persistent_application_disk": False},
                }
            }
        },
        cloud_design_result={},
        implementation_result={"run_root": str(tmp_path / "run")},
    )

    assert captured["applicationPersistentStorageRequired"] is False


def test_semantic_persistence_reaches_vm_delivery_without_fixed_capability_id(tmp_path):
    (tmp_path / "run" / "application").mkdir(parents=True)
    captured = {}
    application = ApplicationRuntimeContract(
        facts=[
            ContractFact(
                id="intent.catalog.state",
                kind="runtime.storage.intent",
                attributes={
                    "durability": "persistent",
                    "accessScope": "node-filesystem",
                    "accessPath": "/srv/catalog-data",
                },
            )
        ]
    )
    cloud = CloudCapabilityContract(
        facts=[
            ContractFact(
                id="capability.generated_name",
                kind="cloud.capability.generated_name",
                attributes={
                    "required": True,
                    "applicationState": {
                        "durability": "persistent",
                        "accessScope": "node-filesystem",
                        "accessPath": "/srv/catalog-data",
                    },
                },
            )
        ]
    )
    planned_cloud, bindings = derive_deployment_bindings(
        application, cloud, DeploymentBindingContract()
    )

    def invoke(prompt):
        captured.update(json.loads(prompt))
        return json.dumps({"terraformFiles": {"main.tf": "terraform {}"}})

    VmDeliveryAdapter(invoke).generate(
        requirements_result={
            "deployment_needs": {
                "generated_name": {
                    "required": True,
                    "decision": "accepted",
                    "metadata": {
                        "applicationState": {
                            "durability": "persistent",
                            "accessScope": "node-filesystem",
                            "accessPath": "/srv/catalog-data",
                        }
                    },
                }
            }
        },
        cloud_design_result={},
        implementation_result={"run_root": str(tmp_path / "run")},
        application_runtime_contract=application.model_dump(mode="json", by_alias=True),
        cloud_capability_contract=planned_cloud.model_dump(mode="json", by_alias=True),
        deployment_binding_contract=bindings.model_dump(mode="json", by_alias=True),
        enable_consistency_validator=False,
    )

    assert captured["applicationPersistentStorageRequired"] is True
    assert captured["applicationMountPath"] == "/srv/catalog-data"


def test_vm_delivery_supplies_pinned_azure_provider_compatibility(tmp_path):
    (tmp_path / "run" / "application").mkdir(parents=True)
    captured = {}

    def invoke(prompt):
        captured.update(json.loads(prompt))
        return json.dumps({"terraformFiles": {"main.tf": "terraform {}"}})

    VmDeliveryAdapter(invoke).generate(
        requirements_result={"resource_spec": {"provider": "azure"}},
        cloud_design_result={},
        implementation_result={"run_root": str(tmp_path / "run")},
    )

    compatibility = captured["providerCompatibility"]
    assert compatibility["providerConstraint"] == "hashicorp/azurerm 5.0.1"
    assert any(
        item["resourceType"] == "azurerm_network_interface_security_group_association"
        for item in compatibility["rules"]
    )
    assert any("create_option" in item["rule"] for item in compatibility["rules"])
    attachment = next(
        item
        for item in compatibility["rules"]
        if item["resourceType"] == "azurerm_virtual_machine_data_disk_attachment"
    )
    assert "managed_disk_id" in attachment["rule"]
    assert "Do not add a data_disk block" in attachment["rule"]


def test_vm_delivery_normalizes_nested_deployment_notes(tmp_path):
    run_root = tmp_path / "run"
    (run_root / "application").mkdir(parents=True)
    adapter = VmDeliveryAdapter(
        lambda _prompt: json.dumps(
            {
                "terraformFiles": {
                    "main.tf": 'terraform { required_version = ">= 1.5" }',
                    "deploymentNotes": ["Set the project variable."],
                }
            }
        )
    )

    result = adapter.generate(
        requirements_result={"resource_spec": {"provider": "gcp"}},
        cloud_design_result={},
        implementation_result={"run_root": str(run_root)},
    )

    assert result["files"] == ["easydep-provider.tf", "main.tf"]
    assert result["deploymentNotes"] == ["Set the project variable."]


def test_vm_delivery_rejects_ambiguous_nested_deployment_notes(tmp_path):
    run_root = tmp_path / "run"
    (run_root / "application").mkdir(parents=True)
    adapter = VmDeliveryAdapter(
        lambda _prompt: json.dumps(
            {
                "terraformFiles": {
                    "main.tf": 'terraform { required_version = ">= 1.5" }',
                    "deploymentNotes": "not-an-array",
                }
            }
        )
    )

    with pytest.raises(ValueError, match="top-level string array"):
        adapter.generate(
            requirements_result={"resource_spec": {"provider": "gcp"}},
            cloud_design_result={},
            implementation_result={"run_root": str(run_root)},
        )


def test_vm_delivery_repairs_provider_validation_failure_once_before_promotion(tmp_path):
    run_root = tmp_path / "run"
    application = run_root / "application"
    application.mkdir(parents=True)
    calls = []
    validations = []

    def invoke(prompt):
        calls.append(json.loads(prompt))
        resource = "aws_instance" if len(calls) == 1 else "aws_vpc"
        return json.dumps(
            {
                "terraformFiles": {"main.tf": f'resource "{resource}" "app" {{}}'},
                "deploymentNotes": [],
            }
        )

    def validate(files):
        validations.append(files)
        if "aws_instance" in files["main.tf"]:
            return {
                "status": "failed",
                "reports": [
                    {
                        "exitCode": 1,
                        "stderr": "Unsupported provider resource",
                    }
                ],
            }
        return {"status": "passed", "reports": []}

    result = VmDeliveryAdapter(invoke, validate).generate(
        requirements_result={"resource_spec": {"provider": "aws"}},
        cloud_design_result={},
        implementation_result={"run_root": str(run_root)},
    )

    assert len(calls) == 2
    assert len(validations) == 2
    assert calls[1]["task"] == "repairTerraform"
    assert calls[1]["validationStage"] == "providerSchema"
    assert calls[1]["validationErrors"] == ["Unsupported provider resource"]
    assert result["preflight"]["repaired"] is True
    assert result["llmCalls"] == 2
    assert "aws_vpc" in (application / "infra" / "main.tf").read_text(encoding="utf-8")


def test_vm_delivery_envelope_repair_repeats_exact_allowed_extensions(tmp_path):
    run_root = tmp_path / "run"
    (run_root / "application").mkdir(parents=True)
    prompts = []

    def invoke(prompt):
        prompts.append(json.loads(prompt))
        extension = "cfg" if len(prompts) == 1 else "tpl"
        return json.dumps(
            {
                "terraformFiles": {
                    "main.tf": 'resource "aws_instance" "app" {}',
                    f"cloudinit.{extension}": "#cloud-config",
                },
                "deploymentNotes": [],
            }
        )

    result = VmDeliveryAdapter(invoke).generate(
        requirements_result={},
        cloud_design_result={},
        implementation_result={"run_root": str(run_root)},
    )

    assert len(prompts) == 2
    assert ".tf, .tftpl, .tpl, and .sh" in prompts[1]["instruction"]
    assert result["files"] == ["cloudinit.tpl", "main.tf"]


def test_vm_delivery_does_not_promote_failed_provider_repair(tmp_path):
    run_root = tmp_path / "run"
    application = run_root / "application"
    application.mkdir(parents=True)
    adapter = VmDeliveryAdapter(
        lambda _prompt: json.dumps(
            {
                "terraformFiles": {"main.tf": 'resource "aws_instance" "app" {}'},
                "deploymentNotes": [],
            }
        ),
        lambda _files: {
            "status": "failed",
            "reports": [{"exitCode": 1, "stderr": "still invalid"}],
        },
    )

    with pytest.raises(ValueError, match="after one repair"):
        adapter.generate(
            requirements_result={"resource_spec": {"provider": "aws"}},
            cloud_design_result={},
            implementation_result={"run_root": str(run_root)},
        )

    assert not (application / "infra").exists()


def test_vm_delivery_no_verification_observes_but_does_not_repair_or_block(tmp_path):
    run_root = tmp_path / "run"
    application = run_root / "application"
    application.mkdir(parents=True)
    calls = []

    def invoke(prompt):
        calls.append(json.loads(prompt))
        return json.dumps(
            {
                "terraformFiles": {"main.tf": 'resource "aws_instance" "app" {}'},
                "deploymentNotes": [],
            }
        )

    result = VmDeliveryAdapter(
        invoke,
        lambda _files: {
            "status": "failed",
            "reports": [{"exitCode": 1, "stderr": "invalid provider claim"}],
        },
    ).generate(
        requirements_result={"resource_spec": {"provider": "aws"}},
        cloud_design_result={},
        implementation_result={"run_root": str(run_root)},
        enable_repair_feedback=False,
    )

    assert len(calls) == 1
    assert result["llmCalls"] == 1
    assert result["preflight"]["status"] == "failed-observed"
    assert result["preflight"]["repairFeedbackEnabled"] is False
    assert (application / "infra/main.tf").is_file()


def test_vm_delivery_records_provider_command_timings_on_failed_repair(tmp_path):
    run_root = tmp_path / "run"
    (run_root / "application").mkdir(parents=True)
    attempt = 0

    def validate(_files):
        nonlocal attempt
        attempt += 1
        return {
            "status": "failed",
            "reports": [
                {
                    "command": "validate",
                    "exitCode": 1,
                    "startedAt": f"2026-01-01T00:00:0{attempt}+00:00",
                    "finishedAt": f"2026-01-01T00:00:0{attempt + 1}+00:00",
                    "elapsedSeconds": 1.0,
                    "stderr": "invalid",
                }
            ],
        }

    adapter = VmDeliveryAdapter(
        lambda _prompt: json.dumps(
            {
                "terraformFiles": {"main.tf": "terraform {}"},
                "deploymentNotes": [],
            }
        ),
        validate,
    )

    with pytest.raises(ValueError, match="after one repair"):
        adapter.generate(
            requirements_result={},
            cloud_design_result={},
            implementation_result={"run_root": str(run_root)},
        )

    provider_events = [
        event
        for event in adapter.last_timing_events
        if event["operation"] == "iac.provider.validate"
    ]
    assert [event["attempt"] for event in provider_events] == ["initial", "repair"]
