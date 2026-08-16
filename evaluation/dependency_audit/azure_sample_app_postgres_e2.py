"""도메인 중립 앱으로 Azure VMSS·Load Balancer·자동 복구 E2 경로를 검증한다."""

from __future__ import annotations

import argparse
import json
import secrets
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any

from evaluation.dependency_audit.aws_sample_app_postgres_e1 import _wait_http
from evaluation.dependency_audit.aws_sample_app_postgres_e2 import (
    FUNCTIONAL_RECOVERY_BUDGET_SECONDS,
    _continuity_summary,
    _continuous_business_probe,
)
from evaluation.dependency_audit.inter_vm_postgres_intervention import (
    AZURE_LOCATION,
    AZURE_VM_SIZE,
    INSTALL_DOCKER_DEBIAN,
    POSTGRES_IMAGE,
    POSTGRES_PASSWORD,
    ExperimentFailure,
    Recorder,
    _run,
    _safe_text,
)
from evaluation.dependency_audit.sample_app_postgres_e1_common import (
    APP_IMAGE,
    app_build_script,
)

AUTO_REPAIR_GRACE_MINUTES = 30
MANAGED_RECOVERY_TIMEOUT_SECONDS = 2700


class AzureE2Recorder(Recorder):
    def __init__(self, run_id: str, output: Path) -> None:
        super().__init__("azure", run_id, output)
        self.document |= {
            "schemaVersion": "easydep-azure-sample-app-postgres-e2/v1",
            "transportUnderTest": (
                "public Standard Load Balancer to a VM scale set of app containers, "
                "then private PostgreSQL"
            ),
            "pathUnderTest": (
                "public IP -> Load Balancer probe/rule -> VMSS app VM group (2) -> "
                "state VM private IPv4:5432 -> managed data disk"
            ),
            "scope": "domain-neutral app-tier availability pilot; state tier remains singleton",
        }
        self.save()

    def finish_e2(
        self,
        outcome: str,
        *,
        error: str | None,
        cleanup: dict[str, Any],
    ) -> None:
        self.document |= {
            "outcome": outcome,
            "error": _safe_text(error or "") or None,
            "cleanup": cleanup,
            "finishedAt": datetime.now(UTC).isoformat(),
            "interpretationLimits": [
                "The test validates app-tier managed replacement, not state-tier high availability.",
                "Azure CLI enforces a 30-minute minimum automatic-repair grace period.",
                "Public HTTP is used; trusted HTTPS, DNS ownership, and certificates are not tested.",
                "One regional development run does not establish an Azure-wide success rate or SLA.",
                "Sequential probes are a functional continuity signal, not a performance load test.",
            ],
        }
        self.save()


def _app_cloud_init(state_private: str, fault_token: str) -> str:
    install = INSTALL_DOCKER_DEBIAN.replace("set -eux", "set -eu")
    install += "\nsudo apt-get install -y -qq curl"
    return f"""#!/bin/bash
set -eu
{app_build_script(install)}
sudo docker rm -f easydep-app >/dev/null 2>&1 || true
sudo docker run -d --name easydep-app -p 8080:8080 \
  -e DATABASE_URL='postgresql://postgres:{POSTGRES_PASSWORD}@{state_private}:5432/postgres' \
  -e EASYDEP_TEST_FAULT_TOKEN='{fault_token}' \
  -e EASYDEP_TEST_INSTANCE_ID="$(hostname)" \
  {APP_IMAGE} --role postgres-app
"""


def _vmss_instances(group: str) -> dict[str, dict[str, str]]:
    values = json.loads(_run([
        "az", "vmss", "list-instances", "-g", group, "-n", "app-vmss",
        "--expand", "instanceView",
        "--query", (
            "[].{instanceId:instanceId,vmId:vmId,"
            "computerName:osProfile.computerName,"
            "health:instanceView.vmHealth.status.code}"
        ),
        "-o", "json",
    ]) or "[]")
    return {
        str(value["computerName"]): {
            "instanceId": str(value["instanceId"]),
            "vmId": str(value["vmId"]),
            "health": str(value.get("health") or ""),
        }
        for value in values
    }


