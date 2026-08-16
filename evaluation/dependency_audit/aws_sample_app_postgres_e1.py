"""도메인 중립 테스트 앱으로 AWS E1 App→PostgreSQL→Volume 경로를 검증한다."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from evaluation.dependency_audit.inter_vm_postgres_intervention import (
    AWS_REGION,
    INSTALL_DOCKER_AMAZON,
    POSTGRES_IMAGE,
    POSTGRES_PASSWORD,
    ExperimentFailure,
    Recorder,
    _aws_value,
    _json,
    _restrict_private_key,
    _run,
    _safe_text,
    _ssh_aws,
)
from evaluation.dependency_audit.sample_app_postgres_e1_common import (
    APP_IMAGE,
    app_build_script,
)


class E1Recorder(Recorder):
    def __init__(self, run_id: str, output: Path) -> None:
        super().__init__("aws", run_id, output)
        self.document |= {
            "schemaVersion": "easydep-aws-sample-app-postgres-e1/v1",
            "transportUnderTest": "app VM/container to state VM private IPv4, TCP 5432",
            "pathUnderTest": (
                "public HTTP -> app VM/container -> state VM private IPv4:5432 "
                "-> PostgreSQL data path -> EBS data volume"
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
                "Public HTTP is used; trusted HTTPS, DNS, and certificate dependencies are not tested.",
                "A same-instance reboot is not state-VM replacement or state-tier high availability.",
                "One development run does not establish an AWS-wide success rate.",
            ],
        }
        self.save()


def _caller_cidr() -> str:
    with urlopen("https://checkip.amazonaws.com", timeout=15) as response:  # noqa: S310
        address = response.read().decode("ascii").strip()
    if not address or any(character not in "0123456789." for character in address):
        raise ExperimentFailure("unable to resolve the IPv4 address of the test runner")
    return f"{address}/32"


def _http(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=12) as response:  # noqa: S310 - run-owned endpoint
            body = response.read()
            return {"status": response.status, "body": json.loads(body or b"{}")}
    except HTTPError as error:
        body = error.read()
        return {"status": error.code, "body": json.loads(body or b"{}")}


def _wait_http(
    method: str,
    url: str,
    expected_status: int,
    *,
    payload: dict[str, Any] | None = None,
    budget: int = 300,
) -> dict[str, Any]:
    deadline = time.monotonic() + budget
    last: dict[str, Any] | str = "no response"
    while time.monotonic() < deadline:
        try:
            last = _http(method, url, payload)
            if last["status"] == expected_status:
                return last
        except (OSError, URLError, TimeoutError, json.JSONDecodeError) as error:
            last = str(error)
        time.sleep(5)
    raise ExperimentFailure(f"HTTP {expected_status} not observed for {url}: {last}")


def _app_build_script(install_docker: str) -> str:
    """이전 import 경로를 유지하는 얇은 호환 래퍼다."""
    return app_build_script(install_docker)


def _app_user_data() -> str:
    return _app_build_script(INSTALL_DOCKER_AMAZON)


def _state_user_data() -> str:
    return f"""{INSTALL_DOCKER_AMAZON}
