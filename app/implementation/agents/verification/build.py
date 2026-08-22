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


SQL_RESERVED_IDENTIFIERS = (
    "year",
    "order",
    "group",
    "user",
    "status",
    "key",
    "value",
    "offset",
    "limit",
    "check",
    "date",
)


def gradle_command() -> list[str]:
    """Use EasyDep's pinned wrapper instead of a machine-global Gradle."""
    wrapper_name = "gradlew.bat" if os.name == "nt" else "gradlew"
    wrapper = Path(__file__).resolve().parents[2] / "tools" / "gradle" / wrapper_name
    if not wrapper.is_file():
        raise RuntimeError(f"Bundled Gradle Wrapper is missing: {wrapper}")
    return [str(wrapper)] if os.name == "nt" else ["sh", str(wrapper)]


class WorkspaceVerificationError(RuntimeError):
    def __init__(self, evidence: dict[str, object]):
        self.evidence = evidence
        output = next(
            (
                str(evidence.get(key)).strip()
                for key in ("testResults", "stderr", "stdout")
                if str(evidence.get(key) or "").strip()
            ),
            "No verification output was captured",
        )
        if output == "No verification output was captured" and evidence.get("command"):
            output = f"command={evidence['command']}; {output}"
        super().__init__("Agent workspace verification failed: " + output[-1000:])


def verification_timeout_seconds() -> int:
    """느린 로컬 환경에서도 검증 병목을 관측할 수 있도록 제한 시간을 구성한다."""
    return max(
        60,
        int(os.getenv("IMPLEMENTATION_VERIFICATION_TIMEOUT_SECONDS", "900")),
    )


def verify_run_workspace(
    run_root: Path, report_name: str = "final-verification.json"
) -> dict[str, object]:
    """Verify all promoted sources from a short ASCII-safe workspace."""
    sandbox = prepare_agent_workspace(
        run_root,
        {"task_id": "final-verification", "allowed_write_paths": []},
    )
    verification = verify_agent_workspace(sandbox)
    frontend_verification = None
    if (sandbox / "application" / "frontend" / "package.json").is_file():
        frontend_verification = verify_frontend_workspace(sandbox)
    result = {
        "status": "SUCCEEDED",
        "workspace": str(sandbox),
        "verification": verification,
        "frontendVerification": frontend_verification,
    }
    if Path(report_name).name != report_name or not report_name.endswith(".json"):
        raise ValueError(f"Invalid verification report name: {report_name}")
    report = run_root / "reports" / report_name
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def verify_agent_workspace(
    sandbox: Path,
    task_type: str = "",
    allowed_write_paths: list[str] | None = None,
) -> dict[str, object]:
    if task_type == "frontend-implementation":
        return verify_frontend_workspace(sandbox)
    executable = gradle_command()
    command = task_verification_command(
        executable, task_type, allowed_write_paths
    )
    started = time.monotonic()
    result = subprocess.run(
        command,
        cwd=sandbox / "application",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=verification_timeout_seconds(),
        check=False,
    )
    evidence = {
        "command": command,
        "exitCode": result.returncode,
        "durationMs": int((time.monotonic() - started) * 1000),
        "stdout": result.stdout[-16000:],
        "stderr": result.stderr[-16000:],
        "testResults": read_gradle_test_failures(sandbox),
    }
    if result.returncode != 0:
        raise WorkspaceVerificationError(evidence)
    return evidence


def task_verification_command(
    executable: list[str],
    task_type: str = "",
    allowed_write_paths: list[str] | None = None,
) -> list[str]:
    """Use a narrow task gate; phase/final verification keeps the full gate."""
    if not task_type or allowed_write_paths is None:
        return [
            *executable,
            "compileJava",
            "bootJar",
            "test",
            "--build-cache",
            "--no-daemon",
        ]

    test_names = sorted(
        {
            Path(path).stem
            for path in allowed_write_paths
            if "/src/test/" in "/" + path.replace("\\", "/")
            and path.endswith(".java")
        }
    )
    command = [*executable, "compileJava"]
    if test_names:
        command.extend(["testClasses", "test"])
        for test_name in test_names:
            command.extend(["--tests", f"*{test_name}"])
    command.extend(["--build-cache", "--no-daemon"])
    return command


def verify_frontend_workspace(sandbox: Path) -> dict[str, object]:
    evidence = run_frontend_verification(sandbox, subprocess.run)
    if evidence["exitCode"] != 0:
        raise WorkspaceVerificationError(evidence)
    return evidence


def production_placeholder_markers(
    sandbox: Path, relative_paths: list[str]
) -> list[str]:
    """Reject actionable unresolved markers in contracted production Java outputs.

    A prose comment containing the generic word ``placeholder`` has no runtime
    effect and is not evidence of incomplete code.  Keep this gate focused on
    actionable TODO/FIXME markers so it does not reject a compiling artifact
    merely for its wording.
    """
    evidence: list[str] = []
    pattern = re.compile(r"\b(?:TODO|FIXME)\b", re.IGNORECASE)
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


def production_test_library_markers(
    sandbox: Path, relative_paths: list[str]
) -> list[str]:
    """Reject test-only Mockito/JUnit use in contracted production Java files."""
    evidence: list[str] = []
    pattern = re.compile(
        r"^\s*import\s+(?:static\s+)?org\.(?:mockito|junit)\.|\bMockito\s*\.",
        re.MULTILINE,
    )
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


def persistence_reserved_identifier_markers(
    sandbox: Path, relative_paths: list[str]
) -> list[str]:
    """Detect H2-reserved persistence names before expensive downstream tests.

    JPA can compile with a reserved ``@Column`` name and only fail when an
    integration test executes Hibernate SQL.  Detect it while the owning
    persistence task is still running, so its focused repair prompt can fix
    the entity or migration instead of repeatedly rewriting an unrelated E2E
    test.
    """
    evidence: list[str] = []
    names = "|".join(re.escape(name) for name in SQL_RESERVED_IDENTIFIERS)
    java_pattern = re.compile(
        rf'@(?:Column|Table)\s*\([^)]*\bname\s*=\s*"({names})"',
        re.IGNORECASE,
    )
    sql_column_pattern = re.compile(
        rf"(?:^|,)\s*({names})\s+(?:[a-z]+|[a-z]+\s*\([^)]*\))",
        re.IGNORECASE,
    )
    for relative in relative_paths:
        normalized = relative.replace("\\", "/")
        if not normalized.endswith((".java", ".sql")):
            continue
        path = sandbox / relative
        if not path.is_file():
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = java_pattern.search(line) if normalized.endswith(".java") else sql_column_pattern.search(line)
            if match:
                identifier = match.group(1)
                evidence.append(
                    f"{normalized}:{number}: H2 reserved identifier `{identifier}` must be renamed "
                    "consistently in the JPA mapping and migration."
                )
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
