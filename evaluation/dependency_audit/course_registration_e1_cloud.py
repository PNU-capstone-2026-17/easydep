"""동일한 생성 앱과 업무 오라클로 Azure/GCP E1 배포 경계를 확인한다.

이 모듈은 앱을 다시 생성하거나 요구사항을 다시 분석하지 않는다. 이미 검증된 이미지와
동결된 외부 오라클을 재사용하고, CSP별 VM·네트워크·영속 디스크 연결만 다르게 수행한다.
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import tempfile
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.dependency_audit.inter_vm_postgres_intervention import (
    AZURE_LOCATION,
    AZURE_VM_SIZE,
    GCP_REGION,
    GCP_ZONE,
    INSTALL_DOCKER_DEBIAN,
    POSTGRES_IMAGE,
    POSTGRES_PASSWORD,
    Recorder,
    _az_run,
    _run,
    _safe_text,
)
from evaluation.dependency_audit.sample_app_managed_tls_common import (
    generate_test_certificate,
)
from evaluation.http_business_oracle import run_business_oracle

ROOT = Path(__file__).resolve().parents[2]
CASE_ROOT = ROOT / "evaluation" / "baselines" / "course-registration-cases"
BUSINESS_ORACLE = CASE_ROOT / "business-oracle.json"
DATABASE_UNAVAILABLE_ORACLE = CASE_ROOT / "database-unavailable-oracle.json"
PERSISTENCE_ORACLE = CASE_ROOT / "persistence-oracle.json"

APP_IMAGE = (
    "public.ecr.aws/w3q5i0g7/easydep-course-campaign-20260815"
    "@sha256:ff0b634b5cbcf9a78099f1f4687acd2eb6cbae8ca12a900770abadcfd7a16adb"
)


class CourseE1Recorder(Recorder):
    def __init__(self, provider: str, run_id: str, output: Path) -> None:
        super().__init__(provider, run_id, output)
        self.document |= {
            "schemaVersion": "easydep-course-registration-cloud-e1/v1",
            "scope": {
                "applicationInstances": 1,
                "stateInstances": 1,
                "persistentDataVolumes": 1,
                "managedLoadBalancer": False,
                "performanceSloMeasured": False,
            },
            "image": {
                "reference": APP_IMAGE,
                "source": "same generated application artifact used for both providers",
            },
            "oracles": {
                "business": str(BUSINESS_ORACLE.relative_to(ROOT)).replace("\\", "/"),
                "databaseUnavailable": str(
                    DATABASE_UNAVAILABLE_ORACLE.relative_to(ROOT)
                ).replace("\\", "/"),
                "persistence": str(PERSISTENCE_ORACLE.relative_to(ROOT)).replace("\\", "/"),
            },
            "pathUnderTest": (
                "public test HTTPS -> App VM/container -> state VM private IPv4:5432 -> "
                "PostgreSQL PGDATA -> CSP data disk"
            ),
        }
        self.save()

    def finish_e1(
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
                "This is one development run for one frozen generated application image.",
                "The one-day self-signed certificate tests TLS wiring, not DNS ownership or public trust.",
                "State VM restart and data preservation do not establish state-tier high availability.",
                "No performance SLO, right-sizing, or cost optimality is measured.",
                "The shared image registry location is not evidence about the target CSP.",
            ],
        }
        self.save()


def _certificate_text() -> tuple[str, str]:
    with tempfile.TemporaryDirectory(prefix="easydep-course-cert-") as temporary:
        material = generate_test_certificate(Path(temporary), "easydep-course.invalid")
        return (
            base64.b64encode(material["certificate"].read_bytes()).decode("ascii"),
            base64.b64encode(material["privateKey"].read_bytes()).decode("ascii"),
        )


def _state_setup(device: str, password: str) -> str:
    return f"""sudo cloud-init status --wait || true
{INSTALL_DOCKER_DEBIAN}
for i in $(seq 1 90); do [ -b '{device}' ] && break; sleep 2; done
test -b '{device}'
if ! sudo blkid '{device}' >/dev/null 2>&1; then sudo mkfs.ext4 -F '{device}'; fi
disk_uuid=$(sudo blkid -s UUID -o value '{device}')
sudo mkdir -p /var/lib/easydep-postgres
grep -q "$disk_uuid" /etc/fstab || echo "UUID=$disk_uuid /var/lib/easydep-postgres ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab >/dev/null
sudo mount -a
sudo mkdir -p /var/lib/easydep-postgres/data
sudo chown 999:999 /var/lib/easydep-postgres/data
mountpoint -q /var/lib/easydep-postgres
sudo docker rm -f easydep-state >/dev/null 2>&1 || true
sudo docker run -d --name easydep-state --restart unless-stopped \\
  -e POSTGRES_DB=appdb -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD='{password}' \\
  -p 5432:5432 -v /var/lib/easydep-postgres/data:/var/lib/postgresql/data {POSTGRES_IMAGE}
