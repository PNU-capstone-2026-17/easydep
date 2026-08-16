"""Resolve an explicit cloud target without coupling it to benchmark case names."""

from __future__ import annotations

import re
from typing import Any

_PROVIDER_PATTERNS = {
    "aws": (r"\baws\b", r"\bamazon\s+web\s+services\b"),
    "azure": (r"\bazure\b", r"\bmicrosoft\s+azure\b"),
    "gcp": (r"\bgcp\b", r"\bgoogle\s+cloud(?:\s+platform)?\b"),
}


def resolve_resource_spec(
    resource_spec: dict[str, Any], resource_constraints_text: str = ""
) -> dict[str, Any]:
    """Prefer one explicit user target and reject ambiguous provider constraints."""
    explicit = {
        provider
        for provider, patterns in _PROVIDER_PATTERNS.items()
        if any(re.search(pattern, resource_constraints_text, re.IGNORECASE) for pattern in patterns)
    }
    if len(explicit) > 1:
        raise ValueError(
            "Cloud constraints name multiple target providers: "
            + ", ".join(sorted(explicit))
        )
    resolved = dict(resource_spec)
    targets = [
        dict(item)
        for item in resolved.get("deploymentTargets") or []
        if isinstance(item, dict)
    ]
    if len(targets) > 1 and not resolved.get("selectedDeploymentTarget"):
        raise ValueError(
            "Multiple deployment alternatives are available. Select one provider and "
            "region before VM selection and IaC generation."
        )
    inferred = str(resolved.get("provider") or "").strip().lower()
    if explicit:
        resolved["provider"] = next(iter(explicit))
        if inferred and inferred != resolved["provider"]:
            resolved["providerAnalysisMismatch"] = {
                "inferred": inferred,
                "explicit": resolved["provider"],
            }
    return resolved
