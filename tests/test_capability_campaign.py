from __future__ import annotations

import copy

import pytest

from evaluation.capability_campaign import adjudicate, make_review, selective_metrics


def _proposals():
    return {
        "schemaVersion": "easydep-capability-proposals/v1",
        "proposals": [
            {"proposalId": "p1", "split": "development", "origin": "inferred", "rawScore": 1.0,
             "evidenceSpans": ["two VMs"]},
            {"proposalId": "p2", "split": "development", "origin": "inferred", "rawScore": 0.4,
             "evidenceSpans": ["HTTPS"]},
        ],
    }


def _complete(review, values):
    for item, value in zip(review["decisions"], values, strict=True):
        item.update(correct=value, reason="요구사항 원문과 대조했다.")
    return review


def test_independent_reviews_produce_reliability_and_labels():
    proposals = _proposals()
    first = _complete(make_review(proposals, "a"), [True, False])
    second = _complete(make_review(proposals, "b"), [True, False])

    labels, metrics = adjudicate(proposals, first, second, {})

    assert metrics == {"percentAgreement": 1.0, "cohenKappa": 1.0}
    assert [item["correct"] for item in labels] == [True, False]


def test_disagreement_requires_reasoned_adjudication():
    proposals = _proposals()
    first = _complete(make_review(proposals, "a"), [True, False])
    second = copy.deepcopy(_complete(make_review(proposals, "b"), [False, False]))

    with pytest.raises(ValueError, match="unresolved"):
        adjudicate(proposals, first, second, {})


def test_selective_metrics_separate_coverage_from_accepted_error():
    labels = [
        {"origin": "inferred", "rawScore": 1.0, "correct": True},
        {"origin": "inferred", "rawScore": 0.5, "correct": False},
    ]
    policy = {"autoAcceptEnabled": True, "acceptThreshold": 0.9, "mapping": [
        {"low": 0.5, "high": 0.5, "value": 0.2},
        {"low": 1.0, "high": 1.0, "value": 1.0},
    ]}

    result = selective_metrics(labels, policy)

    assert result["coverage"] == 0.5
    assert result["acceptedPrecision"] == 1.0
