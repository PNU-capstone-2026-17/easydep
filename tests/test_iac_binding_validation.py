from app.core.orchestration.iac_binding_validation import validate_iac_bindings


def test_literal_backend_port_matches_without_using_provider_resource_types():
    files = {
        "main.tf": """
resource "example_future_gateway" "app" {
  backend_http_settings { port = 8181 }
  listener { port = 443 }
}
"""
    }

    result = validate_iac_bindings(
        files, application_port=8181, mount_path=None
    )

    assert result["status"] == "passed"
    assert result["diagnostics"] == []
    assert {item["value"] for item in result["observations"]} == {8181}


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
    assert "BIND-STORAGE-DESTRUCTIVE-INIT" in {
        item["code"] for item in unsafe["diagnostics"]
    }
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
    assert "BIND-STORAGE-DEVICE-AMBIGUOUS" in {
        item["code"] for item in result["diagnostics"]
    }


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

    assert "BIND-STORAGE-001" in {
        item["code"] for item in absent["diagnostics"]
    }
    assert dynamic["status"] == "passed"
    assert "BIND-STORAGE-UNRESOLVED" in {
        item["code"] for item in dynamic["unresolved"]
    }


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
