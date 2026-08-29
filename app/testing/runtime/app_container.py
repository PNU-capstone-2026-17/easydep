"""복원된 애플리케이션 폴더를 Docker로 실행한다."""

from __future__ import annotations

import hashlib
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from app.testing.runtime.process import run_process_tree

DEFAULT_BUILD_TIMEOUT_SECONDS = 1800
DEFAULT_START_TIMEOUT_SECONDS = 180
_EXPOSE = re.compile(r"(?mi)^\s*EXPOSE\s+(?P<port>\d+)")
_FALLBACK_CONTAINER_PORT = 8080


class ApplicationLaunchError(Exception):
    """생성된 애플리케이션을 실행할 수 없을 때 발생한다."""


def _docker(
    arguments: list[str], *, timeout: int, cwd: Path | None = None
) -> subprocess.CompletedProcess:
    return run_process_tree(
        ["docker", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def exposed_port(context: Path) -> int:
    """Dockerfile의 EXPOSE 값이 있으면 사용하고, 없으면 8080을 사용한다."""
    dockerfile = context / "Dockerfile"
    if not dockerfile.is_file():
        raise ApplicationLaunchError(
            f"복원된 애플리케이션에 Dockerfile이 없습니다: {context}"
        )
    match = _EXPOSE.search(dockerfile.read_text(encoding="utf-8"))
    return int(match.group("port")) if match else _FALLBACK_CONTAINER_PORT


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _responds(url: str) -> bool:
    """HTTP 상태와 관계없이 응답이 오면 서버가 준비된 것으로 본다."""
    try:
        urllib.request.urlopen(url, timeout=5)  # noqa: S310 - localhost only
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError):
        return False
    return True


def _container_logs(name: str) -> str:
    completed = _docker(["logs", "--tail", "80", name], timeout=30)
    return (completed.stdout or "") + (completed.stderr or "")


def runtime_identity(app_id: str, launch_id: str | None = None) -> tuple[str, str]:
    """병렬 Testing 작업마다 겹치지 않는 image와 container 이름을 만든다."""
    unique_launch_id = launch_id or uuid.uuid4().hex
    suffix = hashlib.sha256(
        f"{app_id}\0{unique_launch_id}".encode()
    ).hexdigest()[:20]
    return f"easydep-testing:{suffix}", f"easydep-testing-{suffix}"


def _wait_until_ready(name: str, url: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _responds(url):
            return
        running = _docker(["inspect", "-f", "{{.State.Running}}", name], timeout=30)
        if running.returncode != 0 or "true" not in (running.stdout or "").lower():
            raise ApplicationLaunchError(
                "생성된 애플리케이션이 요청을 받기 전에 종료됐습니다:\n"
                f"{_container_logs(name)[-2000:]}"
            )
        time.sleep(2)
    raise ApplicationLaunchError(
        f"생성된 애플리케이션이 {timeout}초 안에 {url}에 응답하지 않았습니다:\n"
        f"{_container_logs(name)[-2000:]}"
    )


@contextmanager
def running_application(
    app_id: str,
    application_dir: str | Path,
    *,
    launch_id: str | None = None,
    build_timeout_seconds: int = DEFAULT_BUILD_TIMEOUT_SECONDS,
    start_timeout_seconds: int = DEFAULT_START_TIMEOUT_SECONDS,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """이미 복원된 폴더를 build·실행하고 접속 URL을 반환한다."""
    context = Path(application_dir)
    container_port = exposed_port(context)
    tag, name = runtime_identity(app_id, launch_id)
    host_port = free_port()

    built = _docker(
        ["build", "-t", tag, "-f", str(context / "Dockerfile"), str(context)],
        timeout=build_timeout_seconds,
        cwd=context,
    )
    if built.returncode != 0:
        raise ApplicationLaunchError(
            "생성된 애플리케이션 image를 build하지 못했습니다:\n"
            + (built.stderr or built.stdout or "")[-2000:]
        )

    # 같은 실행 ID의 이전 비정상 종료가 남겼을 수 있는 container만 정리한다.
    _docker(["rm", "-f", name], timeout=60)
    started = _docker(
        [
            "run",
            "--rm",
            "-d",
            "--name",
            name,
            "-p",
            f"127.0.0.1:{host_port}:{container_port}",
            tag,
        ],
        timeout=120,
    )
    if started.returncode != 0:
        _docker(["rmi", "-f", tag], timeout=120)
        raise ApplicationLaunchError(
            "생성된 애플리케이션 container를 시작하지 못했습니다:\n"
            + (started.stderr or started.stdout or "")[-2000:]
        )

    base_url = f"http://localhost:{host_port}"
    try:
        _wait_until_ready(name, base_url, start_timeout_seconds)
        yield base_url, {
            "source": "application",
            "image": tag,
            "container": name,
            "containerPort": container_port,
            "hostPort": host_port,
        }
    finally:
        _docker(["rm", "-f", name], timeout=120)
        _docker(["rmi", "-f", tag], timeout=300)
