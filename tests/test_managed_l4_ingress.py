import json
import threading
import uuid
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from evaluation.dependency_audit.managed_l4_ingress_common import startup_script
from evaluation.dependency_audit.sample_app.l4_service import handler


def test_l4_service_reports_instance_and_guards_fault(monkeypatch) -> None:
    exits: list[int] = []
    fault_token = uuid.uuid4().hex
    monkeypatch.setattr(
        "evaluation.dependency_audit.sample_app.l4_service.os._exit", exits.append
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), handler(instance="backend-a", fault_token=fault_token)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(  # noqa: S310 - local test server
            f"http://127.0.0.1:{server.server_port}/instance", timeout=2
        ) as response:
            assert json.loads(response.read()) == {"instance": "backend-a"}
        bad = Request(
            f"http://127.0.0.1:{server.server_port}/__easydep_test/fault",
            data=b'{"token":"wrong"}',
            method="POST",
        )
        try:
            urlopen(bad, timeout=2)  # noqa: S310 - local test server
        except HTTPError as error:
            assert error.code == HTTPStatus.FORBIDDEN
    finally:
        server.shutdown()
        server.server_close()


def test_cloud_startup_payload_fits_ec2_user_data_limit() -> None:
    script = startup_script(port=8080, fault_token="a" * 32)

    assert len(script.encode()) < 16_384
    assert "/health/ready" not in script
    assert "l4_service.py" not in script
