"""Run one domain-neutral Azure Application Gateway ingress intervention and cleanup."""

from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from evaluation.dependency_audit.sample_app_managed_tls_common import (
    generate_test_certificate,
    http_probe,
    https_probe,
    now,
    startup_oracle,
)

AZURE_LOCATION = "koreacentral"


class Azure:
    def __init__(self) -> None:
        self.executable = str(shutil.which("az.cmd") or shutil.which("az") or "az")

    def run(self, *arguments: str, allow_failure: bool = False) -> str:
        completed = subprocess.run(
            [self.executable, *arguments, "--only-show-errors", "--output", "json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
            check=False,
        )
        if completed.returncode and not allow_failure:
            raise RuntimeError(
                json.dumps(
                    {
                        "commandGroup": " ".join(arguments[:3]),
                        "exitCode": completed.returncode,
                        "stderr": completed.stderr[-3000:],
                    },
                    ensure_ascii=False,
                )
            )
        return completed.stdout

    def json(self, *arguments: str) -> Any:
        return json.loads(self.run(*arguments) or "{}")


def _group_exists(client: Azure, group: str) -> bool:
    return client.run("group", "exists", "--name", group).strip().lower() == "true"


def _delete_group(client: Azure, group: str) -> tuple[bool, float]:
    started = time.monotonic()
    client.run("group", "delete", "--name", group, "--yes", "--no-wait", allow_failure=True)
    deadline = started + 1800
    while time.monotonic() < deadline:
        if not _group_exists(client, group):
            return True, round(time.monotonic() - started, 3)
        time.sleep(15)
    return False, round(time.monotonic() - started, 3)


def _ssh_public_key(root: Path) -> Path:
    private_key = root / "azure-admin-key"
    subprocess.run(
        [
            str(shutil.which("ssh-keygen.exe") or shutil.which("ssh-keygen") or "ssh-keygen"),
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "easydep-test",
            "-f",
            str(private_key),
        ],
        capture_output=True,
        timeout=60,
        check=True,
    )
    return Path(f"{private_key}.pub")


def run_experiment(
    output: Path,
    *,
    location: str = AZURE_LOCATION,
    frontend_protocol: str = "https",
) -> dict[str, Any]:
    if frontend_protocol not in {"http", "https"}:
        raise ValueError(f"unsupported frontend protocol: {frontend_protocol}")
    secure_frontend = frontend_protocol == "https"
    protocol_label = frontend_protocol.upper()
    suffix = uuid.uuid4().hex[:8]
    resource_prefix = "tls" if secure_frontend else "http"
    run_id = f"easydep-{resource_prefix}-{suffix}"
    group = f"ed-{resource_prefix}-{suffix}-rg"
    names = {
        "vnet": f"ed-{resource_prefix}-{suffix}-vnet",
        "gatewaySubnet": f"ed-{resource_prefix}-{suffix}-gateway-subnet",
        "appSubnet": f"ed-{resource_prefix}-{suffix}-app-subnet",
        "appNsg": f"ed-{resource_prefix}-{suffix}-app-nsg",
        "vm": f"ed-{resource_prefix}-{suffix}-vm",
        "publicIp": f"ed-{resource_prefix}-{suffix}-pip",
        "gateway": f"ed-{resource_prefix}-{suffix}-agw",
        "probe": f"ed-{resource_prefix}-{suffix}-probe",
    }
    client = Azure()
    started = time.monotonic()
    result: dict[str, Any] = {
        "schemaVersion": (
            "easydep-domain-neutral-managed-tls/v1"
            if secure_frontend
            else "easydep-domain-neutral-managed-http/v1"
        ),
        "provider": "azure",
        "runId": run_id,
        "resourceGroup": group,
        "location": location,
        "startedAt": now(),
        "pathUnderTest": (
            f"{protocol_label} listener -> Application Gateway -> backend pool -> health probe -> "
            "App VM port 8080"
        ),
        "steps": [],
        "limitations": (
            [
                "The certificate is a one-day self-signed PFX.",
                "DNS ownership and public CA trust are not measured.",
                "This is one development run and not an availability or SLA measurement.",
            ]
            if secure_frontend
            else [
                "Transport security, DNS ownership, and certificates are not measured.",
                "This is one development run and not an availability or SLA measurement.",
            ]
        ),
    }
    try:
        if _group_exists(client, group):
            raise RuntimeError("pre-existing experiment resource group blocks execution")
        with tempfile.TemporaryDirectory(
            prefix=f"easydep-azure-managed-{resource_prefix}-"
        ) as temporary:
            root = Path(temporary)
            certificate_password = secrets.token_urlsafe(24) if secure_frontend else None
            material = (
                generate_test_certificate(
                    root,
                    f"ed-tls-{suffix}.invalid",
                    pfx_password=certificate_password,
                )
                if secure_frontend
                else None
            )
            public_key = _ssh_public_key(root)
            script = root / "startup.sh"
            script.write_text(startup_oracle(), encoding="utf-8", newline="\n")
            client.run(
                "group",
                "create",
                "--name",
                group,
                "--location",
                location,
                "--tags",
                f"easydep-run={run_id}",
            )
            client.run(
                "network",
                "vnet",
                "create",
                "--resource-group",
                group,
                "--name",
                names["vnet"],
                "--address-prefixes",
                "10.91.0.0/16",
                "--subnet-name",
                names["gatewaySubnet"],
                "--subnet-prefixes",
                "10.91.0.0/24",
            )
            client.run(
                "network",
                "vnet",
                "subnet",
                "create",
                "--resource-group",
                group,
                "--vnet-name",
                names["vnet"],
                "--name",
                names["appSubnet"],
                "--address-prefixes",
                "10.91.1.0/24",
            )
            client.run(
                "network",
                "nsg",
                "create",
                "--resource-group",
                group,
                "--name",
                names["appNsg"],
                "--tags",
                f"easydep-run={run_id}",
            )
            client.run(
                "network",
                "nsg",
                "rule",
                "create",
                "--resource-group",
                group,
                "--nsg-name",
                names["appNsg"],
                "--name",
                "allow-gateway-to-app",
                "--priority",
                "100",
                "--source-address-prefixes",
                "10.91.0.0/24",
                "--destination-port-ranges",
                "8080",
                "--access",
                "Allow",
                "--protocol",
                "Tcp",
                "--direction",
                "Inbound",
            )
            client.run(
                "network",
                "vnet",
                "subnet",
                "update",
                "--resource-group",
                group,
                "--vnet-name",
                names["vnet"],
                "--name",
                names["appSubnet"],
                "--network-security-group",
                names["appNsg"],
            )
            vm = client.json(
                "vm",
                "create",
                "--resource-group",
                group,
                "--name",
                names["vm"],
                "--image",
                "Ubuntu2204",
                "--size",
                "Standard_B2ats_v2",
                "--vnet-name",
                names["vnet"],
                "--subnet",
                names["appSubnet"],
                "--public-ip-address",
                "",
                "--nsg",
                "",
                "--admin-username",
                "easydepadmin",
                "--ssh-key-values",
                str(public_key),
                "--custom-data",
                str(script),
                "--tags",
                f"easydep-run={run_id}",
            )
            private_ip = vm["privateIpAddress"]
            client.run(
                "network",
                "public-ip",
                "create",
                "--resource-group",
                group,
                "--name",
                names["publicIp"],
                "--sku",
                "Standard",
                "--allocation-method",
                "Static",
                "--tags",
                f"easydep-run={run_id}",
            )
            gateway_started = time.monotonic()
            gateway_arguments = [
                "network",
                "application-gateway",
                "create",
                "--resource-group",
                group,
                "--name",
                names["gateway"],
                "--location",
                location,
                "--sku",
                "Standard_v2",
                "--capacity",
                "1",
                "--priority",
                "100",
                "--vnet-name",
                names["vnet"],
                "--subnet",
                names["gatewaySubnet"],
                "--public-ip-address",
                names["publicIp"],
                "--servers",
                private_ip,
                "--frontend-port",
                "443" if secure_frontend else "80",
                "--http-settings-port",
                "8080",
                "--http-settings-protocol",
                "Http",
                "--connection-draining-timeout",
                "0",
                "--tags",
                f"easydep-run={run_id}",
            ]
            if secure_frontend:
                assert material is not None and certificate_password is not None
                gateway_arguments.extend(
                    [
                        "--cert-file",
                        str(material["pfx"]),
                        "--cert-password",
                        certificate_password,
                    ]
                )
            client.run(*gateway_arguments)
            gateway_create_seconds = round(time.monotonic() - gateway_started, 3)
            client.run(
                "network",
                "application-gateway",
                "probe",
                "create",
                "--resource-group",
                group,
                "--gateway-name",
                names["gateway"],
                "--name",
                names["probe"],
                "--protocol",
                "Http",
                "--host",
                "127.0.0.1",
                "--path",
                "/readyz",
                "--interval",
                "10",
                "--timeout",
                "5",
                "--threshold",
                "2",
                "--match-status-codes",
                "200-399",
            )
            client.run(
                "network",
                "application-gateway",
                "http-settings",
                "update",
                "--resource-group",
                group,
                "--gateway-name",
                names["gateway"],
                "--name",
                "appGatewayBackendHttpSettings",
                "--port",
                "8080",
                "--protocol",
                "Http",
                "--probe",
                names["probe"],
                "--connection-draining-timeout",
                "0",
            )
            address = client.run(
                "network",
                "public-ip",
                "show",
                "--resource-group",
                group,
                "--name",
                names["publicIp"],
                "--query",
                "ipAddress",
                "--output",
                "tsv",
            ).strip().strip('"')
            probe_endpoint = https_probe if secure_frontend else http_probe
            baseline = probe_endpoint(address, expect_success=True, timeout_seconds=900)
            result["steps"].append(
                {
                    "name": f"baseline.managed-{frontend_protocol}-business-path",
                    "status": "passed" if baseline["matched"] else "failed",
                    "gatewayCreateSeconds": gateway_create_seconds,
                    "probe": baseline,
                }
            )
            if not baseline["matched"]:
                raise RuntimeError(
                    f"Azure managed {protocol_label} baseline did not stabilize"
                )
            client.run(
                "network",
                "application-gateway",
                "address-pool",
                "update",
                "--resource-group",
                group,
                "--gateway-name",
                names["gateway"],
                "--name",
                "appGatewayBackendPool",
                "--servers",
                "10.91.1.254",
            )
            removed = probe_endpoint(address, expect_success=False, timeout_seconds=600)
            result["steps"].append(
                {
                    "name": "intervention.backend-membership-replaced-with-unreachable-address",
                    "status": "passed" if removed["matched"] else "failed",
                    "probe": removed,
                }
            )
            if not removed["matched"]:
                raise RuntimeError(
                    f"Azure managed {protocol_label} remained functional without its backend"
                )
            client.run(
                "network",
                "application-gateway",
                "address-pool",
                "update",
                "--resource-group",
                group,
                "--gateway-name",
                names["gateway"],
                "--name",
                "appGatewayBackendPool",
                "--servers",
                private_ip,
            )
            restored = probe_endpoint(address, expect_success=True, timeout_seconds=900)
            result["steps"].append(
                {
                    "name": f"restoration.managed-{frontend_protocol}-business-path",
                    "status": "passed" if restored["matched"] else "failed",
                    "probe": restored,
                }
            )
            if not restored["matched"]:
                raise RuntimeError(
                    f"Azure managed {protocol_label} restoration did not stabilize"
                )
        result["outcome"] = "passed"
    except Exception as exc:
        result["outcome"] = "failed"
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        deleted, delete_seconds = _delete_group(client, group)
        result["cleanup"] = {
            "passed": deleted,
            "resourceGroupExists": _group_exists(client, group),
            "deleteSeconds": delete_seconds,
            "residualResources": [] if deleted else [group],
        }
        if not deleted:
            result["outcome"] = "failed"
        result["finishedAt"] = now()
        result["elapsedSeconds"] = round(time.monotonic() - started, 3)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--location", default=AZURE_LOCATION)
    parser.add_argument("--frontend-protocol", choices=("http", "https"), default="https")
    parser.add_argument("--confirm-location")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z]+", args.location):
        parser.error("invalid Azure location")
    if not args.execute:
        print(
            json.dumps(
                {
                    "mode": "plan",
                    "location": args.location,
                    "frontendProtocol": args.frontend_protocol,
                },
                indent=2,
            )
        )
        return 0
    if args.confirm_location != args.location:
        parser.error("--confirm-location must match --location")
    output = args.output or Path(
        "evaluation/dependency_audit/"
        f"azure-sample-app-managed-{args.frontend_protocol}-result-20260817.json"
    )
    result = run_experiment(
        output,
        location=args.location,
        frontend_protocol=args.frontend_protocol,
    )
    return 0 if result["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
