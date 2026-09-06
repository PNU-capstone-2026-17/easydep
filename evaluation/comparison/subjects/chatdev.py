"""고정 revision의 ChatDev 1.1.6을 실행하고 결과를 run 폴더로 회수한다."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from ..adapters.chatdev import parse_chatdev_usage
from ..adapters.common import write_subject_result
from .artifacts import write_evidence_files
from .common import llm_settings, prompt_sha256, requirement_ids, safe_project_name


def _latest_output(warehouse: Path, prefix: str, before: set[Path]) -> Path | None:
    candidates = [
        path
        for path in warehouse.glob(f"{prefix}*")
        if path.is_dir() and path.resolve() not in before
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime_ns) if candidates else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="실제 ChatDev 비교 arm 실행")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--incremental-source", type=Path)
    parser.add_argument("--requirements-file", type=Path)
    args = parser.parse_args(argv)
    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = args.prompt_file.resolve()
    configured_home = os.environ.get("EASYDEP_CHATDEV_HOME")
    baseline_home = Path(
        configured_home
        or (Path(os.environ.get("LOCALAPPDATA", "")) / "EasyDep" / "comparison" / "chatdev")
    ).resolve()
    python = baseline_home / "Scripts" / "python.exe"
    source = baseline_home / "source"
    run_script = source / "run.py"
    if not python.is_file() or not run_script.is_file():
        raise FileNotFoundError(
            "ChatDev 실행환경이 없습니다. 먼저 evaluation/baselines/setup_chatdev.ps1을 실행하세요."
        )
    api_key, base_url, model = llm_settings()
    project_name = safe_project_name("easydep_comparison", run_dir)
    organization = "EasyDepEvaluation"
    output_prefix = f"{project_name}_{organization}_"
    warehouse = source / "WareHouse"
    warehouse.mkdir(parents=True, exist_ok=True)
    before = {path.resolve() for path in warehouse.glob(f"{output_prefix}*") if path.is_dir()}
    env = os.environ.copy()
    env.update(
        {
            "PYTHONUTF8": "1",
            "OPENAI_API_KEY": api_key,
            "BASE_URL": base_url,
            "OPENAI_BASE_URL": base_url,
            "OPENAI_API_BASE": base_url,
            "OPENAI_MODEL": model,
        }
    )
    command = [
        str(python),
        "-X",
        "utf8",
        str(run_script),
        "--task",
        prompt_file.read_text(encoding="utf-8"),
        "--name",
        project_name,
        "--org",
        organization,
        "--config",
        "Incremental" if args.incremental_source else "Default",
    ]
    if args.incremental_source:
        incremental_source = args.incremental_source.resolve()
        if not incremental_source.is_dir():
            raise FileNotFoundError(f"증분 수정 원본이 없습니다: {incremental_source}")
        command.extend(["--path", str(incremental_source)])
    else:
        incremental_source = None
    process = subprocess.run(
        command,
        cwd=source,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    combined = f"{process.stdout}\n{process.stderr}"
    (run_dir / "framework.log").write_text(combined, encoding="utf-8")
    generated = _latest_output(warehouse, output_prefix, before)
    workspace = run_dir / "workspace"
    if generated is not None:
        if workspace.exists():
            shutil.rmtree(workspace)
        shutil.copytree(generated, workspace)
        shutil.rmtree(generated)
    else:
        workspace.mkdir(parents=True, exist_ok=True)
    usage_text = combined
    if generated is not None:
        native_logs = []
        for framework_log in workspace.rglob("*.log"):
            try:
                if framework_log.stat().st_size <= 10 * 1024 * 1024:
                    native_logs.append(framework_log)
            except OSError:
                continue
        if native_logs:
            newest_native_log = max(native_logs, key=lambda path: path.stat().st_mtime_ns)
            usage_text = newest_native_log.read_text(encoding="utf-8", errors="replace")
            combined += "\n" + usage_text
        (run_dir / "framework.log").write_text(combined, encoding="utf-8")
    artifact_path, requirement_path, _ = write_evidence_files(
        workspace,
        run_dir,
        requirement_ids(
            args.requirements_file.resolve() if args.requirements_file else prompt_file
        ),
    )
    usage = parse_chatdev_usage(usage_text)
    write_subject_result(
        run_dir / "subject-result.json",
        framework="ChatDev",
        framework_version="1.1.6",
        status="completed" if process.returncode == 0 and generated is not None else "failed",
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
            "model": model,
            "llmBaseUrl": base_url,
        },
    )
    print(f"ChatDev workspace: {workspace}")
    return process.returncode if generated is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
