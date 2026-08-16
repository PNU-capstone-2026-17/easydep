"""도메인 중립 앱으로 GCP MIG·HTTP LB·autohealing E2 경로를 검증한다."""

from __future__ import annotations

import argparse
import json
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any

from evaluation.dependency_audit.aws_sample_app_postgres_e1 import _wait_http
from evaluation.dependency_audit.aws_sample_app_postgres_e2 import (
    FUNCTIONAL_RECOVERY_BUDGET_SECONDS,
    HEALTH_CHECK_INTERVAL_SECONDS,
    UNHEALTHY_THRESHOLD_COUNT,
    _continuity_summary,
    _continuous_business_probe,
)
from evaluation.dependency_audit.gcp_sample_app_postgres_e1 import _state_controller
from evaluation.dependency_audit.inter_vm_postgres_intervention import (
    GCP_REGION,
    GCP_ZONE,
    INSTALL_DOCKER_DEBIAN,
    POSTGRES_PASSWORD,
    ExperimentFailure,
    Recorder,
    _run,
    _safe_text,
)
from evaluation.dependency_audit.sample_app_postgres_e1_common import (
    APP_IMAGE,
    app_build_script,
)


class GcpE2Recorder(Recorder):
    def __init__(self, run_id: str, output: Path) -> None:
        super().__init__("gcp", run_id, output)
        self.document |= {
            "schemaVersion": "easydep-gcp-sample-app-postgres-e2/v1",
            "transportUnderTest": (
                "public HTTP forwarding rule to a managed instance group of app VM "
                "containers, then private PostgreSQL"
            ),
            "pathUnderTest": (
                "forwarding rule -> target HTTP proxy -> URL map -> backend service health -> "
                "managed app VM group (2) -> state VM private IPv4:5432 -> persistent disk"
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
                "The test validates app-tier managed autohealing, not state-tier high availability.",
                "Public HTTP is used; trusted HTTPS, DNS ownership, and certificates are not tested.",
                "One zonal development run does not establish a GCP-wide success rate or an SLA.",
                "Sequential probes are a functional continuity signal, not a performance load test.",
            ],
        }
        self.save()


def _app_controller(state_private: str, fault_token: str) -> str:
    install = INSTALL_DOCKER_DEBIAN.replace("set -eux", "set -eu")
    install += "\nsudo apt-get install -y -qq curl"
    return f"""#!/bin/bash
set -eu
{app_build_script(install)}
sudo docker rm -f easydep-app >/dev/null 2>&1 || true
sudo docker run -d --name easydep-app -p 8080:8080 \
  -e DATABASE_URL='postgresql://postgres:{POSTGRES_PASSWORD}@{state_private}:5432/postgres' \
  -e EASYDEP_TEST_FAULT_TOKEN='{fault_token}' \
  -e EASYDEP_TEST_INSTANCE_ID="$(hostname)" \
  {APP_IMAGE} --role postgres-app
for i in $(seq 1 90); do
  status=$(curl -sS -o /tmp/health.json -w '%{{http_code}}' http://127.0.0.1:8080/health/ready || true)
  if [ "$status" = 200 ]; then echo 'EASYDEP_E2 app-ready passed'; break; fi
  sleep 5
done
test "$status" = 200
"""


def _backend_health(project: str, backend: str) -> list[dict[str, str]]:
    value = json.loads(_run([
        "gcloud", "compute", "backend-services", "get-health", backend,
        "--project", project, "--global", "--format=json",
    ]) or "[]")
    observations: list[dict[str, str]] = []
    for group in value:
        for health in group.get("status", {}).get("healthStatus", []):
            observations.append({
                "instance": str(health.get("instance") or "").rsplit("/", 1)[-1],
                "state": str(health.get("healthState") or ""),
            })
    return observations


def _wait_healthy(project: str, backend: str, expected: int, *, timeout: int) -> list[str]:
    deadline = time.monotonic() + timeout
    last: list[dict[str, str]] = []
    while time.monotonic() < deadline:
        last = _backend_health(project, backend)
        healthy = [item["instance"] for item in last if item["state"] == "HEALTHY"]
        if len(healthy) == expected:
            return sorted(healthy)
        time.sleep(10)
    raise ExperimentFailure(f"GCP healthy backend count {expected} not observed: {last}")


