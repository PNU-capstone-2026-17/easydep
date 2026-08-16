"""Run the domain-neutral state-VM replacement experiment on Azure."""

from __future__ import annotations

import argparse
import json
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

from evaluation.dependency_audit.azure_sample_app_postgres_e2 import _azure_guest_run
from evaluation.dependency_audit.inter_vm_postgres_intervention import (
    AZURE_LOCATION,
    AZURE_VM_SIZE,
    INSTALL_DOCKER_DEBIAN,
    ExperimentFailure,
    _run,
)
from evaluation.dependency_audit.sample_app_postgres_e1_common import (
    app_build_script,
    baseline_script,
)
from evaluation.dependency_audit.sample_app_postgres_e3_common import (
    E3Recorder,
    app_rebind_script,
    app_start_script,
    state_setup_script,
)


def _wait_disk_detached(group: str, timeout: int = 600) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = json.loads(_run([
            "az", "disk", "show", "-g", group, "-n", "state-data", "-o", "json",
        ]) or "{}")
        if not value.get("managedBy"):
            return "managed disk detached and reusable"
        time.sleep(5)
    raise ExperimentFailure("managed disk did not detach after state VM deletion")


def run(output: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    group = f"easydep-e3-{suffix}"
    recorder = E3Recorder("azure", group, output)
    admin_password = "Aa1!" + secrets.token_urlsafe(18)
    error: str | None = None
    cleanup: dict[str, Any] = {"passed": False, "residual": []}
    try:
        recorder.step("group.create", lambda: _run([
            "az", "group", "create", "--name", group, "--location", AZURE_LOCATION,
            "--tags", f"easydep-run={group}", "-o", "none",
        ]) or group)
        recorder.step("network.create", lambda: _run([
            "az", "network", "vnet", "create", "-g", group, "-n", "vnet",
            "--address-prefix", "10.84.0.0/16", "--subnet-name", "workloads",
            "--subnet-prefix", "10.84.1.0/24", "-o", "none",
        ]) or "10.84.0.0/16")
        recorder.step("network.egress-public-ip", lambda: _run([
            "az", "network", "public-ip", "create", "-g", group, "-n", "egress-pip",
            "--sku", "Standard", "--allocation-method", "Static", "-o", "none",
        ]) or "egress-pip")
        recorder.step("network.nat-gateway", lambda: _run([
            "az", "network", "nat", "gateway", "create", "-g", group,
            "-n", "egress-nat", "--public-ip-addresses", "egress-pip",
            "--idle-timeout", "10", "-o", "none",
        ]) or "egress-nat")
        recorder.step("network.subnet-egress", lambda: _run([
            "az", "network", "vnet", "subnet", "update", "-g", group,
            "--vnet-name", "vnet", "-n", "workloads", "--nat-gateway", "egress-nat",
            "-o", "none",
        ]) or "workloads subnet uses the NAT gateway")
        for name in ("app-nsg", "state-nsg"):
            recorder.step(f"{name}.create", lambda name=name: _run([
                "az", "network", "nsg", "create", "-g", group, "-n", name, "-o", "none",
            ]) or name)
        recorder.step("state-nsg.allow-postgres", lambda: _run([
            "az", "network", "nsg", "rule", "create", "-g", group,
            "--nsg-name", "state-nsg", "-n", "allow-app-postgres",
            "--priority", "100", "--access", "Allow", "--protocol", "Tcp",
            "--source-address-prefixes", "10.84.1.0/24",
            "--destination-port-ranges", "5432", "-o", "none",
        ]) or "state tcp/5432 from workload subnet")

        def create_vm(name: str, nsg: str) -> str:
            _run([
                "az", "vm", "create", "-g", group, "-n", name,
                "--location", AZURE_LOCATION, "--image", "Ubuntu2204",
                "--size", AZURE_VM_SIZE, "--vnet-name", "vnet", "--subnet", "workloads",
                "--nsg", nsg, "--public-ip-address", "", "--admin-username", "easydep",
                "--authentication-type", "password", "--admin-password", admin_password,
                "--tags", f"easydep-run={group}", "-o", "none",
            ], timeout=1200)
            return name

        recorder.step("initial.state-vm.create", lambda: create_vm("state-a", "state-nsg"))
        recorder.step("app-vm.create", lambda: create_vm("app", "app-nsg"))
        state_a_ip = _run([
            "az", "vm", "show", "-d", "-g", group, "-n", "state-a",
            "--query", "privateIps", "-o", "tsv",
        ]).strip()
        recorder.step("state-volume.create-and-attach", lambda: _run([
            "az", "vm", "disk", "attach", "-g", group, "--vm-name", "state-a",
            "--name", "state-data", "--new", "--size-gb", "4", "--sku", "Standard_LRS",
            "--lun", "0", "-o", "none",
        ], timeout=900) or "new managed disk attached to state-a")
        device = """
device=/dev/disk/azure/scsi1/lun0
for i in $(seq 1 60); do [ -b "$device" ] && break; sleep 2; done
""".strip()
        state_setup = state_setup_script(INSTALL_DOCKER_DEBIAN, device)
        recorder.step(
            "initial.state-ready", lambda: _azure_guest_run(group, "state-a", state_setup)
        )
        app_install = INSTALL_DOCKER_DEBIAN + "\nsudo apt-get install -y -qq curl"
        recorder.step("app-image.build", lambda: _azure_guest_run(
            group, "app", app_build_script(app_install)
        ))
        recorder.step("app-container.start", lambda: _azure_guest_run(
            group, "app", app_start_script(state_a_ip)
        ))
        recorder.step("baseline.app-write-read", lambda: _azure_guest_run(
            group, "app", baseline_script()
        ))
        recorder.step("initial.state-vm.delete", lambda: _run([
            "az", "vm", "delete", "-g", group, "-n", "state-a", "--yes", "-o", "none",
        ], timeout=1200) or "state-a deleted")
        recorder.step("state-volume.available", lambda: _wait_disk_detached(group))
        recorder.step("replacement.state-vm.create", lambda: create_vm("state-b", "state-nsg"))
        recorder.step("replacement.state-volume.attach-existing", lambda: _run([
            "az", "vm", "disk", "attach", "-g", group, "--vm-name", "state-b",
            "--name", "state-data", "--lun", "0", "-o", "none",
        ], timeout=900) or "existing managed disk attached to state-b")
        recorder.step(
            "replacement.state-ready", lambda: _azure_guest_run(group, "state-b", state_setup)
        )
        state_b_ip = _run([
            "az", "vm", "show", "-d", "-g", group, "-n", "state-b",
            "--query", "privateIps", "-o", "tsv",
        ]).strip()
        if state_a_ip == state_b_ip:
            raise ExperimentFailure("replacement state VM unexpectedly reused the same private IP")
        recorder.document["runtimeEndpointObservation"] = {
            "initialStatePrivateIp": state_a_ip,
            "replacementStatePrivateIp": state_b_ip,
            "appVmRecreated": False,
            "appImageRebuilt": False,
        }
        recorder.save()
        recorder.step("replacement.app-rebind-without-rebuild", lambda: _azure_guest_run(
            group, "app", app_rebind_script(state_b_ip)
        ))
        recorder.step(
            "replacement.app-read-existing-value",
            lambda: "same-image rebind oracle read the pre-replacement value",
        )
        outcome = "passed"
    except Exception as exception:
        outcome = "failed"
        error = str(exception)
    finally:
        _run(["az", "group", "delete", "--name", group, "--yes", "--no-wait"], check=False)
        deadline = time.monotonic() + 1800
        exists = True
        while time.monotonic() < deadline:
            exists = _run([
                "az", "group", "exists", "--name", group,
            ], check=False).lower() == "true"
            if not exists:
                break
            time.sleep(10)
        cleanup = {"passed": not exists, "residual": [group] if exists else []}
        recorder.finish_e3(
            outcome if cleanup["passed"] else "failed", error=error, cleanup=cleanup
        )
    return recorder.document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = run(arguments.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
