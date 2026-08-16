"""Re-run retained DepKB experiments with provider-level cleanup guards.

The legacy experiment programs predate the current safety contract.  This
wrapper snapshots AWS resources, gives every Azure experiment a disposable
resource group, and removes GCP resources with the reserved ``depkb`` prefix.
It writes a run summary and refuses to report success when residuals remain.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPERIMENTS = ROOT / "experiments"
PYTHON = sys.executable
CAPTURE_RESULTS = False

AWS_EXPERIMENTS = [
    ("aws-apply-2026-07-30", []),
    ("aws-apply2-2026-07-31", []),
    ("aws-func-2026-07-31", []),
    ("aws-func2-2026-07-31", []),
    ("aws-sig4-2026-07-31", ["build", "meta", "egress", "finish"]),
]
AZURE_EXPERIMENTS = [
    ("azure-preflight-2026-07-30", []),
    ("azure-apply-2026-07-30", []),
    ("azure-apply2-2026-07-30", []),
    ("azure-apply3-2026-07-30", []),
    ("azure-func-2026-07-31", []),
    ("azure-func2-2026-07-31", []),
    ("azure-lb-serve2-2026-08-01", ["build", "serve", "finish"]),
    ("azure-disj2-2026-08-01", ["__hardcoded_group__"]),
]
GCP_EXPERIMENTS = [
    ("gcp-apply-2026-07-31", ["{project}", "{region}", "{zone}"]),
    ("gcp-apply2-2026-07-31", ["{project}", "{region}", "{zone}"]),
    ("gcp-apply3-2026-07-31", ["{project}", "{region}"]),
    ("gcp-apply4-2026-07-31", ["{project}", "{region}"]),
    ("gcp-func-2026-07-31", ["{project}", "{zone}"]),
    ("gcp-func2-2026-07-31", ["{project}", "{region}", "{zone}"]),
    ("gcp-iam-2026-07-31", ["{project}", "{zone}"]),
    ("gcp-paircompat-2026-07-31", ["{project}", "{region}", "{zone}", "{zone_b}"]),
]


def _run(command: list[str], *, cwd: Path | None = None, timeout: int = 1800,
         check: bool = True) -> subprocess.CompletedProcess[str]:
    executable = (
        shutil.which("gcloud.cmd") if command[0] == "gcloud"
        else shutil.which(command[0])
    ) or command[0]
    command = [str(executable), *command[1:]]
    print("+", " ".join(command), flush=True)
    result = subprocess.run(command, cwd=cwd, text=True, timeout=timeout, check=False)
    if check and result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {' '.join(command)}")
    return result


def _json(command: list[str]) -> object:
    result = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return json.loads(result.stdout or "null")


def _preserve_result(name: str, original: bytes | None) -> None:
    """Archive this replication and restore the reviewed historical evidence."""
    if not CAPTURE_RESULTS:
        return
    result = EXPERIMENTS / name / "results.json"
    if result.is_file():
        day = datetime.now(UTC).date().isoformat()
        destination = ROOT / "replications" / day / f"{name}.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            stamp = datetime.now(UTC).strftime("%H%M%S-%f")
            destination = destination.with_name(f"{name}-{stamp}.json")
        shutil.copy2(result, destination)
    if original is None:
        result.unlink(missing_ok=True)
    else:
        result.write_bytes(original)


def _aws_snapshot(region: str) -> dict[str, set[str]]:
    aws = ["aws", "--region", region]
    queries = {
        "instances": [*aws, "ec2", "describe-instances", "--query", "Reservations[].Instances[?State.Name!='terminated'].InstanceId[]", "--output", "json"],
        "volumes": [*aws, "ec2", "describe-volumes", "--query", "Volumes[].VolumeId", "--output", "json"],
        "enis": [*aws, "ec2", "describe-network-interfaces", "--query", "NetworkInterfaces[].NetworkInterfaceId", "--output", "json"],
        "eips": [*aws, "ec2", "describe-addresses", "--query", "Addresses[].AllocationId", "--output", "json"],
        "lbs": [*aws, "elbv2", "describe-load-balancers", "--query", "LoadBalancers[].LoadBalancerArn", "--output", "json"],
        "subnets": [*aws, "ec2", "describe-subnets", "--query", "Subnets[].SubnetId", "--output", "json"],
        "security_groups": [*aws, "ec2", "describe-security-groups", "--query", "SecurityGroups[].GroupId", "--output", "json"],
        "igws": [*aws, "ec2", "describe-internet-gateways", "--query", "InternetGateways[].InternetGatewayId", "--output", "json"],
        "vpcs": [*aws, "ec2", "describe-vpcs", "--query", "Vpcs[].VpcId", "--output", "json"],
        "keypairs": [*aws, "ec2", "describe-key-pairs", "--query", "KeyPairs[].KeyName", "--output", "json"],
        "roles": ["aws", "iam", "list-roles", "--query", "Roles[].RoleName", "--output", "json"],
        "profiles": ["aws", "iam", "list-instance-profiles", "--query", "InstanceProfiles[].InstanceProfileName", "--output", "json"],
    }
    return {name: set(_json(command) or []) for name, command in queries.items()}


def _aws_cleanup(before: dict[str, set[str]], region: str) -> dict[str, list[str]]:
    aws = ["aws", "--region", region]
    after = _aws_snapshot(region)
    new = {name: sorted(after[name] - before[name]) for name in before}
    for arn in new["lbs"]:
        _run([*aws, "elbv2", "delete-load-balancer", "--load-balancer-arn", arn], check=False)
    for instance in new["instances"]:
        _run([*aws, "ec2", "terminate-instances", "--instance-ids", instance], check=False)
    if new["instances"]:
        _run([*aws, "ec2", "wait", "instance-terminated", "--instance-ids", *new["instances"]], timeout=900, check=False)
    time.sleep(15 if new["lbs"] else 0)
    for allocation in new["eips"]:
        _run([*aws, "ec2", "release-address", "--allocation-id", allocation], check=False)
    for eni in new["enis"]:
        _run([*aws, "ec2", "delete-network-interface", "--network-interface-id", eni], check=False)
    for volume in new["volumes"]:
        _run([*aws, "ec2", "delete-volume", "--volume-id", volume], check=False)
    for key in new["keypairs"]:
        _run([*aws, "ec2", "delete-key-pair", "--key-name", key], check=False)
    for profile in new["profiles"]:
        detail = _json(["aws", "iam", "get-instance-profile", "--instance-profile-name", profile, "--output", "json"])
        for role in detail.get("InstanceProfile", {}).get("Roles", []):
            _run(["aws", "iam", "remove-role-from-instance-profile", "--instance-profile-name", profile, "--role-name", role["RoleName"]], check=False)
        _run(["aws", "iam", "delete-instance-profile", "--instance-profile-name", profile], check=False)
    for role in new["roles"]:
        attached = _json(["aws", "iam", "list-attached-role-policies", "--role-name", role, "--output", "json"])
        for policy in attached.get("AttachedPolicies", []):
            _run(["aws", "iam", "detach-role-policy", "--role-name", role, "--policy-arn", policy["PolicyArn"]], check=False)
        _run(["aws", "iam", "delete-role", "--role-name", role], check=False)
    for subnet in new["subnets"]:
        _run([*aws, "ec2", "delete-subnet", "--subnet-id", subnet], check=False)
    for group in new["security_groups"]:
        _run([*aws, "ec2", "delete-security-group", "--group-id", group], check=False)
    for igw in new["igws"]:
        data = _json([*aws, "ec2", "describe-internet-gateways", "--internet-gateway-ids", igw, "--output", "json"])
        for attachment in data.get("InternetGateways", [{}])[0].get("Attachments", []):
            _run([*aws, "ec2", "detach-internet-gateway", "--internet-gateway-id", igw, "--vpc-id", attachment["VpcId"]], check=False)
        _run([*aws, "ec2", "delete-internet-gateway", "--internet-gateway-id", igw], check=False)
    for vpc in new["vpcs"]:
        _run([*aws, "ec2", "delete-vpc", "--vpc-id", vpc], check=False)
    residual = _aws_snapshot(region)
    return {name: sorted(residual[name] - before[name]) for name in before if residual[name] - before[name]}


def run_aws(region: str, only_experiment: str | None = None) -> dict:
    if only_experiment and only_experiment not in {name for name, _ in AWS_EXPERIMENTS}:
        raise ValueError(f"unknown AWS experiment: {only_experiment}")
    before = _aws_snapshot(region)
    failures: list[str] = []
    try:
        for name, phases in AWS_EXPERIMENTS:
            if only_experiment and name != only_experiment:
                continue
            result_path = EXPERIMENTS / name / "results.json"
            original = result_path.read_bytes() if result_path.is_file() else None
            try:
                if CAPTURE_RESULTS:
                    result_path.unlink(missing_ok=True)
                commands = phases or [None]
                for phase in commands:
                    args = [] if phase is None else [phase]
                    _run([PYTHON, "run.py", *args], cwd=EXPERIMENTS / name)
                if name == "aws-apply2-2026-07-31":
                    for fixup in ("run_fix.py", "run_fix2.py"):
                        _run([PYTHON, fixup], cwd=EXPERIMENTS / name)
            except Exception as exc:  # cleanup and evidence restoration still run
                failures.append(f"{name}: {exc}")
            finally:
                _preserve_result(name, original)
    finally:
        residual = _aws_cleanup(before, region)
    return {"failures": failures, "residual": residual}


def run_azure(location: str, only_experiment: str | None = None) -> dict:
    if only_experiment and only_experiment not in {name for name, _ in AZURE_EXPERIMENTS}:
        raise ValueError(f"unknown Azure experiment: {only_experiment}")
    failures: list[str] = []
    residual: list[str] = []
    stamp = datetime.now(UTC).strftime("%m%d%H%M")
    for index, (name, phases) in enumerate(AZURE_EXPERIMENTS):
        if only_experiment and name != only_experiment:
            continue
        hardcoded = phases == ["__hardcoded_group__"]
        group = "depkb-disj2" if hardcoded else f"depkb-rm-{stamp}-{index}"
        result_path = EXPERIMENTS / name / "results.json"
        original = result_path.read_bytes() if result_path.is_file() else None
        try:
            if CAPTURE_RESULTS:
                result_path.unlink(missing_ok=True)
            _run(["az", "group", "create", "-n", group, "-l", location, "-o", "none"])
            selected_phases = phases or [None]
            for phase in selected_phases:
                args = [] if hardcoded else ([group] if phase is None else [phase, group])
                _run([PYTHON, "run.py", *args], cwd=EXPERIMENTS / name)
        except Exception as exc:
            failures.append(f"{name}: {exc}")
        finally:
            _preserve_result(name, original)
            _run(["az", "group", "delete", "-n", group, "--yes", "--no-wait"], check=False)
            _run(["az", "group", "wait", "-n", group, "--deleted"], timeout=900, check=False)
            exists = subprocess.run(
                [str(shutil.which("az") or "az"), "group", "exists", "-n", group],
                capture_output=True, text=True, check=False,
            ).stdout.strip()
            if exists == "true":
                residual.append(group)
    return {"failures": failures, "residual": residual}


def _gcloud_resources(kind: str, project: str, *, subnets: bool = False) -> list[dict]:
    group = ["networks", "subnets"] if subnets else [kind]
    result = subprocess.run(
        [str(shutil.which("gcloud.cmd") or "gcloud"), "compute", *group, "list",
         "--project", project, "--filter=name~'^depkb'", "--format=json(name,zone,region)"],
        capture_output=True, text=True, timeout=180, check=False,
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return list(json.loads(result.stdout or "[]"))


GCP_RESOURCE_KINDS = (
    "instances", "forwarding-rules", "backend-services", "health-checks",
    "firewall-rules", "routes", "disks", "networks", "subnets",
)


def _gcp_snapshot(project: str) -> dict[str, dict[str, dict]]:
    snapshot: dict[str, dict[str, dict]] = {}
    for kind in GCP_RESOURCE_KINDS:
        items = _gcloud_resources(kind, project, subnets=kind == "subnets")
        snapshot[kind] = {str(item["name"]): item for item in items}
    return snapshot


def _gcp_cleanup(
    project: str, before: dict[str, dict[str, dict]]
) -> dict[str, list[str]]:
    """실행 전 snapshot에 없던 ``depkb`` 리소스만 정리한다."""

    after = _gcp_snapshot(project)
    created = {
        kind: [item for name, item in after[kind].items() if name not in before[kind]]
        for kind in GCP_RESOURCE_KINDS
    }
    for kind in GCP_RESOURCE_KINDS[:-2]:
        for item in created[kind]:
            command = ["gcloud", "compute", kind, "delete", item["name"], "--project", project, "--quiet"]
            if item.get("zone"):
                command.extend(["--zone", str(item["zone"]).rsplit("/", 1)[-1]])
            elif item.get("region"):
                command.extend(["--region", str(item["region"]).rsplit("/", 1)[-1]])
            elif kind in {"forwarding-rules", "backend-services"}:
                command.append("--global")
            _run(command, timeout=900, check=False)
    for item in created["subnets"]:
        command = ["gcloud", "compute", "networks", "subnets", "delete", item["name"],
                   "--project", project, "--quiet"]
        if item.get("region"):
            command.extend(["--region", str(item["region"]).rsplit("/", 1)[-1]])
        _run(command, timeout=900, check=False)
    for item in created["networks"]:
        _run(["gcloud", "compute", "networks", "delete", item["name"],
              "--project", project, "--quiet"], check=False)
    final = _gcp_snapshot(project)
    return {
        kind: sorted(name for name in final[kind] if name not in before[kind])
        for kind in GCP_RESOURCE_KINDS
        if any(name not in before[kind] for name in final[kind])
    }


def run_gcp(
    project: str,
    region: str,
    zone: str,
    zone_b: str,
    only_experiment: str | None = None,
) -> dict:
    if only_experiment and only_experiment not in {name for name, _ in GCP_EXPERIMENTS}:
        raise ValueError(f"unknown GCP experiment: {only_experiment}")
    before = _gcp_snapshot(project)
    preexisting = {kind: sorted(items) for kind, items in before.items() if items}
    if preexisting:
        return {
            "failures": [
                "pre-existing depkb-prefixed resources block a legacy fixed-name experiment"
            ],
            "residual": {},
            "preexisting": preexisting,
        }
    failures: list[str] = []
    try:
        for name, template in GCP_EXPERIMENTS:
            if only_experiment and name != only_experiment:
                continue
            values = {"project": project, "region": region, "zone": zone, "zone_b": zone_b}
            args = [part.format(**values) for part in template]
            result_path = EXPERIMENTS / name / "results.json"
            original = result_path.read_bytes() if result_path.is_file() else None
            try:
                if CAPTURE_RESULTS:
                    result_path.unlink(missing_ok=True)
                _run([PYTHON, "run.py", *args], cwd=EXPERIMENTS / name)
            except Exception as exc:
                failures.append(f"{name}: {exc}")
            finally:
                _preserve_result(name, original)
    finally:
        residual = _gcp_cleanup(project, before)
    return {"failures": failures, "residual": residual}


def main() -> None:
    global CAPTURE_RESULTS
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=("aws", "azure", "gcp", "all"))
    parser.add_argument("--aws-region", default="ap-northeast-2")
    parser.add_argument("--azure-location", default="koreacentral")
    parser.add_argument("--gcp-project", default="cloud-resource-testing")
    parser.add_argument("--gcp-region", default="asia-northeast3")
    parser.add_argument("--gcp-zone", default="asia-northeast3-a")
    parser.add_argument("--gcp-zone-b", default="asia-northeast3-b")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--experiment", help="run one named experiment within the selected provider")
    args = parser.parse_args()
    if args.dry_run:
        selected = {"aws", "azure", "gcp"} if args.provider == "all" else {args.provider}
        def chosen(items: list[tuple[str, list[str]]]) -> list[tuple[str, list[str]]]:
            return [item for item in items if not args.experiment or item[0] == args.experiment]

        plan = {
            "providers": sorted(selected),
            "aws": {
                "region": args.aws_region,
                "experiments": chosen(AWS_EXPERIMENTS),
                "guard": "delete only resource IDs absent from the pre-run snapshot",
            } if "aws" in selected else None,
            "azure": {
                "location": args.azure_location,
                "experiments": chosen(AZURE_EXPERIMENTS),
                "guard": "one disposable depkb resource group per experiment; delete and wait",
            } if "azure" in selected else None,
            "gcp": {
                "project": args.gcp_project, "region": args.gcp_region,
                "zone": args.gcp_zone, "zoneB": args.gcp_zone_b,
                "experiments": chosen(GCP_EXPERIMENTS),
                "guard": "block on pre-existing depkb prefix; delete only post-snapshot IDs",
            } if "gcp" in selected else None,
        }
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    CAPTURE_RESULTS = True
    started = datetime.now(UTC).isoformat(timespec="seconds")
    report: dict[str, object] = {"startedAt": started}
    if args.provider in {"aws", "all"}:
        report["aws"] = run_aws(args.aws_region, args.experiment)
    if args.provider in {"azure", "all"}:
        report["azure"] = run_azure(args.azure_location, args.experiment)
    if args.provider in {"gcp", "all"}:
        report["gcp"] = run_gcp(
            args.gcp_project, args.gcp_region, args.gcp_zone, args.gcp_zone_b,
            args.experiment,
        )
    report["finishedAt"] = datetime.now(UTC).isoformat(timespec="seconds")
    if args.experiment:
        stamp = datetime.now(UTC).strftime("%Y-%m-%d/%H%M%S-%f")
        output = ROOT / "replications" / f"{stamp}-{args.provider}-{args.experiment}.report.json"
        output.parent.mkdir(parents=True, exist_ok=True)
    else:
        output = ROOT / "replication-report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
    if any(value.get("failures") or value.get("residual") for value in report.values() if isinstance(value, dict)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
