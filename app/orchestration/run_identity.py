"""cross-stage 실행이 공유하는 run ID와 manifest identity field를 만든다."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any


def safe_segment(value: str, fallback: str) -> str:
    """임의 문자열을 파일시스템에 안전한 run ID segment로 정규화한다."""

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
    """안전한 ``system-variant-case-UTC-shortid`` 형식의 실행 ID를 만든다."""
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
    """EasyDep와 baseline manifest가 공유하는 identity field를 반환한다."""
    return {
        "runId": run_id,
        "system": system,
        "variant": variant,
        "caseId": case_id or "adhoc",
        "purpose": purpose,
        "completedStages": completed_stages or [],
    }
