"""Shared domain-neutral oracle for provider-managed HTTPS ingress experiments."""

from __future__ import annotations

import json
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def openssl_path() -> str:
    discovered = shutil.which("openssl")
    if discovered:
        return discovered
    for candidate in (
        Path("C:/Program Files/Git/mingw64/bin/openssl.exe"),
        Path("C:/Program Files/Git/usr/bin/openssl.exe"),
    ):
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError("openssl is required for the isolated TLS oracle")


def generate_test_certificate(
    root: Path, common_name: str, *, pfx_password: str | None = None
) -> dict[str, Path]:
    """Create one-day test material outside the repository."""

    certificate = root / "certificate.pem"
    private_key = root / "private-key.pem"
    subprocess.run(
        [
            openssl_path(),
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            f"/CN={common_name}",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
        ],
        capture_output=True,
        timeout=60,
        check=True,
    )
    result = {"certificate": certificate, "privateKey": private_key}
    if pfx_password is not None:
        pfx = root / "certificate.pfx"
        subprocess.run(
            [
                openssl_path(),
                "pkcs12",
                "-export",
                "-out",
                str(pfx),
                "-inkey",
                str(private_key),
                "-in",
                str(certificate),
                "-passout",
                f"pass:{pfx_password}",
            ],
            capture_output=True,
            timeout=60,
            check=True,
        )
        result["pfx"] = pfx
    return result


def startup_oracle() -> str:
    return """#!/bin/bash
set -eu
cat >/opt/easydep_tls_oracle.py <<'PY'
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/readyz': payload = {'status': 'ready'}
        elif self.path == '/business': payload = {'service': 'easydep-neutral', 'result': 'ok'}
        else: self.send_response(404); self.end_headers(); return
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *_args): pass
ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
PY
nohup python3 /opt/easydep_tls_oracle.py >/var/log/easydep-tls-oracle.log 2>&1 &
"""


def endpoint_probe(
    address: str,
    *,
    scheme: str,
    expect_success: bool,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    """Require three consecutive matching business observations."""

    if scheme not in {"http", "https"}:
        raise ValueError(f"unsupported endpoint scheme: {scheme}")
    handlers: list[Any] = [urllib.request.ProxyHandler({})]
    if scheme == "https":
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # noqa: S501 - isolated test certificate
        handlers.append(urllib.request.HTTPSHandler(context=context))
    opener = urllib.request.build_opener(*handlers)
    deadline = time.monotonic() + timeout_seconds
    observations: list[dict[str, Any]] = []
    streak = 0
    while time.monotonic() < deadline:
        passed = True
        responses: dict[str, Any] = {}
        for path, expected in (
            ("/readyz", {"status": "ready"}),
            ("/business", {"service": "easydep-neutral", "result": "ok"}),
        ):
            try:
                with opener.open(f"{scheme}://{address}{path}", timeout=15) as response:
                    body = json.loads(response.read())
                    passed = passed and response.status == 200 and body == expected
                    responses[path] = {"status": response.status, "body": body}
            except (OSError, ValueError, urllib.error.URLError) as exc:
                passed = False
                responses[path] = {"error": type(exc).__name__}
        matched = passed == expect_success
        streak = streak + 1 if matched else 0
        observations.append({"observedAt": now(), "passed": passed, "responses": responses})
        if streak >= 3:
            return {
                "matched": True,
                "expectSuccess": expect_success,
                "observations": observations,
            }
        time.sleep(10)
    return {
        "matched": False,
        "expectSuccess": expect_success,
        "observations": observations,
    }


def https_probe(
    address: str,
    *,
    expect_success: bool,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    return endpoint_probe(
        address,
        scheme="https",
        expect_success=expect_success,
        timeout_seconds=timeout_seconds,
    )


def http_probe(
    address: str,
    *,
    expect_success: bool,
    timeout_seconds: int = 600,
) -> dict[str, Any]:
    return endpoint_probe(
        address,
        scheme="http",
        expect_success=expect_success,
        timeout_seconds=timeout_seconds,
    )
