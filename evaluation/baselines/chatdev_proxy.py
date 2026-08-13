"""Credential-safe model-name adapter for the unmodified ChatDev 1.x runtime."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def _normalize_chat_completion(body: bytes, content_type: str) -> bytes:
    """Drop provider extensions that ChatDev 1.x cannot deserialize."""
    if "json" not in content_type.lower():
        return body
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body
    if not isinstance(payload, dict):
        return body
    allowed = {"role", "content", "function_call", "tool_calls"}
    for choice in payload.get("choices") or []:
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            continue
        message = choice["message"]
        choice["message"] = {key: value for key, value in message.items() if key in allowed}
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class ChatDevModelProxy(AbstractContextManager["ChatDevModelProxy"]):
    """Translate ChatDev's fixed OpenAI alias to the experiment's configured model."""

    def __init__(
        self,
        *,
        upstream_base_url: str,
        api_key: str,
        upstream_model: str,
        timeout_seconds: float = 600,
        invalid_max_tokens_fallback: int = 4096,
        temperature_override: float | None = None,
        seed_override: int | None = None,
    ) -> None:
        self.upstream_base_url = upstream_base_url.rstrip("/")
        self.api_key = api_key
        self.upstream_model = upstream_model
        self.timeout_seconds = timeout_seconds
        if invalid_max_tokens_fallback < 1:
            raise ValueError("invalid_max_tokens_fallback must be positive")
        self.invalid_max_tokens_fallback = invalid_max_tokens_fallback
        self.temperature_override = temperature_override
        self.seed_override = seed_override
        self.events: list[dict[str, Any]] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("ChatDev model proxy is not running")
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def __enter__(self) -> ChatDevModelProxy:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
                started = time.perf_counter()
                request_bytes = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                status = 502
                alias: str | None = None
                error_type: str | None = None
                max_tokens_adjustment: dict[str, int] = {}
                sampling_adjustment: dict[str, Any] = {}
                try:
                    payload = json.loads(request_bytes)
                    if not isinstance(payload, dict):
                        raise TypeError("request body must be a JSON object")
                    alias = str(payload.get("model") or "") or None
                    payload["model"] = owner.upstream_model
                    if owner.temperature_override is not None:
                        sampling_adjustment["chatdevTemperature"] = payload.get("temperature")
                        payload["temperature"] = owner.temperature_override
                        sampling_adjustment["forwardedTemperature"] = owner.temperature_override
                    if owner.seed_override is not None:
                        payload["seed"] = owner.seed_override
                        sampling_adjustment["forwardedSeed"] = owner.seed_override
                    requested_max_tokens = payload.get("max_tokens")
                    if isinstance(requested_max_tokens, (int, float)) and requested_max_tokens <= 0:
                        payload["max_tokens"] = owner.invalid_max_tokens_fallback
                        max_tokens_adjustment = {
                            "invalidRequestedMaxTokens": int(requested_max_tokens),
                            "forwardedMaxTokens": owner.invalid_max_tokens_fallback,
                        }
                    suffix = self.path
                    if suffix == "/v1":
                        suffix = ""
                    elif suffix.startswith("/v1/"):
                        suffix = suffix[3:]
                    target = owner.upstream_base_url + suffix
                    request = urllib.request.Request(
                        target,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={
                            "Authorization": f"Bearer {owner.api_key}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                        method="POST",
                    )
                    try:
                        with urllib.request.urlopen(
                            request, timeout=owner.timeout_seconds
                        ) as response:
                            body = response.read()
                            status = response.status
                            content_type = response.headers.get("Content-Type", "application/json")
                    except urllib.error.HTTPError as error:
                        body = error.read()
                        status = error.code
                        content_type = error.headers.get("Content-Type", "application/json")
                        error_type = "HTTPError"
                    if status < 400:
                        body = _normalize_chat_completion(body, content_type)
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as error:  # boundary must return a valid HTTP response
                    error_type = type(error).__name__
                    body = json.dumps(
                        {"error": {"message": str(error), "type": error_type}}
                    ).encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                finally:
                    owner.events.append(
                        {
                            "path": self.path,
                            "chatdevModelAlias": alias,
                            "upstreamModel": owner.upstream_model,
                            "status": status,
                            "elapsedSeconds": round(time.perf_counter() - started, 6),
                            "errorType": error_type,
                        }
                        | max_tokens_adjustment
                        | sampling_adjustment
                    )

            def log_message(self, _format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
