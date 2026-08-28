"""이전 PlantUML 기반 API 추출 import를 보존하는 호환 어댑터다.

새 production 경계는 :mod:`app.design.services.api_spec.service`이며 typed BCE와
sequence 모델만 받는다. 이 모듈은 기존 체크포인트·호출자가 바뀌는 동안 PlantUML 입력을
``legacy`` 모듈에 가두고 종전 dict 반환 shape를 유지한다.
"""
from __future__ import annotations

from typing import Any

from app.design.services.api_spec.legacy import (
    LEGACY_API_SPEC_EXTRACTION_SYSTEM_PROMPT as API_SPEC_EXTRACTION_SYSTEM_PROMPT,
)
from app.design.services.api_spec.legacy import (
    control_parameter_types as _legacy_control_parameter_types,
)
from app.design.services.api_spec.legacy import (
    control_return_types as _legacy_control_return_types,
)
from app.design.services.api_spec.legacy import (
    legacy_api_spec_messages,
)
from app.design.services.api_spec.models import (
    ApiControlArgument,
    ApiControlBinding,
    ApiControlOutcome,
    ApiEndpoint,
    ApiField,
    ApiResponse,
    ApiSchema,
    ApiSpecModel,
)
from app.design.services.api_spec.normalization import (
    control_contracts_from_payload,
    normalize_api_spec_payload,
)
from app.design.services.common.structured import parse_structured


def api_spec_messages(
    scenario_text: str,
    class_diagram_puml: str,
    sequence_diagram_puml: str,
) -> list[dict[str, str]]:
    """기존 PlantUML 호출 메시지를 legacy 경계에서 만든다."""

    return legacy_api_spec_messages(
        scenario_text, class_diagram_puml, sequence_diagram_puml
    )


def extract_api_spec_model(
    scenario_text: str,
    class_diagram_puml: str,
    sequence_diagram_puml: str,
    class_model: Any | None = None,
) -> dict[str, Any]:
    """이전 PlantUML 입력으로 모델을 한 번 제안하고 기존 dict shape를 반환한다."""

    if not scenario_text:
        return {}
    model = parse_structured(
        api_spec_messages(scenario_text, class_diagram_puml, sequence_diagram_puml),
        ApiSpecModel,
    )
    return normalize_api_spec_model(model, class_diagram_puml, class_model)


def _control_parameter_types(
    class_diagram_puml: str,
    class_model: Any | None = None,
) -> dict[tuple[str, str], dict[str, str]]:
    contracts = control_contracts_from_payload(class_model)
    return contracts[0] if contracts is not None else _legacy_control_parameter_types(
        class_diagram_puml
    )


def _control_return_types(
    class_diagram_puml: str,
    class_model: Any | None = None,
) -> dict[tuple[str, str], str]:
    contracts = control_contracts_from_payload(class_model)
    return contracts[1] if contracts is not None else _legacy_control_return_types(
        class_diagram_puml
    )


def normalize_api_spec_model(
    model: dict[str, Any],
    class_diagram_puml: str = "",
    class_model: Any | None = None,
) -> dict[str, Any]:
    """기존 느슨한 입력 우선순위로 API payload를 제자리 정규화한다."""

    if not isinstance(model, dict):
        return model
    contracts = control_contracts_from_payload(class_model)
    if contracts is None:
        contracts = (
            _legacy_control_parameter_types(class_diagram_puml),
            _legacy_control_return_types(class_diagram_puml),
        )
    return normalize_api_spec_payload(model, *contracts)


__all__ = [
    "API_SPEC_EXTRACTION_SYSTEM_PROMPT",
    "ApiControlArgument",
    "ApiControlBinding",
    "ApiControlOutcome",
    "ApiEndpoint",
    "ApiField",
    "ApiResponse",
    "ApiSchema",
    "ApiSpecModel",
    "api_spec_messages",
    "extract_api_spec_model",
    "normalize_api_spec_model",
]
