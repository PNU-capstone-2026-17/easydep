"""생성된 애플리케이션을 실행해 동적 테스트가 접속할 주소를 준비한다.

동적 기능 테스트는 실제로 실행 중인 서버에 요청을 보낸다. 구현 단계가 backend 소스,
frontend와 ``Dockerfile``을 DB snapshot으로 저장하므로, 이 모듈은 그 파일을 Docker build
context로 복원하고 빈 host port에 container를 실행한다. 테스트가 끝나면 이 실행에서 만든
container와 image만 정리한다.
"""

from __future__ import annotations

import hashlib
import re
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from app.db.models import TYPE_DEPLOYMENT_FILE, TYPE_SOURCE_CODE
from app.testing.runtime.process import run_process_tree
from app.testing.schemas.testing_input import TestingInput
from app.testing.utils.artifact_source import (
    ARTIFACT_APPLICATION_PREFIXES,
    ArtifactSnapshotMismatch,
    ArtifactSourceUnavailable,
    materialize_artifact,
)

# 구현 단계의 build context는 ``<run_root>/application``이었다. 저장할 때 frontend 파일은
# 별도 산출물로 나누면서 ``frontend/`` 접두사를 제거하므로 복원할 때 다시 붙여야 원래
# Dockerfile이 기대하는 폴더 구조가 된다.
ARTIFACT_PREFIXES = ARTIFACT_APPLICATION_PREFIXES
# 소스와 Dockerfile이 없으면 애플리케이션을 build할 수 없다.
REQUIRED_ARTIFACTS = (TYPE_SOURCE_CODE, TYPE_DEPLOYMENT_FILE)

DEFAULT_BUILD_TIMEOUT_SECONDS = 1800
DEFAULT_START_TIMEOUT_SECONDS = 180
_EXPOSE = re.compile(r"(?mi)^\s*EXPOSE\s+(?P<port>\d+)")
_FALLBACK_CONTAINER_PORT = 8080


class ApplicationLaunchError(Exception):
    """생성된 애플리케이션을 동적 테스트용으로 실행하지 못했음을 나타낸다."""


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


def build_context(
    app_id: str,
    destination: Path,
    *,
    testing_input: TestingInput | None = None,
    artifact_versions: dict[str, int] | None = None,
    implementation_job_id: str | None = None,
) -> dict[str, Any]:
    """저장된 구현 파일을 Docker build context로 복원한다.

    ``testing_input``이나 ``artifact_versions``가 있으면 그 목록에 든 버전만 사용한다.
    목록에 없는 선택 파일을 최신 snapshot으로 채우지 않으므로 테스트 도중 다른 구현
    작업의 frontend나 IaC 파일이 섞이지 않는다. 두 값을 모두 생략한 기존 호출은 현재
    snapshot을 복원한다.
    """
    if testing_input is not None and testing_input.app_id != app_id:
        raise ApplicationLaunchError(
            "testing_input의 app_id가 실행할 앱과 다릅니다: "
            f"app={app_id}, testing_input={testing_input.app_id}"
        )
    fixed_artifacts = testing_input is not None or artifact_versions is not None
    versions = (
        testing_input.version_map()
        if testing_input is not None
        else dict(artifact_versions or {})
    )
    references = dict(testing_input.artifacts) if testing_input is not None else {}
    expected_job = (
        testing_input.implementation_job_id
        if testing_input is not None
        else implementation_job_id
    )
    sources: dict[str, Any] = {}
    for artifact_type, prefix in ARTIFACT_PREFIXES.items():
        if fixed_artifacts and artifact_type not in versions:
            if artifact_type in REQUIRED_ARTIFACTS:
                raise ApplicationLaunchError(
                    f"고정된 테스트 입력에 {artifact_type} snapshot이 없습니다."
                )
            continue
        target = destination / prefix if prefix else destination
        try:
            sources[artifact_type] = materialize_artifact(
                app_id,
                artifact_type,
                target,
                version_no=versions.get(artifact_type),
                snapshot_ref=references.get(artifact_type),
                expected_implementation_job_id=expected_job,
            )
        except (ArtifactSourceUnavailable, ArtifactSnapshotMismatch) as error:
            if artifact_type in REQUIRED_ARTIFACTS:
                raise ApplicationLaunchError(str(error)) from error
            if fixed_artifacts:
                # 고정 입력에 포함된 선택 산출물도 사라지거나 달라지면 조용히 빼지 않는다.
                # 실행에 사용한 파일 목록이 Testing job의 provenance와 달라지기 때문이다.
                raise ApplicationLaunchError(str(error)) from error
    if not (destination / "Dockerfile").is_file():
        raise ApplicationLaunchError(
            f"The stored {TYPE_DEPLOYMENT_FILE} snapshot for app {app_id} has no Dockerfile."
        )
    return sources


