"""Controlled end-to-end experiment runner and result aggregator."""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sqlite3
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.implementation.runtime.process import run_process_tree
from evaluation.baselines.common import (
    DEFAULT_ARTIFACT_ROOT,
    ROOT,
    ExperimentCase,
    ExperimentSuite,
    canonical_json_sha256,
    model,
    seed,
    temperature,
    write_json,
)
from evaluation.execution_contract import censored
from evaluation.implementation import evaluate_repository, write_evaluation

SUITE_PATH = ROOT / "evaluation" / "baselines" / "cases" / "suite.json"
ABLATION_SUITE_PATH = SUITE_PATH.with_name("ablation-suite.json")
COMPONENT_SUITE_PATH = ROOT / "evaluation" / "baselines" / "component-cases" / "suite.json"
ORACLE_PATH = SUITE_PATH.with_name("oracle.json")
SYSTEM_ARMS = {
    "easydep-full",
    "cot-standard",
    "metagpt-standard",
    "chatdev-standard",
}
ABLATION_ARMS = {"easydep-full", "easydep-no-depkb", "easydep-no-verification"}
COMPONENT_ARMS = {"easydep-full", "easydep-no-depkb"}
DEFAULT_JOB_TIMEOUT_SECONDS = 7200
DEFAULT_EXPERIMENT_MAX_COMPLETION_TOKENS = 16_384
MIN_FREE_DISK_BYTES = 5 * 1024**3


@dataclass(frozen=True)
class Job:
    arm: str
    case_path: str
    case_id: str
    repetition: int
    oracle_path: str = str(ORACLE_PATH)
    resume_run_id: str | None = None

    @property
    def key(self) -> str:
        return f"{self.arm}:{self.case_id}:r{self.repetition}"


def _progress(event: str, **fields: Any) -> None:
    if os.getenv("EASYDEP_EXPERIMENT_SESSION"):
        print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def build_schedule(suite: ExperimentSuite, split: str, random_seed: int) -> list[Job]:
    jobs = [
        Job(
            arm,
            str(case_path),
            ExperimentCase.load(case_path).case_id,
            repetition,
            str(suite.oracle),
        )
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
    repetitions: set[int] | None = None,
) -> list[Job]:
    """Select an explicit pilot subset without changing the frozen schedule order."""
    selected = [
        job
        for job in jobs
        if (not arms or job.arm in arms)
        and (not cases or job.case_id in cases)
        and (not repetitions or job.repetition in repetitions)
    ]
    if arms:
        unknown = sorted(arms - {job.arm for job in jobs})
        if unknown:
            raise ValueError("unknown experiment arm(s): " + ", ".join(unknown))
    if cases:
        unknown = sorted(cases - {job.case_id for job in jobs})
        if unknown:
            raise ValueError("case(s) not in selected split: " + ", ".join(unknown))
    if repetitions:
        unknown = sorted(repetitions - {job.repetition for job in jobs})
        if unknown:
            raise ValueError(
                "repetition(s) not in selected split: "
                + ", ".join(str(item) for item in unknown)
            )
    return selected


def limit_jobs(jobs: list[Job], limit: int | None) -> list[Job]:
    """Apply the same explicit resource bound to previews and executions."""
    if limit is None:
        return jobs
    if limit < 1:
        raise ValueError("experiment limit must be at least 1")
    return jobs[:limit]


def _result_stem(
    split: str,
    *,
    arms: set[str] | None = None,
    cases: set[str] | None = None,
    repetitions: set[int] | None = None,
) -> str:
    """Keep pilot indexes separate from the complete split index."""
    selectors = (
        sorted(arms or set())
        + sorted(cases or set())
        + [f"r{item}" for item in sorted(repetitions or set())]
    )
    return "-".join(["experiment", split, *selectors])


