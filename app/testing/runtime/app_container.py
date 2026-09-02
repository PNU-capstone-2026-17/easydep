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
_ENVIRONMENT_BUILD_FAILURE_MARKERS = (
    "failed to fetch",
    "connection reset",
    "connection refused",
    "network is unreachable",
    "context deadline exceeded",
)


class ApplicationLaunchError(Exception):
    """생성된 애플리케이션을 실행할 수 없을 때 원인 소유자도 함께 전달한다."""

    def __init__(self, message: str, *, defect_class: str = "SUT_DEFECT") -> None:
        super().__init__(message)
        self.defect_class = defect_class


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
    """health endpoint가 실제 성공 응답을 반환하는지 확인한다."""
    try:
        response = urllib.request.urlopen(url, timeout=5)  # noqa: S310 - localhost only
        return 200 <= response.status < 300
    except urllib.error.HTTPError:
        return False
    except (urllib.error.URLError, OSError):
        return False


def _container_logs(name: str) -> str:
    # Spring Boot는 원인 설명 뒤에 긴 stack trace를 출력한다. 마지막 80줄만 읽으면
    # 정작 첫 예외가 잘릴 수 있으므로 넉넉히 수집하고 아래 helper가 응답 크기만 줄인다.
    completed = _docker(["logs", "--tail", "240", name], timeout=30)
    return (completed.stdout or "") + (completed.stderr or "")


def _log_excerpt(logs: str, limit: int = 4000) -> str:
    """긴 로그에서 시작 원인과 마지막 예외를 모두 남긴다."""

    if len(logs) <= limit:
        return logs
    half = max(1, (limit - len("\n... omitted ...\n")) // 2)
    return logs[:half] + "\n... omitted ...\n" + logs[-half:]


def _build_failure_defect_class(output: str) -> str:
    """외부 환경 때문에 실패했는지, 생성된 애플리케이션 문제인지 구분한다.

    Dockerfile이 존재하지 않는 파일을 ``COPY``하는 경우처럼 build context와
    Dockerfile이 맞지 않는 문제는 생성된 애플리케이션을 고쳐야 한다. 네트워크처럼
    코드를 바꿔도 해결할 수 없는 경우만 실행 환경 문제로 분류한다.
    """

    lowered = output.casefold()
    if any(marker in lowered for marker in _ENVIRONMENT_BUILD_FAILURE_MARKERS):
        return "ENVIRONMENT_DEFECT"
    return "SUT_DEFECT"


def runtime_identity(app_id: str, launch_id: str | None = None) -> tuple[str, str]:
    """병렬 Testing 작업마다 겹치지 않는 image와 container 이름을 만든다."""
    unique_launch_id = launch_id or uuid.uuid4().hex
    suffix = hashlib.sha256(
        f"{app_id}\0{unique_launch_id}".encode()
    ).hexdigest()[:20]
    return f"easydep-testing:{suffix}", f"easydep-testing-{suffix}"


def runtime_network_name(container_name: str) -> str:
    """Derive a per-run network name from the already unique container name."""
    return f"{container_name}-net"


def _wait_until_ready(name: str, url: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _responds(url):
            return
        running = _docker(["inspect", "-f", "{{.State.Running}}", name], timeout=30)
        if running.returncode != 0 or "true" not in (running.stdout or "").lower():
            raise ApplicationLaunchError(
                "생성된 애플리케이션이 요청을 받기 전에 종료됐습니다:\n"
                f"{_log_excerpt(_container_logs(name))}"
            )
        time.sleep(2)
    raise ApplicationLaunchError(
        f"생성된 애플리케이션이 {timeout}초 안에 {url}에 응답하지 않았습니다:\n"
        f"{_log_excerpt(_container_logs(name))}"
    )


@contextmanager
def running_application(
    app_id: str,
    application_dir: str | Path,
    *,
    launch_id: str | None = None,
    health_path: str = "/healthz",
    build_timeout_seconds: int = DEFAULT_BUILD_TIMEOUT_SECONDS,
    start_timeout_seconds: int = DEFAULT_START_TIMEOUT_SECONDS,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """이미 복원된 폴더를 build·실행하고 접속 URL을 반환한다."""
    context = Path(application_dir)
    container_port = exposed_port(context)
    tag, name = runtime_identity(app_id, launch_id)
    network = runtime_network_name(name)
    host_port = free_port()

    built = _docker(
        ["build", "-t", tag, "-f", str(context / "Dockerfile"), str(context)],
        timeout=build_timeout_seconds,
        cwd=context,
    )
    if built.returncode != 0:
        build_output = (built.stdout or "") + (built.stderr or "")
        raise ApplicationLaunchError(
            "생성된 애플리케이션 image를 build하지 못했습니다:\n"
            + _log_excerpt(build_output),
            defect_class=_build_failure_defect_class(build_output),
        )

    # 같은 실행 ID의 이전 비정상 종료가 남겼을 수 있는 container만 정리한다.
    _docker(["rm", "-f", name], timeout=60)
    created_network = _docker(["network", "create", network], timeout=60)
    if created_network.returncode != 0:
        _docker(["rmi", "-f", tag], timeout=120)
        raise ApplicationLaunchError(
            "Testing용 Docker network를 만들지 못했습니다:\n"
            + (created_network.stderr or created_network.stdout or "")[-2000:],
            defect_class="ENVIRONMENT_DEFECT",
        )
    started = _docker(
        [
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            # Testing은 아직 CSP의 실제 DB를 provision하지 않는다. 생성 애플리케이션이
            # 외부 DB 주소 때문에 실패하지 않도록 함께 생성된 test profile과 임시 H2를 쓴다.
            "-e",
            "SPRING_PROFILES_ACTIVE=test",
            "-e",
            "SPRING_DATASOURCE_URL=jdbc:h2:mem:easydep_testing;MODE=MySQL;DB_CLOSE_DELAY=-1",
            "-e",
            "SPRING_DATASOURCE_USERNAME=sa",
            "-e",
            "SPRING_DATASOURCE_PASSWORD=",
            # 생성기가 인증 요구를 발견하면 이 표준 Spring 변수를 필수로 만든다. 운영
            # 비밀값을 재사용하지 않고 Testing 전용 계정을 주입해 같은 image를 안전하게 띄운다.
            "-e",
            "SPRING_SECURITY_USER_NAME=easydep-test",
            "-e",
            "SPRING_SECURITY_USER_PASSWORD=easydep-test",
            "-e",
            "SPRING_SECURITY_USER_ROLES=USER",
            "-p",
            f"127.0.0.1:{host_port}:{container_port}",
            tag,
        ],
        timeout=120,
    )
    if started.returncode != 0:
        _docker(["network", "rm", network], timeout=120)
        _docker(["rmi", "-f", tag], timeout=120)
        raise ApplicationLaunchError(
            "생성된 애플리케이션 container를 시작하지 못했습니다:\n"
            + (started.stderr or started.stdout or "")[-2000:],
            defect_class="ENVIRONMENT_DEFECT",
        )

    base_url = f"http://localhost:{host_port}"
    try:
        normalized_health = health_path if health_path.startswith("/") else f"/{health_path}"
        _wait_until_ready(name, f"{base_url}{normalized_health}", start_timeout_seconds)
        yield base_url, {
            "source": "application",
            "image": tag,
            "container": name,
            "network": network,
            "containerPort": container_port,
            "hostPort": host_port,
            "healthPath": normalized_health,
        }
    finally:
        _docker(["rm", "-f", name], timeout=120)
        _docker(["rmi", "-f", tag], timeout=300)
        _docker(["network", "rm", network], timeout=120)
