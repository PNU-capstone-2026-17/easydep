"""Controlled end-to-end experiment runner and result aggregator."""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.core.orchestration.process import run_process_tree
from evaluation.baselines.common import (
    DEFAULT_ARTIFACT_ROOT,
    ROOT,
    ExperimentCase,
    ExperimentSuite,
    model,
    seed,
    temperature,
    write_json,
)
from evaluation.implementation import evaluate_repository, write_evaluation

SUITE_PATH = ROOT / "evaluation" / "baselines" / "cases" / "suite.json"
ORACLE_PATH = SUITE_PATH.with_name("oracle.json")
DEFAULT_JOB_TIMEOUT_SECONDS = 7200


@dataclass(frozen=True)
class Job:
    arm: str
    case_path: str
    case_id: str
    repetition: int

    @property
    def key(self) -> str:
        return f"{self.arm}:{self.case_id}:r{self.repetition}"


def build_schedule(suite: ExperimentSuite, split: str, random_seed: int) -> list[Job]:
    jobs = [
        Job(arm, str(case_path), ExperimentCase.load(case_path).case_id, repetition)
        for repetition in range(1, suite.repetitions + 1)
        for case_path in suite.cases(split)
        for arm in suite.arms
    ]
    random.Random(random_seed).shuffle(jobs)  # noqa: S311 - reproducible experiment order
    return jobs


def select_jobs(
    jobs: list[Job],
    *,
    arms: set[str] | None = None,
    cases: set[str] | None = None,
) -> list[Job]:
    """Select an explicit pilot subset without changing the frozen schedule order."""
    selected = [
        job
        for job in jobs
        if (not arms or job.arm in arms) and (not cases or job.case_id in cases)
    ]
    if arms:
        unknown = sorted(arms - {job.arm for job in jobs})
        if unknown:
            raise ValueError("unknown experiment arm(s): " + ", ".join(unknown))
    if cases:
        unknown = sorted(cases - {job.case_id for job in jobs})
        if unknown:
            raise ValueError("case(s) not in selected split: " + ", ".join(unknown))
    return selected


def _result_stem(
    split: str, *, arms: set[str] | None = None, cases: set[str] | None = None
) -> str:
    """Keep pilot indexes separate from the complete split index."""
    selectors = sorted(arms or set()) + sorted(cases or set())
    return "-".join(["experiment", split, *selectors])


def environment_report() -> dict[str, Any]:
    """Return a credential-safe, read-only experiment environment report."""
    from evaluation.baselines.metagpt import DEFAULT_EXECUTABLE
    from evaluation.implementation import _command, _tool_path

    docker = _tool_path("docker", "EVALUATION_DOCKER_PATH")
    tofu = _tool_path("tofu", "EVALUATION_TOFU_PATH")
    terraform = _tool_path("terraform", "EVALUATION_TERRAFORM_PATH")
    trivy = _tool_path("trivy", "EVALUATION_TRIVY_PATH")
    lizard = shutil.which("lizard")
    configured_metagpt = Path(os.getenv("METAGPT_EXECUTABLE", str(DEFAULT_EXECUTABLE)))
    docker_daemon = (
        _command([docker, "info", "--format", "{{json .ServerVersion}}"], ROOT)
        if docker
        else {"status": "unavailable", "reason": "Docker CLI not found"}
    )
    required = {
        "apiKey": bool(os.getenv("API_KEY")),
        "metaGPT": configured_metagpt.is_file(),
        "iacEngine": bool(tofu or terraform),
        "dockerDaemon": docker_daemon.get("status") == "passed",
        "lizard": bool(lizard),
    }
    return {
        "schemaVersion": "easydep-experiment-environment/v1",
        "ready": all(required.values()),
        "required": required,
        "tools": {
            "docker": docker,
            "dockerDaemon": docker_daemon,
            "opentofu": tofu,
            "terraformFallback": terraform,
            "trivyOptional": trivy,
            "lizard": lizard,
            "metaGPT": str(configured_metagpt.resolve())
            if configured_metagpt.is_file()
            else None,
        },
        "configuration": {
            "model": model(),
            "temperature": temperature(),
            "seed": seed(),
            "apiKeyPresent": bool(os.getenv("API_KEY")),
        },
        "notes": [
            "Trivy is an optional security metric and does not determine eligibility.",
            "EasyDep batch runs use the provider choices recorded in each run manifest.",
        ],
    }


