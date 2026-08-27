"""검토자 간 신뢰도 계산과 파일럿 통과 기준."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

MINIMUM_RELIABILITY = 0.70


def percent_agreement(pairs: Iterable[tuple[str, str]]) -> float:
    observations = list(pairs)
    if not observations:
        raise ValueError("at least one paired decision is required")
    return sum(left == right for left, right in observations) / len(observations)


def cohen_kappa(pairs: Iterable[tuple[str, str]]) -> float:
    observations = list(pairs)
    if not observations:
        raise ValueError("at least one paired decision is required")
    labels = {label for pair in observations for label in pair}
    left = Counter(pair[0] for pair in observations)
    right = Counter(pair[1] for pair in observations)
    observed = percent_agreement(observations)
    expected = sum(
        left[label] / len(observations) * right[label] / len(observations)
        for label in labels
    )
    if expected == 1:
        return 1.0 if observed == 1 else 0.0
    return (observed - expected) / (1 - expected)


def krippendorff_alpha_nominal(rows: Iterable[Sequence[str | None]]) -> float:
    """Nominal alpha supporting missing reviewer values and two or more reviewers."""
    usable = [[value for value in row if value is not None] for row in rows]
    usable = [row for row in usable if len(row) >= 2]
    if not usable:
        raise ValueError("at least one item with two decisions is required")
    disagreements = 0
    pair_count = 0
    marginals: Counter[str] = Counter()
    for row in usable:
        marginals.update(row)
        for index, left in enumerate(row):
            for right in row[index + 1 :]:
                disagreements += int(left != right)
                pair_count += 1
    observed = disagreements / pair_count
    total = sum(marginals.values())
    expected = 1 - sum(count * (count - 1) for count in marginals.values()) / (
        total * (total - 1)
    )
    if expected == 0:
        return 1.0 if observed == 0 else 0.0
    return 1 - observed / expected


def pilot_passes(*metrics: float, minimum: float = MINIMUM_RELIABILITY) -> bool:
    if not metrics:
        raise ValueError("at least one reliability metric is required")
    return all(metric >= minimum for metric in metrics)