def environment_report() -> dict[str, Any]:
    """Return a credential-safe, read-only experiment environment report."""
    from evaluation.baselines.chatdev import DEFAULT_PYTHON as CHATDEV_PYTHON
    from evaluation.baselines.chatdev import DEFAULT_SOURCE as CHATDEV_SOURCE
    from evaluation.baselines.metagpt import DEFAULT_EXECUTABLE
    from evaluation.implementation import _command, _tool_path

    docker = _tool_path("docker", "EVALUATION_DOCKER_PATH")
    tofu = _tool_path("tofu", "EVALUATION_TOFU_PATH")
    terraform = _tool_path("terraform", "EVALUATION_TERRAFORM_PATH")
    trivy = _tool_path("trivy", "EVALUATION_TRIVY_PATH")
    lizard = shutil.which("lizard")
    configured_metagpt = Path(os.getenv("METAGPT_EXECUTABLE", str(DEFAULT_EXECUTABLE)))
    configured_chatdev_python = Path(
        os.getenv("CHATDEV_PYTHON", str(CHATDEV_PYTHON))
    )
    configured_chatdev_source = Path(
        os.getenv("CHATDEV_SOURCE", str(CHATDEV_SOURCE))
    )
    docker_daemon = (
        _command([docker, "info", "--format", "{{json .ServerVersion}}"], ROOT)
        if docker
        else {"status": "unavailable", "reason": "Docker CLI not found"}
    )
    required = {
        "apiKey": bool(os.getenv("API_KEY")),
        "metaGPT": configured_metagpt.is_file(),
        "chatDev": (
            configured_chatdev_python.is_file()
            and (configured_chatdev_source / "run.py").is_file()
        ),
        "iacEngine": bool(tofu or terraform),
        "dockerDaemon": docker_daemon.get("status") == "passed",
        "lizard": bool(lizard),
        "diskFree": shutil.disk_usage(ROOT).free >= MIN_FREE_DISK_BYTES,
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
            "chatDevPython": str(configured_chatdev_python.resolve())
            if configured_chatdev_python.is_file()
            else None,
            "chatDevSource": str(configured_chatdev_source.resolve())
            if (configured_chatdev_source / "run.py").is_file()
            else None,
        },
        "configuration": {
            "model": model(),
            "temperature": temperature(),
            "seed": seed(),
            "apiKeyPresent": bool(os.getenv("API_KEY")),
            "maxConcurrentJobs": 1,
            "minimumFreeDiskBytes": MIN_FREE_DISK_BYTES,
            "designLlmClientTimeoutSeconds": float(
                os.getenv("LLM_TIMEOUT_SECONDS", "300")
            ),
            "designLlmWallTimeoutSeconds": float(
                os.getenv("LLM_WALL_TIMEOUT_SECONDS", "330")
            ),
            "freeDiskBytes": shutil.disk_usage(ROOT).free,
        },
        "notes": [
            "Trivy is an optional security metric and does not determine eligibility.",
            "EasyDep batch runs use the provider choices recorded in each run manifest.",
        ],
    }


def _easydep(
    case_path: Path, variant: str = "full", resume_run_id: str | None = None
) -> Path:
    from app.orchestration import RunRequest, retry_failed_run, run_batch
    from app.orchestration.contracts import ProviderConfig

    case = ExperimentCase.load(case_path)
    request = RunRequest(
        requirements=case.requirements,
        resource_constraints_text=case.cloud_constraints,
        app_id=case.case_id,
        variant=variant,
        case_id=case.case_id,
        purpose="evaluation",
        mode="batch",
        providers=ProviderConfig(),
    )
    result = (
        retry_failed_run(
            resume_run_id,
            reason="experiment checkpoint retry after infrastructure repair",
        )
        if resume_run_id
        else run_batch(request)
    )
    initial_status = result.status.value
    repair_budget = max(0, int(os.getenv("EASYDEP_MAX_CHECKPOINT_REPAIRS", "0")))
    repairs: list[dict[str, Any]] = []
    while result.status.value != "completed" and len(repairs) < repair_budget:
        failed_stage = result.stage.value
        failed_error = str(result.state.get("error") or result.status.value)
        _progress(
            "checkpointRepairStarted",
            runId=result.run_id,
            attempt=len(repairs) + 1,
            failedStage=failed_stage,
        )
        result = retry_failed_run(
            result.run_id,
            reason="bounded experiment repair after validated generation failure",
        )
        repairs.append({
            "attempt": len(repairs) + 1,
            "failedStage": failed_stage,
            "failure": failed_error[-2000:],
            "resultStatus": result.status.value,
            "resultStage": result.stage.value,
        })
        _progress(
            "checkpointRepairFinished",
            runId=result.run_id,
            attempt=len(repairs),
            status=result.status.value,
            stage=result.stage.value,
        )
    repair_record = {
        "schemaVersion": "easydep-bounded-generation-repair/v1",
        "initialStatus": initial_status,
        "repairBudget": repair_budget,
        "repairsUsed": len(repairs),
        "finalStatus": result.status.value,
        "attempts": repairs,
    }
    repair_path = DEFAULT_ARTIFACT_ROOT / result.run_id / "generation-repair.json"
    repair_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(repair_path, repair_record)
    implementation = (result.state.get("implementation") or {}).get("data") or {}
    run_root = implementation.get("run_root")
    workspace = Path(str(run_root)) if run_root else None
    try:
        if result.status.value != "completed":
            raise RuntimeError(
                f"EasyDep stopped at {result.stage.value}: "
                + str(result.state.get("error") or result.status.value)
            )
        return DEFAULT_ARTIFACT_ROOT / result.run_id
    finally:
        workspace_root = (ROOT / ".easydep" / "orchestration" / "workspaces").resolve()
        if workspace is not None and workspace.is_dir():
            resolved = workspace.resolve()
            if resolved.parent == workspace_root:
                shutil.rmtree(resolved)


