from __future__ import annotations

import json
from typing import Any

from evaluation.easydep.product import cli
from evaluation.easydep.product_scenario import (
    HttpProductScenarioTransport,
    ProductScenarioResult,
    ScenarioLocation,
)


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_http_transport_uses_frontend_workspace_and_artifact_urls(monkeypatch) -> None:
    requests: list[tuple[str, str, dict[str, Any] | None]] = []

    def fake_urlopen(request, *, timeout):
        body = json.loads(request.data) if request.data else None
        requests.append((request.method, request.full_url, body))
        return _Response({"app_id": "app/한글"})

    monkeypatch.setattr("evaluation.easydep.product_scenario.urlopen", fake_urlopen)
    transport = HttpProductScenarioTransport("http://easydep.test")

    transport.create_app("주문 서비스")
    transport.get_workspace("app/한글")
    transport.submit_command("app/한글", {"action": "start_design"})
    transport.get_artifacts("app/한글")

    assert requests == [
        ("POST", "http://easydep.test/api/workspace/apps", {"message": "주문 서비스"}),
        ("GET", "http://easydep.test/api/workspace/apps/app%2F%ED%95%9C%EA%B8%80", None),
        (
            "POST",
            "http://easydep.test/api/workspace/apps/app%2F%ED%95%9C%EA%B8%80/commands",
            {"action": "start_design"},
        ),
        ("GET", "http://easydep.test/api/apps/app%2F%ED%95%9C%EA%B8%80", None),
    ]


def test_cli_writes_one_raw_result_without_profiles_or_reports(
    monkeypatch, tmp_path
) -> None:
    output = tmp_path / "result.json"

    class FakeRunner:
        def __init__(self, _transport, *, timeout_seconds: float) -> None:
            assert timeout_seconds == 10

        def run(self, message: str, *, stop_after_stage: str):
            assert (message, stop_after_stage) == ("주문 서비스", "design")
            return ProductScenarioResult(
                location=ScenarioLocation("app-1", "design", "c-1", "COMPLETED", 3),
                implementation_job_id=None,
                testing_job_id=None,
                workspace={"app_id": "app-1"},
                artifacts={"artifacts": {"class_diagram": "diagram"}},
                events=({"event_id": 3},),
            )

    monkeypatch.setattr(cli, "ProductScenarioRunner", FakeRunner)
    monkeypatch.setattr(cli, "HttpProductScenarioTransport", lambda _url: object())

    exit_code = cli.main(
        [
            "--base-url",
            "http://easydep.test",
            "--message",
            "주문 서비스",
            "--stop-after",
            "design",
            "--timeout-seconds",
            "10",
            "--output",
            str(output),
        ]
    )

    value = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert value["ok"] is True
    assert value["location"]["stage"] == "design"
    assert value["artifacts"] == {"artifacts": {"class_diagram": "diagram"}}
