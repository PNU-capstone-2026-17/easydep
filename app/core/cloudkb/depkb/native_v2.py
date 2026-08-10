"""Control-plane 기반 Native v2 관측·표본감사·검토·동결 계약."""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter
from collections.abc import Iterable
from typing import Any

from .reliability import cohen_kappa, krippendorff_alpha_nominal, percent_agreement
from .research_model import validate_observation

OBSERVATION_SCHEMA = "easydep-native-observations/v2"
REVIEW_SCHEMA = "easydep-native-review/v2"
FROZEN_SCHEMA = "easydep-native-model/v2"
PROVIDERS = frozenset({"aws", "azure", "gcp"})
CHANNELS = frozenset({"operation", "path", "schema", "providerBehavior"})


def _digest(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def validate_inventory(document: dict[str, Any]) -> None:
    if document.get("schemaVersion") != OBSERVATION_SCHEMA:
        raise ValueError("unsupported Native v2 observation schema")
    if document.get("provider") not in PROVIDERS:
        raise ValueError("unsupported provider")
    source = document.get("source")
    if not isinstance(source, dict) or not all(
        source.get(key) for key in ("identity", "version", "sha256")
    ):
        raise ValueError("pinned source identity, version, and digest are required")
    anchors = document.get("decisionAnchors")
    if not isinstance(anchors, list) or not anchors:
        raise ValueError("at least one evidenced decision anchor is required")
    for anchor in anchors:
        if not anchor.get("capabilityId") or not anchor.get("sourceLocator"):
            raise ValueError("decision anchor lacks capability or source locator")
        if not anchor.get("requirementIds") or not anchor.get("evidenceSpans"):
            raise ValueError("decision anchor lacks requirement evidence")
    observations = document.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ValueError("Native v2 inventory has no observations")
    ids: list[str] = []
    for item in observations:
        validate_observation(item)
        if item["provider"] != document["provider"]:
            raise ValueError("observation provider differs from inventory")
        if item.get("observationChannel") not in CHANNELS:
            raise ValueError("invalid observation channel")
        if not item.get("serviceFamily"):
            raise ValueError("serviceFamily is required for boundary sampling")
        ids.append(item["nativeId"])
    duplicates = [key for key, count in Counter(ids).items() if count > 1]
    if duplicates:
        raise ValueError("duplicate Native v2 observation ids")


def boundary_sample(
    observations: Iterable[dict[str, Any]], *, seed: int = 42,
    fraction: float = 0.20, minimum: int = 5, maximum: int = 20,
) -> dict[str, Any]:
    """Provider×service family×관측 채널별 결정론적 경계 표본을 반환한다."""
    strata: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in observations:
        key = (item["provider"], item["serviceFamily"], item["observationChannel"])
        strata.setdefault(key, []).append(item)
    selected: list[str] = []
    details: list[dict[str, Any]] = []
    for key, values in sorted(strata.items()):
        ordered = sorted(values, key=lambda value: value["nativeId"])
        count = min(maximum, len(ordered), max(minimum, math.ceil(len(ordered) * fraction)))
        rng = random.Random(f"{seed}:{':'.join(key)}")  # noqa: S311 - preregistered sampling
        chosen = sorted(item["nativeId"] for item in rng.sample(ordered, count))
        selected.extend(chosen)
        details.append({
            "provider": key[0], "serviceFamily": key[1],
            "observationChannel": key[2], "population": len(ordered),
            "selected": len(chosen), "nativeIds": chosen,
        })
    return {
        "schemaVersion": "easydep-native-boundary-sample/v1",
        "seed": seed, "fraction": fraction, "minimum": minimum, "maximum": maximum,
        "strata": details, "selectedNativeIds": sorted(selected),
    }


def review_scope(inventory: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
    validate_inventory(inventory)
    available = {item["nativeId"] for item in inventory["observations"]}
    sampled = set(sample.get("selectedNativeIds") or [])
    if not sampled <= available:
        raise ValueError("boundary sample is not a subset of inventory")
    anchored = {
        item["nativeId"] for item in inventory["observations"]
        if item.get("anchorCapabilityIds")
    }
    selected = sorted(anchored | sampled)
    return {
        "schemaVersion": "easydep-native-review-scope/v1",
        "inventorySha256": _digest(inventory),
        "boundarySampleSha256": _digest(sample),
        "anchoredCount": len(anchored),
        "boundarySampleCount": len(sampled),
        "overlapCount": len(anchored & sampled),
        "selectedCount": len(selected),
        "selectedNativeIds": selected,
    }


def make_review(
    inventory: dict[str, Any], reviewer: str, *, native_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    validate_inventory(inventory)
    if not reviewer.strip():
        raise ValueError("reviewer identity is required")
    selected = set(native_ids) if native_ids is not None else {
        item["nativeId"] for item in inventory["observations"]
    }
    available = {item["nativeId"] for item in inventory["observations"]}
    if not selected or not selected <= available:
        raise ValueError("review scope must be a non-empty inventory subset")
    return {
        "schemaVersion": REVIEW_SCHEMA,
        "provider": inventory["provider"],
        "inventorySha256": _digest(inventory),
        "reviewScopeNativeIds": sorted(selected),
        "reviewer": reviewer,
        "decisions": [{
            "nativeId": item["nativeId"],
            "included": None,
            "derivedTypes": [],
            "reason": "",
            "sourceLocators": [item["sourceLocator"]],
        } for item in inventory["observations"] if item["nativeId"] in selected],
    }


def validate_review(
    inventory: dict[str, Any], review: dict[str, Any], *, complete: bool,
) -> None:
    validate_inventory(inventory)
    if review.get("schemaVersion") != REVIEW_SCHEMA:
        raise ValueError("unsupported Native v2 review schema")
    if review.get("provider") != inventory["provider"]:
        raise ValueError("review provider mismatch")
    if review.get("inventorySha256") != _digest(inventory):
        raise ValueError("review points to a different inventory")
    available = {item["nativeId"] for item in inventory["observations"]}
    expected = set(review.get("reviewScopeNativeIds") or [])
    if not expected or not expected <= available:
        raise ValueError("review scope is not a non-empty inventory subset")
    decisions = review.get("decisions")
    if not isinstance(decisions, list) or {item.get("nativeId") for item in decisions} != expected:
        raise ValueError("review must cover every observation exactly once")
    if len(decisions) != len(expected):
        raise ValueError("duplicate review decision")
    for item in decisions:
        included = item.get("included")
        if complete and not isinstance(included, bool):
            raise ValueError("review is incomplete")
        if isinstance(included, bool) and not str(item.get("reason") or "").strip():
            raise ValueError("completed review decision requires a reason")
        types = item.get("derivedTypes")
        if not isinstance(types, list) or any(not str(value).strip() for value in types):
            raise ValueError("derivedTypes must be non-empty strings when present")
        if included is False and types:
            raise ValueError("excluded observation cannot receive a derived type")


def reliability(
    inventory: dict[str, Any], first: dict[str, Any], second: dict[str, Any],
) -> dict[str, float]:
    validate_review(inventory, first, complete=True)
    validate_review(inventory, second, complete=True)
    if first["reviewScopeNativeIds"] != second["reviewScopeNativeIds"]:
        raise ValueError("reviewers used different review scopes")
    if first["reviewer"] == second["reviewer"]:
        raise ValueError("two distinct reviewers are required")
    left = {item["nativeId"]: item for item in first["decisions"]}
    right = {item["nativeId"]: item for item in second["decisions"]}
    inclusion = [
        (str(left[key]["included"]), str(right[key]["included"])) for key in sorted(left)
    ]
    types = [
        (
            "|".join(sorted(left[key]["derivedTypes"])) or "__no_type__",
            "|".join(sorted(right[key]["derivedTypes"])) or "__no_type__",
        )
        for key in sorted(left)
        if left[key]["included"] or right[key]["included"]
    ]
    return {
        "percentAgreement": percent_agreement(inclusion),
        "cohenKappaInclusion": cohen_kappa(inclusion),
        "krippendorffAlphaType": krippendorff_alpha_nominal(types),
    }


def freeze(
    inventory: dict[str, Any], first: dict[str, Any], second: dict[str, Any],
    adjudications: dict[str, dict[str, Any]], *, minimum_reliability: float = 0.70,
    expected_native_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    if expected_native_ids is not None:
        expected = sorted(set(expected_native_ids))
        if first.get("reviewScopeNativeIds") != expected:
            raise ValueError("first review differs from preregistered review scope")
        if second.get("reviewScopeNativeIds") != expected:
            raise ValueError("second review differs from preregistered review scope")
    metrics = reliability(inventory, first, second)
    if any(
        metrics[name] < minimum_reliability
        for name in ("cohenKappaInclusion", "krippendorffAlphaType")
    ):
        raise ValueError("review reliability is below the preregistered threshold")
    left = {item["nativeId"]: item for item in first["decisions"]}
    right = {item["nativeId"]: item for item in second["decisions"]}
    resolved: list[dict[str, Any]] = []
    for native_id in sorted(left):
        a, b = left[native_id], right[native_id]
        agreement = (
            a["included"] == b["included"]
            and sorted(a["derivedTypes"]) == sorted(b["derivedTypes"])
        )
        decision = a if agreement else adjudications.get(native_id)
        if not isinstance(decision, dict):
            raise TypeError(f"unresolved reviewer disagreement: {native_id}")
        if not isinstance(decision.get("included"), bool) or not decision.get("reason"):
            raise ValueError(f"invalid adjudication: {native_id}")
        resolved.append({
            "nativeId": native_id,
            "included": decision["included"],
            "derivedTypes": sorted(decision.get("derivedTypes") or []),
            "reason": decision["reason"],
        })
    model = {
        "schemaVersion": FROZEN_SCHEMA,
        "provider": inventory["provider"],
        "source": inventory["source"],
        "inventorySha256": _digest(inventory),
        "reviewers": [first["reviewer"], second["reviewer"]],
        "reviewScopeNativeIds": first["reviewScopeNativeIds"],
        "reliability": metrics,
        "decisions": resolved,
    }
    model["freeze"] = {"sha256": _digest(model)}
    return model


def validate_frozen(model: dict[str, Any]) -> None:
    if model.get("schemaVersion") != FROZEN_SCHEMA:
        raise ValueError("unsupported frozen Native v2 schema")
    freeze_record = model.get("freeze")
    if not isinstance(freeze_record, dict) or not freeze_record.get("sha256"):
        raise ValueError("Native v2 model has no freeze digest")
    unsigned = {key: value for key, value in model.items() if key != "freeze"}
    if freeze_record["sha256"] != _digest(unsigned):
        raise ValueError("Native v2 freeze digest mismatch")
    metrics = model.get("reliability") or {}
    if any(
        float(metrics.get(name, -1)) < 0.70
        for name in ("cohenKappaInclusion", "krippendorffAlphaType")
    ):
        raise ValueError("Native v2 reliability is below threshold")
    scope = model.get("reviewScopeNativeIds")
    decisions = model.get("decisions") or []
    if not isinstance(scope, list) or sorted(scope) != sorted(
        item.get("nativeId") for item in decisions
    ):
        raise ValueError("Native v2 decisions differ from frozen review scope")
