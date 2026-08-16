"""Run one domain-neutral AWS ALB HTTPS dependency intervention and cleanup."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from evaluation.dependency_audit.sample_app_managed_tls_common import (
    generate_test_certificate,
    https_probe,
    now,
    startup_oracle,
)

AWS_REGION = "ap-northeast-2"


class Aws:
    def __init__(self, region: str) -> None:
        self.region = region
        self.executable = str(shutil.which("aws.exe") or shutil.which("aws") or "aws")

    def run(self, service: str, *arguments: str, allow_failure: bool = False) -> str:
        completed = subprocess.run(
            [
                self.executable,
                "--region",
                self.region,
                service,
                *arguments,
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            check=False,
        )
        if completed.returncode and not allow_failure:
            raise RuntimeError(
                json.dumps(
                    {
                        "service": service,
                        "operation": arguments[0] if arguments else "",
                        "exitCode": completed.returncode,
                        "stderr": completed.stderr[-2000:],
                    },
                    ensure_ascii=False,
                )
            )
        return completed.stdout

    def json(self, service: str, *arguments: str) -> Any:
        output = self.run(service, *arguments)
        return json.loads(output or "{}")


def _wait_target(client: Aws, target_group: str, instance: str, state: str) -> float:
    started = time.monotonic()
    deadline = started + 600
    while time.monotonic() < deadline:
        payload = client.json(
            "elbv2",
            "describe-target-health",
            "--target-group-arn",
            target_group,
            "--targets",
            f"Id={instance},Port=8080",
        )
        descriptions = payload.get("TargetHealthDescriptions") or []
        current = (
            descriptions[0].get("TargetHealth", {}).get("State")
            if descriptions
            else "unused"
        )
        if current == state or (state == "unused" and not descriptions):
            return round(time.monotonic() - started, 3)
        time.sleep(10)
    raise TimeoutError(f"target did not reach {state}")


def _residual(client: Aws, run_id: str, prefix: str) -> list[str]:
    residual: list[str] = []
    instances = client.json(
        "ec2",
        "describe-instances",
        "--filters",
        f"Name=tag:easydep-run,Values={run_id}",
        "Name=instance-state-name,Values=pending,running,stopping,stopped",
    )
    for reservation in instances.get("Reservations", []):
        residual.extend(item["InstanceId"] for item in reservation.get("Instances", []))
    groups = client.json(
        "ec2", "describe-security-groups", "--filters", f"Name=tag:easydep-run,Values={run_id}"
    )
    residual.extend(item["GroupId"] for item in groups.get("SecurityGroups", []))
    lbs = client.json("elbv2", "describe-load-balancers")
    residual.extend(
        item["LoadBalancerName"]
        for item in lbs.get("LoadBalancers", [])
        if item["LoadBalancerName"].startswith(prefix)
    )
    groups = client.json("elbv2", "describe-target-groups")
    residual.extend(
        item["TargetGroupName"]
        for item in groups.get("TargetGroups", [])
        if item["TargetGroupName"].startswith(prefix)
    )
    certificates = client.json("acm", "list-certificates")
    residual.extend(
        item["DomainName"]
        for item in certificates.get("CertificateSummaryList", [])
        if item["DomainName"].startswith(prefix)
    )
    return sorted(set(residual))


def run_experiment(output: Path, *, region: str = AWS_REGION) -> dict[str, Any]:
    run_id = f"easydep-tls-{uuid.uuid4().hex[:8]}"
    prefix = f"ed-tls-{uuid.uuid4().hex[:8]}"
    client = Aws(region)
    started = time.monotonic()
    result: dict[str, Any] = {
        "schemaVersion": "easydep-domain-neutral-managed-tls/v1",
        "provider": "aws",
        "runId": run_id,
        "prefix": prefix,
        "region": region,
        "startedAt": now(),
        "pathUnderTest": "HTTPS listener -> ALB -> target group health -> App VM port 8080",
        "steps": [],
        "limitations": [
            "The certificate is a one-day self-signed ACM import.",
            "DNS ownership and public CA trust are not measured.",
            "This is one development run and not an availability or SLA measurement.",
        ],
    }
    certificate_arn = listener_arn = load_balancer_arn = target_group_arn = ""
    instance_id = app_sg = alb_sg = ""
    try:
        if _residual(client, run_id, prefix):
            raise RuntimeError("pre-existing experiment resources block execution")
        with tempfile.TemporaryDirectory(prefix="easydep-aws-managed-tls-") as temporary:
            root = Path(temporary)
            material = generate_test_certificate(root, f"{prefix}.invalid")
            imported = client.json(
                "acm",
                "import-certificate",
                "--certificate",
                f"fileb://{material['certificate']}",
                "--private-key",
                f"fileb://{material['privateKey']}",
            )
            certificate_arn = imported["CertificateArn"]
            client.run(
                "acm",
                "add-tags-to-certificate",
                "--certificate-arn",
                certificate_arn,
                "--tags",
                f"Key=easydep-run,Value={run_id}",
            )
            vpc = client.json(
                "ec2", "describe-vpcs", "--filters", "Name=is-default,Values=true"
            )["Vpcs"][0]["VpcId"]
            subnets = client.json(
                "ec2",
                "describe-subnets",
                "--filters",
                "Name=default-for-az,Values=true",
            )["Subnets"]
            selected: list[str] = []
            zones: set[str] = set()
            for subnet in subnets:
                if subnet["AvailabilityZone"] not in zones:
                    selected.append(subnet["SubnetId"])
                    zones.add(subnet["AvailabilityZone"])
                if len(selected) == 2:
                    break
            if len(selected) < 2:
                raise RuntimeError("ALB requires two default subnets in different AZs")
            alb_sg = client.json(
                "ec2",
                "create-security-group",
                "--group-name",
                f"{prefix}-alb",
                "--description",
                "EasyDep managed TLS experiment ALB",
                "--vpc-id",
                vpc,
                "--tag-specifications",
                f"ResourceType=security-group,Tags=[{{Key=easydep-run,Value={run_id}}}]",
            )["GroupId"]
            app_sg = client.json(
                "ec2",
                "create-security-group",
                "--group-name",
                f"{prefix}-app",
                "--description",
                "EasyDep managed TLS experiment app",
                "--vpc-id",
                vpc,
                "--tag-specifications",
                f"ResourceType=security-group,Tags=[{{Key=easydep-run,Value={run_id}}}]",
            )["GroupId"]
            client.run(
                "ec2",
                "authorize-security-group-ingress",
                "--group-id",
                alb_sg,
                "--protocol",
                "tcp",
                "--port",
                "443",
                "--cidr",
                "0.0.0.0/0",
            )
            client.run(
                "ec2",
                "authorize-security-group-ingress",
                "--group-id",
                app_sg,
                "--protocol",
                "tcp",
                "--port",
                "8080",
                "--source-group",
                alb_sg,
            )
            image = client.run(
                "ssm",
                "get-parameter",
                "--name",
                "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
                "--query",
                "Parameter.Value",
                "--output",
                "text",
            ).strip().strip('"')
            script = root / "startup.sh"
            script.write_text(startup_oracle(), encoding="utf-8", newline="\n")
            launched = client.json(
                "ec2",
                "run-instances",
                "--image-id",
                image,
                "--instance-type",
                "t3.micro",
                "--subnet-id",
                selected[0],
                "--security-group-ids",
                app_sg,
                "--user-data",
                f"file://{script}",
                "--tag-specifications",
                f"ResourceType=instance,Tags=[{{Key=Name,Value={prefix}-app}},{{Key=easydep-run,Value={run_id}}}]",
                "--count",
                "1",
            )
            instance_id = launched["Instances"][0]["InstanceId"]
            client.run("ec2", "wait", "instance-status-ok", "--instance-ids", instance_id)
            target_group_arn = client.json(
                "elbv2",
                "create-target-group",
                "--name",
                f"{prefix}-tg",
                "--protocol",
                "HTTP",
                "--port",
                "8080",
                "--vpc-id",
                vpc,
                "--health-check-path",
                "/readyz",
                "--health-check-interval-seconds",
                "10",
                "--healthy-threshold-count",
                "2",
                "--unhealthy-threshold-count",
                "2",
                "--tags",
                f"Key=easydep-run,Value={run_id}",
            )["TargetGroups"][0]["TargetGroupArn"]
            client.run(
                "elbv2",
                "modify-target-group-attributes",
                "--target-group-arn",
                target_group_arn,
                "--attributes",
                "Key=deregistration_delay.timeout_seconds,Value=0",
            )
            client.run(
                "elbv2",
                "register-targets",
                "--target-group-arn",
                target_group_arn,
                "--targets",
                f"Id={instance_id},Port=8080",
            )
            load_balancer = client.json(
                "elbv2",
                "create-load-balancer",
                "--name",
                f"{prefix}-alb",
                "--subnets",
                *selected,
                "--security-groups",
                alb_sg,
                "--scheme",
                "internet-facing",
                "--type",
                "application",
                "--tags",
                f"Key=easydep-run,Value={run_id}",
            )["LoadBalancers"][0]
            load_balancer_arn = load_balancer["LoadBalancerArn"]
            address = load_balancer["DNSName"]
            client.run(
                "elbv2",
                "wait",
                "load-balancer-available",
                "--load-balancer-arns",
                load_balancer_arn,
            )
            listener_arn = client.json(
                "elbv2",
                "create-listener",
                "--load-balancer-arn",
                load_balancer_arn,
                "--protocol",
                "HTTPS",
                "--port",
                "443",
                "--certificates",
                f"CertificateArn={certificate_arn}",
                "--default-actions",
                f"Type=forward,TargetGroupArn={target_group_arn}",
            )["Listeners"][0]["ListenerArn"]
            healthy_seconds = _wait_target(
                client, target_group_arn, instance_id, "healthy"
            )
            baseline = https_probe(address, expect_success=True)
            result["steps"].append(
                {
                    "name": "baseline.managed-https-business-path",
                    "status": "passed" if baseline["matched"] else "failed",
                    "targetHealthySeconds": healthy_seconds,
                    "probe": baseline,
                }
            )
            if not baseline["matched"]:
                raise RuntimeError("AWS managed HTTPS baseline did not stabilize")
            client.run(
                "elbv2",
                "deregister-targets",
                "--target-group-arn",
                target_group_arn,
                "--targets",
                f"Id={instance_id},Port=8080",
            )
            _wait_target(client, target_group_arn, instance_id, "unused")
            removed = https_probe(address, expect_success=False, timeout_seconds=240)
            result["steps"].append(
                {
                    "name": "intervention.backend-membership-removed",
                    "status": "passed" if removed["matched"] else "failed",
                    "probe": removed,
                }
            )
            if not removed["matched"]:
                raise RuntimeError("AWS managed HTTPS remained functional without its backend")
            client.run(
                "elbv2",
                "register-targets",
                "--target-group-arn",
                target_group_arn,
                "--targets",
                f"Id={instance_id},Port=8080",
            )
            _wait_target(client, target_group_arn, instance_id, "healthy")
            restored = https_probe(address, expect_success=True)
            result["steps"].append(
                {
                    "name": "restoration.managed-https-business-path",
                    "status": "passed" if restored["matched"] else "failed",
                    "probe": restored,
                }
            )
            if not restored["matched"]:
                raise RuntimeError("AWS managed HTTPS restoration did not stabilize")
        result["outcome"] = "passed"
    except Exception as exc:
        result["outcome"] = "failed"
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        cleanup: list[dict[str, Any]] = []
        if listener_arn:
            cleanup.append(
                {"resource": "listener", "ok": not bool(client.run("elbv2", "delete-listener", "--listener-arn", listener_arn, allow_failure=True).strip())}
            )
        if load_balancer_arn:
            client.run("elbv2", "delete-load-balancer", "--load-balancer-arn", load_balancer_arn, allow_failure=True)
            client.run("elbv2", "wait", "load-balancers-deleted", "--load-balancer-arns", load_balancer_arn, allow_failure=True)
            cleanup.append({"resource": "loadBalancer", "ok": True})
        if target_group_arn:
            client.run("elbv2", "delete-target-group", "--target-group-arn", target_group_arn, allow_failure=True)
            cleanup.append({"resource": "targetGroup", "ok": True})
        if instance_id:
            client.run("ec2", "terminate-instances", "--instance-ids", instance_id, allow_failure=True)
            client.run("ec2", "wait", "instance-terminated", "--instance-ids", instance_id, allow_failure=True)
            cleanup.append({"resource": "instance", "ok": True})
        for group in (app_sg, alb_sg):
            if group:
                for _ in range(12):
                    deleted = client.run("ec2", "delete-security-group", "--group-id", group, allow_failure=True)
                    if not deleted.strip():
                        break
                    time.sleep(5)
                cleanup.append({"resource": "securityGroup", "ok": True})
        if certificate_arn:
            client.run("acm", "delete-certificate", "--certificate-arn", certificate_arn, allow_failure=True)
            cleanup.append({"resource": "certificate", "ok": True})
        residual = _residual(client, run_id, prefix)
        result["cleanup"] = {
            "passed": not residual,
            "attempts": cleanup,
            "residualResources": residual,
        }
        if residual:
            result["outcome"] = "failed"
        result["finishedAt"] = now()
        result["elapsedSeconds"] = round(time.monotonic() - started, 3)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--region", default=AWS_REGION)
    parser.add_argument("--confirm-region")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/dependency_audit/aws-sample-app-managed-tls-result-20260815.json"),
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z]{2}-[a-z]+-\d", args.region):
        parser.error("invalid AWS region")
    if not args.execute:
        print(json.dumps({"mode": "plan", "region": args.region}, indent=2))
        return 0
    if args.confirm_region != args.region:
        parser.error("--confirm-region must match --region")
    return 0 if run_experiment(args.output, region=args.region)["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
