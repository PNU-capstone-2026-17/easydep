"""산출물 근거로 판정하는 게이트와 생성 앱을 띄우는 오라클 게이트."""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin
from urllib.request import urlopen

from .evaluate import evidence_exists
from .models import SubjectResult
from .oracle import run_http_oracle
from .process import run_command

COMPOSE_NAMES = {
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
}
MAX_READ_BYTES = 10 * 1024 * 1024
LOG_TAIL = 4000


def _resolved(subject: SubjectResult, raw: str) -> Path:
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else subject.workspace / candidate


def _artifact_files(subject: SubjectResult, artifact_id: str) -> list[Path]:
    return [
        path
        for path in (
            _resolved(subject, raw)
            for raw in subject.artifact_evidence.get(artifact_id, ())
        )
        if path.is_file()
    ]


def _display(subject: SubjectResult, path: Path) -> str:
    try:
        return path.relative_to(subject.workspace).as_posix()
    except ValueError:
        return str(path)


def run_artifact_present(config: dict[str, Any], subject: SubjectResult) -> dict[str, Any]:
    """공통 산출물로 신고한 파일이 실제로 존재하는지 확인한다."""
    artifact_ids = [str(item) for item in config.get("artifacts", [])]
    if not artifact_ids:
        return {"status": "failed", "reason": "artifacts가 비어 있습니다."}
    present: list[str] = []
    missing: list[str] = []
    for artifact_id in artifact_ids:
        paths = subject.artifact_evidence.get(artifact_id, ())
        if any(evidence_exists(subject.workspace, path) for path in paths):
            present.append(artifact_id)
        else:
            missing.append(artifact_id)
    return {
        "status": "passed" if not missing else "failed",
        "presentArtifacts": present,
        "missingArtifacts": missing,
    }


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_READ_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def run_artifact_contains(config: dict[str, Any], subject: SubjectResult) -> dict[str, Any]:
    """산출물 본문에 필수 토큰이 있고 금지 토큰이 없는지 확인한다."""
    artifact_id = str(config.get("artifact", ""))
    if not artifact_id:
        return {"status": "failed", "reason": "artifact가 비어 있습니다."}
    any_of = [str(item) for item in config.get("anyOf", [])]
    none_of = [str(item) for item in config.get("noneOf", [])]
    if not any_of and not none_of:
        return {"status": "failed", "reason": "anyOf 또는 noneOf가 필요합니다."}
    files = _artifact_files(subject, artifact_id)
    if not files:
        return {
            "status": "failed",
            "reason": f"{artifact_id} 산출물 파일이 없습니다.",
            "checkedFiles": [],
        }
    corpus = "\n".join(_read(path) for path in files).lower()
    matched = [token for token in any_of if token.lower() in corpus]
    violated = [token for token in none_of if token.lower() in corpus]
    passed = (not any_of or bool(matched)) and not violated
    return {
        "status": "passed" if passed else "failed",
        "checkedFiles": [_display(subject, path) for path in files],
        "matchedTokens": matched,
        "missingTokens": [] if matched or not any_of else any_of,
        "violatedTokens": violated,
    }


def _pick_container_definition(subject: SubjectResult) -> tuple[Path | None, Path | None]:
    compose: Path | None = None
    dockerfile: Path | None = None
    for path in _artifact_files(subject, "container"):
        name = path.name.lower()
        if name in COMPOSE_NAMES and compose is None:
            compose = path
        elif name.startswith("dockerfile") and dockerfile is None:
            dockerfile = path
    return compose, dockerfile


def _service_answers(base_url: str, health_path: str, timeout: float) -> bool:
    url = urljoin(base_url.rstrip("/") + "/", health_path.lstrip("/"))
    try:
        with urlopen(url, timeout=timeout):  # noqa: S310 - 방금 띄운 로컬 컨테이너
            return True
    except HTTPError:
        return True
    except Exception:
        return False


def _tail(text: str) -> str:
    return text[-LOG_TAIL:] if len(text) > LOG_TAIL else text


def run_container_http_oracle(
    config: dict[str, Any], subject: SubjectResult, oracle: dict[str, Any]
) -> dict[str, Any]:
    """비교 대상이 만든 컨테이너 정의로 앱을 띄우고 공통 오라클을 실행한다."""
    port = int(config.get("port", 8080))
    base_url = f"http://127.0.0.1:{port}"
    health_path = str(config.get("healthPath", "/"))
    ready_timeout = float(config.get("readyTimeoutSeconds", 300))
    start_timeout = float(config.get("startTimeoutSeconds", 1800))
    empty: dict[str, Any] = {"phases": [], "passedPhases": 0, "totalPhases": 0}
    docker = shutil.which("docker")
    if docker is None:
        return {**empty, "status": "failed", "reason": "Docker를 찾을 수 없습니다."}
    compose, dockerfile = _pick_container_definition(subject)
    if compose is None and dockerfile is None:
        return {
            **empty,
            "status": "failed",
            "reason": "container 산출물에 실행 가능한 정의가 없습니다.",
        }
    tag = "easydep-comparison-" + hashlib.sha256(
        str(subject.workspace).encode("utf-8")
    ).hexdigest()[:12]
    if compose is not None:
        start_commands = [[docker, "compose", "-f", str(compose), "up", "-d", "--build"]]
        teardown = [docker, "compose", "-f", str(compose), "down", "-v"]
        cwd = compose.parent
        definition = _display(subject, compose)
    else:
        assert dockerfile is not None
        start_commands = [
            [docker, "build", "-t", tag, "-f", str(dockerfile), str(dockerfile.parent)],
            [docker, "run", "-d", "--name", tag, "-p", f"{port}:{port}", tag],
        ]
        teardown = [docker, "rm", "-f", tag]
        cwd = dockerfile.parent
        definition = _display(subject, dockerfile)
    startup: list[dict[str, Any]] = []
    try:
        for command in start_commands:
            result = run_command(command, cwd=cwd, timeout_seconds=start_timeout)
            startup.append(
                {
                    "command": command,
                    "exitCode": result["exitCode"],
                    "timedOut": result["timedOut"],
                    "stderr": _tail(result["stderr"]),
                }
            )
            if result["timedOut"] or result["exitCode"] != 0:
                return {
                    **empty,
                    "status": "failed",
                    "reason": "컨테이너 기동에 실패했습니다.",
                    "containerDefinition": definition,
                    "startup": startup,
                }
        deadline = time.monotonic() + ready_timeout
        ready = False
        while time.monotonic() < deadline:
            if _service_answers(base_url, health_path, timeout=5.0):
                ready = True
                break
            time.sleep(3.0)
        if not ready:
            return {
                **empty,
                "status": "failed",
                "reason": f"{ready_timeout:.0f}초 안에 {base_url}{health_path}가 응답하지 않았습니다.",
                "containerDefinition": definition,
                "startup": startup,
            }
        return {
            **run_http_oracle(oracle, base_url),
            "containerDefinition": definition,
            "baseUrl": base_url,
            "startup": startup,
        }
    finally:
        run_command(teardown, cwd=cwd, timeout_seconds=300)
