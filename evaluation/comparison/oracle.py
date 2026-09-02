"""생성된 애플리케이션에 공통 HTTP 오라클을 실행한다."""

from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


def _contains(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and _contains(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and all(any(_contains(item, wanted) for item in actual) for wanted in expected)
    return actual == expected


def _request(base_url: str, spec: dict[str, Any], timeout: float) -> dict[str, Any]:
    payload = None
    headers = {"Accept": "application/json"}
    if "json" in spec:
        payload = json.dumps(spec["json"], ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(
        urljoin(base_url.rstrip("/") + "/", str(spec.get("path", "")).lstrip("/")),
        data=payload,
        headers=headers,
        method=str(spec.get("method", "GET")),
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - 실험 manifest가 지정한 로컬 생성 앱
            raw = response.read().decode("utf-8", errors="replace")
            status = response.status
    except HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        status = error.code
    try:
        body = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        body = raw
    return {"status": status, "body": body}


def _check_response(actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "status" in expected and actual["status"] != expected["status"]:
        errors.append(f"status: expected {expected['status']}, got {actual['status']}")
    body = actual["body"]
    if "jsonContains" in expected and not _contains(body, expected["jsonContains"]):
        errors.append("jsonContains 조건 불일치")
    if "jsonContainsItems" in expected and not _contains(body, expected["jsonContainsItems"]):
        errors.append("jsonContainsItems 조건 불일치")
    if "jsonLength" in expected and (not hasattr(body, "__len__") or len(body) != expected["jsonLength"]):
        actual_length = len(body) if hasattr(body, "__len__") else None
        errors.append(f"jsonLength: expected {expected['jsonLength']}, got {actual_length}")
    return errors


def run_http_oracle(oracle: dict[str, Any], base_url: str) -> dict[str, Any]:
    timeout = float(oracle.get("requestTimeoutSeconds", 15))
    phases: list[dict[str, Any]] = []
    for phase in oracle.get("phases", []):
        phase_id = str(phase.get("id", "unnamed"))
        try:
            if phase.get("kind") == "request":
                actual = _request(base_url, phase["request"], timeout)
                errors = _check_response(actual, phase.get("expect", {}))
                detail: dict[str, Any] = {"response": actual}
            elif phase.get("kind") == "concurrentRequests":
                requests = phase.get("requests", [])
                with ThreadPoolExecutor(max_workers=max(1, len(requests))) as pool:
                    responses = list(pool.map(lambda spec: _request(base_url, spec, timeout), requests))
                counts = {str(key): value for key, value in Counter(item["status"] for item in responses).items()}
                expected_counts = phase.get("expect", {}).get("statusCounts", {})
                errors = [] if counts == expected_counts else [f"statusCounts: expected {expected_counts}, got {counts}"]
                detail = {"statusCounts": counts, "responses": responses}
            else:
                errors = [f"지원하지 않는 phase kind: {phase.get('kind')}"]
                detail = {}
        except Exception as error:  # 연결 실패도 판정 결과로 보존한다.
            errors = [f"{type(error).__name__}: {error}"]
            detail = {}
        phases.append({"id": phase_id, "status": "passed" if not errors else "failed", "errors": errors, **detail})
    passed = sum(item["status"] == "passed" for item in phases)
    return {
        "status": "passed" if phases and passed == len(phases) else "failed",
        "passedPhases": passed,
        "totalPhases": len(phases),
        "phases": phases,
    }
