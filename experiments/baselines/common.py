"""Shared input, output, and reproducibility helpers for all baselines."""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT_ROOT = ROOT / "artifacts" / "baselines"
@dataclass(frozen=True)
class ExperimentCase:
    case_id: str
    requirements: list[str]
    cloud_constraints: str
    scope: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> ExperimentCase:
        raw = json.loads(path.read_text(encoding="utf-8"))
        required = {"caseId", "requirements", "cloudConstraints", "scope"}
        missing = sorted(required - raw.keys())
        if missing:
            raise ValueError(f"case fields missing: {', '.join(missing)}")
        if not isinstance(raw["requirements"], list) or not raw["requirements"]:
            raise ValueError("requirements must be a non-empty list")
        return cls(
            case_id=str(raw["caseId"]),
            requirements=[str(item) for item in raw["requirements"]],
            cloud_constraints=str(raw["cloudConstraints"]),
            scope=dict(raw["scope"]),
        )

    def prompt(self) -> str:
        requirements = "\n".join(f"R{i}: {text}" for i, text in enumerate(self.requirements, 1))
        return (
            f"[CASE]\n{self.case_id}\n\n[SOFTWARE REQUIREMENTS]\n{requirements}\n\n"
            f"[CLOUD CONSTRAINTS]\n{self.cloud_constraints}\n\n"
            f"[FIXED SCOPE]\n{json.dumps(self.scope, ensure_ascii=False)}"
        )


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "run"


def begin_run(method: str, case: ExperimentCase, output_root: Path | None = None) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    path = (output_root or DEFAULT_ARTIFACT_ROOT) / method / safe_name(case.case_id) / stamp
    path.mkdir(parents=True, exist_ok=False)
    return path


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def git_revision() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def run_manifest(method: str, case: ExperimentCase, command: list[str]) -> dict[str, Any]:
    return {
        "method": method,
        "caseId": case.case_id,
        "startedAt": datetime.now(UTC).isoformat(),
        "gitRevision": git_revision(),
        "model": os.getenv("MODEL", "openai/gpt-oss-120b"),
        "baseUrl": os.getenv("BASE_URL", "https://integrate.api.nvidia.com/v1"),
        "temperature": float(os.getenv("BASELINE_TEMPERATURE", "0")),
        "seed": int(os.getenv("BASELINE_SEED", "42")),
        "python": platform.python_version(),
        "command": command,
        "webSearch": False,
    }


def require_api_key() -> None:
    if not os.getenv("API_KEY"):
        raise RuntimeError("API_KEY is required; load .env or set it in the environment")
