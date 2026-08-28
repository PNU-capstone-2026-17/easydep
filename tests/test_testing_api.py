from __future__ import annotations

import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.testing.api as testing_api
from app.validation import RepairAttempt, RepairLedger


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
    class StubAdapter:
        def run(self, **_kwargs):
            return {"status": "completed", "passed": True, "unitTests": {}}

    monkeypatch.setattr(testing_api, "TestingAdapter", StubAdapter)

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


def test_testing_repair_carries_history_and_records_a_clean_candidate(monkeypatch, tmp_path):
    run_root = tmp_path / "run"
    (run_root / "application").mkdir(parents=True)
    testing_api._testing_jobs.clear()
    ledger = RepairLedger(episode_id="testing-episode")
    testing_api._testing_jobs["repair-1"] = {
        "job_id": "repair-1",
        "repair_history": ledger.model_dump(mode="json"),
        "previous_findings": ["testing.dynamic:FR1 assertion failed"],
    }
    class StubAdapter:
        def run(self, **_kwargs):
            return {"passed": True, "unitTests": {}}

    monkeypatch.setattr(testing_api, "TestingAdapter", StubAdapter)
    captured = {}

    def verification(**kwargs):
        captured.update(kwargs)
        return {
            "reports": {"dynamicFunctional": {"candidateDigest": "candidate-2"}},
            "passed": True,
            "blockingReason": None,
            "diagnostics": [],
        }

    monkeypatch.setattr(testing_api, "run_verification_graph", verification)

    testing_api._run_test("repair-1", "app-1", run_root)

    result = testing_api._testing_jobs["repair-1"]["result"]
    assert result["passed"] is True
    assert result["repair_state"]["status"] == "COMPLETED"
    assert result["repair_state"]["recent_attempts"][0]["outcome"] == "clean"
    assert captured["repair_history"]["episode_id"] == "testing-episode"


def test_testing_repair_stalls_on_a_repeated_candidate(monkeypatch, tmp_path):
    run_root = tmp_path / "run"
    (run_root / "application").mkdir(parents=True)
    testing_api._testing_jobs.clear()
    previous = RepairLedger(episode_id="testing-episode")
    previous.record(
        RepairAttempt(
            stage="testing.dynamic-functional",
            strategy_key="initial_generation",
            input_digest="input-1",
            candidate_digest="same-candidate",
            finding_keys_after=("testing.dynamic:FR1 assertion failed",),
            outcome="no_improvement",
        )
    )
    testing_api._testing_jobs["repair-1"] = {
        "job_id": "repair-1",
        "repair_history": previous.model_dump(mode="json"),
        "previous_findings": ["testing.dynamic:FR1 assertion failed"],
    }

    class StubAdapter:
        def run(self, **_kwargs):
            return {"passed": True, "unitTests": {}}

    monkeypatch.setattr(testing_api, "TestingAdapter", StubAdapter)
    monkeypatch.setattr(
        testing_api,
        "run_verification_graph",
        lambda **_kwargs: {
            "reports": {
                "dynamicFunctional": {"candidateDigest": "same-candidate"}
            },
            "passed": False,
            "blockingReason": "FR1 assertion failed",
            "diagnostics": [],
        },
    )

    testing_api._run_test("repair-1", "app-1", run_root)

    state = testing_api._testing_jobs["repair-1"]["result"]["repair_state"]
    assert state["status"] == "STALLED"
    assert state["recent_attempts"][-1]["outcome"] == "repeated_candidate"
