from __future__ import annotations

from pathlib import Path

from evaluation.dependency_audit.azure_sample_app_postgres_e1 import AzureE1Recorder


def test_azure_e1_recorder_declares_narrow_scope(tmp_path: Path) -> None:
    recorder = AzureE1Recorder("test-run", tmp_path / "result.json")

    assert recorder.document["provider"] == "azure"
    assert recorder.document["transportUnderTest"].startswith("app VM/container")
    assert "private IPv4:5432" in recorder.document["pathUnderTest"]
    assert "managed data disk" in recorder.document["pathUnderTest"]
    assert "not course-registration behavior" in recorder.document["scope"]
