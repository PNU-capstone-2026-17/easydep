"""Isolated runner for the official MetaGPT software-company baseline."""
from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import time
from pathlib import Path

import yaml

from experiments.baselines.common import (
    ExperimentCase,
    begin_run,
    require_api_key,
    run_manifest,
    write_json,
)

IMAGE = "easydep-metagpt:0.8.2"


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
    command = [
        "docker", "run", "--rm", IMAGE, "metagpt", _task(case),
        "--investment", str(investment), "--n_round", str(rounds),
    ]
    manifest = run_manifest("metagpt", case, command)
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
    workspace = run_dir / "workspace"
    workspace.mkdir()
    config = {
        "llm": {
            "api_type": "openai",
            "model": os.getenv("MODEL", "openai/gpt-oss-120b"),
            "base_url": os.getenv("BASE_URL", "https://integrate.api.nvidia.com/v1"),
            "api_key": os.environ["API_KEY"],
        }
    }
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="easydep-metagpt-") as temp:
        config_path = Path(temp) / "config2.yaml"
        config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        docker_command = [
            "docker", "run", "--rm",
            "-v", f"{workspace.resolve()}:/opt/metagpt/workspace",
            "-v", f"{config_path.resolve()}:/root/.metagpt/config2.yaml:ro",
            IMAGE, "metagpt", _task(case),
            "--investment", str(investment), "--n_round", str(rounds),
        ]
        completed = subprocess.run(docker_command, capture_output=True, text=True, check=False)
    (run_dir / "stdout.log").write_text(completed.stdout, encoding="utf-8")
    (run_dir / "stderr.log").write_text(completed.stderr, encoding="utf-8")
    manifest.update({
        "status": "completed" if completed.returncode == 0 else "failed",
        "exitCode": completed.returncode,
        "elapsedSeconds": round(time.perf_counter() - started, 3),
    })
    write_json(run_dir / "manifest.json", manifest)
    if completed.returncode != 0:
        raise RuntimeError(f"MetaGPT failed; inspect {run_dir / 'stderr.log'}")
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MetaGPT in its pinned Docker image")
    parser.add_argument("case", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--investment", type=float, default=3.0)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(run(args.case, args.output_root, args.dry_run, args.investment, args.rounds))


if __name__ == "__main__":
    main()
