"""Public ResourcePlan validation boundary for downstream consumers."""

from __future__ import annotations

from typing import Any

from app.design.services.deployment_diagram.provider_template import (
    validate_complete_provider_template,
)


def validate_provider_resource_plan(plan: dict[str, Any]) -> None:
    """Reject a provider ResourcePlan that violates the accepted design contract."""

    validate_complete_provider_template(plan)


__all__ = ["validate_provider_resource_plan"]
