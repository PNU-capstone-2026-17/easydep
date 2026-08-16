from __future__ import annotations

from pathlib import Path

from evaluation.dependency_audit.gcp_sample_app_postgres_e1 import (
    GcpE1Recorder,
    _app_controller,
    _state_controller,
)


def test_gcp_e1_recorder_declares_narrow_scope(tmp_path: Path) -> None:
    recorder = GcpE1Recorder("test-run", tmp_path / "result.json")

    assert recorder.document["provider"] == "gcp"
    assert recorder.document["transportUnderTest"].startswith("app VM/container")
    assert "private IPv4:5432" in recorder.document["pathUnderTest"]
    assert "persistent data disk" in recorder.document["pathUnderTest"]
    assert "not course-registration behavior" in recorder.document["scope"]


def test_gcp_controllers_cover_disk_and_all_app_markers() -> None:
    state = _state_controller()
    app = _app_controller("10.80.1.4")

    assert "/dev/disk/by-id/google-state-data" in state
    assert "/var/lib/postgresql/data" in state
    assert "10.80.1.4:5432" in app
    assert "EASYDEP_E1 baseline passed" in app
    assert "EASYDEP_E1 blocked passed" in app
    assert "EASYDEP_E1 restored passed" in app
    assert "EASYDEP_E1 reboot-persistence passed" in app
    assert "course" not in app.lower()