def _easydep(case_path: Path) -> Path:
    from app.core.orchestration import RunRequest, run_batch
    from app.core.orchestration.contracts import ProviderConfig, ProviderKind

    case = ExperimentCase.load(case_path)
    result = run_batch(
        RunRequest(
            requirements=case.requirements,
            resource_constraints_text=case.cloud_constraints,
            app_id=case.case_id,
            variant="full",
            case_id=case.case_id,
            purpose="evaluation",
            mode="batch",
            providers=ProviderConfig(
                implementation_scaffold=ProviderKind.LLM,
            ),
        )
    )
    if result.status.value != "completed":
        raise RuntimeError(
            f"EasyDep stopped at {result.stage.value}: "
            + str(result.state.get("error") or result.status.value)
        )
    return DEFAULT_ARTIFACT_ROOT / result.run_id


def _run_arm(job: Job, artifact_root: Path = DEFAULT_ARTIFACT_ROOT) -> Path:
    case_path = Path(job.case_path)
    if job.arm == "cot-standard":
        from evaluation.baselines.cot import run

        return run(case_path, artifact_root)
    if job.arm == "metagpt-standard":
        from evaluation.baselines.metagpt import run

        return run(case_path, artifact_root)
    if job.arm == "easydep-full":
        return _easydep(case_path)
    raise ValueError(f"unknown experiment arm: {job.arm}")


def _repository(run_dir: Path, arm: str) -> Path:
    if arm.startswith("easydep-"):
        return run_dir / "03-implementation" / "application"
    return run_dir / "repo"


def _augment_manifest(
    run_dir: Path,
    job: Job,
    *,
    generation_elapsed: float,
    evaluation_elapsed: float | None = None,
    total_elapsed: float | None = None,
    evaluation_status: str | None = None,
) -> None:
    path = run_dir / "manifest.json"
    value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    value.update({
        "experimentJob": job.key,
        "repetition": job.repetition,
        "model": model(),
        "temperature": temperature(),
        "seed": seed(),
        "elapsedSecondsGeneration": round(generation_elapsed, 3),
    })
    if evaluation_elapsed is not None:
        value["elapsedSecondsEvaluation"] = round(evaluation_elapsed, 3)
    if total_elapsed is not None:
        value["elapsedSecondsTotal"] = round(total_elapsed, 3)
    if evaluation_status is not None:
        value["evaluationStatus"] = evaluation_status
    write_json(path, value)


def run_job(
    job: Job,
    *,
    run_tools: bool = True,
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT,
) -> dict[str, Any]:
    started = time.perf_counter()
    started_at = time.time()
    try:
        run_dir = _run_arm(job, artifact_root)
    except Exception as exc:
        return {
            "job": job.key,
            "status": "failed",
            "generationStatus": "failed",
            "evaluationStatus": "not-run",
            "runId": _find_created_run(job, artifact_root, started_at),
            "generationError": f"{type(exc).__name__}: {exc}",
            "elapsedSeconds": round(time.perf_counter() - started, 3),
        }

    generation_elapsed = time.perf_counter() - started
    evaluation_started = time.perf_counter()
    try:
        result = evaluate_repository(
            _repository(run_dir, job.arm),
            json.loads(ORACLE_PATH.read_text(encoding="utf-8")),
            run_tools=run_tools,
            case_id=job.case_id,
        )
        write_evaluation(run_dir / "evaluation.json", result)
        evaluation_elapsed = time.perf_counter() - evaluation_started
        total_elapsed = time.perf_counter() - started
        _augment_manifest(
            run_dir,
            job,
            generation_elapsed=generation_elapsed,
            evaluation_elapsed=evaluation_elapsed,
            total_elapsed=total_elapsed,
            evaluation_status="completed",
        )
        return {
            "job": job.key,
            "status": "completed",
            "generationStatus": "completed",
            "evaluationStatus": "completed",
            "runId": run_dir.name,
            "evaluation": "evaluation.json",
            "experimentEligible": result["experimentEligible"],
            "elapsedSeconds": round(total_elapsed, 3),
        }
    except Exception as exc:
        evaluation_elapsed = time.perf_counter() - evaluation_started
        total_elapsed = time.perf_counter() - started
        _augment_manifest(
            run_dir,
            job,
            generation_elapsed=generation_elapsed,
            evaluation_elapsed=evaluation_elapsed,
            total_elapsed=total_elapsed,
            evaluation_status="failed",
        )
        return {
            "job": job.key,
            "status": "completed",
            "generationStatus": "completed",
            "evaluationStatus": "failed",
            "runId": run_dir.name,
            "evaluationError": f"{type(exc).__name__}: {exc}",
            "elapsedSeconds": round(total_elapsed, 3),
        }


