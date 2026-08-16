"""Bring the generated application up so dynamic tests have something to hit.

Dynamic functional testing asserts against a *running* system, and until now
nothing started one — ``target_url`` pointed at a hardcoded localhost port that
nobody was listening on.

Everything needed to run the application is already in the database: the
implementation agent stores the backend source tree, the frontend, and the
generated multi-stage ``Dockerfile``.  So this reassembles those snapshots into
a build context, builds the image, and publishes the container on a free host
port for exactly the length of one test run.
"""

from __future__ import annotations

import re
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from app.db.models import (
    TYPE_DEPLOYMENT_FILE,
    TYPE_FRONTEND_SOURCE_CODE,
    TYPE_IAC_CODE,
    TYPE_SOURCE_CODE,
    TYPE_TEST_CODE,
)
from app.testing.runtime.process import run_process_tree
from app.testing.utils.artifact_source import (
    ArtifactSourceUnavailable,
    materialize_artifact,
)

# The build context the implementation agent had was ``<run_root>/application``.
# ``_persist_outputs`` strips the ``frontend/`` prefix when it files those
# sources under their own artifact type, so restoring it here rebuilds the tree
# the generated Dockerfile was written against.
ARTIFACT_PREFIXES: dict[str, str] = {
    TYPE_SOURCE_CODE: "",
    TYPE_FRONTEND_SOURCE_CODE: "frontend",
    TYPE_TEST_CODE: "",
    TYPE_DEPLOYMENT_FILE: "",
    TYPE_IAC_CODE: "",
}
# Without these two there is no application and no way to build one.
REQUIRED_ARTIFACTS = (TYPE_SOURCE_CODE, TYPE_DEPLOYMENT_FILE)

DEFAULT_BUILD_TIMEOUT_SECONDS = 1800
DEFAULT_START_TIMEOUT_SECONDS = 180
_EXPOSE = re.compile(r"(?mi)^\s*EXPOSE\s+(?P<port>\d+)")
_FALLBACK_CONTAINER_PORT = 8080


class ApplicationLaunchError(Exception):
    """The generated application could not be started for dynamic testing."""


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


def build_context(app_id: str, destination: Path) -> dict[str, Any]:
    """Rebuild the implementation agent's application tree from stored snapshots."""
    sources: dict[str, Any] = {}
    for artifact_type, prefix in ARTIFACT_PREFIXES.items():
        target = destination / prefix if prefix else destination
        try:
            sources[artifact_type] = materialize_artifact(app_id, artifact_type, target)
        except ArtifactSourceUnavailable as error:
            if artifact_type in REQUIRED_ARTIFACTS:
                raise ApplicationLaunchError(str(error)) from error
    if not (destination / "Dockerfile").is_file():
        raise ApplicationLaunchError(
            f"The stored {TYPE_DEPLOYMENT_FILE} snapshot for app {app_id} has no Dockerfile."
        )
    return sources


def exposed_port(context: Path) -> int:
    """The port the generated Dockerfile publishes, so the probe is not a guess."""
    match = _EXPOSE.search((context / "Dockerfile").read_text(encoding="utf-8"))
    return int(match.group("port")) if match else _FALLBACK_CONTAINER_PORT


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _responds(url: str) -> bool:
    """Any HTTP status means the server is listening; only transport errors don't."""
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
    build_timeout_seconds: int = DEFAULT_BUILD_TIMEOUT_SECONDS,
    start_timeout_seconds: int = DEFAULT_START_TIMEOUT_SECONDS,
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Build and run the stored application, yielding ``(base_url, provenance)``.

    The container is always removed on the way out, including when the test run
    raises, so a failed run cannot leave the port held.
    """
    tag = f"easydep-testing/{app_id.lower()}:under-test"
    name = f"easydep-testing-{app_id.lower()}"
    host_port = free_port()

    with tempfile.TemporaryDirectory(prefix="easydep-testing-app-") as temporary:
        context = Path(temporary)
        sources = build_context(app_id, context)
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

        # A container left over from an interrupted run would hold the name.
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
