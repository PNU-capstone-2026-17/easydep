from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: str
    message: str
    source: str | None = None


@dataclass
class CommandEvidence:
    name: str
    command: list[str]
    cwd: str
    exit_code: int
    duration_ms: int
    stdout: str
    stderr: str


@dataclass
class JobSpec:
    name: str
    workspace_root: Path
    inputs: dict[str, Path]
    required_inputs: list[str]
    base_package: str
    allow_assumptions: bool
    verify_compile: bool
    output_root: Path
    puml2code_root: Path
    openapi_generator_jar: Path
    agent_mode: str
    agent_model: str
    agent_base_url: str
    agent_temperature: float
    agent_top_p: float
    agent_max_output_tokens: int
    agent_reasoning_budget: int


@dataclass
class RunManifest:
    schema_version: str = "easydep-implementation-agent/v1alpha1"
    job_name: str = ""
    status: str = "RECEIVED"
    input_hash: str = ""
    inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    tools: dict[str, dict[str, str]] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    commands: list[CommandEvidence] = field(default_factory=list)
    generated_files: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    implementation_tasks: list[dict[str, Any]] = field(default_factory=list)
    agent_execution: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
