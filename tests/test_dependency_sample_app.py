import json
import threading
import time
import uuid
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from evaluation.dependency_audit.app_resource_intervention import EXPERIMENT_IDS
from evaluation.dependency_audit.sample_app.service import (
    PostgresStore,
    RecordStore,
    _base64_text,
    _record_key,
    state_handler,
)


def test_record_store_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "state" / "records.json"
    RecordStore(path).write("alpha", {"count": 1})

    assert RecordStore(path).read("alpha") == {"count": 1}


def test_record_path_rejects_nested_or_empty_keys() -> None:
    assert _record_key("/records/item") == "item"
    assert _record_key("/records/") is None
    assert _record_key("/records/a/b") is None


def test_experiment_matrix_is_domain_neutral() -> None:
    assert EXPERIMENT_IDS == (
        "app-port-binding",
        "app-state-endpoint-binding",
        "state-resource-availability",
        "volume-state-persistence",
    )


def test_postgres_store_uses_encoded_values(monkeypatch) -> None:
    statements: list[str] = []
    store = PostgresStore("postgresql://example.invalid/test")

    def query(sql: str) -> str:
        statements.append(sql)
        return '"kept"' if sql.startswith("SELECT value") else ""

    monkeypatch.setattr(store, "_query", query)

    store.write("probe'key", {"value": "한글"})
    value = store.read("probe'key")

    assert value == "kept"
    assert all("probe'key" not in statement for statement in statements)
    assert _base64_text("probe'key") in statements[1]


def test_fault_endpoint_is_opt_in_and_token_guarded(tmp_path: Path, monkeypatch) -> None:
    exits: list[int] = []
    fault_token = uuid.uuid4().hex
    monkeypatch.setattr("evaluation.dependency_audit.sample_app.service.os._exit", exits.append)
    monkeypatch.setenv("EASYDEP_TEST_INSTANCE_ID", "worker-a")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        state_handler(RecordStore(tmp_path / "state.json"), fault_token=fault_token),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/__easydep_test/fault"
        bad = Request(
            url,
            data=b'{"token":"wrong"}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            urlopen(bad, timeout=2)  # noqa: S310 - local test server
        except HTTPError as error:
            assert error.code == HTTPStatus.FORBIDDEN
        good = Request(
            url,
            data=json.dumps({"token": fault_token}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(good, timeout=2) as response:  # noqa: S310 - local test server
            body = json.loads(response.read())
        assert response.status == HTTPStatus.ACCEPTED
        assert body == {"accepted": True, "instance": "worker-a"}
        deadline = time.monotonic() + 2
        while not exits and time.monotonic() < deadline:
            time.sleep(0.02)
        assert exits == [0]
    finally:
        server.shutdown()
        server.server_close()
