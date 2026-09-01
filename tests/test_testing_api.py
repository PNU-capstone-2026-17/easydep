from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import pytest

import app.testing.service as testing_api
from app.db.models import TYPE_DEPLOYMENT_FILE, TYPE_SOURCE_CODE
from app.testing.schemas.testing_input import TestingInput as FixedTestingInput
from app.validation import RepairAttempt, RepairLedger


def _testing_input(run_root, *, implementation_job_id: str = "implementation-1"):
    """DB를 사용하지 않는 API 테스트에 구현 산출물 ID를 만든다."""
    return FixedTestingInput(
        app_id="app-1",
        implementation_job_id=implementation_job_id,
        artifact_version_ids={TYPE_SOURCE_CODE: 11, TYPE_DEPLOYMENT_FILE: 12},
    )


def _stub_materialized_application(monkeypatch, run_root) -> None:
    """이미 준비한 임시 경로를 snapshot 복원 결과처럼 사용한다."""

    @contextmanager
    def materialized(_testing_input):
        yield run_root

    monkeypatch.setattr(testing_api, "materialized_testing_application", materialized)


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

    with pytest.raises(ValueError, match="COMPLETED"):
        testing_api.create_testing_job(
            "app-1",
            testing_api.CreateTestingJobRequest(
                implementation_job_id="implementation-1"
            ),
        )


def test_testing_api_runs_verified_adapter_in_background(monkeypatch, tmp_path):
    """원래 workspace가 없어도 저장 snapshot으로 새 Testing job을 시작한다."""
    run_root = tmp_path / "removed-run"
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
    captured_input = _testing_input(run_root)
    monkeypatch.setattr(
        testing_api,
        "capture_testing_input",
        lambda *_args, **_kwargs: captured_input,
    )

    done = threading.Event()

    def complete(job_id, received_input):
        assert received_input == captured_input
        testing_api._update(
            job_id,
            status="COMPLETED",
            result={"status": "completed", "passed": True, "unitTests": {}},
        )
        done.set()

    monkeypatch.setattr(testing_api, "_run_test", complete)
    job = testing_api.create_testing_job(
        "app-1",
        testing_api.CreateTestingJobRequest(
            implementation_job_id="implementation-1"
        ),
    )

    assert job["implementation_job_id"] == "implementation-1"
    assert done.wait(timeout=1)
    fetched = testing_api.get_testing_job(job["job_id"])
    assert fetched["status"] == "COMPLETED"
    assert fetched["result"]["passed"] is True


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

    with pytest.raises(ValueError, match="does not belong"):
        testing_api.create_testing_job(
            "app-1",
            testing_api.CreateTestingJobRequest(
                implementation_job_id="implementation-1"
            ),
        )


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
    captured_input = _testing_input(run_root)
    monkeypatch.setattr(
        testing_api,
        "capture_testing_input",
        lambda *_args, **_kwargs: captured_input,
    )
    _stub_materialized_application(monkeypatch, run_root)

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
            },
            "application": {"hostPort": 54321},
            "applicationLaunchError": None,
            "errors": [],
            "passed": False,
            "blockingReason": "FR1 assertion failed",
            "diagnostics": [{"code": "DEPLOYMENT_MISCONFIGURATION", "message": "..."}],
        }

    monkeypatch.setattr(testing_api, "run_verification_graph", fake_verification)

    job = testing_api.create_testing_job(
        "app-1",
        testing_api.CreateTestingJobRequest(
            implementation_job_id="implementation-1"
        ),
    )

    for _ in range(100):
        fetched = testing_api.get_testing_job(job["job_id"])
        if fetched["status"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.05)

    assert fetched["status"] == "COMPLETED"
    assert calls["app_id"] == "app-1"
    assert calls["application_dir"] == str(run_root / "application")
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
    _stub_materialized_application(monkeypatch, run_root)
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

    testing_api._run_test("repair-1", _testing_input(run_root))

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
    _stub_materialized_application(monkeypatch, run_root)
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

    testing_api._run_test("repair-1", _testing_input(run_root))

    state = testing_api._testing_jobs["repair-1"]["result"]["repair_state"]
    assert state["status"] == "STALLED"
    assert state["recent_attempts"][-1]["outcome"] == "repeated_candidate"