def _run_arm(job: Job, artifact_root: Path = DEFAULT_ARTIFACT_ROOT) -> Path:
    case_path = Path(job.case_path)
    if job.arm == "cot-standard":
        from evaluation.baselines.cot import run

        return run(case_path, artifact_root)
    if job.arm == "metagpt-standard":
        from evaluation.baselines.metagpt import run

        return run(case_path, artifact_root)
    if job.arm == "chatdev-standard":
        from evaluation.baselines.chatdev import run

        return run(case_path, artifact_root)
    if job.arm.startswith("easydep-"):
        return _easydep(
            case_path, job.arm.removeprefix("easydep-"), job.resume_run_id
        )
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
        "experimentSessionId": os.getenv("EASYDEP_EXPERIMENT_SESSION"),
        "workerProcessId": os.getpid(),
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
    oracle_path = Path(job.oracle_path)
    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    started = time.perf_counter()
    started_at = time.time()
    _progress(
        "generationStarted",
        job=job.key,
        resumeRunId=job.resume_run_id,
        startedAtEpoch=started_at,
    )
    try:
        run_dir = _run_arm(job, artifact_root)
    except Exception as exc:
        generation_elapsed = time.perf_counter() - started
        _progress(
            "generationFinished",
            job=job.key,
            status="failed",
            errorType=type(exc).__name__,
            elapsedSeconds=round(generation_elapsed, 6),
        )
        run_id = job.resume_run_id or _find_created_run(job, artifact_root, started_at)
        record: dict[str, Any] = {
            "job": job.key,
            "status": "failed",
            "generationStatus": "failed",
            "evaluationStatus": "not-run",
            "runId": run_id,
            "generationError": f"{type(exc).__name__}: {exc}",
            "elapsedSeconds": round(generation_elapsed, 3),
        }
        refresh_execution_classification([record], exception=exc)
        if not run_id:
            return record
        partial_run = _resolve_run_dir(artifact_root, run_id)
        record.update(_generation_repair_fields(partial_run))
        repository = _repository(partial_run, job.arm)
        if not repository.is_dir():
            return record
        evaluation_started = time.perf_counter()
        _progress("evaluationStarted", job=job.key, afterGenerationFailure=True)
        try:
            result = evaluate_repository(
                repository,
                oracle,
                run_tools=run_tools,
                case_id=job.case_id,
            )
            write_evaluation(partial_run / "evaluation.json", result)
            evaluation_status = "completed"
            record.update(
                evaluationStatus="completed",
                evaluation="evaluation.json",
                experimentEligible=result["experimentEligible"],
            )
        except Exception as evaluation_exc:
            evaluation_status = "failed"
            record.update(
                evaluationStatus="failed",
                evaluationError=f"{type(evaluation_exc).__name__}: {evaluation_exc}",
            )
        evaluation_elapsed = time.perf_counter() - evaluation_started
        _progress(
            "evaluationFinished",
            job=job.key,
            status=evaluation_status,
            elapsedSeconds=round(evaluation_elapsed, 6),
        )
        total_elapsed = time.perf_counter() - started
        record["elapsedSeconds"] = round(total_elapsed, 3)
        _augment_manifest(
            partial_run, job, generation_elapsed=generation_elapsed,
            evaluation_elapsed=evaluation_elapsed, total_elapsed=total_elapsed,
            evaluation_status=evaluation_status,
        )
        return record

    generation_elapsed = time.perf_counter() - started
    evaluation_started = time.perf_counter()
    _progress(
        "generationFinished",
        job=job.key,
        status="completed",
        elapsedSeconds=round(generation_elapsed, 6),
    )
    _progress("evaluationStarted", job=job.key, afterGenerationFailure=False)
    try:
        result = evaluate_repository(
            _repository(run_dir, job.arm),
            oracle,
            run_tools=run_tools,
            case_id=job.case_id,
        )
        write_evaluation(run_dir / "evaluation.json", result)
        evaluation_elapsed = time.perf_counter() - evaluation_started
        _progress(
            "evaluationFinished",
            job=job.key,
            status="completed",
            elapsedSeconds=round(evaluation_elapsed, 6),
        )
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
        } | _generation_repair_fields(run_dir)
    except Exception as exc:
        evaluation_elapsed = time.perf_counter() - evaluation_started
        _progress(
            "evaluationFinished",
            job=job.key,
            status="failed",
            errorType=type(exc).__name__,
            elapsedSeconds=round(evaluation_elapsed, 6),
        )
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
        } | _generation_repair_fields(run_dir)


