"""Deterministic VM candidate selection from structured requirements."""

from __future__ import annotations

from typing import Any

from app.core.cloudkb.costkb.agent_api import HOURS_PER_MONTH
from app.core.cloudkb.costkb.dataset import filter_specs, load_warning, resolve_region
from app.core.cloudkb.perfkb.agent_api import NOTE_OK, recommend_note

SUPPORTED_PROVIDERS = {"aws", "azure", "gcp"}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def minimum_vm_count(
    resource_spec: dict[str, Any], deployment_needs: dict[str, Any]
) -> int:
    """Derive only a conservative floor, never a complete scaling decision."""
    explicit: list[int] = []
    high_availability = bool(resource_spec.get("multiZone"))
    for need in deployment_needs.values():
        if not isinstance(need, dict):
            continue
        metadata = need.get("metadata") or {}
        if not isinstance(metadata, dict):
            continue
        for key in ("minimum_instances", "min_instances", "vm_count"):
            count = _positive_int(metadata.get(key))
            if count is not None:
                explicit.append(count)
        if metadata.get("high_availability") is True:
            high_availability = True
    if explicit:
        return max(explicit)
    return 2 if high_availability else 1


def _candidate(spec: dict[str, Any], vm_count: int) -> dict[str, Any]:
    hourly = float(spec["hourlyUSD"])
    performance = recommend_note(
        str(spec["provider"]),
        str(spec["specName"]),
        str(spec.get("id") or "") or None,
    )
    return {
        "specName": spec["specName"],
        "provider": spec["provider"],
        "region": spec["region"],
        "vCPU": spec["vCPU"],
        "memoryGiB": spec["memGiB"],
        "architecture": spec.get("architecture"),
        "hourlyListPriceUsd": round(hourly, 8),
        "monthlyComputeListPriceUsd": round(hourly * HOURS_PER_MONTH * vm_count, 2),
        "performanceEvidence": {
            "status": performance.status,
            "note": performance.text,
        },
    }


def select_vm_candidates(
    resource_spec: dict[str, Any],
    deployment_needs: dict[str, Any],
    *,
    limit: int = 5,
) -> dict[str, Any]:
    """Select cost-feasible VM candidates without inventing a capacity floor."""
    provider = str(resource_spec.get("provider") or "").lower()
    region = str(resource_spec.get("region") or "")
    minimum_vcpu = _positive_int(resource_spec.get("minVCpu"))
    minimum_memory = resource_spec.get("minMemoryGiB")
    minimum_memory = (
        float(minimum_memory)
        if isinstance(minimum_memory, (int, float)) and not isinstance(minimum_memory, bool)
        else None
    )
    base = {
        "schemaVersion": "easydep-vm-selection/v1",
        "provider": provider or None,
        "region": region or None,
        "method": "capacity-filter-then-on-demand-compute-cost",
        "catalog": "tumblebug-cost-and-performance",
    }
    if provider not in SUPPORTED_PROVIDERS:
        return {**base, "status": "deferred", "reason": "unsupported_or_missing_provider"}
    if not region:
        return {**base, "status": "deferred", "reason": "missing_region"}
    if minimum_vcpu is None and minimum_memory is None:
        return {
            **base,
            "status": "deferred",
            "reason": "missing_capacity_floor",
            "note": "The system does not assume that the smallest VM is sufficient.",
        }
    resolved_regions, resolution = resolve_region(region)
    if resolution != "exact":
        return {
            **base,
            "status": "deferred",
            "reason": "region_not_exact_in_catalog",
            "regionResolution": resolution,
            "matchedRegions": sorted(resolved_regions),
        }

    vm_count = minimum_vm_count(resource_spec, deployment_needs)
    budget = resource_spec.get("monthlyBudgetUSD")
    budget = (
        float(budget)
        if isinstance(budget, (int, float)) and not isinstance(budget, bool)
        else None
    )
    search_limit = (
        max(limit, 100)
        if resource_spec.get("trafficPattern") == "steady"
        else max(limit, 1)
    )
    specs = filter_specs(
        minimum_vcpu or 0,
        minimum_memory or 0,
        provider,
        region,
        "cost",
        search_limit,
        architecture="x86_64",
    )
    if budget is not None:
        specs = [
            spec for spec in specs
            if float(spec["hourlyUSD"]) * HOURS_PER_MONTH * vm_count <= budget
        ]
    candidates = [_candidate(spec, vm_count) for spec in specs]
    constraints = {
        "minimumVCpuPerVm": minimum_vcpu,
        "minimumMemoryGiBPerVm": minimum_memory,
        "minimumVmCount": vm_count,
        "monthlyBudgetUsd": budget,
        "hoursPerMonth": HOURS_PER_MONTH,
    }
    if not candidates:
        return {
            **base,
            "status": "infeasible",
            "reason": "no_priced_candidate_meets_capacity_and_compute_budget",
            "constraints": constraints,
            "budgetScope": "compute-only",
        }
    recommended = candidates[0]
    selection_basis = "lowest-on-demand-compute-list-price"
    if resource_spec.get("trafficPattern") == "steady":
        sustained = next(
            (
                item for item in candidates
                if item["performanceEvidence"]["status"] == NOTE_OK
            ),
            None,
        )
        if sustained is not None:
            recommended = sustained
            selection_basis = "lowest-cost-candidate-with-no-recorded-performance-warning"
    shown = [recommended]
    shown.extend(item for item in candidates if item is not recommended)
    shown = shown[: max(limit, 1)]
    warning = load_warning()
    return {
        **base,
        "status": "selected",
        "recommended": recommended,
        "selectionBasis": selection_basis,
        "candidates": shown,
        "evaluatedCandidateCount": len(candidates),
        "constraints": constraints,
        "budgetScope": "compute-only",
        "budgetInterpretation": (
            "Passing means only that on-demand VM compute list price fits. Storage, "
            "network egress, load balancers, taxes, and discounts are excluded."
        ),
        "catalogWarning": warning,
    }