for i in $(seq 1 120); do
  sudo docker exec easydep-state pg_isready -U postgres -d appdb && exit 0
  sleep 2
done
exit 1
"""


def _app_setup(
    state_private: str,
    password: str,
    certificate_b64: str,
    private_key_b64: str,
) -> str:
    return f"""sudo cloud-init status --wait || true
{INSTALL_DOCKER_DEBIAN}
export DEBIAN_FRONTEND=noninteractive
sudo apt-get install -y -qq nginx ca-certificates
echo '{certificate_b64}' | base64 -d | sudo tee /etc/ssl/certs/easydep-course.crt >/dev/null
echo '{private_key_b64}' | base64 -d | sudo tee /etc/ssl/private/easydep-course.key >/dev/null
sudo chmod 600 /etc/ssl/private/easydep-course.key
sudo tee /etc/nginx/sites-available/default >/dev/null <<'NGINX'
server {{
  listen 443 ssl;
  ssl_certificate /etc/ssl/certs/easydep-course.crt;
  ssl_certificate_key /etc/ssl/private/easydep-course.key;
  location / {{
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
  }}
}}
NGINX
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
sudo docker pull '{APP_IMAGE}'
sudo docker rm -f easydep-app >/dev/null 2>&1 || true
sudo docker run -d --name easydep-app --restart unless-stopped \\
  -p 127.0.0.1:8080:8080 \\
  -e DATABASE_URL='jdbc:postgresql://{state_private}:5432/appdb' \\
  -e DATABASE_USER=postgres -e DATABASE_PASSWORD='{password}' '{APP_IMAGE}'
