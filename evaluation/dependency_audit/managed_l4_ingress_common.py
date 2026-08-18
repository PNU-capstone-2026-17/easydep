"""세 CSP의 관리형 L4 진입 경로를 같은 중립 앱으로 검증하는 공통 도구."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from evaluation.dependency_audit.inter_vm_postgres_intervention import (
    ExperimentFailure,
    Recorder,
    _safe_text,
)

SAMPLE_SERVICE = Path(__file__).with_name("sample_app") / "l4_service.py"


def http_request(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=12) as response:  # noqa: S310 - run-owned endpoint
            return {
                "status": response.status,
                "body": json.loads(response.read() or b"{}"),
            }
    except HTTPError as error:
        return {"status": error.code, "body": json.loads(error.read() or b"{}")}


def wait_http(
    method: str,
    url: str,
    expected_status: int,
    *,
    payload: dict[str, Any] | None = None,
    budget: int = 300,
) -> dict[str, Any]:
    deadline = time.monotonic() + budget
    last: dict[str, Any] | str = "no response"
    while time.monotonic() < deadline:
        try:
            last = http_request(method, url, payload)
            if last["status"] == expected_status:
                return last
        except (OSError, URLError, TimeoutError, json.JSONDecodeError) as error:
            last = str(error)
        time.sleep(5)
    raise ExperimentFailure(f"HTTP {expected_status} not observed for {url}: {last}")


class ManagedL4Recorder(Recorder):
    def __init__(self, provider: str, run_id: str, output: Path) -> None:
        super().__init__(provider, run_id, output)
        self.document |= {
            "schemaVersion": "easydep-managed-l4-ingress/v1",
            "transportUnderTest": "public TCP/80 L4 load balancer to two HTTP backends",
            "pathUnderTest": (
                "public IPv4 -> TCP listener/forwarding rule -> backend membership -> "
                "HTTP readiness probe -> VM application process"
            ),
            "scope": "domain-neutral L4 ingress dependency experiment",
            "application": {
                "kind": "domain-neutral synthetic HTTP service",
                "endpoints": ["/health/ready", "/instance", "/__easydep_test/fault"],
                "database": False,
                "persistentStorage": False,
            },
        }
        self.save()

    def finish_l4(
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
                "One development run does not establish a provider-wide success rate or SLA.",
                "HTTP is only the observable payload carried over the L4 TCP path.",
                "The experiment does not test TLS, database, persistent storage, or region failure.",
                "Backend process recovery is operator-triggered; managed VM replacement is separate.",
                "Sequential requests demonstrate reachability, not traffic-share fairness or performance.",
            ],
        }
        self.save()


def startup_script(*, port: int, fault_token: str) -> str:
    encoded = base64.b64encode(SAMPLE_SERVICE.read_bytes()).decode("ascii")
    return f"""#!/bin/bash
set -eu
mkdir -p /opt/easydep-l4
echo '{encoded}' | base64 -d > /opt/easydep-l4/service.py
cat > /usr/local/bin/easydep-l4-start <<'EASYDEP_START'
#!/bin/bash
set -eu
pkill -f '/opt/easydep-l4/service.py' 2>/dev/null || true
nohup python3 /opt/easydep-l4/service.py --port {port} \
  --instance "$(hostname)" --fault-token '{fault_token}' \
  >/var/log/easydep-l4.log 2>&1 &
EASYDEP_START
chmod +x /usr/local/bin/easydep-l4-start
/usr/local/bin/easydep-l4-start
"""


def wait_for_instances(
    base_url: str,
    expected: set[str],
    *,
    timeout: int,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    observed: set[str] = set()
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            response = http_request("GET", f"{base_url}/instance")
            if response["status"] == HTTPStatus.OK:
                instance = str(response.get("body", {}).get("instance") or "")
                if instance:
                    observed.add(instance)
        except Exception:  # noqa: BLE001 - transient LB convergence is measured below.
            pass
        if expected <= observed:
            return {"expected": sorted(expected), "observed": sorted(observed), "attempts": attempts}
        time.sleep(1)
    raise ExperimentFailure(
        f"not all L4 backends were reachable: expected={sorted(expected)}, "
        f"observed={sorted(observed)}"
    )


def exercise_fault_exclusion_and_restore(
    base_url: str,
    expected: set[str],
    fault_token: str,
    restore: Callable[[str], Any],
    *,
    exclusion_timeout: int = 240,
    restore_timeout: int = 600,
) -> dict[str, Any]:
    started = time.monotonic()
    fault = wait_http(
        "POST",
        f"{base_url}/__easydep_test/fault",
        HTTPStatus.ACCEPTED,
        payload={"token": fault_token},
        budget=60,
    )
    victim = str(fault.get("body", {}).get("instance") or "")
    if victim not in expected:
        raise ExperimentFailure(f"fault endpoint returned an unknown backend: {victim!r}")
    survivor = next(iter(expected - {victim}))

    observations: list[dict[str, Any]] = []
    stable_successes: list[str] = []
    deadline = time.monotonic() + exclusion_timeout
    while time.monotonic() < deadline:
        observation: dict[str, Any] = {
            "atSeconds": round(time.monotonic() - started, 3),
            "status": None,
            "instance": None,
        }
        try:
            response = http_request("GET", f"{base_url}/instance")
            observation["status"] = response["status"]
            observation["instance"] = response.get("body", {}).get("instance")
        except Exception as exception:  # noqa: BLE001 - failures are part of the observation.
            observation["error"] = _safe_text(str(exception))
        observations.append(observation)
        if observation["status"] == HTTPStatus.OK and observation["instance"] == survivor:
            stable_successes.append(survivor)
        else:
            stable_successes.clear()
        if len(stable_successes) >= 10:
            break
        time.sleep(1)
    if len(stable_successes) < 10:
        raise ExperimentFailure(
            "the L4 path did not converge to ten consecutive successful survivor responses"
        )
    excluded_at = round(time.monotonic() - started, 3)

    restore_detail = restore(victim)
    restored = wait_for_instances(base_url, expected, timeout=restore_timeout)
    restored_at = round(time.monotonic() - started, 3)
    passed = sum(1 for item in observations if item["status"] == HTTPStatus.OK)
    return {
        "victim": victim,
        "survivor": survivor,
        "faultAcceptedAtSeconds": 0,
        "exclusionConfirmedAtSeconds": excluded_at,
        "restorationConfirmedAtSeconds": restored_at,
        "probeCountBeforeRestore": len(observations),
        "successfulProbesBeforeRestore": passed,
        "failedProbesBeforeRestore": len(observations) - passed,
        "restoreAction": restore_detail,
        "restoredBackends": restored,
    }
