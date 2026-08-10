"""공식 문서에서 캡처한 사실의 재현 가능한 지문을 계산한다."""
from __future__ import annotations

import hashlib
import json
from typing import Any

CAPTURE_FIELDS = (
    "sourceLocator",
    "sourceVersion",
    "retrievedOn",
    "documentSection",
    "finding",
)


def capture_digest(capture: dict[str, Any]) -> str:
    """URL 자체가 아니라 날짜·버전·절·판독문을 함께 고정한다."""
    missing = [field for field in CAPTURE_FIELDS if not capture.get(field)]
    if missing:
        raise ValueError(f"official evidence capture is incomplete: {missing}")
    canonical = {field: capture[field] for field in CAPTURE_FIELDS}
    encoded = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
