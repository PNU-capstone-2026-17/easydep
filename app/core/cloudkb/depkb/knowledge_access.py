"""전달 방식과 무관하게 동일한 근거 묶음을 조회하는 경계다."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .official_dependency_model import dependencies_for, load_official_dependencies
from .projection_model import capability_realizations, load_projection


def knowledge_snapshot() -> dict[str, str]:
    """직접 프롬프트·도구·MCP가 공유할 동결 해시를 반환한다."""
    dependencies = load_official_dependencies()
    projections = load_projection()
    projection_digest = hashlib.sha256(json.dumps(
        projections, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return {
        "officialDependenciesSha256": dependencies["freeze"]["sha256"],
        "providerProjectionsSha256": projection_digest,
    }


def query_knowledge(
    *, provider: str, anchors: list[str], capability_ids: list[str]
) -> dict[str, Any]:
    """모든 접근 실험군이 사용할 순수하고 결정적인 조회 결과를 만든다."""
    return {
        "schemaVersion": "easydep-knowledge-query/v1",
        "provider": provider,
        "anchors": sorted(set(anchors)),
        "capabilityIds": sorted(set(capability_ids)),
        "officialDependencies": list(dependencies_for(provider, anchors)),
        "capabilityRealizations": [
            realization
            for capability_id in sorted(set(capability_ids))
            for realization in capability_realizations(provider, capability_id)
        ],
        "snapshot": knowledge_snapshot(),
    }
