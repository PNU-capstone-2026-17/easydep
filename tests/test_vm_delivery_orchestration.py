from __future__ import annotations

import json

import pytest

from app.core.orchestration.adapters.vm_delivery import VmDeliveryAdapter


def test_vm_delivery_writes_only_returned_terraform(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    captured = {}

    def invoke(prompt: str) -> str:
        captured.update(json.loads(prompt))
        return json.dumps({
            "terraformFiles": {"main.tf": "resource \"aws_instance\" \"app\" {}"},
            "deploymentNotes": ["certificate is supplied by variable"],
        })

    result = VmDeliveryAdapter(invoke).generate(
        requirements_result={
            "resource_spec": {"provider": "aws"},
            "deployment_needs": {"instance_count": {"metadata": {"count": 1}}},
        },
        cloud_design_result={
            "infra_intent": {
                "csp": "aws",
                "region": "ap-northeast-2",
                "startResources": ["vm"],
                "resources": [{
                    "id": "vm",
                    "provisioningStatus": "selectedStartResource",
                    "because": [],
                    "detail": "Korean text must not cross the boundary",
                }],
                "createOrder": ["vm"],
                "constraints": [],
            },
            "kb_used": ["depkb"],
        },
        implementation_result={"run_root": str(tmp_path / "run")},
    )

    assert result["cloudKbProvided"] is True
    assert (application / "infra" / "main.tf").read_text(encoding="utf-8").endswith("\n")
    assert captured["dependencyPlan"]["resources"] == [
        {"id": "vm", "provisioningStatus": "selectedStartResource"}
    ]
    assert "Korean text" not in json.dumps(captured)
    assert (application / "Dockerfile").is_file()
    assert (application / ".dockerignore").is_file()
    assert result["containerFilesCreated"] == ["Dockerfile", ".dockerignore"]
    assert result["vmSelection"]["status"] == "deferred"
    assert captured["vmSelection"] == result["vmSelection"]


def test_vm_delivery_preserves_existing_container_files(tmp_path):
    application = tmp_path / "run" / "application"
    application.mkdir(parents=True)
    (application / "Dockerfile").write_text("FROM custom", encoding="utf-8")
    adapter = VmDeliveryAdapter(
        lambda _prompt: json.dumps({
            "terraformFiles": {"main.tf": 'resource "aws_instance" "app" {}'}
        })
    )

    result = adapter.generate(
        requirements_result={},
        cloud_design_result={},
        implementation_result={"run_root": str(tmp_path / "run")},
    )

    assert (application / "Dockerfile").read_text(encoding="utf-8") == "FROM custom"
    assert result["containerFilesCreated"] == [".dockerignore"]


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
    adapter = VmDeliveryAdapter(lambda _prompt: json.dumps({
        "terraformFiles": {
            "main.tf": 'locals { cloud_init = templatefile("cloud_init.tpl", {}) }',
            "cloud_init.tpl": "#!/bin/sh",
        }
    }))

    result = adapter.generate(
        requirements_result={},
        cloud_design_result={},
        implementation_result={"run_root": str(tmp_path / "run")},
    )

    assert result["files"] == ["cloud_init.tpl", "main.tf"]


def test_vm_delivery_rejects_invalid_hcl_without_a_repair_call(tmp_path):
    (tmp_path / "run" / "application").mkdir(parents=True)
    calls = []

    def invoke(prompt):
        calls.append(prompt)
        return json.dumps({"terraformFiles": {
            "main.tf": 'output "url" { value = "a" }',
            "outputs.tf": 'output "url" { value = "b" }',
        }})

    adapter = VmDeliveryAdapter(invoke)

    with pytest.raises(ValueError, match=r'duplicate output "url"'):
        adapter.generate(
            requirements_result={},
            cloud_design_result={},
            implementation_result={"run_root": str(tmp_path / "run")},
        )

    assert len(calls) == 1


def test_vm_delivery_forbids_data_disk_when_persistence_is_not_required(tmp_path):
    (tmp_path / "run" / "application").mkdir(parents=True)
    captured = {}

    def invoke(prompt):
        captured.update(json.loads(prompt))
        return json.dumps({"terraformFiles": {"main.tf": "terraform {}"}})

    VmDeliveryAdapter(invoke).generate(
        requirements_result={
            "deployment_needs": {"persistent_storage": {"required": False}}
        },
        cloud_design_result={},
        implementation_result={"run_root": str(tmp_path / "run")},
    )

    assert captured["applicationPersistentStorageRequired"] is False


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

    assert result["files"] == ["main.tf"]
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
