"""생성 애플리케이션을 실제 build와 test 명령으로 확인한다.

이 모듈은 source를 고치거나 Java 문자열에서 설계 의미를 추측하지 않는다. 검증에 실패하면
명령, 출력과 test 결과를 OpenHands에 전달하고 코딩 에이전트가 같은 작업에서 수정한다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from ..workspace import cleanup_agent_workspace, prepare_agent_workspace
from .frontend import run_frontend_command, run_frontend_verification


def gradle_command() -> list[str]:
    """저장소에 고정된 Gradle Wrapper를 반환한다."""
    wrapper_name = "gradlew.bat" if os.name == "nt" else "gradlew"
    wrapper = Path(__file__).resolve().parents[2] / "tools" / "gradle" / wrapper_name
    if not wrapper.is_file():
        raise RuntimeError(f"Bundled Gradle Wrapper is missing: {wrapper}")
    return [str(wrapper)] if os.name == "nt" else ["sh", str(wrapper)]


class WorkspaceVerificationError(RuntimeError):
    """build 또는 test 명령이 실패했을 때 수리에 사용할 증거를 보존한다."""

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
        if len(output) > 1000:
            output = output[:600] + "\n... 출력 생략 ...\n" + output[-350:]
        super().__init__("Agent workspace verification failed: " + output)


def verification_timeout_seconds() -> int:
    """느린 로컬 build도 끝날 수 있는 검증 제한 시간을 반환한다."""
    return max(
        60,
        int(os.getenv("IMPLEMENTATION_VERIFICATION_TIMEOUT_SECONDS", "900")),
    )


def verify_run_workspace(
    run_root: Path,
    report_name: str = "final-verification.json",
    *,
    verify_frontend: bool = True,
    verify_end_to_end: bool = True,
) -> dict[str, object]:
    """현재 run의 backend와 필요한 경우 frontend를 한 번에 검증한다."""
    sandbox = prepare_agent_workspace(
        run_root,
        {
            "task_id": "final-verification",
            "allowed_write_paths": [],
            "required_output_paths": [],
        },
    )
    try:
        verification = verify_agent_workspace(
            sandbox,
            "" if verify_end_to_end else "compile-only",
            None if verify_end_to_end else [],
        )
        frontend_verification = None
        if (
            verify_frontend
            and (sandbox / "application" / "frontend" / "package.json").is_file()
        ):
            frontend_verification = verify_frontend_workspace(sandbox)
        result = {
            "status": "SUCCEEDED",
            "verification": verification,
            "frontendVerification": frontend_verification,
        }
        if Path(report_name).name != report_name or not report_name.endswith(".json"):
            raise ValueError(f"Invalid verification report name: {report_name}")
        report = run_root / "reports" / report_name
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result
    finally:
        cleanup_agent_workspace(sandbox)


def verify_agent_workspace(
    sandbox: Path,
    task_type: str = "",
    allowed_write_paths: list[str] | None = None,
    *,
    force_rerun: bool = False,
) -> dict[str, object]:
    """기능 작업에는 관련 검사만, 최종 단계에는 전체 검사를 실행한다."""
    if task_type in {"frontend", "frontend-implementation"}:
        return verify_frontend_workspace(sandbox)
    command = task_verification_command(
        gradle_command(),
        task_type,
        allowed_write_paths,
        force_rerun=force_rerun,
    )
    started = time.monotonic()
    environment = os.environ.copy()
    gradle_opts = environment.get("GRADLE_OPTS", "").strip()
    vfs_option = "-Dorg.gradle.vfs.watch=false"
    if vfs_option not in gradle_opts:
        environment["GRADLE_OPTS"] = f"{gradle_opts} {vfs_option}".strip()
    result = subprocess.run(
        command,
        cwd=sandbox / "application",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=verification_timeout_seconds(),
        check=False,
    )
    evidence = {
        "command": command,
        "exitCode": result.returncode,
        "durationMs": int((time.monotonic() - started) * 1000),
        "stdout": _truncate_log_snippet(result.stdout, 16000),
        "stderr": _truncate_log_snippet(result.stderr, 16000),
        "testResults": read_gradle_test_failures(sandbox),
    }
    if result.returncode != 0:
        raise WorkspaceVerificationError(evidence)
    return evidence


def task_verification_command(
    executable: list[str],
    task_type: str = "",
    allowed_write_paths: list[str] | None = None,
    *,
    force_rerun: bool = False,
) -> list[str]:
    """작업 중에는 관련 test만, 최종 단계에는 전체 build와 test를 고른다."""
    if not task_type and allowed_write_paths is None:
        command = [*executable, "compileJava", "bootJar", "test", "--build-cache"]
    else:
        test_names = sorted(
            {
                Path(path).stem
                for path in allowed_write_paths or []
                if "/src/test/" in "/" + path.replace("\\", "/")
                and path.endswith(".java")
            }
        )
        command = [*executable, "compileJava"]
        if test_names:
            command.extend(["testClasses", "test"])
            for test_name in test_names:
                command.extend(["--tests", f"*{test_name}"])
        command.append("--build-cache")
    if force_rerun:
        command.append("--rerun-tasks")
    return command


def verify_frontend_workspace(sandbox: Path) -> dict[str, object]:
    """frontend production build 결과를 같은 오류 형식으로 반환한다."""
    evidence = run_frontend_verification(sandbox, run_frontend_command)
    if evidence["exitCode"] != 0:
        raise WorkspaceVerificationError(evidence)
    return evidence


def read_gradle_test_failures(sandbox: Path) -> str:
    """JUnit XML에서 실패한 test와 원인 stack trace를 읽는다."""
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


def summarize_test_failure(detail: str) -> str:
    """긴 stack trace에서 처음 원인과 애플리케이션 호출 위치를 함께 남긴다."""
    lines = [line.rstrip() for line in detail.splitlines() if line.strip()]
    causes = [
        line
        for line in lines
        if re.search(r"Caused by:|Exception|Error|Assertion", line, re.IGNORECASE)
    ]
    application_frames = [
        line
        for line in lines
        if re.search(r"\bat (?:app//)?(?!org\.|java\.|jdk\.|worker\.)[A-Za-z_]", line)
    ]
    selected = [*lines[:12], *causes[:12], *application_frames[:12], *lines[-8:]]
    return _truncate_log_snippet(
        "\n".join(dict.fromkeys(selected)),
        max_chars=8000,
    )


def _truncate_log_snippet(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    marker = "\n... 출력 중간 생략 ...\n"
    remaining = max_chars - len(marker)
    head = remaining // 2
    return text[:head] + marker + text[-(remaining - head) :]
