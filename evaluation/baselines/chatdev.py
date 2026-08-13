"""Isolated runner for the official ChatDev 1.x software-company baseline."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from evaluation.baselines.chatdev_proxy import ChatDevModelProxy
from evaluation.baselines.common import (
    BUILD_COMPLETENESS_CONTRACT,
    ROOT,
    ExperimentCase,
    base_url,
    begin_run,
    model,
    require_api_key,
    run_manifest,
    seed,
    temperature,
    write_json,
)

CHATDEV_VERSION = "1.1.6"
CHATDEV_REVISION = "bcab15717940818938402394a04aea2052d76665"
DEFAULT_SOURCE = ROOT / ".venv-chatdev" / "source"
DEFAULT_PYTHON = (
    ROOT / ".venv-chatdev" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
)


def _configured_path(variable: str, default: Path) -> Path:
    return Path(os.getenv(variable, str(default))).resolve()


def _runtime() -> tuple[Path, Path]:
    source = _configured_path("CHATDEV_SOURCE", DEFAULT_SOURCE)
    python = _configured_path("CHATDEV_PYTHON", DEFAULT_PYTHON)
    if not (source / "run.py").is_file():
        raise RuntimeError(
            f"ChatDev source not found: {source}. Run evaluation/baselines/setup_chatdev.ps1 first."
        )
    if not python.is_file():
        raise RuntimeError(
            f"ChatDev Python not found: {python}. Run evaluation/baselines/setup_chatdev.ps1 first."
        )
    return source, python


def _project_name(case: ExperimentCase) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", case.case_id).strip("_") or "case"
    return f"EasyDep_{safe}_{uuid.uuid4().hex[:8]}"


def _task(case: ExperimentCase) -> str:
    return f"""{case.prompt()}

Act as the unmodified ChatDev software company and execute its native demand analysis,
design, coding, review, testing, and documentation workflow. Produce a complete Java 21
Spring Boot Gradle repository. The evaluated repository must include application source,
tests, a Dockerfile, deployable Terraform IaC, and README instructions. Preserve any native
ChatDev requirements, design, review, test, and manual artifacts it creates.
The scope is Docker on Linux VM only: no Kubernetes and no managed application platform.
Do not use web search. Do not include credentials. Record unresolved contradictions explicitly.
{BUILD_COMPLETENESS_CONTRACT}
"""


def _generated_directory(warehouse: Path, project_name: str) -> Path | None:
    candidates = sorted(
        (path for path in warehouse.glob(f"{project_name}_EasyDepBaseline_*") if path.is_dir()),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
    )
    return candidates[-1] if candidates else None


def _materialize_repository(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=False,
        ignore=shutil.ignore_patterns(
            ".git", ".gradle", "build", "__pycache__", "*.log", "*.prompt"
        ),
    )


def run(
    case_path: Path,
    output_root: Path | None = None,
    dry_run: bool = False,
) -> Path:
    case = ExperimentCase.load(case_path)
    run_dir = begin_run("chatdev", case, output_root)
    project_name = _project_name(case)
    source = _configured_path("CHATDEV_SOURCE", DEFAULT_SOURCE)
    python = _configured_path("CHATDEV_PYTHON", DEFAULT_PYTHON)
    command = [
        str(python),
        str(source / "run.py"),
        "--task",
        _task(case),
        "--name",
        project_name,
        "--org",
        "EasyDepBaseline",
        "--config",
        "Default",
        "--model",
        "GPT_4O",
    ]
    manifest = run_manifest("chatdev", case, command, run_dir.name)
    manifest.update(
        {
            "chatdevVersion": CHATDEV_VERSION,
            "chatdevRevision": CHATDEV_REVISION,
            "chatdevNativeModelAlias": "gpt-4o",
            "modelTranslation": "local-openai-compatible-proxy",
        }
    )
    write_json(
        run_dir / "input.json",
        {
            "caseId": case.case_id,
            "requirements": case.requirements,
            "cloudConstraints": case.cloud_constraints,
            "scope": case.scope,
        },
    )
    (run_dir / "task.txt").write_text(_task(case), encoding="utf-8")
    manifest.update({"status": "running", "generationStatus": "running"})
    write_json(run_dir / "manifest.json", manifest)
    if dry_run:
        manifest.update({"status": "dry-run", "finishedAt": time.time()})
        write_json(run_dir / "manifest.json", manifest)
        return run_dir

    require_api_key()
    source, python = _runtime()
    command[0] = str(python)
    command[1] = str(source / "run.py")
    warehouse = source / "WareHouse"
    warehouse.mkdir(exist_ok=True)
    generated: Path | None = None
    started = time.perf_counter()
    environment = os.environ.copy()
    environment.update(
        {
            "OPENAI_API_KEY": os.environ["API_KEY"],
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
    )
    try:
        with ChatDevModelProxy(
            upstream_base_url=base_url(),
            api_key=os.environ["API_KEY"],
            upstream_model=model(),
            timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "600")),
            invalid_max_tokens_fallback=int(os.getenv("CHATDEV_MAX_COMPLETION_TOKENS", "4096")),
            temperature_override=temperature(),
            seed_override=seed(),
        ) as proxy:
            environment["BASE_URL"] = proxy.base_url
            completed = subprocess.run(
                command,
                cwd=source,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            proxy_events = list(proxy.events)
        generated = _generated_directory(warehouse, project_name)
        (run_dir / "stdout.log").write_text(completed.stdout, encoding="utf-8")
        (run_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
        write_json(run_dir / "llm-proxy-events.json", proxy_events)
        if generated is not None:
            _materialize_repository(generated, run_dir / "repo")
            native = run_dir / "chatdev-native"
            native.mkdir()
            for path in generated.iterdir():
                if path.suffix in {".log", ".prompt", ".md"} and path.is_file():
                    shutil.copy2(path, native / path.name)
        elapsed = round(time.perf_counter() - started, 3)
        success = completed.returncode == 0 and generated is not None
        manifest.update(
            {
                "status": "completed" if success else "failed",
                "generationStatus": "completed" if success else "failed",
                "completedStages": (
                    ["requirements", "implementation", "testing", "documentation"]
                    if success
                    else []
                ),
                "chatdevNativePhases": (
                    [
                        "DemandAnalysis",
                        "LanguageChoose",
                        "Coding",
                        "CodeCompleteAll",
                        "CodeReview",
                        "Test",
                        "EnvironmentDoc",
                        "Manual",
                    ]
                    if success
                    else []
                ),
                "chatdevPython": str(python),
                "exitCode": completed.returncode,
                "elapsedSeconds": elapsed,
                "repositoryMaterialized": generated is not None,
                "llmCalls": len(proxy_events),
                "llmErrorResponses": sum(
                    1 for event in proxy_events if int(event["status"]) >= 400
                ),
            }
        )
        write_json(run_dir / "manifest.json", manifest)
        if completed.returncode != 0:
            raise RuntimeError(f"ChatDev failed; inspect {run_dir / 'stderr.log'}")
        if generated is None:
            raise RuntimeError("ChatDev exited without a generated WareHouse project")
        return run_dir
    finally:
        if generated is None:
            generated = _generated_directory(warehouse, project_name)
        if generated is not None and generated.is_dir():
            shutil.rmtree(generated)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ChatDev 1.1.6 in its pinned Python 3.11 venv")
    parser.add_argument("case", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(run(args.case, args.output_root, args.dry_run))


if __name__ == "__main__":
    main()
