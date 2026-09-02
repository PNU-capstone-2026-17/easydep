"""사용자 배포 패키지를 생성 직후와 Testing에서 함께 검사한다.

검사는 ``tofu apply``나 실제 CSP 리소스 생성을 절대 실행하지 않는다. 구현 단계는 이
함수로 방금 만든 파일을 확인하고, Testing은 같은 함수를 복원된 snapshot에 다시 적용한다.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.orchestration.process import run_process_tree

_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_SECRET_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:password|passwd|secret|api[_-]?key|token|private[_-]?key)\s*[:=]\s*(?![\"']?\$\{|[\"']?(?:<|CHANGE_ME|REPLACE|YOUR_))[^\s#\"']+"
)
_PLACEHOLDER = re.compile(
    r"\$\{[^}]+\}|\{[^}]+\}|<[^>]+>|CHANGE_ME|REPLACE_ME|YOUR_[A-Z0-9_]+",
    re.IGNORECASE,
)


def _package_root(application: Path) -> Path | None:
    for candidate in (
        application / "deployment",
        application / "deployment-bundle",
        application / "deployment_package",
    ):
        if candidate.is_dir() and any(
            (candidate / name).is_dir() for name in ("tofu", "terraform", "runtime", "scripts")
        ):
            return candidate
    # A generated application commonly has ``terraform/`` but that alone does not
    # make it a user-facing package. The root layout is accepted only with the
    # explicit EasyDep marker (or the complete package shape).
    if (application / ".easydep-managed").is_file() or (
        (application / "README.md").is_file()
        and (application / "runtime").is_dir()
        and (application / "scripts").is_dir()
        and ((application / "tofu").is_dir() or (application / "terraform").is_dir())
    ):
        return application
    return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _command_result(
    command: list[str],
    cwd: Path,
    timeout: int,
    *,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        completed = run_process_tree(
            command,
            cwd=cwd,
            env={**os.environ, **environment} if environment else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except Exception as error:  # subprocess and tool startup errors are inconclusive
        return {"status": "INCONCLUSIVE", "command": command, "error": str(error)}
    output = ((completed.stderr or "") + (completed.stdout or ""))[-4000:]
    return {
        "name": " ".join(command[1:3]) if len(command) > 1 else command[0],
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "command": command,
        "exitCode": completed.returncode,
        "output": output,
    }


def _tool(name: str) -> str | None:
    return shutil.which(name)


def _required_paths(root: Path) -> tuple[list[Path], list[str]]:
    tofu = root / "tofu"
    if not tofu.is_dir():
        tofu = root / "terraform"
    runtime = root / "runtime"
    scripts = root / "scripts"
    required = [root / "README.md"]
    missing: list[str] = []
    if tofu.is_dir():
        required.extend(tofu / name for name in ("main.tf", "variables.tf", "outputs.tf"))
        if not (tofu / "cloud-init.yaml.tftpl").is_file():
            # Providers may use a plain cloud-init file, but one of them is required.
            required.append(tofu / "cloud-init.yaml")
    else:
        missing.append("tofu/ or terraform/")
    required.append(runtime / "compose.yaml")
    required.append(runtime / ".env.example")
    for name in ("plan.sh", "deploy.sh", "verify.sh", "destroy.sh"):
        required.append(scripts / name)
    # PowerShell is optional on POSIX packages, but if one exists it is checked too.
    for path in required:
        if not path.is_file():
            missing.append(path.relative_to(root).as_posix())
    return required, missing


def _secret_findings(root: Path) -> list[str]:
    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in {".git", ".easydep-managed"}:
            continue
        content = _read_text(path)
        if not content:
            continue
        if _PRIVATE_KEY.search(content):
            findings.append(f"{path.relative_to(root).as_posix()}: private key material is present")
        if _SECRET_ASSIGNMENT.search(content):
            findings.append(f"{path.relative_to(root).as_posix()}: secret assignment has a concrete value")
    return findings


def _resource_references(root: Path, resource_plan: dict[str, Any] | None) -> list[str]:
    """Verify that supplied plan anchors occur in the package when they are known."""
    if not resource_plan:
        return []
    text = "\n".join(_read_text(path) for path in root.rglob("*") if path.is_file())
    findings: list[str] = []
    # IDs, ports and health paths are stable mechanical references. Do not require
    # arbitrary display labels, which are allowed to vary by provider adapter.
    candidates: list[tuple[str, Any]] = []
    for key in ("resourceId", "resource_id", "port", "healthPath", "health_path"):
        if key in resource_plan:
            candidates.append((key, resource_plan[key]))
    for key, value in candidates:
        if value not in (None, "") and str(value) not in text:
            findings.append(f"ResourcePlan {key} is not referenced by deployment files")
    return findings


def _compose_validation_environment(root: Path) -> dict[str, str]:
    """Compose 변수에 검사 전용 값을 넣어 파일 구조만 검증한다.

    생성된 ``.env.example``은 비밀값과 image 주소를 의도적으로 비워 둔다. 그대로
    ``docker compose config``를 실행하면 올바른 package도 빈 image 때문에 실패하므로,
    정적 검사에서만 사용하는 무해한 값을 넣는다. 실제 배포값을 만들거나 저장하지 않는다.
    """
    result: dict[str, str] = {}
    env_example = root / "runtime" / ".env.example"
    for line in _read_text(env_example).splitlines():
        name, separator, _value = line.partition("=")
        name = name.strip()
        if separator and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            result[name] = (
                "easydep/validation:latest"
                if name.endswith("_IMAGE")
                else "validation-placeholder"
            )
    return result


def check_deployment_package(
    application_dir: str | Path,
    *,
    expected: bool | None = None,
    resource_plan: dict[str, Any] | None = None,
    timeout_seconds: int = 120,
    include_plan: bool = False,
) -> dict[str, Any]:
    """Check package files and return legacy ``status`` plus canonical ``gateStatus``."""
    application = Path(application_dir)
    root = _package_root(application)
    if root is None:
        if expected is False or expected is None:
            return {
                "status": "SKIPPED",
                "gateStatus": "NOT_APPLICABLE",
                "issues": [],
                "message": "No deployment package is required for this application.",
                "source": {"source": "none", "directory": str(application)},
            }
        message = "A deployment package was expected but no package directory exists."
        return {
            "status": "UNAVAILABLE",
            "gateStatus": "INCONCLUSIVE",
            "issues": [message],
            "message": message,
            "source": {"source": "none", "directory": str(application)},
        }

    _required, missing = _required_paths(root)
    issues = [f"Missing deployment package file: {item}" for item in missing]
    issues.extend(_secret_findings(root))
    issues.extend(_resource_references(root, resource_plan))
    commands: list[dict[str, Any]] = []
    tofu_commands: list[dict[str, Any]] = []

    tofu = root / "tofu"
    if not tofu.is_dir():
        tofu = root / "terraform"
    if tofu.is_dir():
        executable = _tool("tofu") or _tool("terraform")
        if executable:
            # init이 생성 패키지에 .terraform을 남기지 않도록 작은 임시 복사본에서
            # 실행한다. apply와 실제 provider refresh는 하지 않는다.
            with tempfile.TemporaryDirectory(prefix="easydep-tofu-check-") as temporary:
                validation_tofu = Path(temporary) / "tofu"
                shutil.copytree(tofu, validation_tofu)
                tofu_checks = [
                    [executable, "fmt", "-check", "-recursive"],
                    [
                        executable,
                        "init",
                        "-backend=false",
                        "-input=false",
                        "-no-color",
                    ],
                    [executable, "validate", "-no-color"],
                ]
                if include_plan:
                    tofu_checks.append(
                        [
                            executable,
                            "plan",
                            "-refresh=false",
                            "-input=false",
                            "-lock=false",
                            "-no-color",
                        ]
                    )
                for command in tofu_checks:
                    tofu_commands.append(
                        _command_result(command, validation_tofu, timeout_seconds)
                    )
        else:
            tofu_commands.append(
                {
                    "name": "tofu",
                    "status": "INCONCLUSIVE",
                    "reason": "OpenTofu is unavailable.",
                }
            )
    commands.extend(tofu_commands)
    cloud_init = next(
        (path for path in (tofu / "cloud-init.yaml", tofu / "cloud-init.yaml.tftpl") if path.is_file()),
        None,
    )
    cloud = _tool("cloud-init")
    if cloud_init and cloud:
        commands.append(_command_result([cloud, "schema", "--config-file", str(cloud_init)], root, timeout_seconds))
    elif cloud_init:
        commands.append({"name": "cloud-init", "status": "INCONCLUSIVE", "reason": "cloud-init is unavailable."})

    compose = root / "runtime" / "compose.yaml"
    docker = _tool("docker")
    if compose and compose.is_file() and docker:
        commands.append(
            _command_result(
                [docker, "compose", "-f", str(compose), "config"],
                root,
                timeout_seconds,
                environment=_compose_validation_environment(root),
            )
        )
    elif compose.is_file():
        commands.append({"name": "docker-compose", "status": "INCONCLUSIVE", "reason": "Docker is unavailable."})

    bash = _tool("bash")
    for script in sorted((root / "scripts").glob("*.sh")) if (root / "scripts").is_dir() else []:
        if bash:
            commands.append(_command_result([bash, "-n", str(script)], root, timeout_seconds))
        else:
            commands.append({"name": f"bash -n {script.name}", "status": "INCONCLUSIVE", "reason": "bash is unavailable."})

    ps = _tool("pwsh") or _tool("powershell")
    for script in sorted((root / "scripts").glob("*.ps1")) if (root / "scripts").is_dir() else []:
        if ps:
            # ParseFile catches syntax errors without executing the script.
            expression = (
                "& { $tokens=$null; $errors=$null; "
                f"[System.Management.Automation.Language.Parser]::ParseFile('{script}',[ref]$tokens,[ref]$errors); "
                "if($errors.Count -gt 0){exit 1} }"
            )
            commands.append(_command_result([ps, "-NoProfile", "-NonInteractive", "-Command", expression], root, timeout_seconds))
        else:
            commands.append({"name": f"powershell parser {script.name}", "status": "INCONCLUSIVE", "reason": "PowerShell is unavailable."})

    command_issues = [
        str(item.get("output") or item.get("error") or item.get("reason") or "")
        for item in commands
        if item.get("status") == "FAIL"
    ]
    all_issues = [*issues, *[item for item in command_issues if item]]
    if issues:
        status, gate = "FAILED", "FAIL"
    elif any(item.get("status") == "INCONCLUSIVE" for item in commands):
        status, gate = "UNAVAILABLE", "INCONCLUSIVE"
    elif any(item.get("status") == "FAIL" for item in commands):
        status, gate = "FAILED", "FAIL"
    else:
        status, gate = "PASSED", "PASS"
    tofu_issues = [
        str(item.get("output") or item.get("error") or item.get("reason") or "")
        for item in tofu_commands
        if item.get("status") == "FAIL"
    ]
    tofu_inconclusive = any(
        item.get("status") == "INCONCLUSIVE" for item in tofu_commands
    )
    tofu_failed = any(item.get("status") == "FAIL" for item in tofu_commands)
    return {
        "status": status,
        "gateStatus": gate,
        "issues": all_issues,
        "commands": commands,
        "openTofu": {
            "status": (
                "FAILED"
                if tofu_failed
                else "UNAVAILABLE"
                if tofu_inconclusive
                else "PASSED"
            ),
            "gateStatus": (
                "FAIL"
                if tofu_failed
                else "INCONCLUSIVE"
                if tofu_inconclusive
                else "PASS"
            ),
            "issues": tofu_issues,
            "commands": tofu_commands,
            "source": {"source": "application", "directory": str(tofu)},
        },
        "source": {"source": "application", "directory": str(root)},
        "message": (
            "Deployment package checks passed."
            if gate == "PASS"
            else f"Deployment package checks produced {len(all_issues)} finding(s)."
        ),
    }


# Names used by callers in early Wave implementations remain valid.
run_deployment_package_checks = check_deployment_package
verify_deployment_package = check_deployment_package
