"""도메인 중립 앱으로 AWS 관리형 App 그룹·ALB·자동 교체 E2 경로를 검증한다."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any

from evaluation.dependency_audit.aws_sample_app_postgres_e1 import (
    _app_build_script,
    _caller_cidr,
    _http,
    _wait_http,
)
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
from evaluation.dependency_audit.sample_app_postgres_e1_common import APP_IMAGE

HEALTH_CHECK_INTERVAL_SECONDS = 10
UNHEALTHY_THRESHOLD_COUNT = 2
ROUTING_PROPAGATION_ALLOWANCE_SECONDS = 10
FUNCTIONAL_RECOVERY_BUDGET_SECONDS = (
    HEALTH_CHECK_INTERVAL_SECONDS * UNHEALTHY_THRESHOLD_COUNT
    + ROUTING_PROPAGATION_ALLOWANCE_SECONDS
)


class AwsE2Recorder(Recorder):
    def __init__(self, run_id: str, output: Path) -> None:
        super().__init__("aws", run_id, output)
        self.document |= {
            "schemaVersion": "easydep-aws-sample-app-postgres-e2/v1",
            "transportUnderTest": (
                "public HTTP ALB to an Auto Scaling group of app VM containers, then "
                "private PostgreSQL"
            ),
            "pathUnderTest": (
                "ALB listener -> target-group health -> Auto Scaling app VM group (2) -> "
                "state VM private IPv4:5432 -> PostgreSQL data volume"
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
                "Public HTTP is used; trusted HTTPS, DNS ownership, and certificates are not tested.",
                "One development run does not establish an AWS-wide success rate or an SLA.",
                "Sequential probes are a functional continuity signal, not a performance load test.",
            ],
        }
        self.save()


def _unique_az_subnets(vpc_id: str) -> list[dict[str, str]]:
    candidates = _json([
        "aws", "--region", AWS_REGION, "ec2", "describe-subnets",
        "--filters", f"Name=vpc-id,Values={vpc_id}",
        "--query", "Subnets[].{id:SubnetId,az:AvailabilityZone}",
    ])
    selected: list[dict[str, str]] = []
    zones: set[str] = set()
    for candidate in candidates:
        if candidate["az"] in zones:
            continue
        selected.append(candidate)
        zones.add(candidate["az"])
        if len(selected) == 2:
            return selected
    raise ExperimentFailure("AWS E2 requires two default subnets in distinct availability zones")


def _wait_target_count(target_group_arn: str, expected: int, *, timeout: int) -> list[str]:
    deadline = time.monotonic() + timeout
    last: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        last = _json([
            "aws", "--region", AWS_REGION, "elbv2", "describe-target-health",
            "--target-group-arn", target_group_arn,
            "--query", "TargetHealthDescriptions[].{id:Target.Id,state:TargetHealth.State}",
        ])
        healthy = [item["id"] for item in last if item["state"] == "healthy"]
        if len(healthy) == expected:
            return healthy
        time.sleep(10)
    raise ExperimentFailure(f"healthy target count {expected} not observed: {last}")


def _asg_instance_ids(asg_name: str) -> list[str]:
    value = _json([
        "aws", "--region", AWS_REGION, "autoscaling", "describe-auto-scaling-groups",
        "--auto-scaling-group-names", asg_name,
        "--query", "AutoScalingGroups[0].Instances[].InstanceId",
    ])
    return sorted(value or [])


def _continuous_business_probe(
    base_url: str,
    stop: threading.Event,
    observations: list[dict[str, Any]],
    started: float,
) -> None:
    while not stop.is_set():
        recorded: dict[str, Any] = {"atSeconds": round(time.monotonic() - started, 3)}
        try:
            response = _http("GET", f"{base_url}/records/evidence")
            recorded["status"] = response["status"]
            recorded["valueKept"] = response.get("body", {}).get("value") == {
                "message": "kept"
            }
        except Exception as exception:  # noqa: BLE001 - the probe records every failure kind.
            recorded |= {"status": None, "valueKept": False, "error": str(exception)}
        observations.append(recorded)
        stop.wait(1)


def _app_user_data(state_private: str) -> str:
    """Cloud-init이 실행할 수 있는 App VM 부팅 스크립트를 만든다."""
    return "#!/bin/bash\n" + _app_build_script(INSTALL_DOCKER_AMAZON) + f"""
