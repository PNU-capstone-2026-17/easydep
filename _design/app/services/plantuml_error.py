from __future__ import annotations

import subprocess

from app.services.plantuml_runtime import plantuml_command


def check_plantuml_syntax(puml_text: str) -> list[str]:
    """Return syntax errors for a PlantUML source, empty when it is valid.

    Uses `-syntax -pipe`, so the source never touches the filesystem. PlantUML
    reports a valid diagram as its type plus an entity count, and an invalid one
    as ERROR / line number / message.
    """
    if not puml_text.strip():
        return ["PlantUML code is empty."]

    try:
        result = subprocess.run(
            plantuml_command("-syntax", "-pipe"),
            input=puml_text,
            capture_output=True,
            text=True,
            stdin=None,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return ["Java is not installed or PlantUML cannot be executed."]
    except subprocess.TimeoutExpired:
        return ["PlantUML syntax check timed out."]

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if lines and lines[0].upper() == "ERROR":
        location = f"line {lines[1]}" if len(lines) > 1 else "unknown line"
        message = " ".join(lines[2:]) or "Syntax error"
        return [f"{location}: {message}"]

    if result.returncode != 0:
        detail = f"{result.stdout}\n{result.stderr}".strip()
        return [detail or "PlantUML syntax check failed."]

    return []
