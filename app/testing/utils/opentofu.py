"""생성된 IaC를 OpenTofu로 검사한다.

검사는 원본을 바꾸지 않도록 임시 복사본에서 실행한다. ``validate``는 항상 실행하고,
실제 클라우드 문맥까지 확인하는 ``plan``은 ``TESTING_IAC_PLAN=true``일 때만 실행한다.
Testing 단계에서는 어떤 경우에도 ``apply``를 호출하지 않는다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, find_dotenv

from app.config import settings
from app.testing.runtime.process import run_process_tree

_SYSTEM_ENVIRONMENT = {
    "APPDATA",
    "COMSPEC",
    "HOME",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LANG",
    "LC_ALL",
    "LOCALAPPDATA",
    "NO_PROXY",
    "PATH",
    "PATHEXT",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
}
_CLOUD_PREFIXES = (
    "AWS_",
    "ARM_",
    "AZURE_",
    "CLOUDSDK_",
    "GOOGLE_",
    "TF_TOKEN_",
    "TF_VAR_",
)


def _configured_values() -> dict[str, str]:
    """프로세스 환경과 저장소 ``.env``를 합치되 실제 환경값을 우선한다."""
    env_path = find_dotenv(usecwd=True)
    values = {
        str(name): str(value)
        for name, value in (dotenv_values(env_path).items() if env_path else ())
        if value is not None
    }
    values.update(os.environ)
    return values


def _tofu_environment(values: dict[str, str]) -> dict[str, str]:
    """OpenTofu와 Provider가 필요한 값만 골라 새 환경을 만든다.

    ``docker --env-file``처럼 저장소의 모든 비밀값을 넘기지 않는다. 클라우드 인증값과
    ``TF_VAR_*`` 입력값은 이름만 보고 전달하므로 CSP가 달라져도 별도 분기할 필요가 없다.
    """
    environment = {
        name: value
        for name, value in values.items()
        if name in _SYSTEM_ENVIRONMENT
        or name.startswith(_CLOUD_PREFIXES)
    }
    environment.update(
        {
            "CHECKPOINT_DISABLE": "1",
            "TF_IN_AUTOMATION": "1",
            "TF_INPUT": "0",
        }
    )
    configured_cache = (settings.easydep_tofu_plugin_cache or "").strip()
    cache = (
        Path(configured_cache)
        if configured_cache
        else Path(tempfile.gettempdir()) / "easydep" / "provider-plugin-cache"
    )
    cache.mkdir(parents=True, exist_ok=True)
    environment["TF_PLUGIN_CACHE_DIR"] = str(cache.resolve())
    return environment


def _tofu_executable() -> str | None:
    configured = (settings.easydep_opentofu_path or "").strip()
    if configured and Path(configured).is_file():
        return configured
    return shutil.which("tofu")


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def run_opentofu_checks(terraform_dir: Path, *, timeout_seconds: int = 300) -> dict[str, Any]:
    """format, 초기화, 구문 검증과 선택적인 dry-run 결과를 반환한다."""
    executable = _tofu_executable()
    values = _configured_values()
    plan_enabled = _enabled(values.get("TESTING_IAC_PLAN"))
    if executable is None:
        message = "OpenTofu 실행 파일을 찾을 수 없습니다."
        return {
            "status": "UNAVAILABLE",
            "issues": [message],
            "commands": [],
            "planEnabled": plan_enabled,
            "message": message,
        }

    commands: list[tuple[str, list[str]]] = [
        ("fmt", [executable, "fmt", "-check", "-diff", "-recursive"]),
        (
            "init",
            [
                executable,
                "init",
                "-backend=false",
                "-input=false",
                "-no-color",
            ],
        ),
        ("validate", [executable, "validate", "-no-color"]),
    ]
    if plan_enabled:
        commands.append(
            (
                "plan",
                [
                    executable,
                    "plan",
                    "-refresh=false",
                    "-input=false",
                    "-lock=false",
                    "-no-color",
                ],
            )
        )

    executed: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="easydep-tofu-") as temporary:
        work = Path(temporary) / "terraform"
        shutil.copytree(terraform_dir, work)
        environment = _tofu_environment(values)
        for name, command in commands:
            try:
                result = run_process_tree(
                    command,
                    cwd=work,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=timeout_seconds,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                message = f"OpenTofu {name} 실행 실패: {error}"
                return {
                    "status": "FAILED",
                    "issues": [message],
                    "commands": executed,
                    "planEnabled": plan_enabled,
                    "message": message,
                }

            output = (result.stderr.strip() or result.stdout.strip())[-4000:]
            evidence = {
                "name": name,
                "exitCode": result.returncode,
                "output": output,
            }
            executed.append(evidence)
            if result.returncode != 0:
                message = f"OpenTofu {name} 검사 실패: {output or '출력이 없습니다.'}"
                return {
                    "status": "FAILED",
                    "issues": [message],
                    "commands": executed,
                    "planEnabled": plan_enabled,
                    "message": message,
                }

    names = ", ".join(item["name"] for item in executed)
    return {
        "status": "PASSED",
        "issues": [],
        "commands": executed,
        "planEnabled": plan_enabled,
        "message": f"OpenTofu {names} 검사를 통과했습니다.",
    }
