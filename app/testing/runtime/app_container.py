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
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from app.implementation.runtime.process import run_process_tree
from app.testing.runtime.container_runner import (
    GRADLE_CACHE_VOLUME,
    configured_runner_image,
)

DEFAULT_START_TIMEOUT_SECONDS = 360
_EXPOSE = re.compile(r"(?mi)^\s*EXPOSE\s+(?P<port>\d+)")
_FALLBACK_CONTAINER_PORT = 8080
_ENVIRONMENT_BUILD_FAILURE_MARKERS = (
    "failed to fetch",
    "connection reset",
    "connection refused",
    "network is unreachable",
    "context deadline exceeded",
)
_ACTIVE_TESTING_CONTAINERS: set[str] = set()


class ApplicationLaunchError(Exception):
    """생성된 애플리케이션을 실행할 수 없을 때 원인 소유자도 함께 전달한다."""

    def __init__(
        self,
        message: str,
        *,
        defect_class: str = "SUT_DEFECT",
        application_log: str = "",
    ) -> None:
        super().__init__(message)
        self.defect_class = defect_class
        self.application_log = application_log


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
    # 원문은 자르지 않는다. 화면과 LLM prompt의 미리보기만 별도로 제한한다.
    completed = _docker(["logs", name], timeout=30)
    return (completed.stdout or "") + (completed.stderr or "")


