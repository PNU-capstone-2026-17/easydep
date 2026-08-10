from __future__ import annotations

import pytest

from app.core.cloudkb.depkb.research_model import (
    Realization,
    minimum_realizations,
    validate_observation,
)


def test_raw_observation_rejects_predefined_or_neutral_types():
    base = {
        "provider": "aws",
        "nativeId": "ec2.RunInstances",
        "sourceIdentity": "AWS EC2 API",
        "sourceVersion": "2026-08-01",
        "sourceLocator": "RunInstances",
    }
    validate_observation(base)

    with pytest.raises(ValueError, match="premature classification"):
        validate_observation(base | {"nativeForm": "standaloneResource"})


def test_minimum_realizations_returns_all_inclusion_minimal_alternatives():
    candidates = [
        Realization("a", "aws", frozenset({"private-egress"}), frozenset({"vm", "nat"})),
        Realization("b", "aws", frozenset({"private-egress"}), frozenset({"vm", "proxy"})),
        Realization(
            "superset", "aws", frozenset({"private-egress"}),
            frozenset({"vm", "nat", "extra"}),
        ),
        Realization("other", "gcp", frozenset({"private-egress"}), frozenset({"vm", "nat"})),
    ]

    result = minimum_realizations({"private-egress"}, "aws", {}, candidates)

    assert [item.id for item in result] == ["a", "b"]


def test_unconfirmed_realization_cannot_drive_a_plan():
    result = minimum_realizations(
        {"ingress"}, "azure", {},
        [Realization(
            "exploratory", "azure", frozenset({"ingress"}), frozenset({"lb"}),
            status="exploratory",
        )],
    )
    assert result == ()
