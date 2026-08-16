"""Run the domain-neutral state-VM replacement experiment on AWS."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from evaluation.dependency_audit.aws_sample_app_postgres_e1 import _caller_cidr
from evaluation.dependency_audit.inter_vm_postgres_intervention import (
    AWS_REGION,
    INSTALL_DOCKER_AMAZON,
    ExperimentFailure,
    _aws_value,
    _json,
    _restrict_private_key,
    _run,
    _ssh_aws,
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


def _app_bootstrap_script() -> str:
    """Keep EC2 user data below its 16 KiB limit; build through the guest channel."""
    return INSTALL_DOCKER_AMAZON + "\ntouch /tmp/easydep-tools-ready"


def _ssh_retry(
    ip: str,
    key: Path,
    command: str,
    *,
    budget: int = 600,
    command_timeout: int = 600,
) -> str:
    deadline = time.monotonic() + budget
    last_error = ""
    while time.monotonic() < deadline:
        try:
            _ssh_aws(ip, key, command, timeout=command_timeout)
            return "guest command passed"
        except ExperimentFailure as exception:
            last_error = str(exception)
            time.sleep(10)
    raise ExperimentFailure(last_error or "guest command did not become ready")


def _wait_volume(volume_id: str, state: str, timeout: int = 600) -> str:
    _run([
        "aws", "--region", AWS_REGION, "ec2", "wait", f"volume-{state}",
        "--volume-ids", volume_id,
    ], timeout=timeout)
    return f"volume {state}"


def run(output: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    prefix = f"easydep-e3-{suffix}"
    recorder = E3Recorder("aws", prefix, output)
    instances: list[str] = []
    groups: list[str] = []
    volume_id = ""
    key_name = f"{prefix}-key"
    error: str | None = None
    cleanup: dict[str, Any] = {"passed": False, "residual": []}
    with tempfile.TemporaryDirectory(dir=output.parent) as temporary_name:
        temporary = Path(temporary_name)
        key_path = temporary / "aws.pem"
        try:
            vpc_id = recorder.step("network.default-vpc", lambda: _aws_value(
                "ec2", "describe-vpcs", "--filters", "Name=isDefault,Values=true",
                "--query", "Vpcs[0].VpcId",
            ).strip())
            subnet_id = recorder.step("network.subnet", lambda: _aws_value(
                "ec2", "describe-subnets", "--filters", f"Name=vpc-id,Values={vpc_id}",
                "--query", "Subnets[0].SubnetId",
            ).strip())
            ami = recorder.step("image.resolve", lambda: _aws_value(
                "ssm", "get-parameter", "--name",
                "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
                "--query", "Parameter.Value",
            ).strip())

            def create_group(role: str) -> str:
                value = _json([
                    "aws", "--region", AWS_REGION, "ec2", "create-security-group",
                    "--group-name", f"{prefix}-{role}",
                    "--description", f"EasyDep E3 {role}", "--vpc-id", vpc_id,
                    "--tag-specifications",
                    f"ResourceType=security-group,Tags=[{{Key=easydep-run,Value={prefix}}}]",
                ])
                return str(value["GroupId"])

            app_sg = recorder.step("app-sg.create", lambda: create_group("app"))
            state_sg = recorder.step("state-sg.create", lambda: create_group("state"))
            groups.extend((app_sg, state_sg))
            caller_cidr = _caller_cidr()

            def allow_ssh(group: str) -> str:
                _run([
                    "aws", "--region", AWS_REGION, "ec2",
                    "authorize-security-group-ingress", "--group-id", group,
                    "--protocol", "tcp", "--port", "22", "--cidr", caller_cidr,
                ])
                return "tcp/22 from the runner /32"

            for group in groups:
                recorder.step(f"{group}.allow-ssh", lambda group=group: allow_ssh(group))
            recorder.step("state-sg.allow-postgres", lambda: _run([
                "aws", "--region", AWS_REGION, "ec2", "authorize-security-group-ingress",
                "--group-id", state_sg, "--protocol", "tcp", "--port", "5432",
                "--source-group", app_sg,
            ]) or "state tcp/5432 from app security group")
            material = recorder.step("key.create", lambda: _aws_value(
                "ec2", "create-key-pair", "--key-name", key_name, "--key-type", "ed25519",
                "--query", "KeyMaterial",
            ))
            key_path.write_text(material + "\n", encoding="utf-8")
            _restrict_private_key(key_path)

            def launch(name: str, security_group: str, user_data: str) -> str:
                script_path = temporary / f"{name}.sh"
                script_path.write_text("#!/bin/bash\n" + user_data + "\n", encoding="utf-8")
                value = _json([
                    "aws", "--region", AWS_REGION, "ec2", "run-instances",
                    "--image-id", ami, "--instance-type", "t3.micro", "--count", "1",
                    "--subnet-id", subnet_id, "--security-group-ids", security_group,
                    "--associate-public-ip-address", "--key-name", key_name,
                    "--user-data", f"file://{script_path}", "--tag-specifications",
                    f"ResourceType=instance,Tags=[{{Key=Name,Value={name}}},"
                    f"{{Key=easydep-run,Value={prefix}}}]",
                    f"ResourceType=volume,Tags=[{{Key=easydep-run,Value={prefix}}}]",
                ], timeout=300)
                instance_id = str(value["Instances"][0]["InstanceId"])
                instances.append(instance_id)
                return instance_id

            state_bootstrap = INSTALL_DOCKER_AMAZON + "\ntouch /tmp/easydep-tools-ready"
            state_a_id = recorder.step(
                "initial.state-vm.create",
                lambda: launch(f"{prefix}-state-a", state_sg, state_bootstrap),
            )
            app_id = recorder.step(
                "app-vm.create",
                lambda: launch(
                    f"{prefix}-app",
                    app_sg,
                    _app_bootstrap_script(),
                ),
            )
            recorder.step("initial.vms-running", lambda: _run([
                "aws", "--region", AWS_REGION, "ec2", "wait", "instance-running",
                "--instance-ids", state_a_id, app_id,
            ], timeout=600) or "initial VMs running")

            def describe(*instance_ids: str) -> dict[str, Any]:
                value = _json([
                    "aws", "--region", AWS_REGION, "ec2", "describe-instances",
                    "--instance-ids", *instance_ids,
                ])
                return {
                    item["InstanceId"]: item
                    for reservation in value["Reservations"]
                    for item in reservation["Instances"]
                }

            initial = describe(state_a_id, app_id)
            state_a_public = initial[state_a_id]["PublicIpAddress"]
            state_a_ip = initial[state_a_id]["PrivateIpAddress"]
            state_zone = initial[state_a_id]["Placement"]["AvailabilityZone"]
            app_public = initial[app_id]["PublicIpAddress"]
            recorder.step("initial.state-tools-ready", lambda: _ssh_retry(
                state_a_public, key_path,
                "test -f /tmp/easydep-tools-ready && command -v docker >/dev/null",
            ))
            recorder.step("app-tools.ready", lambda: _ssh_retry(
                app_public, key_path,
                "test -f /tmp/easydep-tools-ready && command -v docker >/dev/null",
            ))
            recorder.step("app-image.build", lambda: _ssh_retry(
                app_public, key_path, app_build_script(""), command_timeout=900,
            ))
            volume_id = recorder.step("state-volume.create", lambda: str(_json([
                "aws", "--region", AWS_REGION, "ec2", "create-volume",
                "--availability-zone", state_zone, "--size", "4", "--volume-type", "gp3",
                "--tag-specifications",
                f"ResourceType=volume,Tags=[{{Key=easydep-run,Value={prefix}}}]",
            ])["VolumeId"]))
            recorder.step(
                "state-volume.available", lambda: _wait_volume(volume_id, "available")
            )
            recorder.step("initial.state-volume.attach", lambda: _run([
                "aws", "--region", AWS_REGION, "ec2", "attach-volume",
                "--volume-id", volume_id, "--instance-id", state_a_id,
                "--device", "/dev/sdf",
            ]) or "data volume attached to state-a")
            recorder.step("initial.state-volume.in-use", lambda: _wait_volume(volume_id, "in-use"))
            serial = volume_id.replace("-", "")
            device = f"""
