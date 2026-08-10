from __future__ import annotations

from app.core.cloudkb.depkb.reliability import (
    cohen_kappa,
    krippendorff_alpha_nominal,
    percent_agreement,
    pilot_passes,
)


def test_perfect_review_agreement_scores_one():
    pairs = [("included", "included"), ("excluded", "excluded")]
    assert percent_agreement(pairs) == 1
    assert cohen_kappa(pairs) == 1
    assert krippendorff_alpha_nominal(pairs) == 1


def test_chance_corrected_metrics_expose_disagreement():
    pairs = [
        ("included", "included"),
        ("included", "excluded"),
        ("excluded", "included"),
        ("excluded", "excluded"),
    ]
    assert percent_agreement(pairs) == 0.5
    assert cohen_kappa(pairs) == 0
    assert krippendorff_alpha_nominal(pairs) < 0.7
    assert pilot_passes(cohen_kappa(pairs), krippendorff_alpha_nominal(pairs)) is False
