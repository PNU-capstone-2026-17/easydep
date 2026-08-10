"""Measure AWS public NAT gateway composition/lifecycle without creating a VM."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ERROR_CODE = re.compile(r"\(([A-Za-z0-9._-]+)\) when calling")
SECRET_PATTERNS = (
    (re.compile(r"(?i)(aws_access_key_id\s*[=:]\s*)\S+"), r"\1<redacted>"),
    (re.compile(r"(?i)(aws_secret_access_key\s*[=:]\s*)\S+"), r"\1<redacted>"),
    (re.compile(r"(?i)(aws_session_token\s*[=:]\s*)\S+"), r"\1<redacted>"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "<redacted-access-key>"),
)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sanitize(value: str) -> str:
    value = value.replace("\r", "")
    for pattern, replacement in SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


class Experiment:
    def __init__(self, aws_cli: str, region: str, output: Path, poll: int, wait: int):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.prefix = f"depkb-nat-{stamp}-{secrets.token_hex(3)}"
        self.aws_cli = aws_cli
        self.region = region
        self.output = output
        self.poll = poll
        self.wait = wait
        self.started = now()
        self.steps: list[dict[str, Any]] = []
        self.ids: dict[str, str] = {}

    def call(self, name: str, args: list[str], timeout: int = 180) -> dict[str, Any]:
        started = now()
        command = [self.aws_cli, "--region", self.region, *args, "--no-cli-pager"]
        try:
            proc = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout, check=False
            )
            stdout, stderr = sanitize(proc.stdout or ""), sanitize(proc.stderr or "")
            codes = list(dict.fromkeys(ERROR_CODE.findall(stderr + "\n" + stdout)))
            data = None
            if stdout.lstrip().startswith(("{", "[", '"')):
                try:
                    data = json.loads(stdout)
                except json.JSONDecodeError:
                    pass
            result = {
                "name": name,
                "startedAt": started,
                "finishedAt": now(),
                "command": ["aws", "--region", self.region, *args, "--no-cli-pager"],
                "returnCode": proc.returncode,
                "ok": proc.returncode == 0,
                "errorCodes": codes,
                "stdout": stdout,
                "stderr": stderr,
                "data": data,
            }
        except (subprocess.TimeoutExpired, OSError) as exc:
            result = {
                "name": name, "startedAt": started, "finishedAt": now(),
                "command": ["aws", "--region", self.region, *args, "--no-cli-pager"],
                "returnCode": None, "ok": False,
                "errorCodes": [type(exc).__name__], "stdout": "",
                "stderr": sanitize(str(exc)), "data": None,
            }
        self.steps.append(result)
        print(f"{name:38} {'OK' if result['ok'] else '/'.join(result['errorCodes']) or 'FAIL'}")
        return result

    @staticmethod
    def get(result: dict[str, Any], *path: Any) -> Any:
        value = result.get("data")
        for key in path:
            if value is None:
                return None
            value = value[key] if isinstance(key, int) else value.get(key)
        return value

    def tag_spec(self, resource_type: str) -> str:
        return f"ResourceType={resource_type},Tags=[{{Key=Name,Value={self.prefix}}},{{Key=depkb-experiment,Value={self.prefix}}}]"

    def wait_nat(self, target: str, phase: str) -> None:
        nat_id = self.ids.get("natGatewayId")
        if not nat_id:
            return
        deadline = time.monotonic() + self.wait
        while time.monotonic() < deadline:
            result = self.call(
                f"{phase}.describe-nat",
                ["ec2", "describe-nat-gateways", "--nat-gateway-ids", nat_id, "--output", "json"],
            )
            state = self.get(result, "NatGateways", 0, "State")
            if state == target or (target == "deleted" and state in {"deleted", None}):
                return
            if target == "available" and state in {"failed", "deleted"}:
                return
            time.sleep(self.poll)
        self.steps.append({"name": f"{phase}.wait-timeout", "startedAt": now(),
                           "finishedAt": now(), "ok": False,
                           "errorCodes": ["WaitTimeout"], "targetState": target})

    def cleanup(self) -> None:
        nat_id = self.ids.get("natGatewayId")
        if nat_id:
            self.call("cleanup.delete-nat", ["ec2", "delete-nat-gateway", "--nat-gateway-id", nat_id])
            self.wait_nat("deleted", "cleanup")
        association = self.ids.get("routeTableAssociationId")
        if association:
            self.call("cleanup.disassociate-route-table", ["ec2", "disassociate-route-table", "--association-id", association])
        route_table = self.ids.get("routeTableId")
        if route_table:
            self.call("cleanup.delete-route-table", ["ec2", "delete-route-table", "--route-table-id", route_table])
        subnet = self.ids.get("subnetId")
        if subnet:
            self.call("cleanup.delete-subnet", ["ec2", "delete-subnet", "--subnet-id", subnet])
        igw, vpc = self.ids.get("internetGatewayId"), self.ids.get("vpcId")
        if igw and vpc:
            self.call("cleanup.detach-igw", ["ec2", "detach-internet-gateway", "--internet-gateway-id", igw, "--vpc-id", vpc])
        if igw:
            self.call("cleanup.delete-igw", ["ec2", "delete-internet-gateway", "--internet-gateway-id", igw])
        allocation = self.ids.get("allocationId")
        if allocation:
            self.call("cleanup.release-eip", ["ec2", "release-address", "--allocation-id", allocation])
        if vpc:
            self.call("cleanup.delete-vpc", ["ec2", "delete-vpc", "--vpc-id", vpc])

    def residuals(self) -> None:
        tag_filter = f"Name=tag:depkb-experiment,Values={self.prefix}"
        self.call("residual.describe-vpcs", ["ec2", "describe-vpcs", "--filters", tag_filter, "--output", "json"])
        self.call("residual.describe-subnets", ["ec2", "describe-subnets", "--filters", tag_filter, "--output", "json"])
        self.call("residual.describe-route-tables", ["ec2", "describe-route-tables", "--filters", tag_filter, "--output", "json"])
        self.call("residual.describe-igws", ["ec2", "describe-internet-gateways", "--filters", tag_filter, "--output", "json"])
        self.call("residual.describe-addresses", ["ec2", "describe-addresses", "--filters", tag_filter, "--output", "json"])
        self.call("residual.describe-nats", ["ec2", "describe-nat-gateways", "--filter", tag_filter, "--output", "json"])

    def run(self) -> None:
        try:
            zones = self.call("observe.availability-zones", ["ec2", "describe-availability-zones", "--filters", "Name=state,Values=available", "--output", "json"])
            zone = self.get(zones, "AvailabilityZones", 0, "ZoneName")
            if not zone:
                raise RuntimeError("No available availability zone returned")

            vpc = self.call("create.vpc", ["ec2", "create-vpc", "--cidr-block", "10.97.0.0/16", "--tag-specifications", self.tag_spec("vpc"), "--output", "json"])
            self.ids["vpcId"] = self.get(vpc, "Vpc", "VpcId")
            if not self.ids["vpcId"]:
                raise RuntimeError("VPC creation did not return VpcId")

            subnet = self.call("create.subnet", ["ec2", "create-subnet", "--vpc-id", self.ids["vpcId"], "--cidr-block", "10.97.1.0/24", "--availability-zone", zone, "--tag-specifications", self.tag_spec("subnet"), "--output", "json"])
            self.ids["subnetId"] = self.get(subnet, "Subnet", "SubnetId")

            igw = self.call("create.igw", ["ec2", "create-internet-gateway", "--tag-specifications", self.tag_spec("internet-gateway"), "--output", "json"])
            self.ids["internetGatewayId"] = self.get(igw, "InternetGateway", "InternetGatewayId")
            self.call("compose.attach-igw", ["ec2", "attach-internet-gateway", "--internet-gateway-id", self.ids["internetGatewayId"], "--vpc-id", self.ids["vpcId"]])

            rt = self.call("create.route-table", ["ec2", "create-route-table", "--vpc-id", self.ids["vpcId"], "--tag-specifications", self.tag_spec("route-table"), "--output", "json"])
            self.ids["routeTableId"] = self.get(rt, "RouteTable", "RouteTableId")
            assoc = self.call("compose.associate-route-table", ["ec2", "associate-route-table", "--route-table-id", self.ids["routeTableId"], "--subnet-id", self.ids["subnetId"], "--output", "json"])
            self.ids["routeTableAssociationId"] = self.get(assoc, "AssociationId")
            self.call("compose.default-route", ["ec2", "create-route", "--route-table-id", self.ids["routeTableId"], "--destination-cidr-block", "0.0.0.0/0", "--gateway-id", self.ids["internetGatewayId"], "--output", "json"])

            eip = self.call("create.eip", ["ec2", "allocate-address", "--domain", "vpc", "--tag-specifications", self.tag_spec("elastic-ip"), "--output", "json"])
            self.ids["allocationId"] = self.get(eip, "AllocationId")
            nat = self.call("create.nat", ["ec2", "create-nat-gateway", "--subnet-id", self.ids["subnetId"], "--allocation-id", self.ids["allocationId"], "--connectivity-type", "public", "--tag-specifications", self.tag_spec("natgateway"), "--output", "json"])
            self.ids["natGatewayId"] = self.get(nat, "NatGateway", "NatGatewayId")
            self.wait_nat("available", "observe")

            latest = next((s for s in reversed(self.steps) if s["name"] == "observe.describe-nat"), {})
            if self.get(latest, "NatGateways", 0, "State") == "available":
                self.call("counterfactual.release-associated-eip", ["ec2", "release-address", "--allocation-id", self.ids["allocationId"]])
                self.call("counterfactual.delete-containing-subnet", ["ec2", "delete-subnet", "--subnet-id", self.ids["subnetId"]])
        except Exception as exc:  # preserve evidence and always attempt cleanup
            self.steps.append({"name": "experiment.exception", "startedAt": now(),
                               "finishedAt": now(), "ok": False,
                               "errorCodes": [type(exc).__name__],
                               "stderr": sanitize(str(exc))})
        finally:
            self.cleanup()
            self.residuals()
            document = {
                "schemaVersion": 1,
                "experiment": "aws-neutral-nat-2026-08-07",
                "startedAt": self.started,
                "finishedAt": now(),
                "region": self.region,
                "prefix": self.prefix,
                "resourceIds": self.ids,
                "steps": self.steps,
            }
            serialized = json.dumps(document, indent=2, ensure_ascii=False)
            serialized = serialized.replace(self.prefix, "<RUN>")
            for kind, resource_id in self.ids.items():
                if resource_id:
                    serialized = serialized.replace(resource_id, f"<{kind}>")
            serialized = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<IP>", serialized)
            serialized = re.sub(r'(?<!\d)\d{12}(?!\d)', "<ACCOUNT>", serialized)
            serialized = re.sub(
                r"\b(?:ami|eni|dopt|acl|rtb|igw|nat|eipalloc|subnet|vpc|"
                r"vpc-cidr-assoc|rtbassoc)-[0-9a-f]+\b",
                "<AWS_RESOURCE_ID>",
                serialized,
            )
            self.output.write_text(serialized, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "ap-northeast-2"))
    parser.add_argument("--output", type=Path, default=HERE / "results.json")
    parser.add_argument("--poll-seconds", type=int, default=15)
    parser.add_argument("--wait-seconds", type=int, default=1200)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("refusing cloud mutations without --execute")
    if args.output.exists():
        parser.error(f"refusing to overwrite existing result: {args.output}")
    aws_cli = shutil.which("aws") or r"C:\Program Files\Amazon\AWSCLIV2\aws.exe"
    if not Path(aws_cli).exists():
        parser.error("AWS CLI not found")
    Experiment(aws_cli, args.region, args.output, max(1, args.poll_seconds), max(60, args.wait_seconds)).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
