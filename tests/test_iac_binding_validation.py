from app.core.orchestration.iac_binding_validation import (
    observe_terraform_plan,
    validate_iac_bindings,
    validate_managed_group_binding,
    validate_resource_plan_against_plan,
    validate_resource_plan_binding,
)


def test_literal_backend_port_matches_without_using_provider_resource_types():
    files = {
        "main.tf": """
resource "example_future_gateway" "app" {
  backend_http_settings { port = 8181 }
  listener { port = 443 }
}
"""
    }

    result = validate_iac_bindings(files, application_port=8181, mount_path=None)

    assert result["status"] == "passed"
    assert result["diagnostics"] == []
    assert {item["value"] for item in result["observations"]} == {8181}


def test_resource_plan_binding_rejects_missing_native_resource_and_attachment():
    plan = {
        "provider": "aws",
        "nodes": [
            {
                "id": "compute-state",
                "handling": "create",
                "terraformTypes": ["aws_instance"],
            },
            {
                "id": "disk",
                "handling": "create",
                "terraformTypes": ["aws_ebs_volume"],
            },
            {"id": "workload-state", "handling": "runtimeDerived"},
        ],
        "edges": [{"from": "compute-state", "to": "disk", "label": "attaches"}],
    }

    result = validate_resource_plan_binding(
        {"main.tf": 'resource "aws_instance" "state" {}'},
        resource_plan=plan,
    )

    assert result["status"] == "failed"
    assert {item["code"] for item in result["diagnostics"]} == {
        "BIND-RESOURCE-PLAN-NODE-001",
        "BIND-RESOURCE-PLAN-EDGE-001",
    }


def test_resource_plan_binding_accepts_disk_and_explicit_attachment():
    plan = {
        "provider": "aws",
        "nodes": [
            {
                "id": "compute-state",
                "handling": "create",
                "terraformTypes": ["aws_instance"],
            },
            {
                "id": "disk",
                "handling": "create",
                "terraformTypes": ["aws_ebs_volume"],
            },
        ],
        "edges": [{"from": "compute-state", "to": "disk", "label": "attaches"}],
    }
    files = {
        "main.tf": """
resource "aws_instance" "state" {}
resource "aws_ebs_volume" "state" {}
resource "aws_volume_attachment" "state" {}
"""
    }

    result = validate_resource_plan_binding(files, resource_plan=plan)

    assert result["status"] == "passed"
    assert result["diagnostics"] == []


def test_resource_plan_binding_rejects_unrequested_https_upgrade():
    plan = {
        "provider": "gcp",
        "nodes": [
            {
                "id": "endpoint-api-http",
                "group": "endpoint",
                "protocol": "http",
                "handling": "runtimeDerived",
            }
        ],
        "edges": [],
    }
    files = {
        "startup.sh.tpl": "listen 443 ssl;\nssl_certificate /run/tls.crt;\n"
    }

    result = validate_resource_plan_binding(files, resource_plan=plan)

    assert result["status"] == "failed"
    assert result["diagnostics"][0]["code"] == "BIND-RESOURCE-PLAN-ENDPOINT-001"


def test_resource_plan_binding_rejects_https_without_tls_termination():
    plan = {
        "provider": "aws",
        "nodes": [
            {
                "id": "endpoint-api-https",
                "group": "endpoint",
                "protocol": "https",
                "handling": "runtimeDerived",
            }
        ],
        "edges": [],
    }

    result = validate_resource_plan_binding(
        {"main.tf": 'resource "aws_instance" "api" {}'}, resource_plan=plan
    )

    assert result["status"] == "failed"
    assert result["diagnostics"][0]["code"] == "BIND-RESOURCE-PLAN-ENDPOINT-002"


def test_gcp_resource_plan_binding_rejects_disconnected_lb_resources():
    plan = {
        "provider": "gcp",
        "nodes": [
            {
                "id": "forwarding-rule",
                "handling": "create",
                "terraformTypes": ["google_compute_global_forwarding_rule"],
            },
            {
                "id": "target-http-proxy",
                "handling": "create",
                "terraformTypes": ["google_compute_target_http_proxy"],
            },
        ],
        "edges": [
            {
                "from": "forwarding-rule",
                "to": "target-http-proxy",
                "label": "forwards to",
            }
        ],
    }
    disconnected = {
        "main.tf": """
resource "google_compute_target_http_proxy" "app" {}
resource "google_compute_global_forwarding_rule" "app" {
  target = "not-a-resource-reference"
}
"""
    }
    connected = {
        "main.tf": """
resource "google_compute_target_http_proxy" "app" {}
resource "google_compute_global_forwarding_rule" "app" {
  target = google_compute_target_http_proxy.app.id
}
"""
    }

    failed = validate_resource_plan_binding(disconnected, resource_plan=plan)
    passed = validate_resource_plan_binding(connected, resource_plan=plan)

    assert failed["status"] == "failed"
    assert any(
        item["code"] == "BIND-RESOURCE-PLAN-EDGE-002"
        for item in failed["diagnostics"]
    )
    assert passed["status"] == "passed"


