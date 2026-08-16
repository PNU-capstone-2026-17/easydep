"""Provider-neutral oracle helpers for the state-VM replacement experiment."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.dependency_audit.inter_vm_postgres_intervention import (
    POSTGRES_IMAGE,
    POSTGRES_PASSWORD,
    Recorder,
    _safe_text,
)
from evaluation.dependency_audit.sample_app_postgres_e1_common import (
    APP_IMAGE,
    restored_script,
)


class E3Recorder(Recorder):
    """Persist comparable E3 evidence while a provider-specific runner executes."""

    def __init__(self, provider: str, run_id: str, output: Path) -> None:
        super().__init__(provider, run_id, output)
        self.document |= {
            "schemaVersion": "easydep-sample-app-postgres-e3/v1",
            "scope": "domain-neutral state-VM replacement and runtime-endpoint rebinding",
            "transportUnderTest": (
                "app VM/container to replacement state VM private IPv4, TCP 5432"
            ),
            "pathUnderTest": (
                "unchanged app VM/image -> replacement state VM private endpoint -> "
                "reattached provider data disk -> PostgreSQL data path"
            ),
        }
        self.save()

    def finish_e3(
        self,
        outcome: str,
        *,
        error: str | None,
        cleanup: dict[str, Any],
    ) -> None:
        passed_steps = {
            item["name"]
            for item in self.document["steps"]
            if item.get("status") == "passed"
        }
        self.document |= {
            "outcome": outcome,
            "error": _safe_text(error or "") or None,
            "cleanup": cleanup,
            "finishedAt": datetime.now(UTC).isoformat(),
            "observations": {
                "baselineWriteReadPassed": "baseline.app-write-read" in passed_steps,
                "stateVmWasReplaced": "replacement.state-vm.create" in passed_steps,
                "sameDataDiskWasReattached": (
                    "replacement.state-volume.attach-existing" in passed_steps
                ),
                "appImageWasNotRebuilt": (
                    "replacement.app-rebind-without-rebuild" in passed_steps
                ),
                "existingValueReadAfterReplacement": (
                    "replacement.app-read-existing-value" in passed_steps
                ),
            },
            "interpretationLimits": [
                "This is a recoverability test, not automatic failover or high availability.",
                "The application container is restarted to inject the replacement endpoint.",
                "The state workload is unavailable during replacement.",
                "One development run per provider does not establish a success rate.",
                "The generic key-value oracle does not validate course-registration rules.",
            ],
        }
        self.save()


def state_setup_script(install_docker: str, device_wait_script: str) -> str:
    """Mount an existing-or-empty device without reformatting existing data."""
    return f"""set -eu
{install_docker}
{device_wait_script}
test -b "$device"
if ! sudo blkid "$device" >/dev/null 2>&1; then sudo mkfs.ext4 -F "$device"; fi
sudo mkdir -p /var/lib/easydep-postgres
sudo mount "$device" /var/lib/easydep-postgres
sudo mkdir -p /var/lib/easydep-postgres/data
sudo chown 999:999 /var/lib/easydep-postgres/data
mountpoint -q /var/lib/easydep-postgres
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


def app_start_script(state_private_ip: str) -> str:
    database_url = (
        f"postgresql://postgres:{POSTGRES_PASSWORD}@{state_private_ip}:5432/postgres"
    )
    return f"""set -eu
sudo docker rm -f easydep-app >/dev/null 2>&1 || true
sudo docker run -d --name easydep-app --restart unless-stopped -p 8080:8080 \
  -e DATABASE_URL='{database_url}' {APP_IMAGE} --role postgres-app
"""


def app_rebind_script(state_private_ip: str) -> str:
    """Restart from the same local image and verify the pre-replacement value."""
    start = app_start_script(state_private_ip)
    restored = restored_script()
    return f"""set -eu
before=$(sudo docker image inspect --format '{{{{.Id}}}}' {APP_IMAGE})
{start}
rebind_passed=0
for i in $(seq 1 60); do
  if (
{restored}
  ); then
    after=$(sudo docker image inspect --format '{{{{.Id}}}}' {APP_IMAGE})
    test "$before" = "$after"
    rebind_passed=1
    break
  fi
  sleep 5
done
test "$rebind_passed" = 1
"""


def app_rebind_from_variable_script(variable_name: str) -> str:
    """Rebind using a trusted shell variable whose value is only known at runtime."""
    if not variable_name.isidentifier():
        raise ValueError("variable_name must be a shell-compatible identifier")
    restored = restored_script()
    return f"""set -eu
before=$(sudo docker image inspect --format '{{{{.Id}}}}' {APP_IMAGE})
database_url="postgresql://postgres:{POSTGRES_PASSWORD}@${{{variable_name}}}:5432/postgres"
sudo docker rm -f easydep-app >/dev/null 2>&1 || true
sudo docker run -d --name easydep-app --restart unless-stopped -p 8080:8080 \
  -e DATABASE_URL="$database_url" {APP_IMAGE} --role postgres-app
rebind_passed=0
for i in $(seq 1 60); do
  if (
{restored}
  ); then
    after=$(sudo docker image inspect --format '{{{{.Id}}}}' {APP_IMAGE})
    test "$before" = "$after"
    rebind_passed=1
    break
  fi
  sleep 5
done
test "$rebind_passed" = 1
"""
