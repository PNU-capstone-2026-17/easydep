"""외부 명령을 shell 없이 실행하고 결과를 그대로 보존한다."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout_seconds: float,
) -> dict[str, Any]:
    started = time.monotonic()
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        process = subprocess.Popen(  # noqa: S603 - shell 없이 manifest 배열을 그대로 실행한다.
            command,
            cwd=cwd,
            env=env if env is not None else os.environ.copy(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    except OSError as error:
        return {
            "exitCode": None,
            "timedOut": False,
            "wallSeconds": round(time.monotonic() - started, 3),
            "stdout": "",
            "stderr": f"{type(error).__name__}: {error}",
        }
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "nt":
            subprocess.run(  # noqa: S603 - 방금 시작한 PID의 자식만 종료한다.
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            process.kill()
        stdout, stderr = process.communicate()
    return {
        "exitCode": process.returncode,
        "timedOut": timed_out,
        "wallSeconds": round(time.monotonic() - started, 3),
        "stdout": stdout,
        "stderr": stderr,
    }
