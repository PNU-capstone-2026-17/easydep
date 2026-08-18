from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any

from ..config import ImplementationSettings


_OPENAPI_PATH_PARAMETER = re.compile(r"\{([^{}]+)\}")
_OPENAPI_OPERATIONS = frozenset({"delete", "get", "head", "options", "patch", "post", "put", "trace"})


class PrototypeExecutionError(RuntimeError):
    pass


def _normalize_openapi_path_parameters(api_spec: Any) -> Any:
    """Supply missing OpenAPI path parameters required by code generators.

    Design artifacts may describe a templated endpoint without repeating its path
    parameter in every operation.  OpenAPI Generator rejects that otherwise useful
    artifact, so add a conservative string parameter only where it is absent.
    """
    if not isinstance(api_spec, dict) or not isinstance(api_spec.get("paths"), dict):
        return api_spec

    normalized = json.loads(json.dumps(api_spec))
    for path, path_item in normalized["paths"].items():
        if not isinstance(path, str) or not isinstance(path_item, dict):
            continue
        required_names = set(_OPENAPI_PATH_PARAMETER.findall(path))
        if not required_names:
            continue

        shared_parameters = path_item.get("parameters")
        shared_names = {
            parameter.get("name")
            for parameter in shared_parameters if isinstance(parameter, dict)
            and parameter.get("in") == "path"
            and isinstance(parameter.get("name"), str)
        } if isinstance(shared_parameters, list) else set()

        for operation_name, operation in path_item.items():
            if operation_name.lower() not in _OPENAPI_OPERATIONS or not isinstance(operation, dict):
                continue
            operation_parameters = operation.get("parameters")
            if not isinstance(operation_parameters, list):
                operation_parameters = []
                operation["parameters"] = operation_parameters
            declared_names = shared_names | {
                parameter.get("name")
                for parameter in operation_parameters if isinstance(parameter, dict)
                and parameter.get("in") == "path"
                and isinstance(parameter.get("name"), str)
            }
            for name in sorted(required_names - declared_names):
                operation_parameters.append(
                    {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
                )
    return normalized


class PrototypeClient:
    """Narrow subprocess boundary around the independently runnable prototype."""

    def __init__(self, settings: ImplementationSettings):
        self.settings = settings
        self._process_lock = threading.RLock()
        self._processes: dict[str, subprocess.Popen[str]] = {}

    def prepare_job(self, job_id: str, app_id: str, design: dict[str, Any], base_package: str, allow_assumptions: bool) -> Path:
        if not self.settings.python_executable.is_file():
            raise PrototypeExecutionError(
                f"Current EasyDep Python executable does not exist: {self.settings.python_executable}"
            )
        try:
            self.settings.work_root.relative_to(self.settings.repository_root)
        except ValueError as error:
            raise PrototypeExecutionError(
                "Implementation work root must be inside the EasyDep repository"
            ) from error
        root = self.settings.work_root / job_id
        context = root / "design-context"
        context.mkdir(parents=True, exist_ok=True)
        progress_path = root / "generation-progress.json"
        inputs: dict[str, str] = {}

        def write(name: str, filename: str, value: Any) -> None:
            if value in (None, "", {}):
                return
            path = context / filename
            text = json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, (dict, list)) else str(value)
            path.write_text(text, encoding="utf-8")
            inputs[name] = path.relative_to(self.settings.repository_root).as_posix()

        bce_puml = re.sub(r"\(\s*\.{3}\s*\)", "()", str(design.get("class_diagram_puml") or ""))
        write("bceClass", "class-diagram.puml", bce_puml)
        # 하나의 파일에 유스케이스별 @startuml 블록을 모두 보존한다. 구현 계획·정합성
        # 검사는 이 입력 전체를 순회하므로 모든 유스케이스 호출 흐름이 소스 생성에 반영된다.
        write("sequence", "sequence-diagrams.puml", design.get("sequence_diagram_puml"))
        write("openapi", "openapi.json", _normalize_openapi_path_parameters(design.get("api_spec")))
        write("erd", "erd.puml", design.get("erd_puml"))
        write("deployment", "deployment-diagram.puml", design.get("deployment_diagram_puml"))
        write("cloud", "resource-spec.json", design.get("resource_spec"))
        job = {
            "name": f"easydep-{app_id[:8]}",
            "workspaceRoot": str(self.settings.repository_root),
            "inputs": inputs,
            "requiredInputs": ["bceClass", "sequence", "openapi"],
            "outputRoot": (root / "generated" / "runs").relative_to(self.settings.repository_root).as_posix(),
            "generation": {"basePackage": base_package, "allowAssumptions": allow_assumptions},
            "verification": {"compile": True},
            "progressPath": progress_path.relative_to(self.settings.repository_root).as_posix(),
            "tools": {
                "puml2codeRoot": "app/implementation/tools/puml2code-bce",
            },
            "agent": {"mode": "openhands", "model": self.settings.model, "baseUrl": self.settings.base_url},
        }
        path = root / "job.json"
        path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def prepare_feedback_job(
        self,
        job_id: str,
        app_id: str,
        design: dict[str, Any],
        files: dict[str, str],
        feedback: str,
        base_package: str,
        allow_assumptions: bool,
    ) -> Path:
        path = self.prepare_job(
            job_id, app_id, design, base_package, allow_assumptions
        )
        job = json.loads(path.read_text(encoding="utf-8"))
        root = path.parent
        snapshot_path = root / "base-application.json"
        snapshot_path.write_text(
            json.dumps(
                {
                    "schemaVersion": "implementation-source-snapshot/v1alpha1",
                    "files": {
                        f"application/{name.strip('/')}": content
                        for name, content in sorted(files.items())
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        job["jobType"] = "FEEDBACK_REVISION"
        job["feedback"] = feedback
        job["inputs"]["baseSnapshot"] = snapshot_path.relative_to(
            self.settings.repository_root
        ).as_posix()
        job["requiredInputs"] = ["baseSnapshot"]
        path.write_text(
            json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    def generate(self, job_path: Path) -> Path:
        generated = self._call([str(job_path)], job_path.parent.name)
        return Path(str(generated["output"])).resolve()

    def plan_workflow(self, run_root: Path, job_path: Path) -> dict[str, Any]:
        return self._call(
            ["plan-workflow", str(run_root), str(job_path)], job_path.parent.name
        )

    def generate_and_plan(self, job_path: Path) -> tuple[Path, dict[str, Any]]:
        """Compatibility helper for callers outside the web-worker boundary."""
        run_root = self.generate(job_path)
        return run_root, self.plan_workflow(run_root, job_path)

    def run_phase(self, run_root: Path, job_path: Path, approval_path: Path, retry_failed: bool) -> dict[str, Any]:
        args = ["run-workflow", str(run_root), str(job_path), "--approval", str(approval_path)]
        if retry_failed:
            args.append("--retry-failed")
        return self._call(args, job_path.parent.name)

    def transmission_request(self, run_root: Path) -> dict[str, Any] | None:
        path = run_root / "reports" / "external-transmission-request.json"
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if value.get("status") == "AWAITING_APPROVAL" else None

    def warmup_runtime(self) -> dict[str, Any]:
        """Preload tools and shared dependency caches before the first job."""
        from ..generation.warmup import warmup_implementation_runtime

        return warmup_implementation_runtime(
            self.settings.repository_root,
            self.settings.command_timeout_seconds,
        )

    def cancel(self, job_id: str) -> bool:
        with self._process_lock:
            process = self._processes.get(job_id)
        if process is None or process.poll() is not None:
            return False
        self._terminate_process_tree(process)
        return True

    def _call(
        self, args: list[str], operation_id: str | None = None
    ) -> dict[str, Any]:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env.setdefault(
            "GRADLE_USER_HOME",
            str(self.settings.repository_root / ".easydep" / "gradle-cache"),
        )
        process: subprocess.Popen[str] | None = None
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        )
        try:
            process = subprocess.Popen(
                [str(self.settings.python_executable), "-B", "-m", "app.implementation.interfaces.cli", *args],
                cwd=self.settings.repository_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
            if operation_id:
                with self._process_lock:
                    self._processes[operation_id] = process
            stdout, stderr = process.communicate(
                timeout=self.settings.command_timeout_seconds
            )
        except subprocess.TimeoutExpired as error:
            if process is not None:
                self._terminate_process_tree(process)
            raise PrototypeExecutionError(
                f"Implementation prototype exceeded {self.settings.command_timeout_seconds} seconds"
            ) from error
        finally:
            if operation_id and process is not None:
                with self._process_lock:
                    if self._processes.get(operation_id) is process:
                        self._processes.pop(operation_id, None)
        if process.returncode != 0:
            evidence = (stderr or stdout)[-4000:]
            for line in reversed(stdout.splitlines()):
                try:
                    failed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                output = failed.get("output") if isinstance(failed, dict) else None
                manifest = Path(str(output)) / "reports" / "run-manifest.json" if output else None
                if manifest and manifest.is_file():
                    report = json.loads(manifest.read_text(encoding="utf-8"))
                    messages = [
                        str(item.get("message"))
                        for item in report.get("diagnostics", [])
                        if item.get("severity") == "ERROR"
                    ]
                    if messages:
                        evidence = "; ".join(messages)[-4000:]
                break
            raise PrototypeExecutionError(f"Implementation prototype exited with {process.returncode}: {evidence}")
        for line in reversed(stdout.splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise PrototypeExecutionError("Implementation prototype returned no JSON result")

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            else:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            process.kill()
        finally:
            try:
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
