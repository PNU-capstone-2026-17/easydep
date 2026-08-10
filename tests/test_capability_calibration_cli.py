from __future__ import annotations

import json

import pytest

from evaluation.calibrate_capabilities import (
    build_conservative_policy,
    build_policy,
    load_labels,
)


def test_calibration_input_rejects_holdout_labels(tmp_path):
    labels = tmp_path / "labels.jsonl"
    labels.write_text(json.dumps({
        "split": "holdout", "origin": "inferred", "rawScore": 1, "correct": True,
        "reviewerA": "a", "reviewerB": "b",
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="development"):
        load_labels(labels)


def test_policy_records_exact_label_artifact_hash(tmp_path):
    labels = tmp_path / "labels.jsonl"
    rows = [
        {
            "split": "development", "origin": "inferred", "rawScore": 1, "correct": True,
            "reviewerA": "a", "reviewerB": "b",
        }
        for _ in range(20)
    ]
    labels.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

    policy = build_policy(labels, version="development-v1")

    assert policy["autoAcceptEnabled"] is True
    assert len(policy["labelsSha256"]) == 64


def test_explicit_capabilities_cannot_bias_inferred_calibration(tmp_path):
    labels = tmp_path / "labels.jsonl"
    labels.write_text(json.dumps({
        "split": "development", "origin": "explicit", "rawScore": 1,
        "correct": True, "reviewerA": "a", "reviewerB": "b",
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="only inferred"):
        load_labels(labels)


def test_no_inferred_development_packet_freezes_always_question_policy(tmp_path):
    packet = tmp_path / "proposals.json"
    packet.write_text(json.dumps({
        "schemaVersion": "easydep-capability-proposals/v1",
        "split": "development", "holdoutAccessed": False,
        "proposals": [{"origin": "explicit"}],
    }), encoding="utf-8")

    policy = build_conservative_policy(packet, version="development-v1")

    assert policy["status"] == "frozen"
    assert policy["autoAcceptEnabled"] is False
    assert policy["qualification"]["inferredCount"] == 0
