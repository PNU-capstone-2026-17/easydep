"""GCP regional external passthrough Network Load Balancer를 중립 앱으로 실측한다."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from evaluation.dependency_audit.inter_vm_postgres_intervention import (
    GCP_REGION,
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

GCP_ZONES = ("asia-northeast3-a", "asia-northeast3-c")


def _healthy_backends(project: str, backend: str) -> list[str]:
    raw = _run([
        "gcloud", "compute", "backend-services", "get-health", backend,
        "--project", project, "--region", GCP_REGION, "--format=json",
    ])
    values = json.loads(raw or "[]")
    return sorted(
        str(health.get("instance") or "").rsplit("/", 1)[-1]
        for group in values
        for health in group.get("status", {}).get("healthStatus", [])
        if health.get("healthState") == "HEALTHY"
    )


def _wait_healthy(project: str, backend: str, expected: int, *, timeout: int) -> list[str]:
    deadline = time.monotonic() + timeout
    last: list[str] = []
    while time.monotonic() < deadline:
        last = _healthy_backends(project, backend)
        if len(last) == expected:
            return last
        time.sleep(10)
    raise ExperimentFailure(f"GCP healthy backend count {expected} not observed: {last}")


def run(output: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    prefix = f"easydep-l4-{suffix}"
    recorder = ManagedL4Recorder("gcp", prefix, output)
    fault_token = uuid.uuid4().hex
    project = _run(["gcloud", "config", "get-value", "project"]).strip()
    network = f"{prefix}-net"
    subnet = f"{prefix}-subnet"
    firewall = f"{prefix}-ingress"
    health = f"{prefix}-health"
    backend = f"{prefix}-backend"
    address = f"{prefix}-address"
    forwarding = f"{prefix}-forwarding"
    instances = {
        f"{prefix}-app-1": GCP_ZONES[0],
        f"{prefix}-app-2": GCP_ZONES[1],
    }
    groups = {
        f"{prefix}-group-1": GCP_ZONES[0],
        f"{prefix}-group-2": GCP_ZONES[1],
    }
    tag = f"{prefix}-backend"
    error: str | None = None
    cleanup: dict[str, Any] = {"passed": False, "residual": []}
    try:
        recorder.step("network.create", lambda: _run([
            "gcloud", "compute", "networks", "create", network, "--project", project,
            "--subnet-mode", "custom", "--format=value(name)", "--quiet",
        ]) or network)
        recorder.step("subnet.create", lambda: _run([
            "gcloud", "compute", "networks", "subnets", "create", subnet,
            "--project", project, "--network", network, "--region", GCP_REGION,
            "--range", "10.92.0.0/24", "--format=value(name)", "--quiet",
        ]) or subnet)
        recorder.step("security.client-and-health", lambda: _run([
            "gcloud", "compute", "firewall-rules", "create", firewall,
            "--project", project, "--network", network, "--direction", "INGRESS",
            "--action", "ALLOW", "--rules", "tcp:80", "--source-ranges", "0.0.0.0/0",
            "--target-tags", tag, "--format=value(name)", "--quiet",
        ]) or "client and health-check traffic to tcp/80")

        with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
            script = Path(temporary) / "startup.sh"
            script.write_text(startup_script(port=80, fault_token=fault_token), encoding="utf-8")
            for name, zone in instances.items():
                recorder.step(f"{name}.create", lambda name=name, zone=zone: _run([
                    "gcloud", "compute", "instances", "create", name,
                    "--project", project, "--zone", zone, "--machine-type", "e2-micro",
                    "--network", network, "--subnet", subnet, "--tags", tag,
                    "--image-family", "debian-12", "--image-project", "debian-cloud",
                    "--boot-disk-size", "10GB", "--metadata-from-file", f"startup-script={script}",
                    "--labels", f"easydep-run={suffix}", "--format=value(name)", "--quiet",
                ], timeout=900) or name)
        for (group, zone), instance in zip(groups.items(), instances, strict=True):
            recorder.step(f"{group}.create", lambda group=group, zone=zone: _run([
                "gcloud", "compute", "instance-groups", "unmanaged", "create", group,
                "--project", project, "--zone", zone, "--format=value(name)", "--quiet",
            ]) or group)
            recorder.step(f"{group}.add-backend", lambda group=group, zone=zone, instance=instance: _run([
                "gcloud", "compute", "instance-groups", "unmanaged", "add-instances", group,
                "--project", project, "--zone", zone, "--instances", instance, "--quiet",
            ]) or instance)
        recorder.step("load-balancer.health-check", lambda: _run([
            "gcloud", "compute", "health-checks", "create", "http", health,
            "--project", project, "--region", GCP_REGION, "--port", "80",
            "--request-path", "/health/ready", "--check-interval", "10s", "--timeout", "5s",
            "--healthy-threshold", "2", "--unhealthy-threshold", "2", "--quiet",
        ]) or health)
        recorder.step("load-balancer.backend-service", lambda: _run([
            "gcloud", "compute", "backend-services", "create", backend,
            "--project", project, "--region", GCP_REGION,
            "--load-balancing-scheme", "EXTERNAL", "--protocol", "TCP",
            "--health-checks", health, "--health-checks-region", GCP_REGION, "--quiet",
        ]) or backend)
        for group, zone in groups.items():
            recorder.step(f"load-balancer.attach-{group}", lambda group=group, zone=zone: _run([
                "gcloud", "compute", "backend-services", "add-backend", backend,
                "--project", project, "--region", GCP_REGION, "--instance-group", group,
                "--instance-group-zone", zone, "--balancing-mode", "CONNECTION", "--quiet",
            ]) or group)
        recorder.step("load-balancer.address", lambda: _run([
            "gcloud", "compute", "addresses", "create", address, "--project", project,
            "--region", GCP_REGION, "--network-tier", "PREMIUM", "--quiet",
        ]) or address)
        recorder.step("load-balancer.forwarding-rule", lambda: _run([
            "gcloud", "compute", "forwarding-rules", "create", forwarding,
            "--project", project, "--region", GCP_REGION,
            "--load-balancing-scheme", "EXTERNAL", "--network-tier", "PREMIUM",
            "--address", address, "--ip-protocol", "TCP", "--ports", "80",
            "--backend-service", backend, "--backend-service-region", GCP_REGION, "--quiet",
        ]) or forwarding)
        public_ip = _run([
            "gcloud", "compute", "addresses", "describe", address, "--project", project,
            "--region", GCP_REGION, "--format=value(address)",
        ]).strip()
        recorder.step("baseline.two-healthy-targets", lambda: _wait_healthy(
            project, backend, 2, timeout=900
        ))
        base_url = f"http://{public_ip}"
        recorder.step("baseline.readiness", lambda: wait_http(
            "GET", f"{base_url}/health/ready", 200, budget=300
        ))
        expected = set(instances)
        recorder.step("baseline.two-backends", lambda: wait_for_instances(
            base_url, expected, timeout=300
        ))

        def restore(victim: str) -> str:
            zone = instances.get(victim)
            if not zone:
                raise ExperimentFailure(f"unknown GCP backend {victim!r}")
            _run([
                "gcloud", "compute", "instances", "reset", victim,
                "--project", project, "--zone", zone, "--quiet",
            ], timeout=600)
            return f"instance reset restarted the app on {victim}"

        recorder.step("fault.exclude-and-restore", lambda: exercise_fault_exclusion_and_restore(
            base_url, expected, fault_token, restore, restore_timeout=900
        ))
        recorder.document["resourceObservation"] = {
            "frontend": "GCP regional external passthrough Network Load Balancer TCP/80",
            "health": "regional HTTP /health/ready check on tcp/80",
            "backends": "two zonal unmanaged instance groups with one VM each",
        }
        recorder.save()
        outcome = "passed"
    except Exception as exception:  # noqa: BLE001 - cleanup must run for every failure.
        outcome = "failed"
        error = str(exception)
    finally:
        _run(["gcloud", "compute", "forwarding-rules", "delete", forwarding, "--project", project, "--region", GCP_REGION, "--quiet"], check=False)
        _run(["gcloud", "compute", "addresses", "delete", address, "--project", project, "--region", GCP_REGION, "--quiet"], check=False)
        _run(["gcloud", "compute", "backend-services", "delete", backend, "--project", project, "--region", GCP_REGION, "--quiet"], check=False)
        _run(["gcloud", "compute", "health-checks", "delete", health, "--project", project, "--region", GCP_REGION, "--quiet"], check=False)
        for group, zone in groups.items():
            _run(["gcloud", "compute", "instance-groups", "unmanaged", "delete", group, "--project", project, "--zone", zone, "--quiet"], check=False)
        for name, zone in instances.items():
            _run(["gcloud", "compute", "instances", "delete", name, "--project", project, "--zone", zone, "--quiet"], timeout=900, check=False)
        _run(["gcloud", "compute", "firewall-rules", "delete", firewall, "--project", project, "--quiet"], check=False)
        _run(["gcloud", "compute", "networks", "subnets", "delete", subnet, "--project", project, "--region", GCP_REGION, "--quiet"], check=False)
        _run(["gcloud", "compute", "networks", "delete", network, "--project", project, "--quiet"], check=False)
        residual: list[str] = []
        checks = (
            ["gcloud", "compute", "instances", "list", "--project", project, "--filter", f"labels.easydep-run={suffix}", "--format=value(name)"],
            ["gcloud", "compute", "forwarding-rules", "list", "--project", project, "--filter", f"name={forwarding}", "--format=value(name)"],
            ["gcloud", "compute", "backend-services", "list", "--project", project, "--filter", f"name={backend}", "--format=value(name)"],
            ["gcloud", "compute", "networks", "list", "--project", project, "--filter", f"name={network}", "--format=value(name)"],
        )
        for command in checks:
            residual.extend(line for line in _run(command, check=False).splitlines() if line.strip())
        cleanup = {"passed": not residual, "residual": residual}
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