def _generation_repair_fields(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "generation-repair.json"
    if not path.is_file():
        return {}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        "initialGenerationStatus": record.get("initialStatus"),
        "checkpointRepairBudget": record.get("repairBudget"),
        "checkpointRepairsUsed": record.get("repairsUsed"),
        "recoveredGeneration": (
            record.get("initialStatus") != "completed"
            and record.get("finalStatus") == "completed"
        ),
    }


def _metric(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 6) if values else None,
        "median": round(statistics.median(values), 6) if values else None,
        "min": round(min(values), 6) if values else None,
        "max": round(max(values), 6) if values else None,
    }


_INVALID_CLOUD_CLAIM_SUMMARIES = (
    "invalid resource type",
    "unsupported block type",
    "unsupported argument",
    "reference to undeclared resource",
)


def invalid_cloud_claims(external_tools: dict[str, Any]) -> list[dict[str, str]]:
    """공급자 스키마 검증기가 거부한 IaC 주장만 보수적으로 집계한다."""
    claims: list[dict[str, str]] = []
    engine = external_tools.get("iacEngine") or {}
    for module in engine.get("modules") or []:
        validate = module.get("validate") or {}
        for diagnostic in (validate.get("json") or {}).get("diagnostics") or []:
            summary = str(diagnostic.get("summary") or "")
            if summary.lower().startswith(_INVALID_CLOUD_CLAIM_SUMMARIES):
                claims.append({
                    "module": str(module.get("path") or "."),
                    "summary": summary,
                    "detail": str(diagnostic.get("detail") or ""),
                })
    return claims


def _paired_component_summary(
    records: list[dict[str, Any]], artifact_root: Path, suite: ExperimentSuite
) -> dict[str, Any]:
    case_metadata = {}
    for path in suite.development:
        case = ExperimentCase.load(path)
        case_metadata[case.case_id] = {
            "pairId": str(case.scope["pairId"]),
            "condition": str(case.scope["condition"]),
            "provider": str(case.scope["providers"][0]),
        }
    observations: dict[tuple[str, str, str, int, str], dict[str, float | None]] = {}
    excluded = 0
    for record in records:
        arm, case_id, raw_repetition = str(record["job"]).split(":", 2)
        metadata = case_metadata.get(case_id)
        run_id = record.get("runId")
        path = _resolve_run_dir(artifact_root, str(run_id)) / "evaluation.json"
        if metadata is None or not run_id or not path.is_file():
            excluded += 1
            continue
        evaluation = json.loads(path.read_text(encoding="utf-8"))
        score = evaluation.get("score") or {}
        projection_checks = [
            item for item in score.get("checks") or []
            if item.get("kind") in {"componentProjection", "componentRelation"}
        ]
        projection_rate = None
        if projection_checks:
            projection_rate = sum(
                item.get("status") == "passed" for item in projection_checks
            ) / len(projection_checks)
        container_status = (
            (evaluation.get("externalTools") or {}).get("container") or {}
        ).get("status")
        observations[(
            arm, metadata["pairId"], metadata["provider"],
            int(raw_repetition.removeprefix("r")), metadata["condition"],
        )] = {
            "eligible": float(bool(evaluation.get("experimentEligible"))),
            "semanticPassRate": (
                float(score["passRate"]) if score.get("passRate") is not None else None
            ),
            "functionalPass": (
                float(container_status == "passed")
                if container_status in {"passed", "failed"} else None
            ),
            "componentRelationPassRate": projection_rate,
        }

    metrics = (
        "eligible", "semanticPassRate", "functionalPass",
        "componentRelationPassRate",
    )
    rows = []
    base_keys = {
        key[:4] for key in observations
        if key[4] == "control"
    } & {
        key[:4] for key in observations
        if key[4] == "treatment"
    }
    for arm, pair_id, provider, repetition in sorted(base_keys):
        control = observations[(arm, pair_id, provider, repetition, "control")]
        treatment = observations[(arm, pair_id, provider, repetition, "treatment")]
        deltas = {
            metric: (
                treatment[metric] - control[metric]
                if treatment[metric] is not None and control[metric] is not None else None
            )
            for metric in metrics
        }
        rows.append({
            "arm": arm, "pairId": pair_id, "provider": provider,
            "repetition": repetition, "treatmentMinusControl": deltas,
        })

    contrasts: dict[str, Any] = {}
    for pair_id in sorted({row["pairId"] for row in rows}):
        contrasts[pair_id] = {}
        for provider in ("aws", "azure", "gcp"):
            contrasts[pair_id][provider] = {}
            for arm in sorted(suite.arms):
                selected = [
                    row for row in rows
                    if row["pairId"] == pair_id and row["provider"] == provider
                    and row["arm"] == arm
                ]
                contrasts[pair_id][provider][arm] = {
                    metric: _metric([
                        float(row["treatmentMinusControl"][metric])
                        for row in selected
                        if row["treatmentMinusControl"][metric] is not None
                    ])
                    for metric in metrics
                }

    by_identity = {
        (row["pairId"], row["provider"], row["repetition"], row["arm"]): row
        for row in rows
    }
    did_rows = []
    for pair_id, provider, repetition in sorted({key[:3] for key in by_identity}):
        full = by_identity.get((pair_id, provider, repetition, "easydep-full"))
        no_kb = by_identity.get((pair_id, provider, repetition, "easydep-no-depkb"))
        if not full or not no_kb:
            continue
        did_rows.append({
            "pairId": pair_id, "provider": provider, "repetition": repetition,
            "differenceInDifferences": {
                metric: (
                    full["treatmentMinusControl"][metric]
                    - no_kb["treatmentMinusControl"][metric]
                    if full["treatmentMinusControl"][metric] is not None
                    and no_kb["treatmentMinusControl"][metric] is not None else None
                )
                for metric in metrics
            },
        })
    return {
        "estimand": "(full treatment-control) - (no-depkb treatment-control)",
        "completeWithinArmPairs": len(rows), "excludedObservations": excluded,
        "withinArmContrasts": contrasts, "differenceInDifferences": did_rows,
    }


