"""Shared run identifiers and manifest identity fields."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any


def safe_segment(value: str, fallback: str) -> str:
    segment = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return segment or fallback


def make_run_id(
    system: str,
    variant: str,
    case_id: str = "adhoc",
    *,
    now: datetime | None = None,
    short_id: str | None = None,
) -> str:
    """Return ``system-variant-case-UTC-shortid`` with filesystem-safe segments."""
    stamp = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = safe_segment(short_id or uuid.uuid4().hex[:6], "run")
    return "-".join(
        (
            safe_segment(system, "system"),
            safe_segment(variant, "standard"),
            safe_segment(case_id, "adhoc"),
            stamp,
            suffix,
        )
    )


def identity_manifest(
    run_id: str,
    *,
    system: str,
    variant: str,
    case_id: str = "adhoc",
    purpose: str = "normal",
    completed_stages: list[str] | None = None,
) -> dict[str, Any]:
    """Return the identity fields shared by EasyDep and baseline manifests."""
    return {
        "runId": run_id,
        "system": system,
        "variant": variant,
        "caseId": case_id or "adhoc",
        "purpose": purpose,
        "completedStages": completed_stages or [],
    }
