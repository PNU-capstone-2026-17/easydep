"""외부 패키지 없이 실행되는 App/State 두 역할의 샘플 서비스.

State 역할은 파일에 값을 영속화하고, App 역할은 ``STATE_URL``로 State에
요청을 전달한다. 특정 업무 도메인 대신 port, endpoint, readiness, volume
관계만 관측하기 위한 실험 대상이다.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import tempfile
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class RecordStore:
    """작은 JSON 파일 저장소. 파일 교체로 부분 쓰기를 피한다."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def read(self, key: str) -> object | None:
        with self._lock:
            return self._load().get(key)

    def ready(self) -> bool:
        return True

    def write(self, key: str, value: object) -> None:
        with self._lock:
            values = self._load()
            values[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                dir=self.path.parent, prefix=f".{self.path.name}-", suffix=".tmp"
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(values, handle, ensure_ascii=False, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)

    def _load(self) -> dict[str, object]:
        if not self.path.is_file():
            return {}
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise TypeError("state file must contain a JSON object")
        return loaded


class PostgresStore:
    """`psql`을 통해 PostgreSQL에 값을 저장하는 실험용 backend."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def ready(self) -> bool:
        return self._query("SELECT 1;") == "1"

    def read(self, key: str) -> object | None:
        self._ensure_table()
        encoded_key = _base64_text(key)
        raw = self._query(
            "SELECT value::text FROM easydep_records "  # noqa: S608
            f"WHERE key=convert_from(decode('{encoded_key}','base64'),'UTF8');"
        )
        return json.loads(raw) if raw else None

    def write(self, key: str, value: object) -> None:
        self._ensure_table()
        encoded_key = _base64_text(key)
        encoded_value = _base64_text(json.dumps(value, ensure_ascii=False))
        self._query(
            "INSERT INTO easydep_records(key,value) VALUES ("  # noqa: S608
            f"convert_from(decode('{encoded_key}','base64'),'UTF8'),"
            f"convert_from(decode('{encoded_value}','base64'),'UTF8')::jsonb) "
            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value;"
        )

    def _ensure_table(self) -> None:
        self._query(
            "CREATE TABLE IF NOT EXISTS easydep_records("
            "key text PRIMARY KEY, value jsonb NOT NULL);"
        )

    def _query(self, sql: str) -> str:
        result = subprocess.run(  # noqa: S603 - 고정된 psql 실행 파일과 내부 SQL만 사용한다.
            ["psql", self.database_url, "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-c", sql],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        if result.returncode:
            raise StoreUnavailable(result.stderr.strip() or "PostgreSQL query failed")
        return result.stdout.strip()


class StoreUnavailable(RuntimeError):
    """테스트용 저장소의 실행 경로를 사용할 수 없음을 나타낸다."""


def _base64_text(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _record_key(path: str) -> str | None:
    prefix = "/records/"
    if not path.startswith(prefix):
        return None
    key = path[len(prefix) :]
    return key if key and "/" not in key else None


def _send(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    loaded = json.loads(handler.rfile.read(length) or b"{}")
    if not isinstance(loaded, dict):
        raise TypeError("request body must be a JSON object")
    return loaded


def state_handler(
    store: RecordStore | PostgresStore,
    *,
    role: str = "state",
    fault_token: str | None = None,
) -> type[BaseHTTPRequestHandler]:
    class StateHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path == "/health/ready":
                try:
                    ready = store.ready()
                except (OSError, StoreUnavailable, subprocess.SubprocessError):
                    ready = False
                _send(
                    self,
                    HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ready": ready, "role": role},
                )
                return
            key = _record_key(self.path)
            if key is None:
                _send(self, HTTPStatus.NOT_FOUND, {"error": "not-found"})
                return
            try:
                value = store.read(key)
            except (OSError, StoreUnavailable, subprocess.SubprocessError):
                _send(self, HTTPStatus.BAD_GATEWAY, {"error": "state-unavailable"})
                return
            if value is None:
                _send(self, HTTPStatus.NOT_FOUND, {"error": "missing-record"})
                return
            _send(self, HTTPStatus.OK, {"key": key, "value": value})

        def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            key = _record_key(self.path)
            if key is None:
                _send(self, HTTPStatus.NOT_FOUND, {"error": "not-found"})
                return
            try:
                payload = _read_json(self)
                value = payload["value"]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                _send(self, HTTPStatus.BAD_REQUEST, {"error": "invalid-payload"})
                return
            try:
                store.write(key, value)
            except (OSError, StoreUnavailable, subprocess.SubprocessError):
                _send(self, HTTPStatus.BAD_GATEWAY, {"error": "state-unavailable"})
                return
            _send(self, HTTPStatus.OK, {"key": key, "stored": True})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path != "/__easydep_test/fault" or not fault_token:
                _send(self, HTTPStatus.NOT_FOUND, {"error": "not-found"})
                return
            try:
                supplied = _read_json(self).get("token")
            except (TypeError, ValueError, json.JSONDecodeError):
                supplied = None
            if supplied != fault_token:
                _send(self, HTTPStatus.FORBIDDEN, {"error": "forbidden"})
                return
            _send(
                self,
                HTTPStatus.ACCEPTED,
                {
                    "accepted": True,
                    "instance": os.getenv("EASYDEP_TEST_INSTANCE_ID", ""),
                },
            )
            threading.Timer(1.0, lambda: os._exit(0)).start()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return StateHandler


def _state_request(state_url: str, method: str, path: str, body: bytes | None = None) -> tuple[int, bytes]:
    request = Request(f"{state_url.rstrip('/')}{path}", data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        # 외부 probe의 timeout보다 먼저 실패를 HTTP 502/503으로 바꿔야 관측기가
        # 앱의 판단과 관측기 자체 timeout을 구분할 수 있다.
        with urlopen(request, timeout=0.75) as response:  # noqa: S310 - test-owned URL
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def app_handler(state_url: str) -> type[BaseHTTPRequestHandler]:
    class AppHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path == "/health/ready":
                try:
                    status, _ = _state_request(state_url, "GET", "/health/ready")
                    ready = status == HTTPStatus.OK
                except (OSError, URLError):
                    ready = False
                _send(
                    self,
                    HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
                    {"ready": ready, "role": "app", "stateReachable": ready},
                )
                return
            self._proxy("GET")

        def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._proxy("PUT")

        def _proxy(self, method: str) -> None:
            if _record_key(self.path) is None:
                _send(self, HTTPStatus.NOT_FOUND, {"error": "not-found"})
                return
            body = None
            if method == "PUT":
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
            try:
                status, response_body = _state_request(state_url, method, self.path, body)
                payload = json.loads(response_body or b"{}")
                _send(self, status, payload)
            except (OSError, URLError, json.JSONDecodeError):
                _send(self, HTTPStatus.BAD_GATEWAY, {"error": "state-unavailable"})

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return AppHandler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("app", "state", "postgres-app"), required=True)
    parser.add_argument(
        "--host", default=os.getenv("LISTEN_HOST", "0.0.0.0")  # noqa: S104
    )
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--state-url", default=os.getenv("STATE_URL", "http://state:8081"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument(
        "--data-path", type=Path, default=Path(os.getenv("DATA_PATH", "/data/records.json"))
    )
    args = parser.parse_args()
    if args.role == "state":
        handler = state_handler(RecordStore(args.data_path))
    elif args.role == "postgres-app":
        if not args.database_url:
            parser.error("--database-url or DATABASE_URL is required for postgres-app")
        handler = state_handler(
            PostgresStore(args.database_url),
            role="app",
            fault_token=os.getenv("EASYDEP_TEST_FAULT_TOKEN") or None,
        )
    else:
        handler = app_handler(args.state_url)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
