from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from app.config import settings


@dataclass(frozen=True)
class ImplementationSettings:
    repository_root: Path
    work_root: Path
    python_executable: Path
    max_workers: int
    command_timeout_seconds: int
    startup_warmup: bool = False

    @classmethod
    def from_env(cls) -> ImplementationSettings:
        repository_root = Path(__file__).resolve().parents[2]
        python = Path(sys.executable).resolve()
        work_root = (repository_root / ".easydep" / "implementation-runs").resolve()
        return cls(
            repository_root=repository_root,
            work_root=work_root,
            python_executable=python,
            max_workers=max(1, settings.implementation_max_workers),
            command_timeout_seconds=max(
                60, settings.implementation_command_timeout_seconds
            ),
            startup_warmup=settings.implementation_startup_warmup,
        )


# System & Infrastructure Defaults
DEFAULT_CONTAINER_PORT: int = settings.implementation_default_container_port
DEFAULT_DOCKER_GRADLE_IMAGE: str = settings.implementation_docker_gradle_image
DEFAULT_DOCKER_JRE_IMAGE: str = settings.implementation_docker_jre_image
DEFAULT_AWS_LOG_RETENTION_DAYS: int = settings.implementation_aws_log_retention_days
DEFAULT_AZURE_MYSQL_BACKUP_RETENTION_DAYS: int = settings.implementation_azure_mysql_retention_days

