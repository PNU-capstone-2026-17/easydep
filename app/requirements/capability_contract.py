"""Calibrated selective decisions for requirement-derived capabilities.

The LLM never decides acceptance by asserting that it is confident.  Repeated proposal
agreement is the raw score; a reviewer-labelled development set calibrates that score and
the frozen threshold is applied without consulting holdout labels.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "easydep-capability-threshold/v1"
DEFAULT_POLICY = Path(__file__).with_name("knowledge") / "capability-threshold.json"
REALIZATION_CHANGING_FIELDS = frozenset({
    "provider", "region", "security", "availability", "scale", "budget"
})
SUPPORTED_DEPENDENCY_CAPABILITY_IDS = frozenset({
    "persistent-block-storage",
    "load-balanced-ingress",
    "https-load-balanced-ingress",
})
_LINK_TOKEN = re.compile(r"[a-z0-9]+")


def link_dependency_capability(
    key: str, role: str, evidence_spans: Iterable[str] = ()
) -> str | None:
    """Conservatively link an open need to one supported stable ID or NIL.

    Candidate tokens come only from the stable IDs themselves. A more specific
    matched ID wins only when its token set strictly contains the other match;
    unrelated multiple matches abstain.
    """
    observed = set(_LINK_TOKEN.findall(
        " ".join((key, role, *evidence_spans)).casefold().replace("_", " ")
    ))
    token_sets = {
        capability_id: set(_LINK_TOKEN.findall(capability_id))
        for capability_id in SUPPORTED_DEPENDENCY_CAPABILITY_IDS
    }
    matched = {
        capability_id: tokens
        for capability_id, tokens in token_sets.items()
        if tokens <= observed
    }
    maximal = [
        capability_id
        for capability_id, tokens in matched.items()
        if not any(
            tokens < other_tokens
            for other_id, other_tokens in matched.items()
            if other_id != capability_id
        )
    ]
    return maximal[0] if len(maximal) == 1 else None


@dataclass(frozen=True)
class CalibrationPoint:
    raw_score: float
    correct: bool


def _pava(points: Iterable[CalibrationPoint]) -> list[dict[str, float | int]]:
    """Return a deterministic isotonic mapping using pooled adjacent violators."""
    grouped: dict[float, list[int]] = {}
    for point in points:
        grouped.setdefault(float(point.raw_score), []).append(int(point.correct))
    blocks = [
        {"low": score, "high": score, "correct": sum(values), "count": len(values)}
        for score, values in sorted(grouped.items())
    ]
    index = 0
    while index < len(blocks) - 1:
        left, right = blocks[index], blocks[index + 1]
        left_mean = float(left["correct"]) / int(left["count"])
        right_mean = float(right["correct"]) / int(right["count"])
        if left_mean <= right_mean:
            index += 1
            continue
        blocks[index : index + 2] = [{
            "low": left["low"],
            "high": right["high"],
            "correct": int(left["correct"]) + int(right["correct"]),
            "count": int(left["count"]) + int(right["count"]),
        }]
        index = max(0, index - 1)
    return [{
        "low": float(block["low"]),
        "high": float(block["high"]),
        "value": float(block["correct"]) / int(block["count"]),
        "count": int(block["count"]),
    } for block in blocks]


def calibrated_score(raw_score: float, mapping: list[dict[str, Any]]) -> float | None:
    for block in mapping:
        if float(block["low"]) <= raw_score <= float(block["high"]):
            return float(block["value"])
    return None


def wilson_lower(correct: int, count: int, z: float = 1.959963984540054) -> float:
    if count <= 0:
        return 0.0
    proportion = correct / count
    denominator = 1 + z * z / count
    centre = proportion + z * z / (2 * count)
    margin = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * count)) / count)
    return (centre - margin) / denominator


def fit_policy(points: Iterable[CalibrationPoint], *, version: str) -> dict[str, Any]:
    observations = list(points)
    mapping = _pava(observations)
    candidates = sorted({block["value"] for block in mapping})
    threshold: float | None = None
    qualification: dict[str, Any] | None = None
    scored = [(calibrated_score(item.raw_score, mapping), item.correct) for item in observations]
    for candidate in candidates:
        selected = [correct for score, correct in scored if score is not None and score >= candidate]
        if not selected:
            continue
        correct = sum(selected)
        precision = correct / len(selected)
        lower = wilson_lower(correct, len(selected))
        if precision >= 0.90 and lower >= 0.80:
            threshold = float(candidate)
            qualification = {
                "selected": len(selected), "correct": correct,
                "precision": precision, "wilsonLower95": lower,
            }
            break
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "frozen",
        "version": version,
        "method": "isotonic-pava",
        "sampleCount": len(observations),
        "targetPrecision": 0.90,
        "minimumWilsonLower95": 0.80,
        "autoAcceptEnabled": threshold is not None,
        "acceptThreshold": threshold,
        "mapping": mapping,
        "qualification": qualification,
    }


def load_policy(path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    if not path.is_file():
        return {
            "schemaVersion": SCHEMA_VERSION,
            "status": "unfitted",
            "version": "unfitted",
            "autoAcceptEnabled": False,
            "acceptThreshold": None,
            "mapping": [],
        }
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("unsupported capability threshold policy")
    return policy


def decide(
    *, raw_score: float, origin: str, evidence_valid: bool,
    unresolved_fields: Iterable[str], impossible: bool = False,
    out_of_scope: bool = False, policy: dict[str, Any] | None = None,
) -> tuple[str, str, float | None]:
    """Apply hard safety gates before the calibrated selective-prediction rule."""
    unresolved = {str(item) for item in unresolved_fields}
    if impossible:
        return "abstained", "logically-impossible", None
    if out_of_scope:
        return "abstained", "model-out-of-scope", None
    if not evidence_valid:
        return "needsQuestion", "missing-or-ungrounded-evidence", None
    if unresolved & REALIZATION_CHANGING_FIELDS:
        return "needsQuestion", "realization-changing-ambiguity", None
    if origin == "explicit":
        return "accepted", "explicit-grounded-constraint", 1.0
    selected_policy = policy or load_policy()
    score = calibrated_score(raw_score, selected_policy.get("mapping") or [])
    threshold = selected_policy.get("acceptThreshold")
    if (
        selected_policy.get("autoAcceptEnabled") is True
        and score is not None and threshold is not None and score >= float(threshold)
    ):
        return "accepted", "calibrated-threshold-met", score
    return "needsQuestion", "calibrated-threshold-not-met", score


def accepted_needs(needs: dict[str, Any]) -> dict[str, Any]:
    """Project accepted capabilities for downstream agents during the v1 migration."""
    return {
        key: value for key, value in needs.items()
        if isinstance(value, dict) and value.get("decision", "accepted") == "accepted"
    }


def requires_persistent_storage(needs: dict[str, Any]) -> bool:
    """Interpret the currently supported persistence capability without losing polarity.

    ``required`` expresses constraint strength, not whether a Boolean metadata value is
    true.  An explicit ``persistent_application_disk: false`` therefore prohibits the
    disk realization even when that prohibition is mandatory.
    """
    accepted = accepted_needs(needs)
    semantic_need = next(
        (
            value
            for value in accepted.values()
            if isinstance((value.get("metadata") or {}).get("applicationState"), dict)
            and (value.get("metadata") or {})["applicationState"].get("durability")
            == "persistent"
        ),
        None,
    )
    need = next(
        (
            value
            for value in accepted.values()
            if "persistent-block-storage"
            in (value.get("dependencyCapabilityIds") or [])
        ),
        semantic_need or accepted.get("persistent_storage") or {},
    )
    if need.get("required") is not True:
        return False
    metadata = need.get("metadata") or {}
    return metadata.get("persistent_application_disk") is not False


def requires_load_balanced_ingress(needs: dict[str, Any]) -> bool:
    """Return whether an accepted need requests modeled load-balanced ingress."""
    for need in accepted_needs(needs).values():
        capability_ids = need.get("dependencyCapabilityIds") or []
        if not ({
            "load-balanced-ingress", "https-load-balanced-ingress"
        } & set(capability_ids)):
            continue
        if need.get("required") is not True:
            continue
        return True
    return False
