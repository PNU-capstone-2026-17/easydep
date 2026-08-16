from __future__ import annotations

import json
from pathlib import Path

RESULT_ROOT = Path("evaluation/dependency_audit")
RESULTS = {
    "aws": "aws-sample-app-postgres-e1-result-20260814-3.json",
    "azure": "azure-sample-app-postgres-e1-result-20260814-2.json",
    "gcp": "gcp-sample-app-postgres-e1-result-20260814.json",
}


def test_three_provider_sample_app_results_pass_and_clean_up() -> None:
    for provider, filename in RESULTS.items():
        result = json.loads((RESULT_ROOT / filename).read_text(encoding="utf-8"))

        assert result["provider"] == provider
        assert result["outcome"] == "passed"
        assert result["cleanup"] == {"passed": True, "residual": []}
        assert "not course-registration behavior" in result["scope"]


def test_three_provider_results_observe_business_loss_restore_and_persistence() -> None:
    expected_fragments = (
        "baseline.business-read",
        "baseline.app-write-read",
        "intervention.business-failed",
        "intervention.app-observes-state-loss",
        "restore.business-read",
        "restore.app-reads-existing-value",
        "persistence.business-read-after-reboot",
        "persistence.app-reads-existing-value",
        "persistence.app-observes-loss-and-existing-value",
    )
    for filename in RESULTS.values():
        result = json.loads((RESULT_ROOT / filename).read_text(encoding="utf-8"))
        names = [step["name"] for step in result["steps"] if step["status"] == "passed"]

        assert any(name.startswith("baseline.") for name in names)
        assert any((name.startswith("intervention.") and "failed" in name) or
                   name.endswith("state-loss") for name in names)
        assert any(name.startswith("restore.") and ("read" in name) for name in names)
        assert any(name.startswith("persistence.") and ("value" in name or "read" in name)
                   for name in names)
        assert any(fragment in names for fragment in expected_fragments)
