"""Browser E2E coverage for the per-use-case sequence diagram gallery.

The real design HTML and JavaScript run in Chromium. Only the API boundary is
made deterministic so this test does not require MySQL, an LLM, or PlantUML.

Run explicitly with::

    RUN_E2E_TESTS=1 python -m pytest -q tests/test_sequence_diagram_frontend_e2e.py
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

APP_ID = "sequence-gallery-e2e"
ROOT = Path(__file__).parents[1]
DESIGN_HTML = (ROOT / "frontend" / "design" / "index.html").read_bytes()

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("RUN_E2E_TESTS") != "1",
        reason="browser E2E tests require RUN_E2E_TESTS=1",
    ),
]


def _validation(**overrides: dict) -> dict:
    stages = {
        stage: {"valid": None, "errors": [], "findings": []}
        for stage in (
            "class_diagram",
            "sequence_diagram",
            "api_spec",
            "erd",
            "deployment_diagram",
        )
    }
    stages.update(overrides)
    return stages


USECASE_SPEC = {
    "use_cases": [
        {"id": "UC-01", "name": "주문 생성", "primary_actor": "고객"},
        {"id": "UC-02", "name": "주문 조회", "primary_actor": "고객"},
    ],
    "use_case_specs": [
        {"use_case_id": "UC-01", "main_scenario": []},
        {"use_case_id": "UC-02", "main_scenario": []},
    ],
}

INITIAL_RESPONSE = {
    "app_id": APP_ID,
    "artifacts": {"usecase_spec": USECASE_SPEC},
    "validation": _validation(),
}

CLASS_RESPONSE = {
    "app_id": APP_ID,
    "status": "need_feedback",
    "stage": "class_diagram",
    "artifacts": {
        "usecase_spec": USECASE_SPEC,
        "class_diagram": "@startuml\nclass Order\n@enduml",
    },
    "validation": _validation(
        class_diagram={"valid": True, "errors": [], "findings": []}
    ),
}

SEQUENCE_RESPONSE = {
    "app_id": APP_ID,
    "status": "need_feedback",
    "stage": "sequence_diagram",
    "artifacts": {
        "usecase_spec": USECASE_SPEC,
        "class_diagram": "@startuml\nclass Order\n@enduml",
        "sequence_diagram": (
            "@startuml UC_01\ntitle UC-01 - 주문 생성\n@enduml\n\n"
            "@startuml UC_02\ntitle UC-02 - 주문 조회\n@enduml"
        ),
    },
    "validation": _validation(
        class_diagram={"valid": True, "errors": [], "findings": []},
        sequence_diagram={"valid": True, "errors": [], "findings": []},
    ),
}

DIAGRAMS_RESPONSE = {
    "diagrams": [
        {"use_case_id": "UC-01", "use_case_name": "주문 생성"},
        {"use_case_id": "UC-02", "use_case_name": "주문 조회"},
    ]
}

SVG_IMAGE = b"""<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16">
<rect width="16" height="16" fill="#2563eb"/>
</svg>"""


class _DesignUiHandler(BaseHTTPRequestHandler):
    """Serve the real UI plus deterministic design API responses."""

    server: "_DesignUiServer"

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_svg(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Content-Length", str(len(SVG_IMAGE)))
        self.end_headers()
        self.wfile.write(SVG_IMAGE)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = urlparse(self.path).path
        self.server.requests.append(("GET", path))
        if path in ("/design", "/design/"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(DESIGN_HTML)))
            self.end_headers()
            self.wfile.write(DESIGN_HTML)
        elif path == f"/api/apps/{APP_ID}":
            self._send_json(INITIAL_RESPONSE)
        elif path == f"/api/apps/{APP_ID}/design/session":
            self._send_json(
                {"session": {"exists": False, "active": False, "stage": None}}
            )
        elif path == f"/api/apps/{APP_ID}/design/trace":
            self._send_json({"change_plan": []})
        elif path == f"/api/apps/{APP_ID}/stages/sequence_diagram/diagrams":
            self._send_json(DIAGRAMS_RESPONSE)
        elif path.endswith("/image.png") or path.endswith("/image.svg"):
            self._send_svg()
        else:
            self._send_json({"detail": f"Unhandled test route: {path}"}, status=404)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.requests.append(("POST", path))
        self.server.request_bodies.append((path, body))
        if path == f"/api/apps/{APP_ID}/design/start":
            self._send_json(CLASS_RESPONSE)
        elif path == f"/api/apps/{APP_ID}/design/resume":
            self._send_json(SEQUENCE_RESPONSE)
        else:
            self._send_json({"detail": f"Unhandled test route: {path}"}, status=404)


class _DesignUiServer(ThreadingHTTPServer):
    requests: list[tuple[str, str]]
    request_bodies: list[tuple[str, dict]]


@pytest.fixture
def design_ui_server() -> Iterator[tuple[str, _DesignUiServer]]:
    server = _DesignUiServer(("127.0.0.1", 0), _DesignUiHandler)
    server.requests = []
    server.request_bodies = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_design_ui_generates_and_renders_one_sequence_diagram_per_use_case(
    design_ui_server: tuple[str, _DesignUiServer],
) -> None:
    base_url, server = design_ui_server

    with playwright_sync.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.add_init_script(
            f"localStorage.setItem('easydep_app_id', {json.dumps(APP_ID)})"
        )

        page.goto(f"{base_url}/design/", wait_until="networkidle")
        page.get_by_role("button", name="설계 시작").click()
        playwright_sync.expect(page.locator("#artifactOutput")).to_contain_text(
            "class Order"
        )

        page.get_by_role("button", name="다음 단계").click()
        gallery = page.locator(".sequence-diagram-gallery")
        gallery.wait_for()

        cards = gallery.locator(".sequence-diagram-card")
        assert cards.count() == 2
        assert cards.nth(0).locator("h3").inner_text() == "UC-01 · 주문 생성"
        assert cards.nth(1).locator("h3").inner_text() == "UC-02 · 주문 조회"

        images = gallery.locator(".sequence-diagram-image")
        assert images.count() == 2
        assert images.nth(0).get_attribute("src").startswith(
            f"/api/apps/{APP_ID}/stages/sequence_diagram/diagrams/UC-01/image.png"
        )
        assert images.nth(1).get_attribute("src").startswith(
            f"/api/apps/{APP_ID}/stages/sequence_diagram/diagrams/UC-02/image.png"
        )
        page.wait_for_function(
            "() => [...document.querySelectorAll('.sequence-diagram-image')]"
            ".every((image) => image.complete && image.naturalWidth > 0)"
        )

        with page.expect_request(
            lambda request: "/diagrams/UC-02/image.svg" in request.url
        ):
            images.nth(1).click()
        assert page.locator("#lightboxOverlay").get_attribute("class").endswith("open")
        browser.close()

    assert server.request_bodies == [
        (f"/api/apps/{APP_ID}/design/start", {}),
        (f"/api/apps/{APP_ID}/design/resume", {"feedback": ""}),
    ]
    requested_paths = {path for method, path in server.requests if method == "GET"}
    assert f"/api/apps/{APP_ID}/stages/sequence_diagram/diagrams" in requested_paths
    assert (
        f"/api/apps/{APP_ID}/stages/sequence_diagram/diagrams/UC-01/image.png"
        in requested_paths
    )
    assert (
        f"/api/apps/{APP_ID}/stages/sequence_diagram/diagrams/UC-02/image.png"
        in requested_paths
    )
