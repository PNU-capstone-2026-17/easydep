"""유스케이스 시나리오·클래스·시퀀스 다이어그램에서 OpenAPI 3.0 명세(JSON dict)를 생성한다."""
from __future__ import annotations

from typing import Any

from app.design.services.api_spec.extractor import (
    extract_api_elements_from_diagrams,
)
from app.design.services.api_spec.openapi_builder import (
    generate_openapi_spec_from_json,
)


def generate_api_spec_with_llm(
    class_diagram_puml: str,
    sequence_diagram_puml: str,
) -> dict[str, Any]:
    elements = extract_api_elements_from_diagrams(
        class_diagram_puml,
        sequence_diagram_puml,
    )
    return generate_openapi_spec_from_json(elements)
