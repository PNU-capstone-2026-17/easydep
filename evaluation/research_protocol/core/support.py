"""연구 프로토콜 실행기들이 공유하는 사례 중립 지원 함수."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def redact_tail(text: str, values: list[str], *, limit: int = 12_000) -> str:
    result = text
    for value in sorted((item for item in values if item), key=len, reverse=True):
        result = result.replace(value, "<redacted-input>")
    return result[-limit:]


def run_captured(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    redactions: list[str],
    timeout_seconds: int,
    timeout_reason: str | None = None,
    include_timestamps: bool = False,
) -> dict[str, Any]:
    started_at = datetime.now(UTC).isoformat()
    started = perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        if timeout_reason is None:
            raise
        result: dict[str, Any] = {
            "status": "censored",
            "reason": timeout_reason,
            "elapsedSeconds": round(perf_counter() - started, 6),
            "outputTail": redact_tail(str(error), redactions),
        }
    else:
        result = {
            "status": "passed" if completed.returncode == 0 else "failed",
            "returnCode": completed.returncode,
            "elapsedSeconds": round(perf_counter() - started, 6),
            "outputTail": redact_tail(
                "\n".join(
                    part for part in (completed.stdout, completed.stderr) if part
                ),
                redactions,
            ),
        }
    if include_timestamps:
        result["startedAt"] = started_at
        result["finishedAt"] = datetime.now(UTC).isoformat()
    return result


def copy_terraform_inputs(source: Path, destination: Path) -> list[str]:
    destination.mkdir(parents=True, exist_ok=False)
    copied = []
    for path in source.iterdir():
        if not path.is_file():
            continue
        if path.suffix not in {".tf", ".tpl", ".tftpl"} and path.name != ".terraform.lock.hcl":
            continue
        shutil.copyfile(path, destination / path.name)
        copied.append(path.name)
    return sorted(copied)
