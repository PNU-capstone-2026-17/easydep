"""기존 dict·PlantUML 기반 API 수정 호출을 보존하는 호환 어댑터다."""
from __future__ import annotations

from typing import Any

from app.design.services.api_spec.extractor import normalize_api_spec_model
from app.design.services.api_spec.legacy import (
    LEGACY_API_SPEC_REVISION_SYSTEM_PROMPT as API_SPEC_REVISION_SYSTEM_PROMPT,
)
from app.design.services.api_spec.models import ApiSpecModel
from app.design.services.common.structured import parse_structured, revision_messages


def revise_api_spec_model(
    current_model: dict[str, Any],
    feedback: str,
    context_text: str = "",
    targets: set[str] | None = None,
    class_diagram_puml: str = "",
    class_model: Any | None = None,
) -> dict[str, Any]:
    """기존 수정 envelope와 dict 반환 shape를 유지해 typed 전환을 연결한다."""

    if not current_model or not feedback:
        return current_model or {}
    revised = parse_structured(
        revision_messages(
            API_SPEC_REVISION_SYSTEM_PROMPT,
            "Use Case Specification, Class Diagram and Sequence Diagram",
            context_text,
            "Current API Endpoint Model",
            current_model,
            feedback,
            targets,
        ),
        ApiSpecModel,
    )
    return normalize_api_spec_model(revised, class_diagram_puml, class_model)


__all__ = ["API_SPEC_REVISION_SYSTEM_PROMPT", "revise_api_spec_model"]
