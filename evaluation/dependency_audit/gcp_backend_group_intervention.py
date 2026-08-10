"""GCP backend group 기능 개입을 생성·복구·정리까지 한 경계에서 실행한다."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .experiment_plan import REQUIRED_PHASES

EXPERIMENT_ID = "intervention.gcp.backend-service-backend-group.necessity"
PREFIX = "edbgint"
REGION = "asia-northeast3"
ZONE = "asia-northeast3-a"
COMMAND_TIMEOUT_SECONDS = 600
PROBE_TIMEOUT_SECONDS = 600
ESTIMATED_CAMPAIGN_COST_USD = 3.0
LOCAL_FILE_FLAGS = {
    "--certificate": "<temporary-certificate>",
    "--private-key": "<temporary-private-key>",
    "--metadata-from-file": "<temporary-startup-script>",
}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def phase(phase_id: str, status: str, evidence: str) -> dict[str, str]:
    return {"id": phase_id, "status": status, "observedAt": now(), "evidence": evidence}


def sanitize_command(command: list[str] | tuple[str, ...]) -> list[str]:
    """증거 파일에 기록되는 일회용 로컬 파일 경로를 안정적인 표기로 바꾼다."""
    sanitized = []
    for index, argument in enumerate(command):
        if index == 0 and Path(argument).name.lower() in {"gcloud", "gcloud.cmd"}:
            sanitized.append("gcloud")
            continue
        flag, separator, _value = argument.partition("=")
        replacement = LOCAL_FILE_FLAGS.get(flag)
        sanitized.append(f"{flag}={replacement}" if separator and replacement else argument)
    return sanitized


@dataclass(frozen=True)
class Names:
    prefix: str

    @property
    def network(self) -> str:
        return f"{self.prefix}-net"

    @property
    def subnet(self) -> str:
        return f"{self.prefix}-sub"

    @property
    def firewall(self) -> str:
        return f"{self.prefix}-hc"

    @property
    def vm(self) -> str:
        return f"{self.prefix}-vm"

    @property
    def group(self) -> str:
        return f"{self.prefix}-group"

    @property
    def health(self) -> str:
        return f"{self.prefix}-health"

    @property
    def backend(self) -> str:
        return f"{self.prefix}-backend"

    @property
    def certificate(self) -> str:
        return f"{self.prefix}-cert"

    @property
    def url_map(self) -> str:
        return f"{self.prefix}-map"

    @property
    def proxy(self) -> str:
        return f"{self.prefix}-proxy"

    @property
    def forwarding_rule(self) -> str:
        return f"{self.prefix}-forward"


class Gcloud:
    def __init__(self, project: str, *, execute: bool) -> None:
        self.project = project
        self.execute = execute
        self.executable = str(shutil.which("gcloud.cmd") or shutil.which("gcloud") or "gcloud")
        self.commands: list[list[str]] = []

    def run(self, *arguments: str, allow_failure: bool = False) -> dict[str, Any]:
        command = [self.executable, *arguments, "--project", self.project, "--quiet"]
        recorded_command = sanitize_command(command)
        self.commands.append(recorded_command)
        if not self.execute:
            return {"status": "planned", "command": recorded_command, "stdout": "", "stderr": ""}
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
        result = {
            "status": "passed" if completed.returncode == 0 else "failed",
            "command": recorded_command,
            "exitCode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        if completed.returncode and not allow_failure:
            raise RuntimeError(json.dumps(result, ensure_ascii=False))
        return result


def startup_script() -> str:
    return """#!/bin/bash
set -eu
cat >/opt/easydep_oracle.py <<'PY'
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/readyz':
            body = {'status': 'ready'}
        elif self.path == '/business':
            body = {'service': 'easydep-intervention', 'result': 'ok'}
        else:
            self.send_response(404); self.end_headers(); return
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers(); self.wfile.write(payload)
    def log_message(self, *_args): pass
HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
PY
nohup python3 /opt/easydep_oracle.py >/var/log/easydep-oracle.log 2>&1 &
"""


def creation_commands(names: Names, script_path: Path, cert_path: Path, key_path: Path) -> list[tuple[str, ...]]:
    return [
        ("compute", "networks", "create", names.network, "--subnet-mode=custom"),
        ("compute", "networks", "subnets", "create", names.subnet,
         f"--network={names.network}", f"--region={REGION}", "--range=10.93.0.0/24"),
        ("compute", "firewall-rules", "create", names.firewall,
         f"--network={names.network}", "--allow=tcp:8080",
         "--source-ranges=35.191.0.0/16,130.211.0.0/22", "--target-tags=easydep-hc"),
        ("compute", "instances", "create", names.vm, f"--zone={ZONE}",
         "--machine-type=e2-micro", "--image-family=debian-12", "--image-project=debian-cloud",
         f"--subnet={names.subnet}", "--no-address", "--tags=easydep-hc",
         f"--metadata-from-file=startup-script={script_path}"),
        ("compute", "instance-groups", "unmanaged", "create", names.group, f"--zone={ZONE}"),
        ("compute", "instance-groups", "unmanaged", "add-instances", names.group,
         f"--zone={ZONE}", f"--instances={names.vm}"),
        ("compute", "instance-groups", "unmanaged", "set-named-ports", names.group,
         f"--zone={ZONE}", "--named-ports=http:8080"),
        ("compute", "health-checks", "create", "http", names.health,
         "--port=8080", "--request-path=/readyz"),
        ("compute", "backend-services", "create", names.backend, "--global",
         "--protocol=HTTP", "--port-name=http", f"--health-checks={names.health}"),
        ("compute", "backend-services", "add-backend", names.backend, "--global",
         f"--instance-group={names.group}", f"--instance-group-zone={ZONE}"),
        ("compute", "ssl-certificates", "create", names.certificate,
         f"--certificate={cert_path}", f"--private-key={key_path}"),
        ("compute", "url-maps", "create", names.url_map, f"--default-service={names.backend}"),
        ("compute", "target-https-proxies", "create", names.proxy,
         f"--url-map={names.url_map}", f"--ssl-certificates={names.certificate}"),
        ("compute", "forwarding-rules", "create", names.forwarding_rule, "--global",
         f"--target-https-proxy={names.proxy}", "--ports=443"),
    ]


def cleanup_commands(names: Names) -> list[tuple[str, ...]]:
    return [
        ("compute", "forwarding-rules", "delete", names.forwarding_rule, "--global"),
        ("compute", "target-https-proxies", "delete", names.proxy),
        ("compute", "url-maps", "delete", names.url_map),
        ("compute", "ssl-certificates", "delete", names.certificate),
        ("compute", "backend-services", "delete", names.backend, "--global"),
        ("compute", "health-checks", "delete", names.health),
        ("compute", "instance-groups", "unmanaged", "delete", names.group, f"--zone={ZONE}"),
        ("compute", "instances", "delete", names.vm, f"--zone={ZONE}"),
        ("compute", "firewall-rules", "delete", names.firewall),
        ("compute", "networks", "subnets", "delete", names.subnet, f"--region={REGION}"),
        ("compute", "networks", "delete", names.network),
    ]


def probe(ip: str, *, expect_success: bool, timeout_seconds: int = PROBE_TIMEOUT_SECONDS) -> dict[str, Any]:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # noqa: S501 - isolated one-day test certificate
    deadline = time.time() + timeout_seconds
    observations: list[dict[str, Any]] = []
    streak = 0
    while time.time() < deadline:
        passed = True
        payloads = {}
        for path, expected in (
            ("/readyz", {"status": "ready"}),
            ("/business", {"service": "easydep-intervention", "result": "ok"}),
        ):
            try:
                with urllib.request.urlopen(
                    f"https://{ip}{path}", context=context, timeout=15
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    passed = passed and response.status == 200 and payload == expected
                    payloads[path] = {"status": response.status, "body": payload}
            except (OSError, ValueError, urllib.error.URLError) as exc:
                passed = False
                payloads[path] = {"error": type(exc).__name__}
        matched = passed == expect_success
        streak = streak + 1 if matched else 0
        observations.append({"observedAt": now(), "passed": passed, "responses": payloads})
        if streak >= 3:
            return {"matched": True, "expectSuccess": expect_success, "observations": observations}
        time.sleep(10)
    return {"matched": False, "expectSuccess": expect_success, "observations": observations}


def residual_names(client: Gcloud, prefix: str) -> list[str]:
    commands = [
        ("compute", "instances", "list", f"--filter=name~^{prefix}", "--format=value(name)"),
        ("compute", "instance-groups", "unmanaged", "list", f"--filter=name~^{prefix}", "--format=value(name)"),
        ("compute", "backend-services", "list", f"--filter=name~^{prefix}", "--format=value(name)"),
        ("compute", "health-checks", "list", f"--filter=name~^{prefix}", "--format=value(name)"),
        ("compute", "forwarding-rules", "list", f"--filter=name~^{prefix}", "--format=value(name)"),
        ("compute", "target-https-proxies", "list", f"--filter=name~^{prefix}", "--format=value(name)"),
        ("compute", "url-maps", "list", f"--filter=name~^{prefix}", "--format=value(name)"),
        ("compute", "ssl-certificates", "list", f"--filter=name~^{prefix}", "--format=value(name)"),
        ("compute", "firewall-rules", "list", f"--filter=name~^{prefix}", "--format=value(name)"),
        ("compute", "networks", "subnets", "list", f"--filter=name~^{prefix}", "--format=value(name)"),
        ("compute", "networks", "list", f"--filter=name~^{prefix}", "--format=value(name)"),
    ]
    residual = []
    for command in commands:
        result = client.run(*command, allow_failure=True)
        residual.extend(line.strip() for line in result.get("stdout", "").splitlines() if line.strip())
    return sorted(set(residual))


def openssl_path() -> str | None:
    discovered = shutil.which("openssl")
    if discovered:
        return discovered
    for candidate in (
        Path("C:/Program Files/Git/mingw64/bin/openssl.exe"),
        Path("C:/Program Files/Git/usr/bin/openssl.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def generate_certificate(directory: Path, names: Names, *, execute: bool) -> tuple[Path, Path]:
    cert, key = directory / "cert.pem", directory / "key.pem"
    if not execute:
        cert.write_text("planned", encoding="utf-8")
        key.write_text("planned", encoding="utf-8")
        return cert, key
    openssl = openssl_path()
    if not openssl:
        raise RuntimeError("openssl is required for the isolated TLS oracle")
    subprocess.run(
        [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", f"/CN={names.prefix}.invalid", "-keyout", str(key), "-out", str(cert)],
        capture_output=True, timeout=60, check=True,
    )
    return cert, key


def outcome(phases: list[dict[str, str]]) -> str:
    states = {item["id"]: item["status"] for item in phases}
    if states["interventionProvision"] == "failed":
        return "provisionBlocked"
    if states["interventionStartup"] == "failed":
        return "runtimeBlocked"
    if states["interventionFunction"] == "failed":
        return "functionBlocked"
    return "noEffect"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--confirm-project")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{5,29}", args.project):
        parser.error("invalid GCP project id")
    if args.execute and args.preflight_only:
        parser.error("choose --execute or --preflight-only")
    if (args.execute or args.preflight_only) and args.confirm_project != args.project:
        parser.error("remote access requires --confirm-project to match --project")
    suffix = ".preflight.json" if args.preflight_only else ".json"
    output = args.output or Path("evaluation/research_protocol/intervention-results") / f"{EXPERIMENT_ID}{suffix}"
    result: dict[str, Any] = {
        "schemaVersion": "easydep-dependency-intervention-result/v1",
        "experimentId": EXPERIMENT_ID,
        "provider": "gcp",
        "project": args.project,
        "executionMode": "execute" if args.execute else (
            "preflight" if args.preflight_only else "plan"
        ),
        "replications": [],
    }
    client = Gcloud(args.project, execute=args.execute or args.preflight_only)
    if not args.execute and not args.preflight_only:
        names = Names(f"{PREFIX}-r1")
        with tempfile.TemporaryDirectory(prefix="easydep-gcp-plan-") as temporary:
            root = Path(temporary)
            script = root / "startup.sh"
            script.write_text(startup_script(), encoding="utf-8")
            cert, key = generate_certificate(root, names, execute=False)
            for command in creation_commands(names, script, cert, key):
                client.run(*command)
            for command in cleanup_commands(names):
                client.run(*command, allow_failure=True)
        print(json.dumps({"mode": "plan", "commands": client.commands}, ensure_ascii=False, indent=2))
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    project_check = client.run("projects", "describe", args.project, "--format=value(projectId)")
    api_check = client.run(
        "services", "list", "--enabled", "--filter=config.name=compute.googleapis.com",
        "--format=value(config.name)",
    )
    billing_check = client.run(
        "billing", "projects", "describe", args.project, "--format=value(billingEnabled)"
    )
    preexisting = residual_names(client, PREFIX)
    result["preflight"] = {
        "projectMatched": project_check["stdout"].strip() == args.project,
        "computeApiEnabled": api_check["stdout"].strip() == "compute.googleapis.com",
        "billingEnabled": billing_check["stdout"].strip().lower() == "true",
        "estimatedCampaignCostUSD": ESTIMATED_CAMPAIGN_COST_USD,
        "preexistingResources": preexisting,
        "opensslExecutable": openssl_path(),
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not all((
        result["preflight"]["projectMatched"],
        result["preflight"]["computeApiEnabled"],
        result["preflight"]["billingEnabled"],
        bool(result["preflight"]["opensslExecutable"]),
    )):
        raise RuntimeError(f"GCP preflight failed: {result['preflight']}")
    if preexisting:
        raise RuntimeError(f"pre-existing experiment resources block execution: {preexisting}")
    if args.preflight_only:
        print(json.dumps(result["preflight"], ensure_ascii=False, indent=2))
        return 0
    for replication in range(1, 4):
        names = Names(f"{PREFIX}-r{replication}")
        ledger = output.parent / f"{EXPERIMENT_ID}.r{replication}.ledger.json"
        evidence = output.parent / f"{EXPERIMENT_ID}.r{replication}.evidence.json"
        record = {
            "replication": replication,
            "budgetCensored": False,
            "schedulerDelayed": False,
            "outcomeClass": "provisionBlocked",
            "phases": [],
        }
        raw: dict[str, Any] = {"prefix": names.prefix, "startedAt": now(), "commands": []}
        try:
            before = residual_names(client, names.prefix)
            if before:
                raise RuntimeError(f"pre-existing resources block execution: {before}")
            with tempfile.TemporaryDirectory(prefix=f"easydep-gcp-r{replication}-") as temporary:
                root = Path(temporary)
                script = root / "startup.sh"
                script.write_text(startup_script(), encoding="utf-8")
                cert, key = generate_certificate(root, names, execute=True)
                planned = creation_commands(names, script, cert, key)
                ledger.write_text(json.dumps({
                    "project": args.project, "prefix": names.prefix,
                    "plannedResources": [sanitize_command(item) for item in planned], "recordedAt": now(),
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                for command in planned:
                    raw["commands"].append(client.run(*command))
                record["phases"].append(phase("controlProvision", "passed", str(evidence)))
                instance = client.run("compute", "instances", "describe", names.vm,
                                      f"--zone={ZONE}", "--format=value(status)")
                running = instance["stdout"].strip() == "RUNNING"
                record["phases"].append(phase("controlStartup", "passed" if running else "failed", str(evidence)))
                ip_result = client.run("compute", "forwarding-rules", "describe", names.forwarding_rule,
                                       "--global", "--format=value(IPAddress)")
                ip = ip_result["stdout"].strip()
                raw["controlProbe"] = probe(ip, expect_success=True)
                control_ok = raw["controlProbe"]["matched"]
                record["phases"].append(phase("controlFunction", "passed" if control_ok else "failed", str(evidence)))
                if not control_ok:
                    raise RuntimeError("control functional oracle did not stabilize")
                raw["removeBackend"] = client.run(
                    "compute", "backend-services", "remove-backend", names.backend, "--global",
                    f"--instance-group={names.group}", f"--instance-group-zone={ZONE}",
                )
                record["phases"].append(phase("dependencyIntervention", "passed", str(evidence)))
                backend = client.run("compute", "backend-services", "describe", names.backend,
                                     "--global", "--format=json")
                stable = backend["status"] == "passed"
                record["phases"].append(phase("interventionProvision", "passed" if stable else "failed", str(evidence)))
                instance = client.run("compute", "instances", "describe", names.vm,
                                      f"--zone={ZONE}", "--format=value(status)")
                running = instance["stdout"].strip() == "RUNNING"
                record["phases"].append(phase("interventionStartup", "passed" if running else "failed", str(evidence)))
                raw["interventionProbe"] = probe(ip, expect_success=False, timeout_seconds=240)
                function_failed = raw["interventionProbe"]["matched"]
                record["phases"].append(phase(
                    "interventionFunction", "failed" if function_failed else "passed", str(evidence)
                ))
                raw["restoreBackend"] = client.run(
                    "compute", "backend-services", "add-backend", names.backend, "--global",
                    f"--instance-group={names.group}", f"--instance-group-zone={ZONE}",
                )
                raw["restorationProbe"] = probe(ip, expect_success=True)
                restored = raw["restorationProbe"]["matched"]
                record["phases"].append(phase("restorationFunction", "passed" if restored else "failed", str(evidence)))
                record["outcomeClass"] = outcome(record["phases"])
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            raw["error"] = {"type": type(exc).__name__, "message": str(exc)}
            present = {item["id"] for item in record["phases"]}
            record["phases"].extend(
                phase(phase_id, "notReached", str(evidence))
                for phase_id in REQUIRED_PHASES if phase_id not in present
            )
            record["phases"].sort(key=lambda item: REQUIRED_PHASES.index(item["id"]))
        finally:
            cleanup = []
            for command in cleanup_commands(names):
                cleanup.append(client.run(*command, allow_failure=True))
            raw["cleanup"] = cleanup
            raw["residualResources"] = residual_names(client, names.prefix)
            raw["finishedAt"] = now()
            evidence.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
            result["replications"].append(record)
            result["residualResources"] = raw["residualResources"]
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            if raw["residualResources"]:
                raise RuntimeError(f"cleanup incomplete; refusing next replication: {raw['residualResources']}")
    result["cleanupVerified"] = True
    result["residualResources"] = []
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
