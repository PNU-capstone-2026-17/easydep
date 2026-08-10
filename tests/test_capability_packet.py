from __future__ import annotations

import json

import pytest

from evaluation.research_protocol.commands import capability_packet


def test_packet_reads_only_development_and_records_provenance(tmp_path, monkeypatch):
    input_path = tmp_path / "dev.json"
    input_path.write_text(json.dumps({
        "name": "dev", "classified": [{"id": "N1", "text": "durable", "type": "NFR"}],
    }), encoding="utf-8")
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({
        "development": ["dev.json"], "holdout": ["secret.json"],
    }), encoding="utf-8")
    monkeypatch.setattr(capability_packet, "derive_deployment_needs", lambda _state: {
        "capability_contract": {"capabilities": [{
            "id": "durability", "statement": "Preserve state", "requirementIds": ["N1"],
            "evidenceSpans": ["durable"], "origin": "inferred", "rawConfidence": 0.8,
            "decision": "needsQuestion", "decisionReason": "calibrated-threshold-not-met",
        }]},
    })

    packet = capability_packet.build_packet(suite)

    assert packet["holdoutAccessed"] is False
    assert packet["proposals"][0]["split"] == "development"
    assert len(packet["inputs"][0]["sha256"]) == 64


def test_packet_rejects_holdout_like_development_path(tmp_path):
    path = tmp_path / "holdout-leak.json"
    path.write_text("{}", encoding="utf-8")
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({
        "development": [path.name], "holdout": ["other.json"],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="holdout-like"):
        capability_packet.build_packet(suite)
