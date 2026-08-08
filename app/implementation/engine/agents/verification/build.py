from __future__ import annotations

import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from ..workspace import prepare_agent_workspace
from .frontend import run_frontend_verification


def gradle_command() -> list[str]:
    """Use EasyDep's pinned wrapper instead of a machine-global Gradle."""
    wrapper_name = "gradlew.bat" if os.name == "nt" else "gradlew"
    wrapper = Path(__file__).resolve().parents[3] / "tools" / "gradle" / wrapper_name
    if not wrapper.is_file():
        raise RuntimeError(f"Bundled Gradle Wrapper is missing: {wrapper}")
    return [str(wrapper)] if os.name == "nt" else ["sh", str(wrapper)]


class WorkspaceVerificationError(RuntimeError):
    def __init__(self, evidence: dict[str, object]):
        self.evidence = evidence
        output = str(
            evidence.get("testResults")
            or evidence.get("stderr")
            or evidence.get("stdout")
            or ""
        )
        super().__init__("Agent workspace verification failed: " + output[-1000:])


def verify_run_workspace(
    run_root: Path,
    *,
    verify_workspace=None,
) -> dict[str, object]:
    """Verify all promoted sources from a short ASCII-safe workspace."""
    sandbox = prepare_agent_workspace(
        run_root,
        {"task_id": "final-verification", "allowed_write_paths": []},
    )
    verifier = verify_workspace or verify_agent_workspace
    verification = verifier(sandbox)
    frontend_verification = None
    if (sandbox / "application" / "frontend" / "package.json").is_file():
        frontend_verification = verify_frontend_workspace(sandbox)
    result = {
        "status": "SUCCEEDED",
        "workspace": str(sandbox),
        "verification": verification,
        "frontendVerification": frontend_verification,
    }
    report = run_root / "reports" / "final-verification.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def verify_agent_workspace(
    sandbox: Path, task_type: str = ""
) -> dict[str, object]:
    if task_type == "frontend-implementation":
        return verify_frontend_workspace(sandbox)
    executable = gradle_command()
    started = time.monotonic()
    result = subprocess.run(
        [*executable, "compileJava", "bootJar", "test", "--no-daemon"],
        cwd=sandbox / "application",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    evidence = {
        "command": [*executable, "compileJava", "bootJar", "test", "--no-daemon"],
        "exitCode": result.returncode,
        "durationMs": int((time.monotonic() - started) * 1000),
        "stdout": result.stdout[-16000:],
        "stderr": result.stderr[-16000:],
        "testResults": read_gradle_test_failures(sandbox),
    }
    if result.returncode != 0:
        raise WorkspaceVerificationError(evidence)
    return evidence


def verify_frontend_workspace(sandbox: Path) -> dict[str, object]:
    evidence = run_frontend_verification(sandbox, subprocess.run)
    if evidence["exitCode"] != 0:
        raise WorkspaceVerificationError(evidence)
    return evidence


def production_placeholder_markers(
    sandbox: Path, relative_paths: list[str]
) -> list[str]:
    """Reject unresolved implementation markers in contracted production Java outputs."""
    evidence: list[str] = []
    pattern = re.compile(r"\b(?:TODO|FIXME|PLACEHOLDER)\b", re.IGNORECASE)
    for relative in relative_paths:
        normalized = relative.replace("\\", "/")
        if "/src/main/java/" not in f"/{normalized}" or not normalized.endswith(".java"):
            continue
        path = sandbox / relative
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                evidence.append(f"{normalized}:{number}: {line.strip()}")
    return evidence


def read_gradle_test_failures(sandbox: Path) -> str:
    result_dir = sandbox / "application" / "build" / "test-results" / "test"
    reports: list[str] = []
    for report in sorted(result_dir.glob("*.xml")):
        try:
            root = ET.parse(report).getroot()
        except ET.ParseError:
            continue
        for case in root.findall("testcase"):
            problem = case.find("failure")
            if problem is None:
                problem = case.find("error")
            if problem is None:
                continue
            message = problem.get("message") or "test failed"
            detail = (problem.text or "").strip()
            if detail:
                message += "\n" + summarize_test_failure(detail)
            reports.append(f"{case.get('classname')}.{case.get('name')}: {message}")
    return _truncate_log_snippet("\n\n".join(reports), max_chars=8000)


def _truncate_log_snippet(text: str, max_chars: int = 8000) -> str:
    return text[-max_chars:] if len(text) > max_chars else text


def summarize_test_failure(detail: str) -> str:
    """Keep causal exception lines, rather than only the end of a long trace."""
    lines = [line.rstrip() for line in detail.splitlines() if line.strip()]
    causal = [
        line
        for line in lines
        if re.search(
            r"(?:Caused by:|Suppressed:|Error creating bean|Requested bean is currently in creation|"
            r"NoSuchBeanDefinitionException|NoUniqueBeanDefinitionException|UnsatisfiedDependencyException|"
            r"BeanCurrentlyInCreationException)",
            line,
        )
    ]
    selected = causal or lines[:30]
    selected.extend(lines[-8:])
    return _truncate_log_snippet("\n".join(dict.fromkeys(selected)), max_chars=8000)
