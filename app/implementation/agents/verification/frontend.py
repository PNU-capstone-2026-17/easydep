from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

MUTATING_HTTP_METHODS = {"post", "put", "patch", "delete"}

RESPONSIVE_TABLE_STYLES = """

/* EasyDep accessibility repair: keep data tables usable on narrow screens. */
@media (max-width: 40rem) {
  table {
    display: block;
    max-width: 100%;
    overflow-x: auto;
  }
}
"""


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
        for number, line in enumerate(source.splitlines(), 1):
            # ``placeholder=`` is a valid JSX input attribute, not an
            # unfinished implementation marker.
            if re.search(
                r"(?<![\w-])(?:TODO|FIXME|PLACEHOLDER)\b(?!\s*=)",
                line,
                re.IGNORECASE,
            ):
                violations.append(
                    f"{relative}:{number}: unresolved implementation marker; "
                    "remove the marker and implement the described behavior"
                )
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


def repair_responsive_table_styles(
    sandbox: Path, relative_paths: list[str]
) -> list[str]:
    """Add the narrow-screen table rule when the generated UI declares a table.

    The rule is a mechanical accessibility safeguard, not a domain decision. It
    is safe to apply before the frontend contract gate because it only touches the
    declared ``styles.css`` output and is idempotent.
    """
    paths = [sandbox / relative for relative in relative_paths]
    source_paths = [path for path in paths if path.suffix in {".ts", ".tsx"} and path.is_file()]
    if not source_paths:
        return []
    if not any("<table" in path.read_text(encoding="utf-8") for path in source_paths):
        return []
    style_path = next(
        (path for path in paths if path.name == "styles.css" and path.is_file()),
        None,
    )
    if style_path is None:
        return []
    styles = style_path.read_text(encoding="utf-8")
    if re.search(r"overflow-x\s*:\s*(?:auto|scroll)", styles):
        return []
    separator = "\n" if styles.endswith("\n") else "\n\n"
    style_path.write_text(styles + separator + RESPONSIVE_TABLE_STYLES.lstrip("\n"), encoding="utf-8")
    return [str(style_path.relative_to(sandbox)).replace("\\", "/")]


def repair_frontend_accessibility_contract(
    sandbox: Path, relative_paths: list[str]
) -> list[str]:
    """Remove stale comment markers and invalid static aria references."""
    changed: list[str] = []
    for relative in relative_paths:
        path = sandbox / relative
        if not path.is_file() or path.suffix not in {".ts", ".tsx"}:
            continue
        source = path.read_text(encoding="utf-8")
        repaired = re.sub(
            r"(?mi)^\s*(?://|/\*|\{\/\*)[^\n]*(?:TODO|FIXME|PLACEHOLDER)[^\n]*(?:\*/\}|\*/)?\s*\n?",
            "",
            source,
        )
        declared_ids = {
            value
            for attribute in _jsx_attribute_values(repaired, "id")
            for value in attribute.split()
        }

        def replace_reference(match: re.Match[str]) -> str:
            value = match.group(2)
            valid = [token for token in value.split() if token in declared_ids]
            return f"aria-describedby=\"{' '.join(valid)}\"" if valid else ""

        repaired = re.sub(
            r"aria-describedby\s*=\s*([\"'])(.*?)\1",
            replace_reference,
            repaired,
        )
        if repaired != source:
            path.write_text(repaired, encoding="utf-8")
            changed.append(str(path.relative_to(sandbox)).replace("\\", "/"))
    return changed


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


def run_frontend_command(
    command: list[str],
    *,
    cwd: Path,
    capture_output: bool,
    text: bool,
    encoding: str,
    errors: str,
    timeout: int,
    check: bool,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run npm and terminate the complete command tree if it times out."""
    if os.name != "nt":
        return subprocess.run(
            command,
            cwd=cwd,
            capture_output=capture_output,
            text=text,
            encoding=encoding,
            errors=errors,
            timeout=timeout,
            check=check,
            env=env,
        )

    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        encoding=encoding,
        errors=errors,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        env=env,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as error:
        try:
            terminated = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
            if terminated.returncode != 0:
                process.kill()
        except (OSError, subprocess.SubprocessError):
            process.kill()
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=stdout or error.output,
            stderr=stderr or error.stderr,
        ) from error
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _timeout_output(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value or "")


def _frontend_command_environment() -> dict[str, str]:
    """Reuse a system cache instead of re-downloading each clean sandbox."""
    environment = os.environ.copy()
    environment.setdefault(
        "NPM_CONFIG_CACHE",
        str(Path(tempfile.gettempdir()) / "easydep-npm-cache"),
    )
    return environment


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
    main = frontend / "src" / "main.tsx"
    main_source = main.read_text(encoding="utf-8") if main.is_file() else ""
    has_hash_router = bool(
        re.search(
            r"import\s*\{[^}]*\bHashRouter\b[^}]*\}\s*from\s*['\"]react-router-dom['\"]",
            main_source,
        )
        and re.search(r"<HashRouter(?:\s|>)", main_source)
    )
    if not has_hash_router:
        return {
            "command": ["npm", "run", "build"],
            "commands": [],
            "exitCode": 1,
            "durationMs": 0,
            "stdout": "",
            "stderr": "Frontend static deployment requires HashRouter in src/main.tsx",
            "testResults": "",
        }
    executable = "npm.cmd" if os.name == "nt" else "npm"
    commands = []
    # 같은 task의 repair는 같은 sandbox를 쓴다. 성공한 ``npm ci``가 남긴 lock record가
    # 있으면 dependency를 다시 지우고 설치하지 않고 TypeScript build만 반복한다.
    if not (frontend / "node_modules" / ".package-lock.json").is_file():
        commands.append(
            [
                executable,
                "ci",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--prefer-offline",
            ]
        )
    commands.append([executable, "run", "build"])
    started = time.monotonic()
    outputs: list[str] = []
    errors: list[str] = []
    exit_code = 0
    executed_command = commands[0]
    environment = _frontend_command_environment()
    for command in commands:
        executed_command = command
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
                env=environment,
            )
        except subprocess.TimeoutExpired as error:
            exit_code = 1
            stdout = _timeout_output(error.stdout or error.output)
            stderr = _timeout_output(error.stderr)
            errors.append(
                "Frontend command timed out after "
                f"{timeout_seconds} seconds: {' '.join(command)}"
            )
            if stdout:
                outputs.append(stdout[-12000:])
            if stderr:
                errors.append(stderr[-12000:])
            break
        except OSError as error:
            exit_code = 1
            errors.append(str(error))
            break
        outputs.append(result.stdout[-12000:])
        errors.append(result.stderr[-12000:])
        exit_code = result.returncode
        if exit_code != 0:
            break
    return {
        "command": executed_command,
        "commands": commands,
        "exitCode": exit_code,
        "durationMs": int((time.monotonic() - started) * 1000),
        "stdout": "\n".join(outputs)[-16000:],
        "stderr": "\n".join(errors)[-16000:],
        "testResults": "",
    }
