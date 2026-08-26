"""PlantUML jar와 대화하는 공유 툴체인: 실행 명령, 문법 검사, 이미지 렌더.

산출물별 diagram(클래스·시퀀스·ERD·배포)이 모두 같은 jar로 검사·렌더되므로
특정 산출물에 두지 않고 공유한다. 산출물별 "무엇을 그릴지"(BCE→PlantUML 변환 등)는
각 산출물 서비스에 있고, 여기서는 "어떻게 실행/검사/렌더할지"만 다룬다.
"""
from __future__ import annotations

import subprocess

from dotenv import load_dotenv

load_dotenv()


# Keep checked-in SVG examples and API rendering on the exact same renderer.
# Updating PlantUML is an intentional dependency change: change this digest,
# regenerate the examples, and review the resulting SVG diff together.
PLANTUML_IMAGE = (
    "plantuml/plantuml@sha256:"
    "47870c1f76cfb3747bc7090bfe83013a4e3105b5a0bb1515e2baf5d3e2b3ee9d"
)


def plantuml_command(*arguments: str) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "-i",
        PLANTUML_IMAGE,
        "-charset",
        "UTF-8",
        *arguments,
    ]


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
            input=puml_text.encode("utf-8"),
            capture_output=True,
            stdin=None,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return ["Docker is not installed or plantuml/plantuml cannot be executed."]
    except subprocess.TimeoutExpired:
        return ["PlantUML syntax check timed out."]

    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if lines and lines[0].upper() == "ERROR":
        location = f"line {lines[1]}" if len(lines) > 1 else "unknown line"
        message = " ".join(lines[2:]) or "Syntax error"
        return [f"{location}: {message}"]

    if result.returncode != 0:
        detail = f"{stdout}\n{stderr}".strip()
        return [detail or "PlantUML syntax check failed."]

    return []


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