def _metric(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 6) if values else None,
        "median": round(statistics.median(values), 6) if values else None,
        "min": round(min(values), 6) if values else None,
        "max": round(max(values), 6) if values else None,
    }


def aggregate(records: list[dict[str, Any]], artifact_root: Path) -> dict[str, Any]:
    by_arm: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        arm = str(record["job"]).split(":", 1)[0]
        by_arm.setdefault(arm, []).append(record)
    arms: dict[str, Any] = {}
    for arm, items in sorted(by_arm.items()):
        pass_rates: list[float] = []
        complexity_metrics: dict[str, list[float]] = {
            "mean": [],
            "p95": [],
            "max": [],
            "functionsAbove10Ratio": [],
            "decisionPointDensityPer100Nloc": [],
        }
        branch_coverage: list[float] = []
        complexity_coverage: list[float] = []
        coverage_available = 0
        container_pass: list[float] = []
        container_unavailable = 0
        elapsed_seconds: list[float] = []
        eligible = 0
        implementation_complete = 0
        markdown_contaminated = 0
        semantic_unknown = 0
        for item in items:
            run_id = item.get("runId")
            path = artifact_root / str(run_id) / "evaluation.json"
            elapsed = item.get("elapsedSeconds")
            manifest_path = artifact_root / str(run_id) / "manifest.json"
            if run_id and manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                elapsed = manifest.get(
                    "elapsedSecondsTotal", manifest.get("elapsedSeconds", elapsed)
                )
            if elapsed is not None:
                elapsed_seconds.append(float(elapsed))
            if not run_id or not path.is_file():
                continue
            result = json.loads(path.read_text(encoding="utf-8"))
            score = result.get("score") or {}
            if score.get("passRate") is not None:
                pass_rates.append(float(score["passRate"]))
            semantic_unknown += int(score.get("unknown") or 0)
            repository = result.get("repository") or {}
            implementation_complete += int(
                bool(repository.get("implementationComplete"))
            )
            markdown_contaminated += int(
                bool(repository.get("markdownContaminatedFiles"))
            )
            quality = result.get("codeQuality") or {}
            complexity = quality.get("complexity") or {}
            if complexity.get("status") == "available":
                ccn = complexity.get("cyclomaticComplexity") or {}
                for name in ("mean", "p95", "max", "functionsAbove10Ratio"):
                    if ccn.get(name) is not None:
                        complexity_metrics[name].append(float(ccn[name]))
                density = complexity.get("decisionPointDensityPer100Nloc")
                if density is not None:
                    complexity_metrics["decisionPointDensityPer100Nloc"].append(
                        float(density)
                    )
            coverage = quality.get("coverage") or {}
            if coverage.get("status") == "available":
                coverage_available += 1
                counters = coverage.get("counters") or {}
                for name, target in (
                    ("branch", branch_coverage),
                    ("complexity", complexity_coverage),
                ):
                    ratio = (counters.get(name) or {}).get("ratio")
                    if ratio is not None:
                        target.append(float(ratio))
            container = (result.get("externalTools") or {}).get("container") or {}
            if container.get("status") in {"passed", "failed"}:
                container_pass.append(float(container["status"] == "passed"))
            elif container.get("status") == "unavailable":
                container_unavailable += 1
            eligible += int(bool(result.get("experimentEligible")))
        arms[arm] = {
            "scheduled": len(items),
            "completed": sum(item.get("status") == "completed" for item in items),
            "failed": sum(item.get("status") == "failed" for item in items),
            "evaluationCompleted": sum(
                item.get("evaluationStatus") == "completed" for item in items
            ),
            "evaluationFailed": sum(
                item.get("evaluationStatus") == "failed" for item in items
            ),
            "evaluationNotRun": sum(
                item.get("evaluationStatus") == "not-run" for item in items
            ),
            "experimentEligible": eligible,
            "implementationCompleteRuns": implementation_complete,
            "markdownContaminatedRuns": markdown_contaminated,
            "semanticUnknownChecks": semantic_unknown,
            "elapsedSeconds": _metric(elapsed_seconds),
            "semanticPassRate": _metric(pass_rates),
            "codeQuality": {
                "cyclomaticComplexity": {
                    name: _metric(values)
                    for name, values in complexity_metrics.items()
                    if name != "decisionPointDensityPer100Nloc"
                },
                "decisionPointDensityPer100Nloc": _metric(
                    complexity_metrics["decisionPointDensityPer100Nloc"]
                ),
                "coverage": {
                    "availableRuns": coverage_available,
                    "missingRuns": sum(item.get("status") == "completed" for item in items)
                    - coverage_available,
                    "branchRatio": _metric(branch_coverage),
                    "complexityRatio": _metric(complexity_coverage),
                },
            },
            "containerFunctionalPassRate": _metric(container_pass),
            "containerUnavailableRuns": container_unavailable,
        }
    return {"schemaVersion": "easydep-experiment-summary/v1", "arms": arms}


