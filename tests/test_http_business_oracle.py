from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from evaluation.http_business_oracle import run_business_oracle


class _ReservationHandler(BaseHTTPRequestHandler):
    remaining = 1
    lock = threading.Lock()

    def log_message(self, _format, *_args):
        return

    def _write(self, status: int, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):  # noqa: N802
        if self.path == "/reset":
            with self.lock:
                type(self).remaining = 1
            self._write(200, {"remaining": 1})
            return
        if self.path == "/reserve":
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            with self.lock:
                if type(self).remaining:
                    type(self).remaining = 0
                    self._write(201, {"accepted": True})
                else:
                    self._write(409, {"accepted": False})
            return
        self._write(404, {})

    def do_GET(self):  # noqa: N802
        if self.path == "/state":
            with self.lock:
                remaining = type(self).remaining
            self._write(200, {"remaining": remaining})
            return
        if self.path == "/items":
            self._write(200, [{"id": "second", "value": 2}, {"id": "first", "value": 1}])
            return
        self._write(404, {})


@pytest.fixture
def reservation_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ReservationHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_business_oracle_runs_sequential_and_barrier_concurrent_phases(reservation_server):
    oracle = {
        "schemaVersion": "easydep-http-business-oracle/v1",
        "oracleId": "limited-capacity-reservation",
        "phases": [
            {
                "id": "reset",
                "kind": "request",
                "request": {"method": "POST", "path": "/reset"},
                "expect": {"status": 200, "jsonContains": {"remaining": 1}},
            },
            {
                "id": "race",
                "kind": "concurrentRequests",
                "requests": [
                    {"method": "POST", "path": "/reserve", "json": {"actor": actor}}
                    for actor in range(5)
                ],
                "expect": {"statusCounts": {"201": 1, "409": 4}},
            },
            {
                "id": "not-oversold",
                "kind": "request",
                "request": {"method": "GET", "path": "/state"},
                "expect": {"status": 200, "jsonContains": {"remaining": 0}},
            },
            {
                "id": "unordered-items",
                "kind": "request",
                "request": {"method": "GET", "path": "/items"},
                "expect": {
                    "status": 200,
                    "jsonContainsItems": [{"id": "first"}, {"id": "second"}],
                    "jsonLength": 2,
                },
            },
        ],
    }

    result = run_business_oracle(reservation_server, oracle)

    assert result["status"] == "passed"
    assert result["passedPhases"] == 4
    assert result["results"][1]["observedStatusCounts"] == {"201": 1, "409": 4}


def test_business_oracle_stops_after_failed_phase(reservation_server):
    result = run_business_oracle(
        reservation_server,
        {
            "schemaVersion": "easydep-http-business-oracle/v1",
            "phases": [
                {
                    "id": "wrong-expectation",
                    "request": {"method": "GET", "path": "/state"},
                    "expect": {"status": 201},
                },
                {
                    "id": "must-not-run",
                    "request": {"method": "POST", "path": "/reset"},
                    "expect": {"status": 200},
                },
            ],
        },
    )

    assert result["status"] == "failed"
    assert result["executedPhases"] == 1


def test_business_oracle_rejects_unknown_schema(reservation_server):
    with pytest.raises(ValueError, match="unsupported business oracle schema"):
        run_business_oracle(reservation_server, {"schemaVersion": "unknown", "phases": [{}]})


def test_insecure_test_tls_is_rejected_for_plain_http(reservation_server):
    with pytest.raises(ValueError, match="only valid for an HTTPS test endpoint"):
        run_business_oracle(
            reservation_server,
            {
                "schemaVersion": "easydep-http-business-oracle/v1",
                "phases": [
                    {
                        "id": "state",
                        "request": {"method": "GET", "path": "/state"},
                        "expect": {"status": 200},
                    }
                ],
            },
            insecure_test_tls=True,
        )
