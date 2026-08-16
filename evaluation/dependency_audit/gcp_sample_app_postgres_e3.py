"""Run the domain-neutral state-VM replacement experiment on GCP."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from evaluation.dependency_audit.gcp_sample_app_postgres_e1 import _metadata_url
from evaluation.dependency_audit.inter_vm_postgres_intervention import (
    GCP_REGION,
    GCP_ZONE,
    INSTALL_DOCKER_DEBIAN,
    ExperimentFailure,
    _gcp_wait_for_marker,
    _run,
)
from evaluation.dependency_audit.sample_app_postgres_e1_common import (
    app_build_script,
    baseline_script,
)
from evaluation.dependency_audit.sample_app_postgres_e3_common import (
    E3Recorder,
    app_rebind_from_variable_script,
    app_start_script,
    state_setup_script,
)


def _state_controller(marker: str) -> str:
    install = INSTALL_DOCKER_DEBIAN.replace("set -eux", "set -eu")
    device = """
device=/dev/disk/by-id/google-state-data
for i in $(seq 1 60); do [ -b "$device" ] && break; sleep 2; done
""".strip()
    return "#!/bin/bash\n" + state_setup_script(install, device) + f"\necho '{marker}'\n"


def _app_controller(initial_state_ip: str) -> str:
    install = INSTALL_DOCKER_DEBIAN.replace("set -eux", "set -eu")
    install += "\nsudo apt-get install -y -qq curl"
    metadata = _metadata_url()
    state_metadata = metadata.rsplit("/", 1)[0] + "/easydep-state-ip"
    baseline = baseline_script()
    start = app_start_script(initial_state_ip)
    return f"""#!/bin/bash
set -eu
{app_build_script(install)}
{start}
baseline_passed=0
for i in $(seq 1 60); do
  if (
{baseline}
  ); then
    echo 'EASYDEP_E3 baseline passed'
    baseline_passed=1
    break
  fi
  sleep 5
done
test "$baseline_passed" = 1
while [ "$(curl -fsS -H 'Metadata-Flavor: Google' '{metadata}' || true)" != rebind ]; do
  sleep 2
done
replacement_ip=$(curl -fsS -H 'Metadata-Flavor: Google' '{state_metadata}')
(
{app_rebind_from_variable_script("replacement_ip")}
)
echo 'EASYDEP_E3 rebind-read passed'
"""


def _list_residuals(project: str, prefix: str, suffix: str) -> list[str]:
    commands = (
        ["gcloud", "compute", "instances", "list", "--project", project,
         "--filter", f"labels.easydep-run={suffix}", "--format", "value(name)"],
        ["gcloud", "compute", "disks", "list", "--project", project,
         "--filter", f"labels.easydep-run={suffix}", "--format", "value(name)"],
        ["gcloud", "compute", "firewall-rules", "list", "--project", project,
         "--filter", f"name~'^{prefix}'", "--format", "value(name)"],
        ["gcloud", "compute", "networks", "list", "--project", project,
         "--filter", f"name={prefix}-net", "--format", "value(name)"],
        ["gcloud", "compute", "networks", "subnets", "list", "--project", project,
         "--filter", f"name={prefix}-subnet", "--format", "value(name)"],
    )
    return [
        item
        for command in commands
        for item in _run(command, check=False).splitlines()
        if item.strip()
    ]


def _wait_state_ready(project: str, instance: str, timeout: int = 900) -> str:
    """Use guest readiness plus normal startup completion, not a custom final marker."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        output = _run([
            "gcloud", "compute", "instances", "get-serial-port-output", instance,
            "--project", project, "--zone", GCP_ZONE, "--port", "1",
        ], timeout=60, check=False)
        postgres_ready = "/var/run/postgresql:5432 - accepting connections" in output
        startup_finished = "Finished running startup scripts" in output
        if postgres_ready and startup_finished:
            return "PostgreSQL accepted connections and the startup script completed"
        time.sleep(10)
    raise ExperimentFailure("state guest did not reach PostgreSQL/startup readiness")


