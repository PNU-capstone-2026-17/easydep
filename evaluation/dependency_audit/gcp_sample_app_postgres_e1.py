"""도메인 중립 테스트 앱으로 GCP E1 App→PostgreSQL→Disk 경로를 검증한다."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.dependency_audit.inter_vm_postgres_intervention import (
    GCP_REGION,
    GCP_ZONE,
    INSTALL_DOCKER_DEBIAN,
    POSTGRES_IMAGE,
    POSTGRES_PASSWORD,
    Recorder,
    _gcp_wait_for_marker,
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


class GcpE1Recorder(Recorder):
    def __init__(self, run_id: str, output: Path) -> None:
        super().__init__("gcp", run_id, output)
        self.document |= {
            "schemaVersion": "easydep-gcp-sample-app-postgres-e1/v1",
            "transportUnderTest": "app VM/container to state VM private IPv4, TCP 5432",
            "pathUnderTest": (
                "app VM/container local HTTP -> state VM private IPv4:5432 -> "
                "PostgreSQL data path -> GCP persistent data disk"
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
                "A same-instance reset is not state-VM replacement or state-tier high availability.",
                "One development run does not establish a GCP-wide success rate.",
            ],
        }
        self.save()


def _metadata_url() -> str:
    return (
        "http://metadata.google.internal/computeMetadata/v1/instance/attributes/"
        "easydep-phase"
    )


def _app_controller(state_private: str) -> str:
    install = INSTALL_DOCKER_DEBIAN.replace("set -eux", "set -eu")
    install += "\nsudo apt-get install -y -qq curl"
    build = app_build_script(install)
    database_url = (
        f"postgresql://postgres:{POSTGRES_PASSWORD}@{state_private}:5432/postgres"
    )
    metadata = _metadata_url()
    baseline = baseline_script()
    blocked = blocked_script()
    restored = restored_script()
    return f"""#!/bin/bash
set -eu
{build}
sudo docker rm -f easydep-app >/dev/null 2>&1 || true
sudo docker run -d --name easydep-app --restart unless-stopped -p 8080:8080 \
  -e DATABASE_URL='{database_url}' {APP_IMAGE} --role postgres-app
for i in $(seq 1 60); do
  if (
{baseline}
  ); then
    echo 'EASYDEP_E1 baseline passed'
    baseline_passed=1
    break
  fi
  sleep 5
