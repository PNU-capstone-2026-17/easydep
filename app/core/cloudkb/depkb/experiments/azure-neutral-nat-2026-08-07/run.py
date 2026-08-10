"""Run the preregistered Azure NAT Gateway control-plane experiment.

This script mutates Azure only when passed ``--execute``. It never creates a VM.
See plan.md before execution.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results.json"

UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
IPV4_RE = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
CODE_PATTERNS = (
    re.compile(r'"code"\s*:\s*"([^"\r\n]+)"', re.IGNORECASE),
    re.compile(r"\(([-A-Za-z0-9_.]+)\)\s*[^\r\n]*"),
    re.compile(r"Code:\s*([-A-Za-z0-9_.]+)", re.IGNORECASE),
)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sanitize(value: str, limit: int = 800) -> str:
    value = UUID_RE.sub("<uuid-redacted>", value)
    value = IPV4_RE.sub("<ipv4-redacted>", value)
    value = re.sub(
        r"(?i)(authorization|bearer|token|secret|password)(\s*[:=]\s*)\S+",
        r"\1\2<redacted>",
        value,
    )
    value = re.sub(
        r"(?i)/subscriptions/[^/\s]+", "/subscriptions/<redacted>", value
    )
    value = re.sub(r"(?i)/tenants/[^/\s]+", "/tenants/<redacted>", value)
    return value.replace("\r", "").strip()[:limit]


def error_codes(text: str) -> list[str]:
    found: list[str] = []
    for pattern in CODE_PATTERNS:
        for match in pattern.findall(text):
            code = sanitize(match, 100)
            if code and code not in found:
                found.append(code)
    return found[:10]


class Experiment:
    def __init__(self, az: str, location: str) -> None:
        suffix = datetime.now(UTC).strftime("%H%M%S") + secrets.token_hex(3)
        self.az = az
        self.location = location
        self.rg = f"depkb-neutral-nat-{suffix}"
        self.vnet = f"vnet-{suffix}"
        self.subnet = f"subnet-{suffix}"
        self.pip = f"pip-{suffix}"
        self.nat = f"nat-{suffix}"
        self.doc: dict[str, Any] = {
            "schemaVersion": 1,
            "experiment": "azure-neutral-nat-2026-08-07",
            "planStatus": "preregistered",
            "startedAt": now(),
            "location": location,
            "resourceNames": {
                "resourceGroup": self.rg,
                "vnet": self.vnet,
                "subnet": self.subnet,
                "publicIp": self.pip,
                "natGateway": self.nat,
            },
            "steps": [],
            "cleanup": {},
        }
        self.save()

    def save(self) -> None:
        serialized = json.dumps(self.doc, ensure_ascii=False, indent=2) + "\n"
        for name in (self.rg, self.vnet, self.subnet, self.pip, self.nat):
            serialized = serialized.replace(name, "<RUN_RESOURCE>")
        RESULTS.write_text(serialized, encoding="utf-8")

    def command(
        self,
        name: str,
        args: list[str],
        *,
        timeout: int = 600,
        expected: str = "success",
        cleanup: bool = False,
    ) -> dict[str, Any]:
        started = time.monotonic()
        started_at = now()
        try:
            result = subprocess.run(
                [self.az, *args, "--only-show-errors"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            combined = "\n".join(part for part in (result.stderr, result.stdout) if part)
            entry: dict[str, Any] = {
                "name": name,
                "startedAt": started_at,
                "finishedAt": now(),
                "durationSeconds": round(time.monotonic() - started, 3),
                "ok": result.returncode == 0,
                "exitCode": result.returncode,
                "expected": expected,
                "errorCodes": error_codes(combined),
            }
            if result.returncode != 0:
                entry["errorExcerpt"] = sanitize(combined)
            entry["out"] = result.stdout
        except subprocess.TimeoutExpired as exc:
            combined = "\n".join(
                str(part or "") for part in (exc.stderr, exc.stdout) if part
            )
            entry = {
                "name": name,
                "startedAt": started_at,
                "finishedAt": now(),
                "durationSeconds": round(time.monotonic() - started, 3),
                "ok": False,
                "timedOut": True,
                "expected": expected,
                "errorCodes": error_codes(combined),
                "errorExcerpt": sanitize(combined),
                "out": "",
            }
        stored = {key: value for key, value in entry.items() if key != "out"}
        if cleanup:
            self.doc["cleanup"].setdefault("steps", []).append(stored)
        else:
            self.doc["steps"].append(stored)
        entry["_stored"] = stored
        self.save()
        label = "OK" if entry["ok"] else "/".join(entry["errorCodes"]) or "FAIL"
        print(f"{name:42} {label}", flush=True)
        return entry

    def observe_json(self, entry: dict[str, Any]) -> Any:
        if not entry["ok"]:
            return None
        try:
            return json.loads(entry["out"] or "null")
        except json.JSONDecodeError:
            return None

    def exists(self, kind: str, name: str, step: str) -> bool:
        entry = self.command(
            step,
            ["network", *kind.split(), "show", "-g", self.rg, "-n", name, "-o", "none"],
            expected="probe; NotFound means absent",
        )
        present = entry["ok"]
        entry["_stored"]["observation"] = {"exists": present}
        self.save()
        return present

    def run(self) -> None:
        try:
            if not self.command(
                "S1.create-resource-group",
                ["group", "create", "-n", self.rg, "-l", self.location, "-o", "none"],
            )["ok"]:
                return
            if not self.command(
                "S2.create-vnet-and-subnet",
                [
                    "network", "vnet", "create", "-g", self.rg, "-n", self.vnet,
                    "-l", self.location, "--address-prefixes", "10.247.0.0/16",
                    "--subnet-name", self.subnet, "--subnet-prefixes", "10.247.1.0/24",
                    "-o", "none",
                ],
            )["ok"]:
                return
            if not self.command(
                "S3.create-static-standard-public-ip",
                [
                    "network", "public-ip", "create", "-g", self.rg, "-n", self.pip,
                    "-l", self.location, "--sku", "Standard", "--allocation-method",
                    "Static", "--version", "IPv4", "-o", "none",
                ],
            )["ok"]:
                return
            if not self.command(
                "S4.create-nat-with-public-ip",
                [
                    "network", "nat", "gateway", "create", "-g", self.rg, "-n", self.nat,
                    "-l", self.location, "--public-ip-addresses", self.pip,
                    "--idle-timeout", "4", "-o", "none",
                ],
            )["ok"]:
                return
            if not self.command(
                "S5.associate-subnet",
                [
                    "network", "vnet", "subnet", "update", "-g", self.rg,
                    "--vnet-name", self.vnet, "-n", self.subnet,
                    "--nat-gateway", self.nat, "-o", "none",
                ],
            )["ok"]:
                return

            shape = self.command(
                "O1.read-composition-shape",
                [
                    "network", "nat", "gateway", "show", "-g", self.rg, "-n", self.nat,
                    "-o", "json",
                ],
            )
            shape_data = self.observe_json(shape) or {}
            shape["_stored"]["observation"] = {
                "publicIpCount": len(shape_data.get("publicIpAddresses", [])),
                "subnetCount": len(shape_data.get("subnets", [])),
            }
            self.save()

            self.command(
                "C1.delete-referenced-public-ip",
                ["network", "public-ip", "delete", "-g", self.rg, "-n", self.pip],
                expected="rejection while referenced",
            )
            pip_present = self.exists("public-ip", self.pip, "C1b.public-ip-exists")

            self.command(
                "C2.delete-associated-nat-gateway",
                ["network", "nat", "gateway", "delete", "-g", self.rg, "-n", self.nat],
                expected="rejection while subnet-associated",
            )
            nat_present = self.exists("nat gateway", self.nat, "C2b.nat-exists")

            if nat_present:
                detached = self.command(
                    "L1.detach-nat-from-subnet",
                    [
                        "network", "vnet", "subnet", "update", "-g", self.rg,
                        "--vnet-name", self.vnet, "-n", self.subnet,
                        "--remove", "natGateway", "-o", "none",
                    ],
                )
                if detached["ok"]:
                    verify = self.command(
                        "L2.verify-subnet-detached",
                        [
                            "network", "vnet", "subnet", "show", "-g", self.rg,
                            "--vnet-name", self.vnet, "-n", self.subnet,
                            "--query", "natGateway==null", "-o", "json",
                        ],
                    )
                    verify["_stored"]["observation"] = {
                        "natGatewayAbsent": self.observe_json(verify)
                    }
                    self.save()
                    self.command(
                        "L3.delete-detached-nat-gateway",
                        ["network", "nat", "gateway", "delete", "-g", self.rg, "-n", self.nat],
                    )
                    self.exists("nat gateway", self.nat, "L4.nat-absent-after-delete")

            if pip_present:
                self.command(
                    "L5.delete-unreferenced-public-ip",
                    ["network", "public-ip", "delete", "-g", self.rg, "-n", self.pip],
                )
                self.exists("public-ip", self.pip, "L6.public-ip-absent-after-delete")
        finally:
            self.cleanup()

    def cleanup(self) -> None:
        delete = self.command(
            "T1.delete-resource-group",
            ["group", "delete", "-n", self.rg, "--yes", "--no-wait"],
            timeout=120,
            cleanup=True,
        )
        if delete["ok"]:
            self.command(
                "T2.wait-resource-group-deleted",
                ["group", "wait", "-n", self.rg, "--deleted", "--interval", "10", "--timeout", "1200"],
                timeout=1260,
                cleanup=True,
            )
        probe = self.command(
            "T3.residual-resource-group-check",
            ["group", "exists", "-n", self.rg, "-o", "json"],
            timeout=60,
            cleanup=True,
        )
        exists_value = self.observe_json(probe)
        probe["_stored"]["observation"] = {"resourceGroupExists": exists_value}
        self.doc["cleanup"]["complete"] = probe["ok"] and exists_value is False
        self.doc["finishedAt"] = now()
        self.save()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="allow Azure mutations")
    parser.add_argument("--location", default="koreacentral")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.execute:
        raise SystemExit("Refusing Azure mutation without --execute; read plan.md first.")
    if RESULTS.exists():
        raise SystemExit(f"Refusing to overwrite existing result: {RESULTS}")
    az = shutil.which("az")
    if not az:
        raise SystemExit("Azure CLI 'az' was not found on PATH.")
    experiment = Experiment(az, args.location)
    experiment.run()
    return 0 if experiment.doc["cleanup"].get("complete") else 2


if __name__ == "__main__":
    raise SystemExit(main())