touch /tmp/easydep-ready
"""


def run(output: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    prefix = f"easydep-e1-{suffix}"
    recorder = E1Recorder(prefix, output)
    instances: list[str] = []
    groups: list[str] = []
    volume_id = ""
    key_name = f"{prefix}-key"
    error: str | None = None
    cleanup: dict[str, Any] = {"passed": False, "residual": []}

    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        key_path = Path(temporary) / "aws.pem"
        try:
            caller_cidr = _caller_cidr()
            recorder.step("runner.public-ip", lambda: "runner IPv4 /32 resolved")
            vpc_id = recorder.step(
                "network.default-vpc",
                lambda: _aws_value(
                    "ec2",
                    "describe-vpcs",
                    "--filters",
                    "Name=isDefault,Values=true",
                    "--query",
                    "Vpcs[0].VpcId",
                ).strip(),
            )
            subnet = recorder.step(
                "network.subnet",
                lambda: _json([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "describe-subnets",
                    "--filters",
                    f"Name=vpc-id,Values={vpc_id}",
                    "--query",
                    "Subnets[0].{id:SubnetId,az:AvailabilityZone}",
                ]),
            )
            ami = recorder.step(
                "image.resolve",
                lambda: _aws_value(
                    "ssm",
                    "get-parameter",
                    "--name",
                    "/aws/service/ami-amazon-linux-latest/"
                    "al2023-ami-kernel-default-x86_64",
                    "--query",
                    "Parameter.Value",
                ).strip(),
            )

            def create_group(role: str) -> str:
                result = _json([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "create-security-group",
                    "--group-name",
                    f"{prefix}-{role}",
                    "--description",
                    f"EasyDep E1 {role}",
                    "--vpc-id",
                    vpc_id,
                    "--tag-specifications",
                    "ResourceType=security-group,"
                    f"Tags=[{{Key=easydep-run,Value={prefix}}}]",
                ])
                groups.append(result["GroupId"])
                return result["GroupId"]

            app_sg = recorder.step("app-sg.create", lambda: create_group("app"))
            state_sg = recorder.step("state-sg.create", lambda: create_group("state"))
            def allow_runner(group: str, port: str) -> str:
                _run([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "authorize-security-group-ingress",
                    "--group-id",
                    group,
                    "--protocol",
                    "tcp",
                    "--port",
                    port,
                    "--cidr",
                    caller_cidr,
                ])
                return f"tcp/{port} from runner /32"

            for group, port in ((app_sg, "22"), (app_sg, "8080"), (state_sg, "22")):
                recorder.step(
                    f"security.allow-{group}-{port}",
                    lambda group=group, port=port: allow_runner(group, port),
                )

            def allow_postgres() -> str:
                _run([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "authorize-security-group-ingress",
                    "--group-id",
                    state_sg,
                    "--protocol",
                    "tcp",
                    "--port",
                    "5432",
                    "--source-group",
                    app_sg,
                ])
                return "state tcp/5432 from app security group"

            recorder.step("state-sg.allow-postgres", allow_postgres)
            material = recorder.step(
                "key.create",
                lambda: _aws_value(
                    "ec2",
                    "create-key-pair",
                    "--key-name",
                    key_name,
                    "--key-type",
                    "ed25519",
                    "--query",
                    "KeyMaterial",
                ),
            )
            key_path.write_text(material + "\n", encoding="utf-8")
            _restrict_private_key(key_path)

            def launch(
                name: str,
                security_group: str,
                user_data: str,
                *,
                instance_type: str,
            ) -> str:
                script = Path(temporary) / f"{name}.sh"
                script.write_text("#!/bin/bash\n" + user_data + "\n", encoding="utf-8")
                result = _json([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "run-instances",
                    "--image-id",
                    ami,
                    "--instance-type",
                    instance_type,
                    "--count",
                    "1",
                    "--subnet-id",
                    subnet["id"],
                    "--security-group-ids",
                    security_group,
                    "--associate-public-ip-address",
                    "--key-name",
                    key_name,
                    "--user-data",
                    f"file://{script}",
                    "--tag-specifications",
                    "ResourceType=instance,"
                    f"Tags=[{{Key=Name,Value={name}}},{{Key=easydep-run,Value={prefix}}}]",
                    "ResourceType=volume,"
                    f"Tags=[{{Key=easydep-run,Value={prefix}}}]",
                ], timeout=300)
                return result["Instances"][0]["InstanceId"]

            state_id = recorder.step(
                "state-vm.create",
                lambda: launch(
                    f"{prefix}-state",
                    state_sg,
                    _state_user_data(),
                    instance_type="t3.micro",
                ),
            )
            instances.append(state_id)
            app_id = recorder.step(
                "app-vm.create",
                lambda: launch(
                    f"{prefix}-app",
                    app_sg,
                    _app_user_data(),
                    instance_type="t3.small",
                ),
            )
            instances.append(app_id)
            recorder.step(
                "vms.running",
                lambda: _run([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "wait",
                    "instance-running",
                    "--instance-ids",
                    state_id,
                    app_id,
                ], timeout=600)
                or "both VMs running",
            )
            described = _json([
                "aws",
                "--region",
                AWS_REGION,
                "ec2",
                "describe-instances",
                "--instance-ids",
                state_id,
                app_id,
            ])
            by_id = {
                instance["InstanceId"]: instance
                for reservation in described["Reservations"]
                for instance in reservation["Instances"]
            }
            state_public = by_id[state_id]["PublicIpAddress"]
            state_private = by_id[state_id]["PrivateIpAddress"]
            app_public = by_id[app_id]["PublicIpAddress"]
            recorder.document["networkObservation"] = {
                "appEndpoint": "public IPv4 TCP/8080 restricted to runner /32",
                "databaseEndpointUsedByApp": f"{state_private}:5432",
                "databaseIngressSource": "app security group",
            }
            recorder.save()

            def ssh_retry(
                address: str,
                command: str,
                *,
                budget: int = 600,
                command_timeout: int = 120,
            ) -> str:
                deadline = time.monotonic() + budget
                last_error = ""
                while time.monotonic() < deadline:
                    try:
                        _ssh_aws(address, key_path, command, timeout=command_timeout)
                        return "guest command passed"
                    except (ExperimentFailure, subprocess.TimeoutExpired) as exception:
                        last_error = str(exception)
                        time.sleep(10)
                raise ExperimentFailure(last_error)

            recorder.step(
                "state-vm.tools-ready",
                lambda: ssh_retry(
                    state_public,
                    "test -f /tmp/easydep-ready && command -v docker >/dev/null",
                ),
            )
            recorder.step(
                "app-vm.image-ready",
                lambda: ssh_retry(
                    app_public,
                    f"test -f /tmp/easydep-ready && sudo docker image inspect {APP_IMAGE} >/dev/null",
                    budget=900,
                ),
            )
            volume_id = recorder.step(
                "state-volume.create",
                lambda: _json([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "create-volume",
                    "--availability-zone",
                    subnet["az"],
                    "--size",
                    "4",
                    "--volume-type",
                    "gp3",
                    "--tag-specifications",
                    "ResourceType=volume,"
                    f"Tags=[{{Key=Name,Value={prefix}-data}},{{Key=easydep-run,Value={prefix}}}]",
                ])["VolumeId"],
            )
            recorder.step(
                "state-volume.available",
                lambda: _run([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "wait",
                    "volume-available",
                    "--volume-ids",
                    volume_id,
                ], timeout=300)
                or "volume available",
            )
            recorder.step(
                "state-volume.attach",
                lambda: _run([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "attach-volume",
                    "--volume-id",
                    volume_id,
                    "--instance-id",
                    state_id,
                    "--device",
                    "/dev/sdf",
                ])
                or "volume attached",
            )
            recorder.step(
                "state-volume.in-use",
                lambda: _run([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "wait",
                    "volume-in-use",
                    "--volume-ids",
                    volume_id,
                ], timeout=300)
                or "volume attachment complete",
            )
            serial = volume_id.replace("-", "")
            device_resolve = f"""