def test_gcp_plan_json_observes_resource_reference_pairs():
    observation = observe_terraform_plan(
        {
            "format_version": "1.2",
            "terraform_version": "1.9.0",
            "planned_values": {
                "root_module": {
                    "resources": [
                        {
                            "address": "google_compute_global_forwarding_rule.app",
                            "type": "google_compute_global_forwarding_rule",
                        },
                        {
                            "address": "google_compute_target_https_proxy.app",
                            "type": "google_compute_target_https_proxy",
                        },
                    ]
                }
            },
            "configuration": {
                "root_module": {
                    "resources": [
                        {
                            "address": "google_compute_global_forwarding_rule.app",
                            "type": "google_compute_global_forwarding_rule",
                            "expressions": {
                                "target": {
                                    "references": [
                                        "google_compute_target_https_proxy.app.id"
                                    ]
                                }
                            },
                        },
                        {
                            "address": "google_compute_target_https_proxy.app",
                            "type": "google_compute_target_https_proxy",
                            "expressions": {},
                        },
                    ]
                }
            },
        }
    )

    assert observation["referencePairs"] == [
        {
            "fromType": "google_compute_global_forwarding_rule",
            "toType": "google_compute_target_https_proxy",
        }
    ]


def test_plan_json_observation_excludes_values_and_checks_resolved_counts():
    observation = observe_terraform_plan(
        {
            "format_version": "1.2",
            "terraform_version": "1.9.0",
            "planned_values": {
                "root_module": {
                    "resources": [
                        {
                            "address": "aws_instance.state",
                            "type": "aws_instance",
                            "values": {"user_data": "secret-value"},
                        },
                        {
                            "address": "aws_ebs_volume.state",
                            "type": "aws_ebs_volume",
                            "values": {"encrypted": True},
                        },
                        {
                            "address": "aws_volume_attachment.state",
                            "type": "aws_volume_attachment",
                            "values": {},
                        },
                    ]
                }
            },
        }
    )
    plan = {
        "provider": "aws",
        "nodes": [
            {
                "id": "compute-state",
                "handling": "create",
                "terraformTypes": ["aws_instance"],
            },
            {
                "id": "disk",
                "handling": "create",
                "terraformTypes": ["aws_ebs_volume"],
            },
        ],
        "edges": [{"from": "compute-state", "to": "disk", "label": "attaches"}],
    }

    result = validate_resource_plan_against_plan(plan, observation)

    assert observation["resourceCounts"] == {
        "aws_ebs_volume": 1,
        "aws_instance": 1,
        "aws_volume_attachment": 1,
    }
    assert "secret-value" not in str(observation)
    assert result["status"] == "passed"
    assert all(item["status"] == "passed" for item in result["checks"])


def test_unavailable_plan_json_is_not_counted_as_success_or_failure():
    result = validate_resource_plan_against_plan(
        {"provider": "aws", "nodes": [], "edges": []},
        {"status": "not-observed", "reason": "variables are missing"},
    )

    assert result == {
        "status": "not-observed",
        "checks": [],
        "diagnostics": [],
        "reason": "variables are missing",
    }


def test_observable_backend_port_conflict_is_rejected():
    result = validate_iac_bindings(
        {"main.tf": 'resource "unknown_service" "app" { backend_port = 9090 }'},
        application_port=8080,
        mount_path=None,
    )

    assert result["status"] == "failed"
    assert result["diagnostics"][0]["code"] == "BIND-PORT-001"
    assert result["diagnostics"][0]["details"]["observed"] == [9090]


def test_dynamic_backend_port_is_unresolved_not_failed():
    result = validate_iac_bindings(
        {"main.tf": 'resource "unknown_service" "app" { backend_port = var.app_port }'},
        application_port=8080,
        mount_path=None,
    )

    assert result["status"] == "passed"
    assert result["unresolved"][0]["code"] == "BIND-PORT-UNRESOLVED"


def test_mount_command_is_checked_against_contract_path():
    result = validate_iac_bindings(
        {
            "main.tf": 'resource "unknown_vm" "app" {}',
            "bootstrap.sh": "blkid /dev/data || mkfs /dev/data\nmount /dev/data /srv/state\n",
        },
        application_port=8080,
        mount_path="/srv/state",
    )

    assert result["status"] == "passed"
    assert any(item["value"] == "/srv/state" for item in result["observations"])


