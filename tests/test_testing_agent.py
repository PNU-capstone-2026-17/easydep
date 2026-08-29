"""The testing agent reads its inputs from the database, not from a workspace.

Static analysis scans the deployment/IaC snapshots the implementation agent
stored, and the dynamic functional stage tests against the requirements the
requirements agent stored. These tests pin both, because a scan that silently
falls back to a stale directory reports a pass that means nothing.
"""

import hashlib
import threading
import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from app.db.models import (
    TYPE_DEPLOYMENT_FILE,
    TYPE_IAC_CODE,
)
from app.testing.graphs.testing_graph import create_testing_graph
from app.testing.utils.requirements_source import (
    RequirementsUnavailable,
    functional_requirements,
)


def _snapshot(files: dict[str, str], version_no: int = 3) -> dict:
    return {
        "artifact_type": "any",
        "version_no": version_no,
        "metadata": {"implementation_job_id": "job-1"},
        "files": {
            path: {
                "content": text,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
            for path, text in files.items()
        },
        "created_at": "2026-08-16T00:00:00",
    }


K8S_FILES = {
    "k8s/deployment.yaml": "apiVersion: apps/v1\nkind: Deployment\n",
    "Dockerfile": "FROM eclipse-temurin:21-jre\n",
}
IAC_FILES = {
    "terraform/main.tf": 'resource "aws_db_instance" "bad" {\n  publicly_accessible = true\n}\n',
}


@pytest.fixture
def stored_artifacts():
    """Serve the two file snapshots the implementation agent writes."""
    by_type = {
        TYPE_DEPLOYMENT_FILE: _snapshot(K8S_FILES),
        TYPE_IAC_CODE: _snapshot(IAC_FILES),
    }
    with patch(
        "app.testing.utils.artifact_source.load_file_snapshot",
        side_effect=lambda app_id, artifact_type: by_type.get(artifact_type),
    ) as loader:
        yield loader


# ---------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------


def test_functional_requirements_reads_stored_classified_list():
    """The requirements agent stores a bare list, not a {"requirements": …} object."""
    stored = [
        {"id": "FR1", "text": "A user can register.", "type": "FR"},
        {"id": "NFR1", "text": "Login responds within 200ms.", "type": "NFR"},
        {"id": "FR2", "text": "A user can log in.", "type": "FR"},
    ]
    with patch(
        "app.testing.utils.requirements_source.load_state",
        return_value={"refined_requirements": stored},
    ):
        items = functional_requirements("app-1")

    assert [item["id"] for item in items] == ["FR1", "FR2"]


def test_functional_requirements_without_analysis():
    with patch(
        "app.testing.utils.requirements_source.load_state", return_value={}
    ), pytest.raises(RequirementsUnavailable):
        functional_requirements("app-1")


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def _initial_state(**overrides) -> dict:
    state = {
        "run_id": "test-123",
        "app_id": "app-1",
        "manifests_dir": "",
        "iac_dir": "",
        "target_url": "http://localhost:8080",
        "current_node": "",
        "errors": [],
        "static_report": None,
        "dynamic_functional_report": None,
        "dynamic_nfr_report": None,
        "iac_report": None,
    }
    state.update(overrides)
    return state


def test_static_stages_scan_the_same_application_folder(tmp_path):
    """배포와 IaC 검사가 같은 복원 폴더의 파일을 읽는다."""
    (tmp_path / "k8s").mkdir()
    (tmp_path / "terraform").mkdir()
    (tmp_path / "Dockerfile").write_text(K8S_FILES["Dockerfile"], encoding="utf-8")
    (tmp_path / "k8s/deployment.yaml").write_text(
        K8S_FILES["k8s/deployment.yaml"], encoding="utf-8"
    )
    (tmp_path / "terraform/main.tf").write_text(
        IAC_FILES["terraform/main.tf"], encoding="utf-8"
    )
    scanned: list[list[str]] = []

    def fake_scan(target_dir):
        from pathlib import Path

        scanned.append(
            sorted(
                path.relative_to(target_dir).as_posix()
                for path in Path(target_dir).rglob("*")
                if path.is_file()
            )
        )
        return [f"[{scanned[-1][0]}] Something (HIGH): ..."]

    with patch(
        "app.testing.utils.static_analysis.run_trivy_scan", side_effect=fake_scan
    ), patch(
        "app.testing.utils.requirements_source.load_state",
        return_value={"refined_requirements": []},
    ):
        result = create_testing_graph().invoke(
            _initial_state(application_dir=str(tmp_path))
        )

    assert sorted(scanned) == sorted(
        [
            ["Dockerfile", "k8s/deployment.yaml", "terraform/main.tf"],
            ["main.tf"],
        ]
    )

    assert result["static_report"]["status"] == "FAILED"
    assert result["static_report"]["source"]["source"] == "application"
    assert result["iac_report"]["status"] == "FAILED"
    assert result["iac_report"]["source"]["source"] == "application"

    # No functional requirements were stored, so nothing is asserted about the app.
    assert result["dynamic_functional_report"]["status"] == "SKIPPED"
    assert result["dynamic_nfr_report"]["status"] == "SKIPPED"


def test_static_stages_overlap_and_merge_results_in_stage_order():
    """Independent scans overlap, while externally visible errors stay deterministic."""
    barrier = threading.Barrier(2)

    def scan(name, report_key):
        def run(_state):
            barrier.wait(timeout=1)
            time.sleep(0.02 if name == "static_verification" else 0)
            return {
                "current_node": name,
                "errors": [f"{name}-error"],
                report_key: {"status": "FAILED"},
            }

        return run

    with patch(
        "app.testing.graphs.testing_graph.static_verification_node",
        scan("static_verification", "static_report"),
    ), patch(
        "app.testing.graphs.testing_graph.iac_verification_node",
        scan("iac_verification", "iac_report"),
    ), patch(
        "app.testing.graphs.testing_graph.dynamic_functional_node",
        return_value={
            "current_node": "dynamic_functional",
            "dynamic_functional_report": {"status": "SKIPPED"},
        },
    ):
        result = create_testing_graph().invoke(_initial_state())

    assert result["errors"] == ["static_verification-error", "iac_verification-error"]
    assert result["static_report"]["status"] == "FAILED"
    assert result["iac_report"]["status"] == "FAILED"


def test_static_stage_reports_a_missing_iac_folder(tmp_path):
    manifests = tmp_path / "k8s"
    manifests.mkdir()
    (manifests / "deployment.yaml").write_text("kind: Deployment\n", encoding="utf-8")

    with patch(
        "app.testing.utils.artifact_source.load_file_snapshot", return_value=None
    ), patch(
        "app.testing.utils.static_analysis.run_trivy_scan", return_value=[]
    ), patch(
        "app.testing.utils.requirements_source.load_state",
        return_value={"refined_requirements": []},
    ):
        result = create_testing_graph().invoke(
            _initial_state(application_dir=str(tmp_path))
        )

    assert result["static_report"]["status"] == "PASSED"
    assert result["static_report"]["source"]["source"] == "application"
    # The IaC stage had neither a snapshot nor a directory: nothing was scanned,
    # which is neither a pass nor a misconfiguration.
    assert result["iac_report"]["status"] == "UNAVAILABLE"
    assert result["iac_report"]["issues"] == []
    assert result["iac_report"]["source"]["source"] == "none"


def test_dynamic_functional_generates_from_stored_requirements(stored_artifacts):
    stored = [
        {"id": "FR1", "text": "A user can register.", "type": "FR"},
        {"id": "NFR1", "text": "Login responds within 200ms.", "type": "NFR"},
    ]
    captured: dict = {}

    def fake_generate(code, target_url, repository_root):
        captured["code"] = code
        return {"status": "passed", "exit_code": 0, "stdout": "", "stderr": "", "report": {}}

    with patch(
        "app.testing.utils.static_analysis.run_trivy_scan", return_value=[]
    ), patch(
        "app.testing.utils.requirements_source.load_state",
        return_value={"refined_requirements": stored},
    ), patch(
        "app.testing.nodes.dynamic_functional.configured_api_key", return_value="key"
    ), patch(
        "app.testing.nodes.dynamic_functional.run_dynamic_test", side_effect=fake_generate
    ), patch(
        "app.testing.nodes.dynamic_functional.OpenAI"
    ) as mock_openai:
        completion = mock_openai.return_value.chat.completions.create.return_value
        completion.choices[0].message.content = "def test_fr1(page): pass"
        result = create_testing_graph().invoke(_initial_state())

    report = result["dynamic_functional_report"]
    assert report["status"] == "passed"
    assert report["requirements"] == {
        "source": "db",
        "artifact_type": "REFINE_REQ",
        "count": 1,
        "ids": ["FR1"],
    }

    # The prompt carries the stored FR text and leaves the NFR to the NFR stage.
    prompt = mock_openai.return_value.chat.completions.create.call_args.kwargs[
        "messages"
    ][0]["content"]
    assert "A user can register." in prompt
    assert "Login responds within 200ms." not in prompt


def test_dynamic_functional_prompt_includes_accumulated_repair_history(stored_artifacts):
    from app.validation import RepairAttempt, RepairLedger

    ledger = RepairLedger(episode_id="testing-episode")
    ledger.record(
        RepairAttempt(
            stage="testing.dynamic-functional",
            strategy_key="first-attempt",
            input_digest="input-1",
            candidate_digest="rejected-candidate",
            finding_keys_before=("FR1 failed",),
            finding_keys_after=("FR1 failed",),
            outcome="no_improvement",
        )
    )
    with patch(
        "app.testing.utils.static_analysis.run_trivy_scan", return_value=[]
    ), patch(
        "app.testing.utils.requirements_source.load_state",
        return_value={
            "refined_requirements": [
                {"id": "FR1", "text": "A user can register.", "type": "FR"}
            ]
        },
    ), patch(
        "app.testing.nodes.dynamic_functional.configured_api_key", return_value="key"
    ), patch(
        "app.testing.nodes.dynamic_functional.run_dynamic_test",
        return_value={"status": "passed"},
    ), patch("app.testing.nodes.dynamic_functional.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value.choices[
            0
        ].message.content = "def test_fr1(page): pass"
        create_testing_graph().invoke(
            _initial_state(repair_history=ledger.model_dump(mode="json"))
        )

    prompt = mock_openai.return_value.chat.completions.create.call_args.kwargs[
        "messages"
    ][0]["content"]
    assert "rejected-candidate" in prompt
    assert "FR1 failed" in prompt


def test_dynamic_functional_without_app_id_does_not_silently_pass():
    with patch(
        "app.testing.utils.static_analysis.run_trivy_scan", return_value=[]
    ), patch("app.testing.utils.artifact_source.load_file_snapshot", return_value=None):
        result = create_testing_graph().invoke(_initial_state(app_id=""))

    assert result["dynamic_functional_report"]["status"] == "FAILED"
    assert result["dynamic_functional_report"]["reason"] == "Missing app_id"


# ---------------------------------------------------------------------------
# Bringing the generated application up for the dynamic stages
# ---------------------------------------------------------------------------


def test_exposed_port_is_read_from_the_generated_dockerfile(tmp_path):
    from app.testing.runtime.app_container import exposed_port

    (tmp_path / "Dockerfile").write_text(
        "FROM eclipse-temurin:21-jre\nEXPOSE 9090\nENTRYPOINT [\"java\"]\n",
        encoding="utf-8",
    )
    assert exposed_port(tmp_path) == 9090


def test_parallel_testing_jobs_use_different_docker_names():
    """같은 앱의 두 Testing job이 image/container 이름을 공유하면 안 된다."""
    from app.testing.runtime.app_container import runtime_identity

    first = runtime_identity("app-1", "testing-job-1")
    second = runtime_identity("app-1", "testing-job-2")

    assert first != second
    assert first == runtime_identity("app-1", "testing-job-1")
    assert all("app-1" not in value for value in first)


# ---------------------------------------------------------------------------
# The shared verification pass
# ---------------------------------------------------------------------------


@contextmanager
def _fake_launch(app_id, application_dir, **kwargs):
    yield "http://localhost:54321", {"source": "application", "image": "img", "hostPort": 54321}


def test_verification_runs_dynamic_tests_against_the_launched_app(tmp_path):
    from app.testing.runtime import verification

    captured: dict = {}

    def fake_run(code, target_url, repository_root):
        captured["target_url"] = target_url
        return {"status": "passed", "exit_code": 0, "stdout": "", "stderr": "", "report": {}}

    with patch(
        "app.testing.utils.static_analysis.run_trivy_scan", return_value=[]
    ), patch(
        "app.testing.runtime.verification.running_application", _fake_launch
    ), patch(
        "app.testing.utils.requirements_source.load_state",
        return_value={"refined_requirements": [{"id": "FR1", "text": "Register.", "type": "FR"}]},
    ), patch(
        "app.testing.nodes.dynamic_functional.configured_api_key", return_value="key"
    ), patch(
        "app.testing.nodes.dynamic_functional.run_dynamic_test", side_effect=fake_run
    ), patch(
        "app.testing.nodes.dynamic_functional.OpenAI"
    ) as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value.choices[
            0
        ].message.content = "def test_fr1(page): pass"
        result = verification.run_verification_graph(
            run_id="r1", app_id="app-1", application_dir=str(tmp_path)
        )

    assert captured["target_url"] == "http://localhost:54321"
    assert result["passed"] is True
    assert result["blockingReason"] is None
    assert result["application"]["hostPort"] == 54321
    assert result["reports"]["static"]["source"]["source"] == "application"
    assert result["reports"]["dynamicFunctional"]["targetUrl"] == "http://localhost:54321"


def test_verification_still_scans_when_the_app_cannot_be_launched(tmp_path):
    """A build failure must not cost the static analysis of the same artifacts."""
    from app.testing.runtime import verification
    from app.testing.runtime.app_container import ApplicationLaunchError

    @contextmanager
    def failing_launch(app_id, application_dir, **kwargs):
        raise ApplicationLaunchError("docker build failed")
        yield  # pragma: no cover

    with patch(
        "app.testing.utils.static_analysis.run_trivy_scan",
        return_value=["[k8s/deployment.yaml] No resource limits (HIGH): ..."],
    ), patch(
        "app.testing.runtime.verification.running_application", failing_launch
    ), patch(
        "app.testing.nodes.dynamic_functional.run_dynamic_test"
    ) as never_run:
        result = verification.run_verification_graph(
            run_id="r1", app_id="app-1", application_dir=str(tmp_path)
        )

    never_run.assert_not_called()
    assert result["applicationLaunchError"] == "docker build failed"
    assert result["reports"]["static"]["status"] == "FAILED"
    assert result["reports"]["dynamicFunctional"]["status"] == "SKIPPED"
    # 실행할 애플리케이션이 없으면 기능을 검증하지 못했으므로 성공일 수 없다.
    assert result["passed"] is False
    assert "동적 테스트" in result["blockingReason"]
    assert [item["code"] for item in result["diagnostics"]] == [
        "APPLICATION_LAUNCH_FAILED",
        "DEPLOYMENT_MISCONFIGURATION",
        "IAC_NOT_SCANNED",
    ]


def test_verification_reuses_a_caller_supplied_url(stored_artifacts):
    from app.testing.runtime import verification

    with patch(
        "app.testing.utils.static_analysis.run_trivy_scan", return_value=[]
    ), patch(
        "app.testing.runtime.verification.running_application"
    ) as launcher, patch(
        "app.testing.utils.requirements_source.load_state",
        return_value={"refined_requirements": []},
    ):
        result = verification.run_verification_graph(
            run_id="r1", app_id="app-1", target_url="http://staging.example:8080"
        )

    launcher.assert_not_called()
    assert result["application"] == {"source": "caller"}
