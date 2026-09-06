"""Testing 수리 뒤 관련 gate만 다시 실행하는 흐름을 확인한다."""

from __future__ import annotations

import pytest

from app.testing.nodes import static_verification
from app.testing.runtime import verification


def test_static_iac_repair_does_not_launch_application(tmp_path, monkeypatch) -> None:
    """tofu 수리는 Trivy와 OpenTofu만 실행하고 이전 동적 결과를 재사용한다."""

    application = tmp_path / "application"
    (application / "deployment" / "tofu").mkdir(parents=True)
    (application / "deployment" / "tofu" / "main.tf").write_text(
        'terraform { required_version = ">= 1.8.0" }\n',
        encoding="utf-8",
    )
    checked_scopes: list[set[str]] = []

    monkeypatch.setattr(
        verification,
        "_launch",
        lambda *_args, **_kwargs: pytest.fail(
            "a static/IaC repair must not launch the Spring application"
        ),
    )
    monkeypatch.setattr(
        static_verification,
        "scan_stage",
        lambda **_kwargs: {
            "current_node": "static_verification",
            "errors": [],
            "static_report": {
                "status": "PASSED",
                "gateStatus": "PASS",
                "issues": [],
                "commands": [],
                "tool": "trivy",
            },
        },
    )

    def check_package(*_args, **kwargs):
        checked_scopes.append(set(kwargs["gate_scope"]))
        return {
            "status": "PASSED",
            "gateStatus": "PASS",
            "issues": [],
            "commands": [],
            "openTofu": {
                "status": "PASSED",
                "gateStatus": "PASS",
                "issues": [],
                "commands": [],
            },
        }

    monkeypatch.setattr(
        static_verification,
        "check_deployment_package",
        check_package,
    )
    previous_reports = {
        "static": {
            "status": "FAILED",
            "gateStatus": "FAIL",
            "trivyScan": {"status": "FAILED", "gateStatus": "FAIL", "issues": ["old"]},
            "deploymentPackage": {
                "status": "PASSED",
                "gateStatus": "PASS",
                "issues": [],
            },
        },
        "iac": {"status": "FAILED", "gateStatus": "FAIL", "issues": ["old"]},
        "dynamicFunctional": {
            "status": "passed",
            "gateStatus": "PASS",
            "candidatePlan": {
                "cases": [],
                "inputValues": {
                    "case-order": [
                        {
                            "operation_id": "createOrder",
                            "location": "body.description",
                            "value": "frozen repair input",
                        }
                    ]
                },
            },
        },
    }
    digests = verification._gate_input_digests(
        str(application), testing_input=None
    )
    previous_reports["static"]["deploymentPackage"]["inputDigest"] = digests[
        "package"
    ]
    previous_reports["dynamicFunctional"]["inputDigest"] = digests[
        "dynamicFunctional"
    ]

    result = verification.run_verification_graph(
        run_id="testing-2",
        app_id="app-1",
        application_dir=str(application),
        iac_expected=True,
        gate_scope={"static", "iac"},
        previous_reports=previous_reports,
        previous_job_id="testing-1",
    )

    assert result["passed"] is True
    assert checked_scopes == [{"iac"}]
    assert result["reports"]["static"]["deploymentPackage"]["reused"] is True
    assert result["reports"]["dynamicFunctional"]["reused"] is True
    assert result["reports"]["dynamicFunctional"]["reusedFromJobId"] == "testing-1"
    assert result["reports"]["dynamicFunctional"]["candidatePlan"]["inputValues"] == {
        "case-order": [
            {
                "operation_id": "createOrder",
                "location": "body.description",
                "value": "frozen repair input",
            }
        ]
    }


def test_report_without_input_digest_is_executed_once() -> None:
    """입력 식별값이 없는 예전 PASS는 변경 여부를 모르므로 재사용하지 않는다."""

    selected = verification._effective_scope(
        {"iac"},
        {
            "static": {
                "trivyScan": {"gateStatus": "PASS"},
                "deploymentPackage": {"gateStatus": "PASS"},
            },
            "dynamicFunctional": {"gateStatus": "PASS"},
        },
        {
            "static": "static-now",
            "package": "package-now",
            "iac": "iac-now",
            "dynamicFunctional": "dynamic-now",
        },
    )

    assert selected == {"static", "package", "iac", "dynamicFunctional"}