def exposed_port(context: Path) -> int:
    """Dockerfile의 EXPOSE 값을 읽어 애플리케이션이 기다리는 port를 찾는다."""
    match = _EXPOSE.search((context / "Dockerfile").read_text(encoding="utf-8"))
    return int(match.group("port")) if match else _FALLBACK_CONTAINER_PORT


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _responds(url: str) -> bool:
    """HTTP 상태와 관계없이 응답이 오면 서버가 요청을 받을 준비가 된 것으로 본다."""
    try:
        urllib.request.urlopen(url, timeout=5)  # noqa: S310 - fixed localhost URL
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError):
        return False
    return True


def _container_logs(name: str) -> str:
    completed = _docker(["logs", "--tail", "80", name], timeout=30)
    return (completed.stdout or "") + (completed.stderr or "")


def runtime_identity(app_id: str, launch_id: str | None = None) -> tuple[str, str]:
    """한 번의 테스트 실행만 사용하는 Docker image와 container 이름을 만든다.

    같은 앱을 동시에 테스트하더라도 각 Testing job의 ``launch_id``가 다르므로 서로의
    image를 덮어쓰거나 container를 삭제하지 않는다. 호출자가 실행 ID를 주지 않는 직접
    호출도 UUID를 사용해 다른 실행과 겹치지 않게 한다. Docker 이름에 앱 이름을 그대로
    넣지 않고 SHA-256 일부만 쓰므로 공백이나 특수 문자가 있는 앱 ID도 안전하다.
    """
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
        running = _docker(
            ["inspect", "-f", "{{.State.Running}}", name], timeout=30
        )
        if running.returncode != 0 or "true" not in (running.stdout or "").lower():
            raise ApplicationLaunchError(
                "The generated application container exited before it served a "
                f"request:\n{_container_logs(name)[-2000:]}"
            )
        time.sleep(2)
    raise ApplicationLaunchError(
        f"The generated application did not answer on {url} within {timeout}s:\n"
        f"{_container_logs(name)[-2000:]}"
    )


@contextmanager
def running_application(
    app_id: str,
    *,
    launch_id: str | None = None,
    testing_input: TestingInput | None = None,
    artifact_versions: dict[str, int] | None = None,
    implementation_job_id: str | None = None,
    build_timeout_seconds: int = DEFAULT_BUILD_TIMEOUT_SECONDS,
    start_timeout_seconds: int = DEFAULT_START_TIMEOUT_SECONDS,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """저장된 앱을 build·실행하고 ``(접속 URL, 출처 정보)``를 반환한다.

    테스트 중 예외가 발생해도 이 실행이 만든 container와 image를 항상 정리한다. 따라서
    실패한 테스트가 container 이름이나 port를 계속 차지하지 않는다.
    """
    tag, name = runtime_identity(app_id, launch_id)
    host_port = free_port()

    with tempfile.TemporaryDirectory(prefix="easydep-testing-app-") as temporary:
        context = Path(temporary)
        sources = build_context(
            app_id,
            context,
            testing_input=testing_input,
            artifact_versions=artifact_versions,
            implementation_job_id=implementation_job_id,
        )
        container_port = exposed_port(context)

        built = _docker(
            ["build", "-t", tag, "-f", str(context / "Dockerfile"), str(context)],
            timeout=build_timeout_seconds,
            cwd=context,
        )
        if built.returncode != 0:
            raise ApplicationLaunchError(
                "Failed to build the generated application image:\n"
                + (built.stderr or built.stdout or "")[-2000:]
            )

        # 같은 실행 ID로 이전 시도가 비정상 종료됐다면 남은 container만 먼저 정리한다.
        _docker(["rm", "-f", name], timeout=60)
        started = _docker(
            [
                "run", "--rm", "-d",
                "--name", name,
                "-p", f"127.0.0.1:{host_port}:{container_port}",
                tag,
            ],
            timeout=120,
        )
        if started.returncode != 0:
            _docker(["rmi", "-f", tag], timeout=120)
            raise ApplicationLaunchError(
                "Failed to start the generated application container:\n"
                + (started.stderr or started.stdout or "")[-2000:]
            )

        base_url = f"http://localhost:{host_port}"
        try:
            _wait_until_ready(name, base_url, start_timeout_seconds)
            yield base_url, {
                "source": "db",
                "image": tag,
                "container": name,
                "containerPort": container_port,
                "hostPort": host_port,
                "artifacts": {
                    artifact_type: info.get("version_no")
                    for artifact_type, info in sources.items()
                },
            }
        finally:
            _docker(["rm", "-f", name], timeout=120)
            _docker(["rmi", "-f", tag], timeout=300)