def aggregate(
    records: list[dict[str, Any]], artifact_root: Path,
    suite: ExperimentSuite | None = None,
) -> dict[str, Any]:
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
        projection_completeness: list[float] = []
        projection_missing = 0
        invalid_claim_counts: list[float] = []
        for item in items:
            run_id = item.get("runId")
            run_dir = _resolve_run_dir(artifact_root, str(run_id))
            path = run_dir / "evaluation.json"
            elapsed = item.get("elapsedSeconds")
            manifest_path = run_dir / "manifest.json"
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
            projection = [
                check for check in score.get("checks") or []
                if check.get("kind") == "providerProjection"
            ]
            if projection:
                passed = sum(bool(check.get("passed")) for check in projection)
                projection_completeness.append(passed / len(projection))
                projection_missing += len(projection) - passed
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
            invalid_claim_counts.append(float(len(invalid_cloud_claims(
                result.get("externalTools") or {}
            ))))
            if container.get("status") in {"passed", "failed"}:
                container_pass.append(float(container["status"] == "passed"))
            elif container.get("status") == "unavailable":
                container_unavailable += 1
            eligible += int(bool(result.get("experimentEligible")))
        arms[arm] = {
            "scheduled": len(items),
            "completed": sum(item.get("status") == "completed" for item in items),
            "failed": sum(
                item.get("status") == "failed"
                and item.get("executionStatus") not in {
                    "censored", "infrastructureFailure"
                }
                for item in items
            ),
            "censored": sum(
                item.get("executionStatus") == "censored" for item in items
            ),
            "infrastructureFailure": sum(
                item.get("executionStatus") == "infrastructureFailure"
                for item in items
            ),
            "censorReasons": {
                reason: sum(item.get("censorReason") == reason for item in items)
                for reason in sorted({
                    str(item.get("censorReason")) for item in items
                    if item.get("censorReason")
                })
            },
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
            "providerProjection": {
                "componentCompleteness": _metric(projection_completeness),
                "missingComponents": projection_missing,
            },
            "invalidCloudClaimsPerRun": _metric(invalid_claim_counts),
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
    summary = {"schemaVersion": "easydep-experiment-summary/v1", "arms": arms}
    if suite is not None and suite.study_design == "paired-components":
        summary["pairedComponents"] = _paired_component_summary(
            records, artifact_root, suite
        )
    return summary


def refresh_completed_records(
    records: list[dict[str, Any]], artifact_root: Path
) -> None:
    """Refresh evaluation fields without changing the original generation status."""
    for record in records:
        run_id = record.get("runId")
        if not run_id:
            continue
        path = _resolve_run_dir(artifact_root, str(run_id)) / "evaluation.json"
        if not path.is_file():
            continue
        try:
            evaluation = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        record["evaluationStatus"] = "completed"
        record["evaluation"] = "evaluation.json"
        record["experimentEligible"] = bool(evaluation.get("experimentEligible"))


def refresh_execution_classification(
    records: list[dict[str, Any]], *, exception: Exception | None = None
) -> None:
    """명시적인 LLM 응답 완료 timeout만 검열로 분리한다.

    비스트리밍 호출의 예외만으로 TTFT나 엔드포인트 전체 장애를 주장할 수 없으므로
    원인을 더 좁게 이름 붙이지 않는다.
    """
    for record in records:
        if record.get("executionStatus"):
            continue
        message = str(record.get("generationError") or "").lower()
        is_timeout = (
            isinstance(exception, TimeoutError)
            or any(
                marker in message
                for marker in ("request timed out", "llm request timed out")
            )
        )
        provider_timeout = (
            "timed out after" in message
            and any(marker in message for marker in ("tofu", "terraform"))
        )
        connection_error = "connection error" in message
        if record.get("generationStatus") == "failed" and provider_timeout:
            record.update(
                executionStatus="censored",
                censorReason="providerOperationTimeout",
                budgetCensored=True,
            )
        elif record.get("generationStatus") == "failed" and is_timeout:
            record.update(
                executionStatus="censored",
                censorReason="llmResponseCompletionTimeout",
            )
        elif record.get("generationStatus") == "failed" and connection_error:
            record.update(
                executionStatus="infrastructureFailure",
                infrastructureReason="llmTransportError",
            )


def _artifact_roots(artifact_root: Path) -> tuple[Path, ...]:
    roots = [artifact_root]
    if artifact_root.resolve() != DEFAULT_ARTIFACT_ROOT.resolve():
        roots.append(DEFAULT_ARTIFACT_ROOT)
    return tuple(roots)


def _resolve_run_dir(artifact_root: Path, run_id: str) -> Path:
    for root in _artifact_roots(artifact_root):
        candidate = root / run_id
        if candidate.is_dir():
            return candidate
    return artifact_root / run_id


def _find_created_run(job: Job, artifact_root: Path, started_at: float) -> str | None:
    if job.arm.startswith("easydep-"):
        system, variant = "easydep", job.arm.removeprefix("easydep-")
    else:
        system, variant = job.arm.rsplit("-", 1)
    paths = [path for root in _artifact_roots(artifact_root) for path in root.glob("*")]
    for path in sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True):
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
    if not job.arm.startswith("easydep-"):
        return None
    store_path = ROOT / ".easydep" / "orchestration" / "runs.sqlite3"
    if not store_path.is_file():
        return None
    updated_after = datetime.fromtimestamp(started_at, UTC).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with sqlite3.connect(store_path, timeout=5) as connection:
            rows = connection.execute(
                "SELECT run_id, state_json FROM runs WHERE updated_at >= ? "
                "ORDER BY updated_at DESC",
                (updated_after,),
            ).fetchall()
    except (OSError, sqlite3.Error):
        return None
    expected_variant = job.arm.removeprefix("easydep-")
    for run_id, encoded in rows:
        try:
            request = json.loads(encoded).get("request") or {}
        except (TypeError, json.JSONDecodeError):
            continue
        if (
            request.get("case_id") == job.case_id
            and request.get("variant") == expected_variant
        ):
            return str(run_id)
    return None


