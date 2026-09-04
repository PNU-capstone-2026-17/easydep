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
from .frontend import (
    reuse_frontend_build,
    run_frontend_command,
    run_frontend_verification,
    store_frontend_build,
)


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
            output = output[:600] + "\n... output omitted ...\n" + output[-350:]
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
    """현재 run의 backend와 필요한 경우 frontend를 한 번에 검증한다.

    ``verify_end_to_end=False``는 HTTP 시나리오만 생략한다. 피드백 수리 뒤에는
    compile만으로 회귀를 확인할 수 없으므로 backend의 단위·작은 통합 테스트는 모두 실행한다.
    """
    cached_frontend = reuse_frontend_build(run_root) if verify_frontend else None
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
            "" if verify_end_to_end else "wiring",
            None if verify_end_to_end else [],
        )
        scenario_verification = (
            verify_use_case_scenarios(sandbox, run_root)
            if verify_end_to_end
            else {"status": "NOT_CHECKED", "tasks": []}
        )
        frontend_verification = None
        if verify_frontend and (sandbox / "application" / "frontend" / "package.json").is_file():
            frontend_verification = cached_frontend or verify_frontend_workspace(sandbox)
            if cached_frontend is None:
                store_frontend_build(run_root, sandbox, frontend_verification)
        result = {
            "status": (
                "SUCCEEDED"
                # 피드백 수정 직후에는 구현 단계의 단위·작은 통합 테스트만 다시 실행한다.
                # 이 호출에서는 HTTP 시나리오를 일부러 다음 Testing 단계에 맡기므로
                # NOT_CHECKED도 정상 결과다. 이를 실패로 보면 성공한 source를 다시 OpenHands에
                # 보내는 의미 없는 수리 루프가 생긴다.
                if scenario_verification.get("status")
                in {"PASSED", "NOT_APPLICABLE", "NOT_CHECKED"}
                else "FAILED"
            ),
            "verification": verification,
            "scenarioVerification": scenario_verification,
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
        if result["status"] != "SUCCEEDED":
            findings = scenario_verification.get("findings", [])
            raise WorkspaceVerificationError(
                {
                    "command": ["use-case-scenario-verification"],
                    "exitCode": 1,
                    "durationMs": 0,
                    "stdout": "",
                    "stderr": json.dumps(findings, ensure_ascii=False, indent=2),
                    "testResults": "",
                    "scenarioVerification": scenario_verification,
                }
            )
        return result
    finally:
        cleanup_agent_workspace(sandbox)


def verify_agent_workspace(
    sandbox: Path,
    task_type: str = "",
    allowed_write_paths: list[str] | None = None,
) -> dict[str, object]:
    """기능 작업에는 관련 검사만, 최종 단계에는 전체 검사를 실행한다.

    같은 sandbox에서 수리할 때도 Gradle의 증분 결과와 build cache를 재사용한다. 바뀐
    source는 Gradle이 다시 compile하므로 ``--rerun-tasks``로 모든 task를 강제할 필요가 없다.
    """
    if task_type in {"frontend", "frontend-implementation"}:
        return verify_frontend_workspace(sandbox)
    if task_type == "use-case":
        broad_tests = _broad_feature_tests(sandbox, allowed_write_paths or [])
        if broad_tests:
            raise WorkspaceVerificationError(
                {
                    "command": ["feature-test-isolation"],
                    "exitCode": 1,
                    "durationMs": 0,
                    "stdout": "",
                    "stderr": (
                        "A feature task must not start the whole application with "
                        "@SpringBootTest because other feature beans are implemented in "
                        "parallel. Replace it with a plain unit test or a narrow test slice: "
                        + ", ".join(broad_tests)
                    ),
                    "testResults": "",
                    "diagnosticPaths": [],
                }
            )
    command = task_verification_command(
        gradle_command(),
        task_type,
        allowed_write_paths,
    )
    started = time.monotonic()
    environment = os.environ.copy()
    # 생성 애플리케이션의 일반 설정은 실제 DB 주소를 환경변수로 받는다. 기능 검사는
    # 함께 생성한 H2용 application-test.yml을 사용해야 개발자 PC의 DB 설정에 의존하지 않는다.
    environment.setdefault("SPRING_PROFILES_ACTIVE", "test")
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
    diagnostic_paths = (
        _store_failed_verification_output(sandbox, result.stdout, result.stderr)
        if result.returncode != 0
        else []
    )
    evidence = {
        "command": command,
        "exitCode": result.returncode,
        "durationMs": int((time.monotonic() - started) * 1000),
        "stdout": _truncate_log_snippet(result.stdout, 16000),
        "stderr": _truncate_log_snippet(result.stderr, 16000),
        "testResults": read_gradle_test_failures(sandbox),
        "diagnosticPaths": diagnostic_paths,
    }
    if result.returncode != 0:
        raise WorkspaceVerificationError(evidence)
    return evidence


def _broad_feature_tests(sandbox: Path, allowed_write_paths: list[str]) -> list[str]:
    """독립 기능 작업에서 전체 Spring 애플리케이션을 띄우는 test를 찾는다."""

    violations: list[str] = []
    for relative in allowed_write_paths:
        normalized = str(relative).replace("\\", "/")
        if "/src/test/" not in "/" + normalized or not normalized.endswith(".java"):
            continue
        path = sandbox / relative
        if path.is_file() and "@SpringBootTest" in path.read_text(encoding="utf-8"):
            violations.append(normalized)
    return violations


def _store_failed_verification_output(
    sandbox: Path,
    stdout: str,
    stderr: str,
) -> list[str]:
    """축약하지 않은 명령 출력을 사용자용 증거로 보존한다.

    긴 로그를 매번 LLM 대화에 넣으면 같은 Spring stack trace가 문맥을 대부분 차지한다.
    코딩 에이전트에는 ``compact_verification_evidence``가 만든 핵심 원인만 전달하고, 원문은
    실행 이력을 조사하는 사람이 확인할 수 있도록 보존한다. ``build``는 구현 결과 수집 대상이
    아니므로 이 파일이 생성 애플리케이션에 섞이지 않는다.
    """
    output_dir = sandbox / "application" / "build" / "easydep-verification"
    output_dir.mkdir(parents=True, exist_ok=True)

    paths: list[str] = []
    for name, content in (("stdout.log", stdout), ("stderr.log", stderr)):
        if not content.strip():
            continue
        path = output_dir / name
        path.write_text(content, encoding="utf-8")
        paths.append(str(path.resolve()))

    # Gradle의 JUnit XML에는 console 출력보다 더 깊은 예외 원인이 기록되는 경우가 많다.
    # 파일이 여러 개일 수 있으므로 디렉터리를 알려 주고 필요한 보고서만 열게 한다.
    test_results = sandbox / "application" / "build" / "test-results" / "test"
    if any(test_results.glob("*.xml")):
        paths.append(str(test_results.resolve()))
    return paths


def task_verification_command(
    executable: list[str],
    task_type: str = "",
    allowed_write_paths: list[str] | None = None,
) -> list[str]:
    """작업 중에는 관련 test만, 최종 단계에는 전체 build와 test를 고른다.

    Gradle의 ``test`` 작업은 필요한 main/test compile을 스스로 선행한다. 따라서 test가
    있는 작업에서 ``compileJava``와 ``testClasses``를 따로 호출하면 같은 의존성 그래프를
    세 번 요청하는 셈이다. 테스트가 없는 작업만 빠른 타입 확인을 위해 ``compileJava``를
    직접 실행한다.
    """
    if not task_type and allowed_write_paths is None:
        # 이어지는 Docker build가 배포할 bootJar를 실제로 만든다. 여기서는 전체 test만
        # 실행해 같은 jar packaging을 연속으로 두 번 하지 않는다.
        command = [*executable, "test", "--build-cache"]
    elif task_type == "wiring":
        # wiring은 서로 독립적으로 만든 기능을 모두 합친 뒤 Spring Bean, DB migration과
        # 통합 흐름을 확인하는 단계다. 수정 가능 목록에는 아직 없는 테스트 파일도 후보로
        # 들어갈 수 있으므로 파일 이름으로 Gradle filter를 만들지 않고 전체 test를 한 번 돈다.
        command = [*executable, "test", "--build-cache"]
    else:
        test_names = sorted(
            {
                Path(path).stem
                for path in allowed_write_paths or []
                if "/src/test/" in "/" + path.replace("\\", "/") and path.endswith(".java")
            }
        )
        command = [*executable]
        if test_names:
            command.append("test")
            for test_name in test_names:
                command.extend(["--tests", f"*{test_name}"])
        else:
            command.append("compileJava")
        command.append("--build-cache")
    return command


def verify_frontend_workspace(sandbox: Path) -> dict[str, object]:
    """frontend production build 결과를 같은 오류 형식으로 반환한다."""
    evidence = run_frontend_verification(sandbox, run_frontend_command)
    if evidence["exitCode"] != 0:
        raise WorkspaceVerificationError(evidence)
    return evidence


def read_gradle_test_failures(sandbox: Path) -> str:
    """JUnit XML에서 대표 실패와 가장 구체적인 원인을 짧게 읽는다.

    Spring은 첫 ApplicationContext 실패 뒤의 모든 test에 거의 같은 예외를 기록한다. 모든
    testcase를 연결하면 실제 ``Caused by``가 중간에 묻히므로, 원인 사슬이 가장 풍부한 실패를
    대표로 고르고 나머지는 test 이름만 알려 준다.
    """
    result_dir = sandbox / "application" / "build" / "test-results" / "test"
    failures: list[tuple[int, str, str]] = []
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
                message = summarize_test_failure(detail) + "\nReported failure: " + message
            test_name = f"{case.get('classname')}.{case.get('name')}"
            # 구체적인 원인 사슬이 많고 Spring의 반복 실패 문구가 아닌 항목을 우선한다.
            score = detail.count("Caused by:") * 10
            if "failure threshold" not in detail.lower():
                score += 5
            failures.append((score, test_name, message))
    if not failures:
        return ""

    failures.sort(key=lambda item: item[0], reverse=True)
    _score, primary_name, primary_message = failures[0]
    lines = [f"{primary_name}: {primary_message}"]
    other_names = list(dict.fromkeys(name for _score, name, _message in failures[1:]))
    if other_names:
        shown = ", ".join(other_names[:8])
        remaining = len(other_names) - 8
        suffix = f" (+{remaining} more)" if remaining > 0 else ""
        lines.append(f"Other failing tests: {shown}{suffix}")
    return _truncate_log_snippet("\n".join(lines), max_chars=6000)


def verify_use_case_scenarios(sandbox: Path, run_root: Path) -> dict[str, object]:
    """각 유스케이스 작업의 시나리오 테스트가 실제로 실행됐는지 확인한다.

    Java 소스에 특정 문자열이 있는지는 보지 않는다. Gradle이 만든 JUnit XML만 읽어 각
    작업이 약속한 테스트 클래스가 실제로 성공했는지 확인한다. 하나의 시나리오 메서드가
    여러 유스케이스를 이어서 검사할 수 있으므로 유스케이스 수와 JUnit 메서드 수를 같다고
    가정하지 않는다. 테스트 본문의 관찰값 검사는 JUnit assertion이 담당한다.
    """
    manifest_path = run_root / "reports" / "run-manifest.json"
    if not manifest_path.is_file():
        return {"status": "NOT_APPLICABLE", "tasks": [], "findings": []}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    planned = [
        task
        for task in manifest.get("implementation_tasks", [])
        if isinstance(task, dict) and task.get("task_type") == "use-case"
    ]
    if not planned:
        return {"status": "NOT_APPLICABLE", "tasks": [], "findings": []}

    executed: dict[str, dict[str, int]] = {}
    result_dir = sandbox / "application" / "build" / "test-results" / "test"
    for report in sorted(result_dir.glob("*.xml")):
        try:
            root = ET.parse(report).getroot()
        except ET.ParseError:
            continue
        for case in root.findall("testcase"):
            class_name = str(case.get("classname") or "").rsplit(".", 1)[-1]
            if not class_name:
                continue
            counts = executed.setdefault(class_name, {"passed": 0, "failed": 0, "skipped": 0})
            if case.find("failure") is not None or case.find("error") is not None:
                counts["failed"] += 1
            elif case.find("skipped") is not None:
                counts["skipped"] += 1
            else:
                counts["passed"] += 1

    task_results: list[dict[str, object]] = []
    findings: list[str] = []
    covered_use_cases: set[str] = set()
    for task in planned:
        test_paths = [
            str(path)
            for path in task.get(
                "required_test_paths",
                task.get("required_output_paths", task.get("allowed_write_paths", [])),
            )
            if "/src/test/" in "/" + str(path).replace("\\", "/") and str(path).endswith(".java")
        ]
        use_case_ids = [
            str(item) for item in task.get("use_case_ids", task.get("useCaseIds", [])) if str(item)
        ]
        covered_use_cases.update(use_case_ids)
        classes = [Path(path).stem for path in test_paths]
        passed = sum(executed.get(name, {}).get("passed", 0) for name in classes)
        failed = sum(executed.get(name, {}).get("failed", 0) for name in classes)
        skipped = sum(executed.get(name, {}).get("skipped", 0) for name in classes)
        # 한 테스트 메서드가 묶음의 여러 유스케이스를 하나의 흐름으로 실행할 수 있다.
        # 여기서는 약속한 테스트 클래스가 실제로 실행됐는지만 확인한다.
        required_passes = 1
        status = (
            "PASSED"
            if classes and passed >= required_passes and failed == 0 and skipped == 0
            else "FAILED"
        )
        result = {
            "taskId": str(task.get("task_id") or ""),
            "useCaseIds": use_case_ids,
            "testPaths": test_paths,
            "requiredPassedCases": required_passes,
            "passedCases": passed,
            "failedCases": failed,
            "skippedCases": skipped,
            "status": status,
        }
        task_results.append(result)
        if status == "FAILED":
            findings.append(
                f"{result['taskId']}: expected a passing scenario test, "
                f"got passed={passed}, failed={failed}, skipped={skipped}; "
                + (", ".join(test_paths) or "required scenario test file is missing")
            )

    # 유스케이스 coverage의 기준은 그 기능을 구현한 작업 자체다. 수리용 wiring 작업에
    # 같은 ID 목록을 복사해 두고 다시 비교하지 않는다.
    expected_use_cases = {
        str(use_case_id)
        for task in planned
        for use_case_id in task.get("use_case_ids", task.get("useCaseIds", []))
        if str(use_case_id)
    }
    if expected_use_cases and covered_use_cases != expected_use_cases:
        missing = sorted(expected_use_cases - covered_use_cases)
        unexpected = sorted(covered_use_cases - expected_use_cases)
        findings.append(
            "use-case planning coverage mismatch: "
            f"missing={missing or 'none'}, unexpected={unexpected or 'none'}"
        )

    return {
        "status": "FAILED" if findings else "PASSED",
        "coveredUseCaseIds": sorted(covered_use_cases),
        "tasks": task_results,
        "findings": findings,
    }


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
    # 바깥 예외뿐 아니라 stack trace 뒤쪽의 가장 구체적인 원인도 남긴다. Spring은
    # ApplicationContext 예외 아래에 누락된 property나 Bean 이름을 여러 단계 뒤에 기록한다.
    # 가장 안쪽 원인을 앞에 놓는다. 긴 Spring 설정 문구보다 실제 MappingException이나
    # assertion 메시지가 먼저 보이므로 작은 출력 제한에서도 수리 근거가 남는다.
    selected = [
        *reversed(causes[-12:]),
        *lines[:6],
        *application_frames[:8],
        *lines[-6:],
    ]
    return _truncate_log_snippet(
        "\n".join(dict.fromkeys(selected)),
        max_chars=8000,
    )


def compact_verification_evidence(
    evidence: dict[str, object],
    *,
    max_chars: int = 8000,
) -> str:
    """LLM에 한 번만 전달할 build/test 핵심 진단을 만든다.

    원본 evidence에는 같은 Gradle 오류가 test XML, stderr와 stdout에 반복될 수 있다. 전체
    stack trace를 그대로 이어 붙이면 실제 원인은 묻히고 Conversation만 빠르게 커진다. 명령과
    종료 코드는 남기되, 출력은 최초 원인과 애플리케이션 위치를 고르는 기존 요약기를 거친 뒤
    중복 줄을 제거한다. 원본 evidence 자체는 바꾸지 않으므로 보고서와 사용자 조회에는 전체
    기록이 계속 남는다.
    """

    command = evidence.get("command") or []
    command_text = (
        " ".join(str(part) for part in command) if isinstance(command, list) else str(command)
    )
    lines = [
        f"Command: {command_text or '(not available)'}",
        f"Exit code: {evidence.get('exitCode', 1)}",
    ]
    # JUnit XML이 있으면 test 이름과 가장 깊은 원인을 이미 담고 있다. 같은 stack trace의
    # stdout/stderr 사본을 다시 붙이지 않는다. compile 실패처럼 XML이 없을 때만 다음
    # 출력으로 물러난다.
    for key in ("testResults", "stderr", "stdout"):
        raw = str(evidence.get(key) or "").strip()
        if raw:
            lines.extend(summarize_test_failure(raw).splitlines())
            break

    # Spring test 설정처럼 한 줄 자체가 매우 길 때도 model 입력의 대부분을 차지하지 않게 한다.
    shortened = [
        line if len(line) <= 1200 else line[:850] + " ... " + line[-300:]
        for line in dict.fromkeys(lines)
    ]
    return _truncate_log_snippet("\n".join(shortened), max_chars=max_chars)


def _truncate_log_snippet(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    marker = "\n... middle of output omitted ...\n"
    remaining = max_chars - len(marker)
    head = remaining // 2
    return text[:head] + marker + text[-(remaining - head) :]
