"""Versioned contracts for the four-stage cross-agent workflow."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Protocol, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class StageName(StrEnum):
    REQUIREMENTS = "requirements"
    DESIGN = "design"
    IMPLEMENTATION = "implementation"
    TESTING = "testing"
    COMPLETED = "completed"


class StepStatus(StrEnum):
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProviderKind(StrEnum):
    MEMBER = "member"
    LLM = "llm"
    BUILTIN = "builtin"


class RunMode(StrEnum):
    INTERACTIVE = "interactive"
    BATCH = "batch"


class ProviderConfig(BaseModel):
    """Explicit implementation choice for every replaceable substep."""

    model_config = ConfigDict(extra="forbid")

    requirements_analysis: ProviderKind = ProviderKind.MEMBER
    design_architecture: ProviderKind = ProviderKind.MEMBER
    design_cloud_enrichment: ProviderKind = ProviderKind.BUILTIN
    implementation_scaffold: ProviderKind = ProviderKind.MEMBER
    implementation_acceptance_tests: ProviderKind = ProviderKind.LLM
    implementation_logic: ProviderKind = ProviderKind.LLM
    implementation_vm_selection: ProviderKind = ProviderKind.BUILTIN
    implementation_vm_delivery: ProviderKind = ProviderKind.LLM
    testing_application: ProviderKind = ProviderKind.BUILTIN


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements: list[str] = Field(min_length=1)
    resource_constraints_text: str = ""
    app_id: str | None = None
    run_id: str | None = None
    variant: str = "full"
    case_id: str = "adhoc"
    purpose: str = "normal"
    mode: RunMode = RunMode.INTERACTIVE
    providers: ProviderConfig = Field(default_factory=ProviderConfig)


class Diagnostic(BaseModel):
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "error"


class StepResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "easydep-step-result/v1"
    step: str
    provider: ProviderKind
    status: StepStatus
    output: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    prompt: Any | None = None


class StageOutput(BaseModel):
    schema_version: str
    data: dict[str, Any] = Field(default_factory=dict)
    steps: list[StepResult] = Field(default_factory=list)


class RequirementsOutput(StageOutput):
    schema_version: str = "easydep-requirements/v1"


class DesignOutput(StageOutput):
    schema_version: str = "easydep-design/v1"


class ImplementationOutput(StageOutput):
    schema_version: str = "easydep-implementation/v1"


class TestingOutput(StageOutput):
    schema_version: str = "easydep-testing/v1"


class StepContext(BaseModel):
    run_id: str
    app_id: str
    mode: RunMode
    response: Any | None = None
    requirement_revision: int = 0
    checkpoint_retry_attempt: int = 0


class StepProvider(Protocol):
    def run(self, payload: dict[str, Any], context: StepContext) -> StepResult: ...


class OrchestrationState(TypedDict, total=False):
    request: dict[str, Any]
    run_id: str
    app_id: str
    current_stage: str
    status: str
    response: Any
    requirements: dict[str, Any]
    design: dict[str, Any]
    implementation: dict[str, Any]
    testing: dict[str, Any]
    error: str
    retryHistory: list[dict[str, Any]]
    requirementRevisionHistory: list[dict[str, Any]]


class RunResult(BaseModel):
    run_id: str
    app_id: str
    stage: StageName
    status: StepStatus
    prompt: Any | None = None
    state: dict[str, Any]
