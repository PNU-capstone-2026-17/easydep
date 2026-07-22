"""BCE 추출 결과(JSON)를 클래스 다이어그램 PlantUML로 변환한다.

jar 실행·렌더는 common.plantuml이 맡고, 여기서는 클래스 다이어그램 고유의
"무엇을 그릴지"(스테레오타입·필드·메서드·관계 매핑)만 다룬다.
"""
from __future__ import annotations

import re
from typing import Any


def sanitize_class_name(name: str) -> str:
    if not name:
        return "UnknownClass"
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def sanitize_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("‑", "-")


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

        clean_stereotype = stereotype_raw.replace("<", "").replace(">", "").strip()
        stereo_tag = f" <<{clean_stereotype}>>" if clean_stereotype else ""

        puml_lines.append(f"class {class_name}{stereo_tag} {{")

        for field in class_item.get("fields", []):
            clean_field = sanitize_text(field)
            puml_lines.append(f"  - {clean_field}")

        for method in class_item.get("methods", []):
            clean_method = sanitize_text(method)
            puml_lines.append(f"  + {clean_method}")

        puml_lines.append("}")

        if description:
            clean_description = sanitize_text(description)
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
            line += f" : {clean_description}"

        puml_lines.append(line)

    puml_lines.append("")
    puml_lines.append("@enduml")

    final_puml = "\n".join(puml_lines)
    return final_puml.replace("\xa0", " ").replace("\u200b", "")
