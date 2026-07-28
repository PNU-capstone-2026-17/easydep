"""LLM이 돌려준 텍스트에서 PlantUML/JSON 산출물을 뽑아내는 공유 파서.

코드 펜스나 잡담이 섞여 와도 산출물만 건져낸다. 모든 산출물 서비스가 공유한다.
"""
from __future__ import annotations

import json
import re
from typing import Any


def extract_puml(content: str) -> str:
    match = re.search(r"(@startuml.*?@enduml)", content, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()

    cleaned = strip_code_fence(content)
    if not cleaned.startswith("@startuml"):
        cleaned = "@startuml\n" + cleaned
    if not cleaned.rstrip().endswith("@enduml"):
        cleaned = cleaned.rstrip() + "\n@enduml"
    return cleaned.strip()


def extract_json(content: str) -> dict[str, Any]:
    cleaned = strip_code_fence(content)
    return json.loads(cleaned)


def strip_code_fence(content: str) -> str:
    cleaned = content.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```plantuml"):
        cleaned = cleaned[11:]
    elif cleaned.startswith("```puml"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]

    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]

    return cleaned.strip()
