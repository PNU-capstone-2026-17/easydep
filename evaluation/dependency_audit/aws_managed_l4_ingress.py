"""AWS Network Load Balancer의 L4 백엔드 의존성을 중립 앱으로 실측한다."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from evaluation.dependency_audit.aws_sample_app_postgres_e1 import _caller_cidr
from evaluation.dependency_audit.aws_sample_app_postgres_e2 import (
    _unique_az_subnets,
    _wait_target_count,
)
from evaluation.dependency_audit.inter_vm_postgres_intervention import (
    AWS_REGION,
    ExperimentFailure,
    _aws_value,
    _json,
    _restrict_private_key,
    _run,
    _ssh_aws,
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
    prefix = f"easydep-l4-{suffix}"
    recorder = ManagedL4Recorder("aws", prefix, output)
    fault_token = uuid.uuid4().hex
    key_name = f"{prefix}-key"
    lb_name = f"l4-{suffix}"
    target_name = f"l4-{suffix}-tg"
    groups: list[str] = []
    instances: dict[str, dict[str, str]] = {}
    load_balancer_arn = ""
    listener_arn = ""
    target_group_arn = ""
    error: str | None = None
    cleanup: dict[str, Any] = {"passed": False, "residual": []}

    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        temporary_root = Path(temporary)
        key_path = temporary_root / "aws.pem"
        try:
            caller_cidr = _caller_cidr()
            recorder.step("runner.public-ip", lambda: "runner IPv4 /32 resolved")
            vpc_id = recorder.step("network.default-vpc", lambda: _aws_value(
                "ec2", "describe-vpcs", "--filters", "Name=isDefault,Values=true",
                "--query", "Vpcs[0].VpcId",
            ).strip())
            subnets = recorder.step(
                "network.two-az-subnets", lambda: _unique_az_subnets(vpc_id)
            )

            def create_group(name: str, description: str) -> str:
                group_id = _json([
                    "aws", "--region", AWS_REGION, "ec2", "create-security-group",
                    "--group-name", name, "--description", description, "--vpc-id", vpc_id,
                    "--tag-specifications",
                    f"ResourceType=security-group,Tags=[{{Key=easydep-run,Value={prefix}}}]",
                ])["GroupId"]
                groups.append(group_id)
                return group_id

            lb_group = recorder.step(
                "security.lb.create",
                lambda: create_group(f"{prefix}-lb", "EasyDep managed L4 experiment LB"),
            )
            app_group = recorder.step(
                "security.app.create",
                lambda: create_group(f"{prefix}-app", "EasyDep managed L4 experiment app"),
            )
            def authorize(group: str, port: str, source_flag: str, source: str) -> str:
                _run([
                    "aws", "--region", AWS_REGION, "ec2", "authorize-security-group-ingress",
                    "--group-id", group, "--protocol", "tcp", "--port", port,
                    source_flag, source,
                ])
                return f"tcp/{port} ingress rule created"

            recorder.step(
                "security.client-to-lb",
                lambda: authorize(lb_group, "80", "--cidr", caller_cidr),
            )
            recorder.step(
                "security.lb-to-app",
                lambda: authorize(app_group, "8080", "--source-group", lb_group),
            )
            recorder.step(
                "security.runner-ssh",
                lambda: authorize(app_group, "22", "--cidr", caller_cidr),
            )
            material = recorder.step("key.create", lambda: _aws_value(
                "ec2", "create-key-pair", "--key-name", key_name, "--key-type", "ed25519",
                "--query", "KeyMaterial",
            ))
            key_path.write_text(material + "\n", encoding="utf-8")
            _restrict_private_key(key_path)
            ami = recorder.step("image.resolve", lambda: _aws_value(
                "ssm", "get-parameter", "--name",
                "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
                "--query", "Parameter.Value",
            ).strip())

            for index, subnet in enumerate(subnets, start=1):
                name = f"app-{index}"
                script = temporary_root / f"{name}.sh"
                script.write_text(
                    "#!/bin/bash\nset -eu\nhostnamectl set-hostname " + name + "\n" +
                    startup_script(port=8080, fault_token=fault_token).removeprefix("#!/bin/bash\n"),
                    encoding="utf-8",
                )
                instance_id = recorder.step(f"{name}.create", lambda subnet=subnet, script=script, name=name: _json([
                    "aws", "--region", AWS_REGION, "ec2", "run-instances",
                    "--image-id", ami, "--instance-type", "t3.micro", "--count", "1",
                    "--subnet-id", subnet["id"], "--security-group-ids", app_group,
                    "--associate-public-ip-address", "--key-name", key_name,
                    "--user-data", f"file://{script}", "--tag-specifications",
                    "ResourceType=instance,"
                    f"Tags=[{{Key=Name,Value={prefix}-{name}}},{{Key=easydep-run,Value={prefix}}}]",
                    "ResourceType=volume,"
                    f"Tags=[{{Key=easydep-run,Value={prefix}}}]",
                ])["Instances"][0]["InstanceId"])
                instances[name] = {"id": instance_id, "publicIp": ""}
            recorder.step("backends.running", lambda: _run([
                "aws", "--region", AWS_REGION, "ec2", "wait", "instance-status-ok",
                "--instance-ids", *[item["id"] for item in instances.values()],
            ], timeout=900) or "two EC2 instances passed status checks")
            for name, instance in instances.items():
                instance["publicIp"] = _aws_value(
                    "ec2", "describe-instances", "--instance-ids", instance["id"],
                    "--query", "Reservations[0].Instances[0].PublicIpAddress",
                ).strip()

            target_group_arn = recorder.step("load-balancer.target-group", lambda: _json([
                "aws", "--region", AWS_REGION, "elbv2", "create-target-group",
                "--name", target_name, "--protocol", "TCP", "--port", "8080",
                "--vpc-id", vpc_id, "--target-type", "instance",
                "--health-check-protocol", "HTTP", "--health-check-port", "8080",
                "--health-check-path", "/health/ready", "--health-check-interval-seconds", "10",
                "--healthy-threshold-count", "2", "--unhealthy-threshold-count", "2",
                "--tags", f"Key=easydep-run,Value={prefix}",
            ])["TargetGroups"][0]["TargetGroupArn"])
            recorder.step("load-balancer.register-backends", lambda: _run([
                "aws", "--region", AWS_REGION, "elbv2", "register-targets",
                "--target-group-arn", target_group_arn, "--targets",
                *[f"Id={item['id']},Port=8080" for item in instances.values()],
            ]) or "two instance targets")
            load_balancer = recorder.step("load-balancer.create", lambda: _json([
                "aws", "--region", AWS_REGION, "elbv2", "create-load-balancer",
                "--name", lb_name, "--type", "network", "--scheme", "internet-facing",
                "--subnets", *[item["id"] for item in subnets],
                "--security-groups", lb_group,
                "--tags", f"Key=easydep-run,Value={prefix}",
            ]))["LoadBalancers"][0]
            load_balancer_arn = load_balancer["LoadBalancerArn"]
            load_balancer_dns = load_balancer["DNSName"]
            recorder.step("load-balancer.available", lambda: _run([
                "aws", "--region", AWS_REGION, "elbv2", "wait", "load-balancer-available",
                "--load-balancer-arns", load_balancer_arn,
            ], timeout=600) or "NLB available")
            listener_arn = recorder.step("load-balancer.listener", lambda: _json([
                "aws", "--region", AWS_REGION, "elbv2", "create-listener",
                "--load-balancer-arn", load_balancer_arn, "--protocol", "TCP", "--port", "80",
                "--default-actions", f"Type=forward,TargetGroupArn={target_group_arn}",
            ])["Listeners"][0]["ListenerArn"])
            recorder.step("baseline.two-healthy-targets", lambda: _wait_target_count(
                target_group_arn, 2, timeout=900
            ))
            base_url = f"http://{load_balancer_dns}"
            recorder.step("baseline.readiness", lambda: wait_http(
                "GET", f"{base_url}/health/ready", 200, budget=300
            ))
            expected = set(instances)
            recorder.step("baseline.two-backends", lambda: wait_for_instances(
                base_url, expected, timeout=300
            ))

            def restore(victim: str) -> str:
                if victim not in instances:
                    raise ExperimentFailure(f"unknown AWS backend {victim!r}")
                deadline = time.monotonic() + 300
                last_error = ""
                while time.monotonic() < deadline:
                    try:
                        _ssh_aws(
                            instances[victim]["publicIp"], key_path,
                            "sudo /usr/local/bin/easydep-l4-start", timeout=120,
                        )
                        return f"SSH restarted the app on {victim}"
                    except (ExperimentFailure, subprocess.TimeoutExpired) as exception:
                        last_error = str(exception)
                        time.sleep(10)
                raise ExperimentFailure(last_error)

            recorder.step("fault.exclude-and-restore", lambda: exercise_fault_exclusion_and_restore(
                base_url, expected, fault_token, restore
            ))
            recorder.document["resourceObservation"] = {
                "frontend": "AWS Network Load Balancer TCP/80",
                "health": "target group HTTP /health/ready on tcp/8080",
                "backends": "two EC2 instances in distinct default-VPC availability zones",
            }
            recorder.save()
            outcome = "passed"
        except Exception as exception:  # noqa: BLE001 - cleanup must run for every failure.
            outcome = "failed"
            error = str(exception)
        finally:
            if listener_arn:
                _run(["aws", "--region", AWS_REGION, "elbv2", "delete-listener", "--listener-arn", listener_arn], check=False)
            if load_balancer_arn:
                _run(["aws", "--region", AWS_REGION, "elbv2", "delete-load-balancer", "--load-balancer-arn", load_balancer_arn], check=False)
                _run(["aws", "--region", AWS_REGION, "elbv2", "wait", "load-balancers-deleted", "--load-balancer-arns", load_balancer_arn], timeout=900, check=False)
            if target_group_arn:
                _run(["aws", "--region", AWS_REGION, "elbv2", "delete-target-group", "--target-group-arn", target_group_arn], check=False)
            instance_ids = [item["id"] for item in instances.values()]
            if instance_ids:
                _run(["aws", "--region", AWS_REGION, "ec2", "terminate-instances", "--instance-ids", *instance_ids], check=False)
                _run(["aws", "--region", AWS_REGION, "ec2", "wait", "instance-terminated", "--instance-ids", *instance_ids], timeout=900, check=False)
            _run(["aws", "--region", AWS_REGION, "ec2", "delete-key-pair", "--key-name", key_name], check=False)
            deadline = time.monotonic() + 300
            while groups and time.monotonic() < deadline:
                remaining: list[str] = []
                for group_id in reversed(groups):
                    try:
                        _run(["aws", "--region", AWS_REGION, "ec2", "delete-security-group", "--group-id", group_id])
                    except ExperimentFailure:
                        remaining.append(group_id)
                groups = remaining
                if groups:
                    time.sleep(10)
            residual: list[str] = []
            for command in (
                ["aws", "--region", AWS_REGION, "ec2", "describe-instances", "--filters", f"Name=tag:easydep-run,Values={prefix}", "Name=instance-state-name,Values=pending,running,stopping,stopped", "--query", "Reservations[].Instances[].InstanceId", "--output", "json"],
                ["aws", "--region", AWS_REGION, "ec2", "describe-security-groups", "--filters", f"Name=tag:easydep-run,Values={prefix}", "--query", "SecurityGroups[].GroupId", "--output", "json"],
            ):
                try:
                    residual.extend(json.loads(_run(command, check=False) or "[]"))
                except json.JSONDecodeError:
                    residual.append("residual-query-failed")
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
