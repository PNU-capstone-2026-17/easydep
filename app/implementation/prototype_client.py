from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .config import ImplementationSettings


class PrototypeExecutionError(RuntimeError):
    pass


class PrototypeClient:
    """Narrow subprocess boundary around the independently runnable prototype."""

    def __init__(self, settings: ImplementationSettings):
        self.settings = settings

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
        inputs: dict[str, str] = {}

        def write(name: str, filename: str, value: Any) -> None:
            if value in (None, "", {}):
                return
            path = context / filename
            text = json.dumps(value, ensure_ascii=False, indent=2) if isinstance(value, (dict, list)) else str(value)
            path.write_text(text, encoding="utf-8")
            inputs[name] = path.relative_to(self.settings.repository_root).as_posix()

        write("bceClass", "class-diagram.puml", design.get("class_diagram_puml"))
        write("sequence", "sequence-diagram.puml", design.get("sequence_diagram_puml"))
        write("openapi", "openapi.json", design.get("api_spec"))
        write("erd", "erd.puml", design.get("erd_puml"))
        write("deployment", "deployment-diagram.puml", design.get("deployment_diagram_puml"))
        write("cloud", "resource-spec.json", design.get("resource_spec"))
        write("deploymentIntent", "deployment-intent.json", design.get("deployment_intent"))
        job = {
            "name": f"easydep-{app_id[:8]}",
            "workspaceRoot": str(self.settings.repository_root),
            "inputs": inputs,
            "requiredInputs": ["bceClass", "openapi"],
            "outputRoot": (root / "generated" / "runs").relative_to(self.settings.repository_root).as_posix(),
            "generation": {"basePackage": base_package, "allowAssumptions": allow_assumptions},
            "verification": {"compile": True},
            "tools": {
                "puml2codeRoot": "app/implementation/tools/puml2code-bce",
                "openapiGeneratorJar": "app/implementation/tools/openapi-generator/openapi-generator-cli-7.24.0.jar",
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

    def generate_and_plan(self, job_path: Path) -> tuple[Path, dict[str, Any]]:
        generated = self._call([str(job_path)])
        run_root = Path(str(generated["output"])).resolve()
        return run_root, self._call(["plan-workflow", str(run_root), str(job_path)])

    def run_phase(self, run_root: Path, job_path: Path, approval_path: Path, retry_failed: bool) -> dict[str, Any]:
        args = ["run-workflow", str(run_root), str(job_path), "--approval", str(approval_path)]
        if retry_failed:
            args.append("--retry-failed")
        return self._call(args)

    def transmission_request(self, run_root: Path) -> dict[str, Any] | None:
        path = run_root / "reports" / "external-transmission-request.json"
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if value.get("status") == "AWAITING_APPROVAL" else None

    def _call(self, args: list[str]) -> dict[str, Any]:
        env = os.environ.copy()
        env.setdefault(
            "GRADLE_USER_HOME",
            str(self.settings.repository_root / ".easydep" / "gradle-cache"),
        )
        try:
            result = subprocess.run(
                [str(self.settings.python_executable), "-B", "-m", "app.implementation.engine.cli", *args],
                cwd=self.settings.repository_root,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.settings.command_timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise PrototypeExecutionError(
                f"Implementation prototype exceeded {self.settings.command_timeout_seconds} seconds"
            ) from error
        if result.returncode != 0:
            evidence = (result.stderr or result.stdout)[-4000:]
            for line in reversed(result.stdout.splitlines()):
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
            raise PrototypeExecutionError(f"Implementation prototype exited with {result.returncode}: {evidence}")
        for line in reversed(result.stdout.splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise PrototypeExecutionError("Implementation prototype returned no JSON result")
