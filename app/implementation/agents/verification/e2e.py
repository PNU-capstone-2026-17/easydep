"""wiring 작업이 실제 HTTP 흐름을 하나 이상 만들었는지 가볍게 확인한다.

정확한 동작과 Spring 연결은 Gradle이 실행하는 FlowTest가 검증한다. 여기서는 빈 test나
repository 직접 호출을 E2E로 잘못 제출하지 않도록 대표 HTTP 요청의 최소 근거만 확인한다.
"""

from __future__ import annotations

import re
from pathlib import Path

_METHOD_EVIDENCE = {
    "GET": ("HttpMethod.GET", "getForEntity(", "getForObject(", "get("),
    "POST": ("HttpMethod.POST", "postForEntity(", "postForObject(", "post("),
    "PUT": ("HttpMethod.PUT", ".put(", "put("),
    "PATCH": ("HttpMethod.PATCH", "patchForObject(", "patch("),
    "DELETE": ("HttpMethod.DELETE", ".delete(", "delete("),
}

_STATUS_NAMES = {
    200: "OK",
    201: "CREATED",
    202: "ACCEPTED",
    204: "NO_CONTENT",
}

_MOCKMVC_STATUS_ASSERTIONS = {
    200: "isOk(",
    201: "isCreated(",
    202: "isAccepted(",
    204: "isNoContent(",
}


def e2e_contract_violations(
    path: Path, contract: dict[str, object] | None = None
) -> list[str]:
    """대표 method·path·성공 status가 실행되는 FlowTest인지 확인한다."""
    if not path.is_file():
        return [f"{path.as_posix()}: HTTP flow test file is missing"]

    source = path.read_text(encoding="utf-8")
    violations: list[str] = []
    if "@SpringBootTest" not in source:
        violations.append(f"{path.as_posix()}: add @SpringBootTest")
    if not any(client in source for client in ("TestRestTemplate", "MockMvc")):
        violations.append(f"{path.as_posix()}: exercise the application through HTTP")
    if not re.search(r"(?m)^\s*@Test\b", source):
        violations.append(f"{path.as_posix()}: add at least one executable @Test")

    if not contract:
        return violations
    method = str(contract.get("method") or "").upper()
    expected_path = str(contract.get("path") or "")
    status = _integer(contract.get("status"))
    if method and not any(token in source for token in _METHOD_EVIDENCE.get(method, ())):
        violations.append(f"{path.as_posix()}: exercise HTTP method {method}")
    if expected_path and expected_path not in source:
        violations.append(f"{path.as_posix()}: exercise HTTP path {expected_path}")
    if status is not None:
        status_tokens = {str(status)}
        if name := _STATUS_NAMES.get(status):
            status_tokens.update({name, f"HttpStatus.{name}"})
        if mockmvc_assertion := _MOCKMVC_STATUS_ASSERTIONS.get(status):
            status_tokens.add(mockmvc_assertion)
        assertion_lines = [
            line
            for line in source.splitlines()
            if re.search(r"assert|expect|status", line, re.IGNORECASE)
        ]
        if not any(
            token in line for line in assertion_lines for token in status_tokens
        ):
            violations.append(f"{path.as_posix()}: assert HTTP status {status}")
    return violations


def _integer(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