device=
for i in $(seq 1 60); do
  link=$(find /dev/disk/by-id -maxdepth 1 -type l -name '*{serial}*' -print -quit 2>/dev/null || true)
  if [ -n "$link" ]; then device=$(readlink -f "$link"); break; fi
  sleep 2
done
""".strip()
            state_setup = state_setup_script("", device)
            recorder.step("initial.state-ready", lambda: _ssh_retry(
                state_a_public, key_path, state_setup
            ))
            recorder.step("app-container.start", lambda: _ssh_retry(
                app_public, key_path, app_start_script(state_a_ip)
            ))
            recorder.step("baseline.app-write-read", lambda: _ssh_retry(
                app_public, key_path, baseline_script()
            ))
            recorder.step("initial.state-vm.terminate", lambda: _run([
                "aws", "--region", AWS_REGION, "ec2", "terminate-instances",
                "--instance-ids", state_a_id,
            ]) or "state-a termination requested")
            recorder.step("initial.state-vm.terminated", lambda: _run([
                "aws", "--region", AWS_REGION, "ec2", "wait", "instance-terminated",
                "--instance-ids", state_a_id,
            ], timeout=900) or "state-a terminated")
            recorder.step(
                "replacement.state-volume.available",
                lambda: _wait_volume(volume_id, "available", timeout=900),
            )
            state_b_id = recorder.step(
                "replacement.state-vm.create",
                lambda: launch(f"{prefix}-state-b", state_sg, state_bootstrap),
            )
            recorder.step("replacement.state-vm.running", lambda: _run([
                "aws", "--region", AWS_REGION, "ec2", "wait", "instance-running",
                "--instance-ids", state_b_id,
            ], timeout=600) or "state-b running")
            replacement = describe(state_b_id)[state_b_id]
            state_b_public = replacement["PublicIpAddress"]
            state_b_ip = replacement["PrivateIpAddress"]
            recorder.step("replacement.state-tools-ready", lambda: _ssh_retry(
                state_b_public, key_path,
                "test -f /tmp/easydep-tools-ready && command -v docker >/dev/null",
            ))
            recorder.step("replacement.state-volume.attach-existing", lambda: _run([
                "aws", "--region", AWS_REGION, "ec2", "attach-volume",
                "--volume-id", volume_id, "--instance-id", state_b_id,
                "--device", "/dev/sdf",
            ]) or "existing data volume attached to state-b")
            recorder.step(
                "replacement.state-volume.in-use", lambda: _wait_volume(volume_id, "in-use")
            )
            recorder.step("replacement.state-ready", lambda: _ssh_retry(
                state_b_public, key_path, state_setup
            ))
            recorder.document["runtimeEndpointObservation"] = {
                "initialStatePrivateIp": state_a_ip,
                "replacementStatePrivateIp": state_b_ip,
                "privateIpChanged": state_a_ip != state_b_ip,
                "appVmRecreated": False,
                "appImageRebuilt": False,
            }
            recorder.save()
            recorder.step("replacement.app-rebind-without-rebuild", lambda: _ssh_retry(
                app_public, key_path, app_rebind_script(state_b_ip)
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
            if instances:
                _run([
                    "aws", "--region", AWS_REGION, "ec2", "terminate-instances",
                    "--instance-ids", *instances,
                ], check=False)
                _run([
                    "aws", "--region", AWS_REGION, "ec2", "wait", "instance-terminated",
                    "--instance-ids", *instances,
                ], timeout=900, check=False)
            if volume_id:
                _wait_volume(volume_id, "available", timeout=900)
                _run([
                    "aws", "--region", AWS_REGION, "ec2", "delete-volume",
                    "--volume-id", volume_id,
                ], check=False)
            _run([
                "aws", "--region", AWS_REGION, "ec2", "delete-key-pair",
                "--key-name", key_name,
            ], check=False)
            deadline = time.monotonic() + 300
            while groups and time.monotonic() < deadline:
                remaining: list[str] = []
                for group in reversed(groups):
                    try:
                        _run([
                            "aws", "--region", AWS_REGION, "ec2", "delete-security-group",
                            "--group-id", group,
                        ])
                    except ExperimentFailure:
                        remaining.append(group)
                groups = remaining
                if groups:
                    time.sleep(10)
            residual_commands = (
                ["aws", "--region", AWS_REGION, "ec2", "describe-instances", "--filters",
                 f"Name=tag:easydep-run,Values={prefix}",
                 "Name=instance-state-name,Values=pending,running,stopping,stopped",
                 "--query", "Reservations[].Instances[].InstanceId", "--output", "json"],
                ["aws", "--region", AWS_REGION, "ec2", "describe-volumes", "--filters",
                 f"Name=tag:easydep-run,Values={prefix}",
                 "--query", "Volumes[].VolumeId", "--output", "json"],
                ["aws", "--region", AWS_REGION, "ec2", "describe-security-groups", "--filters",
                 f"Name=tag:easydep-run,Values={prefix}",
                 "--query", "SecurityGroups[].GroupId", "--output", "json"],
                ["aws", "--region", AWS_REGION, "ec2", "describe-key-pairs", "--filters",
                 f"Name=key-name,Values={key_name}",
                 "--query", "KeyPairs[].KeyName", "--output", "json"],
            )
            residual = [
                item
                for command in residual_commands
                for item in json.loads(_run(command, check=False) or "[]")
            ]
            cleanup = {"passed": not residual, "residual": residual}
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