"""


def _load_oracle(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_oracle_until(
    base_url: str,
    oracle_path: Path,
    output: Path,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        attempts += 1
        last = run_business_oracle(
            base_url,
            _load_oracle(oracle_path),
            insecure_test_tls=True,
        )
        if last["status"] == "passed":
            last["attemptsUntilPass"] = attempts
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(last, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            return last
        time.sleep(10)
    if last is None:
        raise TimeoutError(f"oracle was not attempted: {oracle_path.name}")
    last["attemptsUntilPass"] = attempts
    output.write_text(json.dumps(last, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    raise RuntimeError(f"oracle did not pass: {oracle_path.name}")


def _wait_health(base_url: str, *, timeout_seconds: int = 900) -> dict[str, Any]:
    oracle = {
        "schemaVersion": "easydep-http-business-oracle/v1",
        "oracleId": "course-registration-health-up-v1",
        "requestTimeoutSeconds": 15,
        "phases": [
            {
                "id": "application-and-database-ready",
                "request": {"method": "GET", "path": "/health"},
                "expect": {"status": 200, "jsonContains": {"status": "UP"}},
            }
        ],
    }
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        attempts += 1
        last = run_business_oracle(base_url, oracle, insecure_test_tls=True)
        if last["status"] == "passed":
            return {"attempts": attempts, "observation": last}
        time.sleep(10)
    raise RuntimeError(f"application health did not become UP: {last}")


def _oracle_outputs(output: Path, provider: str) -> dict[str, Path]:
    return {
        "business": output.with_name(f"course-registration-business-{provider}-20260815.json"),
        "databaseUnavailable": output.with_name(
            f"course-registration-database-unavailable-{provider}-20260815.json"
        ),
        "persistence": output.with_name(
            f"course-registration-persistence-{provider}-20260815.json"
        ),
    }


def run_azure(output: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    group = f"easydep-course-{suffix}"
    recorder = CourseE1Recorder("azure", group, output)
    password = POSTGRES_PASSWORD
    admin_password = "Aa1!" + secrets.token_urlsafe(18)
    certificate, private_key = _certificate_text()
    outputs = _oracle_outputs(output, "azure")
    cleanup: dict[str, Any] = {"passed": False, "residual": []}
    error: str | None = None
    try:
        recorder.step("resource-group.create", lambda: _run([
            "az", "group", "create", "--name", group, "--location", AZURE_LOCATION,
            "--tags", f"easydep-run={group}", "-o", "none",
        ]) or group)
        recorder.step("network.create", lambda: _run([
            "az", "network", "vnet", "create", "-g", group, "-n", "vnet",
            "--address-prefix", "10.92.0.0/16", "--subnet-name", "workloads",
            "--subnet-prefix", "10.92.1.0/24", "-o", "none",
        ]) or "10.92.1.0/24")
        for name in ("app-nsg", "state-nsg"):
            recorder.step(f"{name}.create", lambda name=name: _run([
                "az", "network", "nsg", "create", "-g", group, "-n", name,
                "--tags", f"easydep-run={group}", "-o", "none",
            ]) or name)
        recorder.step("app-nsg.allow-https", lambda: _run([
            "az", "network", "nsg", "rule", "create", "-g", group,
            "--nsg-name", "app-nsg", "-n", "allow-https", "--priority", "100",
            "--access", "Allow", "--protocol", "Tcp", "--source-address-prefixes", "Internet",
            "--destination-port-ranges", "443", "-o", "none",
        ]) or "public tcp/443")

        def create_vm(name: str, nsg: str, *, public_ip_name: str) -> str:
            command = [
                "az", "vm", "create", "-g", group, "-n", name,
                "--location", AZURE_LOCATION, "--image", "Ubuntu2204",
                "--size", AZURE_VM_SIZE, "--vnet-name", "vnet", "--subnet", "workloads",
                "--nsg", nsg, "--admin-username", "easydep", "--authentication-type", "password",
                "--admin-password", admin_password, "--tags", f"easydep-run={group}",
                "--public-ip-address", public_ip_name, "-o", "none",
            ]
            _run(command, timeout=1200)
            return name

        recorder.step(
            "state-vm.create",
            lambda: create_vm("state", "state-nsg", public_ip_name="state-pip"),
        )
        recorder.step(
            "app-vm.create",
            lambda: create_vm("app", "app-nsg", public_ip_name="app-pip"),
        )
        state_private = _run([
            "az", "vm", "show", "-d", "-g", group, "-n", "state",
            "--query", "privateIps", "-o", "tsv",
        ]).strip()
        app_private = _run([
            "az", "vm", "show", "-d", "-g", group, "-n", "app",
            "--query", "privateIps", "-o", "tsv",
        ]).strip()
        app_public = _run([
            "az", "vm", "show", "-d", "-g", group, "-n", "app",
            "--query", "publicIps", "-o", "tsv",
        ]).strip()
        state_public = _run([
            "az", "vm", "show", "-d", "-g", group, "-n", "state",
            "--query", "publicIps", "-o", "tsv",
        ]).strip()
        recorder.step("state-nsg.allow-postgres-from-app", lambda: _run([
            "az", "network", "nsg", "rule", "create", "-g", group,
            "--nsg-name", "state-nsg", "-n", "allow-app-postgres", "--priority", "100",
            "--access", "Allow", "--protocol", "Tcp", "--source-address-prefixes", f"{app_private}/32",
            "--destination-port-ranges", "5432", "-o", "none",
        ]) or f"tcp/5432 from {app_private}/32")
        recorder.step("state-volume.attach", lambda: _run([
            "az", "vm", "disk", "attach", "-g", group, "--vm-name", "state",
            "--name", "state-data", "--new", "--size-gb", "4", "--sku", "Standard_LRS",
            "--lun", "0", "-o", "none",
        ], timeout=900) or "managed data disk LUN 0")
        recorder.step("state-runtime.start", lambda: _az_run(
            group, "state", _state_setup("/dev/disk/azure/scsi1/lun0", password)
        ))
        recorder.step("application-runtime.start", lambda: _az_run(
            group, "app", _app_setup(state_private, password, certificate, private_key)
        ))
        base_url = f"https://{app_public}"
        recorder.document["networkObservation"] = {
            "applicationEndpoint": base_url,
            "databaseEndpointUsedByApp": f"{state_private}:5432",
            "databaseIngressSource": f"{app_private}/32",
            "databasePublicAddressForBootstrap": state_public,
            "databasePortPubliclyAllowed": False,
        }
        recorder.save()
        recorder.step("health.ready", lambda: _wait_health(base_url))
        recorder.step("business-and-concurrency", lambda: _run_oracle_until(
            base_url, BUSINESS_ORACLE, outputs["business"], timeout_seconds=300
        ))
        recorder.step("state-vm.stop", lambda: _run([
            "az", "vm", "stop", "-g", group, "-n", "state", "-o", "none",
        ], timeout=900) or "state VM stopped")
        recorder.step("database-unavailable-health", lambda: _run_oracle_until(
            base_url, DATABASE_UNAVAILABLE_ORACLE, outputs["databaseUnavailable"],
            timeout_seconds=300,
        ))
        recorder.step("state-vm.start", lambda: _run([
            "az", "vm", "start", "-g", group, "-n", "state", "-o", "none",
        ], timeout=900) or "state VM started")
        recorder.step("health.recovered", lambda: _wait_health(base_url))
        recorder.step("persistence-after-state-restart", lambda: _run_oracle_until(
            base_url, PERSISTENCE_ORACLE, outputs["persistence"], timeout_seconds=300
        ))
        outcome = "passed"
    except Exception as exception:
        outcome = "failed"
        error = str(exception)
    finally:
        started = time.monotonic()
        _run(["az", "group", "delete", "--name", group, "--yes", "--no-wait"], check=False)
        exists = True
        deadline = started + 1800
        while time.monotonic() < deadline:
            exists = _run(["az", "group", "exists", "--name", group], check=False).lower() == "true"
            if not exists:
                break
            time.sleep(15)
        cleanup = {
            "passed": not exists,
            "residual": [group] if exists else [],
            "durationSeconds": round(time.monotonic() - started, 3),
        }
        recorder.finish_e1(outcome if cleanup["passed"] else "failed", error=error, cleanup=cleanup)
    return recorder.document


def _gcp_serial(project: str, instance: str) -> str:
    return _run([
        "gcloud", "compute", "instances", "get-serial-port-output", instance,
        "--project", project, "--zone", GCP_ZONE, "--port", "1",
    ], check=False)


def _gcp_delete(command: list[str]) -> None:
    _run(command, timeout=900, check=False)


def run_gcp(output: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    prefix = f"easydep-course-{suffix}"
    names = {
        "network": f"{prefix}-net",
        "subnet": f"{prefix}-subnet",
        "app": f"{prefix}-app",
        "state": f"{prefix}-state",
        "disk": f"{prefix}-data",
        "allowHttps": f"{prefix}-allow-https",
        "allowPostgres": f"{prefix}-allow-postgres",
    }
    project = _run(["gcloud", "config", "get-value", "project"]).strip()
    recorder = CourseE1Recorder("gcp", prefix, output)
    password = POSTGRES_PASSWORD
    certificate, private_key = _certificate_text()
    outputs = _oracle_outputs(output, "gcp")
    cleanup: dict[str, Any] = {"passed": False, "residual": []}
    error: str | None = None
    try:
        recorder.step("network.create", lambda: _run([
            "gcloud", "compute", "networks", "create", names["network"], "--project", project,
            "--subnet-mode", "custom", "--quiet", "--format=value(name)",
        ]) or names["network"])
        recorder.step("subnet.create", lambda: _run([
            "gcloud", "compute", "networks", "subnets", "create", names["subnet"],
            "--project", project, "--network", names["network"], "--region", GCP_REGION,
            "--range", "10.93.1.0/24", "--quiet", "--format=value(name)",
        ]) or "10.93.1.0/24")
        recorder.step("firewall.allow-https", lambda: _run([
            "gcloud", "compute", "firewall-rules", "create", names["allowHttps"],
            "--project", project, "--network", names["network"], "--direction", "INGRESS",
            "--action", "ALLOW", "--rules", "tcp:443", "--source-ranges", "0.0.0.0/0",
            "--target-tags", "easydep-course-app", "--quiet",
        ]) or "public tcp/443")
        recorder.step("firewall.allow-postgres-from-app", lambda: _run([
            "gcloud", "compute", "firewall-rules", "create", names["allowPostgres"],
            "--project", project, "--network", names["network"], "--direction", "INGRESS",
            "--action", "ALLOW", "--rules", "tcp:5432", "--source-tags", "easydep-course-app",
            "--target-tags", "easydep-course-state", "--quiet",
        ]) or "tcp/5432 from app source tag")
        recorder.step("state-disk.create", lambda: _run([
            "gcloud", "compute", "disks", "create", names["disk"], "--project", project,
            "--zone", GCP_ZONE, "--size", "10GB", "--type", "pd-balanced",
            "--labels", f"easydep-run={suffix}", "--quiet", "--format=value(name)",
        ]) or names["disk"])
        with tempfile.TemporaryDirectory(prefix="easydep-course-gcp-") as temporary:
            root = Path(temporary)
            state_script = root / "state.sh"
            state_script.write_text(
                "#!/bin/bash\n" + _state_setup("/dev/disk/by-id/google-course-data", password),
                encoding="utf-8", newline="\n",
            )
            recorder.step("state-vm.create", lambda: _run([
                "gcloud", "compute", "instances", "create", names["state"], "--project", project,
                "--zone", GCP_ZONE, "--machine-type", "e2-small", "--network", names["network"],
                "--subnet", names["subnet"], "--tags", "easydep-course-state",
                "--image-family", "debian-12", "--image-project", "debian-cloud",
                "--boot-disk-size", "10GB", "--disk",
                f"name={names['disk']},device-name=course-data,mode=rw,boot=no,auto-delete=no",
                "--metadata-from-file", f"startup-script={state_script}",
                "--labels", f"easydep-run={suffix}", "--quiet", "--format=value(name)",
            ], timeout=900) or names["state"])
            state_private = _run([
                "gcloud", "compute", "instances", "describe", names["state"],
                "--project", project, "--zone", GCP_ZONE,
                "--format=value(networkInterfaces[0].networkIP)",
            ]).strip()
            app_script = root / "app.sh"
            app_script.write_text(
                "#!/bin/bash\n" + _app_setup(
                    state_private, password, certificate, private_key
                ) + "\necho EASYDEP_COURSE_APP_READY\n",
                encoding="utf-8", newline="\n",
            )
            recorder.step("app-vm.create", lambda: _run([
                "gcloud", "compute", "instances", "create", names["app"], "--project", project,
                "--zone", GCP_ZONE, "--machine-type", "e2-small", "--network", names["network"],
                "--subnet", names["subnet"], "--tags", "easydep-course-app",
                "--image-family", "debian-12", "--image-project", "debian-cloud",
                "--boot-disk-size", "10GB", "--metadata-from-file",
                f"startup-script={app_script}", "--labels", f"easydep-run={suffix}",
                "--quiet", "--format=value(name)",
            ], timeout=900) or names["app"])
        app_public = _run([
            "gcloud", "compute", "instances", "describe", names["app"],
            "--project", project, "--zone", GCP_ZONE,
            "--format=value(networkInterfaces[0].accessConfigs[0].natIP)",
        ]).strip()
        base_url = f"https://{app_public}"
        recorder.document["networkObservation"] = {
            "applicationEndpoint": base_url,
            "databaseEndpointUsedByApp": f"{state_private}:5432",
            "databaseIngressSource": "easydep-course-app source tag",
            "databasePortPubliclyAllowed": False,
        }
        recorder.save()
        try:
            recorder.step("health.ready", lambda: _wait_health(base_url))
        except Exception:
            recorder.document["diagnosticSerial"] = {
                "state": _safe_text(_gcp_serial(project, names["state"])),
                "app": _safe_text(_gcp_serial(project, names["app"])),
            }
            recorder.save()
            raise
        recorder.step("business-and-concurrency", lambda: _run_oracle_until(
            base_url, BUSINESS_ORACLE, outputs["business"], timeout_seconds=300
        ))
        recorder.step("state-vm.stop", lambda: _run([
            "gcloud", "compute", "instances", "stop", names["state"], "--project", project,
            "--zone", GCP_ZONE, "--quiet",
        ], timeout=900) or "state VM stopped")
        recorder.step("database-unavailable-health", lambda: _run_oracle_until(
            base_url, DATABASE_UNAVAILABLE_ORACLE, outputs["databaseUnavailable"],
            timeout_seconds=300,
        ))
        recorder.step("state-vm.start", lambda: _run([
            "gcloud", "compute", "instances", "start", names["state"], "--project", project,
            "--zone", GCP_ZONE, "--quiet",
        ], timeout=900) or "state VM started")
        recorder.step("health.recovered", lambda: _wait_health(base_url))
        recorder.step("persistence-after-state-restart", lambda: _run_oracle_until(
            base_url, PERSISTENCE_ORACLE, outputs["persistence"], timeout_seconds=300
        ))
        outcome = "passed"
    except Exception as exception:
        outcome = "failed"
        error = str(exception)
    finally:
        started = time.monotonic()
        _gcp_delete([
            "gcloud", "compute", "instances", "delete", names["app"], names["state"],
            "--project", project, "--zone", GCP_ZONE, "--quiet",
        ])
        _gcp_delete([
            "gcloud", "compute", "disks", "delete", names["disk"], "--project", project,
            "--zone", GCP_ZONE, "--quiet",
        ])
        for firewall in (names["allowHttps"], names["allowPostgres"]):
            _gcp_delete([
                "gcloud", "compute", "firewall-rules", "delete", firewall,
                "--project", project, "--quiet",
            ])
        _gcp_delete([
            "gcloud", "compute", "networks", "subnets", "delete", names["subnet"],
            "--project", project, "--region", GCP_REGION, "--quiet",
        ])
        _gcp_delete([
            "gcloud", "compute", "networks", "delete", names["network"],
            "--project", project, "--quiet",
        ])
        residual_commands = (
            ["gcloud", "compute", "instances", "list", "--project", project,
             "--filter", f"labels.easydep-run={suffix}", "--format=value(name)"],
            ["gcloud", "compute", "disks", "list", "--project", project,
             "--filter", f"labels.easydep-run={suffix}", "--format=value(name)"],
            ["gcloud", "compute", "firewall-rules", "list", "--project", project,
             "--filter", f"name~'^{prefix}'", "--format=value(name)"],
            ["gcloud", "compute", "networks", "list", "--project", project,
             "--filter", f"name={names['network']}", "--format=value(name)"],
            ["gcloud", "compute", "networks", "subnets", "list", "--project", project,
             "--filter", f"name={names['subnet']}", "--format=value(name)"],
        )
        residual = [
            item.strip()
            for command in residual_commands
            for item in _run(command, check=False).splitlines()
            if item.strip()
        ]
        cleanup = {
            "passed": not residual,
            "residual": residual,
            "durationSeconds": round(time.monotonic() - started, 3),
        }
        recorder.finish_e1(outcome if cleanup["passed"] else "failed", error=error, cleanup=cleanup)
    return recorder.document


RUNNERS: dict[str, Callable[[Path], dict[str, Any]]] = {
    "azure": run_azure,
    "gcp": run_gcp,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=sorted(RUNNERS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = RUNNERS[arguments.provider](arguments.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
