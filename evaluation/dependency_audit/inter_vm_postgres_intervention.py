"""두 VM 사이의 사설 주소 PostgreSQL 연결을 CSP별로 개입 검증한다."""
# ruff: noqa: S608 -- 외부 입력을 결합하지 않는 고정 SQL fixture다.

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

POSTGRES_IMAGE = "postgres:17-bookworm"
POSTGRES_PASSWORD = os.environ.get("EASYDEP_TEST_POSTGRES_PASSWORD") or (
    "Aa1!" + secrets.token_urlsafe(18)
)
AWS_REGION = "ap-northeast-2"
AZURE_LOCATION = "koreacentral"
AZURE_VM_SIZE = "Standard_B2ats_v2"
GCP_ZONE = "asia-northeast3-a"
GCP_REGION = "asia-northeast3"


class ExperimentFailure(RuntimeError):
    """클라우드 명령 또는 기능 oracle이 기대 결과와 달랐음을 나타낸다."""


class TransientGuestUnavailable(ExperimentFailure):
    """VM은 생성됐지만 SSH 관리 경로가 아직 준비되지 않았음을 나타낸다."""


def _executable(name: str) -> str:
    return shutil.which(name) or shutil.which(f"{name}.cmd") or f"{name}.cmd"


def _safe_text(value: str) -> str:
    value = value.replace(POSTGRES_PASSWORD, "<redacted-password>")
    value = re.sub(
        r"-----BEGIN OPENSSH PRIVATE KEY-----.*?-----END OPENSSH PRIVATE KEY-----",
        "<redacted-private-key>",
        value,
        flags=re.DOTALL,
    )
    value = re.sub(
        r"/subscriptions/[0-9a-f-]+",
        "/subscriptions/<redacted>",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"arn:aws:[^\s\"']+", "arn:aws:<redacted>", value)
    value = re.sub(r"(?<!\d)\d{12}(?!\d)", "<redacted-account>", value)
    value = re.sub(r"projects/[^/\s\"']+", "projects/<redacted>", value)
    return value[-3000:]


def _safe_detail(value: Any) -> Any:
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, list):
        return [_safe_detail(item) for item in value]
    if isinstance(value, dict):
        return {key: _safe_detail(item) for key, item in value.items()}
    return value


def _run(command: list[str], *, timeout: int = 900, check: bool = True) -> str:
    command = [_executable(command[0]), *command[1:]]
    env = os.environ | {"AWS_PAGER": ""}
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=env,
    )
    if check and result.returncode:
        raise ExperimentFailure(_safe_text(result.stderr or result.stdout))
    return result.stdout.strip()


def _json(command: list[str], *, timeout: int = 900) -> Any:
    output = _run(command, timeout=timeout)
    return json.loads(output or "null")


