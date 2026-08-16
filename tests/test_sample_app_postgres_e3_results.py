from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path("evaluation/dependency_audit")
ADJUDICATION = ROOT / "multi-provider-sample-app-postgres-e3-adjudication-20260815.json"


def test_selected_e3_results_pass_match_hashes_and_clean_up() -> None:
    adjudication = json.loads(ADJUDICATION.read_text(encoding="utf-8"))

    assert {item["provider"] for item in adjudication["providers"]} == {
        "aws",
        "azure",
        "gcp",
    }
    for item in adjudication["providers"]:
        path = ROOT / item["rawFile"]
        result = json.loads(path.read_text(encoding="utf-8"))

        canonical = path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")
        assert hashlib.sha256(canonical).hexdigest() == item["sha256CanonicalLf"]
        assert result["provider"] == item["provider"]
        assert result["outcome"] == "passed"
        assert result["cleanup"] == {"passed": True, "residual": []}
        assert result["transportUnderTest"].startswith("app VM/container")
        assert result["observations"] == {
            "baselineWriteReadPassed": True,
            "stateVmWasReplaced": True,
            "sameDataDiskWasReattached": True,
            "appImageWasNotRebuilt": True,
            "existingValueReadAfterReplacement": True,
        }


def test_e3_rebinds_changed_private_endpoint_without_recreating_app() -> None:
    adjudication = json.loads(ADJUDICATION.read_text(encoding="utf-8"))

    for item in adjudication["providers"]:
        result = json.loads((ROOT / item["rawFile"]).read_text(encoding="utf-8"))
        observation = result["runtimeEndpointObservation"]

        assert observation["initialStatePrivateIp"] != observation["replacementStatePrivateIp"]
        assert observation["appVmRecreated"] is False
        assert observation["appImageRebuilt"] is False