def test_unconditional_filesystem_format_is_rejected_for_persistent_storage():
    unsafe = validate_iac_bindings(
        {
            "bootstrap.sh": (
                "mkfs.ext4 -F /dev/data\n"
                "mount /dev/data /mnt/data\n"
                "docker run -v /mnt/data:/srv/state app\n"
            )
        },
        application_port=8080,
        mount_path="/srv/state",
    )
    guarded = validate_iac_bindings(
        {
            "bootstrap.sh": (
                "if ! blkid /dev/data; then\n"
                "  mkfs.ext4 /dev/data\n"
                "fi\n"
                "mount /dev/data /mnt/data\n"
                "docker run -v /mnt/data:/srv/state app\n"
            )
        },
        application_port=8080,
        mount_path="/srv/state",
    )

    assert unsafe["status"] == "failed"
    assert "BIND-STORAGE-DESTRUCTIVE-INIT" in {item["code"] for item in unsafe["diagnostics"]}
    assert guarded["status"] == "passed"


def test_first_enumerated_block_device_is_rejected_for_persistent_storage():
    result = validate_iac_bindings(
        {
            "bootstrap.sh": (
                "DISK=$(lsblk -dpno NAME | grep -v /dev/root | head -n 1)\n"
                "if ! blkid $DISK; then mkfs.ext4 $DISK; fi\n"
                "mount $DISK /mnt/data\n"
                "docker run -v /mnt/data:/srv/state app\n"
            )
        },
        application_port=8080,
        mount_path="/srv/state",
    )

    assert result["status"] == "failed"
    assert "BIND-STORAGE-DEVICE-AMBIGUOUS" in {item["code"] for item in result["diagnostics"]}


def test_absent_required_mount_is_rejected_but_dynamic_target_is_unresolved():
    absent = validate_iac_bindings(
        {"main.tf": 'resource "unknown_vm" "app" {}'},
        application_port=8080,
        mount_path="/srv/state",
    )
    dynamic = validate_iac_bindings(
        {"bootstrap.sh": "mount /dev/data ${application_mount_path}\n"},
        application_port=8080,
        mount_path="/srv/state",
    )

    assert "BIND-STORAGE-001" in {item["code"] for item in absent["diagnostics"]}
    assert dynamic["status"] == "passed"
    assert "BIND-STORAGE-UNRESOLVED" in {item["code"] for item in dynamic["unresolved"]}


def test_container_target_is_not_confused_with_a_different_guest_mount_path():
    valid = validate_iac_bindings(
        {"bootstrap.sh": "mount /dev/data /mnt/data\ndocker run -v /mnt/data:/srv/state app\n"},
        application_port=8080,
        mount_path="/srv/state",
    )
    invalid = validate_iac_bindings(
        {"bootstrap.sh": "mount /dev/data /srv/state\ndocker run -v /srv/state:/wrong app\n"},
        application_port=8080,
        mount_path="/srv/state",
    )

    assert valid["status"] == "passed"
    assert invalid["status"] == "failed"
    diagnostic = invalid["diagnostics"][0]
    assert diagnostic["code"] == "BIND-STORAGE-001"
    assert diagnostic["details"]["observedBoundary"] == "container"


def test_managed_availability_binding_accepts_native_groups_and_health_policy():
    cases = {
        "aws": """
resource "aws_launch_template" "app" { instance_type = "t3.medium" }
resource "aws_autoscaling_group" "app" {
  health_check_type = "ELB"
  target_group_arns = [aws_lb_target_group.app.arn]
}
""",
        "azure": """
resource "azurerm_linux_virtual_machine_scale_set" "app" {
  sku = "Standard_D2s_v5"
  automatic_instance_repair { enabled = true }
}
""",
        "gcp": """
resource "google_compute_instance_template" "app" { machine_type = "e2-medium" }
resource "google_compute_region_instance_group_manager" "app" {
  auto_healing_policies { health_check = google_compute_health_check.app.id }
}
""",
    }

    for provider, terraform in cases.items():
        result = validate_managed_group_binding(
            {"main.tf": terraform}, provider=provider, required=True
        )
        assert result["status"] == "passed", (provider, result)


def test_managed_group_binding_rejects_standalone_vm():
    result = validate_managed_group_binding(
        {"main.tf": 'resource "aws_instance" "app" {}'},
        provider="aws",
        required=True,
    )

    assert result["status"] == "failed"
    assert {item["code"] for item in result["diagnostics"]} == {"BIND-GROUP-001"}
