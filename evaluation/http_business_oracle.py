"""Run a declarative black-box business oracle against a local or cloud HTTP endpoint."""

from __future__ import annotations

import argparse
import json
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "easydep-http-business-oracle/v1"


def _contains(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _contains(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_contains(left, right) for left, right in zip(actual, expected, strict=True))
        )
    return actual == expected


def _request(
    base_url: str,
    spec: dict[str, Any],
    *,
    timeout_seconds: float,
    ssl_context: ssl.SSLContext | None = None,
) -> dict[str, Any]:
    method = str(spec.get("method") or "GET").upper()
    body = spec.get("json")
    request = urllib.request.Request(
        urllib.parse.urljoin(base_url.rstrip("/") + "/", str(spec["path"]).lstrip("/")),
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(  # noqa: S310
            request,
            timeout=timeout_seconds,
            context=ssl_context,
        ) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return {
                "httpStatus": response.status,
                "json": json.loads(raw) if raw else None,
                "transportError": None,
            }
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = None
        return {
            "httpStatus": error.code,
            "json": payload,
            "transportError": None,
        }
    except (OSError, ValueError, urllib.error.URLError) as error:
        return {
            "httpStatus": None,
            "json": None,
            "transportError": f"{type(error).__name__}: {error}",
        }


def _request_passed(observed: dict[str, Any], expected: dict[str, Any]) -> bool:
    statuses = expected.get("statuses")
    if statuses is None:
        statuses = [expected.get("status", 200)]
    if observed.get("httpStatus") not in {int(item) for item in statuses}:
        return False
    if "jsonContains" in expected and not _contains(
        observed.get("json"), expected["jsonContains"]
    ):
        return False
    if "jsonContainsItems" in expected:
        payload = observed.get("json")
        expected_items = expected["jsonContainsItems"]
        if not isinstance(payload, list) or not isinstance(expected_items, list):
            return False
        remaining = list(payload)
        for expected_item in expected_items:
            match = next(
                (index for index, actual_item in enumerate(remaining) if _contains(actual_item, expected_item)),
                None,
            )
            if match is None:
                return False
            remaining.pop(match)
    if "jsonLength" in expected:
        payload = observed.get("json")
        if not isinstance(payload, list) or len(payload) != int(expected["jsonLength"]):
            return False
    return observed.get("transportError") is None


def _run_single(
    base_url: str,
    phase: dict[str, Any],
    timeout_seconds: float,
    ssl_context: ssl.SSLContext | None,
) -> dict[str, Any]:
    observed = _request(
        base_url,
        phase["request"],
        timeout_seconds=timeout_seconds,
        ssl_context=ssl_context,
    )
    expected = dict(phase.get("expect") or {})
    passed = _request_passed(observed, expected)
    return {
        "id": phase["id"],
        "kind": "request",
        "status": "passed" if passed else "failed",
        "observed": observed,
        "expected": expected,
    }


def _run_concurrent(
    base_url: str,
    phase: dict[str, Any],
    timeout_seconds: float,
    ssl_context: ssl.SSLContext | None,
) -> dict[str, Any]:
    requests = list(phase.get("requests") or [])
    if len(requests) < 2:
        raise ValueError(f"concurrent phase requires at least two requests: {phase.get('id')}")
    barrier = threading.Barrier(len(requests))

    def send(spec: dict[str, Any]) -> dict[str, Any]:
        barrier.wait(timeout=timeout_seconds)
        return _request(
            base_url,
            spec,
            timeout_seconds=timeout_seconds,
            ssl_context=ssl_context,
        )

    with ThreadPoolExecutor(max_workers=len(requests)) as executor:
        observed = list(executor.map(send, requests))
    status_counts = Counter(str(item.get("httpStatus")) for item in observed)
    expected = dict(phase.get("expect") or {})
    expected_counts = {
        str(code): int(count) for code, count in (expected.get("statusCounts") or {}).items()
    }
    transport_errors = [item["transportError"] for item in observed if item["transportError"]]
    passed = dict(status_counts) == expected_counts and not transport_errors
    return {
        "id": phase["id"],
        "kind": "concurrentRequests",
        "status": "passed" if passed else "failed",
        "observedStatusCounts": dict(sorted(status_counts.items())),
        "expectedStatusCounts": expected_counts,
        "transportErrors": transport_errors,
        "responses": observed,
    }


def run_business_oracle(
    base_url: str,
    oracle: dict[str, Any],
    *,
    insecure_test_tls: bool = False,
) -> dict[str, Any]:
    if oracle.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError(f"unsupported business oracle schema: {oracle.get('schemaVersion')}")
    phases = list(oracle.get("phases") or [])
    if not phases:
        raise ValueError("business oracle has no phases")
    timeout_seconds = float(oracle.get("requestTimeoutSeconds", 10))
    is_https = urllib.parse.urlparse(base_url).scheme.lower() == "https"
    ssl_context = None
    if insecure_test_tls:
        if not is_https:
            raise ValueError("insecure_test_tls is only valid for an HTTPS test endpoint")
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
    results: list[dict[str, Any]] = []
    for phase in phases:
        kind = str(phase.get("kind") or "request")
        if kind == "request":
            result = _run_single(base_url, phase, timeout_seconds, ssl_context)
        elif kind == "concurrentRequests":
            result = _run_concurrent(base_url, phase, timeout_seconds, ssl_context)
        else:
            raise ValueError(f"unsupported business oracle phase kind: {kind}")
        results.append(result)
        if result["status"] != "passed" and bool(phase.get("stopOnFailure", True)):
            break
    return {
        "schemaVersion": SCHEMA_VERSION,
        "oracleId": oracle.get("oracleId"),
        "baseUrl": base_url,
        "observedAt": datetime.now(UTC).isoformat(),
        "tlsVerification": (
            "disabled-for-synthetic-test"
            if insecure_test_tls
            else "system-trust"
            if is_https
            else "not-applicable"
        ),
        "status": (
            "passed"
            if len(results) == len(phases)
            and all(item["status"] == "passed" for item in results)
            else "failed"
        ),
        "passedPhases": sum(item["status"] == "passed" for item in results),
        "totalPhases": len(phases),
        "executedPhases": len(results),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--oracle", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--insecure-test-tls",
        action="store_true",
        help="Disable TLS verification only for an explicitly synthetic self-signed endpoint.",
    )
    args = parser.parse_args()
    oracle = json.loads(args.oracle.read_text(encoding="utf-8"))
    result = run_business_oracle(
        args.base_url,
        oracle,
        insecure_test_tls=args.insecure_test_tls,
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