def _azure_guest_run(group: str, vm: str, script: str) -> str:
    marker = f"EASYDEP_AZ_GUEST_OK_{uuid.uuid4().hex}"
    command_name = f"easydep-{uuid.uuid4().hex[:8]}"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".sh",
            prefix="easydep-azure-e2-",
            delete=False,
        ) as temporary:
            temporary.write(f"{script}\necho {marker}\n")
            temporary_path = Path(temporary.name)
        _run([
            "az", "vm", "run-command", "create", "-g", group,
            "--vm-name", vm, "--run-command-name", command_name,
            "--location", AZURE_LOCATION, "--async-execution", "false",
            "--timeout-in-seconds", "1200", "--script", f"@{temporary_path}",
            "-o", "none",
        ], timeout=1500)
        value = json.loads(_run([
            "az", "vm", "run-command", "show", "-g", group,
            "--vm-name", vm, "--run-command-name", command_name,
            "--instance-view", "-o", "json",
        ]) or "{}")
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    instance_view = value.get("instanceView") or value
    execution_state = str(instance_view.get("executionState") or "")
    exit_code = instance_view.get("exitCode")
    output = str(instance_view.get("output") or "")
    error = str(instance_view.get("error") or "")
    _run([
        "az", "vm", "run-command", "delete", "-g", group,
        "--vm-name", vm, "--run-command-name", command_name, "--yes",
    ], check=False)
    if execution_state != "Succeeded" or exit_code != 0 or marker not in output:
        raise ExperimentFailure(
            "Azure managed guest command failed: "
            f"state={execution_state}, exitCode={exit_code}, "
            f"output={_safe_text(output)}, error={_safe_text(error)}, "
            f"shape={sorted(value)}"
        )
    return marker


