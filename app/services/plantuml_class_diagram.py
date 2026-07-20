from __future__ import annotations

import os
import re
import subprocess
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


def save_plantuml_file(puml_text: str, output_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        file.write(puml_text)


def compile_plantuml_to_image(
    puml_file: str,
    plantuml_jar_path: str = "plantuml.jar",
) -> dict[str, Any]:
    cmd = [
        "java",
        "-Djava.awt.headless=true",
        "-jar",
        plantuml_jar_path,
        "-charset",
        "UTF-8",
        "-failfast2",
        puml_file,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )

        stdout_clean = result.stdout.replace("\r", "\n").strip()
        stderr_clean = result.stderr.replace("\r", "\n").strip()
        output_log = f"{stdout_clean}\n{stderr_clean}".strip()

        if (
            result.returncode != 0
            or "Syntax Error" in output_log
            or "No diagram found" in output_log
            or "errors" in output_log.lower()
        ):
            return {
                "success": False,
                "error_message": output_log,
            }

        # Also generate SVG for high-quality fullscreen viewing
        try:
            svg_cmd = [
                "java",
                "-Djava.awt.headless=true",
                "-jar",
                plantuml_jar_path,
                "-tsvg",
                "-charset",
                "UTF-8",
                puml_file,
            ]
            subprocess.run(
                svg_cmd,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except Exception:
            pass  # SVG generation is optional

        return {"success": True, "error_message": None}
    except FileNotFoundError:
        return {
            "success": False,
            "error_message": "Java is not installed or PlantUML cannot be executed.",
        }
    except subprocess.TimeoutExpired as error:
        return {
            "success": False,
            "error_message": f"PlantUML execution timed out: {error}",
        }
    except Exception as error:
        return {"success": False, "error_message": str(error)}
