"""Azure Standard Load Balancer의 L4 백엔드 의존성을 중립 앱으로 실측한다."""

from __future__ import annotations

import argparse
import json
import secrets
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from evaluation.dependency_audit.inter_vm_postgres_intervention import (
    AZURE_LOCATION,
    AZURE_VM_SIZE,
    ExperimentFailure,
    _run,
)
from evaluation.dependency_audit.managed_l4_ingress_common import (
    ManagedL4Recorder,
    exercise_fault_exclusion_and_restore,
    startup_script,
    wait_for_instances,
    wait_http,
)


def run(output: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    group = f"easydep-l4-{suffix}"
    recorder = ManagedL4Recorder("azure", group, output)
    password = "Aa1!" + secrets.token_urlsafe(18)
    fault_token = uuid.uuid4().hex
    error: str | None = None
    cleanup: dict[str, Any] = {"passed": False, "residual": []}
    try:
        recorder.step("resource-group.create", lambda: _run([
            "az", "group", "create", "--name", group, "--location", AZURE_LOCATION,
            "--tags", f"easydep-run={group}", "-o", "none",
        ]) or group)
        recorder.step("network.create", lambda: _run([
            "az", "network", "vnet", "create", "-g", group, "-n", "vnet",
            "--address-prefix", "10.91.0.0/16", "--subnet-name", "backends",
            "--subnet-prefix", "10.91.1.0/24", "-o", "none",
        ]) or "vnet/backends")
        recorder.step("security.create", lambda: _run([
            "az", "network", "nsg", "create", "-g", group, "-n", "backend-nsg",
            "-o", "none",
        ]) or "backend-nsg")
        recorder.step("security.client-http", lambda: _run([
            "az", "network", "nsg", "rule", "create", "-g", group,
            "--nsg-name", "backend-nsg", "-n", "allow-l4-client",
            "--priority", "100", "--access", "Allow", "--protocol", "Tcp",
            "--source-address-prefixes", "Internet", "--destination-port-ranges", "8080",
            "-o", "none",
        ]) or "internet tcp/8080")
        recorder.step("security.health-probe", lambda: _run([
            "az", "network", "nsg", "rule", "create", "-g", group,
            "--nsg-name", "backend-nsg", "-n", "allow-lb-health",
            "--priority", "110", "--access", "Allow", "--protocol", "Tcp",
            "--source-address-prefixes", "AzureLoadBalancer",
            "--destination-port-ranges", "8080", "-o", "none",
        ]) or "AzureLoadBalancer tcp/8080")
        recorder.step("load-balancer.public-ip", lambda: _run([
            "az", "network", "public-ip", "create", "-g", group, "-n", "lb-pip",
            "--sku", "Standard", "--allocation-method", "Static", "-o", "none",
        ]) or "lb-pip")
        recorder.step("load-balancer.create", lambda: _run([
            "az", "network", "lb", "create", "-g", group, "-n", "l4-lb",
            "--sku", "Standard", "--public-ip-address", "lb-pip",
            "--frontend-ip-name", "public", "--backend-pool-name", "backends",
            "-o", "none",
        ]) or "l4-lb")
        recorder.step("load-balancer.health-probe", lambda: _run([
            "az", "network", "lb", "probe", "create", "-g", group,
            "--lb-name", "l4-lb", "-n", "ready", "--protocol", "Http",
            "--port", "8080", "--path", "/health/ready", "--interval", "10",
            "--threshold", "2", "-o", "none",
        ]) or "HTTP /health/ready")
        recorder.step("load-balancer.rule", lambda: _run([
            "az", "network", "lb", "rule", "create", "-g", group,
            "--lb-name", "l4-lb", "-n", "tcp-80", "--protocol", "Tcp",
            "--frontend-ip-name", "public", "--frontend-port", "80",
            "--backend-pool-name", "backends", "--backend-port", "8080",
            "--probe-name", "ready", "--disable-outbound-snat", "true", "-o", "none",
        ]) or "tcp/80 -> tcp/8080")

        with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
            cloud_init = Path(temporary) / "startup.sh"
            cloud_init.write_text(
                startup_script(port=8080, fault_token=fault_token), encoding="utf-8"
            )
            for index, zone in ((1, "1"), (2, "2")):
                name = f"app-{index}"
                recorder.step(f"{name}.nic", lambda name=name: _run([
                    "az", "network", "nic", "create", "-g", group, "-n", f"{name}-nic",
                    "--vnet-name", "vnet", "--subnet", "backends",
                    "--network-security-group", "backend-nsg", "--lb-name", "l4-lb",
                    "--lb-address-pools", "backends", "-o", "none",
                ]) or f"{name}-nic")
                recorder.step(f"{name}.create", lambda name=name, zone=zone: _run([
                    "az", "vm", "create", "-g", group, "-n", name,
                    "--computer-name", name, "--location", AZURE_LOCATION,
                    "--zone", zone, "--image", "Ubuntu2204", "--size", AZURE_VM_SIZE,
                    "--nics", f"{name}-nic", "--custom-data", str(cloud_init),
                    "--admin-username", "easydep", "--authentication-type", "password",
                    "--admin-password", password, "--tags", f"easydep-run={group}",
                    "-o", "none",
                ], timeout=1200) or name)

        public_ip = _run([
            "az", "network", "public-ip", "show", "-g", group, "-n", "lb-pip",
            "--query", "ipAddress", "-o", "tsv",
        ]).strip()
        base_url = f"http://{public_ip}"
        recorder.step("baseline.readiness", lambda: wait_http(
            "GET", f"{base_url}/health/ready", 200, budget=900
        ))
        expected = {"app-1", "app-2"}
        recorder.step("baseline.two-backends", lambda: wait_for_instances(
            base_url, expected, timeout=300
        ))

        def restore(victim: str) -> str:
            if victim not in expected:
                raise ExperimentFailure(f"unknown Azure backend {victim!r}")
            _run([
                "az", "vm", "run-command", "invoke", "-g", group, "-n", victim,
                "--command-id", "RunShellScript", "--scripts",
                "sudo /usr/local/bin/easydep-l4-start", "-o", "none",
            ], timeout=600)
            return f"RunShellScript restarted the app on {victim}"

        recorder.step("fault.exclude-and-restore", lambda: exercise_fault_exclusion_and_restore(
            base_url, expected, fault_token, restore
        ))
        recorder.document["resourceObservation"] = {
            "frontend": "Azure Standard Load Balancer TCP/80",
            "health": "HTTP /health/ready on tcp/8080",
            "backends": "two zonal Linux VMs associated through NIC backend membership",
        }
        recorder.save()
        outcome = "passed"
    except Exception as exception:  # noqa: BLE001 - cleanup must run for every failure.
        outcome = "failed"
        error = str(exception)
    finally:
        _run(["az", "group", "delete", "--name", group, "--yes", "--no-wait"], check=False)
        deadline = time.monotonic() + 1800
        exists = True
        while time.monotonic() < deadline:
            exists = _run(["az", "group", "exists", "--name", group], check=False).lower() == "true"
            if not exists:
                break
            time.sleep(10)
        cleanup = {"passed": not exists, "residual": [group] if exists else []}
        recorder.finish_l4(outcome if cleanup["passed"] else "failed", error=error, cleanup=cleanup)
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
