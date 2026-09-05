"""배포 정적 검사가 host PATH 대신 공용 툴체인을 쓰는지 확인한다."""

from __future__ import annotations

import json
import subprocess

from app.implementation.delivery import verification
from app.testing import repair_check
from app.testing.nodes import static_verification
from app.testing.runtime import container_runner
from app.testing.runtime.container_runner import ToolchainExecution
from app.testing.utils import docker_trivy


def _completed(command: list[str], *, code: int = 0, stdout: str = ""):
    return subprocess.CompletedProcess(command, code, stdout=stdout, stderr="")


def test_toolchain_command_wraps_host_execution_in_fixed_image(monkeypatch, tmp_path):
    observed: list[str] = []

    def fake_run(command, **_kwargs):
        observed.extend(command)
        return _completed(command)

    monkeypatch.delenv("EASYDEP_FIXED_LINUX_RUNNER", raising=False)
    monkeypatch.setenv("EASYDEP_TOOLCHAIN_IMAGE", "easydep-toolchain:test")
    monkeypatch.setattr(container_runner, "run_process_tree", fake_run)

    result = container_runner.run_toolchain_command(
        ["bash", "-n", "doctor.sh"], cwd=tmp_path, timeout=30
    )

    assert result.toolchain == "easydep-toolchain:test"
    assert result.command == ("bash", "-n", "doctor.sh")
    assert observed[-5:] == [
        "--entrypoint",
        "bash",
        "easydep-toolchain:test",
        "-n",
        "doctor.sh",
    ]
    assert "none" in observed
    assert str(tmp_path.resolve()) in " ".join(observed)


def test_toolchain_command_runs_directly_inside_fixed_runner(monkeypatch, tmp_path):
    observed: list[str] = []

    def fake_run(command, **_kwargs):
        observed.extend(command)
        return _completed(command)

    monkeypatch.setenv("EASYDEP_FIXED_LINUX_RUNNER", "1")
    monkeypatch.setattr(container_runner, "run_process_tree", fake_run)

    result = container_runner.run_toolchain_command(
        ["tofu", "validate", "-no-color"], cwd=tmp_path, timeout=30
    )

    assert observed == ["tofu", "validate", "-no-color"]
    assert result.toolchain == "fixed-linux-runner"
    assert result.environment_error is False


def test_trivy_uses_shared_toolchain_and_keeps_exact_findings(monkeypatch, tmp_path):
    payload = {
        "Results": [
            {
                "Target": "deployment/tofu/main.tf",
                "Misconfigurations": [
                    {
                        "ID": "AVD-AWS-9999",
                        "Title": "Example finding",
                        "Severity": "HIGH",
                        "Message": "Example message",
                    }
                ],
            }
        ]
    }

    def fake_run(command, **_kwargs):
        return ToolchainExecution(
            completed=_completed(command, stdout=json.dumps(payload)),
            command=tuple(command),
            toolchain="easydep-toolchain:test",
            environment_error=False,
        )

    monkeypatch.setattr(docker_trivy, "run_toolchain_command", fake_run)

    issues = docker_trivy.run_trivy_scan(str(tmp_path))

    assert issues == [
        "[deployment/tofu/main.tf] AVD-AWS-9999: Example finding (HIGH): Example message"
    ]
    assert issues.evidence["toolchain"] == "easydep-toolchain:test"
    assert issues.evidence["targets"] == ["deployment/tofu/main.tf"]
    assert issues.evidence["findings"] == [
        {
            "ruleId": "AVD-AWS-9999",
            "target": "deployment/tofu/main.tf",
            "resource": "",
            "startLine": None,
            "endLine": None,
            "finding": issues[0],
        }
    ]