def refresh_completed_records(
    records: list[dict[str, Any]], artifact_root: Path
) -> None:
    """Refresh evaluation fields without changing the original generation status."""
    for record in records:
        run_id = record.get("runId")
        if not run_id:
            continue
        path = artifact_root / str(run_id) / "evaluation.json"
        if not path.is_file():
            continue
        try:
            evaluation = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        record["evaluationStatus"] = "completed"
        record["evaluation"] = "evaluation.json"
        record["experimentEligible"] = bool(evaluation.get("experimentEligible"))


def _find_created_run(job: Job, artifact_root: Path, started_at: float) -> str | None:
    system, variant = job.arm.rsplit("-", 1)
    for path in sorted(artifact_root.glob("*"), key=lambda item: item.stat().st_mtime, reverse=True):
        manifest_path = path / "manifest.json"
        if not manifest_path.is_file() or path.stat().st_mtime < started_at:
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            manifest.get("system") == system
            and manifest.get("variant") == variant
            and manifest.get("caseId") == job.case_id
        ):
            return path.name
    return None


def _run_isolated(
    job: Job,
    *,
    artifact_root: Path,
    run_tools: bool,
    timeout_seconds: int,
) -> dict[str, Any]:
    worker_root = ROOT / ".easydep" / "experiment-workers"
    worker_root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    job_path = worker_root / f"{token}-job.json"
    result_path = worker_root / f"{token}-result.json"
    write_json(job_path, asdict(job))
    command = [
        sys.executable,
        "-m",
        "evaluation.experiment",
        "--worker-job",
        str(job_path),
        "--worker-result",
        str(result_path),
        "--artifact-root",
        str(artifact_root),
    ]
    if not run_tools:
        command.append("--skip-tools")
    environment = os.environ.copy()
    environment.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    started_at = time.time()
    try:
        completed = run_process_tree(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        if result_path.is_file():
            record = json.loads(result_path.read_text(encoding="utf-8"))
        else:
            record = {
                "job": job.key,
                "status": "failed",
                "runId": _find_created_run(job, artifact_root, started_at),
                "error": f"worker exited {completed.returncode} without a result",
            }
        if completed.stderr:
            record["workerStderr"] = completed.stderr[-4000:]
        return record
    except subprocess.TimeoutExpired:
        run_id = _find_created_run(job, artifact_root, started_at)
        record = {
            "job": job.key,
            "status": "timeout",
            "runId": run_id,
            "error": f"wall-clock limit exceeded: {timeout_seconds}s",
            "elapsedSeconds": float(timeout_seconds),
        }
        if run_id:
            write_json(artifact_root / run_id / "experiment-timeout.json", record)
        return record
    finally:
        job_path.unlink(missing_ok=True)
        result_path.unlink(missing_ok=True)


def execute(
    split: str,
    *,
    artifact_root: Path = DEFAULT_ARTIFACT_ROOT,
    run_tools: bool = True,
    resume: bool = False,
    limit: int | None = None,
    timeout_seconds: int = DEFAULT_JOB_TIMEOUT_SECONDS,
    arms: set[str] | None = None,
    cases: set[str] | None = None,
) -> Path:
    suite = ExperimentSuite.load(SUITE_PATH)
    schedule = select_jobs(
        build_schedule(suite, split, seed()), arms=arms, cases=cases
    )
    result_stem = _result_stem(split, arms=arms, cases=cases)
    index_path = artifact_root / f"{result_stem}.json"
    index = (
        json.loads(index_path.read_text(encoding="utf-8"))
        if resume and index_path.is_file()
        else {
            "schemaVersion": "easydep-experiment-index/v1",
            "split": split,
            "seed": seed(),
            "model": model(),
            "jobs": [asdict(job) | {"key": job.key} for job in schedule],
            "results": [],
        }
    )
    done = {
        item["job"]
        for item in index["results"]
        if item.get("status") in {"completed", "failed", "timeout"}
    }
    pending = [job for job in schedule if job.key not in done]
    if limit is not None:
        pending = pending[:limit]
    for job in pending:
        index["results"].append({"job": job.key, "status": "running"})
        write_json(index_path, index)
        result = _run_isolated(
            job,
            artifact_root=artifact_root,
            run_tools=run_tools,
            timeout_seconds=timeout_seconds,
        )
        index["results"][-1] = result
        write_json(index_path, index)
    refresh_completed_records(index["results"], artifact_root)
    write_json(index_path, index)
    summary = aggregate(index["results"], artifact_root)
    write_json(artifact_root / f"summary-{result_stem.removeprefix('experiment-')}.json", summary)
    return index_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the controlled VM benchmark")
    parser.add_argument("--split", choices=("development", "holdout"))
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-tools", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--timeout-seconds", type=int, default=DEFAULT_JOB_TIMEOUT_SECONDS
    )
    parser.add_argument("--print-schedule", action="store_true")
    parser.add_argument("--check-environment", action="store_true")
    parser.add_argument("--arm", action="append", choices=(
        "easydep-full", "cot-standard", "metagpt-standard"
    ))
    parser.add_argument("--case", action="append")
    parser.add_argument("--worker-job", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-result", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.check_environment:
        print(json.dumps(environment_report(), ensure_ascii=False, indent=2))
        return
    if args.worker_job:
        if not args.worker_result:
            parser.error("--worker-result is required with --worker-job")
        job = Job(**json.loads(args.worker_job.read_text(encoding="utf-8")))
        write_json(
            args.worker_result,
            run_job(job, run_tools=not args.skip_tools, artifact_root=args.artifact_root),
        )
        return
    if not args.split:
        parser.error("--split is required")
    suite = ExperimentSuite.load(SUITE_PATH)
    if args.print_schedule:
        schedule = select_jobs(
            build_schedule(suite, args.split, seed()),
            arms=set(args.arm or []),
            cases=set(args.case or []),
        )
        print(json.dumps([asdict(job) for job in schedule], indent=2))
        return
    print(execute(
        args.split,
        artifact_root=args.artifact_root,
        run_tools=not args.skip_tools,
        resume=args.resume,
        limit=args.limit,
        timeout_seconds=args.timeout_seconds,
        arms=set(args.arm or []),
        cases=set(args.case or []),
    ))


if __name__ == "__main__":
    main()
