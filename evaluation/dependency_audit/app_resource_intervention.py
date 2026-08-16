"""최소 샘플 앱으로 앱-리소스 의존성을 control→개입→복원한다.

기본 ``process`` 모드는 CSP와 Docker에 독립적으로 관계 자체를 확인한다.
``docker`` 모드는 같은 샘플 앱을 실제 container/network/volume 경계에서 확인한다.
모든 생성 객체는 run label로 한정하고 ``finally``에서 정리한다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = Path(__file__).resolve().parent / "sample_app"
SCHEMA = "easydep-app-resource-intervention/v1"
EXPERIMENT_IDS = (
    "app-port-binding",
    "app-state-endpoint-binding",
    "state-resource-availability",
    "volume-state-persistence",
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request(port: int, method: str, path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=2) as response:  # noqa: S310 - loopback only
            body = response.read().decode("utf-8", errors="replace")
            return {"status": response.status, "body": body, "error": None}
    except HTTPError as error:
        return {
            "status": error.code,
            "body": error.read().decode("utf-8", errors="replace"),
            "error": None,
        }
    except (OSError, URLError) as error:
        return {"status": None, "body": "", "error": type(error).__name__}


def _wait_for(port: int, path: str, statuses: set[int], budget: float = 20) -> dict:
    deadline = time.monotonic() + budget
    last = {"status": None, "body": "", "error": "not-started"}
    while time.monotonic() < deadline:
        last = _request(port, "GET", path)
        if last["status"] in statuses:
            return last
        time.sleep(0.25)
    return last


@dataclass
class Child:
    process: subprocess.Popen[str]
    log_handle: object

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log_handle.close()  # type: ignore[attr-defined]


class Recorder:
    def __init__(self, mode: str, run_id: str) -> None:
        self.result: dict = {
            "schemaVersion": SCHEMA,
            "runId": run_id,
            "mode": mode,
            "startedAt": _now(),
            "experiments": [],
            "cleanupVerified": False,
            "residualResources": [],
        }

    def phase(
        self,
        experiment_id: str,
        phase: str,
        expected: str,
        action: Callable[[], tuple[bool, dict]],
    ) -> bool:
        started = time.monotonic()
        observed: dict
        try:
            passed, observed = action()
        except Exception as error:  # evidence must survive unexpected harness errors
            passed = False
            observed = {"exception": type(error).__name__, "message": str(error)[:500]}
        row = {
            "experimentId": experiment_id,
            "phase": phase,
            "status": "passed" if passed else "failed",
            "expected": expected,
            "observed": observed,
            "durationSeconds": round(time.monotonic() - started, 3),
        }
        self.result["experiments"].append(row)
        print(f"{experiment_id}/{phase}: {row['status']}", flush=True)
        return passed


def _start_process(role: str, port: int, log_dir: Path, **settings: str) -> Child:
    log_path = log_dir / f"{role}-{port}.log"
    handle = log_path.open("w", encoding="utf-8")
    command = [
        sys.executable,
        "-B",
        str(SAMPLE / "service.py"),
        "--role",
        role,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    for key, value in settings.items():
        command.extend([f"--{key.replace('_', '-')}", value])
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return Child(process=process, log_handle=handle)


def _status_is(port: int, path: str, expected: set[int]) -> tuple[bool, dict]:
    observed = _wait_for(port, path, expected)
    return observed["status"] in expected, observed


def run_process(recorder: Recorder) -> list[str]:
    children: list[Child] = []
    with tempfile.TemporaryDirectory(prefix="easydep-dependency-") as temporary:
        root = Path(temporary)
        persistent = root / "persistent" / "records.json"
        ephemeral = root / "ephemeral" / "records.json"
        state_port = _free_port()
        app_port = _free_port()

        def state(path: Path = persistent) -> Child:
            child = _start_process(
                "state", state_port, root, data_path=str(path)
            )
            children.append(child)
            _wait_for(state_port, "/health/ready", {200})
            return child

        def app(state_url: str, port: int = app_port) -> Child:
            child = _start_process("app", port, root, state_url=state_url)
            children.append(child)
            return child

        current_state = state()
        current_app = app(f"http://127.0.0.1:{state_port}")

        recorder.phase(
            "app-port-binding",
            "control",
            "계약된 port에서 readiness 200",
            lambda: _status_is(app_port, "/health/ready", {200}),
        )
        current_app.stop()
        children.remove(current_app)
        wrong_port = _free_port()
        current_app = app(f"http://127.0.0.1:{state_port}", wrong_port)
        recorder.phase(
            "app-port-binding",
            "intervention",
            "프로세스는 실행 중이지만 계약 port는 도달 불가",
            lambda: (
                current_app.process.poll() is None
                and _wait_for(app_port, "/health/ready", {200}, 3)["status"] is None,
                {
                    "processRunning": current_app.process.poll() is None,
                    "contractPort": app_port,
                    "actualPort": wrong_port,
                },
            ),
        )
        current_app.stop()
        children.remove(current_app)
        current_app = app(f"http://127.0.0.1:{state_port}")
        recorder.phase(
            "app-port-binding",
            "restoration",
            "계약 port 복원 후 readiness 200",
            lambda: _status_is(app_port, "/health/ready", {200}),
        )

        current_app.stop()
        children.remove(current_app)
        missing_port = _free_port()
        current_app = app(f"http://127.0.0.1:{missing_port}")
        recorder.phase(
            "app-state-endpoint-binding",
            "intervention",
            "잘못된 State endpoint에서 readiness 503",
            lambda: _status_is(app_port, "/health/ready", {503}),
        )
        current_app.stop()
        children.remove(current_app)
        current_app = app(f"http://127.0.0.1:{state_port}")
        recorder.phase(
            "app-state-endpoint-binding",
            "restoration",
            "State endpoint 복원 후 readiness 200",
            lambda: _status_is(app_port, "/health/ready", {200}),
        )

        current_state.stop()
        children.remove(current_state)
        recorder.phase(
            "state-resource-availability",
            "intervention",
            "State 중단 시 readiness 503과 업무 요청 실패",
            lambda: (
                _wait_for(app_port, "/health/ready", {503})["status"] == 503
                and _request(app_port, "GET", "/records/probe")["status"] == 502,
                {
                    "readiness": _request(app_port, "GET", "/health/ready"),
                    "business": _request(app_port, "GET", "/records/probe"),
                },
            ),
        )
        current_state = state()
        recorder.phase(
            "state-resource-availability",
            "restoration",
            "State 복원 후 readiness 200",
            lambda: _status_is(app_port, "/health/ready", {200}),
        )

        recorder.phase(
            "volume-state-persistence",
            "control",
            "영속 경로에 기록 성공",
            lambda: (
                _request(app_port, "PUT", "/records/durable", {"value": "kept"})["status"]
                == 200,
                _request(app_port, "GET", "/records/durable"),
            ),
        )
        current_state.stop()
        children.remove(current_state)
        current_state = state(ephemeral)
        recorder.phase(
            "volume-state-persistence",
            "intervention",
            "다른 비영속 경로로 재기동하면 기존 상태가 보이지 않음",
            lambda: (
                _request(app_port, "GET", "/records/durable")["status"] == 404,
                _request(app_port, "GET", "/records/durable"),
            ),
        )
        current_state.stop()
        children.remove(current_state)
        current_state = state(persistent)
        recorder.phase(
            "volume-state-persistence",
            "restoration",
            "원래 영속 경로로 복원하면 기존 상태 조회 성공",
            lambda: (
                _request(app_port, "GET", "/records/durable")["status"] == 200,
                _request(app_port, "GET", "/records/durable"),
            ),
        )

        for child in reversed(children):
            child.stop()
        residual = [str(child.process.pid) for child in children if child.process.poll() is None]
        return residual


def _docker(command: list[str], timeout: int = 180, check: bool = True) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("docker") or "docker"
    completed = subprocess.run(
        [executable, *command], capture_output=True, text=True, timeout=timeout, check=False
    )
    if check and completed.returncode:
        raise RuntimeError((completed.stderr or completed.stdout).strip())
    return completed


def _docker_ids(kind: str, label: str) -> list[str]:
    command = {
        "container": ["ps", "-aq", "--filter", f"label={label}"],
        "network": ["network", "ls", "-q", "--filter", f"label={label}"],
        "volume": ["volume", "ls", "-q", "--filter", f"label={label}"],
        "image": ["image", "ls", "-q", "--filter", f"label={label}"],
    }[kind]
    return sorted(set(_docker(command).stdout.split()))


def run_docker(recorder: Recorder) -> list[str]:
    """Docker 경계 실험.

    process 모드와 같은 네 관계를 확인한다. Docker daemon이 없는 경우에는
    환경 실패로 종료하며 process 결과를 대체하지 않는다.
    """

    label = f"easydep.dependency-run={recorder.result['runId']}"
    tag = f"easydep-dependency-sample:{recorder.result['runId']}"
    network = f"easydep-dep-{recorder.result['runId']}"
    volume = f"easydep-dep-{recorder.result['runId']}"
    setup_started = time.monotonic()
    try:
        docker_version = _docker(
            ["version", "--format", "{{.Server.Version}}"], timeout=30
        ).stdout.strip()
        _docker(["build", "--label", label, "-t", tag, str(SAMPLE)], timeout=900)
        _docker(["network", "create", "--label", label, network])
        _docker(["volume", "create", "--label", label, volume])
    except Exception:
        # setup 도중 일부 객체만 만들어진 경우에도 label 범위만 정리한다.
        try:
            for container in _docker_ids("container", label):
                _docker(["rm", "-f", container], check=False)
            for created_network in _docker_ids("network", label):
                _docker(["network", "rm", created_network], check=False)
            for created_volume in _docker_ids("volume", label):
                _docker(["volume", "rm", created_volume], check=False)
            for created_image in _docker_ids("image", label):
                _docker(["image", "rm", "-f", created_image], check=False)
        finally:
            raise
    recorder.result["environment"] = {
        "dockerServerVersion": docker_version,
        "sampleImageId": _docker(["image", "inspect", "-f", "{{.Id}}", tag]).stdout.strip(),
        "setupSeconds": round(time.monotonic() - setup_started, 3),
    }

    state_name = f"{network}-state"
    app_name = f"{network}-app"

    def remove(name: str) -> None:
        _docker(["rm", "-f", name], check=False)

    def start_state(*, persistent: bool, actual_port: int = 8081) -> None:
        remove(state_name)
        command = [
            "run",
            "-d",
            "--name",
            state_name,
            "--label",
            label,
            "--network",
            network,
            "--network-alias",
            "state",
        ]
        if persistent:
            command.extend(["-v", f"{volume}:/data"])
        command.extend(
            [
                tag,
                "--role",
                "state",
                "--host",
                "0.0.0.0",  # noqa: S104 - Docker network 내부에서만 노출
                "--port",
                str(actual_port),
                "--data-path",
                "/data/records.json",
            ]
        )
        _docker(command)

    def start_app(*, state_url: str, actual_port: int = 8080) -> int:
        remove(app_name)
        _docker(
            [
                "run",
                "-d",
                "--name",
                app_name,
                "--label",
                label,
                "--network",
                network,
                "-p",
                "127.0.0.1::8080",
                tag,
                "--role",
                "app",
                "--host",
                "0.0.0.0",  # noqa: S104 - host publish는 127.0.0.1로 제한
                "--port",
                str(actual_port),
                "--state-url",
                state_url,
            ]
        )
        binding = _docker(["port", app_name, "8080/tcp"]).stdout.strip().splitlines()[0]
        return int(binding.rsplit(":", 1)[1])

    try:
        start_state(persistent=True)
        app_port = start_app(state_url="http://state:8081")
        recorder.phase(
            "app-port-binding",
            "control",
            "published contract port에서 readiness 200",
            lambda: _status_is(app_port, "/health/ready", {200}),
        )

        app_port = start_app(state_url="http://state:8081", actual_port=8082)
        recorder.phase(
            "app-port-binding",
            "intervention",
            "Container는 실행 중이지만 contract port는 도달 불가",
            lambda: (
                _docker(["inspect", "-f", "{{.State.Running}}", app_name]).stdout.strip()
                == "true"
                and _wait_for(app_port, "/health/ready", {200}, 3)["status"] is None,
                {
                    "containerRunning": _docker(
                        ["inspect", "-f", "{{.State.Running}}", app_name]
                    ).stdout.strip(),
                    "publishedContainerPort": 8080,
                    "actualListenPort": 8082,
                },
            ),
        )
        app_port = start_app(state_url="http://state:8081")
        recorder.phase(
            "app-port-binding",
            "restoration",
            "listen port 복원 후 readiness 200",
            lambda: _status_is(app_port, "/health/ready", {200}),
        )

        # DNS 부정 응답 시간과 endpoint binding을 섞지 않도록 loopback의 닫힌
        # port를 사용한다. 잘못된 endpoint라는 조작은 같고 실패는 즉시 관측된다.
        app_port = start_app(state_url="http://127.0.0.1:9")
        recorder.phase(
            "app-state-endpoint-binding",
            "intervention",
            "잘못된 State DNS에서 readiness 503",
            lambda: _status_is(app_port, "/health/ready", {503}),
        )
        app_port = start_app(state_url="http://state:8081")
        recorder.phase(
            "app-state-endpoint-binding",
            "restoration",
            "State endpoint 복원 후 readiness 200",
            lambda: _status_is(app_port, "/health/ready", {200}),
        )

        # 주소·Container 존재 여부는 유지하고 State가 계약 port를 듣는지만
        # 제거한다. DNS 관계와 service availability를 한 개입에 섞지 않는다.
        start_state(persistent=True, actual_port=8082)
        recorder.phase(
            "state-resource-availability",
            "intervention",
            "State가 계약 port를 듣지 않으면 readiness 503과 업무 요청 502",
            lambda: (
                _wait_for(app_port, "/health/ready", {503})["status"] == 503
                and _request(app_port, "GET", "/records/probe")["status"] == 502,
                {
                    "readiness": _request(app_port, "GET", "/health/ready"),
                    "business": _request(app_port, "GET", "/records/probe"),
                },
            ),
        )
        start_state(persistent=True)
        recorder.phase(
            "state-resource-availability",
            "restoration",
            "State Container 복원 후 readiness 200",
            lambda: _status_is(app_port, "/health/ready", {200}),
        )

        recorder.phase(
            "volume-state-persistence",
            "control",
            "named Volume 경로에 기록 성공",
            lambda: (
                _request(app_port, "PUT", "/records/durable", {"value": "kept"})["status"]
                == 200,
                _request(app_port, "GET", "/records/durable"),
            ),
        )
        start_state(persistent=False)
        recorder.phase(
            "volume-state-persistence",
            "intervention",
            "Volume 없는 State Container에서는 기존 상태가 보이지 않음",
            lambda: (
                _wait_for(app_port, "/health/ready", {200})["status"] == 200
                and _request(app_port, "GET", "/records/durable")["status"] == 404,
                _request(app_port, "GET", "/records/durable"),
            ),
        )
        start_state(persistent=True)
        recorder.phase(
            "volume-state-persistence",
            "restoration",
            "named Volume 재연결 후 기존 상태 조회 성공",
            lambda: (
                _wait_for(app_port, "/health/ready", {200})["status"] == 200
                and _request(app_port, "GET", "/records/durable")["status"] == 200,
                _request(app_port, "GET", "/records/durable"),
            ),
        )
    finally:
        for container in _docker_ids("container", label):
            _docker(["rm", "-f", container], check=False)
        _docker(["network", "rm", network], check=False)
        _docker(["volume", "rm", volume], check=False)
        _docker(["image", "rm", "-f", tag], check=False)
    residual = []
    for kind in ("container", "network", "volume", "image"):
        residual.extend(f"{kind}:{identifier}" for identifier in _docker_ids(kind, label))
    return residual


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("process", "docker"), default="process")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_id = uuid.uuid4().hex[:12]
    output = args.output or (
        ROOT
        / "artifacts"
        / "measurements"
        / f"app-resource-dependency-{args.mode}-{run_id}.json"
    )
    recorder = Recorder(args.mode, run_id)
    try:
        residual = run_process(recorder) if args.mode == "process" else run_docker(recorder)
        recorder.result["residualResources"] = residual
        recorder.result["cleanupVerified"] = not residual
    except Exception as error:
        recorder.result["harnessError"] = {
            "type": type(error).__name__,
            "message": str(error)[:1000],
        }
        recorder.result["cleanupVerified"] = False
    recorder.result["finishedAt"] = _now()
    passed = sum(row["status"] == "passed" for row in recorder.result["experiments"])
    recorder.result["summary"] = {
        "passedPhases": passed,
        "totalPhases": len(recorder.result["experiments"]),
        "allPassed": passed == len(recorder.result["experiments"])
        and recorder.result["cleanupVerified"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(recorder.result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
    if not recorder.result["summary"]["allPassed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
