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
from app.implementation.prototype_client import PrototypeClient


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
        lines = [
            f"' implementation relation: {line}"
            if connector.search(line)
            else line
            for line in source.splitlines()
        ]
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
        job_path = Path(str(result["job_path"]))
        run_root = Path(str(result["run_root"]))
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
        workflow = self.client.run_phase(run_root, job_path, approval_path, False)
        return self._payload(job_path, run_root, workflow)

    def _payload(
        self, job_path: Path, run_root: Path, workflow: dict[str, Any]
    ) -> dict[str, Any]:
        request = self.client.transmission_request(run_root)
        status = str(workflow.get("status", "FAILED"))
        return {
            "status": "completed" if status == "COMPLETE" else "needs_approval" if request else status.lower(),
            "job_path": str(job_path),
            "run_root": str(run_root),
            "workflow": workflow,
            "transmission_request": request,
        }