def _state_ready_from_serial(output: str) -> bool:
    return "EASYDEP_E1 state-ready passed" in output or (
        "accepting connections" in output
        and "Finished running startup scripts" in output
        and "Failed to run startup scripts" not in output
    )


def _wait_state_ready(project: str, instance: str, *, timeout: int = 600) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    last_output = ""
    while time.monotonic() < deadline:
        last_output = _run([
            "gcloud", "compute", "instances", "get-serial-port-output", instance,
            "--project", project, "--zone", GCP_ZONE, "--port", "1",
        ], timeout=60, check=False)
        if _state_ready_from_serial(last_output):
            return {
                "postgres": "accepting connections",
                "startupScript": "finished",
            }
        time.sleep(10)
    raise ExperimentFailure(
        f"GCP state readiness evidence not observed: {_safe_text(last_output)}"
    )


def _instance_observation(project: str, instance: str) -> dict[str, str]:
    value = json.loads(_run([
        "gcloud", "compute", "instances", "describe", instance,
        "--project", project, "--zone", GCP_ZONE,
        "--format=json(id,lastStartTimestamp)",
    ]) or "{}")
    return {
        "id": str(value.get("id") or ""),
        "lastStartTimestamp": str(value.get("lastStartTimestamp") or ""),
    }


def run(output: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    prefix = f"easydep-e2-{suffix}"
    network = f"{prefix}-net"
    subnet = f"{prefix}-subnet"
    state = f"{prefix}-state"
    disk = f"{prefix}-data"
    template = f"{prefix}-template"
    mig = f"{prefix}-mig"
    health_check = f"{prefix}-hc"
    backend = f"{prefix}-backend"
    url_map = f"{prefix}-map"
    proxy = f"{prefix}-proxy"
    forwarding = f"{prefix}-forwarding"
    allow_pg = f"{prefix}-allow-pg"
    allow_lb = f"{prefix}-allow-lb"
    project = _run(["gcloud", "config", "get-value", "project"]).strip()
    recorder = GcpE2Recorder(prefix, output)
    fault_token = uuid.uuid4().hex
    error: str | None = None
    cleanup: dict[str, Any] = {"passed": False, "residual": []}
    try:
        recorder.step("network.create", lambda: _run([
            "gcloud", "compute", "networks", "create", network, "--project", project,
            "--subnet-mode", "custom", "--format=value(name)", "--quiet",
        ]) or network)
        recorder.step("subnet.create", lambda: _run([
            "gcloud", "compute", "networks", "subnets", "create", subnet,
            "--project", project, "--network", network, "--region", GCP_REGION,
            "--range", "10.82.1.0/24", "--format=value(name)", "--quiet",
        ]) or subnet)

        def allow_postgres() -> str:
            _run([
                "gcloud", "compute", "firewall-rules", "create", allow_pg,
                "--project", project, "--network", network, "--direction", "INGRESS",
                "--action", "ALLOW", "--rules", "tcp:5432", "--source-tags", "easydep-app",
                "--target-tags", "easydep-state", "--quiet",
            ])
            return "state tcp/5432 from app source tag"

        recorder.step("firewall.allow-postgres", allow_postgres)
        recorder.step("firewall.allow-google-health", lambda: _run([
            "gcloud", "compute", "firewall-rules", "create", allow_lb,
            "--project", project, "--network", network, "--direction", "INGRESS",
            "--action", "ALLOW", "--rules", "tcp:8080",
            "--source-ranges", "130.211.0.0/22,35.191.0.0/16",
            "--target-tags", "easydep-app", "--quiet",
        ]) or "Google health checker and proxy ranges to app tcp/8080")
        recorder.step("state-disk.create", lambda: _run([
            "gcloud", "compute", "disks", "create", disk, "--project", project,
            "--zone", GCP_ZONE, "--size", "10GB", "--type", "pd-balanced",
            "--labels", f"easydep-run={suffix}", "--format=value(name)", "--quiet",
        ]) or disk)
        with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
            state_script = Path(temporary) / "state.sh"
            state_script.write_text(_state_controller(), encoding="utf-8")
            recorder.step("state-vm.create", lambda: _run([
                "gcloud", "compute", "instances", "create", state, "--project", project,
                "--zone", GCP_ZONE, "--machine-type", "e2-small", "--network", network,
                "--subnet", subnet, "--tags", "easydep-state", "--image-family", "debian-12",
                "--image-project", "debian-cloud", "--boot-disk-size", "10GB",
                "--disk", f"name={disk},device-name=state-data,mode=rw,boot=no,auto-delete=no",
                "--metadata-from-file", f"startup-script={state_script}",
                "--labels", f"easydep-run={suffix}", "--format=value(name)", "--quiet",
            ], timeout=900) or state)
            state_private = _run([
                "gcloud", "compute", "instances", "describe", state,
                "--project", project, "--zone", GCP_ZONE,
                "--format=value(networkInterfaces[0].networkIP)",
            ]).strip()
            recorder.step("state-vm.postgres-ready", lambda: _wait_state_ready(
                project, state, timeout=600
            ))
            app_script = Path(temporary) / "app.sh"
            app_script.write_text(
                _app_controller(state_private, fault_token), encoding="utf-8"
            )
            subnet_path = (
                f"projects/{project}/regions/{GCP_REGION}/subnetworks/{subnet}"
            )
            recorder.step("app-group.template", lambda: _run([
                "gcloud", "compute", "instance-templates", "create", template,
                "--project", project, "--machine-type", "e2-small", "--network", network,
                "--subnet", subnet_path, "--tags", "easydep-app", "--image-family", "debian-12",
                "--image-project", "debian-cloud", "--boot-disk-size", "10GB",
                "--metadata-from-file", f"startup-script={app_script}",
                "--labels", f"easydep-run={suffix}", "--format=value(name)", "--quiet",
            ], timeout=300) or template)
        recorder.step("health-check.create", lambda: _run([
            "gcloud", "compute", "health-checks", "create", "http", health_check,
            "--project", project, "--port", "8080", "--request-path", "/health/ready",
            "--check-interval", f"{HEALTH_CHECK_INTERVAL_SECONDS}s", "--timeout", "5s",
            "--healthy-threshold", "2", "--unhealthy-threshold",
            str(UNHEALTHY_THRESHOLD_COUNT), "--quiet",
        ]) or health_check)
        recorder.step("app-group.create", lambda: _run([
            "gcloud", "compute", "instance-groups", "managed", "create", mig,
            "--project", project, "--zone", GCP_ZONE, "--template", template,
            "--base-instance-name", f"e2-{suffix}-app", "--size", "2", "--quiet",
        ], timeout=600) or "managed instance group size 2")
        recorder.step("app-group.named-port", lambda: _run([
            "gcloud", "compute", "instance-groups", "managed", "set-named-ports", mig,
            "--project", project, "--zone", GCP_ZONE, "--named-ports", "http:8080", "--quiet",
        ]) or "http:8080")
        recorder.step("load-balancer.backend-service", lambda: _run([
            "gcloud", "compute", "backend-services", "create", backend,
            "--project", project, "--global", "--protocol", "HTTP",
            "--port-name", "http", "--health-checks", health_check, "--quiet",
        ]) or backend)
        recorder.step("load-balancer.add-managed-group", lambda: _run([
            "gcloud", "compute", "backend-services", "add-backend", backend,
            "--project", project, "--global", "--instance-group", mig,
            "--instance-group-zone", GCP_ZONE, "--balancing-mode", "UTILIZATION",
            "--max-utilization", "0.8", "--quiet",
        ]) or "managed instance group attached")
        recorder.step("load-balancer.url-map", lambda: _run([
            "gcloud", "compute", "url-maps", "create", url_map,
            "--project", project, "--default-service", backend, "--quiet",
        ]) or url_map)
        recorder.step("load-balancer.http-proxy", lambda: _run([
            "gcloud", "compute", "target-http-proxies", "create", proxy,
            "--project", project, "--url-map", url_map, "--quiet",
        ]) or proxy)
        recorder.step("load-balancer.forwarding-rule", lambda: _run([
            "gcloud", "compute", "forwarding-rules", "create", forwarding,
            "--project", project, "--global", "--target-http-proxy", proxy,
            "--ports", "80", "--quiet",
        ]) or forwarding)
        public_ip = _run([
            "gcloud", "compute", "forwarding-rules", "describe", forwarding,
            "--project", project, "--global", "--format=value(IPAddress)",
        ]).strip()
        healthy_instances = recorder.step("baseline.two-healthy-backends", lambda: _wait_healthy(
            project, backend, 2, timeout=900
        ))
        base_url = f"http://{public_ip}"
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
        recorder.step("app-group.enable-autohealing", lambda: _run([
            "gcloud", "compute", "instance-groups", "managed", "update", mig,
            "--project", project, "--zone", GCP_ZONE, "--health-check", health_check,
            "--initial-delay", "180", "--quiet",
        ]) or "MIG autohealing uses the HTTP health check")
        initial_instances = {
            instance: _instance_observation(project, instance)
            for instance in healthy_instances
        }

        def health_fault_and_recovery() -> dict[str, Any]:
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
                fault_response = _wait_http(
                    "POST", f"{base_url}/__easydep_test/fault", HTTPStatus.ACCEPTED,
                    payload={"token": fault_token}, budget=30,
                )
                milestones["faultAccepted"] = round(time.monotonic() - started, 3)
                reported_instance = str(fault_response.get("body", {}).get("instance") or "")
                victim = reported_instance if reported_instance in initial_instances else ""
                unhealthy_seen = False
                recovery: dict[str, str] | None = None
                deadline = time.monotonic() + 1200
                while time.monotonic() < deadline:
                    health = _backend_health(project, backend)
                    unhealthy_initial = [
                        item["instance"] for item in health
                        if item["instance"] in initial_instances
                        and item["state"] != "HEALTHY"
                    ]
                    if victim:
                        unhealthy_initial = [
                            name for name in unhealthy_initial if name == victim
                        ]
                    if unhealthy_initial and not unhealthy_seen:
                        unhealthy_seen = True
                        victim = unhealthy_initial[0]
                        milestones["victimUnhealthy"] = round(time.monotonic() - started, 3)
                    current_names = [line.strip() for line in _run([
                        "gcloud", "compute", "instance-groups", "managed", "list-instances", mig,
                        "--project", project, "--zone", GCP_ZONE,
                        "--format=value(instance.basename())",
                    ]).splitlines() if line.strip()]
                    current_instances: dict[str, dict[str, str]] = {}
                    for name in current_names:
                        try:
                            current_instances[name] = _instance_observation(project, name)
                        except ExperimentFailure:
                            continue
                    healthy = [item["instance"] for item in health if item["state"] == "HEALTHY"]
                    changed = next((
                        (name, observation) for name, observation in current_instances.items()
                        if name not in initial_instances
                        or observation != initial_instances[name]
                    ), None)
                    if unhealthy_seen and changed and len(healthy) == 2:
                        recovered_name, recovered = changed
                        before = initial_instances.get(recovered_name)
                        recovery = {
                            "instance": recovered_name,
                            "instanceId": recovered["id"],
                            "lastStartTimestamp": recovered["lastStartTimestamp"],
                            "action": (
                                "restart-in-place"
                                if before and before["id"] == recovered["id"]
                                else "replacement"
                            ),
                        }
                        milestones["managedRecoveryHealthy"] = round(
                            time.monotonic() - started, 3
                        )
                        break
                    time.sleep(10)
                if not unhealthy_seen:
                    raise ExperimentFailure("GCP backend did not mark the stopped app unhealthy")
                if not recovery:
                    raise ExperimentFailure("GCP MIG did not complete a healthy managed recovery")
            finally:
                stop.set()
                thread.join(timeout=20)
            continuity = _continuity_summary(observations)
            unhealthy_at = milestones["victimUnhealthy"]
            recovery_at = milestones["managedRecoveryHealthy"]
            successes_during = sum(
                1 for item in observations
                if unhealthy_at <= float(item["atSeconds"]) <= recovery_at
                and item.get("status") == HTTPStatus.OK
                and item.get("valueKept")
            )
            if not successes_during:
                raise ExperimentFailure(f"no successful request during GCP recovery: {continuity}")
            if continuity["maxConsecutiveFailureSeconds"] > FUNCTIONAL_RECOVERY_BUDGET_SECONDS:
                raise ExperimentFailure(
                    "GCP functional recovery exceeded the health-derived budget: "
                    f"budget={FUNCTIONAL_RECOVERY_BUDGET_SECONDS}, observed={continuity}"
                )
            return {
                "victimInstance": victim,
                "victimInstanceId": initial_instances[victim]["id"],
                "managedRecovery": recovery,
                "faultRequest": fault_response,
                "milestonesSeconds": milestones,
                "healthDerivedFunctionalRecoveryBudgetSeconds": (
                    FUNCTIONAL_RECOVERY_BUDGET_SECONDS
                ),
                "businessContinuity": continuity,
                "successfulRequestsDuringManagedRecovery": successes_during,
            }

        recorder.step("fault.health-based-managed-recovery", health_fault_and_recovery)
        recorder.step("recovery.business-read", lambda: _wait_http(
            "GET", f"{base_url}/records/evidence", HTTPStatus.OK, budget=120
        ))
        recorder.document["availabilityObservation"] = {
            "scope": "application tier",
            "stateTier": "single state VM; not highly available",
            "failureInjection": "stop one app process and let backend health feed MIG autohealing",
            "groupScope": "single zone; zone failure is not tested",
        }
        recorder.save()
        outcome = "passed"
    except Exception as exception:
        outcome = "failed"
        error = str(exception)
    finally:
        _run([
            "gcloud", "compute", "forwarding-rules", "delete", forwarding,
            "--project", project, "--global", "--quiet",
        ], check=False)
        _run([
            "gcloud", "compute", "target-http-proxies", "delete", proxy,
            "--project", project, "--quiet",
        ], check=False)
        _run([
            "gcloud", "compute", "url-maps", "delete", url_map,
            "--project", project, "--quiet",
        ], check=False)
        _run([
            "gcloud", "compute", "backend-services", "delete", backend,
            "--project", project, "--global", "--quiet",
        ], check=False)
        _run([
            "gcloud", "compute", "instance-groups", "managed", "delete", mig,
            "--project", project, "--zone", GCP_ZONE, "--quiet",
        ], timeout=900, check=False)
        _run([
            "gcloud", "compute", "instance-templates", "delete", template,
            "--project", project, "--quiet",
        ], check=False)
        _run([
            "gcloud", "compute", "health-checks", "delete", health_check,
            "--project", project, "--quiet",
        ], check=False)
        _run([
            "gcloud", "compute", "instances", "delete", state, "--project", project,
            "--zone", GCP_ZONE, "--quiet",
        ], timeout=900, check=False)
        _run([
            "gcloud", "compute", "disks", "delete", disk, "--project", project,
            "--zone", GCP_ZONE, "--quiet",
        ], timeout=600, check=False)
        for rule in (allow_pg, allow_lb):
            _run([
                "gcloud", "compute", "firewall-rules", "delete", rule,
                "--project", project, "--quiet",
            ], check=False)
        _run([
            "gcloud", "compute", "networks", "subnets", "delete", subnet,
            "--project", project, "--region", GCP_REGION, "--quiet",
        ], check=False)
        _run([
            "gcloud", "compute", "networks", "delete", network,
            "--project", project, "--quiet",
        ], check=False)
        residual_queries = (
            ["gcloud", "compute", "instances", "list", "--project", project,
             "--filter", f"labels.easydep-run={suffix}", "--format=value(name)"],
            ["gcloud", "compute", "disks", "list", "--project", project,
             "--filter", f"labels.easydep-run={suffix}", "--format=value(name)"],
            ["gcloud", "compute", "instance-templates", "list", "--project", project,
             "--filter", f"name={template}", "--format=value(name)"],
            ["gcloud", "compute", "instance-groups", "managed", "list", "--project", project,
             "--filter", f"name={mig}", "--format=value(name)"],
            ["gcloud", "compute", "forwarding-rules", "list", "--project", project,
             "--filter", f"name={forwarding}", "--format=value(name)"],
            ["gcloud", "compute", "networks", "list", "--project", project,
             "--filter", f"name={network}", "--format=value(name)"],
        )
        residual = [
            item
            for command in residual_queries
            for item in _run(command, check=False).splitlines()
            if item.strip()
        ]
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
