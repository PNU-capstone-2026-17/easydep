from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("evaluation/dependency_audit")
ADJUDICATION = ROOT / "multi-provider-sample-app-postgres-e2-adjudication-20260815.json"


def test_three_provider_e2_selected_results_pass_and_match_hashes() -> None:
    adjudication = json.loads(ADJUDICATION.read_text(encoding="utf-8"))

    assert {item["provider"] for item in adjudication["providers"]} == {
        "aws",
        "azure",
        "gcp",
    }
    for item in adjudication["providers"]:
        raw_path = ROOT / item["rawFile"]
        result = json.loads(raw_path.read_text(encoding="utf-8"))

        assert result["outcome"] == "passed"
        assert result["cleanup"] == {"passed": True, "residual": []}


def test_e2_adjudication_preserves_provider_specific_recovery_actions() -> None:
    adjudication = json.loads(ADJUDICATION.read_text(encoding="utf-8"))
    actions = {item["provider"]: item["managedAction"] for item in adjudication["providers"]}

    assert actions == {
        "aws": "replacement",
        "azure": "replacement-after-PT30M-grace",
        "gcp": "restart-in-place",
    }
