"""typed 전환 전 API 호출자를 위한 PlantUML 호환 어댑터다.

production 생성은 ``BCEModel``과 ``SequenceCollection``을 받는다. 체크포인트와 이전
내부 호출자는 이 격리된 어댑터를 통해 기존 동작을 얻되 PlantUML을 service 입력으로
되돌리지 않는다.
"""
from __future__ import annotations

import re

from app.design.services.api_spec.prompts import (
    API_SPEC_EXTRACTION_SYSTEM_PROMPT,
    API_SPEC_REVISION_SYSTEM_PROMPT,
)

LEGACY_API_SPEC_EXTRACTION_SYSTEM_PROMPT = (
    API_SPEC_EXTRACTION_SYSTEM_PROMPT.replace(
        "the accepted analysis-level BCE class model, and its deterministic typed sequence\n"
        "model.",
        "the analysis-level class diagram, and the sequence diagram derived from them.",
    )
    .replace("in the sequence model", "in the sequence diagram")
    .replace("in the class model", "in the class diagram")
    .replace("in the BCE model", "in the class diagram")
    .replace("from the BCE model", "from the class diagram")
    .replace("given BCE\n    model", "given class\n    diagram")
)
LEGACY_API_SPEC_REVISION_SYSTEM_PROMPT = (
    API_SPEC_REVISION_SYSTEM_PROMPT.replace(
        "accepted BCE model, and deterministic sequence model it\nwas derived from",
        "class diagram, and sequence diagram it\nwas derived from",
    ).replace("in the sequence model", "in the sequence diagram")
)


def legacy_api_spec_messages(
    scenario_text: str,
    class_diagram_puml: str,
    sequence_diagram_puml: str,
) -> list[dict[str, str]]:
    """이전 PlantUML prompt envelope를 만든다."""

    return [
        {
            "role": "system",
            "content": LEGACY_API_SPEC_EXTRACTION_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": (
                f"[Use Case Specification]\n{scenario_text}\n\n"
                f"[Class Diagram PlantUML]\n{class_diagram_puml}\n\n"
                f"[Sequence Diagrams PlantUML]\n{sequence_diagram_puml}"
            ),
        },
    ]


def control_parameter_types(class_diagram_puml: str) -> dict[tuple[str, str], dict[str, str]]:
    """이전 class 투영에서 Control parameter 계약을 읽는다."""

    result: dict[tuple[str, str], dict[str, str]] = {}
    class_pattern = re.compile(
        r"(?ms)^\s*class\s+(?P<class>[A-Za-z_]\w*)[^\{]*\{(?P<body>.*?)^\s*\}"
    )
    method_pattern = re.compile(
        r"^\s*[+\-#]\s*(?P<name>[A-Za-z_]\w*)\s*\((?P<params>[^)]*)\)"
        r"\s*(?::\s*[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?(?:<[^>]+>)?)?\s*$",
        re.MULTILINE,
    )
    for match in class_pattern.finditer(class_diagram_puml or ""):
        if not re.search(r"<<\s*Control\s*>>", match.group(0), re.IGNORECASE):
            continue
        for method in method_pattern.finditer(match.group("body")):
            parameters: dict[str, str] = {}
            for raw in method.group("params").split(","):
                name, separator, type_name = raw.strip().partition(":")
                if separator and name.strip() and type_name.strip():
                    parameters[name.strip()] = type_name.strip()
            result[(match.group("class"), method.group("name"))] = parameters
    return result


def control_return_types(class_diagram_puml: str) -> dict[tuple[str, str], str]:
    """이전 class 투영에서 Control 반환 계약을 읽는다."""

    result: dict[tuple[str, str], str] = {}
    class_pattern = re.compile(
        r"(?ms)^\s*class\s+(?P<class>[A-Za-z_]\w*)[^\{]*\{(?P<body>.*?)^\s*\}"
    )
    method_pattern = re.compile(
        r"^\s*[+\-#]\s*(?P<name>[A-Za-z_]\w*)\s*\([^)]*\)\s*"
        r":\s*(?P<return>[^\s]+(?:<[^>]+>)?)\s*$",
        re.MULTILINE,
    )
    for match in class_pattern.finditer(class_diagram_puml or ""):
        if not re.search(r"<<\s*Control\s*>>", match.group(0), re.IGNORECASE):
            continue
        for method in method_pattern.finditer(match.group("body")):
            result[(match.group("class"), method.group("name"))] = method.group(
                "return"
            ).strip()
    return result
