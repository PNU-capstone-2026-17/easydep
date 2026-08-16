"""도메인 중립 앱의 VM 직접 TLS 종료 경로를 로컬에서 격리 검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from evaluation.dependency_audit.sample_app.service import RecordStore, state_handler


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _openssl() -> str:
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


def _certificate(root: Path) -> tuple[Path, Path]:
    certificate = root / "certificate.pem"
    private_key = root / "private-key.pem"
    subprocess.run(
        [
            _openssl(),
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=easydep-neutral.invalid",
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
        ],
        capture_output=True,
        timeout=60,
        check=True,
    )
    return certificate, private_key


def _proxy_handler(upstream: str) -> type[BaseHTTPRequestHandler]:
    class ProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._forward("GET")

        def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._forward("PUT")

        def _forward(self, method: str) -> None:
            body = None
            if method == "PUT":
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            request = urllib.request.Request(
                f"{upstream}{self.path}", data=body, method=method
            )
            if body is not None:
                request.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310
                    payload = response.read()
                    status = response.status
                    content_type = response.headers.get("Content-Type", "application/json")
            except urllib.error.HTTPError as error:
                payload = error.read()
                status = error.code
                content_type = error.headers.get("Content-Type", "application/json")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return ProxyHandler


def _start_server(
    handler: type[BaseHTTPRequestHandler],
    *,
    port: int = 0,
    tls: tuple[Path, Path] | None = None,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(tls[0]), str(tls[1]))
        server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop_server(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _https_json(
    port: int, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"https://127.0.0.1:{port}{path}", data=body, method=method
    )
    if body is not None:
        request.add_header("Content-Type", "application/json")
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # noqa: S501 - one-day isolated test certificate
    with urllib.request.urlopen(request, context=context, timeout=3) as response:  # noqa: S310
        return response.status, json.loads(response.read())


def _fingerprint(port: int) -> str:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # noqa: S501 - one-day isolated test certificate
    with (
        socket.create_connection(("127.0.0.1", port), timeout=3) as connection,
        context.wrap_socket(
            connection, server_hostname="easydep-neutral.invalid"
        ) as tls,
    ):
        certificate = tls.getpeercert(binary_form=True)
    return hashlib.sha256(certificate).hexdigest()


def _connection_fails(port: int, *, tls: bool) -> bool:
    try:
        if tls:
            _https_json(port, "/health/ready")
        else:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health/ready", timeout=2)  # noqa: S310
    except (OSError, ssl.SSLError, urllib.error.URLError):
        return True
    return False


def run_local_tls_experiment(output: Path) -> dict[str, Any]:
    """Run baseline, TLS-terminator removal, restoration, and exact cleanup."""

    started = time.monotonic()
    result: dict[str, Any] = {
        "schemaVersion": "easydep-domain-neutral-direct-tls/v1",
        "scope": "process-level VM-side TLS termination and application forwarding",
        "startedAt": _now(),
        "steps": [],
        "limitations": [
            "The certificate is a one-day self-signed test certificate.",
            "DNS ownership and public CA trust are not measured.",
            "Provider public-address, route, and traffic-filter paths are separate gates.",
        ],
    }
    state_server: ThreadingHTTPServer | None = None
    state_thread: threading.Thread | None = None
    tls_server: ThreadingHTTPServer | None = None
    tls_thread: threading.Thread | None = None
    tls_port = 0
    try:
        with tempfile.TemporaryDirectory(prefix="easydep-neutral-tls-") as temporary:
            root = Path(temporary)
            certificate, private_key = _certificate(root)
            state_server, state_thread = _start_server(
                state_handler(RecordStore(root / "records.json"))
            )
            state_port = state_server.server_address[1]
            tls_server, tls_thread = _start_server(
                _proxy_handler(f"http://127.0.0.1:{state_port}"),
                tls=(certificate, private_key),
            )
            tls_port = tls_server.server_address[1]

            ready = _https_json(tls_port, "/health/ready")
            written = _https_json(
                tls_port,
                "/records/evidence",
                method="PUT",
                payload={"value": {"kept": True}},
            )
            read = _https_json(tls_port, "/records/evidence")
            fingerprint = _fingerprint(tls_port)
            plaintext_rejected = _connection_fails(tls_port, tls=False)
            baseline_passed = (
                ready == (HTTPStatus.OK, {"ready": True, "role": "state"})
                and written[0] == HTTPStatus.OK
                and read == (HTTPStatus.OK, {"key": "evidence", "value": {"kept": True}})
                and len(fingerprint) == 64
                and plaintext_rejected
            )
            result["steps"].append(
                {
                    "name": "baseline.https-business-path",
                    "status": "passed" if baseline_passed else "failed",
                    "certificateSha256": fingerprint,
                    "plaintextRejected": plaintext_rejected,
                }
            )
            if not baseline_passed:
                raise RuntimeError("TLS baseline oracle failed")

            _stop_server(tls_server, tls_thread)
            tls_server = None
            tls_thread = None
            removed = _connection_fails(tls_port, tls=True)
            result["steps"].append(
                {
                    "name": "intervention.tls-terminator-removed",
                    "status": "passed" if removed else "failed",
                }
            )
            if not removed:
                raise RuntimeError("HTTPS remained available without the TLS terminator")

            tls_server, tls_thread = _start_server(
                _proxy_handler(f"http://127.0.0.1:{state_port}"),
                port=tls_port,
                tls=(certificate, private_key),
            )
            restored = _https_json(tls_port, "/records/evidence")
            restored_passed = restored == (
                HTTPStatus.OK,
                {"key": "evidence", "value": {"kept": True}},
            )
            result["steps"].append(
                {
                    "name": "restoration.https-business-path",
                    "status": "passed" if restored_passed else "failed",
                }
            )
            if not restored_passed:
                raise RuntimeError("TLS restoration oracle failed")
        result["outcome"] = "passed"
    except Exception as exc:
        result["outcome"] = "failed"
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        if tls_server and tls_thread:
            _stop_server(tls_server, tls_thread)
        if state_server and state_thread:
            _stop_server(state_server, state_thread)
        closed = True
        if tls_port:
            with socket.socket() as probe:
                closed = probe.connect_ex(("127.0.0.1", tls_port)) != 0
        result["cleanup"] = {
            "passed": closed,
            "temporaryDirectoryRemoved": True,
            "listeningPortClosed": closed,
        }
        if not closed:
            result["outcome"] = "failed"
        result["finishedAt"] = _now()
        result["elapsedSeconds"] = round(time.monotonic() - started, 3)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "evaluation/dependency_audit/domain-neutral-direct-tls-result-20260815.json"
        ),
    )
    args = parser.parse_args()
    return 0 if run_local_tls_experiment(args.output)["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
