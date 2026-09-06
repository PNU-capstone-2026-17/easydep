"""MetaGPT 0.8.2를 격리된 작업공간에서 실행한다."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..adapters.common import write_subject_result
from ..adapters.metagpt import parse_metagpt_log, parse_metagpt_usage_events
from .artifacts import write_evidence_files
from .common import llm_settings, prompt_sha256, requirement_ids, safe_project_name


def _workspace(run_dir: Path, project_name: str) -> Path:
    expected = run_dir / "workspace" / project_name
    if expected.is_dir():
        return expected
    candidates = [path for path in (run_dir / "workspace").glob("*") if path.is_dir()]
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime_ns)
    return expected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="실제 MetaGPT 비교 arm 실행")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--investment", type=float, default=10.0)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--incremental-source", type=Path)
    parser.add_argument("--incremental-project-relative", type=Path)
    parser.add_argument("--requirements-file", type=Path)
    args = parser.parse_args(argv)
    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = args.prompt_file.resolve()
    configured_home = os.environ.get("EASYDEP_METAGPT_HOME")
    baseline_home = Path(
        configured_home
        or (Path(os.environ.get("LOCALAPPDATA", "")) / "EasyDep" / "comparison" / "metagpt")
    ).resolve()
    executable = baseline_home / "Scripts" / "metagpt.exe"
    if not executable.is_file():
        raise FileNotFoundError(
            "MetaGPT 실행환경이 없습니다. 먼저 evaluation/baselines/setup_metagpt.ps1을 실행하세요."
        )
    api_key, base_url, model = llm_settings()
    incremental_source = (
        args.incremental_source.resolve() if args.incremental_source else None
    )
    workspace = run_dir / "workspace"
    if incremental_source:
        if not incremental_source.is_dir():
            raise FileNotFoundError(f"증분 수정 원본이 없습니다: {incremental_source}")
        if workspace.exists():
            shutil.rmtree(workspace)
        shutil.copytree(incremental_source, workspace)
        project_path = (
            workspace / args.incremental_project_relative
            if args.incremental_project_relative
            else workspace
        ).resolve()
        if not project_path.is_dir() or workspace not in project_path.parents and project_path != workspace:
            raise ValueError("증분 수정 프로젝트 경로가 복사된 작업공간 밖이거나 존재하지 않습니다.")
        project_name = project_path.name
    else:
        project_path = None
        project_name = safe_project_name("easydep_comparison", run_dir)
    log_path = run_dir / "framework.log"
    usage_log_path = run_dir / "metagpt-provider-usage.jsonl"
    usage_status_path = run_dir / "metagpt-usage-instrumentation.json"
    for generated_metric_path in (usage_log_path, usage_status_path):
        if generated_metric_path.exists():
            generated_metric_path.unlink()
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["METAGPT_PROJECT_ROOT"] = str(run_dir)
    usage_hook_directory = (
        Path(__file__).resolve().parents[2] / "baselines" / "metagpt_usage_hook"
    )
    existing_python_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(usage_hook_directory) + (
        os.pathsep + existing_python_path if existing_python_path else ""
    )
    env["EASYDEP_METAGPT_USAGE_LOG"] = str(usage_log_path)
    env["EASYDEP_METAGPT_USAGE_STATUS"] = str(usage_status_path)
    command = [
        str(executable),
        prompt_file.read_text(encoding="utf-8"),
        "--project-name",
        project_name,
        "--investment",
        str(args.investment),
        "--n-round",
        str(args.rounds),
        "--run-tests",
    ]
    if project_path is not None:
        command.extend(["--inc", "--project-path", str(project_path)])
    with tempfile.TemporaryDirectory(prefix="easydep-metagpt-") as secret_home:
        home = Path(secret_home)
        config_root = home / ".metagpt"
        config_root.mkdir(parents=True)
        # MetaGPT 0.8.2는 설정 파일만 지원한다. 시스템 임시 폴더에 만들고 실행 직후 제거한다.
        (config_root / "config2.yaml").write_text(
            "llm:\n"
            "  api_type: openai\n"
            f"  model: {model!r}\n"
            f"  base_url: {base_url!r}\n"
            f"  api_key: {api_key!r}\n",
            encoding="utf-8",
        )
        env["HOME"] = secret_home
        env["USERPROFILE"] = secret_home
        process = subprocess.run(
            command,
            cwd=run_dir,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    combined = f"{process.stdout}\n{process.stderr}"
    workspace = workspace if incremental_source else _workspace(run_dir, project_name)
    generated = workspace.is_dir() and any(workspace.iterdir())
    workspace.mkdir(parents=True, exist_ok=True)
    for framework_log in workspace.rglob("*.log"):
        try:
            if framework_log.stat().st_size <= 10 * 1024 * 1024:
                combined += "\n" + framework_log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    log_path.write_text(combined, encoding="utf-8")
    artifact_path, requirement_path, _ = write_evidence_files(
        workspace,
        run_dir,
        requirement_ids(
            args.requirements_file.resolve() if args.requirements_file else prompt_file
        ),
    )
    structured_usage = parse_metagpt_usage_events(
        usage_log_path.read_text(encoding="utf-8", errors="replace")
        if usage_log_path.is_file()
        else ""
    )
    usage = (
        structured_usage
        if structured_usage["totalTokens"] is not None
        else parse_metagpt_log(combined)
    )
    instrumentation_status: dict[str, object]
    try:
        raw_status = json.loads(usage_status_path.read_text(encoding="utf-8"))
        instrumentation_status = raw_status if isinstance(raw_status, dict) else {}
    except (OSError, json.JSONDecodeError):
        instrumentation_status = {
            "status": "notObserved",
            "detail": "The child process did not produce an instrumentation status artifact.",
        }
    structured_complete = (
        instrumentation_status.get("status") == "installed"
        and structured_usage["totalTokens"] is not None
        and structured_usage["missingUsageCalls"] == 0
        and structured_usage["duplicateUsageRows"] == 0
    )
    framework_succeeded = process.returncode == 0 and generated
    write_subject_result(
        run_dir / "subject-result.json",
        framework="MetaGPT",
        framework_version="0.8.2",
        status="completed" if framework_succeeded and structured_complete else "failed",
        workspace=workspace,
        input_tokens=usage["inputTokens"],
        output_tokens=usage["outputTokens"],
        total_tokens=usage["totalTokens"],
        llm_calls=usage["llmCalls"],
        missing_usage_calls=int(usage["missingUsageCalls"]),
        source=str(usage["source"]),
        evidence=json.loads(requirement_path.read_text(encoding="utf-8")),
        artifact_evidence=json.loads(artifact_path.read_text(encoding="utf-8")),
        metadata={
            "projectName": project_name,
            "promptSha256": prompt_sha256(prompt_file),
            "revisionMode": "incremental" if incremental_source else "initial",
            "incrementalSource": str(incremental_source) if incremental_source else None,
            "incrementalProjectRelative": (
                str(args.incremental_project_relative)
                if args.incremental_project_relative
                else None
            ),
            "model": model,
            "llmBaseUrl": base_url,
            "usageInstrumentation": {
                "status": instrumentation_status.get("status", "unknown"),
                "detail": instrumentation_status.get("detail", ""),
                "structuredUsageComplete": structured_complete,
                "validUsageEvents": structured_usage["llmCalls"],
                "invalidUsageRows": structured_usage["invalidUsageRows"],
                "duplicateUsageRows": structured_usage["duplicateUsageRows"],
                "llmCallDefinition": "unique provider responses containing a usage object",
                "usageLog": usage_log_path.name,
                "statusArtifact": usage_status_path.name,
            },
        },
    )
    print(f"MetaGPT workspace: {workspace}")
    if framework_succeeded and not structured_complete:
        print(
            "MetaGPT generation completed, but structured token usage was incomplete; "
            "the run is rejected for comparison.",
        )
        return 3
    return process.returncode if generated else 2


if __name__ == "__main__":
    raise SystemExit(main())
