"""비교 실험의 입력 계약과 검증."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA = "easydep-comparison-manifest/v1"
SUBJECT_RESULT_SCHEMA = "easydep-comparison-subject-result/v1"
PROMPT_PROFILES = {"requirementsOnly", "commonArtifacts"}


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label}은 JSON 객체여야 합니다.")
    return value


def _non_empty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}은 비어 있지 않은 문자열이어야 합니다.")
    return value.strip()


def _string_list(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label}은 문자열 배열이어야 합니다.")
    return tuple(value)


@dataclass(frozen=True)
class RequirementSpec:
    id: str
    text: str
    verification_gates: tuple[str, ...]
    evidence_stages: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConstraintSpec:
    id: str
    text: str
    verification_gates: tuple[str, ...]


@dataclass(frozen=True)
class GateSpec:
    id: str
    kind: str
    config: dict[str, Any]
    required: bool = True


@dataclass(frozen=True)
class ArtifactSpec:
    id: str
    title: str
    description: str


@dataclass(frozen=True)
class PromptProtocol:
    task_preamble: str
    artifact_contract_preamble: str
    artifact_contract: tuple[ArtifactSpec, ...]


@dataclass(frozen=True)
class ArmSpec:
    id: str
    framework: str
    framework_version: str
    command: tuple[str, ...]
    result_path: str = "subject-result.json"
    cwd: str = "{repository}"
    timeout_seconds: float = 7200.0
    env: dict[str, str] = field(default_factory=dict)
    prompt_profile: str = "requirementsOnly"


@dataclass(frozen=True)
class Manifest:
    path: Path
    experiment_id: str
    repetitions: int
    output_root: str
    requirements: tuple[RequirementSpec, ...]
    constraints: tuple[ConstraintSpec, ...]
    gates: tuple[GateSpec, ...]
    arms: tuple[ArmSpec, ...]
    prompt_protocol: PromptProtocol | None = None

    @property
    def directory(self) -> Path:
        return self.path.parent


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    llm_calls: int | None = None
    missing_usage_calls: int = 0
    source: str = "not-reported"

    def as_dict(self) -> dict[str, Any]:
        return {
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.total_tokens,
            "llmCalls": self.llm_calls,
            "missingUsageCalls": self.missing_usage_calls,
            "source": self.source,
        }


@dataclass(frozen=True)
class SubjectResult:
    framework: str
    framework_version: str
    status: str
    workspace: Path
    usage: TokenUsage
    requirement_evidence: dict[str, dict[str, tuple[str, ...]]]
    artifact_evidence: dict[str, tuple[str, ...]]
    metadata: dict[str, Any]


def _unique_ids(items: list[Any], label: str) -> None:
    ids = [item.id for item in items]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        raise ValueError(f"{label} ID가 중복됩니다: {', '.join(duplicates)}")


def load_manifest(path: str | Path) -> Manifest:
    source = Path(path).resolve()
    data = _object(json.loads(source.read_text(encoding="utf-8")), "manifest")
    if data.get("schemaVersion") != MANIFEST_SCHEMA:
        raise ValueError(f"schemaVersion은 {MANIFEST_SCHEMA!r}이어야 합니다.")

    requirements: list[RequirementSpec] = []
    for index, raw in enumerate(data.get("requirements", [])):
        item = _object(raw, f"requirements[{index}]")
        requirements.append(
            RequirementSpec(
                id=_non_empty_string(item.get("id"), f"requirements[{index}].id"),
                text=_non_empty_string(item.get("text"), f"requirements[{index}].text"),
                verification_gates=_string_list(
                    item.get("verificationGates", []),
                    f"requirements[{index}].verificationGates",
                ),
                evidence_stages=_string_list(
                    item.get("evidenceStages", []),
                    f"requirements[{index}].evidenceStages",
                ),
            )
        )

    constraints: list[ConstraintSpec] = []
    for index, raw in enumerate(data.get("constraints", [])):
        item = _object(raw, f"constraints[{index}]")
        constraints.append(
            ConstraintSpec(
                id=_non_empty_string(item.get("id"), f"constraints[{index}].id"),
                text=_non_empty_string(item.get("text"), f"constraints[{index}].text"),
                verification_gates=_string_list(
                    item.get("verificationGates", []),
                    f"constraints[{index}].verificationGates",
                ),
            )
        )

    gates: list[GateSpec] = []
    for index, raw in enumerate(data.get("gates", [])):
        item = _object(raw, f"gates[{index}]")
        gates.append(
            GateSpec(
                id=_non_empty_string(item.get("id"), f"gates[{index}].id"),
                kind=_non_empty_string(item.get("kind"), f"gates[{index}].kind"),
                required=bool(item.get("required", True)),
                config={
                    key: value
                    for key, value in item.items()
                    if key not in {"id", "kind", "required"}
                },
            )
        )

    prompt_protocol: PromptProtocol | None = None
    raw_prompt_protocol = data.get("promptProtocol")
    if raw_prompt_protocol is not None:
        prompt = _object(raw_prompt_protocol, "promptProtocol")
        raw_artifacts = prompt.get("artifactContract", [])
        if not isinstance(raw_artifacts, list) or not raw_artifacts:
            raise ValueError("promptProtocol.artifactContract에는 하나 이상의 산출물이 필요합니다.")
        artifacts: list[ArtifactSpec] = []
        for index, raw in enumerate(raw_artifacts):
            item = _object(raw, f"promptProtocol.artifactContract[{index}]")
            artifacts.append(
                ArtifactSpec(
                    id=_non_empty_string(
                        item.get("id"), f"promptProtocol.artifactContract[{index}].id"
                    ),
                    title=_non_empty_string(
                        item.get("title"), f"promptProtocol.artifactContract[{index}].title"
                    ),
                    description=_non_empty_string(
                        item.get("description"),
                        f"promptProtocol.artifactContract[{index}].description",
                    ),
                )
            )
        _unique_ids(artifacts, "공통 산출물")
        prompt_protocol = PromptProtocol(
            task_preamble=str(
                prompt.get(
                    "taskPreamble",
                    "Develop a complete, executable application for the following requirements.",
                )
            ).strip(),
            artifact_contract_preamble=str(
                prompt.get(
                    "artifactContractPreamble",
                    "Produce the following design, implementation, test, and deployment deliverables.",
                )
            ).strip(),
            artifact_contract=tuple(artifacts),
        )
        if not prompt_protocol.task_preamble or not prompt_protocol.artifact_contract_preamble:
            raise ValueError("promptProtocol의 preamble은 비어 있을 수 없습니다.")

    arms: list[ArmSpec] = []
    raw_arms = data.get("arms", [])
    if not isinstance(raw_arms, list) or not raw_arms:
        raise ValueError("arms에는 하나 이상의 비교 대상이 필요합니다.")
    for index, raw in enumerate(raw_arms):
        item = _object(raw, f"arms[{index}]")
        timeout = float(item.get("timeoutSeconds", 7200))
        if timeout <= 0:
            raise ValueError(f"arms[{index}].timeoutSeconds는 0보다 커야 합니다.")
        raw_env = _object(item.get("env", {}), f"arms[{index}].env")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in raw_env.items()):
            raise ValueError(f"arms[{index}].env의 키와 값은 문자열이어야 합니다.")
        command = _string_list(item.get("command"), f"arms[{index}].command")
        if not command:
            raise ValueError(f"arms[{index}].command는 비어 있을 수 없습니다.")
        prompt_profile = str(item.get("promptProfile", "requirementsOnly"))
        if prompt_profile not in PROMPT_PROFILES:
            raise ValueError(
                f"arms[{index}].promptProfile은 requirementsOnly 또는 commonArtifacts여야 합니다."
            )
        if prompt_profile == "commonArtifacts" and prompt_protocol is None:
            raise ValueError(
                f"arms[{index}]가 commonArtifacts를 사용하지만 promptProtocol이 없습니다."
            )
        arms.append(
            ArmSpec(
                id=_non_empty_string(item.get("id"), f"arms[{index}].id"),
                framework=_non_empty_string(item.get("framework"), f"arms[{index}].framework"),
                framework_version=str(item.get("frameworkVersion", "unknown")),
                command=command,
                result_path=str(item.get("resultPath", "subject-result.json")),
                cwd=str(item.get("cwd", "{repository}")),
                timeout_seconds=timeout,
                env=dict(raw_env),
                prompt_profile=prompt_profile,
            )
        )

    _unique_ids(requirements, "요구사항")
    _unique_ids(constraints, "제약조건")
    _unique_ids(gates, "게이트")
    _unique_ids(arms, "비교 대상")
    gate_ids = {gate.id for gate in gates}
    for item in [*requirements, *constraints]:
        referenced_gate_ids = {reference.split("#", 1)[0] for reference in item.verification_gates}
        unknown = sorted(referenced_gate_ids - gate_ids)
        if unknown:
            raise ValueError(f"{item.id}가 존재하지 않는 게이트를 참조합니다: {', '.join(unknown)}")

    repetitions = data.get("repetitions", 1)
    if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions <= 0:
        raise ValueError("repetitions는 1 이상의 정수여야 합니다.")
    return Manifest(
        path=source,
        experiment_id=_non_empty_string(data.get("experimentId"), "experimentId"),
        repetitions=repetitions,
        output_root=str(data.get("outputRoot", "artifacts/comparison")),
        requirements=tuple(requirements),
        constraints=tuple(constraints),
        gates=tuple(gates),
        arms=tuple(arms),
        prompt_protocol=prompt_protocol,
    )


def _optional_non_negative_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label}은 0 이상의 정수 또는 null이어야 합니다.")
    return value


def load_subject_result(path: str | Path, *, run_directory: Path) -> SubjectResult:
    source = Path(path)
    data = _object(json.loads(source.read_text(encoding="utf-8")), "subject result")
    if data.get("schemaVersion") != SUBJECT_RESULT_SCHEMA:
        raise ValueError(f"subject result schemaVersion은 {SUBJECT_RESULT_SCHEMA!r}이어야 합니다.")
    status = str(data.get("status", ""))
    if status not in {"completed", "failed", "timeout"}:
        raise ValueError("subject result status는 completed, failed, timeout 중 하나여야 합니다.")
    raw_workspace = Path(_non_empty_string(data.get("workspace"), "workspace"))
    workspace = raw_workspace if raw_workspace.is_absolute() else (run_directory / raw_workspace).resolve()
    raw_usage = _object(data.get("usage", {}), "usage")
    input_tokens = _optional_non_negative_int(raw_usage.get("inputTokens"), "usage.inputTokens")
    output_tokens = _optional_non_negative_int(raw_usage.get("outputTokens"), "usage.outputTokens")
    total_tokens = _optional_non_negative_int(raw_usage.get("totalTokens"), "usage.totalTokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    usage = TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        llm_calls=_optional_non_negative_int(raw_usage.get("llmCalls"), "usage.llmCalls"),
        missing_usage_calls=_optional_non_negative_int(raw_usage.get("missingUsageCalls", 0), "usage.missingUsageCalls") or 0,
        source=str(raw_usage.get("source", "not-reported")),
    )
    raw_evidence = _object(data.get("requirementEvidence", {}), "requirementEvidence")
    evidence: dict[str, dict[str, tuple[str, ...]]] = {}
    for requirement_id, raw_stages in raw_evidence.items():
        stages = _object(raw_stages, f"requirementEvidence.{requirement_id}")
        evidence[str(requirement_id)] = {
            str(stage): _string_list(paths, f"requirementEvidence.{requirement_id}.{stage}")
            for stage, paths in stages.items()
        }
    raw_artifact_evidence = _object(data.get("artifactEvidence", {}), "artifactEvidence")
    artifact_evidence = {
        str(artifact_id): _string_list(paths, f"artifactEvidence.{artifact_id}")
        for artifact_id, paths in raw_artifact_evidence.items()
    }
    return SubjectResult(
        framework=_non_empty_string(data.get("framework"), "framework"),
        framework_version=str(data.get("frameworkVersion", "unknown")),
        status=status,
        workspace=workspace,
        usage=usage,
        requirement_evidence=evidence,
        artifact_evidence=artifact_evidence,
        metadata=_object(data.get("metadata", {}), "metadata"),
    )