def _run_isolated(
    job: Job,
    *,
    artifact_root: Path,
    run_tools: bool,
    timeout_seconds: int,
    enable_stall_probe: bool = False,
    max_checkpoint_repairs: int = 0,
    approve_member_implementation: bool = False,
) -> dict[str, Any]:
    worker_root = ROOT / ".easydep" / "experiment-workers"
    worker_root.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    job_path = worker_root / f"{token}-job.json"
    result_path = worker_root / f"{token}-result.json"
    log_root = artifact_root / "worker-logs"
    log_root.mkdir(parents=True, exist_ok=True)
    safe_job_key = job.key.replace(":", "-")
    log_path = log_root / f"{safe_job_key}-{token}.log"
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
    environment.update({
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "EASYDEP_EXPERIMENT_SESSION": token,
        "EASYDEP_MAX_CHECKPOINT_REPAIRS": str(max_checkpoint_repairs),
    })
    # 모델이나 게이트웨이가 종료 신호를 보내지 않는 구조화 스트림 하나가
    # 전체 실험 일정을 점유하지 않도록 실험 실행에는 명시적인 출력 상한을 둔다.
    # 검증된 환경별 값이 이미 지정된 경우에는 그 값을 그대로 보존한다.
    environment.setdefault(
        "LLM_MAX_COMPLETION_TOKENS",
        str(DEFAULT_EXPERIMENT_MAX_COMPLETION_TOKENS),
    )
    if approve_member_implementation:
        environment["EASYDEP_APPROVE_MEMBER_IMPLEMENTATION"] = "1"
    if enable_stall_probe:
        environment.setdefault("EASYDEP_LLM_STALL_PROBE_AFTER_SECONDS", "120")
        environment.setdefault("EASYDEP_LLM_STALL_PROBE_TIMEOUT_SECONDS", "60")
    started_at = time.time()
    try:
        with log_path.open("w", encoding="utf-8", buffering=1) as worker_log:
            worker_log.write(json.dumps({
                "event": "workerStarted",
                "job": job.key,
                "startedAtEpoch": started_at,
                "timeoutSeconds": timeout_seconds,
                "maxCompletionTokens": int(
                    environment["LLM_MAX_COMPLETION_TOKENS"]
                ),
            }, ensure_ascii=False) + "\n")
            completed = run_process_tree(
                command,
                cwd=ROOT,
                env=environment,
                stdout=worker_log,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
            worker_log.write(json.dumps({
                "event": "workerExited",
                "returnCode": completed.returncode,
                "elapsedSeconds": time.time() - started_at,
            }, ensure_ascii=False) + "\n")
        if result_path.is_file():
            record = json.loads(result_path.read_text(encoding="utf-8"))
        else:
            record = {
                "job": job.key,
                "status": "failed",
                "runId": _find_created_run(job, artifact_root, started_at),
                "error": f"worker exited {completed.returncode} without a result",
            }
        record["workerLog"] = log_path.relative_to(artifact_root).as_posix()
        return record
    except subprocess.TimeoutExpired:
        cleanup_started = time.time()
        cleaned_runners = _stop_experiment_member_runners(token)
        cleanup_seconds = time.time() - cleanup_started
        run_id = _find_created_run(job, artifact_root, started_at)
        record = {
            "job": job.key,
            "status": "timeout",
            "runId": run_id,
            "error": f"wall-clock limit exceeded: {timeout_seconds}s",
            "elapsedSeconds": float(timeout_seconds) + cleanup_seconds,
            "workerLog": log_path.relative_to(artifact_root).as_posix(),
            "cleanedMemberRunners": cleaned_runners,
        } | censored(
            phase="create",
            reason="measurementWallClock",
            elapsed_seconds=float(timeout_seconds),
        )
        record["cleanupSeconds"] = cleanup_seconds
        if run_id:
            write_json(
                _resolve_run_dir(artifact_root, run_id) / "experiment-timeout.json",
                record,
            )
        return record
    finally:
        job_path.unlink(missing_ok=True)
        result_path.unlink(missing_ok=True)


def _stop_experiment_member_runners(session_id: str) -> list[str]:
    """시간이 검열된 한 실험 세션의 멤버 runner만 정리한다."""
    try:
        query = subprocess.run(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                "label=easydep.owner=member-runner",
                "--filter",
                f"label=easydep.experiment-session={session_id}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    container_ids = [line.strip() for line in query.stdout.splitlines() if line.strip()]
    if container_ids:
        try:
            subprocess.run(
                ["docker", "stop", "--timeout", "10", *container_ids],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    return container_ids


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
    repetitions: set[int] | None = None,
    confirmatory: bool = False,
    study: str = "system",
    retry_failed_checkpoints: bool = False,
    max_checkpoint_repairs: int = 0,
    approve_member_implementation: bool = False,
) -> Path:
    artifact_root.mkdir(parents=True, exist_ok=True)
    research_lock = None
    if split == "holdout" or confirmatory:
        from evaluation.research_protocol.commands.readiness import readiness

        research_lock = readiness()
        if not research_lock["ready"]:
            kinds = ", ".join(item["kind"] for item in research_lock["blockers"])
            raise RuntimeError(f"confirmatory research preflight failed: {kinds}")
    if study == "system":
        suite_path = SUITE_PATH
        required_arms = SYSTEM_ARMS
    elif study == "ablation":
        suite_path = ABLATION_SUITE_PATH
        required_arms = ABLATION_ARMS
    elif study == "component":
        suite_path = COMPONENT_SUITE_PATH
        required_arms = COMPONENT_ARMS
    else:
        raise ValueError("study must be system, ablation, or component")
    suite = ExperimentSuite.load(suite_path, expected_arms=required_arms)
    schedule = select_jobs(
        build_schedule(suite, split, seed()),
        arms=arms,
        cases=cases,
        repetitions=repetitions,
    )
    result_stem = _result_stem(
        split, arms=arms, cases=cases, repetitions=repetitions
    )
    if study != "system":
        result_stem = f"{study}-{result_stem}"
    index_path = artifact_root / f"{result_stem}.json"
    index = (
        json.loads(index_path.read_text(encoding="utf-8"))
        if resume and index_path.is_file()
        else {
            "schemaVersion": "easydep-experiment-index/v1",
            "study": study,
            "split": split,
            "seed": seed(),
            "model": model(),
            "confirmatory": confirmatory or split == "holdout",
            "researchLock": research_lock,
            "suiteSha256": canonical_json_sha256(suite_path),
            "oracleSha256": canonical_json_sha256(suite.oracle),
            "jobs": [asdict(job) | {"key": job.key} for job in schedule],
            "results": [],
        }
    )
    latest = {item["job"]: item for item in index["results"]}
    done = set()
    for key, item in latest.items():
        status = item.get("status")
        retryable = (
            retry_failed_checkpoints and status == "failed"
            and bool(item.get("runId")) and key.startswith("easydep-")
        )
        if status in {"completed", "timeout"} or (status == "failed" and not retryable):
            done.add(key)
    pending = [job for job in schedule if job.key not in done]
    pending = limit_jobs(pending, limit)
    for job in pending:
        free_bytes = shutil.disk_usage(artifact_root.resolve().anchor).free
        if free_bytes < MIN_FREE_DISK_BYTES:
            raise RuntimeError(
                f"local resource preflight failed: free disk {free_bytes} bytes is below "
                f"{MIN_FREE_DISK_BYTES} bytes"
            )
        existing = latest.get(job.key)
        resume_run_id = None
        if existing and retry_failed_checkpoints:
            if existing.get("status") == "failed" and existing.get("runId"):
                resume_run_id = str(existing["runId"])
            elif existing.get("status") == "running" and existing.get("resumeRunId"):
                # A terminated controller can leave a worker attempt marked running.
                # Preserve the explicitly selected checkpoint instead of silently
                # creating a new orchestration run from requirements.
                resume_run_id = str(existing["resumeRunId"])
        execution_job = replace(job, resume_run_id=resume_run_id)
        if existing is not None and existing.get("status") in {"running", "failed"}:
            history = list(existing.get("attemptHistory") or [])
            history.append({
                key: value for key, value in existing.items()
                if key != "attemptHistory"
            })
            existing.clear()
            existing.update({
                "job": job.key, "status": "running", "attemptHistory": history,
                "resumeRunId": resume_run_id,
            })
            slot = existing
        else:
            slot = {"job": job.key, "status": "running"}
            index["results"].append(slot)
            latest[job.key] = slot
        write_json(index_path, index)
        result = _run_isolated(
            execution_job,
            artifact_root=artifact_root,
            run_tools=run_tools,
            timeout_seconds=timeout_seconds,
            enable_stall_probe=not (confirmatory or split == "holdout"),
            max_checkpoint_repairs=max_checkpoint_repairs,
            approve_member_implementation=approve_member_implementation,
        )
        history = slot.get("attemptHistory")
        slot.clear()
        slot.update(result)
        if history:
            slot["attemptHistory"] = history
        write_json(index_path, index)
    refresh_completed_records(index["results"], artifact_root)
    refresh_execution_classification(index["results"])
    write_json(index_path, index)
    summary = aggregate(index["results"], artifact_root, suite)
    write_json(artifact_root / f"summary-{result_stem.removeprefix('experiment-')}.json", summary)
    return index_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the controlled VM benchmark")
    parser.add_argument("--split", choices=("development", "holdout"))
    parser.add_argument(
        "--study", choices=("system", "ablation", "component"), default="system"
    )
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--retry-failed-checkpoints", action="store_true",
        help="실패한 EasyDep run을 같은 implementation/testing checkpoint에서 재개합니다.",
    )
    parser.add_argument(
        "--max-checkpoint-repairs", type=int, default=0,
        help="각 EasyDep 셀에서 같은 run의 실패 소유 작업부터 재개할 최대 횟수입니다.",
    )
    parser.add_argument(
        "--approve-member-implementation", action="store_true",
        help="멤버 OpenHands workflow의 현재 run 외부 전송을 한 번에 승인합니다.",
    )
    parser.add_argument("--skip-tools", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--timeout-seconds", type=int, default=DEFAULT_JOB_TIMEOUT_SECONDS
    )
    parser.add_argument("--print-schedule", action="store_true")
    parser.add_argument("--check-environment", action="store_true")
    parser.add_argument(
        "--confirmatory", action="store_true",
        help="동결된 연구 준비도 검사를 통과한 실행만 허용합니다.",
    )
    parser.add_argument("--arm", action="append", choices=(
        "easydep-full", "cot-standard", "metagpt-standard", "chatdev-standard",
        "easydep-no-depkb", "easydep-no-verification",
    ))
    parser.add_argument("--case", action="append")
    parser.add_argument("--repetition", action="append", type=int)
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
    suite_path = {
        "system": SUITE_PATH,
        "ablation": ABLATION_SUITE_PATH,
        "component": COMPONENT_SUITE_PATH,
    }[args.study]
    required_arms = {
        "system": SYSTEM_ARMS,
        "ablation": ABLATION_ARMS,
        "component": COMPONENT_ARMS,
    }[args.study]
    suite = ExperimentSuite.load(suite_path, expected_arms=required_arms)
    if args.print_schedule:
        schedule = select_jobs(
            build_schedule(suite, args.split, seed()),
            arms=set(args.arm or []),
            cases=set(args.case or []),
            repetitions=set(args.repetition or []),
        )
        try:
            schedule = limit_jobs(schedule, args.limit)
        except ValueError as error:
            parser.error(str(error))
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
        repetitions=set(args.repetition or []),
        confirmatory=args.confirmatory,
        study=args.study,
        retry_failed_checkpoints=args.retry_failed_checkpoints,
        max_checkpoint_repairs=args.max_checkpoint_repairs,
        approve_member_implementation=args.approve_member_implementation,
    ))


if __name__ == "__main__":
    main()
