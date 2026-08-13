import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from evaluation.baselines.chatdev import (
    CHATDEV_REVISION,
    _generated_directory,
    run,
)
from evaluation.baselines.chatdev_proxy import ChatDevModelProxy

CASE = Path("evaluation/baselines/cases/p1-stateless-aws.json")


def test_chatdev_dry_run_records_pinned_native_workflow(tmp_path: Path) -> None:
    run_dir = run(CASE, output_root=tmp_path, dry_run=True)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    task = (run_dir / "task.txt").read_text(encoding="utf-8")

    assert manifest["chatdevRevision"] == CHATDEV_REVISION
    assert manifest["status"] == "dry-run"
    assert manifest["modelTranslation"] == "local-openai-compatible-proxy"
    assert "native demand analysis" in task
    assert "EasyDep-specific" not in task


def test_generated_directory_is_scoped_to_the_unique_project(tmp_path: Path) -> None:
    warehouse = tmp_path / "WareHouse"
    warehouse.mkdir()
    unrelated = warehouse / "Other_EasyDepBaseline_20260810120000"
    older = warehouse / "EasyDep_P1_abc_EasyDepBaseline_20260810120000"
    newer = warehouse / "EasyDep_P1_abc_EasyDepBaseline_20260810120100"
    for path in (unrelated, older, newer):
        path.mkdir()

    assert _generated_directory(warehouse, "EasyDep_P1_abc") == newer


def test_proxy_translates_only_the_model_and_keeps_no_content() -> None:
    received: list[dict[str, object]] = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(json.loads(body))
            response = json.dumps(
                {
                    "id": "test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "experiment-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "ok",
                                "annotations": [{"provider": "extension"}],
                                "reasoning_content": "hidden",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, _format: str, *args: object) -> None:
            return

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    try:
        with ChatDevModelProxy(
            upstream_base_url=f"http://127.0.0.1:{upstream.server_port}/v1",
            api_key="secret-not-recorded",
            upstream_model="experiment-model",
            temperature_override=0.0,
            seed_override=42,
        ) as proxy:
            request = Request(
                proxy.base_url + "/chat/completions",
                data=json.dumps(
                    {
                        "model": "gpt-4o",
                        "temperature": 0.2,
                        "max_tokens": -3,
                        "messages": [{"role": "user", "content": "private"}],
                    }
                ).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request) as response:
                assert response.status == 200
                normalized = json.loads(response.read())
            events = list(proxy.events)
    finally:
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)

    assert received[0]["model"] == "experiment-model"
    assert received[0]["max_tokens"] == 4096
    assert received[0]["temperature"] == 0.0
    assert received[0]["seed"] == 42
    assert received[0]["messages"][0]["content"] == "private"
    assert normalized["choices"][0]["message"] == {
        "role": "assistant",
        "content": "ok",
    }
    assert events == [
        {
            "path": "/v1/chat/completions",
            "chatdevModelAlias": "gpt-4o",
            "upstreamModel": "experiment-model",
            "status": 200,
            "elapsedSeconds": events[0]["elapsedSeconds"],
            "errorType": None,
            "invalidRequestedMaxTokens": -3,
            "forwardedMaxTokens": 4096,
            "chatdevTemperature": 0.2,
            "forwardedTemperature": 0.0,
            "forwardedSeed": 42,
        }
    ]
    assert "private" not in json.dumps(events)
    assert "secret-not-recorded" not in json.dumps(events)
