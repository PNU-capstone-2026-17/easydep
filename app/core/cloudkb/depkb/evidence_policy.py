"""공식 출처의 증거 등급으로 리소스 경계와 의존성 주장을 판정한다."""
from __future__ import annotations

from typing import Any

CLAIM_TYPES = frozenset({
    "resourceBoundary", "dependencyExistence", "dependencyNecessity", "neutralCrosswalk",
})
SOURCE_ROLES = frozenset({
    "vendorLifecycleSchema", "vendorReferenceSchema", "vendorManual",
    "runtimeIntervention", "neutralModel",
})


def _valid_observations(claim: dict[str, Any]) -> list[dict[str, Any]]:
    observations = claim.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ValueError("claim requires observations")
    for item in observations:
        if item.get("sourceRole") not in SOURCE_ROLES:
            raise ValueError("unsupported evidence source role")
        if not item.get("sourceLocator") or not item.get("sourceSha256"):
            raise ValueError("evidence requires a locator and pinned digest")
        if item.get("supports") not in {True, False}:
            raise ValueError("evidence support direction must be boolean")
    return observations


def adjudicate(claim: dict[str, Any]) -> dict[str, Any]:
    """중립 모델이나 LLM의 의견이 벤더 사실로 승격되지 않게 고정 규칙을 적용한다."""
    claim_type = claim.get("claimType")
    if claim_type not in CLAIM_TYPES:
        raise ValueError("unsupported evidence claim type")
    observations = _valid_observations(claim)
    supporting = [item for item in observations if item["supports"]]
    contradicting = [item for item in observations if not item["supports"]]
    roles = {item["sourceRole"] for item in supporting}
    if contradicting and supporting:
        status, reason = "exceptionReview", "pinned-sources-conflict"
    elif contradicting:
        status, reason = "rejected", "official-or-runtime-counterevidence"
    elif claim_type == "resourceBoundary":
        lifecycle = [
            item for item in supporting
            if item["sourceRole"] == "vendorLifecycleSchema"
            and item.get("independentIdentity") is True
            and {"create", "read", "delete"} <= set(item.get("lifecycleOperations") or [])
        ]
        status = "confirmed" if lifecycle else "candidate"
        reason = "vendor-lifecycle-and-identity" if lifecycle else "lifecycle-proof-incomplete"
    elif claim_type == "dependencyExistence":
        if roles & {"vendorReferenceSchema", "vendorManual", "runtimeIntervention"}:
            status, reason = "confirmed", "vendor-reference-or-stronger-evidence"
        else:
            status, reason = "candidate", "neutral-hypothesis-only"
    elif claim_type == "dependencyNecessity":
        intervention = any(
            item["sourceRole"] == "runtimeIntervention"
            and item.get("controlPassed") is True
            and item.get("removalFailed") is True
            and item.get("restorationPassed") is True
            and int(item.get("replications") or 0) >= 3
            for item in supporting
        )
        manual = any(
            item["sourceRole"] == "vendorManual"
            and item.get("normativeRequirement") is True
            for item in supporting
        )
        if intervention:
            status, reason = "confirmed", "replicated-removal-recovery"
        elif manual:
            status, reason = "documented", "vendor-normative-prerequisite"
        else:
            status, reason = "candidate", "reference-does-not-prove-necessity"
    else:
        neutral = any(item["sourceRole"] == "neutralModel" for item in supporting)
        vendor = any(item["sourceRole"].startswith("vendor") for item in supporting)
        status = "confirmed" if neutral and vendor else "candidate"
        reason = "neutral-hypothesis-vendor-validated" if status == "confirmed" else (
            "crosswalk-side-missing"
        )
    return {
        **claim,
        "decision": status,
        "decisionReason": reason,
        "humanReviewRequired": status == "exceptionReview",
    }
