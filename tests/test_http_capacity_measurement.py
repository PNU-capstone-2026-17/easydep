from __future__ import annotations

import http.server
import os
import threading

from evaluation.research_protocol.commands.measure_http_capacity import (
    _parse_binary_size,
    measure_http_capacity,
)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):  # noqa: A002
        return


def test_http_load_point_records_latency_throughput_and_process_resources():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = measure_http_capacity(
            url=f"http://127.0.0.1:{server.server_port}/health",
            duration_seconds=0.3,
            concurrency=2,
            timeout_seconds=1,
            process_id=os.getpid(),
            sample_interval_seconds=0.05,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert result["attempts"] > 0
    assert result["successes"] == result["attempts"]
    assert result["errorRate"] == 0
    assert result["sustainableRpsPerInstance"] > 0
    assert result["p95LatencyMs"] is not None
    assert result["p99RssBytes"] > 0


def test_docker_memory_units_are_converted_to_bytes():
    assert _parse_binary_size("512KiB") == 512 * 1024
    assert _parse_binary_size("1.5GiB") == round(1.5 * 1024**3)
