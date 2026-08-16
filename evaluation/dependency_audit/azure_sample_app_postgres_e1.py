"""도메인 중립 테스트 앱으로 Azure E1 App→PostgreSQL→Disk 경로를 검증한다."""

from __future__ import annotations

import argparse
import json
import secrets
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.dependency_audit.inter_vm_postgres_intervention import (
    AZURE_LOCATION,
    AZURE_VM_SIZE,
    INSTALL_DOCKER_DEBIAN,
    POSTGRES_IMAGE,
    POSTGRES_PASSWORD,
    Recorder,
    _az_run,
    _run,
    _safe_text,
)
from evaluation.dependency_audit.sample_app_postgres_e1_common import (
    APP_IMAGE,
    app_build_script,
    baseline_script,
    blocked_script,
    restored_script,
)


class AzureE1Recorder(Recorder):
    def __init__(self, run_id: str, output: Path) -> None:
        super().__init__("azure", run_id, output)
        self.document |= {
            "schemaVersion": "easydep-azure-sample-app-postgres-e1/v1",
            "transportUnderTest": "app VM/container to state VM private IPv4, TCP 5432",
            "pathUnderTest": (
                "app VM/container local HTTP -> state VM private IPv4:5432 -> "
                "PostgreSQL data path -> Azure managed data disk"
            ),
            "scope": "domain-neutral dependency pilot; not course-registration behavior",
        }
        self.save()

    def finish_e1(
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
                "The test app validates resource bindings, not course-registration rules.",
                "The application endpoint is checked locally; public ingress and HTTPS are not tested.",
                "A same-instance reboot is not state-VM replacement or state-tier high availability.",
                "One development run does not establish an Azure-wide success rate.",
            ],
        }
        self.save()


def run(output: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    group = f"easydep-e1-{suffix}"
    recorder = AzureE1Recorder(group, output)
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
            "--address-prefix", "10.79.0.0/16", "--subnet-name", "workloads",
            "--subnet-prefix", "10.79.1.0/24", "-o", "none",
        ]) or "10.79.0.0/16")
        for name in ("app-nsg", "state-nsg"):
            recorder.step(f"{name}.create", lambda name=name: _run([
                "az", "network", "nsg", "create", "-g", group, "-n", name, "-o", "none",
            ]) or name)
        recorder.step("state-nsg.deny-vnet-postgres", lambda: _run([
            "az", "network", "nsg", "rule", "create", "-g", group,
            "--nsg-name", "state-nsg", "-n", "deny-vnet-postgres",
            "--priority", "110", "--access", "Deny", "--protocol", "Tcp",
            "--source-address-prefixes", "VirtualNetwork", "--destination-port-ranges", "5432",
            "-o", "none",
        ]) or "explicit deny keeps the blocked condition observable")

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

        recorder.step("state-vm.create", lambda: create_vm("state", "state-nsg"))
        recorder.step("app-vm.create", lambda: create_vm("app", "app-nsg"))
        state_private = _run([
            "az", "vm", "show", "-d", "-g", group, "-n", "state",
            "--query", "privateIps", "-o", "tsv",
        ]).strip()
        app_private = _run([
            "az", "vm", "show", "-d", "-g", group, "-n", "app",
            "--query", "privateIps", "-o", "tsv",
        ]).strip()
        recorder.document["networkObservation"] = {
            "applicationOracle": "app VM localhost:8080 through Azure Run Command",
            "databaseEndpointUsedByApp": f"{state_private}:5432",
            "databaseIngressSource": f"{app_private}/32",
            "publicAddresses": False,
        }
        recorder.save()

        def allow_postgres() -> str:
            _run([
                "az", "network", "nsg", "rule", "create", "-g", group,
                "--nsg-name", "state-nsg", "-n", "allow-app-postgres",
                "--priority", "100", "--access", "Allow", "--protocol", "Tcp",
                "--source-address-prefixes", f"{app_private}/32",
                "--destination-port-ranges", "5432", "-o", "none",
            ])
            return "state tcp/5432 from app private IPv4"

        recorder.step("state-nsg.allow-postgres", allow_postgres)
        recorder.step("state-volume.attach", lambda: _run([
            "az", "vm", "disk", "attach", "-g", group, "--vm-name", "state",
            "--name", "state-data", "--new", "--size-gb", "4", "--sku", "Standard_LRS",
            "--lun", "0", "-o", "none",
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
mountpoint -q /var/lib/easydep-postgres
sudo docker rm -f easydep-state >/dev/null 2>&1 || true
sudo docker run -d --name easydep-state --restart unless-stopped \
  -e POSTGRES_PASSWORD='{POSTGRES_PASSWORD}' -p 5432:5432 \
  -v /var/lib/easydep-postgres/data:/var/lib/postgresql/data {POSTGRES_IMAGE}
for i in $(seq 1 90); do
  sudo docker exec easydep-state pg_isready -U postgres && exit 0
  sleep 2
done
exit 1
"""
        recorder.step("state-volume.mount-and-postgres", lambda: _az_run(
            group, "state", state_setup
        ))
        app_install = INSTALL_DOCKER_DEBIAN + "\nsudo apt-get install -y -qq curl"
        recorder.step("app-image.build", lambda: _az_run(
            group, "app", app_build_script(app_install)
        ))
        database_url = (
            f"postgresql://postgres:{POSTGRES_PASSWORD}@{state_private}:5432/postgres"
        )
        app_start = f"""
set -eu
sudo docker rm -f easydep-app >/dev/null 2>&1 || true
sudo docker run -d --name easydep-app --restart unless-stopped -p 8080:8080 \
  -e DATABASE_URL='{database_url}' {APP_IMAGE} --role postgres-app
"""
        recorder.step("app-container.start", lambda: _az_run(group, "app", app_start))
        recorder.step("baseline.app-write-read", lambda: _az_run(
            group, "app", baseline_script()
        ))
        recorder.step("intervention.delete-postgres-allow", lambda: _run([
            "az", "network", "nsg", "rule", "delete", "-g", group,
            "--nsg-name", "state-nsg", "-n", "allow-app-postgres", "-o", "none",
        ]) or "explicit app-to-state allow removed")
        recorder.step("intervention.app-observes-state-loss", lambda: _az_run(
            group, "app", blocked_script()
        ))
        recorder.step("restore.allow-postgres", allow_postgres)
        recorder.step("restore.app-reads-existing-value", lambda: _az_run(
            group, "app", restored_script()
        ))
        recorder.step("persistence.state-vm-restart", lambda: _run([
            "az", "vm", "restart", "-g", group, "-n", "state", "-o", "none",
        ], timeout=900) or "state VM restarted")
        recorder.step("persistence.app-reads-existing-value", lambda: _az_run(
            group, "app", restored_script()
        ))
        outcome = "passed"
    except Exception as exception:
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
        recorder.finish_e1(
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
