from __future__ import annotations

from evaluation.research_protocol.commands import readiness as module


def test_draft_protocol_and_missing_models_block_confirmatory_run(tmp_path, monkeypatch):
    protocol = tmp_path / "protocol.json"
    anchors = tmp_path / "decision-anchors.json"
    protocol.write_text('{"status":"draft-not-frozen"}', encoding="utf-8")
    anchors.write_text('{"status":"development"}', encoding="utf-8")
    monkeypatch.setattr(module, "PROTOCOL", protocol)
    monkeypatch.setattr(module, "ANCHORS", anchors)
    monkeypatch.setattr(module, "NATIVE_DIR", tmp_path / "missing")
    monkeypatch.setattr(module, "REALIZATIONS", tmp_path / "missing-realizations.json")
    monkeypatch.setattr(module, "INTERVENTIONS", tmp_path / "missing-interventions.json")
    monkeypatch.setattr(module, "RUNTIME_DEPENDENCIES", tmp_path / "missing-runtime.json")
    monkeypatch.setattr(module, "load_policy", lambda _path: {
        "version": "unfitted", "status": "unfitted", "autoAcceptEnabled": False
    })

    result = module.readiness()

    assert result["ready"] is False
    assert {item["kind"] for item in result["blockers"]} == {
        "protocol", "decisionAnchors", "capabilityCalibration", "nativeModelMissing",
        "providerRealizationInvalid", "interventionManifestInvalid",
        "runtimeDependencyModelInvalid",
    }
