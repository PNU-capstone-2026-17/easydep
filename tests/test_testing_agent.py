"""The testing agent reads its inputs from the database, not from a workspace.

Static analysis scans the deployment/IaC snapshots the implementation agent
stored, and the dynamic functional stage tests against the requirements the
requirements agent stored. These tests pin both, because a scan that silently
falls back to a stale directory reports a pass that means nothing.
"""

import hashlib
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from app.db.models import (
    TYPE_DEPLOYMENT_FILE,
    TYPE_IAC_CODE,
    TYPE_SOURCE_CODE,
)
from app.testing import service as testing_service
from app.testing.graphs.testing_graph import create_testing_graph
from app.testing.schemas.testing_input import TestingInput as FrozenTestingInput


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
    "deployment/tofu/main.tf": 'resource "aws_db_instance" "bad" {\n  publicly_accessible = true\n}\n',
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


def test_static_scan_reads_the_restored_application_once(tmp_path):
    """복원 폴더 전체를 중복 Trivy 실행 없이 한 번만 읽는다."""
    (tmp_path / "k8s").mkdir()
    (tmp_path / "deployment/tofu").mkdir(parents=True)
    (tmp_path / "Dockerfile").write_text(K8S_FILES["Dockerfile"], encoding="utf-8")
    (tmp_path / "k8s/deployment.yaml").write_text(
        K8S_FILES["k8s/deployment.yaml"], encoding="utf-8"
    )
    (tmp_path / "deployment/tofu/main.tf").write_text(
        IAC_FILES["deployment/tofu/main.tf"], encoding="utf-8"
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

    with patch("app.testing.utils.static_analysis.run_trivy_scan", side_effect=fake_scan):
        result = create_testing_graph().invoke(_initial_state(application_dir=str(tmp_path)))

    assert scanned == [[
        "Dockerfile",
        "deployment/tofu/main.tf",
        "k8s/deployment.yaml",
    ]]

    assert result["static_report"]["status"] == "FAILED"
    assert result["static_report"]["source"]["source"] == "application"

    # No functional requirements were stored, so nothing is asserted about the app.
    assert result["dynamic_functional_report"]["status"] == "SKIPPED"


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
    assert result["iac_report"]["status"] == "SKIPPED"
    assert result["iac_report"]["issues"] == []
    assert result["iac_report"]["source"]["source"] == "none"


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
    """API 검사는 공용 툴체인과 cache로 backend만 실행한다."""
    from app.testing.runtime import app_container

    (tmp_path / "Dockerfile").write_text("FROM scratch\nEXPOSE 8000\n", encoding="utf-8")
    commands: list[list[str]] = []

    def docker(arguments, **_kwargs):
        commands.append(arguments)
        return type("Completed", (), {"returncode": 0, "stdout": "id\n", "stderr": ""})()

    monkeypatch.setattr(app_container, "_docker", docker)
    monkeypatch.setattr(app_container, "_wait_until_ready", lambda *_args: None)
    monkeypatch.setattr(app_container, "configured_runner_image", lambda: "toolchain:test")

    with app_container.running_application("app-1", tmp_path, launch_id="run-1") as (_, info):
        assert info["healthPath"] == "/healthz"

    start = next(command for command in commands if command[:2] == ["run", "-d"])
    assert "--rm" not in start
    assert not any(command[0] == "build" for command in commands)
    assert "toolchain:test" in start
    assert "bootRun" in start
    assert f"{app_container.GRADLE_CACHE_VOLUME}:/tmp/easydep-gradle-cache" in start
    assert "SPRING_PROFILES_ACTIVE=test" in start
    assert any(value.startswith("SPRING_DATASOURCE_URL=jdbc:h2:mem:") for value in start)
    assert "SPRING_SECURITY_USER_NAME=easydep-test" in start
    assert "SPRING_SECURITY_USER_PASSWORD=easydep-test" in start
    assert any(command[:2] == ["rm", "-f"] for command in commands)


def test_running_application_does_not_rebuild_frontend_for_api_checks(
    tmp_path, monkeypatch
):
    """배포 Dockerfile의 frontend COPY는 API 기능 검사 실행 경로와 무관하다."""
    from app.testing.runtime import app_container

    (tmp_path / "Dockerfile").write_text(
        "FROM gradle:8.14.2-jdk21\nCOPY frontend/dist/ src/main/resources/static/\n",
        encoding="utf-8",
    )

    def docker(arguments, **_kwargs):
        assert arguments[0] != "build"
        return type("Completed", (), {"returncode": 0, "stdout": "id\n", "stderr": ""})()

    monkeypatch.setattr(app_container, "_docker", docker)
    monkeypatch.setattr(app_container, "_wait_until_ready", lambda *_args: None)
    monkeypatch.setattr(app_container, "configured_runner_image", lambda: "toolchain:test")

    with app_container.running_application("app-1", tmp_path):
        pass


def test_application_start_timeout_does_not_trigger_source_repair(monkeypatch):
    """준비 시간 초과만으로 생성 source가 잘못됐다고 단정하지 않는다."""
    from app.testing.runtime import app_container
    from app.testing.runtime.app_container import ApplicationLaunchError

    clock = iter((0.0, 2.0))
    monkeypatch.setattr(app_container.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(app_container, "_container_logs", lambda _name: "Spring is starting")

    with pytest.raises(ApplicationLaunchError) as raised:
        app_container._wait_until_ready("app", "http://localhost/healthz", 1)

    assert raised.value.defect_class == "ENVIRONMENT_DEFECT"


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


def test_testing_result_preserves_static_failure_evidence_for_repair(
    tmp_path, monkeypatch
):
    """배포 수리가 규칙, 실행 명령과 실제 대상 파일을 모두 받는다."""
    fixed_input = FrozenTestingInput(
        app_id="app-1",
        implementation_job_id="implementation-1",
        artifact_version_ids={TYPE_SOURCE_CODE: 1, TYPE_DEPLOYMENT_FILE: 2},
    )
    issue = (
        "[deployment/tofu/main.tf] AVD-AWS-0131: "
        "EBS volume encryption is disabled (HIGH)"
    )
    command = {
        "name": "trivy config",
        "command": ["trivy", "config", "--format", "json", "/src"],
        "exitCode": 1,
        "status": "FAIL",
        "tool": "trivy",
        "toolchain": "easydep-toolchain:local",
    }
    verification = {
        "passed": False,
        "status": "FAIL",
        "gateStatus": "FAIL",
        "gateCounts": {"passed": 2, "failed": 1, "inconclusive": 0},
        "blockingReason": issue,
        "diagnostics": [],
        "reports": {
            "static": {
                "status": "FAILED",
                "gateStatus": "FAIL",
                "issues": [issue],
                "trivyScan": {
                    "status": "FAILED",
                    "gateStatus": "FAIL",
                    "issues": [issue],
                    "commands": [command],
                    "targets": ["deployment/tofu/main.tf"],
                    "tool": "trivy",
                },
                "deploymentPackage": {
                    "status": "PASSED",
                    "gateStatus": "PASS",
                    "issues": [],
                },
            },
            "iac": {"status": "SKIPPED", "gateStatus": "NOT_APPLICABLE"},
            "dynamicFunctional": {"status": "passed", "gateStatus": "PASS"},
        },
    }

    @contextmanager
    def restored_application(_testing_input):
        yield tmp_path

    monkeypatch.setattr(
        testing_service,
        "materialized_testing_application",
        restored_application,
    )
    monkeypatch.setattr(
        testing_service,
        "run_verification_graph",
        lambda **_kwargs: verification,
    )
    monkeypatch.setattr(
        testing_service,
        "load_file_snapshot",
        lambda *_args, **_kwargs: None,
    )

    result = testing_service.run_testing(
        "app-1",
        "implementation-1",
        run_id="testing-command",
        checkpoint={
            "implementation_job_id": "implementation-1",
            "testing_input": fixed_input.model_dump(mode="json"),
        },
    )["result"]

    assert len(result["blocking_findings"]) == 1
    finding = result["blocking_findings"][0]
    assert finding["code"] == "testing.static"
    assert finding["file_hints"] == ["application/deployment/tofu/main.tf"]
    assert finding["evidence"]["issues"] == [issue]
    assert finding["evidence"]["commands"] == [command]
    assert finding["evidence"]["tool"] == "trivy"


def test_static_repair_rechecks_static_gate_without_starting_the_application(
    tmp_path, monkeypatch
):
    """Gradle 성공 여부가 아니라 원래 실패한 정적 검사가 수리 완료를 정한다."""
    from app.implementation.agents import task_check
    from app.implementation.agents.verification import build
    from app.testing import repair_check

    application = tmp_path / "application"
    application.mkdir()
    trivy_command = {
        "name": "trivy config",
        "command": ["trivy", "config", str(application)],
        "exitCode": 1,
        "status": "FAIL",
    }
    monkeypatch.setattr(
        repair_check,
        "static_verification_node",
        lambda _state: {
            "static_report": {
                "status": "FAILED",
                "gateStatus": "FAIL",
                "issues": ["AVD-AWS-0131 is still present"],
                "commands": [trivy_command],
            },
            "iac_report": {"status": "PASSED", "gateStatus": "PASS"},
        },
    )
    monkeypatch.setattr(
        repair_check,
        "running_application",
        lambda *_args, **_kwargs: pytest.fail(
            "a deployment-only repair must not start the Spring application"
        ),
    )
    monkeypatch.setattr(
        build.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "a Gradle pass must not replace the failed static gate"
        ),
    )

    passed, output = task_check.run_task_check(
        tmp_path,
        "testing-static",
        ["application/deployment/tofu/main.tf"],
        {"gate": "static"},
    )

    assert passed is False
    assert "AVD-AWS-0131 is still present" in output


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
