"""Isolated runner for the official MetaGPT software-company baseline."""
from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import time
from pathlib import Path

import yaml

from evaluation.baselines.common import (
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

DEFAULT_EXECUTABLE = ROOT / ".venv-metagpt" / (
    "Scripts/metagpt.exe" if os.name == "nt" else "bin/metagpt"
)


def _executable() -> Path:
    configured = os.getenv("METAGPT_EXECUTABLE")
    executable = Path(configured) if configured else DEFAULT_EXECUTABLE
    if not executable.is_file():
        raise RuntimeError(
            f"MetaGPT executable not found: {executable}. "
            "Run evaluation/baselines/setup_metagpt.ps1 first."
        )
    return executable


def _task(case: ExperimentCase) -> str:
    return f"""{case.prompt()}

Act as the unmodified MetaGPT software company. Produce a complete Java 21 Spring Boot Gradle
repository. Include requirements, architecture/design, Mermaid deployment diagram, source,
tests, Dockerfile, VM deployment documentation or IaC, and a requirement traceability file.
The scope is Docker on Linux VM only: no Kubernetes and no managed application platform.
Do not use web search. Do not include credentials. Record unresolved contradictions explicitly.
"""


def run(
    case_path: Path,
    output_root: Path | None = None,
    dry_run: bool = False,
    investment: float = 3.0,
    rounds: int = 5,
) -> Path:
    case = ExperimentCase.load(case_path)
    run_dir = begin_run("metagpt", case, output_root)
    executable = Path(os.getenv("METAGPT_EXECUTABLE", str(DEFAULT_EXECUTABLE)))
    command = [str(executable), _task(case), "--investment", str(investment),
               "--n-round", str(rounds)]
    manifest = run_manifest("metagpt", case, command, run_dir.name)
    manifest.update({"metagptVersion": "0.8.2", "investment": investment, "rounds": rounds})
    write_json(run_dir / "input.json", {
        "caseId": case.case_id,
        "requirements": case.requirements,
        "cloudConstraints": case.cloud_constraints,
        "scope": case.scope,
    })
    (run_dir / "task.txt").write_text(_task(case), encoding="utf-8")
    if dry_run:
        manifest["status"] = "dry-run"
        write_json(run_dir / "manifest.json", manifest)
        return run_dir

    require_api_key()
    executable = _executable()
    workspace = run_dir / "workspace"
    workspace.mkdir()
    config = {
        "llm": {
            "api_type": "openai",
            "model": model(),
            "base_url": base_url(),
            "api_key": os.environ["API_KEY"],
            "temperature": temperature(),
            "seed": seed(),
        }
    }
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="easydep-metagpt-") as temp:
        profile = Path(temp)
        config_path = profile / ".metagpt" / "config2.yaml"
        config_path.parent.mkdir()
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        metagpt_command = [
            str(executable), _task(case), "--investment", str(investment),
            "--n-round", str(rounds),
        ]
        environment = os.environ.copy()
        environment.update({"HOME": str(profile), "USERPROFILE": str(profile)})
        completed = subprocess.run(
            metagpt_command,
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    (run_dir / "stdout.log").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    manifest.update({
        "status": "completed" if completed.returncode == 0 else "failed",
        "completedStages": (
            ["requirements", "design", "implementation", "testing"]
            if completed.returncode == 0
            else []
        ),
        "exitCode": completed.returncode,
        "elapsedSeconds": round(time.perf_counter() - started, 3),
    })
    write_json(run_dir / "manifest.json", manifest)
    if completed.returncode != 0:
        raise RuntimeError(f"MetaGPT failed; inspect {run_dir / 'stderr.log'}")
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MetaGPT in its pinned Python 3.11 venv")
    parser.add_argument("case", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--investment", type=float, default=3.0)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(run(args.case, args.output_root, args.dry_run, args.investment, args.rounds))


if __name__ == "__main__":
    main()