sudo docker rm -f easydep-app >/dev/null 2>&1 || true
sudo docker run -d --name easydep-app --restart unless-stopped -p 8080:8080 \
  -e DATABASE_URL='postgresql://postgres:{POSTGRES_PASSWORD}@{state_private}:5432/postgres' \
  {APP_IMAGE} --role postgres-app
"""


def _continuity_summary(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """연속 probe에서 실패 구간과 성공률을 계산한다."""
    failure_windows: list[float] = []
    failure_started: float | None = None
    for observation in observations:
        passed = observation.get("status") == HTTPStatus.OK and observation.get("valueKept")
        at_seconds = float(observation["atSeconds"])
        if not passed and failure_started is None:
            failure_started = at_seconds
        elif passed and failure_started is not None:
            failure_windows.append(at_seconds - failure_started)
            failure_started = None
    if failure_started is not None and observations:
        failure_windows.append(float(observations[-1]["atSeconds"]) - failure_started)
    failed = sum(
        1 for observation in observations
        if observation.get("status") != HTTPStatus.OK or not observation.get("valueKept")
    )
    return {
        "probeCount": len(observations),
        "successCount": len(observations) - failed,
        "failureCount": failed,
        "successRatio": round((len(observations) - failed) / len(observations), 6)
        if observations else 0.0,
        "maxConsecutiveFailureSeconds": round(max(failure_windows, default=0.0), 3),
    }


def run(output: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    prefix = f"easydep-e2-{suffix}"
    recorder = AwsE2Recorder(prefix, output)
    key_name = f"{prefix}-key"
    launch_template = f"{prefix}-lt"
    asg_name = f"{prefix}-asg"
    security_groups: list[str] = []
    state_instance = ""
    state_volume = ""
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
            subnets = recorder.step("network.two-az-subnets", lambda: _unique_az_subnets(vpc_id))

            def create_group(name: str, description: str) -> str:
                group_id = _json([
                    "aws", "--region", AWS_REGION, "ec2", "create-security-group",
                    "--group-name", name, "--description", description, "--vpc-id", vpc_id,
                    "--tag-specifications",
                    f"ResourceType=security-group,Tags=[{{Key=easydep-run,Value={prefix}}}]",
                ])["GroupId"]
                security_groups.append(group_id)
                return group_id

            alb_sg = recorder.step(
                "security.alb.create", lambda: create_group(f"{prefix}-alb", "EasyDep E2 ALB")
            )
            app_sg = recorder.step(
                "security.app.create", lambda: create_group(f"{prefix}-app", "EasyDep E2 app")
            )
            state_sg = recorder.step(
                "security.state.create", lambda: create_group(f"{prefix}-state", "EasyDep E2 state")
            )

            def allow_cidr(group: str, port: str) -> str:
                _run([
                    "aws", "--region", AWS_REGION, "ec2", "authorize-security-group-ingress",
                    "--group-id", group, "--protocol", "tcp", "--port", port,
                    "--cidr", caller_cidr,
                ])
                return f"tcp/{port} from runner /32"

            recorder.step("security.alb-http", lambda: allow_cidr(alb_sg, "80"))
            recorder.step("security.app-ssh", lambda: allow_cidr(app_sg, "22"))
            recorder.step("security.state-ssh", lambda: allow_cidr(state_sg, "22"))

            def allow_group(target: str, source: str, port: str) -> str:
                _run([
                    "aws", "--region", AWS_REGION, "ec2", "authorize-security-group-ingress",
                    "--group-id", target, "--protocol", "tcp", "--port", port,
                    "--source-group", source,
                ])
                return f"tcp/{port} from source security group"

            recorder.step("security.alb-to-app", lambda: allow_group(app_sg, alb_sg, "8080"))
            recorder.step("security.app-to-state", lambda: allow_group(state_sg, app_sg, "5432"))
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

            state_script = temporary_root / "state.sh"
            state_script.write_text(
                "#!/bin/bash\n" + INSTALL_DOCKER_AMAZON + "\ntouch /tmp/easydep-ready\n",
                encoding="utf-8",
            )
            state_instance = recorder.step("state-vm.create", lambda: _json([
                "aws", "--region", AWS_REGION, "ec2", "run-instances",
                "--image-id", ami, "--instance-type", "t3.micro", "--count", "1",
                "--subnet-id", subnets[0]["id"], "--security-group-ids", state_sg,
                "--associate-public-ip-address", "--key-name", key_name,
                "--user-data", f"file://{state_script}", "--tag-specifications",
                "ResourceType=instance,"
                f"Tags=[{{Key=Name,Value={prefix}-state}},{{Key=easydep-run,Value={prefix}}}]",
                "ResourceType=volume,"
                f"Tags=[{{Key=easydep-run,Value={prefix}}}]",
            ], timeout=300)["Instances"][0]["InstanceId"])
            recorder.step("state-vm.running", lambda: _run([
                "aws", "--region", AWS_REGION, "ec2", "wait", "instance-running",
                "--instance-ids", state_instance,
            ], timeout=600) or "state VM running")
            state_description = _json([
                "aws", "--region", AWS_REGION, "ec2", "describe-instances",
                "--instance-ids", state_instance,
                "--query", "Reservations[0].Instances[0].{private:PrivateIpAddress,public:PublicIpAddress}",
            ])
            state_private = state_description["private"]
            state_public = state_description["public"]

            def state_ssh(command: str, *, budget: int = 600, timeout: int = 180) -> str:
                deadline = time.monotonic() + budget
                last_error = ""
                while time.monotonic() < deadline:
                    try:
                        _ssh_aws(state_public, key_path, command, timeout=timeout)
                        return "guest command passed"
                    except (ExperimentFailure, subprocess.TimeoutExpired) as exception:
                        last_error = str(exception)
                        time.sleep(10)
                raise ExperimentFailure(last_error)

            recorder.step("state-vm.tools-ready", lambda: state_ssh(
                "test -f /tmp/easydep-ready && command -v docker >/dev/null"
            ))
            state_volume = recorder.step("state-volume.create", lambda: _json([
                "aws", "--region", AWS_REGION, "ec2", "create-volume",
                "--availability-zone", subnets[0]["az"], "--size", "4", "--volume-type", "gp3",
                "--tag-specifications", "ResourceType=volume,"
                f"Tags=[{{Key=Name,Value={prefix}-data}},{{Key=easydep-run,Value={prefix}}}]",
            ])["VolumeId"])
            recorder.step("state-volume.available", lambda: _run([
                "aws", "--region", AWS_REGION, "ec2", "wait", "volume-available",
                "--volume-ids", state_volume,
            ], timeout=300) or "data volume available")
            recorder.step("state-volume.attach", lambda: _run([
                "aws", "--region", AWS_REGION, "ec2", "attach-volume",
                "--volume-id", state_volume, "--instance-id", state_instance, "--device", "/dev/sdf",
            ]) or "data volume attached")
            recorder.step("state-volume.in-use", lambda: _run([
                "aws", "--region", AWS_REGION, "ec2", "wait", "volume-in-use",
                "--volume-ids", state_volume,
            ], timeout=300) or "attachment complete")
            serial = state_volume.replace("-", "")
            setup_state = f"""