def test_trivy_exception_matches_one_resource_not_the_whole_rule(tmp_path):
    """같은 AWS rule 문장이라도 ResourcePlan에 없는 리소스는 차단한다."""

    tofu = tmp_path / "deployment" / "tofu"
    tofu.mkdir(parents=True)
    (tofu / "main.tf").write_text(
        'resource "aws_security_group" "planned" {\n'
        '  egress { from_port = 443; to_port = 443; protocol = "tcp"; '
        'cidr_blocks = ["0.0.0.0/0"] }\n}\n',
        encoding="utf-8",
    )
    issue = "[deployment/tofu/main.tf] AWS-0104: unrestricted egress"
    plan = {
        "provider": "aws",
        "nodes": [
            {"id": "planned", "terraformTypes": ["aws_security_group"]},
            {"id": "compute", "terraformTypes": ["aws_instance"]},
            {"id": "registry", "terraformTypes": ["aws_ecr_repository"]},
            {
                "id": "route",
                "terraformTypes": ["aws_route"],
                "attributes": {"destination": "0.0.0.0/0"},
            },
        ],
    }
    findings = [
        {
            "ruleId": "AWS-0104",
            "target": "deployment/tofu/main.tf",
            "resource": "aws_security_group.planned",
            "finding": issue,
        },
        {
            "ruleId": "AWS-0104",
            "target": "deployment/tofu/main.tf",
            "resource": "aws_security_group.unplanned",
            "finding": issue,
        },
    ]

    blocking, allowed = static_verification._review_trivy_findings(
        plan,
        [issue, issue],
        findings=findings,
        application=tmp_path,
    )

    assert blocking == [issue]
    assert [item["resource"] for item in allowed] == ["aws_security_group.planned"]


def test_selected_deployment_projection_exposes_its_resource_plan() -> None:
    """selectedTarget 객체와 projection target 객체를 ID로 연결한다."""

    plan = {"provider": "aws", "nodes": [{"id": "network"}]}
    state = {
        "testing_input": {
            "contract_artifacts": {
                "deployment": {
                    "content": {
                        "selectedTarget": {
                            "id": "aws-cape-town",
                            "provider": "aws",
                        },
                        "projections": [
                            {
                                "target": {
                                    "id": "aws-seoul",
                                    "provider": "aws",
                                },
                                "resourcePlan": {"provider": "aws", "nodes": []},
                            },
                            {
                                "target": {
                                    "id": "aws-cape-town",
                                    "provider": "aws",
                                },
                                "resourcePlan": plan,
                            }
                        ],
                    }
                }
            }
        }
    }

    assert static_verification._resource_plan(state) == plan


def test_repair_check_passes_only_the_assigned_static_gate(monkeypatch, tmp_path):
    """OpenHands 내부 재검사는 다른 배포 검사를 함께 실행하지 않는다."""

    captured: dict[str, object] = {}

    def verify(state):
        captured["gate_scope"] = state["gate_scope"]
        return {
            "static_report": {
                "trivyScan": {"gateStatus": "PASS", "issues": []},
                "deploymentPackage": {"gateStatus": "PASS", "issues": []},
            },
            "iac_report": {"gateStatus": "PASS", "issues": []},
        }

    monkeypatch.setattr(repair_check, "static_verification_node", verify)

    result = repair_check.verify_testing_repair_gate(
        tmp_path,
        "testing-iac",
        {"testing_input": {}},
    )

    assert captured["gate_scope"] == ["iac"]
    assert result["gateStatus"] == "PASS"


def test_deployment_check_treats_toolchain_start_failure_as_inconclusive(
    monkeypatch, tmp_path
):
    deployment = tmp_path / "deployment"
    tofu = deployment / "tofu"
    runtime = deployment / "runtime"
    tofu.mkdir(parents=True)
    runtime.mkdir()
    for path in (
        deployment / "README.md",
        deployment / "easydep.ps1",
        tofu / "main.tf",
        tofu / "variables.tf",
        tofu / "outputs.tf",
        tofu / "cloud-init.yaml.tftpl",
        runtime / "compose.yaml",
        runtime / ".env.example",
    ):
        path.write_text("# validation fixture\n", encoding="utf-8")

    def unavailable(command, **_kwargs):
        return ToolchainExecution(
            completed=_completed(command, code=125),
            command=tuple(command),
            toolchain="easydep-toolchain:test",
            environment_error=True,
        )

    monkeypatch.setattr(verification, "run_toolchain_command", unavailable)

    result = verification.check_deployment_package(tmp_path)

    assert result["gateStatus"] == "INCONCLUSIVE"
    assert result["openTofu"]["gateStatus"] == "INCONCLUSIVE"
    assert all(command["status"] == "INCONCLUSIVE" for command in result["commands"])
    assert not any("apply" in command["command"] for command in result["commands"])