def run(output: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    group = f"easydep-e2-{suffix}"
    recorder = AzureE2Recorder(group, output)
    admin_password = "Aa1!" + secrets.token_urlsafe(18)
    fault_token = uuid.uuid4().hex
    error: str | None = None
    cleanup: dict[str, Any] = {"passed": False, "residual": []}

    try:
        recorder.step("group.create", lambda: _run([
            "az", "group", "create", "--name", group, "--location", AZURE_LOCATION,
            "--tags", f"easydep-run={group}", "-o", "none",
        ]) or group)
        recorder.step("network.create", lambda: _run([
            "az", "network", "vnet", "create", "-g", group, "-n", "vnet",
            "--address-prefix", "10.83.0.0/16", "--subnet-name", "workloads",
            "--subnet-prefix", "10.83.1.0/24", "-o", "none",
        ]) or "10.83.0.0/16")
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
                "az", "network", "nsg", "create", "-g", group, "-n", name,
                "-o", "none",
            ]) or name)
        recorder.step("state-nsg.allow-postgres", lambda: _run([
            "az", "network", "nsg", "rule", "create", "-g", group,
            "--nsg-name", "state-nsg", "-n", "allow-app-postgres",
            "--priority", "100", "--access", "Allow", "--protocol", "Tcp",
            "--source-address-prefixes", "10.83.1.0/24",
            "--destination-port-ranges", "5432", "-o", "none",
        ]) or "state tcp/5432 from the workload subnet")
        recorder.step("app-nsg.allow-http", lambda: _run([
            "az", "network", "nsg", "rule", "create", "-g", group,
            "--nsg-name", "app-nsg", "-n", "allow-public-http",
            "--priority", "100", "--access", "Allow", "--protocol", "Tcp",
            "--source-address-prefixes", "Internet", "--destination-port-ranges", "8080",
            "-o", "none",
        ]) or "public LB traffic to app tcp/8080")
        recorder.step("app-nsg.allow-probe", lambda: _run([
            "az", "network", "nsg", "rule", "create", "-g", group,
            "--nsg-name", "app-nsg", "-n", "allow-azure-lb-probe",
            "--priority", "110", "--access", "Allow", "--protocol", "Tcp",
            "--source-address-prefixes", "AzureLoadBalancer",
            "--destination-port-ranges", "8080", "-o", "none",
        ]) or "Azure Load Balancer probe to app tcp/8080")

        recorder.step("state-vm.create", lambda: _run([
            "az", "vm", "create", "-g", group, "-n", "state",
            "--location", AZURE_LOCATION, "--image", "Ubuntu2204",
            "--size", AZURE_VM_SIZE, "--vnet-name", "vnet", "--subnet", "workloads",
            "--nsg", "state-nsg", "--public-ip-address", "",
            "--admin-username", "easydep", "--authentication-type", "password",
            "--admin-password", admin_password, "--tags", f"easydep-run={group}",
            "-o", "none",
        ], timeout=1200) or "state")
        state_private = _run([
            "az", "vm", "show", "-d", "-g", group, "-n", "state",
            "--query", "privateIps", "-o", "tsv",
        ]).strip()
        recorder.step("state-volume.attach", lambda: _run([
            "az", "vm", "disk", "attach", "-g", group, "--vm-name", "state",
            "--name", "state-data", "--new", "--size-gb", "4",
            "--sku", "Standard_LRS", "--lun", "0", "-o", "none",
        ], timeout=900) or "managed data disk attached as LUN 0")
        state_setup = f"""{INSTALL_DOCKER_DEBIAN}
for i in $(seq 1 60); do
  device=/dev/disk/azure/scsi1/lun0
  [ -b "$device" ] && break
  sleep 2
done
test -b "$device"
if ! sudo blkid "$device" >/dev/null 2>&1; then sudo mkfs.ext4 -F "$device"; fi
uuid=$(sudo blkid -s UUID -o value "$device")
sudo mkdir -p /var/lib/easydep-postgres
grep -q "$uuid" /etc/fstab || echo "UUID=$uuid /var/lib/easydep-postgres ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab >/dev/null
sudo mount -a
sudo mkdir -p /var/lib/easydep-postgres/data
sudo chown 999:999 /var/lib/easydep-postgres/data
sudo docker rm -f easydep-state >/dev/null 2>&1 || true
sudo docker run -d --name easydep-state --restart unless-stopped \
  -e POSTGRES_PASSWORD='{POSTGRES_PASSWORD}' -p 5432:5432 \
  -v /var/lib/easydep-postgres/data:/var/lib/postgresql/data {POSTGRES_IMAGE}
ready=0
for i in $(seq 1 90); do
  if sudo docker exec easydep-state pg_isready -U postgres; then ready=1; break; fi
  sleep 2
done
test "$ready" = 1
"""
        recorder.step("state-volume.mount-and-postgres", lambda: _azure_guest_run(
            group, "state", state_setup
        ))

        recorder.step("load-balancer.public-ip", lambda: _run([
            "az", "network", "public-ip", "create", "-g", group, "-n", "app-pip",
            "--sku", "Standard", "--allocation-method", "Static", "-o", "none",
        ]) or "app-pip")
        recorder.step("load-balancer.create", lambda: _run([
            "az", "network", "lb", "create", "-g", group, "-n", "app-lb",
            "--sku", "Standard", "--public-ip-address", "app-pip",
            "--frontend-ip-name", "app-frontend", "--backend-pool-name", "app-pool",
            "-o", "none",
        ]) or "app-lb")
        recorder.step("load-balancer.health-probe", lambda: _run([
            "az", "network", "lb", "probe", "create", "-g", group,
            "--lb-name", "app-lb", "-n", "app-probe", "--protocol", "Http",
            "--port", "8080", "--path", "/health/ready", "--interval", "10",
            "--threshold", "2", "-o", "none",
        ]) or "HTTP /health/ready every 10 seconds, threshold 2")
        recorder.step("load-balancer.rule", lambda: _run([
            "az", "network", "lb", "rule", "create", "-g", group,
            "--lb-name", "app-lb", "-n", "app-http", "--protocol", "Tcp",
            "--frontend-ip-name", "app-frontend", "--frontend-port", "80",
            "--backend-pool-name", "app-pool", "--backend-port", "8080",
            "--probe-name", "app-probe", "--disable-outbound-snat", "true",
            "-o", "none",
        ]) or "public tcp/80 to app tcp/8080")
        with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
            app_script = Path(temporary) / "app.sh"
            app_script.write_text(
                _app_cloud_init(state_private, fault_token), encoding="utf-8"
            )
            recorder.step("app-group.create", lambda: _run([
                "az", "vmss", "create", "-g", group, "-n", "app-vmss",
                "--location", AZURE_LOCATION, "--image", "Ubuntu2204",
                "--vm-sku", AZURE_VM_SIZE, "--instance-count", "2",
                "--orchestration-mode", "Uniform", "--upgrade-policy-mode", "Manual",
                "--vnet-name", "vnet", "--subnet", "workloads", "--nsg", "app-nsg",
                "--load-balancer", "app-lb", "--backend-pool-name", "app-pool",
                "--health-probe", "app-probe", "--enable-automatic-repairs", "true",
                "--automatic-repairs-grace-period", str(AUTO_REPAIR_GRACE_MINUTES),
                "--automatic-repairs-action", "Replace", "--custom-data", str(app_script),
                "--admin-username", "easydep", "--authentication-type", "password",
                "--admin-password", admin_password, "--tags", f"easydep-run={group}",
                "-o", "none",
            ], timeout=1800) or "VMSS size 2 with LB health and automatic Replace")

        public_ip = _run([
            "az", "network", "public-ip", "show", "-g", group, "-n", "app-pip",
            "--query", "ipAddress", "-o", "tsv",
        ]).strip()
        base_url = f"http://{public_ip}"
        recorder.step("baseline.readiness", lambda: _wait_http(
            "GET", f"{base_url}/health/ready", HTTPStatus.OK, budget=900
        ))
        recorder.step("baseline.business-write", lambda: _wait_http(
            "PUT", f"{base_url}/records/evidence", HTTPStatus.OK,
            payload={"value": {"message": "kept"}}, budget=300,
        ))
        recorder.step("baseline.business-read", lambda: _wait_http(
            "GET", f"{base_url}/records/evidence", HTTPStatus.OK, budget=120
        ))
        initial_instances = _vmss_instances(group)
        if len(initial_instances) != 2:
            raise ExperimentFailure(f"Azure VMSS did not expose two instances: {initial_instances}")

        def health_fault_and_recovery() -> dict[str, Any]:
            started = time.monotonic()
            observations: list[dict[str, Any]] = []
            milestones: dict[str, float] = {}
            stop = threading.Event()
            thread = threading.Thread(
                target=_continuous_business_probe,
                args=(base_url, stop, observations, started),
                daemon=True,
            )
            thread.start()
            try:
                fault_response = _wait_http(
                    "POST", f"{base_url}/__easydep_test/fault", HTTPStatus.ACCEPTED,
                    payload={"token": fault_token}, budget=30,
                )
                victim = str(fault_response.get("body", {}).get("instance") or "")
                if victim not in initial_instances:
                    raise ExperimentFailure(
                        f"Azure fault response did not identify a VMSS instance: {fault_response}"
                    )
                milestones["faultAccepted"] = round(time.monotonic() - started, 3)
                deadline = time.monotonic() + MANAGED_RECOVERY_TIMEOUT_SECONDS
                recovered: dict[str, str] | None = None
                while time.monotonic() < deadline:
                    current = _vmss_instances(group)
                    if (
                        "victimUnhealthy" not in milestones
                        and current.get(victim, {}).get("health") == "HealthState/unhealthy"
                    ):
                        milestones["victimUnhealthy"] = round(
                            time.monotonic() - started, 3
                        )
                    changed = next((
                        (name, observation) for name, observation in current.items()
                        if name not in initial_instances
                        or observation["vmId"] != initial_instances[name]["vmId"]
                    ), None)
                    if changed:
                        recovered_name, recovered_observation = changed
                        recovered = {"instance": recovered_name, **recovered_observation}
                        milestones["managedReplacementObserved"] = round(
                            time.monotonic() - started, 3
                        )
                        _wait_http(
                            "GET", f"{base_url}/health/ready", HTTPStatus.OK, budget=600
                        )
                        milestones["managedReplacementHealthy"] = round(
                            time.monotonic() - started, 3
                        )
                        break
                    time.sleep(30)
                if not recovered:
                    raise ExperimentFailure("Azure VMSS automatic replacement was not observed")
            finally:
                stop.set()
                thread.join(timeout=20)
            continuity = _continuity_summary(observations)
            successes_during = sum(
                1 for item in observations
                if item.get("status") == HTTPStatus.OK and item.get("valueKept")
            )
            if not successes_during:
                raise ExperimentFailure(f"no successful request during Azure recovery: {continuity}")
            if continuity["maxConsecutiveFailureSeconds"] > FUNCTIONAL_RECOVERY_BUDGET_SECONDS:
                raise ExperimentFailure(
                    "Azure functional routing recovery exceeded the health-derived budget: "
                    f"budget={FUNCTIONAL_RECOVERY_BUDGET_SECONDS}, observed={continuity}"
                )
            return {
                "victimInstance": victim,
                "victimVmId": initial_instances[victim]["vmId"],
                "managedRecovery": recovered,
                "faultRequest": fault_response,
                "milestonesSeconds": milestones,
                "automaticRepairGraceMinutes": AUTO_REPAIR_GRACE_MINUTES,
                "healthDerivedFunctionalRecoveryBudgetSeconds": (
                    FUNCTIONAL_RECOVERY_BUDGET_SECONDS
                ),
                "businessContinuity": continuity,
                "successfulRequestsDuringManagedRecovery": successes_during,
            }

        recorder.step("fault.health-based-managed-recovery", health_fault_and_recovery)
        recorder.step("recovery.business-read", lambda: _wait_http(
            "GET", f"{base_url}/records/evidence", HTTPStatus.OK, budget=120
        ))
        recorder.document["availabilityObservation"] = {
            "scope": "application tier",
            "stateTier": "single state VM; not highly available",
            "failureInjection": "stop one app process and let LB health feed VMSS auto repairs",
            "groupScope": "single region; region failure is not tested",
            "automaticRepairAction": "Replace",
            "automaticRepairGraceMinutes": AUTO_REPAIR_GRACE_MINUTES,
        }
        recorder.save()
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
                "az", "group", "exists", "--name", group
            ], check=False).lower() == "true"
            if not exists:
                break
            time.sleep(10)
        cleanup = {"passed": not exists, "residual": [group] if exists else []}
        recorder.finish_e2(
            outcome if cleanup["passed"] else "failed",
            error=error,
            cleanup=cleanup,
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
