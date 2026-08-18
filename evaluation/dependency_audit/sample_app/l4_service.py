"""L4 전달·상태 검사·백엔드 제외만 관측하는 최소 HTTP 앱."""

from __future__ import annotations

import argparse
import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _send(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, sort_keys=True).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handler(*, instance: str, fault_token: str) -> type[BaseHTTPRequestHandler]:
    class L4Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path == "/health/ready":
                _send(self, HTTPStatus.OK, {"ready": True, "instance": instance})
            elif self.path == "/instance":
                _send(self, HTTPStatus.OK, {"instance": instance})
            else:
                _send(self, HTTPStatus.NOT_FOUND, {"error": "not-found"})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path != "/__easydep_test/fault":
                _send(self, HTTPStatus.NOT_FOUND, {"error": "not-found"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                supplied = json.loads(self.rfile.read(size) or b"{}").get("token")
            except (TypeError, ValueError, json.JSONDecodeError):
                supplied = None
            if supplied != fault_token:
                _send(self, HTTPStatus.FORBIDDEN, {"error": "forbidden"})
                return
            _send(self, HTTPStatus.ACCEPTED, {"accepted": True, "instance": instance})
            threading.Timer(1.0, lambda: os._exit(0)).start()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return L4Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--fault-token", required=True)
    args = parser.parse_args()
    ThreadingHTTPServer(
        (args.host, args.port),
        handler(instance=args.instance, fault_token=args.fault_token),
    ).serve_forever()


if __name__ == "__main__":
    main()
