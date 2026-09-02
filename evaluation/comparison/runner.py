"""비교 대상 실행, 검증 게이트 적용, 결과 보존."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from .evaluate import evaluate_run
from .models import ArmSpec, Manifest, SubjectResult, TokenUsage, load_subject_result
from .oracle import run_http_oracle
from .prompts import materialize_prompt_files


def _render(value: str, variables: dict[str, Any]) -> str:
    try:
        return value.format_map({key: str(item) for key, item in variables.items()})
    except KeyError as error:
        raise ValueError(f"알 수 없는 템플릿 변수입니다: {error.args[0]}") from error


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    started = time.monotonic()
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    try:
        process = subprocess.Popen(  # noqa: S603 - shell 없이 manifest 배열을 그대로 실행한다.
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
    except OSError as error:
        return {
            "exitCode": None,
            "timedOut": False,
            "wallSeconds": round(time.monotonic() - started, 3),
            "stdout": "",
            "stderr": f"{type(error).__name__}: {error}",
        }
    timed_out = False
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "nt":
            subprocess.run(  # noqa: S603 - 방금 시작한 PID의 자식만 종료한다.
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            process.kill()
        stdout, stderr = process.communicate()
    return {
        "exitCode": process.returncode,
        "timedOut": timed_out,
        "wallSeconds": round(time.monotonic() - started, 3),
        "stdout": stdout,
        "stderr": stderr,
    }


def _subject_failure(arm: ArmSpec, run_directory: Path, status: str) -> SubjectResult:
    return SubjectResult(
        framework=arm.framework,
        framework_version=arm.framework_version,
        status=status,
        workspace=run_directory,
        usage=TokenUsage(),
        requirement_evidence={},
        artifact_evidence={},
        metadata={},
    )


def _gate_variables(base: dict[str, Any], subject: SubjectResult) -> dict[str, Any]:
    variables = {**base, "workspace": subject.workspace}
    for key, value in subject.metadata.items():
        if isinstance(value, (str, int, float)):
            variables[key] = value
    if "baseUrl" in subject.metadata:
        variables["base_url"] = subject.metadata["baseUrl"]
    return variables


def _run_gate(gate: Any, variables: dict[str, Any], subject: SubjectResult, manifest: Manifest) -> dict[str, Any]:
    base = {"id": gate.id, "kind": gate.kind, "required": gate.required}
    if subject.status != "completed":
        return {**base, "status": "not_run", "reason": f"subject status={subject.status}"}
    if gate.kind == "fileExists":
        raw_paths = gate.config.get("paths", [])
        if not raw_paths:
            return {**base, "status": "failed", "reason": "paths가 비어 있습니다."}
        paths = [Path(_render(str(path), variables)) for path in raw_paths]
        missing = [str(path) for path in paths if not path.exists()]
        return {**base, "status": "passed" if not missing else "failed", "checkedPaths": [str(path) for path in paths], "missingPaths": missing}
    if gate.kind == "command":
        command = [_render(str(item), variables) for item in gate.config.get("command", [])]
        if not command:
            return {**base, "status": "failed", "reason": "command가 비어 있습니다."}
        cwd = Path(_render(str(gate.config.get("cwd", "{workspace}")), variables))
        if not cwd.is_dir():
            return {**base, "status": "failed", "reason": f"cwd가 없습니다: {cwd}"}
        result = _run_command(
            command,
            cwd=cwd,
            env=os.environ.copy(),
            timeout_seconds=float(gate.config.get("timeoutSeconds", 600)),
        )
        expected = gate.config.get("expectedExitCodes", [0])
        passed = not result["timedOut"] and result["exitCode"] in expected
        return {**base, "status": "passed" if passed else "failed", **result}
    if gate.kind == "httpOracle":
        oracle_path = Path(_render(str(gate.config.get("oraclePath", "")), variables))
        if not oracle_path.is_absolute():
            oracle_path = (manifest.directory / oracle_path).resolve()
        base_url = _render(str(gate.config.get("baseUrl", "{base_url}")), variables)
        oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
        return {**base, **run_http_oracle(oracle, base_url)}
    return {**base, "status": "failed", "reason": f"지원하지 않는 gate kind: {gate.kind}"}


def _safe_run_gate(
    gate: Any,
    variables: dict[str, Any],
    subject: SubjectResult,
    manifest: Manifest,
) -> dict[str, Any]:
    try:
        return _run_gate(gate, variables, subject, manifest)
    except Exception as error:
        return {
            "id": gate.id,
            "kind": gate.kind,
            "required": gate.required,
            "status": "failed",
            "reason": f"{type(error).__name__}: {error}",
        }


def run_experiment(manifest: Manifest, *, output_root: Path | None = None) -> dict[str, Any]:
    repository = Path.cwd().resolve()
    root = output_root or Path(manifest.output_root)
    if not root.is_absolute():
        root = (repository / root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    runs: list[dict[str, Any]] = []
    for arm in manifest.arms:
        for repetition in range(1, manifest.repetitions + 1):
            run_directory = root / manifest.experiment_id / arm.id / f"run-{repetition:03d}"
            run_directory.mkdir(parents=True, exist_ok=True)
            prompt_metadata = materialize_prompt_files(
                manifest, arm, run_directory
            )
            variables = {
                "repository": repository,
                "manifest_dir": manifest.directory,
                "run_dir": run_directory,
                "arm_id": arm.id,
                "experiment_id": manifest.experiment_id,
                "repetition": repetition,
                "python": sys.executable,
                "task_input_file": prompt_metadata["taskInputPath"],
                "artifact_contract_file": prompt_metadata["artifactContractPath"],
                "prompt_file": prompt_metadata["armPromptPath"],
                "prompt_metadata_file": prompt_metadata["metadataPath"],
                "prompt_profile": prompt_metadata["promptProfile"],
                "prompt_sha256": prompt_metadata["armPromptSha256"],
            }
            command = [_render(item, variables) for item in arm.command]
            cwd = Path(_render(arm.cwd, variables))
            env = os.environ.copy()
            env.update({key: _render(value, variables) for key, value in arm.env.items()})
            started = time.monotonic()
            if not cwd.is_dir():
                execution = {"exitCode": None, "timedOut": False, "wallSeconds": 0.0, "stdout": "", "stderr": f"cwd가 없습니다: {cwd}"}
            else:
                execution = _run_command(command, cwd=cwd, env=env, timeout_seconds=arm.timeout_seconds)
            (run_directory / "stdout.log").write_text(execution["stdout"], encoding="utf-8")
            (run_directory / "stderr.log").write_text(execution["stderr"], encoding="utf-8")
            result_path = Path(_render(arm.result_path, variables))
            if not result_path.is_absolute():
                result_path = run_directory / result_path
            if result_path.is_file():
                try:
                    subject = load_subject_result(result_path, run_directory=run_directory)
                    load_error = None
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    subject = _subject_failure(arm, run_directory, "failed")
                    load_error = f"결과 파일 오류: {error}"
            else:
                subject = _subject_failure(arm, run_directory, "failed")
                load_error = f"결과 파일 없음: {result_path}"
            if execution["timedOut"]:
                subject = replace(subject, status="timeout")
                load_error = "실행 시간 초과" if load_error is None else f"실행 시간 초과; {load_error}"
            elif execution["exitCode"] != 0:
                subject = replace(subject, status="failed")
                reason = f"비교 대상 실행 실패 (exitCode={execution['exitCode']})"
                load_error = reason if load_error is None else f"{reason}; {load_error}"
            if subject.framework != arm.framework:
                subject = replace(subject, status="failed")
                reason = f"framework 불일치: manifest={arm.framework}, result={subject.framework}"
                load_error = reason if load_error is None else f"{load_error}; {reason}"
            if (
                arm.framework_version != "unknown"
                and subject.framework_version != arm.framework_version
            ):
                subject = replace(subject, status="failed")
                reason = (
                    "frameworkVersion 불일치: "
                    f"manifest={arm.framework_version}, result={subject.framework_version}"
                )
                load_error = reason if load_error is None else f"{load_error}; {reason}"
            gate_variables = _gate_variables(variables, subject)
            gate_results = [
                _safe_run_gate(gate, gate_variables, subject, manifest)
                for gate in manifest.gates
            ]
            evaluation = evaluate_run(manifest, subject, gate_results, wall_seconds=time.monotonic() - started)
            run = {
                "armId": arm.id,
                "framework": subject.framework,
                "frameworkVersion": subject.framework_version,
                "repetition": repetition,
                "runDirectory": str(run_directory),
                "command": command,
                "execution": {key: value for key, value in execution.items() if key not in {"stdout", "stderr"}},
                "prompt": prompt_metadata,
                "resultLoadError": load_error,
                **evaluation,
            }
            (run_directory / "evaluation.json").write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
            runs.append(run)
    prompt_protocol = None
    if manifest.prompt_protocol is not None:
        prompt_protocol = {
            "artifactContract": [
                {
                    "id": artifact.id,
                    "title": artifact.title,
                    "description": artifact.description,
                }
                for artifact in manifest.prompt_protocol.artifact_contract
            ]
        }
    return {
        "schemaVersion": "easydep-comparison-report/v1",
        "experimentId": manifest.experiment_id,
        "repetitions": manifest.repetitions,
        "promptProtocol": prompt_protocol,
        "runs": runs,
    }
