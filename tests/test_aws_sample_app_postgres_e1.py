import json
from pathlib import Path

from evaluation.dependency_audit.aws_sample_app_postgres_e1 import _app_user_data

ROOT = Path("evaluation/dependency_audit")


def test_app_bootstrap_is_domain_neutral() -> None:
    script = _app_user_data().lower()

    assert "docker build" in script
    assert "service.py" in script
    assert "course" not in script
    assert "student" not in script
    assert "enrollment" not in script


def test_unassisted_result_covers_runtime_intervention_and_reboot() -> None:
    result = json.loads(
        (ROOT / "aws-sample-app-postgres-e1-result-20260814-3.json").read_text(
            encoding="utf-8"
        )
    )
    steps = {step["name"]: step for step in result["steps"]}

    assert result["outcome"] == "passed"
    assert result["cleanup"] == {"passed": True, "residual": []}
    assert steps["baseline.business-write"]["detail"]["status"] == 200
    assert steps["baseline.business-read"]["detail"]["body"]["value"] == {
        "message": "kept"
    }
    assert steps["intervention.readiness-failed"]["detail"]["status"] == 503
    assert steps["intervention.business-failed"]["detail"]["status"] == 502
    assert steps["persistence.readiness-during-reboot"]["detail"]["status"] == 503
    assert steps["persistence.readiness-after-reboot"]["detail"]["status"] == 200
    assert steps["persistence.business-read-after-reboot"]["detail"]["body"][
        "value"
    ] == {"message": "kept"}


def test_adjudication_separates_harness_and_confirmation_runs() -> None:
    adjudication = json.loads(
        (ROOT / "aws-sample-app-postgres-e1-adjudication-20260814.json").read_text(
            encoding="utf-8"
        )
    )

    assert [attempt["classification"] for attempt in adjudication["attempts"]] == [
        "harness-failure",
        "development-assisted-pass",
        "unassisted-confirmation-pass",
    ]
    assert adjudication["decision"] == "confirmed-for-development-scope"
    assert adjudication["attempts"][-1]["independentResidualCheck"] == {
        "instances": [],
        "volumes": [],
        "securityGroups": [],
        "keyPairs": [],
    }
