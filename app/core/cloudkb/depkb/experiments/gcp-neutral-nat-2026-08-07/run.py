"""Execute the preregistered GCP Cloud Router + Cloud NAT control-plane experiment."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results.json"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def classify(text: str) -> list[str]:
    patterns = {
        "AUTH": r"(?i)(unauthenticated|login required|invalid credentials)",
        "PERMISSION_DENIED": r"(?i)(permission denied|forbidden|not authorized)",
        "NOT_FOUND": r"(?i)(not found|was not found)",
        "ALREADY_EXISTS": r"(?i)(already exists|alreadyExists)",
        "DEPENDENCY": r"(?i)(in use|being used|resourceInUse|dependent|nat)",
        "QUOTA": r"(?i)(quota|rate limit|resource exhausted)",
        "API_DISABLED": r"(?i)(api.*not.*enabled|service.*disabled)",
        "POLICY": r"(?i)(organization policy|constraint|policy violation)",
        "INVALID_ARGUMENT": r"(?i)(invalid argument|invalid value|bad request)",
    }
    return [code for code, pattern in patterns.items() if re.search(pattern, text)]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project")
    parser.add_argument("--region", default="asia-northeast3")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        parser.error("refusing cloud mutations without --execute")
    if RESULTS.exists():
        parser.error(f"refusing to overwrite existing result: {RESULTS}")
    gcloud_cli = shutil.which("gcloud")
    if not gcloud_cli:
        parser.error("gcloud is not installed or not on PATH")

    project = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        probe = subprocess.run(
            [gcloud_cli, "config", "get-value", "project"], capture_output=True,
            text=True, timeout=30, check=False,
        )
        project = probe.stdout.strip()
    if not project or project == "(unset)":
        parser.error("provide --project, GOOGLE_CLOUD_PROJECT, or an active gcloud project")

    suffix = datetime.now(UTC).strftime("%m%d%H%M%S") + "-" + secrets.token_hex(2)
    prefix = f"ed-nat-{suffix}"
    names = {kind: f"{prefix}-{tail}" for kind, tail in {
        "network": "net", "subnet": "sub", "router": "rtr",
        "address": "ip", "nat": "nat",
    }.items()}
    records: list[dict[str, Any]] = []
    created: set[str] = set()
    experiment_failed = False

    def sanitize(value: str) -> str:
        value = value.replace(project, "<PROJECT>").replace(prefix, "<RUN>")
        value = re.sub(r"(?i)[\w.+-]+@[\w.-]+", "<ACCOUNT>", value)
        value = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<IP>", value)
        value = re.sub(r'("id"\s*:\s*")\d+', r"\1<ID>", value)
        value = re.sub(r'("(?:fingerprint|labelFingerprint)"\s*:\s*")[^"]+', r"\1<REDACTED>", value)
        value = re.sub(r"(?i)(access[_ -]?token|authorization):?\s*\S+", r"\1:<REDACTED>", value)
        return value[:4000]

    def run(label: str, argv: list[str], *, timeout: int = 240) -> dict[str, Any]:
        started = now()
        try:
            proc = subprocess.run(
                [gcloud_cli, *argv, "--project", project, "--quiet"],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
            combined = "\n".join(x for x in (proc.stdout.strip(), proc.stderr.strip()) if x)
            item = {"step": label, "startedAt": started, "finishedAt": now(),
                    "exitCode": proc.returncode, "ok": proc.returncode == 0,
                    "errorCodes": [] if proc.returncode == 0 else classify(combined),
                    "excerpt": sanitize(combined)}
        except subprocess.TimeoutExpired as exc:
            combined = "\n".join(str(x) for x in (exc.stdout, exc.stderr) if x)
            item = {"step": label, "startedAt": started, "finishedAt": now(),
                    "exitCode": 124, "ok": False, "errorCodes": ["TIMEOUT"],
                    "excerpt": sanitize(combined)}
        records.append(item)
        print(f"{label:34} {'OK' if item['ok'] else 'FAIL'}")
        return item

    def describe(kind: str, argv: list[str]) -> None:
        run(f"describe-{kind}", [*argv, "--format=json"])

    cleanup_started = False
    try:
        preflight = run("preflight-auth", ["auth", "list", "--filter=status:ACTIVE",
                                             "--format=value(account)"])
        if not preflight["ok"] or not preflight["excerpt"].strip():
            raise RuntimeError("no active gcloud account")
        if run("create-network", ["compute", "networks", "create", names["network"],
                                   "--subnet-mode=custom"])["ok"]:
            created.add("network")
        else:
            raise RuntimeError("network creation failed")
        if run("create-subnet", ["compute", "networks", "subnets", "create", names["subnet"],
                                  f"--network={names['network']}", "--range=10.237.0.0/28",
                                  f"--region={args.region}"])["ok"]:
            created.add("subnet")
        else:
            raise RuntimeError("subnet creation failed")
        if run("create-router", ["compute", "routers", "create", names["router"],
                                  f"--network={names['network']}", f"--region={args.region}"])["ok"]:
            created.add("router")
        else:
            raise RuntimeError("router creation failed")
        if run("create-address", ["compute", "addresses", "create", names["address"],
                                   f"--region={args.region}"])["ok"]:
            created.add("address")
        else:
            raise RuntimeError("address creation failed")
        if run("create-nat", ["compute", "routers", "nats", "create", names["nat"],
                               f"--router={names['router']}", f"--region={args.region}",
                               f"--nat-custom-subnet-ip-ranges={names['subnet']}",
                               f"--nat-external-ip-pool={names['address']}"])["ok"]:
            created.add("nat")
        else:
            raise RuntimeError("NAT creation failed")

        describe("network", ["compute", "networks", "describe", names["network"]])
        describe("subnet", ["compute", "networks", "subnets", "describe", names["subnet"],
                            f"--region={args.region}"])
        describe("router", ["compute", "routers", "describe", names["router"],
                            f"--region={args.region}"])
        describe("address", ["compute", "addresses", "describe", names["address"],
                             f"--region={args.region}"])
        describe("nat", ["compute", "routers", "nats", "describe", names["nat"],
                         f"--router={names['router']}", f"--region={args.region}"])

        counter = run("counterfactual-delete-router-with-nat",
                      ["compute", "routers", "delete", names["router"], f"--region={args.region}"])
        if counter["ok"]:
            created.discard("router")
            created.discard("nat")
        else:
            describe("router-after-counterfactual", ["compute", "routers", "describe",
                     names["router"], f"--region={args.region}"])
            describe("nat-after-counterfactual", ["compute", "routers", "nats", "describe",
                     names["nat"], f"--router={names['router']}", f"--region={args.region}"])
    except RuntimeError as exc:
        experiment_failed = True
        records.append({"step": "runner-exception", "startedAt": now(), "finishedAt": now(),
                        "exitCode": None, "ok": False, "errorCodes": [type(exc).__name__],
                        "excerpt": sanitize(str(exc))})
    finally:
        cleanup_started = True
        if "nat" in created and run("cleanup-nat", ["compute", "routers", "nats", "delete",
                                      names["nat"], f"--router={names['router']}",
                                      f"--region={args.region}"])["ok"]:
            created.discard("nat")
        for kind, argv in [
            ("address", ["compute", "addresses", "delete", names["address"], f"--region={args.region}"]),
            ("router", ["compute", "routers", "delete", names["router"], f"--region={args.region}"]),
            ("subnet", ["compute", "networks", "subnets", "delete", names["subnet"], f"--region={args.region}"]),
            ("network", ["compute", "networks", "delete", names["network"]]),
        ]:
            if kind in created and run(f"cleanup-{kind}", argv)["ok"]:
                created.discard(kind)

        checks = [
            ("network", ["compute", "networks", "describe", names["network"]]),
            ("subnet", ["compute", "networks", "subnets", "describe", names["subnet"], f"--region={args.region}"]),
            ("router", ["compute", "routers", "describe", names["router"], f"--region={args.region}"]),
            ("address", ["compute", "addresses", "describe", names["address"], f"--region={args.region}"]),
        ]
        for kind, argv in checks:
            outcome = run(f"residual-{kind}", argv)
            outcome["residualPresent"] = outcome["ok"]
        # NAT is nested under a router; if the router is gone, its NAT cannot remain.
        nat_residual = run("residual-nat", ["compute", "routers", "nats", "describe", names["nat"],
                                           f"--router={names['router']}", f"--region={args.region}"])
        nat_residual["residualPresent"] = nat_residual["ok"]

        document = {"schemaVersion": 1, "experiment": "gcp-neutral-nat-2026-08-07",
                    "startedAt": records[0]["startedAt"] if records else now(),
                    "finishedAt": now(), "region": args.region, "project": "<PROJECT>",
                    "runPrefix": "<RUN>", "cleanupAttempted": cleanup_started,
                    "knownCreatedButNotDeleted": sorted(created), "steps": records}
        RESULTS.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")

    return 2 if created or experiment_failed else 0


if __name__ == "__main__":
    sys.exit(main())