set -eu
for i in $(seq 1 60); do
  link=$(find /dev/disk/by-id -maxdepth 1 -type l -name '*{serial}' -print -quit 2>/dev/null || true)
  [ -n "$link" ] && break
  sleep 2
done
test -n "${{link:-}}"
readlink -f "$link" | sudo tee /tmp/easydep-data-device >/dev/null
test -b "$(cat /tmp/easydep-data-device)"
"""
            recorder.step(
                "state-volume.guest-device",
                lambda: ssh_retry(
                    state_public,
                    device_resolve,
                    budget=240,
                    command_timeout=150,
                ),
            )
            mount_volume = """
set -eu
device=$(cat /tmp/easydep-data-device)
if ! sudo blkid "$device" >/dev/null 2>&1; then sudo mkfs.ext4 -F "$device"; fi
uuid=$(sudo blkid -s UUID -o value "$device")
sudo mkdir -p /var/lib/easydep-postgres
grep -q "$uuid" /etc/fstab || echo "UUID=$uuid /var/lib/easydep-postgres ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab >/dev/null
sudo mount -a
sudo mkdir -p /var/lib/easydep-postgres/data
sudo chown 999:999 /var/lib/easydep-postgres/data
mountpoint -q /var/lib/easydep-postgres
"""
            recorder.step(
                "state-volume.format-and-mount",
                lambda: ssh_retry(
                    state_public,
                    mount_volume,
                    budget=300,
                    command_timeout=240,
                ),
            )
            start_postgres = f"""
set -eu
sudo docker rm -f easydep-state >/dev/null 2>&1 || true
sudo docker run -d --name easydep-state --restart unless-stopped \
  -e POSTGRES_PASSWORD='{POSTGRES_PASSWORD}' -p 5432:5432 \
  -v /var/lib/easydep-postgres/data:/var/lib/postgresql/data:Z {POSTGRES_IMAGE}
for i in $(seq 1 90); do
  sudo docker exec easydep-state pg_isready -U postgres && exit 0
  sleep 2
done
exit 1
"""
            recorder.step(
                "state-postgres.start",
                lambda: ssh_retry(
                    state_public,
                    start_postgres,
                    budget=420,
                    command_timeout=300,
                ),
            )
            database_url = (
                f"postgresql://postgres:{POSTGRES_PASSWORD}@{state_private}:5432/postgres"
            )
            app_start = f"""
sudo docker rm -f easydep-app >/dev/null 2>&1 || true
sudo docker run -d --name easydep-app --restart unless-stopped -p 8080:8080 \
  -e DATABASE_URL='{database_url}' {APP_IMAGE} --role postgres-app
