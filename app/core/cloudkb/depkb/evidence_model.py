"""공식 근거 규칙으로 판정된 CSP 모델의 동결 계약."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .evidence_policy import adjudicate

SCHEMA = "easydep-official-evidence-model/v1"
RULE_VERSION = "official-evidence-ladder/v1"


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def freeze_model(provider: str, claims: list[dict[str, Any]]) -> dict[str, Any]:
    if provider not in {"aws", "azure", "gcp"}:
        raise ValueError("unsupported provider")
    decided = [adjudicate(claim) for claim in claims]
    if not any(
        item["claimType"] == "resourceBoundary" and item["decision"] == "confirmed"
        for item in decided
    ):
        raise ValueError("model has no officially confirmed resource boundary")
    exceptions = [
        item.get("claimId") for item in decided if item["humanReviewRequired"]
    ]
    if exceptions:
        raise ValueError(f"unresolved high-impact evidence conflicts: {exceptions}")
    model = {
        "schemaVersion": SCHEMA,
        "provider": provider,
        "ruleVersion": RULE_VERSION,
        "claims": decided,
        "exceptionClaimIds": [],
    }
    model["freeze"] = {"sha256": _digest(model)}
    return model


def validate_frozen_model(model: dict[str, Any]) -> None:
    if model.get("schemaVersion") != SCHEMA or model.get("ruleVersion") != RULE_VERSION:
        raise ValueError("unsupported official evidence model")
    if model.get("provider") not in {"aws", "azure", "gcp"}:
        raise ValueError("unsupported provider")
    claims = model.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ValueError("official evidence model has no claims")
    for stored in claims:
        decided = adjudicate({
            key: value for key, value in stored.items()
            if key not in {"decision", "decisionReason", "humanReviewRequired"}
        })
        for field in ("decision", "decisionReason", "humanReviewRequired"):
            if stored.get(field) != decided[field]:
                raise ValueError("stored evidence decision differs from current rules")
    if model.get("exceptionClaimIds"):
        raise ValueError("official evidence model has unresolved exceptions")
    freeze = model.get("freeze") or {}
    unsigned = {key: value for key, value in model.items() if key != "freeze"}
    if freeze.get("sha256") != _digest(unsigned):
        raise ValueError("official evidence model freeze digest mismatch")