def run(output: Path) -> dict[str, Any]:
    suffix = uuid.uuid4().hex[:8]
    prefix = f"easydep-e3-{suffix}"
    network = f"{prefix}-net"
    subnet = f"{prefix}-subnet"
    app = f"{prefix}-app"
    state_a = f"{prefix}-state-a"
    state_b = f"{prefix}-state-b"
    disk = f"{prefix}-data"
    allow_pg = f"{prefix}-allow-pg"
    project = _run(["gcloud", "config", "get-value", "project"]).strip()
    recorder = E3Recorder("gcp", prefix, output)
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
            "--range", "10.83.1.0/24", "--format=value(name)", "--quiet",
        ]) or "10.83.1.0/24")
        recorder.step("firewall.allow-postgres", lambda: _run([
            "gcloud", "compute", "firewall-rules", "create", allow_pg,
            "--project", project, "--network", network, "--direction", "INGRESS",
            "--action", "ALLOW", "--rules", "tcp:5432", "--source-tags", "easydep-app",
            "--target-tags", "easydep-state", "--quiet",
        ]) or "state tcp/5432 from app source tag")
        recorder.step("state-volume.create", lambda: _run([
            "gcloud", "compute", "disks", "create", disk, "--project", project,
            "--zone", GCP_ZONE, "--size", "10GB", "--type", "pd-balanced",
            "--labels", f"easydep-run={suffix}", "--format=value(name)", "--quiet",
        ]) or disk)

        def create_state(name: str, marker: str, temporary: Path) -> str:
            script = temporary / f"{name}.sh"
            script.write_text(_state_controller(marker), encoding="utf-8")
            return _run([
                "gcloud", "compute", "instances", "create", name, "--project", project,
                "--zone", GCP_ZONE, "--machine-type", "e2-small", "--network", network,
                "--subnet", subnet, "--tags", "easydep-state", "--image-family", "debian-12",
                "--image-project", "debian-cloud", "--boot-disk-size", "10GB",
                "--disk", f"name={disk},device-name=state-data,mode=rw,boot=no,auto-delete=no",
                "--metadata-from-file", f"startup-script={script}",
                "--labels", f"easydep-run={suffix}", "--format=value(name)", "--quiet",
            ], timeout=900) or name

        with tempfile.TemporaryDirectory(dir=output.parent) as temporary_name:
            temporary = Path(temporary_name)
            recorder.step(
                "initial.state-vm.create",
                lambda: create_state(state_a, "EASYDEP_E3 state-a-ready", temporary),
            )
            recorder.step(
                "initial.state-ready", lambda: _wait_state_ready(project, state_a)
            )
            state_a_ip = _run([
                "gcloud", "compute", "instances", "describe", state_a,
                "--project", project, "--zone", GCP_ZONE,
                "--format", "value(networkInterfaces[0].networkIP)",
            ]).strip()
            app_script = temporary / "app.sh"
            app_script.write_text(_app_controller(state_a_ip), encoding="utf-8")
            recorder.step("app-vm.create", lambda: _run([
                "gcloud", "compute", "instances", "create", app, "--project", project,
                "--zone", GCP_ZONE, "--machine-type", "e2-small", "--network", network,
                "--subnet", subnet, "--tags", "easydep-app", "--image-family", "debian-12",
                "--image-project", "debian-cloud", "--boot-disk-size", "10GB",
                "--metadata", f"easydep-phase=baseline,easydep-state-ip={state_a_ip}",
                "--metadata-from-file", f"startup-script={app_script}",
                "--labels", f"easydep-run={suffix}", "--format=value(name)", "--quiet",
            ], timeout=900) or app)
            recorder.step("baseline.app-write-read", lambda: _gcp_wait_for_marker(
                project, app, "EASYDEP_E3 baseline passed", timeout=900
            ))
            recorder.step("initial.state-vm.delete", lambda: _run([
                "gcloud", "compute", "instances", "delete", state_a, "--project", project,
                "--zone", GCP_ZONE, "--keep-disks", "data", "--quiet",
            ], timeout=900) or state_a)
            recorder.step("state-volume.available", lambda: _wait_disk_ready(project, disk))
            recorder.step(
                "replacement.state-vm.create",
                lambda: create_state(state_b, "EASYDEP_E3 state-b-ready", temporary),
            )
            recorder.step(
                "replacement.state-volume.attach-existing",
                lambda: "same disk supplied to replacement create command",
            )
            recorder.step(
                "replacement.state-ready", lambda: _wait_state_ready(project, state_b)
            )
            state_b_ip = _run([
                "gcloud", "compute", "instances", "describe", state_b,
                "--project", project, "--zone", GCP_ZONE,
                "--format", "value(networkInterfaces[0].networkIP)",
            ]).strip()
            if state_a_ip == state_b_ip:
                raise ExperimentFailure("replacement state VM unexpectedly reused the same private IP")
            recorder.document["runtimeEndpointObservation"] = {
                "initialStatePrivateIp": state_a_ip,
                "replacementStatePrivateIp": state_b_ip,
                "appVmRecreated": False,
                "appImageRebuilt": False,
            }
            recorder.save()
            recorder.step("replacement.app-rebind-signal", lambda: _run([
                "gcloud", "compute", "instances", "add-metadata", app,
                "--project", project, "--zone", GCP_ZONE,
                "--metadata", f"easydep-phase=rebind,easydep-state-ip={state_b_ip}", "--quiet",
            ]) or "runtime endpoint metadata updated")
            recorder.step(
                "replacement.app-rebind-without-rebuild",
                lambda: _gcp_wait_for_marker(
                    project, app, "EASYDEP_E3 rebind-read passed", timeout=900
                ),
            )
            recorder.step(
                "replacement.app-read-existing-value",
                lambda: "same-image rebind oracle read the pre-replacement value",
            )
        outcome = "passed"
    except Exception as exception:
        outcome = "failed"
        error = str(exception)
    finally:
        _run([
            "gcloud", "compute", "instances", "delete", app, state_a, state_b,
            "--project", project, "--zone", GCP_ZONE, "--quiet",
        ], timeout=900, check=False)
        _run([
            "gcloud", "compute", "disks", "delete", disk, "--project", project,
            "--zone", GCP_ZONE, "--quiet",
        ], timeout=600, check=False)
        _run([
            "gcloud", "compute", "firewall-rules", "delete", allow_pg,
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
        residual = _list_residuals(project, prefix, suffix)
        cleanup = {"passed": not residual, "residual": residual}
        recorder.finish_e3(
            outcome if cleanup["passed"] else "failed", error=error, cleanup=cleanup
        )
    return recorder.document


def _wait_disk_ready(project: str, disk: str, timeout: int = 300) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        users = _run([
            "gcloud", "compute", "disks", "describe", disk, "--project", project,
            "--zone", GCP_ZONE, "--format", "value(users)",
        ]).strip()
        if not users:
            return "persistent disk detached and reusable"
        time.sleep(5)
    raise ExperimentFailure("persistent disk did not become reusable after state VM deletion")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = run(arguments.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
