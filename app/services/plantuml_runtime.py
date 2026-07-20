from __future__ import annotations

import os

from dotenv import load_dotenv


load_dotenv()


def plantuml_jar_path() -> str:
    """Where the PlantUML jar lives. Deployment config, not per-request data."""
    return os.getenv("PLANTUML_JAR_PATH", "plantuml.jar")


def plantuml_command(*arguments: str) -> list[str]:
    return [
        "java",
        "-Djava.awt.headless=true",
        "-jar",
        plantuml_jar_path(),
        "-charset",
        "UTF-8",
        *arguments,
    ]