"""
            recorder.step(
                "app-container.start",
                lambda: ssh_retry(app_public, app_start, budget=300),
            )
            base_url = f"http://{app_public}:8080"
            recorder.step(
                "baseline.readiness",
                lambda: _wait_http("GET", f"{base_url}/health/ready", HTTPStatus.OK),
            )
            recorder.step(
                "baseline.business-write",
                lambda: _wait_http(
                    "PUT",
                    f"{base_url}/records/evidence",
                    HTTPStatus.OK,
                    payload={"value": {"message": "kept"}},
                ),
            )

            def read_kept() -> dict[str, Any]:
                response = _wait_http(
                    "GET", f"{base_url}/records/evidence", HTTPStatus.OK
                )
                if response["body"].get("value") != {"message": "kept"}:
                    raise ExperimentFailure(f"unexpected business value: {response}")
                return response

            recorder.step("baseline.business-read", read_kept)
            recorder.step(
                "intervention.revoke-postgres",
                lambda: _run([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "revoke-security-group-ingress",
                    "--group-id",
                    state_sg,
                    "--protocol",
                    "tcp",
                    "--port",
                    "5432",
                    "--source-group",
                    app_sg,
                ])
                or "state tcp/5432 rule revoked",
            )
            recorder.step(
                "intervention.readiness-failed",
                lambda: _wait_http(
                    "GET", f"{base_url}/health/ready", HTTPStatus.SERVICE_UNAVAILABLE, budget=180
                ),
            )
            recorder.step(
                "intervention.business-failed",
                lambda: _wait_http(
                    "GET", f"{base_url}/records/evidence", HTTPStatus.BAD_GATEWAY, budget=180
                ),
            )
            recorder.step("restore.allow-postgres", allow_postgres)
            recorder.step("restore.readiness", lambda: _wait_http(
                "GET", f"{base_url}/health/ready", HTTPStatus.OK, budget=180
            ))
            recorder.step("restore.business-read", read_kept)
            recorder.step(
                "persistence.state-vm-reboot",
                lambda: _run([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "reboot-instances",
                    "--instance-ids",
                    state_id,
                ])
                or "state VM reboot requested",
            )
            recorder.step(
                "persistence.readiness-during-reboot",
                lambda: _wait_http(
                    "GET",
                    f"{base_url}/health/ready",
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    budget=180,
                ),
            )
            recorder.step(
                "persistence.state-vm-status-ok",
                lambda: _run([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "wait",
                    "instance-status-ok",
                    "--instance-ids",
                    state_id,
                ], timeout=900)
                or "state VM status ok",
            )
            recorder.step(
                "persistence.readiness-after-reboot",
                lambda: _wait_http(
                    "GET", f"{base_url}/health/ready", HTTPStatus.OK, budget=300
                ),
            )
            recorder.step("persistence.business-read-after-reboot", read_kept)
            outcome = "passed"
        except Exception as exception:
            outcome = "failed"
            error = str(exception)
        finally:
            if instances:
                _run([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "terminate-instances",
                    "--instance-ids",
                    *instances,
                ], check=False)
                _run([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "wait",
                    "instance-terminated",
                    "--instance-ids",
                    *instances,
                ], timeout=900, check=False)
            if volume_id:
                _run([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "wait",
                    "volume-available",
                    "--volume-ids",
                    volume_id,
                ], timeout=600, check=False)
                _run([
                    "aws",
                    "--region",
                    AWS_REGION,
                    "ec2",
                    "delete-volume",
                    "--volume-id",
                    volume_id,
                ], check=False)
            _run([
                "aws",
                "--region",
                AWS_REGION,
                "ec2",
                "delete-key-pair",
                "--key-name",
                key_name,
            ], check=False)
            deadline = time.monotonic() + 180
            while groups and time.monotonic() < deadline:
                remaining = []
                for group in reversed(groups):
                    try:
                        _run([
                            "aws",
                            "--region",
                            AWS_REGION,
                            "ec2",
                            "delete-security-group",
                            "--group-id",
                            group,
                        ])
                    except ExperimentFailure:
                        remaining.append(group)
                groups = remaining
                if groups:
                    time.sleep(10)
            residual: list[str] = []
            for command in (
                [
                    "aws", "--region", AWS_REGION, "ec2", "describe-instances",
                    "--filters", f"Name=tag:easydep-run,Values={prefix}",
                    "Name=instance-state-name,Values=pending,running,stopping,stopped",
                    "--query", "Reservations[].Instances[].InstanceId", "--output", "json",
                ],
                [
                    "aws", "--region", AWS_REGION, "ec2", "describe-volumes",
                    "--filters", f"Name=tag:easydep-run,Values={prefix}",
                    "--query", "Volumes[].VolumeId", "--output", "json",
                ],
                [
                    "aws", "--region", AWS_REGION, "ec2", "describe-security-groups",
                    "--filters", f"Name=tag:easydep-run,Values={prefix}",
                    "--query", "SecurityGroups[].GroupId", "--output", "json",
                ],
            ):
                try:
                    residual.extend(json.loads(_run(command, check=False) or "[]"))
                except json.JSONDecodeError:
                    residual.append("residual-query-failed")
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
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
