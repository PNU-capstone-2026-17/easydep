from __future__ import annotations

import re
import subprocess
from typing import Any

from app.design.services.plantuml_runtime import plantuml_command


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


def render_plantuml(puml_text: str, image_format: str = "png") -> bytes:
    """Render a diagram straight to image bytes.

    Uses `-pipe`, so nothing is written to disk: artifacts live in MySQL and
    images are rebuilt from that text whenever they are requested.
    """
    result = subprocess.run(
        plantuml_command("-pipe", f"-t{image_format}"),
        input=puml_text.encode("utf-8"),
        capture_output=True,
        timeout=30,
        check=False,
    )
    return result.stdout