def _log_excerpt(logs: str, limit: int = 4000) -> str:
    """긴 로그에서 시작, 마지막 출력과 중간의 근본 예외를 함께 남긴다."""

    if len(logs) <= limit:
        return logs
    omitted = "\n... omitted ...\n"
    anchors = ("\nCaused by:", " with root cause", "Exception:", "\nERROR ")
    anchor = max(logs.rfind(marker) for marker in anchors)
    if anchor < 0:
        half = max(1, (limit - len(omitted)) // 2)
        return (logs[:half] + omitted + logs[-half:])[:limit]

    available = limit - (2 * len(omitted))
    if available < 3:
        return logs[:limit]
    edge_size = max(1, available // 4)
    middle_size = available - (2 * edge_size)
    middle_start = max(edge_size, anchor - (middle_size // 4))
    middle_end = min(len(logs) - edge_size, middle_start + middle_size)
    middle_start = max(edge_size, middle_end - middle_size)
    if middle_start <= edge_size or middle_end >= len(logs) - edge_size:
        half = max(1, (limit - len(omitted)) // 2)
        return (logs[:half] + omitted + logs[-half:])[:limit]
    return (
        logs[:edge_size]
        + omitted
        + logs[middle_start:middle_end]
        + omitted
        + logs[-edge_size:]
    )[:limit]


def application_log(runtime: Mapping[str, Any]) -> str:
    """현재 Testing이 소유한 실행 중인 앱의 전체 로그를 읽는다.

    외부 ``target_url``이나 보고서에서 온 임의 container 이름으로 Docker 로그를
    읽으면 다른 실행의 정보를 유출할 수 있다. 이 프로세스의
    :func:`running_application`이 아직 소유하고 있는 이름만 허용한다.
    """

    if not isinstance(runtime, Mapping) or runtime.get("source") != "application":
        return ""
    name = runtime.get("container")
    if not isinstance(name, str):
        return ""
    if name not in _ACTIVE_TESTING_CONTAINERS:
        return ""
    return _container_logs(name)


def application_log_excerpt(runtime: Mapping[str, Any], *, limit: int = 6000) -> str:
    """전체 원문을 보존한 상태에서 표시용 미리보기만 제한한다."""

    return _log_excerpt(application_log(runtime), limit=limit)


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
        try:
            running = _docker(
                ["inspect", "-f", "{{.State.Running}}", name], timeout=30
            )
        except subprocess.TimeoutExpired as error:
            raise ApplicationLaunchError(
                "Docker timed out while checking the generated application container.",
                defect_class="ENVIRONMENT_DEFECT",
            ) from error
        if running.returncode != 0 or "true" not in (running.stdout or "").lower():
            logs = _container_logs(name)
            excerpt = _log_excerpt(logs)
            raise ApplicationLaunchError(
                "생성된 애플리케이션이 요청을 받기 전에 종료됐습니다:\n"
                f"{excerpt}",
                defect_class=_build_failure_defect_class(logs),
                application_log=logs,
            )
        time.sleep(2)
    logs = _container_logs(name)
    excerpt = _log_excerpt(logs)
    raise ApplicationLaunchError(
        f"생성된 애플리케이션이 {timeout}초 안에 {url}에 응답하지 않았습니다:\n"
        f"{excerpt}",
        # 준비 시간 초과만으로 source 결함을 확정할 수 없다. 첫 Gradle 실행이나 Windows
        # bind mount가 느린 경우 코드를 고쳐도 달라지지 않으므로 환경 문제로 재실행한다.
        defect_class="ENVIRONMENT_DEFECT",
        application_log=logs,
    )


@contextmanager
def running_application(
    app_id: str,
    application_dir: str | Path,
    *,
    launch_id: str | None = None,
    health_path: str = "/healthz",
    start_timeout_seconds: int = DEFAULT_START_TIMEOUT_SECONDS,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """공용 툴체인에서 복원된 backend를 실행하고 접속 URL을 반환한다.

    배포용 Dockerfile은 frontend까지 포함한 최종 image를 만드는 산출물이다. API 기능
    테스트마다 그 image를 다시 만들면 npm 설치와 Gradle dependency 다운로드가 반복된다.
    구현 단계가 이미 단위·작은 통합 테스트와 frontend build를 통과시켰으므로 여기서는
    고정 툴체인과 공유 Gradle cache로 Spring Boot backend만 실행한다.
    """
    context = Path(application_dir)
    container_port = exposed_port(context)
    _, name = runtime_identity(app_id, launch_id)
    network = runtime_network_name(name)
    host_port = free_port()
    runner_image = configured_runner_image()

    # 같은 실행 ID의 이전 비정상 종료가 남겼을 수 있는 container만 정리한다.
    _docker(["rm", "-f", name], timeout=60)
    created_network = _docker(["network", "create", network], timeout=60)
    if created_network.returncode != 0:
        output = created_network.stderr or created_network.stdout or ""
        raise ApplicationLaunchError(
            "Testing용 Docker network를 만들지 못했습니다:\n"
            + _log_excerpt(output, limit=2000),
            defect_class="ENVIRONMENT_DEFECT",
            application_log=output,
        )
    started = _docker(
        [
            "run",
            "-d",
            "--name",
            name,
            "--network",
            network,
            "--label",
            "easydep.owner=testing-application",
            "-v",
            f"{context.resolve()}:/easydep-application:rw",
            "-v",
            f"{GRADLE_CACHE_VOLUME}:/tmp/easydep-gradle-cache",
            "-w",
            "/easydep-application",
            "-e",
            "GRADLE_USER_HOME=/tmp/easydep-gradle-cache",
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
            "--entrypoint",
            "gradle",
            runner_image,
            "bootRun",
            "--no-daemon",
            "--build-cache",
        ],
        timeout=120,
    )
    if started.returncode != 0:
        _docker(["network", "rm", network], timeout=120)
        output = started.stderr or started.stdout or ""
        raise ApplicationLaunchError(
            "공용 툴체인에서 생성된 애플리케이션을 시작하지 못했습니다:\n"
            + _log_excerpt(output, limit=2000),
            defect_class="ENVIRONMENT_DEFECT",
            application_log=output,
        )

    base_url = f"http://localhost:{host_port}"
    try:
        normalized_health = health_path if health_path.startswith("/") else f"/{health_path}"
        _wait_until_ready(name, f"{base_url}{normalized_health}", start_timeout_seconds)
        runtime = {
            "source": "application",
            "image": runner_image,
            "container": name,
            "network": network,
            "containerPort": container_port,
            "hostPort": host_port,
            "healthPath": normalized_health,
            "profile": "test",
            "database": "h2-mysql-mode",
        }
        _ACTIVE_TESTING_CONTAINERS.add(name)
        try:
            yield base_url, runtime
        finally:
            _ACTIVE_TESTING_CONTAINERS.discard(name)
    finally:
        _docker(["rm", "-f", name], timeout=120)
        _docker(["network", "rm", network], timeout=120)
