"""MetaGPT 0.8.2를 격리된 작업공간에서 실행한다."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from ..adapters.common import write_subject_result
from ..adapters.metagpt import parse_metagpt_log
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
    args = parser.parse_args(argv)
    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = args.prompt_file.resolve()
    baseline_home = Path(os.environ.get("EASYDEP_METAGPT_HOME", "")).resolve()
    executable = baseline_home / "Scripts" / "metagpt.exe"
    if not executable.is_file():
        raise FileNotFoundError(
            "MetaGPT 실행환경이 없습니다. 먼저 evaluation/baselines/setup_metagpt.ps1을 실행하세요."
        )
    api_key, base_url, model = llm_settings()
    project_name = safe_project_name("easydep_comparison", run_dir)
    log_path = run_dir / "framework.log"
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["METAGPT_PROJECT_ROOT"] = str(run_dir)
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
    workspace = _workspace(run_dir, project_name)
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
        workspace, run_dir, requirement_ids(prompt_file)
    )
    usage = parse_metagpt_log(combined)
    write_subject_result(
        run_dir / "subject-result.json",
        framework="MetaGPT",
        framework_version="0.8.2",
        status="completed" if process.returncode == 0 and generated else "failed",
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
            "model": model,
            "llmBaseUrl": base_url,
        },
    )
    print(f"MetaGPT workspace: {workspace}")
    return process.returncode if generated else 2


if __name__ == "__main__":
    raise SystemExit(main())
