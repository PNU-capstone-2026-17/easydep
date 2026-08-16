from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .frontend_scaffold import (
    FrontendScaffoldError,
    OPENAPI_GENERATOR_NAME,
    OPENAPI_GENERATOR_VERSION,
    openapi_typescript_fetch_command,
    render_package_lock,
    resolve_api_base_url,
    validate_openapi,
    write_react_scaffold,
)


CommandRunner = Callable[[str, list[str], Path], object]


@dataclass(frozen=True)
class FrontendGenerationResult:
    api_base_url: str
    generator: str
    generator_version: str

    def artifact_metadata(self, application_name: str) -> dict[str, str]:
        return {
            "generator": f"openapi-generator/{self.generator}@{self.generator_version}",
            "stage": "SCAFFOLD",
            "application_name": application_name,
            "api_base_url": self.api_base_url,
        }

    def tool_metadata(self) -> dict[str, str]:
        return {
            "generator": self.generator,
            "version": f"openapi-generator-cli/v{self.generator_version}",
        }


def generate_frontend_project(
    *,
    workspace_root: Path,
    openapi_path: Path,
    frontend_root: Path,
    api_spec: dict[str, Any],
    application_name: str,
    api_base_url: str | None,
    run_command: CommandRunner,
) -> FrontendGenerationResult:
    """Generate the client, deterministic React shell, and npm dependency lock."""
    validate_openapi(api_spec)
    effective_api_base_url = resolve_api_base_url(api_spec, api_base_url)
    generated_client = frontend_root / "src" / "generated"
    run_command(
        "openapi-generator-typescript-fetch",
        openapi_typescript_fetch_command(
            workspace_root, openapi_path, generated_client
        ),
        workspace_root,
    )
    scaffold = write_react_scaffold(
        frontend_root,
        api_spec,
        application_name=application_name,
        api_base_url=effective_api_base_url,
    )
    rendered_lock = render_package_lock(scaffold["package.json"])
    if rendered_lock is not None:
        (frontend_root / "package-lock.json").write_text(
            rendered_lock, encoding="utf-8"
        )
    else:
        npm = "npm.cmd" if os.name == "nt" else "npm"
        run_command(
            "npm-package-lock",
            [
                npm,
                "install",
                "--package-lock-only",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ],
            frontend_root,
        )
    if not (frontend_root / "package-lock.json").is_file():
        raise FrontendScaffoldError(
            "npm package-lock generation completed without package-lock.json"
        )
    return FrontendGenerationResult(
        api_base_url=effective_api_base_url,
        generator=OPENAPI_GENERATOR_NAME,
        generator_version=OPENAPI_GENERATOR_VERSION,
    )


def write_openapi_input(path: Path, api_spec: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(api_spec, ensure_ascii=False, indent=2), encoding="utf-8"
    )
