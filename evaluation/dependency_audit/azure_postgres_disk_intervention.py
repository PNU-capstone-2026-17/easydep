"""Azure VM에서 PostgreSQL의 추가 data disk 필요 조건을 검증한다."""
# ruff: noqa: S608 -- 고정 SQL fixture를 guest VM에서 실행하며 외부 입력을 결합하지 않는다.

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

IMAGE = "Ubuntu2204"
# 이 학생 구독의 Korea Central에서 B1s는 capacity restriction으로 거부됐다.
# 기존 Azure 기능 실험에서도 통과한 가장 작은 확인 SKU를 사용한다.
VM_SIZE = "Standard_B2ats_v2"
POSTGRES_IMAGE = "postgres:17-bookworm"
ADMIN_USER = "easydep"
DATA_DISK = "pgdata"
AZ = shutil.which("az") or shutil.which("az.cmd") or "az.cmd"

INSTALL_DOCKER = """
set -eux
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq docker.io
sudo systemctl enable --now docker
sudo docker pull postgres:17-bookworm
""".strip()

ROOT_DISK_PROBE = INSTALL_DOCKER + """
sudo docker run -d --name pg -e POSTGRES_PASSWORD=easydep-test-only postgres:17-bookworm
for i in $(seq 1 60); do sudo docker exec pg pg_isready -U postgres && break; sleep 2; done
sudo docker exec pg psql -U postgres -v ON_ERROR_STOP=1 -c "CREATE TABLE evidence(k text PRIMARY KEY, v text); INSERT INTO evidence VALUES ('probe','boot-disk');"
test "$(sudo docker exec pg psql -U postgres -tAc "SELECT v FROM evidence WHERE k='probe';")" = "boot-disk"
sudo docker restart pg
for i in $(seq 1 60); do sudo docker exec pg pg_isready -U postgres && break; sleep 2; done
test "$(sudo docker exec pg psql -U postgres -tAc "SELECT v FROM evidence WHERE k='probe';")" = "boot-disk"
"""

DATA_DISK_WRITE = INSTALL_DOCKER + """
DEVICE=/dev/disk/azure/scsi1/lun0
for i in $(seq 1 60); do test -e "$DEVICE" && break; sleep 2; done
test -e "$DEVICE"
sudo mkfs.ext4 -F "$DEVICE"
sudo mkdir -p /mnt/pgdata
sudo mount "$DEVICE" /mnt/pgdata
sudo docker run -d --name pg -e POSTGRES_PASSWORD=easydep-test-only -v /mnt/pgdata:/var/lib/postgresql/data postgres:17-bookworm
for i in $(seq 1 60); do sudo docker exec pg pg_isready -U postgres && break; sleep 2; done
sudo docker exec pg psql -U postgres -v ON_ERROR_STOP=1 -c "CREATE TABLE evidence(k text PRIMARY KEY, v text); INSERT INTO evidence VALUES ('probe','data-disk');"
test "$(sudo docker exec pg psql -U postgres -tAc "SELECT v FROM evidence WHERE k='probe';")" = "data-disk"
"""

DATA_DISK_READ = INSTALL_DOCKER + """
DEVICE=/dev/disk/azure/scsi1/lun0
for i in $(seq 1 60); do test -e "$DEVICE" && break; sleep 2; done
test -e "$DEVICE"
sudo mkdir -p /mnt/pgdata
sudo mount "$DEVICE" /mnt/pgdata
sudo docker run -d --name pg -e POSTGRES_PASSWORD=easydep-test-only -v /mnt/pgdata:/var/lib/postgresql/data postgres:17-bookworm
for i in $(seq 1 60); do sudo docker exec pg pg_isready -U postgres && break; sleep 2; done
test "$(sudo docker exec pg psql -U postgres -tAc "SELECT v FROM evidence WHERE k='probe';")" = "data-disk"
"""


class ExperimentFailure(RuntimeError):
    """기대한 Azure 또는 PostgreSQL 결과가 나오지 않았음을 나타낸다."""


def _az(*args: str, check: bool = True) -> Any:
    command = [AZ, *args, "-o", "json"]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and result.returncode:
        raise ExperimentFailure(result.stderr.strip() or result.stdout.strip())
    if not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout.strip()


def _record(steps: list[dict[str, Any]], name: str, action) -> Any:
    started = time.monotonic()
    try:
        value = action()
    except Exception as exc:
        steps.append({
            "name": name,
            "status": "failed",
            "durationSeconds": round(time.monotonic() - started, 3),
            "detail": str(exc),
        })
        raise
    steps.append({
        "name": name,
        "status": "passed",
        "durationSeconds": round(time.monotonic() - started, 3),
        "detail": value,
    })
    return value


def _create_vm(group: str, name: str, location: str, admin_password: str) -> None:
    _az(
        "vm", "create",
        "--resource-group", group,
        "--name", name,
        "--location", location,
        "--image", IMAGE,
        "--size", VM_SIZE,
        "--admin-username", ADMIN_USER,
        "--authentication-type", "password",
        "--admin-password", admin_password,
        "--public-ip-address", "",
        "--tags", f"easydep-run={group}",
    )


def _run_command(group: str, vm: str, script: str) -> str:
    result = _az(
        "vm", "run-command", "invoke",
        "--resource-group", group,
        "--name", vm,
        "--command-id", "RunShellScript",
        "--scripts", script,
    )
    messages = "\n".join(str(item.get("message") or "") for item in result.get("value", []))
    if "Enable failed" in messages or ("[stderr]" in messages and "ERROR" in messages):
        raise ExperimentFailure(messages)
    return messages[-4000:]


