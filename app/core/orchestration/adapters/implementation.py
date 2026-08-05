"""Boundary around the member-owned implementation workflow."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.implementation.config import ImplementationSettings
from app.implementation.prototype_client import PrototypeClient, PrototypeExecutionError


class ImplementationContractError(RuntimeError):
    pass


class ImplementationAdapter:
    def __init__(self, settings: ImplementationSettings | None = None) -> None:
        self.settings = settings or ImplementationSettings.from_env()
        self.client = PrototypeClient(self.settings)

    @staticmethod
    def _normalize_bce_for_generator(source: str) -> str:
        """Hide relationships unsupported by the bundled class-to-code parser."""
        connector = re.compile(r"\s(?:-->|->|\.\.>|\*--|o--|--\|>)\s")
        untyped_field = re.compile(r"^(\s*[+#~-]\s+)([A-Za-z_]\w*)\s*$")
        method = re.compile(
            r"^(\s*[+#~-]\s+[A-Za-z_]\w*\()([^)]*)(\)(?:\s*:\s*\S+)?\s*)$"
        )
        lines = []
        for line in source.splitlines():
            if connector.search(line):
                lines.append(f"' implementation relation: {line}")
                continue
            match = untyped_field.match(line)
            if match:
                lines.append(f"{match.group(1)}{match.group(2)}: String")
                continue
            call = method.match(line)
            if call and call.group(2).strip():
                parameters = ", ".join(
                    part.strip() if ":" in part else f"{part.strip()}: String"
                    for part in call.group(2).split(",")
                )
                lines.append(f"{call.group(1)}{parameters}{call.group(3)}")
                continue
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _design_payload(
        requirements_result: dict[str, Any],
        design_result: dict[str, Any],
        cloud_design_result: dict[str, Any],
        infrastructure_recommendation: dict[str, Any],
    ) -> dict[str, Any]:
        artifacts = design_result.get("artifacts") or {}
        payload = {
            "class_diagram_puml": ImplementationAdapter._normalize_bce_for_generator(
                str(artifacts.get("class_diagram") or "")
            ),
            "sequence_diagram_puml": artifacts.get("sequence_diagram") or "",
            "api_spec": artifacts.get("api_spec") or {},
            "erd_puml": artifacts.get("erd") or "",
            "deployment_diagram_puml": cloud_design_result.get(
                "deployment_diagram_puml"
            )
            or artifacts.get("deployment_diagram")
            or "",
            "resource_spec": {
                **(requirements_result.get("resource_spec") or {}),
                "provisionalRecommendation": infrastructure_recommendation,
            },
        }
        missing = [key for key in ("class_diagram_puml", "api_spec") if not payload[key]]
        if missing:
            raise ImplementationContractError(
                "Missing required design artifacts: " + ", ".join(missing)
            )
        return payload

    @staticmethod
    def _bridge_api_key() -> None:
        """Expose the repository-wide API_KEY under the worker's legacy name."""
        if os.getenv("API_KEY") and not os.getenv("NVIDIA_API_KEY"):
            os.environ["NVIDIA_API_KEY"] = os.environ["API_KEY"]

    def _configure_gradle_memory(self) -> None:
        cache = self.settings.repository_root / ".easydep" / "gradle-cache"
        cache.mkdir(parents=True, exist_ok=True)
        heap_mb = max(128, int(os.getenv("IMPLEMENTATION_GRADLE_XMX_MB", "128")))
        (cache / "gradle.properties").write_text(
            (
                "org.gradle.daemon=false\n"
                "org.gradle.parallel=false\n"
                "org.gradle.workers.max=1\n"
                f"org.gradle.jvmargs=-Xmx{heap_mb}m -Xss256k "
                "-XX:MaxMetaspaceSize=192m -XX:+UseSerialGC\n"
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _model() -> str:
        value = os.getenv("MODEL", "openai/gpt-oss-120b")
        return value if value.startswith("nvidia_nim/") else f"nvidia_nim/{value}"

    def start(
        self,
        *,
        run_id: str,
        app_id: str,
        requirements_result: dict[str, Any],
        design_result: dict[str, Any],
        cloud_design_result: dict[str, Any],
        infrastructure_recommendation: dict[str, Any],
    ) -> dict[str, Any]:
        self._bridge_api_key()
        self._configure_gradle_memory()
        design = self._design_payload(
            requirements_result,
            design_result,
            cloud_design_result,
            infrastructure_recommendation,
        )
        job_path = self.client.prepare_job(
            f"orchestration-{run_id}", app_id, design, "com.easydep.generated", True
        )
        job = json.loads(job_path.read_text(encoding="utf-8"))
        # The member workflow performs compileJava + bootJar + test after each
        # implementation phase using the shared Gradle cache. Avoid its earlier
        # duplicate cold-cache compile, whose fixed timeout is shorter than the
        # first dependency resolution on the evaluation host.
        job["verification"]["compile"] = False
        job["agent"].update(
            {
                "model": self._model(),
                "baseUrl": os.getenv("BASE_URL", self.settings.base_url),
                "temperature": float(os.getenv("TEMPERATURE", "0")),
            }
        )
        job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        run_root, workflow = self.client.generate_and_plan(job_path)
        return self._payload(job_path, run_root, workflow)

    def resume(self, result: dict[str, Any], *, approved: bool) -> dict[str, Any]:
        if not approved:
            return {**result, "status": "rejected"}
        self._bridge_api_key()
        self._configure_gradle_memory()
        job_path = Path(str(result["job_path"]))
        run_root = Path(str(result["run_root"]))
        try:
            # Reconciliation can turn completed task results into checkpoints and
            # therefore change the exact next task set/request ID.
            self.client._call(["plan-workflow", str(run_root), str(job_path)])
            request = self.client.transmission_request(run_root)
            if request is None:
                return result
            approval_path = job_path.parent / f"approval-{uuid.uuid4().hex}.json"
            approval_path.write_text(
                json.dumps(
                    {
                        "requestId": request["requestId"],
                        "approved": True,
                        "approvedAt": datetime.now(UTC).isoformat(),
                        "approvedBy": "orchestration-user",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            workflow = self.client.run_phase(run_root, job_path, approval_path, True)
            return self._payload(job_path, run_root, workflow)
        except PrototypeExecutionError as error:
            state_path = run_root / "reports" / "workflow-state.json"
            if not state_path.is_file():
                raise
            workflow = json.loads(state_path.read_text(encoding="utf-8"))
            payload = self._payload(job_path, run_root, workflow)
            payload["execution_error"] = str(error)
            return payload

    def _payload(
        self, job_path: Path, run_root: Path, workflow: dict[str, Any]
    ) -> dict[str, Any]:
        request = self.client.transmission_request(run_root)
        status = str(workflow.get("status", "FAILED"))
        if status == "COMPLETE":
            public_status = "completed"
        elif status == "FAILED":
            public_status = "failed"
        elif request:
            public_status = "needs_approval"
        else:
            public_status = status.lower()
        return {
            "status": public_status,
            "job_path": str(job_path),
            "run_root": str(run_root),
            "workflow": workflow,
            "transmission_request": request,
        }
