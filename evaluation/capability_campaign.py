"""Capability 제안의 독립 검토, 합의 정답 및 선택적 예측 지표를 관리한다."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.cloudkb.depkb.reliability import cohen_kappa, percent_agreement
from app.requirements.resources.capability_contract import calibrated_score

PROPOSAL_SCHEMA = "easydep-capability-proposals/v1"
REVIEW_SCHEMA = "easydep-capability-review/v1"


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def make_review(proposals: dict[str, Any], reviewer: str) -> dict[str, Any]:
    validate_proposals(proposals)
    if not reviewer.strip():
        raise ValueError("reviewer identity is required")
    return {
        "schemaVersion": REVIEW_SCHEMA,
        "proposalsSha256": _digest(proposals),
        "reviewer": reviewer,
        "decisions": [
            {"proposalId": item["proposalId"], "correct": None, "reason": ""}
            for item in proposals["proposals"]
        ],
    }


def validate_proposals(document: dict[str, Any]) -> None:
    if document.get("schemaVersion") != PROPOSAL_SCHEMA:
        raise ValueError("unsupported capability proposal schema")
    proposals = document.get("proposals")
    if not isinstance(proposals, list) or not proposals:
        raise ValueError("capability proposals are empty")
    ids = [item.get("proposalId") for item in proposals]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("proposalId must be present and unique")
    for item in proposals:
        if item.get("split") not in {"development", "holdout"}:
            raise ValueError("proposal split is invalid")
        if item.get("origin") not in {"explicit", "inferred"}:
            raise ValueError("proposal origin is invalid")
        score = item.get("rawScore")
        if not isinstance(score, (int, float)) or not 0 <= score <= 1:
            raise ValueError("rawScore must be between zero and one")
        if not item.get("evidenceSpans"):
            raise ValueError("proposal requires exact evidence spans")


def _decisions(
    proposals: dict[str, Any], review: dict[str, Any], *, complete: bool,
) -> dict[str, dict[str, Any]]:
    if review.get("schemaVersion") != REVIEW_SCHEMA:
        raise ValueError("unsupported capability review schema")
    if review.get("proposalsSha256") != _digest(proposals):
        raise ValueError("review points to different proposals")
    expected = {item["proposalId"] for item in proposals["proposals"]}
    values = review.get("decisions")
    if not isinstance(values, list) or len(values) != len(expected):
        raise ValueError("review must cover every proposal exactly once")
    indexed = {item.get("proposalId"): item for item in values}
    if set(indexed) != expected:
        raise ValueError("review proposal ids do not match")
    for item in values:
        if complete and not isinstance(item.get("correct"), bool):
            raise ValueError("review is incomplete")
        if isinstance(item.get("correct"), bool) and not str(item.get("reason") or "").strip():
            raise ValueError("completed decision requires a reason")
    return indexed


def adjudicate(
    proposals: dict[str, Any], first: dict[str, Any], second: dict[str, Any],
    resolutions: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    validate_proposals(proposals)
    left = _decisions(proposals, first, complete=True)
    right = _decisions(proposals, second, complete=True)
    if first.get("reviewer") == second.get("reviewer"):
        raise ValueError("two distinct reviewers are required")
    pairs = [(str(left[key]["correct"]), str(right[key]["correct"])) for key in sorted(left)]
    metrics = {"percentAgreement": percent_agreement(pairs), "cohenKappa": cohen_kappa(pairs)}
    labels: list[dict[str, Any]] = []
    by_id = {item["proposalId"]: item for item in proposals["proposals"]}
    for proposal_id in sorted(left):
        if left[proposal_id]["correct"] == right[proposal_id]["correct"]:
            correct = left[proposal_id]["correct"]
        else:
            resolution = resolutions.get(proposal_id)
            if not isinstance(resolution, dict) or not isinstance(resolution.get("correct"), bool):
                raise ValueError(f"unresolved reviewer disagreement: {proposal_id}")
            if not str(resolution.get("reason") or "").strip():
                raise ValueError(f"adjudication reason is required: {proposal_id}")
            correct = resolution["correct"]
        proposal = by_id[proposal_id]
        labels.append({
            "proposalId": proposal_id, "split": proposal["split"],
            "rawScore": proposal["rawScore"], "correct": correct,
            "origin": proposal["origin"],
            "reviewerA": first["reviewer"], "reviewerB": second["reviewer"],
        })
    return labels, metrics


def selective_metrics(labels: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    if any(item.get("origin") != "inferred" for item in labels):
        raise ValueError("selective calibration metrics require inferred capabilities only")
    threshold = policy.get("acceptThreshold")
    accepted: list[bool] = []
    correct: list[bool] = []
    for item in labels:
        score = calibrated_score(float(item["rawScore"]), policy.get("mapping") or [])
        is_accepted = bool(
            policy.get("autoAcceptEnabled") and threshold is not None
            and score is not None and score >= float(threshold)
        )
        accepted.append(is_accepted)
        correct.append(bool(item["correct"]))
    selected = [ok for chosen, ok in zip(accepted, correct, strict=True) if chosen]
    errors = sum(not value for value in selected)
    return {
        "sampleCount": len(labels), "acceptedCount": len(selected),
        "questionOrAbstentionCount": len(labels) - len(selected),
        "coverage": len(selected) / len(labels) if labels else 0.0,
        "acceptedPrecision": sum(selected) / len(selected) if selected else None,
        "acceptedErrorRate": errors / len(selected) if selected else None,
    }


def write_labels(path: Path, labels: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in labels),
        encoding="utf-8",
    )