def _data_disk_count(group: str, vm: str) -> int:
    value = _az("vm", "show", "--resource-group", group, "--name", vm)
    return len((value.get("storageProfile") or {}).get("dataDisks") or [])


def _wait_disk_detached(group: str, timeout_seconds: int = 300) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        disk = _az("disk", "show", "--resource-group", group, "--name", DATA_DISK)
        managed_by = disk.get("managedBy")
        if managed_by in (None, ""):
            return
        time.sleep(5)
    raise ExperimentFailure("data disk did not become detached after VM deletion")


def run(location: str, *, skip_root: bool = False) -> dict[str, Any]:
    group = f"easydep-pg-{uuid.uuid4().hex[:8]}"
    root_vm = "pg-root"
    data_vm = "pg-data-a"
    replacement_vm = "pg-data-b"
    started_at = datetime.now(UTC)
    admin_password = "Aa1!" + secrets.token_urlsafe(18)
    steps: list[dict[str, Any]] = []
    outcome = "failed"
    error: str | None = None

    try:
        _record(
            steps,
            "group.create",
            lambda: (
                _az("group", "create", "--name", group, "--location", location),
                group,
            )[1],
        )
        if not skip_root:
            _record(
                steps,
                "root-vm.create",
                lambda: _create_vm(group, root_vm, location, admin_password),
            )
            root_count = _record(
                steps, "root-vm.data-disk-count", lambda: _data_disk_count(group, root_vm)
            )
            if root_count != 0:
                raise ExperimentFailure(f"root control has {root_count} data disks")
            _record(
                steps,
                "root-vm.postgres",
                lambda: _run_command(group, root_vm, ROOT_DISK_PROBE),
            )
            _record(
                steps,
                "root-vm.delete",
                lambda: _az(
                    "vm", "delete", "--resource-group", group, "--name", root_vm, "--yes"
                ),
            )

        _record(
            steps,
            "data-vm.create",
            lambda: _create_vm(group, data_vm, location, admin_password),
        )
        _record(
            steps,
            "data-disk.create-and-attach",
            lambda: (
                _az(
                    "vm", "disk", "attach", "--resource-group", group,
                    "--vm-name", data_vm, "--name", DATA_DISK, "--new",
                    "--size-gb", "4", "--sku", "Standard_LRS",
                ),
                "attached-new-data-disk",
            )[1],
        )
        data_count = _record(
            steps,
            "data-vm.data-disk-count",
            lambda: _data_disk_count(group, data_vm),
        )
        if data_count != 1:
            raise ExperimentFailure(f"data control has {data_count} data disks")
        _record(
            steps,
            "data-vm.postgres-write",
            lambda: _run_command(group, data_vm, DATA_DISK_WRITE),
        )
        _record(
            steps,
            "data-vm.delete",
            lambda: _az(
                "vm", "delete", "--resource-group", group, "--name", data_vm, "--yes"
            ),
        )
        _record(steps, "data-disk.wait-detached", lambda: _wait_disk_detached(group))

        _record(
            steps,
            "replacement-vm.create",
            lambda: _create_vm(group, replacement_vm, location, admin_password),
        )
        _record(
            steps,
            "replacement-vm.attach-existing-disk",
            lambda: (
                _az(
                    "vm", "disk", "attach", "--resource-group", group,
                    "--vm-name", replacement_vm, "--name", DATA_DISK,
                ),
                "attached-existing-data-disk",
            )[1],
        )
        _record(
            steps,
            "replacement-vm.postgres-read",
            lambda: _run_command(group, replacement_vm, DATA_DISK_READ),
        )
        outcome = "passed"
    except Exception as exc:
        error = str(exc)
    finally:
        _az("group", "delete", "--name", group, "--yes", "--no-wait", check=False)
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            exists = _az("group", "exists", "--name", group, check=False)
            if exists is False:
                break
            time.sleep(10)

    residual_group = _az("group", "exists", "--name", group, check=False) is True
    cleanup_passed = not residual_group
    if not cleanup_passed:
        outcome = "failed"
    return {
        "schemaVersion": "easydep-azure-postgres-disk-intervention/v1",
        "runId": group,
        "provider": "azure",
        "location": location,
        "vmSize": VM_SIZE,
        "vmImage": IMAGE,
        "postgresImage": POSTGRES_IMAGE,
        "rootControlSkipped": skip_root,
        "startedAt": started_at.isoformat(),
        "finishedAt": datetime.now(UTC).isoformat(),
        "outcome": outcome,
        "error": error,
        "observations": {
            "postgresRunsWithBootDiskOnly": any(
                item["name"] == "root-vm.postgres" and item["status"] == "passed"
                for item in steps
            ),
            "additionalDataDiskIsNotProvisioningRequired": any(
                item["name"] == "root-vm.data-disk-count" and item.get("detail") == 0
                for item in steps
            ),
            "retainedDataDiskSurvivesVmReplacement": any(
                item["name"] == "replacement-vm.postgres-read" and item["status"] == "passed"
                for item in steps
            ),
        },
        "steps": steps,
        "cleanup": {
            "passed": cleanup_passed,
            "resourceGroupExists": residual_group,
        },
        "interpretationLimits": [
            "Both variants have an Azure OS disk; the comparison concerns an additional data disk.",
            "The replacement observation supports independent data lifecycle, not high availability.",
            "This is one development run in one Azure region and does not establish a cross-provider rate.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location", default="koreacentral")
    parser.add_argument("--skip-root", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.location, skip_root=args.skip_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
