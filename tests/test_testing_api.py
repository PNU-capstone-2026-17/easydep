from __future__ import annotations

import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.testing.api as testing_api


def _application() -> TestClient:
    app = FastAPI()
    app.include_router(testing_api.router)
    return TestClient(app)


def test_testing_api_requires_completed_implementation(monkeypatch, tmp_path):
    monkeypatch.setattr(
        testing_api.implementation_worker,
        "get_testing_input",
        lambda _job_id: {
            "job_id": "implementation-1",
            "app_id": "app-1",
            "status": "RUNNING",
            "run_root": str(tmp_path),
        },
    )

    response = _application().post(
        "/api/testing/apps/app-1/jobs",
        json={"implementation_job_id": "implementation-1"},
    )

    assert response.status_code == 409
    assert "COMPLETED" in response.json()["detail"]


def test_testing_api_runs_verified_adapter_in_background(monkeypatch, tmp_path):
    run_root = tmp_path / "run"
    (run_root / "application").mkdir(parents=True)
    testing_api._testing_jobs.clear()
    monkeypatch.setattr(
        testing_api.implementation_worker,
        "get_testing_input",
        lambda _job_id: {
            "job_id": "implementation-1",
            "app_id": "app-1",
            "status": "COMPLETED",
            "run_root": str(run_root),
        },
    )

    done = threading.Event()

    def complete(job_id, received_app_id, received_root):
        assert received_app_id == "app-1"
        assert received_root == run_root
        testing_api._update(
            job_id,
            status="COMPLETED",
            result={"status": "completed", "passed": True, "unitTests": {}},
        )
        done.set()

    monkeypatch.setattr(testing_api, "_run_test", complete)
    response = _application().post(
        "/api/testing/apps/app-1/jobs",
        json={"implementation_job_id": "implementation-1"},
    )

    assert response.status_code == 202
    job = response.json()
    assert job["implementation_job_id"] == "implementation-1"
    assert done.wait(timeout=1)
    fetched = _application().get(f"/api/testing/jobs/{job['job_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "COMPLETED"
    assert fetched.json()["result"]["passed"] is True


def test_testing_api_rejects_job_for_a_different_app(monkeypatch, tmp_path):
    monkeypatch.setattr(
        testing_api.implementation_worker,
        "get_testing_input",
        lambda _job_id: {
            "job_id": "implementation-1",
            "app_id": "different-app",
            "status": "COMPLETED",
            "run_root": str(tmp_path),
        },
    )

    response = _application().post(
        "/api/testing/apps/app-1/jobs",
        json={"implementation_job_id": "implementation-1"},
    )

    assert response.status_code == 404


def test_testing_job_reports_static_and_dynamic_verification(monkeypatch, tmp_path):
    """The web job runs the verification graph, not just the Gradle unit tests."""
    run_root = tmp_path / "run"
    (run_root / "application").mkdir(parents=True)
    testing_api._testing_jobs.clear()

    monkeypatch.setattr(
        testing_api.implementation_worker,
        "get_testing_input",
        lambda _job_id: {
            "job_id": "implementation-1",
            "app_id": "app-1",
            "status": "COMPLETED",
            "run_root": str(run_root),
        },
    )
    monkeypatch.setattr(
        testing_api,
        "TestingAdapter",
        lambda: type(
            "Stub",
            (),
            {"run": lambda _self, **_kwargs: {"status": "completed", "passed": True, "unitTests": {}}},
        )(),
    )

    calls: dict = {}

    def fake_verification(**kwargs):
        calls.update(kwargs)
        return {
            "reports": {
                "static": {"status": "FAILED", "issues": ["no limits"]},
                "iac": {"status": "PASSED", "issues": []},
                "dynamicFunctional": {"status": "failed", "reason": "FR1 assertion failed"},
                "dynamicNfr": {"status": "SKIPPED"},
            },
            "application": {"hostPort": 54321},
            "applicationLaunchError": None,
            "errors": [],
            "passed": False,
            "blockingReason": "FR1 assertion failed",
            "diagnostics": [{"code": "DEPLOYMENT_MISCONFIGURATION", "message": "..."}],
        }

    monkeypatch.setattr(testing_api, "run_verification_graph", fake_verification)

    client = _application()
    job = client.post(
        "/api/testing/apps/app-1/jobs",
        json={"implementation_job_id": "implementation-1"},
    ).json()

    for _ in range(100):
        fetched = client.get(f"/api/testing/jobs/{job['job_id']}").json()
        if fetched["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.05)

    assert fetched["status"] == "COMPLETED"
    assert calls["app_id"] == "app-1"
    result = fetched["result"]
    # Unit tests passed, but the dynamic functional stage did not.
    assert result["passed"] is False
    assert result["verification"]["reports"]["static"]["status"] == "FAILED"
    assert result["verification"]["reports"]["dynamicFunctional"]["reason"] == (
        "FR1 assertion failed"
    )
    assert [item["code"] for item in result["diagnostics"]] == [
        "DEPLOYMENT_MISCONFIGURATION"
    ]