class Recorder:
    def __init__(self, provider: str, run_id: str, output: Path) -> None:
        self.output = output
        self.document: dict[str, Any] = {
            "schemaVersion": "easydep-inter-vm-postgres-intervention/v1",
            "provider": provider,
            "runId": run_id,
            "startedAt": datetime.now(UTC).isoformat(),
            "transportUnderTest": "probe VM to state VM private IPv4, TCP 5432",
            "steps": [],
            "outcome": "running",
        }
        self.save()

    def save(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(
            json.dumps(self.document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def step(self, name: str, action: Callable[[], Any]) -> Any:
        started = time.monotonic()
        try:
            detail = action()
        except Exception as exc:
            self.document["steps"].append({
                "name": name,
                "status": "failed",
                "durationSeconds": round(time.monotonic() - started, 3),
                "detail": _safe_text(str(exc)),
            })
            self.save()
            raise
        self.document["steps"].append({
            "name": name,
            "status": "passed",
            "durationSeconds": round(time.monotonic() - started, 3),
            "detail": _safe_detail(detail),
        })
        self.save()
        return detail

    def finish(self, outcome: str, *, error: str | None, cleanup: dict[str, Any]) -> None:
        self.document |= {
            "outcome": outcome,
            "error": _safe_text(error or "") or None,
            "cleanup": cleanup,
            "finishedAt": datetime.now(UTC).isoformat(),
            "interpretationLimits": [
                "The experiment verifies private-address transport, not absence of a public address.",
                "One development run does not establish a provider-wide success rate.",
                "PostgreSQL connectivity does not establish high availability or performance.",
            ],
        }
        self.save()


INSTALL_DOCKER_DEBIAN = """
set -eux
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y -qq docker.io
sudo systemctl enable --now docker
sudo docker pull postgres:17-bookworm
""".strip()

INSTALL_DOCKER_AMAZON = """
set -eux
sudo dnf install -y docker
sudo systemctl enable --now docker
sudo docker pull postgres:17-bookworm
""".strip()

START_POSTGRES = f"""
sudo docker rm -f easydep-state >/dev/null 2>&1 || true
sudo docker run -d --name easydep-state --restart unless-stopped \\
  -e POSTGRES_PASSWORD={POSTGRES_PASSWORD} -p 5432:5432 {POSTGRES_IMAGE}
for i in $(seq 1 90); do
  sudo docker exec easydep-state pg_isready -U postgres && exit 0
  sleep 2
done
exit 1
""".strip()


def _psql_uri(private_ip: str) -> str:
    return f"postgresql://postgres:{POSTGRES_PASSWORD}@{private_ip}:5432/postgres?connect_timeout=5"


def _baseline_script(private_ip: str) -> str:
    uri = _psql_uri(private_ip)
    return f"""
set -eux
sudo docker run --rm {POSTGRES_IMAGE} psql '{uri}' -v ON_ERROR_STOP=1 -c \
  "CREATE TABLE IF NOT EXISTS easydep_evidence(k text PRIMARY KEY, v text); INSERT INTO easydep_evidence VALUES ('probe','inter-vm') ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v;"
value=$(sudo docker run --rm {POSTGRES_IMAGE} psql '{uri}' -tAc \
  "SELECT v FROM easydep_evidence WHERE k='probe';")
test "$value" = "inter-vm"
""".strip()


def _blocked_script(private_ip: str) -> str:
    uri = _psql_uri(private_ip)
    return f"""
set -eux
if timeout 20 sudo docker run --rm {POSTGRES_IMAGE} psql '{uri}' -tAc "SELECT 1"; then
  echo "unexpected PostgreSQL success" >&2
  exit 42
fi
exit 0
""".strip()


def _restored_script(private_ip: str) -> str:
    uri = _psql_uri(private_ip)
    return f"""
set -eux
value=$(sudo docker run --rm {POSTGRES_IMAGE} psql '{uri}' -tAc \
  "SELECT v FROM easydep_evidence WHERE k='probe';")
test "$value" = "inter-vm"
""".strip()


def _restrict_private_key(path: Path) -> None:
    if os.name != "nt":
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        return
    _run([
        "icacls", str(path), "/inheritance:r", "/grant:r", f"{getpass.getuser()}:R"
    ])


def _ssh_aws(ip: str, key: Path, command: str, *, timeout: int = 300) -> None:
    invocation = [
        _executable("ssh"), "-o", "StrictHostKeyChecking=no", "-o",
        "UserKnownHostsFile=NUL", "-o", "ConnectTimeout=15", "-o", "BatchMode=yes",
        "-i", str(key), f"ec2-user@{ip}", command,
    ]
    result = subprocess.run(
        invocation,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode == 255:
        raise TransientGuestUnavailable(_safe_text(result.stderr or result.stdout))
    if result.returncode:
        raise ExperimentFailure(_safe_text(result.stderr or result.stdout))


def _aws_value(*args: str) -> str:
    return _run(["aws", "--region", AWS_REGION, *args, "--output", "text"])


def run_aws(output: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    prefix = f"easydep-ivm-{suffix}"
    rec = Recorder("aws", prefix, output)
    instance_ids: list[str] = []
    group_ids: list[str] = []
    key_name = f"{prefix}-key"
    cleanup: dict[str, Any] = {"passed": False, "residual": []}
    error: str | None = None

    with tempfile.TemporaryDirectory(dir=output.parent) as temp_dir:
        key_path = Path(temp_dir) / "aws.pem"
        try:
            vpc_id = rec.step("network.default-vpc", lambda: _aws_value(
                "ec2", "describe-vpcs", "--filters", "Name=isDefault,Values=true",
                "--query", "Vpcs[0].VpcId",
            ).strip())
            subnet_id = rec.step("network.subnet", lambda: _aws_value(
                "ec2", "describe-subnets", "--filters", f"Name=vpc-id,Values={vpc_id}",
                "--query", "Subnets[0].SubnetId",
            ).strip())
            ami = rec.step("image.resolve", lambda: _aws_value(
                "ssm", "get-parameter", "--name",
                "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
                "--query", "Parameter.Value",
            ).strip())
            probe_sg = rec.step("probe-sg.create", lambda: _json([
                "aws", "--region", AWS_REGION, "ec2", "create-security-group",
                "--group-name", f"{prefix}-probe", "--description", "EasyDep inter-VM probe",
                "--vpc-id", vpc_id, "--tag-specifications",
                f"ResourceType=security-group,Tags=[{{Key=easydep-run,Value={prefix}}}]",
            ])["GroupId"])
            group_ids.append(probe_sg)
            state_sg = rec.step("state-sg.create", lambda: _json([
                "aws", "--region", AWS_REGION, "ec2", "create-security-group",
                "--group-name", f"{prefix}-state", "--description", "EasyDep inter-VM state",
                "--vpc-id", vpc_id, "--tag-specifications",
                f"ResourceType=security-group,Tags=[{{Key=easydep-run,Value={prefix}}}]",
            ])["GroupId"])
            group_ids.append(state_sg)
            rec.step("probe-sg.allow-ssh", lambda: _run([
                "aws", "--region", AWS_REGION, "ec2", "authorize-security-group-ingress",
                "--group-id", probe_sg, "--protocol", "tcp", "--port", "22",
                "--cidr", "0.0.0.0/0",
            ]) or "tcp/22 temporary key-only access")

            def allow_postgres() -> str:
                _run([
                    "aws", "--region", AWS_REGION, "ec2", "authorize-security-group-ingress",
                    "--group-id", state_sg, "--protocol", "tcp", "--port", "5432",
                    "--source-group", probe_sg,
                ])
                return "state tcp/5432 from probe security group"

            rec.step("state-sg.allow-postgres", allow_postgres)
            material = rec.step("key.create", lambda: _aws_value(
                "ec2", "create-key-pair", "--key-name", key_name, "--key-type", "ed25519",
                "--query", "KeyMaterial",
            ))
            key_path.write_text(material + "\n", encoding="utf-8")
            _restrict_private_key(key_path)

            def launch(name: str, sg: str, user_data: str) -> str:
                script_path = Path(temp_dir) / f"{name}.sh"
                script_path.write_text("#!/bin/bash\n" + user_data + "\n", encoding="utf-8")
                value = _json([
                    "aws", "--region", AWS_REGION, "ec2", "run-instances",
                    "--image-id", ami, "--instance-type", "t3.micro", "--count", "1",
                    "--subnet-id", subnet_id, "--security-group-ids", sg,
                    "--associate-public-ip-address", "--key-name", key_name,
                    "--user-data", f"file://{script_path}", "--tag-specifications",
                    f"ResourceType=instance,Tags=[{{Key=Name,Value={name}}},{{Key=easydep-run,Value={prefix}}}]",
                    f"ResourceType=volume,Tags=[{{Key=easydep-run,Value={prefix}}}]",
                ], timeout=300)
                return value["Instances"][0]["InstanceId"]

            state_id = rec.step(
                "state-vm.create",
                lambda: launch(f"{prefix}-state", state_sg, INSTALL_DOCKER_AMAZON + "\n" + START_POSTGRES),
            )
            instance_ids.append(state_id)
            probe_id = rec.step(
                "probe-vm.create",
                lambda: launch(f"{prefix}-probe", probe_sg, INSTALL_DOCKER_AMAZON + "\n" + "touch /tmp/easydep-ready"),
            )
            instance_ids.append(probe_id)
            rec.step("vms.running", lambda: _run([
                "aws", "--region", AWS_REGION, "ec2", "wait", "instance-running",
                "--instance-ids", state_id, probe_id,
            ], timeout=600) or "both running")
            values = _json([
                "aws", "--region", AWS_REGION, "ec2", "describe-instances",
                "--instance-ids", state_id, probe_id,
            ])
            by_id = {
                item["InstanceId"]: item
                for reservation in values["Reservations"] for item in reservation["Instances"]
            }
            state_private = by_id[state_id]["PrivateIpAddress"]
            probe_public = by_id[probe_id]["PublicIpAddress"]
            rec.document["networkObservation"] = {
                "stateAddressUsedByProbe": state_private,
                "probeHadPublicManagementAddress": True,
                "stateHadPublicBootstrapAddress": True,
            }
            rec.save()

            def probe_ssh(command: str, timeout: int = 300, budget: int = 300) -> str:
                deadline = time.monotonic() + budget
                last_error = ""
                while time.monotonic() < deadline:
                    try:
                        _ssh_aws(probe_public, key_path, command, timeout=timeout)
                        return "guest command passed"
                    except ExperimentFailure as exc:
                        last_error = str(exc)
                        time.sleep(10)
                raise ExperimentFailure(last_error)

            def wait_for_probe_tools() -> str:
                deadline = time.monotonic() + 600
                last_error = ""
                while time.monotonic() < deadline:
                    try:
                        _ssh_aws(
                            probe_public,
                            key_path,
                            "test -f /tmp/easydep-ready && command -v docker >/dev/null",
                            timeout=60,
                        )
                        return "cloud-init and Docker ready"
                    except ExperimentFailure as exc:
                        last_error = str(exc)
                        time.sleep(10)
                raise ExperimentFailure(last_error)

            rec.step("probe-vm.tools-ready", wait_for_probe_tools)
            rec.step(
                "baseline.private-postgres-write-read",
                lambda: probe_ssh(_baseline_script(state_private)),
            )
            rec.step("intervention.revoke-postgres", lambda: _run([
                "aws", "--region", AWS_REGION, "ec2", "revoke-security-group-ingress",
                "--group-id", state_sg, "--protocol", "tcp", "--port", "5432",
                "--source-group", probe_sg,
            ]) or "tcp/5432 rule revoked")
            rec.step(
                "intervention.connection-blocked",
                lambda: probe_ssh(_blocked_script(state_private), timeout=90, budget=180),
            )
            rec.step("restore.allow-postgres", allow_postgres)
            rec.step("restore.private-postgres-read", lambda: probe_ssh(_restored_script(state_private)))
            outcome = "passed"
        except Exception as exc:
            outcome = "failed"
            error = str(exc)
        finally:
            if instance_ids:
                _run([
                    "aws", "--region", AWS_REGION, "ec2", "terminate-instances",
                    "--instance-ids", *instance_ids,
                ], check=False)
                _run([
                    "aws", "--region", AWS_REGION, "ec2", "wait", "instance-terminated",
                    "--instance-ids", *instance_ids,
                ], timeout=900, check=False)
            _run([
                "aws", "--region", AWS_REGION, "ec2", "delete-key-pair", "--key-name", key_name,
            ], check=False)
            deadline = time.monotonic() + 180
            while group_ids and time.monotonic() < deadline:
                remaining = []
                for group_id in reversed(group_ids):
                    try:
                        _run([
                            "aws", "--region", AWS_REGION, "ec2", "delete-security-group",
                            "--group-id", group_id,
                        ])
                    except ExperimentFailure:
                        remaining.append(group_id)
                group_ids = remaining
                if group_ids:
                    time.sleep(10)
            residual_instances = json.loads(_run([
                "aws", "--region", AWS_REGION, "ec2", "describe-instances", "--filters",
                f"Name=tag:easydep-run,Values={prefix}", "Name=instance-state-name,Values=pending,running,stopping,stopped",
                "--query", "Reservations[].Instances[].InstanceId", "--output", "json",
            ], check=False) or "[]")
            residual_groups = json.loads(_run([
                "aws", "--region", AWS_REGION, "ec2", "describe-security-groups", "--filters",
                f"Name=tag:easydep-run,Values={prefix}", "--query", "SecurityGroups[].GroupId",
                "--output", "json",
            ], check=False) or "[]")
            residual_volumes = json.loads(_run([
                "aws", "--region", AWS_REGION, "ec2", "describe-volumes", "--filters",
                f"Name=tag:easydep-run,Values={prefix}", "--query", "Volumes[].VolumeId",
                "--output", "json",
            ], check=False) or "[]")
            residual_keys = json.loads(_run([
                "aws", "--region", AWS_REGION, "ec2", "describe-key-pairs", "--filters",
                f"Name=key-name,Values={key_name}", "--query", "KeyPairs[].KeyName",
                "--output", "json",
            ], check=False) or "[]")
            residual = [
                *residual_instances, *residual_groups, *residual_volumes, *residual_keys
            ]
            cleanup = {"passed": not residual, "residual": residual}
            rec.finish(outcome if cleanup["passed"] else "failed", error=error, cleanup=cleanup)
    return rec.document


def _az_run(group: str, vm: str, script: str) -> str:
    # Azure Run Command reports ProvisioningState/succeeded even when the guest
    # shell exits non-zero. Keep the exit inside a subshell and print a sentinel
    # after it so the harness can distinguish control-plane delivery from guest
    # command success.
    encoded_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
    script_argument = (
        "easydep_script=$(mktemp /tmp/easydep-run-command.XXXXXX); "
        f"echo '{encoded_script}' | base64 -d > \"$easydep_script\"; "
        "bash \"$easydep_script\"; easydep_exit_code=$?; "
        "rm -f \"$easydep_script\"; "
        "echo EASYDEP_EXIT_CODE=$easydep_exit_code; exit 0"
    )
    value = _json([
        "az", "vm", "run-command", "invoke", "--resource-group", group, "--name", vm,
        "--command-id", "RunShellScript", "--scripts", script_argument, "-o", "json",
    ], timeout=1200)
    messages = "\n".join(str(item.get("message") or "") for item in value.get("value", []))
    if "Enable failed" in messages:
        raise ExperimentFailure(messages)
    match = re.search(r"EASYDEP_EXIT_CODE=(\d+)", messages)
    if match is None:
        raise ExperimentFailure(
            "Azure guest command did not report an exit code: " + _safe_text(messages)
        )
    if match.group(1) != "0":
        raise ExperimentFailure(messages)
    return "guest command passed"


def run_azure(output: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    group = f"easydep-ivm-{suffix}"
    rec = Recorder("azure", group, output)
    admin_password = "Aa1!" + secrets.token_urlsafe(18)
    cleanup: dict[str, Any] = {"passed": False, "residual": []}
    error: str | None = None
    try:
        rec.step("group.create", lambda: _run([
            "az", "group", "create", "--name", group, "--location", AZURE_LOCATION,
            "--tags", f"easydep-run={group}", "-o", "none",
        ]) or group)
        rec.step("network.create", lambda: _run([
            "az", "network", "vnet", "create", "-g", group, "-n", "vnet",
            "--address-prefix", "10.77.0.0/16", "--subnet-name", "workloads",
            "--subnet-prefix", "10.77.1.0/24", "-o", "none",
        ]) or "10.77.0.0/16")
        for name in ("probe-nsg", "state-nsg"):
            rec.step(f"{name}.create", lambda name=name: _run([
                "az", "network", "nsg", "create", "-g", group, "-n", name, "-o", "none",
            ]) or name)
        rec.step("state-nsg.deny-vnet-postgres", lambda: _run([
            "az", "network", "nsg", "rule", "create", "-g", group,
            "--nsg-name", "state-nsg", "-n", "deny-vnet-postgres",
            "--priority", "110", "--access", "Deny", "--protocol", "Tcp",
            "--source-address-prefixes", "VirtualNetwork", "--destination-port-ranges", "5432",
            "-o", "none",
        ]) or "deny tcp/5432 after explicit allow")

        def create_vm(name: str, nsg: str) -> None:
            _run([
                "az", "vm", "create", "-g", group, "-n", name,
                "--location", AZURE_LOCATION, "--image", "Ubuntu2204",
                "--size", AZURE_VM_SIZE, "--vnet-name", "vnet", "--subnet", "workloads",
                "--nsg", nsg, "--public-ip-address", "", "--admin-username", "easydep",
                "--authentication-type", "password", "--admin-password", admin_password,
                "--tags", f"easydep-run={group}", "-o", "none",
            ], timeout=1200)

        rec.step("state-vm.create", lambda: create_vm("state", "state-nsg") or "state")
        rec.step("probe-vm.create", lambda: create_vm("probe", "probe-nsg") or "probe")
        state_private = _run([
            "az", "vm", "show", "-d", "-g", group, "-n", "state",
            "--query", "privateIps", "-o", "tsv",
        ]).strip()
        probe_private = _run([
            "az", "vm", "show", "-d", "-g", group, "-n", "probe",
            "--query", "privateIps", "-o", "tsv",
        ]).strip()
        rec.document["networkObservation"] = {
            "stateAddressUsedByProbe": state_private,
            "probeAddressUsedByRule": probe_private,
            "publicAddresses": False,
        }
        rec.save()

        def allow_postgres() -> str:
            _run([
                "az", "network", "nsg", "rule", "create", "-g", group,
                "--nsg-name", "state-nsg", "-n", "allow-probe-postgres",
                "--priority", "100", "--access", "Allow", "--protocol", "Tcp",
                "--source-address-prefixes", f"{probe_private}/32",
                "--destination-port-ranges", "5432", "-o", "none",
            ])
            return "state tcp/5432 from probe private IPv4"

        rec.step("state-nsg.allow-postgres", allow_postgres)
        rec.step("state-vm.postgres", lambda: _az_run(
            group, "state", INSTALL_DOCKER_DEBIAN + "\n" + START_POSTGRES
        ))
        rec.step("probe-vm.tools", lambda: _az_run(group, "probe", INSTALL_DOCKER_DEBIAN))
        rec.step("baseline.private-postgres-write-read", lambda: _az_run(
            group, "probe", _baseline_script(state_private)
        ))
        rec.step("intervention.delete-allow", lambda: _run([
            "az", "network", "nsg", "rule", "delete", "-g", group,
            "--nsg-name", "state-nsg", "-n", "allow-probe-postgres", "-o", "none",
        ]) or "explicit allow removed; deny rule remains")
        rec.step("intervention.connection-blocked", lambda: _az_run(
            group, "probe", _blocked_script(state_private)
        ))
        rec.step("restore.allow-postgres", allow_postgres)
        rec.step("restore.private-postgres-read", lambda: _az_run(
            group, "probe", _restored_script(state_private)
        ))
        outcome = "passed"
    except Exception as exc:
        outcome = "failed"
        error = str(exc)
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
        rec.finish(outcome if cleanup["passed"] else "failed", error=error, cleanup=cleanup)
    return rec.document


def _gcp_probe_controller(private_ip: str) -> str:
    """Run the data-plane checks in the guest without relying on host-to-VM SSH."""
    baseline = _baseline_script(private_ip).replace("set -eux", "set -eu")
    blocked = _blocked_script(private_ip).replace("set -eux", "set -eu")
    restored = _restored_script(private_ip).replace("set -eux", "set -eu")
    metadata = (
        "http://metadata.google.internal/computeMetadata/v1/instance/attributes/"
        "easydep-phase"
    )
    return f"""#!/bin/bash
set -eu
{INSTALL_DOCKER_DEBIAN.replace("set -eux", "set -eu")}
for i in $(seq 1 60); do
  if (
{baseline}
  ); then
    echo 'EASYDEP_RESULT baseline passed'
    baseline_passed=1
    break
  fi
  sleep 5
done
test "${{baseline_passed:-0}}" = 1
while [ "$(curl -fsS -H 'Metadata-Flavor: Google' '{metadata}' || true)" != block ]; do
  sleep 2
done
(
{blocked}
)
echo 'EASYDEP_RESULT blocked passed'
while [ "$(curl -fsS -H 'Metadata-Flavor: Google' '{metadata}' || true)" != restore ]; do
  sleep 2
done
for i in $(seq 1 60); do
  if (
{restored}
  ); then
    echo 'EASYDEP_RESULT restored passed'
    exit 0
  fi
  sleep 5
done
echo 'EASYDEP_RESULT restored failed'
exit 1
"""


def _gcp_wait_for_marker(
    project: str,
    vm: str,
    marker: str,
    *,
    timeout: int = 600,
) -> str:
    deadline = time.monotonic() + timeout
    last_output = ""
    while time.monotonic() < deadline:
        last_output = _run([
            "gcloud", "compute", "instances", "get-serial-port-output", vm,
            "--project", project, "--zone", GCP_ZONE, "--port", "1",
        ], timeout=60, check=False)
        if marker in last_output:
            return marker
        if "EASYDEP_RESULT restored failed" in last_output:
            raise ExperimentFailure("probe guest reported restore failure")
        time.sleep(10)
    raise ExperimentFailure(
        f"serial marker not observed: {marker}; tail={_safe_text(last_output)}"
    )


def run_gcp(output: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    prefix = f"easydep-ivm-{suffix}"
    network = f"{prefix}-net"
    subnet = f"{prefix}-subnet"
    probe = f"{prefix}-probe"
    state = f"{prefix}-state"
    allow_pg = f"{prefix}-allow-pg"
    project = _run(["gcloud", "config", "get-value", "project"]).strip()
    rec = Recorder("gcp", prefix, output)
    cleanup: dict[str, Any] = {"passed": False, "residual": []}
    error: str | None = None
    try:
        rec.step("network.create", lambda: _run([
            "gcloud", "compute", "networks", "create", network, "--project", project,
            "--subnet-mode", "custom", "--quiet",
        ]) or network)
        rec.step("subnet.create", lambda: _run([
            "gcloud", "compute", "networks", "subnets", "create", subnet,
            "--project", project, "--network", network, "--region", GCP_REGION,
            "--range", "10.78.1.0/24", "--quiet",
        ]) or "10.78.1.0/24")
        def allow_postgres() -> str:
            _run([
                "gcloud", "compute", "firewall-rules", "create", allow_pg,
                "--project", project, "--network", network, "--direction", "INGRESS",
                "--action", "ALLOW", "--rules", "tcp:5432", "--source-tags", "easydep-probe",
                "--target-tags", "easydep-state", "--quiet",
            ])
            return "state tcp/5432 from probe source tag"

        rec.step("firewall.allow-postgres", allow_postgres)
        with tempfile.TemporaryDirectory(dir=output.parent) as temp_dir:
            state_script = Path(temp_dir) / "state.sh"
            state_script.write_text(
                "#!/bin/bash\n"
                + INSTALL_DOCKER_DEBIAN.replace("set -eux", "set -eu")
                + "\n"
                + START_POSTGRES.replace("set -eux", "set -eu")
                + "\n",
                encoding="utf-8",
            )
            rec.step("state-vm.create", lambda: _run([
                "gcloud", "compute", "instances", "create", state, "--project", project,
                "--zone", GCP_ZONE, "--machine-type", "e2-small", "--network", network,
                "--subnet", subnet, "--tags", "easydep-state", "--image-family", "debian-12",
                "--image-project", "debian-cloud", "--boot-disk-size", "10GB",
                "--metadata-from-file", f"startup-script={state_script}",
                "--labels", f"easydep-run={suffix}", "--quiet",
            ], timeout=900) or state)
            state_private = _run([
                "gcloud", "compute", "instances", "describe", state,
                "--project", project, "--zone", GCP_ZONE,
                "--format", "value(networkInterfaces[0].networkIP)",
            ]).strip()
            probe_script = Path(temp_dir) / "probe.sh"
            probe_script.write_text(
                _gcp_probe_controller(state_private),
                encoding="utf-8",
            )
            rec.step("probe-vm.create", lambda: _run([
                "gcloud", "compute", "instances", "create", probe, "--project", project,
                "--zone", GCP_ZONE, "--machine-type", "e2-small", "--network", network,
                "--subnet", subnet, "--tags", "easydep-probe", "--image-family", "debian-12",
                "--image-project", "debian-cloud", "--boot-disk-size", "10GB",
                "--metadata-from-file", f"startup-script={probe_script}",
                "--labels", f"easydep-run={suffix}", "--quiet",
            ], timeout=900) or probe)
        rec.document["networkObservation"] = {
            "stateAddressUsedByProbe": state_private,
            "probeHadPublicBootstrapAddress": True,
            "stateHadPublicBootstrapAddress": True,
            "hostToVmManagementChannel": "none; guest serial markers and metadata phases",
        }
        rec.save()

        rec.step(
            "baseline.private-postgres-write-read",
            lambda: _gcp_wait_for_marker(project, probe, "EASYDEP_RESULT baseline passed"),
        )
        rec.step("intervention.delete-allow", lambda: _run([
            "gcloud", "compute", "firewall-rules", "delete", allow_pg,
            "--project", project, "--quiet",
        ]) or "tcp/5432 allow removed")
        rec.step("intervention.signal-block", lambda: _run([
            "gcloud", "compute", "instances", "add-metadata", probe,
            "--project", project, "--zone", GCP_ZONE,
            "--metadata", "easydep-phase=block", "--quiet",
        ]) or "probe phase=block")
        rec.step(
            "intervention.connection-blocked",
            lambda: _gcp_wait_for_marker(project, probe, "EASYDEP_RESULT blocked passed", timeout=180),
        )
        rec.step("restore.allow-postgres", allow_postgres)
        rec.step("restore.signal", lambda: _run([
            "gcloud", "compute", "instances", "add-metadata", probe,
            "--project", project, "--zone", GCP_ZONE,
            "--metadata", "easydep-phase=restore", "--quiet",
        ]) or "probe phase=restore")
        rec.step(
            "restore.private-postgres-read",
            lambda: _gcp_wait_for_marker(project, probe, "EASYDEP_RESULT restored passed"),
        )
        outcome = "passed"
    except Exception as exc:
        outcome = "failed"
        error = str(exc)
    finally:
        _run([
            "gcloud", "compute", "instances", "delete", probe, state, "--project", project,
            "--zone", GCP_ZONE, "--quiet",
        ], timeout=900, check=False)
        for rule in (allow_pg,):
            _run([
                "gcloud", "compute", "firewall-rules", "delete", rule,
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
        residual_instances = _run([
            "gcloud", "compute", "instances", "list", "--project", project,
            "--filter", f"labels.easydep-run={suffix}", "--format", "value(name)",
        ], check=False)
        residual_firewalls = _run([
            "gcloud", "compute", "firewall-rules", "list", "--project", project,
            "--filter", f"name~'^{prefix}'", "--format", "value(name)",
        ], check=False)
        residual_networks = _run([
            "gcloud", "compute", "networks", "list", "--project", project,
            "--filter", f"name={network}", "--format", "value(name)",
        ], check=False)
        residual_subnets = _run([
            "gcloud", "compute", "networks", "subnets", "list", "--project", project,
            "--filter", f"name={subnet}", "--format", "value(name)",
        ], check=False)
        residual = [
            item
            for text in (
                residual_instances, residual_firewalls, residual_networks, residual_subnets
            )
            for item in text.splitlines()
            if item.strip()
        ]
        cleanup = {"passed": not residual, "residual": residual}
        rec.finish(outcome if cleanup["passed"] else "failed", error=error, cleanup=cleanup)
    return rec.document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("aws", "azure", "gcp"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runners = {"aws": run_aws, "azure": run_azure, "gcp": run_gcp}
    result = runners[args.provider](args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
