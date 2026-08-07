from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ImplementationSettings:
    repository_root: Path
    work_root: Path
    python_executable: Path
    max_workers: int
    model: str
    base_url: str
    command_timeout_seconds: int

    @classmethod
    def from_env(cls) -> "ImplementationSettings":
        repository_root = Path(__file__).resolve().parents[2]
        python = Path(sys.executable).resolve()
        work_root = (repository_root / ".easydep" / "implementation-runs").resolve()
        return cls(
            repository_root=repository_root,
            work_root=work_root,
            python_executable=python,
            max_workers=max(1, int(os.getenv("IMPLEMENTATION_MAX_WORKERS", "1"))),
            model=os.getenv("IMPLEMENTATION_AGENT_MODEL", "nvidia_nim/openai/gpt-oss-120b"),
            base_url=os.getenv("IMPLEMENTATION_AGENT_BASE_URL", "https://integrate.api.nvidia.com/v1"),
            command_timeout_seconds=max(
                60, int(os.getenv("IMPLEMENTATION_COMMAND_TIMEOUT_SECONDS", "3600"))
            ),
        )


# System & Infrastructure Defaults
DEFAULT_CONTAINER_PORT: int = int(os.getenv("IMPLEMENTATION_DEFAULT_CONTAINER_PORT", "8000"))
DEFAULT_DOCKER_GRADLE_IMAGE: str = os.getenv("IMPLEMENTATION_DOCKER_GRADLE_IMAGE", "gradle:8.14.2-jdk21")
DEFAULT_DOCKER_JRE_IMAGE: str = os.getenv("IMPLEMENTATION_DOCKER_JRE_IMAGE", "eclipse-temurin:21-jre-alpine")
DEFAULT_AWS_LOG_RETENTION_DAYS: int = int(os.getenv("IMPLEMENTATION_AWS_LOG_RETENTION_DAYS", "30"))
DEFAULT_AZURE_MYSQL_BACKUP_RETENTION_DAYS: int = int(os.getenv("IMPLEMENTATION_AZURE_MYSQL_RETENTION_DAYS", "7"))

