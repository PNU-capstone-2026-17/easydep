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


@pytest.fixture(autouse=True)
def _opentofu_succeeds_without_starting_a_real_provider_download():
    """이 파일의 graph 테스트는 OpenTofu 자체가 아니라 단계 연결만 확인한다."""
    with patch(
        "app.testing.nodes.iac_verification.run_opentofu_checks",
        return_value={
            "status": "PASSED",
            "issues": [],
            "commands": [],
            "planEnabled": False,
            "message": "OpenTofu 검사를 통과했습니다.",
        },
    ):
        yield


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def _initial_state(**overrides) -> dict:
    state = {
        "run_id": "test-123",
        "app_id": "app-1",
        "manifests_dir": "",
        "iac_dir": "",
        "target_url": "",
        "current_node": "",
        "errors": [],
        "static_report": None,
        "dynamic_functional_report": None,
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
    (tmp_path / "terraform/main.tf").write_text(IAC_FILES["terraform/main.tf"], encoding="utf-8")
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

    with patch("app.testing.utils.static_analysis.run_trivy_scan", side_effect=fake_scan):
        result = create_testing_graph().invoke(_initial_state(application_dir=str(tmp_path)))

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

    with (
        patch(
            "app.testing.graphs.testing_graph.static_verification_node",
            scan("static_verification", "static_report"),
        ),
        patch(
            "app.testing.graphs.testing_graph.iac_verification_node",
            scan("iac_verification", "iac_report"),
        ),
        patch(
            "app.testing.graphs.testing_graph.dynamic_functional_node",
            return_value={
                "current_node": "dynamic_functional",
                "dynamic_functional_report": {"status": "SKIPPED"},
            },
        ),
    ):
        result = create_testing_graph().invoke(_initial_state())

    assert result["errors"] == ["static_verification-error", "iac_verification-error"]
    assert result["static_report"]["status"] == "FAILED"
    assert result["iac_report"]["status"] == "FAILED"


def test_static_stage_reports_a_missing_iac_folder(tmp_path):
    manifests = tmp_path / "k8s"
    manifests.mkdir()
    (manifests / "deployment.yaml").write_text("kind: Deployment\n", encoding="utf-8")

    with (
        patch("app.testing.utils.artifact_source.load_file_snapshot", return_value=None),
        patch("app.testing.utils.static_analysis.run_trivy_scan", return_value=[]),
    ):
        result = create_testing_graph().invoke(_initial_state(application_dir=str(tmp_path)))

    assert result["static_report"]["status"] == "PASSED"
    assert result["static_report"]["source"]["source"] == "application"
    # The IaC stage had neither a snapshot nor a directory: nothing was scanned,
    # which is neither a pass nor a misconfiguration.
    assert result["iac_report"]["status"] == "UNAVAILABLE"
    assert result["iac_report"]["issues"] == []
    assert result["iac_report"]["source"]["source"] == "none"


def test_opentofu_plan_is_dry_run_and_never_apply(tmp_path, monkeypatch):
    """plan을 켜도 원본을 바꾸거나 apply를 실행하지 않는다."""
    from app.testing.utils import opentofu

    terraform = tmp_path / "terraform"
    terraform.mkdir()
    source = terraform / "main.tf"
    source.write_text('terraform { required_version = ">= 1.6.0" }\n', encoding="utf-8")
    commands: list[list[str]] = []

    monkeypatch.setattr(opentofu, "_tofu_executable", lambda: "tofu")
    monkeypatch.setattr(
        opentofu,
        "_configured_values",
        lambda: {"PATH": "tools", "TESTING_IAC_PLAN": "true"},
    )

    def completed(command, **_kwargs):
        commands.append(list(command))
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(opentofu, "run_process_tree", completed)

    report = opentofu.run_opentofu_checks(terraform)

    assert report["status"] == "PASSED"
    assert [command[1] for command in commands] == ["fmt", "init", "validate", "plan"]
    assert all("apply" not in command for command in commands)
    assert source.read_text(encoding="utf-8").endswith("\n")


# ---------------------------------------------------------------------------
# Bringing the generated application up for the dynamic stages
# ---------------------------------------------------------------------------


def test_exposed_port_is_read_from_the_generated_dockerfile(tmp_path):
    from app.testing.runtime.app_container import exposed_port

    (tmp_path / "Dockerfile").write_text(
        'FROM eclipse-temurin:21-jre\nEXPOSE 9090\nENTRYPOINT ["java"]\n',
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


def test_running_application_uses_test_database_and_keeps_container_for_logs(tmp_path, monkeypatch):
    """실패 로그를 읽기 전에 Docker가 컨테이너를 자동 삭제하지 않는다."""
    from app.testing.runtime import app_container

    (tmp_path / "Dockerfile").write_text("FROM scratch\nEXPOSE 8000\n", encoding="utf-8")
    commands: list[list[str]] = []

    def docker(arguments, **_kwargs):
        commands.append(arguments)
        return type("Completed", (), {"returncode": 0, "stdout": "id\n", "stderr": ""})()

    monkeypatch.setattr(app_container, "_docker", docker)
    monkeypatch.setattr(app_container, "_wait_until_ready", lambda *_args: None)

    with app_container.running_application("app-1", tmp_path, launch_id="run-1") as (_, info):
        assert info["healthPath"] == "/healthz"

    start = next(command for command in commands if command[:2] == ["run", "-d"])
    assert "--rm" not in start
    assert "SPRING_PROFILES_ACTIVE=test" in start
    assert any(value.startswith("SPRING_DATASOURCE_URL=jdbc:h2:mem:") for value in start)
    assert "SPRING_SECURITY_USER_NAME=easydep-test" in start
    assert "SPRING_SECURITY_USER_PASSWORD=easydep-test" in start
    assert any(command[:2] == ["rm", "-f"] for command in commands)


def test_running_application_classifies_missing_frontend_build_as_product_defect(
    tmp_path, monkeypatch
):
    """Dockerfile이 없는 frontend 산출물을 참조하면 구현 단계가 고쳐야 한다."""
    from app.testing.runtime import app_container
    from app.testing.runtime.app_container import ApplicationLaunchError

    (tmp_path / "Dockerfile").write_text(
        "FROM gradle:8.14.2-jdk21\nCOPY frontend/dist/ src/main/resources/static/\n",
        encoding="utf-8",
    )

    def docker(arguments, **_kwargs):
        assert arguments[0] == "build"
        return type(
            "Completed",
            (),
            {
                "returncode": 1,
                "stdout": "",
                "stderr": 'failed to calculate checksum: "/frontend/dist": not found',
            },
        )()

    monkeypatch.setattr(app_container, "_docker", docker)
    with (
        pytest.raises(ApplicationLaunchError) as raised,
        app_container.running_application("app-1", tmp_path),
    ):
        pass

    assert raised.value.defect_class == "SUT_DEFECT"
    assert "/frontend/dist" in str(raised.value)


def test_dynamic_testing_uses_the_shared_llm_model(monkeypatch):
    """Testing이 공통 MODEL 값을 그대로 사용한다."""
    from app.testing.runtime import provider

    monkeypatch.setattr(provider.settings, "model", "openai/gpt-oss-120b")

    assert provider.configured_model("fallback") == "openai/gpt-oss-120b"


def test_static_failure_blocks_the_testing_result():
    from app.testing.runtime.verification import blocking_reason

    reason = blocking_reason(
        {
            "static": {"status": "FAILED", "issues": ["Dockerfile runs as root"]},
            "iac": {"status": "PASSED"},
            "dynamicFunctional": {"status": "passed"},
        }
    )

    assert reason == "배포 설정 정적 검사에 실패했습니다: Dockerfile runs as root"


# ---------------------------------------------------------------------------
# The shared verification pass
# ---------------------------------------------------------------------------


@contextmanager
def _fake_launch(app_id, application_dir, **kwargs):
    yield "http://localhost:54321", {"source": "application", "image": "img", "hostPort": 54321}


def test_verification_runs_dynamic_tests_against_the_launched_app(tmp_path):
    from app.testing.runtime import verification

    captured: dict = {}

    def fake_dynamic(state):
        captured["target_url"] = state["target_url"]
        return {
            "current_node": "dynamic_functional",
            "dynamic_functional_report": {
                "status": "passed",
                "gateStatus": "PASS",
                "targetUrl": state["target_url"],
            },
        }

    with (
        patch("app.testing.utils.static_analysis.run_trivy_scan", return_value=[]),
        patch("app.testing.runtime.verification.running_application", _fake_launch),
        patch("app.testing.graphs.testing_graph.dynamic_functional_node", fake_dynamic),
    ):
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

    with (
        patch(
            "app.testing.utils.static_analysis.run_trivy_scan",
            return_value=["[k8s/deployment.yaml] No resource limits (HIGH): ..."],
        ),
        patch("app.testing.runtime.verification.running_application", failing_launch),
    ):
        result = verification.run_verification_graph(
            run_id="r1", app_id="app-1", application_dir=str(tmp_path)
        )

    assert result["applicationLaunchError"] == "docker build failed"
    assert result["reports"]["static"]["status"] == "FAILED"
    assert result["reports"]["dynamicFunctional"]["status"] == "FAILED"
    assert result["reports"]["dynamicFunctional"]["defectClass"] == "SUT_DEFECT"
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

    with (
        patch("app.testing.utils.static_analysis.run_trivy_scan", return_value=[]),
        patch("app.testing.runtime.verification.running_application") as launcher,
    ):
        result = verification.run_verification_graph(
            run_id="r1", app_id="app-1", target_url="http://staging.example:8080"
        )

    launcher.assert_not_called()
    assert result["application"] == {"source": "caller"}
