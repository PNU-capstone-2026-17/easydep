import json

import pytest

from evaluation.easydep.cloud_resources import review_gold


def _review():
    packet = json.loads(review_gold.PACKET.read_text(encoding="utf-8"))
    return {
        "reviewerId": "reviewer-01",
        "reviewedAt": "2026-08-07T12:00:00+09:00",
        "independenceAttestation": True,
        "cases": [
            {
                "caseId": case["caseId"],
                "mandatoryNodes": ["vm"],
                "mandatoryRelations": [],
                "rationale": "Documented independent decision.",
            }
            for case in packet["cases"]
        ],
    }


def test_review_packet_contains_no_gold_answers():
    packet = json.loads(review_gold.PACKET.read_text(encoding="utf-8"))
    assert all("mandatoryNodes" not in case for case in packet["cases"])
    assert all("mandatoryRelations" not in case for case in packet["cases"])
    assert all(case["sources"] for case in packet["cases"])


def test_valid_review_is_frozen_with_packet_hash(tmp_path):
    review = _review()
    path = tmp_path / "review.json"
    output = tmp_path / "gold.json"
    path.write_text(json.dumps(review), encoding="utf-8")
    review_gold.freeze(path, output)
    frozen = json.loads(output.read_text(encoding="utf-8"))
    assert frozen["_metadata"]["independenceStatus"] == "independently-reviewed"
    assert frozen["_metadata"]["reviewPacketSha256"] == review_gold.packet_sha256()


@pytest.mark.parametrize("field,value", [("reviewerId", "pending"), ("reviewedAt", "later")])
def test_placeholder_or_invalid_review_metadata_is_rejected(field, value):
    review = _review()
    review[field] = value
    with pytest.raises(ValueError):
        review_gold.validate_review(review)


def test_missing_independence_attestation_is_rejected():
    review = _review()
    del review["independenceAttestation"]
    with pytest.raises(ValueError):
        review_gold.validate_review(review)


def test_relation_endpoints_must_be_in_reviewed_nodes():
    review = _review()
    review["cases"][0]["mandatoryRelations"] = [["vm", "network"]]
    with pytest.raises(ValueError):
        review_gold.validate_review(review)
