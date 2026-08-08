from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable


MUTATING_HTTP_METHODS = {"post", "put", "patch", "delete"}


def has_mutating_operations(openapi: object) -> bool:
    if not isinstance(openapi, dict) or not isinstance(openapi.get("paths"), dict):
        return False
    return any(
        str(method).lower() in MUTATING_HTTP_METHODS
        for path_item in openapi["paths"].values()
        if isinstance(path_item, dict)
        for method in path_item
    )


def frontend_contract_violations(
    sandbox: Path,
    relative_paths: list[str],
    *,
    requires_success_feedback: bool = False,
) -> list[str]:
    sources: list[str] = []
    styles: list[str] = []
    violations: list[str] = []
    for relative in relative_paths:
        path = sandbox / relative
        if not path.is_file():
            continue
        if path.suffix == ".css":
            styles.append(path.read_text(encoding="utf-8"))
            continue
        if path.suffix not in {".ts", ".tsx"}:
            continue
        source = path.read_text(encoding="utf-8")
        sources.append(source)
        if re.search(r"\b(?:TODO|FIXME|PLACEHOLDER)\b", source, re.IGNORECASE):
            violations.append(f"{relative}: unresolved implementation marker")
        if re.search(
            r"(?:(?:window|globalThis)\s*\.\s*)?\b(?:fetch|XMLHttpRequest)\s*\(",
            source,
        ) or re.search(r"\baxios\b", source):
            violations.append(
                f"{relative}: direct HTTP calls are forbidden; use src/generated"
            )
    combined = "\n".join(sources)
    if not re.search(r"from\s+['\"][^'\"]*generated", combined):
        violations.append(
            "Frontend implementation does not import the OpenAPI Generator client/models"
        )
    if requires_success_feedback and not re.search(
        r"(?:role\s*=\s*['\"]status['\"]|aria-live\s*=\s*['\"](?:polite|assertive)['\"])",
        combined,
    ):
        violations.append(
            "Mutating API operations require an accessible success status announcement"
        )
    declared_ids = {
        value
        for attribute in _jsx_attribute_values(combined, "id")
        for value in attribute.split()
    }
    for attribute in _jsx_attribute_values(combined, "aria-describedby"):
        for described_id in attribute.split():
            if described_id not in declared_ids:
                violations.append(
                    f"aria-describedby references missing element id: {described_id}"
                )
    combined_styles = "\n".join(styles)
    if "<table" in combined and not re.search(
        r"overflow-x\s*:\s*(?:auto|scroll)", combined_styles
    ):
        violations.append(
            "Data tables require responsive narrow-screen handling in styles.css"
        )
    return violations


def _jsx_attribute_values(source: str, attribute: str) -> list[str]:
    pattern = re.compile(
        rf"\b{re.escape(attribute)}\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|\{{([^{{}}]*)\}})"
    )
    values: list[str] = []
    for match in pattern.finditer(source):
        static_value = match.group(1) if match.group(1) is not None else match.group(2)
        if static_value is not None:
            values.append(static_value)
            continue
        expression = match.group(3) or ""
        values.extend(
            value
            for _, value in re.findall(r"(['\"`])([^'\"`]*)\1", expression)
            if value
        )
    return values


def run_frontend_verification(
    sandbox: Path,
    run_command: Callable[..., subprocess.CompletedProcess[str]],
    *,
    timeout_seconds: int = 300,
) -> dict[str, object]:
    frontend = sandbox / "application" / "frontend"
    package = frontend / "package.json"
    lock = frontend / "package-lock.json"
    if not package.is_file() or not lock.is_file():
        missing = "package.json" if not package.is_file() else "package-lock.json"
        return {
            "command": ["npm", "run", "build"],
            "commands": [],
            "exitCode": 1,
            "durationMs": 0,
            "stdout": "",
            "stderr": f"Frontend {missing} was not found",
            "testResults": "",
        }
    executable = "npm.cmd" if os.name == "nt" else "npm"
    commands = [
        [executable, "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
        [executable, "run", "build"],
    ]
    started = time.monotonic()
    outputs: list[str] = []
    errors: list[str] = []
    exit_code = 0
    for command in commands:
        try:
            result = run_command(
                command,
                cwd=frontend,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            exit_code = 1
            errors.append(str(error))
            break
        outputs.append(result.stdout[-12000:])
        errors.append(result.stderr[-12000:])
        exit_code = result.returncode
        if exit_code != 0:
            break
    return {
        "command": commands[-1],
        "commands": commands,
        "exitCode": exit_code,
        "durationMs": int((time.monotonic() - started) * 1000),
        "stdout": "\n".join(outputs)[-16000:],
        "stderr": "\n".join(errors)[-16000:],
        "testResults": "",
    }
