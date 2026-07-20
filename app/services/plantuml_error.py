from __future__ import annotations

import subprocess


def extract_plantuml_error_hint(
    puml_content: str,
    plantuml_jar_path: str = "plantuml.jar",
) -> str:
    syntax_cmd = [
        "java",
        "-Djava.awt.headless=true",
        "-jar",
        plantuml_jar_path,
        "-syntax",
        "-pipe",
        "-charset",
        "UTF-8",
    ]

    result = subprocess.run(
        syntax_cmd,
        input=puml_content,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    return f"{result.stdout}\n{result.stderr}".strip()