done
test "${{baseline_passed:-0}}" = 1
while [ "$(curl -fsS -H 'Metadata-Flavor: Google' '{metadata}' || true)" != block ]; do sleep 2; done
(
{blocked}
)
echo 'EASYDEP_E1 blocked passed'
while [ "$(curl -fsS -H 'Metadata-Flavor: Google' '{metadata}' || true)" != restore ]; do sleep 2; done
(
{restored}
)
echo 'EASYDEP_E1 restored passed'
while [ "$(curl -fsS -H 'Metadata-Flavor: Google' '{metadata}' || true)" != reboot ]; do sleep 2; done
seen_down=0
for i in $(seq 1 180); do
  status=$(curl -sS -o /tmp/read.json -w '%{{http_code}}' http://127.0.0.1:8080/records/evidence || true)
  if [ "$status" = 502 ] || [ "$status" = 503 ] || [ "$status" = 000 ]; then seen_down=1; fi
  if [ "$seen_down" = 1 ] && [ "$status" = 200 ] && grep -q '"message": "kept"' /tmp/read.json; then
    echo 'EASYDEP_E1 reboot-persistence passed'
    exit 0
  fi
  sleep 2
done
echo 'EASYDEP_E1 reboot-persistence failed'
exit 1
"""


def _state_controller() -> str:
    install = INSTALL_DOCKER_DEBIAN.replace("set -eux", "set -eu")
    return f"""#!/bin/bash
set -eu
{install}
device=/dev/disk/by-id/google-state-data
for i in $(seq 1 60); do [ -b "$device" ] && break; sleep 2; done
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
  if sudo docker exec easydep-state pg_isready -U postgres; then
    echo 'EASYDEP_E1 state-ready passed'
    exit 0
  fi
  sleep 2
done
exit 1
"""


def run(output: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    prefix = f"easydep-e1-{suffix}"
    network = f"{prefix}-net"
    subnet = f"{prefix}-subnet"
    app = f"{prefix}-app"
    state = f"{prefix}-state"
    disk = f"{prefix}-data"
    allow_pg = f"{prefix}-allow-pg"
    project = _run(["gcloud", "config", "get-value", "project"]).strip()
    recorder = GcpE1Recorder(prefix, output)
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
            "--range", "10.80.1.0/24", "--format=value(name)", "--quiet",
        ]) or "10.80.1.0/24")

        def allow_postgres() -> str:
            _run([
                "gcloud", "compute", "firewall-rules", "create", allow_pg,
                "--project", project, "--network", network, "--direction", "INGRESS",
                "--action", "ALLOW", "--rules", "tcp:5432", "--source-tags", "easydep-app",
                "--target-tags", "easydep-state", "--quiet",
            ])
            return "state tcp/5432 from app source tag"

        recorder.step("firewall.allow-postgres", allow_postgres)
        recorder.step("state-disk.create", lambda: _run([
            "gcloud", "compute", "disks", "create", disk, "--project", project,
            "--zone", GCP_ZONE, "--size", "10GB", "--type", "pd-balanced",
            "--labels", f"easydep-run={suffix}", "--format=value(name)", "--quiet",
        ]) or disk)
        with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
            state_script = Path(temporary) / "state.sh"
            state_script.write_text(_state_controller(), encoding="utf-8")
            recorder.step("state-vm.create", lambda: _run([
                "gcloud", "compute", "instances", "create", state, "--project", project,
                "--zone", GCP_ZONE, "--machine-type", "e2-small", "--network", network,
                "--subnet", subnet, "--tags", "easydep-state", "--image-family", "debian-12",
                "--image-project", "debian-cloud", "--boot-disk-size", "10GB",
                "--disk", f"name={disk},device-name=state-data,mode=rw,boot=no,auto-delete=no",
                "--metadata-from-file", f"startup-script={state_script}",
                "--labels", f"easydep-run={suffix}", "--format=value(name)", "--quiet",
            ], timeout=900) or state)
            state_private = _run([
                "gcloud", "compute", "instances", "describe", state,
                "--project", project, "--zone", GCP_ZONE,
                "--format", "value(networkInterfaces[0].networkIP)",
            ]).strip()
            app_script = Path(temporary) / "app.sh"
            app_script.write_text(_app_controller(state_private), encoding="utf-8")
            recorder.step("app-vm.create", lambda: _run([
                "gcloud", "compute", "instances", "create", app, "--project", project,
                "--zone", GCP_ZONE, "--machine-type", "e2-small", "--network", network,
                "--subnet", subnet, "--tags", "easydep-app", "--image-family", "debian-12",
                "--image-project", "debian-cloud", "--boot-disk-size", "10GB",
                "--metadata", "easydep-phase=baseline",
                "--metadata-from-file", f"startup-script={app_script}",
                "--labels", f"easydep-run={suffix}", "--format=value(name)", "--quiet",
            ], timeout=900) or app)
        recorder.document["networkObservation"] = {
            "applicationOracle": "app VM localhost:8080 through serial markers",
            "databaseEndpointUsedByApp": f"{state_private}:5432",
            "databaseIngressSource": "easydep-app source tag",
            "hostToVmManagementChannel": "none; guest serial markers and metadata phases",
        }
        recorder.save()
        recorder.step("baseline.app-write-read", lambda: _gcp_wait_for_marker(
            project, app, "EASYDEP_E1 baseline passed", timeout=900
        ))
        recorder.step("intervention.delete-postgres-allow", lambda: _run([
            "gcloud", "compute", "firewall-rules", "delete", allow_pg,
            "--project", project, "--quiet",
        ]) or "app-to-state tcp/5432 allow removed")
        recorder.step("intervention.signal", lambda: _run([
            "gcloud", "compute", "instances", "add-metadata", app,
            "--project", project, "--zone", GCP_ZONE,
            "--metadata", "easydep-phase=block", "--quiet",
        ]) or "app phase=block")
        recorder.step("intervention.app-observes-state-loss", lambda: _gcp_wait_for_marker(
            project, app, "EASYDEP_E1 blocked passed", timeout=240
        ))
        recorder.step("restore.allow-postgres", allow_postgres)
        recorder.step("restore.signal", lambda: _run([
            "gcloud", "compute", "instances", "add-metadata", app,
            "--project", project, "--zone", GCP_ZONE,
            "--metadata", "easydep-phase=restore", "--quiet",
        ]) or "app phase=restore")
        recorder.step("restore.app-reads-existing-value", lambda: _gcp_wait_for_marker(
            project, app, "EASYDEP_E1 restored passed", timeout=600
        ))
        recorder.step("persistence.signal-observer", lambda: _run([
            "gcloud", "compute", "instances", "add-metadata", app,
            "--project", project, "--zone", GCP_ZONE,
            "--metadata", "easydep-phase=reboot", "--quiet",
        ]) or "app phase=reboot")
        time.sleep(3)
        recorder.step("persistence.state-vm-reset", lambda: _run([
            "gcloud", "compute", "instances", "reset", state,
            "--project", project, "--zone", GCP_ZONE, "--quiet",
        ], timeout=600) or "state VM reset")
        recorder.step("persistence.app-observes-loss-and-existing-value", lambda: _gcp_wait_for_marker(
            project, app, "EASYDEP_E1 reboot-persistence passed", timeout=600
        ))
        outcome = "passed"
    except Exception as exception:
        outcome = "failed"
        error = str(exception)
    finally:
        _run([
            "gcloud", "compute", "instances", "delete", app, state, "--project", project,
            "--zone", GCP_ZONE, "--quiet",
        ], timeout=900, check=False)
        _run([
            "gcloud", "compute", "disks", "delete", disk, "--project", project,
            "--zone", GCP_ZONE, "--quiet",
        ], timeout=600, check=False)
        _run([
            "gcloud", "compute", "firewall-rules", "delete", allow_pg,
            "--project", project, "--quiet",
        ], check=False)
        _run([
            "gcloud", "compute", "networks", "subnets", "delete", subnet,
            "--project", project, "--region", GCP_REGION, "--quiet",
        ], check=False)
        _run([
            "gcloud", "compute", "networks", "delete", network,
            "--project", project, "--quiet",
        ], check=False)
        residual_queries = (
            ["gcloud", "compute", "instances", "list", "--project", project,
             "--filter", f"labels.easydep-run={suffix}", "--format", "value(name)"],
            ["gcloud", "compute", "disks", "list", "--project", project,
             "--filter", f"labels.easydep-run={suffix}", "--format", "value(name)"],
            ["gcloud", "compute", "firewall-rules", "list", "--project", project,
             "--filter", f"name~'^{prefix}'", "--format", "value(name)"],
            ["gcloud", "compute", "networks", "list", "--project", project,
             "--filter", f"name={network}", "--format", "value(name)"],
            ["gcloud", "compute", "networks", "subnets", "list", "--project", project,
             "--filter", f"name={subnet}", "--format", "value(name)"],
        )
        residual = [
            item
            for command in residual_queries
            for item in _run(command, check=False).splitlines()
            if item.strip()
        ]
        cleanup = {"passed": not residual, "residual": residual}
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
