"""벤더 중립 모델의 추상화 근거와 연구 내 사용 범위를 검증한다."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

ROLES = {"candidateSource", "methodComparator", "boundaryComparator"}
LEVELS = {"iaas-resource", "topology-metamodel", "application-runtime", "user-defined-composite", "provider-schema"}


def validate_neutral_evidence(document: dict[str, Any]) -> None:
    if document.get("schemaVersion") != "easydep-neutral-model-evidence/v1":
        raise ValueError("unsupported neutral evidence registry")
    ids: set[str] = set()
    for model in document.get("models") or []:
        if not model.get("id") or model["id"] in ids:
            raise ValueError("missing or duplicate model id")
        ids.add(model["id"])
        if model.get("researchRole") not in ROLES or model.get("abstractionLevel") not in LEVELS:
            raise ValueError("invalid research role or abstraction level")
        if not model.get("abstractionBasis") or not model.get("correspondenceMechanism"):
            raise ValueError("model requires its abstraction rationale and correspondence mechanism")
        if not model.get("knownLimits") or not model.get("easydepUse"):
            raise ValueError("model requires limits and an explicit use")
        sources = model.get("sources") or []
        if not sources:
            raise ValueError("model requires primary sources")
        for source in sources:
            url = urlparse(source.get("url", ""))
            if url.scheme != "https" or not source.get("locator") or not source.get("version"):
                raise ValueError("source requires HTTPS URL, version, and locator")
