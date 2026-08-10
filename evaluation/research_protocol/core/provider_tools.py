"""고정 OpenTofu 공급자 캐시를 사용하는 연구 실행기의 공용 도구."""

from __future__ import annotations

import subprocess
from pathlib import Path
from time import perf_counter
from typing import Any

from app.core.cloudkb.depkb.provider_cache import (
    PINNED_PROVIDERS,
    PLUGIN_CACHE,
    audit_provider_cache,
    provider_cache_environment,
)


def directory_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def run_provider_command(
    command: list[str],
    cwd: Path,
    timeout_seconds: int = 600,
    *,
    environment: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
            env=environment,
        )
    except subprocess.TimeoutExpired as error:
        return {
            "status": "timeout",
            "error": str(error),
            "command": command,
            "elapsedSeconds": round(perf_counter() - started, 6),
        }
    return {
        "status": "passed" if completed.returncode == 0 else "failed",
        "exitCode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "command": command,
        "elapsedSeconds": round(perf_counter() - started, 6),
    }


__all__ = [
    "PINNED_PROVIDERS",
    "PLUGIN_CACHE",
    "audit_provider_cache",
    "directory_size",
    "provider_cache_environment",
    "run_provider_command",
]
