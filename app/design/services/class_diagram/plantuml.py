"""BCE 추출 결과(JSON)를 클래스 다이어그램 PlantUML로 변환한다.

jar 실행·렌더는 common.plantuml이 맡고, 여기서는 클래스 다이어그램 고유의
"무엇을 그릴지"(스테레오타입·필드·메서드·관계 매핑)만 다룬다.

콘텐츠 문자열(필드·메서드·설명)은 LLM이 만든 자유 텍스트라 PlantUML 구조 문자를
품을 수 있다. sanitize_text가 그것을 중화하므로 이 변환은 "구성에 의해" 항상 문법적으로
유효한 PlantUML을 낸다 — 별도의 문법 수리 루프가 필요 없다.
"""
from __future__ import annotations

import re
from typing import Any


# PlantUML 구조 문자: 멤버/라벨 텍스트에 그대로 들어가면 class 본문을 조기에 닫거나
# 라벨을 깨뜨린다. 의미는 최대한 보존하며 안전한 문자로 바꾼다.
_PUML_UNSAFE = str.maketrans(
    {
        "{": "(",
        "}": ")",
        '"': "'",
        "\n": " ",
        "\r": " ",
    }
)


def sanitize_class_name(name: str) -> str:
    if not name:
        return "UnknownClass"
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def sanitize_stereotype(stereotype: str) -> str:
    """스테레오타입은 class 선언의 <<...>>에 들어가므로 단어 문자로만 남긴다."""
    cleaned = stereotype.replace("<", "").replace(">", "").strip()
    return re.sub(r"[^a-zA-Z0-9_ ]", "", cleaned).strip()


def sanitize_text(text: str) -> str:
    """멤버/설명 텍스트를 한 줄의 PlantUML-안전 토큰으로 만든다."""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("\u2011", "-")
    return text.translate(_PUML_UNSAFE)


def generate_plantuml_from_bce_json(json_data: dict[str, Any]) -> str:
    if not json_data:
        return ""

    classes = json_data.get("Classes", [])
    relationships = json_data.get("Relationships", [])

    if not classes and not relationships:
        return ""

    puml_lines = [
        "@startuml",
        "allowmixing",
        "!theme plain",
        "skinparam classAttributeIconSize 0",
        "",
    ]

    for class_item in classes:
        raw_name = class_item.get("className", "UnknownClass")
        class_name = sanitize_class_name(raw_name)
        description = class_item.get("description", "")
        stereotype_raw = class_item.get("stereotype", "")

        clean_stereotype = sanitize_stereotype(stereotype_raw)
        stereo_tag = f" <<{clean_stereotype}>>" if clean_stereotype else ""

        puml_lines.append(f"class {class_name}{stereo_tag} {{")

        for field in class_item.get("fields", []):
            clean_field = sanitize_text(field)
            if clean_field:
                puml_lines.append(f"  - {clean_field}")

        for method in class_item.get("methods", []):
            clean_method = sanitize_text(method)
            if clean_method:
                puml_lines.append(f"  + {clean_method}")

        puml_lines.append("}")

        if description:
            clean_description = sanitize_text(description)
            if clean_description:
                puml_lines.append(f"note top of {class_name} : {clean_description}")

        puml_lines.append("")

    relation_mapping = {
        "Inheritance": "<|--",
        "Dependency": "..>",
        "Association": "-->",
        "Aggregation": "o--",
        "Composition": "*--",
    }

    for relationship in relationships:
        source = sanitize_class_name(relationship.get("source", ""))
        target = sanitize_class_name(relationship.get("target", ""))
        relation_type = relationship.get("type", "Association")
        description = relationship.get("description", "")

        if relation_type == "Inheritance":
            line = f"{target} <|-- {source}"
        else:
            puml_symbol = relation_mapping.get(relation_type, "-->")
            line = f"{source} {puml_symbol} {target}"

        if description:
            clean_description = sanitize_text(description)
            if clean_description:
                line += f" : {clean_description}"

        puml_lines.append(line)

    puml_lines.append("")
    puml_lines.append("@enduml")

    final_puml = "\n".join(puml_lines)
    return final_puml.replace("\xa0", " ").replace("\u200b", "")
