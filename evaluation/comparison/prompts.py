"""비교군에 전달할 요구사항과 공통 산출물 계약을 결정적으로 생성한다."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import ArmSpec, Manifest


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_task_input(manifest: Manifest) -> str:
    preamble = (
        manifest.prompt_protocol.task_preamble
        if manifest.prompt_protocol is not None
        else "Complete the following software development task."
    )
    lines = [preamble, "", "Requirements:"]
    lines.extend(f"- [{item.id}] {item.text}" for item in manifest.requirements)
    if manifest.constraints:
        lines.extend(["", "Constraints:"])
        lines.extend(f"- [{item.id}] {item.text}" for item in manifest.constraints)
    return "\n".join(lines).rstrip() + "\n"


def render_artifact_contract(manifest: Manifest) -> str:
    protocol = manifest.prompt_protocol
    if protocol is None:
        return ""
    lines = [
        protocol.artifact_contract_preamble,
        "",
        "Required deliverables:",
    ]
    lines.extend(
        f"- [{artifact.id}] {artifact.title}: {artifact.description}"
        for artifact in protocol.artifact_contract
    )
    lines.extend(
        [
            "",
            "Keep each deliverable in a file that can be inspected after the run.",
            "Use the framework's native notation when necessary; do not omit semantic content "
            "only because its default file format differs.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_arm_prompt(manifest: Manifest, arm: ArmSpec) -> str:
    task = render_task_input(manifest)
    if arm.prompt_profile == "requirementsOnly":
        return task
    contract = render_artifact_contract(manifest)
    return f"{task.rstrip()}\n\n{contract}"


def materialize_prompt_files(
    manifest: Manifest,
    arm: ArmSpec,
    run_directory: Path,
) -> dict[str, Any]:
    task = render_task_input(manifest)
    contract = render_artifact_contract(manifest)
    prompt = render_arm_prompt(manifest, arm)
    task_path = run_directory / "task-input.txt"
    contract_path = run_directory / "common-artifact-contract.txt"
    prompt_path = run_directory / "arm-prompt.txt"
    metadata_path = run_directory / "prompt-metadata.json"
    task_path.write_text(task, encoding="utf-8")
    contract_path.write_text(contract, encoding="utf-8")
    prompt_path.write_text(prompt, encoding="utf-8")
    metadata = {
        "promptProfile": arm.prompt_profile,
        "taskInputSha256": _sha256(task),
        "artifactContractSha256": _sha256(contract) if contract else None,
        "armPromptSha256": _sha256(prompt),
        "taskInputPath": str(task_path),
        "artifactContractPath": str(contract_path),
        "armPromptPath": str(prompt_path),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {**metadata, "metadataPath": str(metadata_path)}