set -eu
for i in $(seq 1 60); do
  link=$(find /dev/disk/by-id -maxdepth 1 -type l -name '*{serial}' -print -quit 2>/dev/null || true)
  [ -n "$link" ] && break
  sleep 2
done
test -n "${{link:-}}"
device=$(readlink -f "$link")
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
  -v /var/lib/easydep-postgres/data:/var/lib/postgresql/data:Z {POSTGRES_IMAGE}
for i in $(seq 1 90); do sudo docker exec easydep-state pg_isready -U postgres && exit 0; sleep 2; done
exit 1
"""
            recorder.step("state-volume.mount-and-postgres", lambda: state_ssh(
                setup_state, budget=420, timeout=300
            ))

            app_script = _app_user_data(state_private)
            launch_data = {
                "ImageId": ami,
                "InstanceType": "t3.small",
                "KeyName": key_name,
                "UserData": base64.b64encode(app_script.encode("utf-8")).decode("ascii"),
                "NetworkInterfaces": [{
                    "DeviceIndex": 0,
                    "AssociatePublicIpAddress": True,
                    "DeleteOnTermination": True,
                    "Groups": [app_sg],
                }],
                "TagSpecifications": [
                    {"ResourceType": "instance", "Tags": [
                        {"Key": "Name", "Value": f"{prefix}-app"},
                        {"Key": "easydep-run", "Value": prefix},
                    ]},
                    {"ResourceType": "volume", "Tags": [
                        {"Key": "easydep-run", "Value": prefix},
                    ]},
                ],
            }
            launch_path = temporary_root / "launch-template.json"
            launch_path.write_text(json.dumps(launch_data), encoding="utf-8")
            recorder.step("app-group.launch-template", lambda: _run([
                "aws", "--region", AWS_REGION, "ec2", "create-launch-template",
                "--launch-template-name", launch_template,
                "--launch-template-data", f"file://{launch_path}",
            ]) and launch_template)
            target_group_arn = recorder.step("load-balancer.target-group", lambda: _json([
                "aws", "--region", AWS_REGION, "elbv2", "create-target-group",
                "--name", f"e2-{suffix}-tg", "--protocol", "HTTP", "--port", "8080",
                "--vpc-id", vpc_id, "--target-type", "instance",
                "--health-check-protocol", "HTTP", "--health-check-path", "/health/ready",
                "--health-check-interval-seconds", str(HEALTH_CHECK_INTERVAL_SECONDS),
                "--healthy-threshold-count", "2",
                "--unhealthy-threshold-count", str(UNHEALTHY_THRESHOLD_COUNT),
                "--tags", f"Key=easydep-run,Value={prefix}",
            ])["TargetGroups"][0]["TargetGroupArn"])
            lb = recorder.step("load-balancer.create", lambda: _json([
                "aws", "--region", AWS_REGION, "elbv2", "create-load-balancer",
                "--name", f"e2-{suffix}-alb", "--type", "application", "--scheme", "internet-facing",
                "--subnets", subnets[0]["id"], subnets[1]["id"],
                "--security-groups", alb_sg, "--tags", f"Key=easydep-run,Value={prefix}",
            ]))
            load_balancer_arn = lb["LoadBalancers"][0]["LoadBalancerArn"]
            load_balancer_dns = lb["LoadBalancers"][0]["DNSName"]
            recorder.step("load-balancer.available", lambda: _run([
                "aws", "--region", AWS_REGION, "elbv2", "wait", "load-balancer-available",
                "--load-balancer-arns", load_balancer_arn,
            ], timeout=600) or "ALB available")
            listener_arn = recorder.step("load-balancer.listener", lambda: _json([
                "aws", "--region", AWS_REGION, "elbv2", "create-listener",
                "--load-balancer-arn", load_balancer_arn, "--protocol", "HTTP", "--port", "80",
                "--default-actions", f"Type=forward,TargetGroupArn={target_group_arn}",
            ])["Listeners"][0]["ListenerArn"])
            recorder.step("app-group.create", lambda: _run([
                "aws", "--region", AWS_REGION, "autoscaling", "create-auto-scaling-group",
                "--auto-scaling-group-name", asg_name,
                "--launch-template", f"LaunchTemplateName={launch_template},Version=$Latest",
                "--min-size", "2", "--max-size", "2", "--desired-capacity", "2",
                "--vpc-zone-identifier", f"{subnets[0]['id']},{subnets[1]['id']}",
                "--target-group-arns", target_group_arn, "--health-check-type", "ELB",
                "--health-check-grace-period", "180", "--tags",
                f"Key=easydep-run,Value={prefix},PropagateAtLaunch=true",
            ]) or "Auto Scaling group desired capacity 2")
            healthy_ids = recorder.step("baseline.two-healthy-targets", lambda: _wait_target_count(
                target_group_arn, 2, timeout=900
            ))
            base_url = f"http://{load_balancer_dns}"
            recorder.step("baseline.readiness", lambda: _wait_http(
                "GET", f"{base_url}/health/ready", HTTPStatus.OK, budget=300
            ))
            recorder.step("baseline.business-write", lambda: _wait_http(
                "PUT", f"{base_url}/records/evidence", HTTPStatus.OK,
                payload={"value": {"message": "kept"}}, budget=300,
            ))
            recorder.step("baseline.business-read", lambda: _wait_http(
                "GET", f"{base_url}/records/evidence", HTTPStatus.OK, budget=120
            ))
            victim = healthy_ids[0]
            victim_public = _aws_value(
                "ec2", "describe-instances", "--instance-ids", victim,
                "--query", "Reservations[0].Instances[0].PublicIpAddress",
            ).strip()

            def health_fault_and_replacement() -> dict[str, Any]:
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
                    _ssh_aws(
                        victim_public,
                        key_path,
                        "sudo docker stop easydep-app >/dev/null",
                        timeout=120,
                    )
                    milestones["appStopped"] = round(time.monotonic() - started, 3)
                    unhealthy_seen = False
                    replacement = ""
                    deadline = time.monotonic() + 900
                    while time.monotonic() < deadline:
                        targets = _json([
                            "aws", "--region", AWS_REGION, "elbv2", "describe-target-health",
                            "--target-group-arn", target_group_arn,
                            "--query", "TargetHealthDescriptions[].{id:Target.Id,state:TargetHealth.State}",
                        ])
                        victim_states = [item["state"] for item in targets if item["id"] == victim]
                        if victim_states and victim_states[0] != "healthy" and not unhealthy_seen:
                            unhealthy_seen = True
                            milestones["victimUnhealthy"] = round(time.monotonic() - started, 3)
                        current_ids = _asg_instance_ids(asg_name)
                        replacements = [item for item in current_ids if item != victim]
                        new_ids = [item for item in replacements if item not in healthy_ids]
                        healthy = [item["id"] for item in targets if item["state"] == "healthy"]
                        if unhealthy_seen and new_ids and len(healthy) == 2 and new_ids[0] in healthy:
                            replacement = new_ids[0]
                            milestones["replacementHealthy"] = round(
                                time.monotonic() - started, 3
                            )
                            break
                        time.sleep(10)
                    if not unhealthy_seen:
                        raise ExperimentFailure("ALB did not mark the stopped app target unhealthy")
                    if not replacement:
                        raise ExperimentFailure("ASG replacement did not return two healthy targets")
                finally:
                    stop.set()
                    thread.join(timeout=20)
                continuity = _continuity_summary(observations)
                if len(observations) < 5:
                    raise ExperimentFailure(f"too few continuity probes: {len(observations)}")
                unhealthy_at = milestones["victimUnhealthy"]
                replacement_at = milestones["replacementHealthy"]
                successes_during_replacement = sum(
                    1 for item in observations
                    if unhealthy_at <= float(item["atSeconds"]) <= replacement_at
                    and item.get("status") == HTTPStatus.OK
                    and item.get("valueKept")
                )
                if not successes_during_replacement:
                    raise ExperimentFailure(
                        f"no successful business request while replacement was in progress: {continuity}"
                    )
                if (
                    continuity["maxConsecutiveFailureSeconds"]
                    > FUNCTIONAL_RECOVERY_BUDGET_SECONDS
                ):
                    raise ExperimentFailure(
                        "functional recovery exceeded the health-derived budget: "
                        f"budget={FUNCTIONAL_RECOVERY_BUDGET_SECONDS}, observed={continuity}"
                    )
                return {
                    "victimInstance": victim,
                    "replacementInstance": replacement,
                    "milestonesSeconds": milestones,
                    "healthDerivedRecoveryBudgetSeconds": FUNCTIONAL_RECOVERY_BUDGET_SECONDS,
                    "businessContinuity": continuity,
                    "successesDuringReplacement": successes_during_replacement,
                }

            recorder.step("fault.health-based-managed-replacement", health_fault_and_replacement)
            recorder.step("recovery.business-read", lambda: _wait_http(
                "GET", f"{base_url}/records/evidence", HTTPStatus.OK, budget=120
            ))
            recorder.document["availabilityObservation"] = {
                "scope": "application tier",
                "stateTier": "single state VM; not highly available",
                "failureInjection": "stop one app container and let ALB health feed ASG replacement",
            }
            recorder.save()
            outcome = "passed"
        except Exception as exception:
            outcome = "failed"
            error = str(exception)
        finally:
            if asg_name:
                _run([
                    "aws", "--region", AWS_REGION, "autoscaling", "update-auto-scaling-group",
                    "--auto-scaling-group-name", asg_name, "--min-size", "0",
                    "--desired-capacity", "0",
                ], check=False)
                _run([
                    "aws", "--region", AWS_REGION, "autoscaling", "delete-auto-scaling-group",
                    "--auto-scaling-group-name", asg_name, "--force-delete",
                ], timeout=600, check=False)
                deadline = time.monotonic() + 300
                while time.monotonic() < deadline:
                    remaining_asg = _run([
                        "aws", "--region", AWS_REGION, "autoscaling",
                        "describe-auto-scaling-groups", "--auto-scaling-group-names", asg_name,
                        "--query", "length(AutoScalingGroups)", "--output", "text",
                    ], check=False).strip()
                    if remaining_asg == "0":
                        break
                    time.sleep(10)
            if listener_arn:
                _run([
                    "aws", "--region", AWS_REGION, "elbv2", "delete-listener",
                    "--listener-arn", listener_arn,
                ], check=False)
            if load_balancer_arn:
                _run([
                    "aws", "--region", AWS_REGION, "elbv2", "delete-load-balancer",
                    "--load-balancer-arn", load_balancer_arn,
                ], check=False)
                _run([
                    "aws", "--region", AWS_REGION, "elbv2", "wait", "load-balancers-deleted",
                    "--load-balancer-arns", load_balancer_arn,
                ], timeout=900, check=False)
            if target_group_arn:
                _run([
                    "aws", "--region", AWS_REGION, "elbv2", "delete-target-group",
                    "--target-group-arn", target_group_arn,
                ], check=False)
            _run([
                "aws", "--region", AWS_REGION, "ec2", "delete-launch-template",
                "--launch-template-name", launch_template,
            ], check=False)
            if state_instance:
                _run([
                    "aws", "--region", AWS_REGION, "ec2", "terminate-instances",
                    "--instance-ids", state_instance,
                ], check=False)
                _run([
                    "aws", "--region", AWS_REGION, "ec2", "wait", "instance-terminated",
                    "--instance-ids", state_instance,
                ], timeout=900, check=False)
            tagged_instances_raw = _run([
                "aws", "--region", AWS_REGION, "ec2", "describe-instances", "--filters",
                f"Name=tag:easydep-run,Values={prefix}",
                "Name=instance-state-name,Values=pending,running,stopping,stopped",
                "--query", "Reservations[].Instances[].InstanceId", "--output", "json",
            ], check=False)
            try:
                tagged_instances = json.loads(tagged_instances_raw or "[]")
            except json.JSONDecodeError:
                tagged_instances = []
            if tagged_instances:
                _run([
                    "aws", "--region", AWS_REGION, "ec2", "terminate-instances",
                    "--instance-ids", *tagged_instances,
                ], check=False)
                _run([
                    "aws", "--region", AWS_REGION, "ec2", "wait", "instance-terminated",
                    "--instance-ids", *tagged_instances,
                ], timeout=900, check=False)
            if state_volume:
                _run([
                    "aws", "--region", AWS_REGION, "ec2", "wait", "volume-available",
                    "--volume-ids", state_volume,
                ], timeout=600, check=False)
                _run([
                    "aws", "--region", AWS_REGION, "ec2", "delete-volume",
                    "--volume-id", state_volume,
                ], check=False)
            _run([
                "aws", "--region", AWS_REGION, "ec2", "delete-key-pair", "--key-name", key_name,
            ], check=False)
            deadline = time.monotonic() + 300
            while security_groups and time.monotonic() < deadline:
                remaining: list[str] = []
                for group_id in reversed(security_groups):
                    try:
                        _run([
                            "aws", "--region", AWS_REGION, "ec2", "delete-security-group",
                            "--group-id", group_id,
                        ])
                    except ExperimentFailure:
                        remaining.append(group_id)
                security_groups = remaining
                if security_groups:
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
            )
            residual: list[str] = []
            for command in residual_commands:
                try:
                    residual.extend(json.loads(_run(command, check=False) or "[]"))
                except json.JSONDecodeError:
                    residual.append("residual-query-failed")
            for command, label in (
                (["aws", "--region", AWS_REGION, "autoscaling", "describe-auto-scaling-groups",
                  "--auto-scaling-group-names", asg_name,
                  "--query", "AutoScalingGroups[].AutoScalingGroupName", "--output", "json"], "asg"),
                (["aws", "--region", AWS_REGION, "ec2", "describe-launch-templates",
                  "--launch-template-names", launch_template,
                  "--query", "LaunchTemplates[].LaunchTemplateName", "--output", "json"], "launch-template"),
                (["aws", "--region", AWS_REGION, "elbv2", "describe-load-balancers",
                  "--names", f"e2-{suffix}-alb", "--query", "LoadBalancers[].LoadBalancerName",
                  "--output", "json"], "load-balancer"),
            ):
                raw = _run(command, check=False)
                try:
                    residual.extend(json.loads(raw or "[]"))
                except json.JSONDecodeError:
                    if raw and "not found" not in raw.lower():
                        residual.append(f"{label}-query-failed")
            cleanup = {"passed": not residual, "residual": residual}
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
