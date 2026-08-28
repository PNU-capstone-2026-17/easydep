"""실제 provider를 사용하는 bounded E1 클래스 설계 최적화 실행기다.

오프라인 후보 판정은 ``class_design_optimization.py``가 담당한다. 이 모듈은 동결된 E1
시나리오를 직접 생성하며 cold LLM 실행은 정확히 9개 cell을 넘지 않는다. 마지막 후보의
warm 검증은 같은 accepted-unit cache를 읽기만 하며 physical provider 호출을 허용하지
않는다.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.config import settings
from app.design.schemas.class_model import BCEModel
from app.design.services.class_diagram.cache import (
    AcceptedUnitCacheMiss,
    ProcessLocalAcceptedUnitCache,
)
from app.design.services.class_diagram.plantuml import generate_plantuml_from_bce_json
from app.design.services.class_diagram.service import generate_class_model
from app.design.services.common.structured import StructuredLlmError, capture_llm_timings
from app.design.services.common.validation import validate_puml_artifact
from app.design.services.sequence_diagram.plantuml import generate_sequence_from_model
from app.design.services.sequence_diagram.projection import project_sequence_model
from app.design.validation import design_readiness_report
from evaluation.class_design_evaluation import (
    CASE_ID,
    evaluate_candidate,
    frozen_e1_scenario_index,
)

SCHEMA_VERSION = "easydep-class-design-live-optimization/v1"
OUTPUT_TIERS = (2048, 4096, 8192, 16384)
MAX_COLD_GENERATIONS = 9
_COLD_CELL_ORDER = (
    "baseline-1",
    "baseline-2",
    "baseline-3",
    "compact",
    "call-plan-low",
    "operation-low",
    "candidate-1",
    "candidate-2",
    "candidate-3",
)


@contextmanager
def _override_settings(values: Mapping[str, Any]) -> Iterator[None]:
    previous = {name: getattr(settings, name) for name in values}
    try:
        for name, value in values.items():
            setattr(settings, name, value)
        yield
    finally:
        for name, value in previous.items():
            setattr(settings, name, value)


def _physical_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event.get("physicalRequest", True) is not False]


def _stage(operation: str) -> str | None:
    folded = operation.casefold()
    if folded.startswith("interactioninventory"):
        return "inventory"
    if folded.startswith("interactionoperation"):
        return "operation"
    if folded.startswith("interactioncallplan"):
        return "callPlan"
    return None


def _event_metrics(events: list[dict[str, Any]], wall_seconds: float) -> dict[str, Any]:
    physical = _physical_events(events)
    input_tokens = sum(int(event.get("inputTokens") or 0) for event in physical)
    output_tokens = sum(int(event.get("outputTokens") or 0) for event in physical)
    total_tokens = sum(
        int(event.get("totalTokens") or 0)
        or int(event.get("inputTokens") or 0) + int(event.get("outputTokens") or 0)
        for event in physical
    )
    stage_output_max = {"inventory": 0, "operation": 0, "callPlan": 0}
    stage_length_or_schema_failure = dict.fromkeys(stage_output_max, False)
    for event in physical:
        stage = _stage(str(event.get("operation") or ""))
        if stage is None:
            continue
        stage_output_max[stage] = max(
            stage_output_max[stage], int(event.get("outputTokens") or 0)
        )
        finish_reasons = {str(item).casefold() for item in event.get("finishReasons") or []}
        if (
            "length" in finish_reasons
            or event.get("failureCategory") == "schema_validation"
            or event.get("status") == "failed"
        ):
            stage_length_or_schema_failure[stage] = True
    repairs = sum(
        event.get("repairKind") in {"schema", "semantic"}
        or "repair" in str(event.get("operation") or "").casefold()
        for event in physical
    )
    handoffs = sum(bool(event.get("handoff")) for event in physical)
    return {
        "physicalLlmCalls": len(physical),
        "logicalCacheEvents": len(events) - len(physical),
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": total_tokens,
        "wallSeconds": round(wall_seconds, 6),
        "repairs": repairs,
        "handoffs": handoffs,
        "stageOutputMax": stage_output_max,
        "stageLengthOrSchemaFailure": stage_length_or_schema_failure,
    }


def _machine_gates(
    index,
    model: BCEModel,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = model.model_dump(by_alias=True)
    class_puml = generate_plantuml_from_bce_json(payload)
    sequence = project_sequence_model(index, model, class_puml)
    sequence_payload = sequence.model_dump()
    sequence_puml = generate_sequence_from_model(sequence_payload)
    evaluation = evaluate_candidate(payload, sequence_model=sequence_payload)
    state = {
        "usecase_spec": index.raw,
        "relationships": index.raw.get("relationships") or {},
        "extracted_bce_classes": payload,
        "class_diagram_puml": class_puml,
        "sequence_diagram_model": sequence_payload,
        "sequence_diagram_puml": sequence_puml,
    }
    readiness = design_readiness_report(state, ("class_diagram", "sequence_diagram"))
    syntax = {
        "class": validate_puml_artifact(class_puml),
        "sequence": validate_puml_artifact(sequence_puml),
    }
    passed = (
        evaluation.get("status") == "passed"
        and readiness.get("status") == "READY"
        and all(item.get("syntax_valid") for item in syntax.values())
    )
    gates = {
        "status": "passed" if passed else "failed",
        "evaluation": evaluation,
        "readiness": readiness,
        "plantuml": syntax,
    }
    artifacts = {
        "classModel": payload,
        "classPuml": class_puml,
        "sequenceModel": sequence_payload,
        "sequencePuml": sequence_puml,
    }
    return gates, artifacts


def _run_cell(
    key: str,
    treatment: str,
    overrides: Mapping[str, Any],
    *,
    cache: ProcessLocalAcceptedUnitCache,
    generator: Callable[..., BCEModel],
    run_id_factory: Callable[[str], str],
) -> dict[str, Any]:
    run_id = run_id_factory(key)
    index = frozen_e1_scenario_index()
    configured = {**dict(overrides), "llm_max_retries": 0, "easydep_experiment_session": run_id}
    started = time.perf_counter()
    try:
        with _override_settings(configured), capture_llm_timings() as events:
            model = generator(index, cache=cache)
    except (StructuredLlmError, ValueError) as error:
        wall_seconds = time.perf_counter() - started
        return {
            "runId": run_id,
            "cell": key,
            "treatment": treatment,
            "settings": dict(overrides),
            "metrics": _event_metrics(events, wall_seconds),
            "timingEvents": list(events),
            "machineGates": {
                "status": "failed",
                "generation": {
                    "status": "failed",
                    "errorType": type(error).__name__,
                    "message": " ".join(str(error).split())[:4000],
                },
            },
            "artifacts": {},
            "status": "failed",
        }
    wall_seconds = time.perf_counter() - started
    gates, artifacts = _machine_gates(index, model)
    return {
        "runId": run_id,
        "cell": key,
        "treatment": treatment,
        "settings": dict(overrides),
        "metrics": _event_metrics(events, wall_seconds),
        "timingEvents": list(events),
        "machineGates": gates,
        "artifacts": artifacts,
        "status": gates["status"],
    }


def _median(runs: list[dict[str, Any]], field: str) -> float:
    measured = [
        run for run in runs
        if run["metrics"].get("measurementStatus")
        != "unavailable-after-process-exit"
    ]
    if not measured:
        return 0.0
    return float(statistics.median(float(run["metrics"][field]) for run in measured))


def _p95(runs: list[dict[str, Any]], field: str) -> float:
    values = sorted(
        float(run["metrics"][field])
        for run in runs
        if run["metrics"].get("measurementStatus")
        != "unavailable-after-process-exit"
    )
    if not values:
        return 0.0
    return values[max(0, math.ceil(len(values) * 0.95) - 1)]


def _next_tier(value: float, existing: int) -> int:
    for tier in OUTPUT_TIERS:
        if value <= tier:
            return tier
    return existing


def _candidate_caps(
    baselines: list[dict[str, Any]], *, existing: Mapping[str, int]
) -> dict[str, int]:
    result: dict[str, int] = {}
    for stage, current in existing.items():
        failed = any(
            run["metrics"]["stageLengthOrSchemaFailure"][stage] for run in baselines
        )
        observed = max(run["metrics"]["stageOutputMax"][stage] for run in baselines)
        result[stage] = current if failed or observed == 0 else _next_tier(observed * 1.5, current)
    return result


def _default_run_id(key: str) -> str:
    return f"class-opt-{CASE_ID}-{key}-{uuid.uuid4().hex[:12]}"


def _baseline_settings() -> dict[str, Any]:
    return {
        "design_class_compact_operation_payload": False,
        "design_class_inventory_reasoning_effort": "medium",
        "design_class_operation_reasoning_effort": "medium",
        "design_class_call_plan_reasoning_effort": "medium",
        "design_class_inventory_max_completion_tokens": 16384,
        "design_class_operation_max_completion_tokens": 8192,
        "design_class_call_plan_max_completion_tokens": 8192,
    }


def execute_live_e1(
    *,
    generator: Callable[..., BCEModel] = generate_class_model,
    run_id_factory: Callable[[str], str] = _default_run_id,
    cell_runner: Callable[..., dict[str, Any]] = _run_cell,
    resume_report: Mapping[str, Any] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run bounded cold cells and one zero-provider warm cache verification.

    ``resume_report`` may contain a successfully persisted prefix. A report with an
    ``inFlight`` cell is rejected because the process cannot prove whether that
    physical request reached the provider; repeating it could exceed the cold-call
    budget.
    """

    baseline_settings = _baseline_settings()
    if resume_report is not None:
        terminal = _validate_resume_report(resume_report)
        if terminal:
            return deepcopy(dict(resume_report))
        resume_runs = deepcopy(list(resume_report["runs"]))
    else:
        resume_runs = []
    runs: list[dict[str, Any]] = []

    def checkpoint(*, in_flight: str | None = None) -> None:
        if progress is None:
            return
        progress(
            _report(
                runs,
                stopped_at=None,
                decision={"adopted": False, "status": "in_progress"},
                status="in_progress",
                in_flight=in_flight,
            )
        )

    def cold(
        key: str,
        treatment: str,
        overrides: Mapping[str, Any],
        *,
        cache: ProcessLocalAcceptedUnitCache | None = None,
        persist_after: bool = True,
    ) -> dict[str, Any]:
        position = len(runs)
        if position < len(resume_runs):
            existing = resume_runs[position]
            _validate_reused_cell(existing, key, treatment, overrides)
            runs.append(existing)
            return existing
        if len(runs) >= MAX_COLD_GENERATIONS:
            raise RuntimeError("frozen E1 cold generation budget exceeded")
        checkpoint(in_flight=key)
        run = cell_runner(
            key,
            treatment,
            overrides,
            cache=cache or ProcessLocalAcceptedUnitCache(capacity=256),
            generator=generator,
            run_id_factory=run_id_factory,
        )
        runs.append(run)
        if persist_after:
            checkpoint()
        return run

    baselines = [cold(f"baseline-{index}", "baseline", baseline_settings) for index in range(1, 4)]

    compact = cold(
        "compact",
        "compact",
        {**baseline_settings, "design_class_compact_operation_payload": True},
    )
    call_low = cold(
        "call-plan-low",
        "call-plan-low",
        {**baseline_settings, "design_class_call_plan_reasoning_effort": "low"},
    )
    operation_low = cold(
        "operation-low",
        "operation-low",
        {**baseline_settings, "design_class_operation_reasoning_effort": "low"},
    )

    baseline_input = _median(baselines, "inputTokens")
    compact_reduction = (
        1.0 - float(compact["metrics"]["inputTokens"]) / baseline_input
        if baseline_input
        else 0.0
    )
    baseline_repairs = _median(baselines, "repairs")
    baseline_handoffs = _median(baselines, "handoffs")
    accepted_compact = compact["status"] == "passed" and compact_reduction >= 0.15
    accepted_call_low = (
        call_low["status"] == "passed"
        and call_low["metrics"]["repairs"] <= baseline_repairs
        and call_low["metrics"]["handoffs"] <= baseline_handoffs
    )
    accepted_operation_low = (
        operation_low["status"] == "passed"
        and operation_low["metrics"]["repairs"] <= baseline_repairs
        and operation_low["metrics"]["handoffs"] <= baseline_handoffs
    )
    caps = _candidate_caps(
        baselines,
        existing={
            "inventory": int(baseline_settings[
                "design_class_inventory_max_completion_tokens"
            ]),
            "operation": int(baseline_settings[
                "design_class_operation_max_completion_tokens"
            ]),
            "callPlan": int(baseline_settings[
                "design_class_call_plan_max_completion_tokens"
            ]),
        },
    )
    candidate_settings = {
        **baseline_settings,
        "design_class_compact_operation_payload": accepted_compact,
        "design_class_call_plan_reasoning_effort": "low" if accepted_call_low else "medium",
        "design_class_operation_reasoning_effort": (
            "low" if accepted_operation_low else "medium"
        ),
        "design_class_inventory_max_completion_tokens": caps["inventory"],
        "design_class_operation_max_completion_tokens": caps["operation"],
        "design_class_call_plan_max_completion_tokens": caps["callPlan"],
    }
    token_limit = max(run["metrics"]["totalTokens"] for run in baselines) * 1.25
    candidate_runs: list[dict[str, Any]] = []
    last_cache: ProcessLocalAcceptedUnitCache | None = None
    stopped_at: str | None = None
    for ordinal in range(1, 4):
        last_cache = ProcessLocalAcceptedUnitCache(capacity=256)
        run = cold(
            f"candidate-{ordinal}",
            "candidate",
            candidate_settings,
            cache=last_cache,
            # Candidate 3 and its process-local warm verification are one atomic
            # cell. Persisting candidate 3 alone would make a safe warm resume
            # impossible without another physical generation.
            persist_after=False,
        )
        candidate_runs.append(run)
        if run["metrics"]["totalTokens"] > token_limit:
            run["status"] = "failed"
            run["machineGates"]["tokenBudget"] = {
                "status": "failed",
                "limit": token_limit,
                "observed": run["metrics"]["totalTokens"],
            }
        if ordinal != 3:
            checkpoint()
        if run["status"] != "passed":
            stopped_at = run["cell"]
            break

    warm: dict[str, Any] | None = None
    if stopped_at is None and len(candidate_runs) == 3 and last_cache is not None:
        last_cache.seal()
        try:
            warm = cell_runner(
                "candidate-warm-verification",
                "cache-warm",
                candidate_settings,
                cache=last_cache,
                generator=generator,
                run_id_factory=run_id_factory,
            )
        except AcceptedUnitCacheMiss as error:
            warm = _failed_warm_cache_miss(
                error,
                settings_values=candidate_settings,
                run_id_factory=run_id_factory,
            )
        if warm["metrics"]["physicalLlmCalls"] != 0:
            warm["status"] = "failed"
            stopped_at = warm["cell"]
        elif warm["status"] != "passed":
            stopped_at = warm["cell"]

    decision = _decision(
        baselines,
        candidate_runs,
        compact_reduction=compact_reduction,
        token_limit=token_limit,
        candidate_caps=caps,
        warm=warm,
    )
    report = _report(runs, stopped_at=stopped_at, decision=decision, warm=warm)
    if progress is not None:
        progress(report)
    return report


def _failed_warm_cache_miss(
    error: AcceptedUnitCacheMiss,
    *,
    settings_values: Mapping[str, Any],
    run_id_factory: Callable[[str], str],
) -> dict[str, Any]:
    key = "candidate-warm-verification"
    return {
        "runId": run_id_factory(key),
        "cell": key,
        "treatment": "cache-warm",
        "settings": dict(settings_values),
        "metrics": _event_metrics([], 0.0),
        "timingEvents": [],
        "machineGates": {
            "status": "failed",
            "cacheWarm": {
                "status": "failed",
                "reason": "sealed-cache-miss",
                "cacheKey": error.key,
            },
        },
        "artifacts": {},
        "status": "failed",
    }


def _validate_reused_cell(
    run: Mapping[str, Any],
    key: str,
    treatment: str,
    overrides: Mapping[str, Any],
) -> None:
    if run.get("cell") != key or run.get("treatment") != treatment:
        raise ValueError(f"resume cell does not match {key}")
    if run.get("settings") != dict(overrides):
        raise ValueError(f"resume settings do not match {key}")


def _validate_resume_report(report: Mapping[str, Any]) -> bool:
    if report.get("schemaVersion") != SCHEMA_VERSION or report.get("caseId") != CASE_ID:
        raise ValueError("resume report identity does not match the frozen E1 protocol")
    if report.get("maxColdGenerations") != MAX_COLD_GENERATIONS:
        raise ValueError("resume report has a different cold generation budget")
    if report.get("retryBudget") != 0:
        raise ValueError("resume report has a non-zero retry budget")
    if report.get("inFlight"):
        raise RuntimeError(
            "resume report contains an ambiguous inFlight cell; refusing an "
            "automatic physical retry"
        )
    runs = report.get("runs")
    if not isinstance(runs, list) or len(runs) > MAX_COLD_GENERATIONS:
        raise ValueError("resume report has an invalid cold run list")
    cells = [run.get("cell") for run in runs if isinstance(run, Mapping)]
    if len(cells) != len(runs) or cells != list(_COLD_CELL_ORDER[: len(runs)]):
        raise ValueError("resume report cold cells are not a valid completed prefix")
    if report.get("coldGenerationCount") != len(runs):
        raise ValueError("resume report cold generation count is inconsistent")
    status = report.get("status")
    if (
        status == "stopped"
        and report.get("stoppedAt") == "baseline"
        and len(runs) == 3
    ):
        # v1 initially treated any baseline failure as an experiment-wide stop.
        # The protocol only stops the failed run; singleton treatments remain
        # independent and candidate repetitions stop on their own gate.
        return False
    if status in {"completed", "stopped"}:
        return True
    if status != "in_progress":
        raise ValueError("resume report status is not resumable")
    return False


def _decision(
    baselines: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    compact_reduction: float,
    token_limit: float,
    candidate_caps: Mapping[str, int],
    warm: dict[str, Any] | None,
) -> dict[str, Any]:
    complete = len(candidates) == 3 and all(run["status"] == "passed" for run in candidates)
    baseline_tokens = _median(baselines, "totalTokens")
    candidate_tokens = _median(candidates, "totalTokens") if candidates else float("inf")
    baseline_wall = _median(baselines, "wallSeconds")
    candidate_wall = _median(candidates, "wallSeconds") if candidates else float("inf")
    token_gain = 1.0 - candidate_tokens / baseline_tokens if baseline_tokens else 0.0
    wall_gain = 1.0 - candidate_wall / baseline_wall if baseline_wall else 0.0
    baseline_wall_p95 = _p95(baselines, "wallSeconds")
    p95_ratio = (
        _p95(candidates, "wallSeconds") / baseline_wall_p95
        if candidates and baseline_wall_p95
        else float("inf")
    )
    checks = {
        "allMachineGates": complete,
        "repairMedianNotIncreased": (
            complete and _median(candidates, "repairs") <= _median(baselines, "repairs")
        ),
        "handoffMedianNotIncreased": (
            complete and _median(candidates, "handoffs") <= _median(baselines, "handoffs")
        ),
        "compactInputReductionAtLeast15Percent": compact_reduction >= 0.15,
        "tokenOrWallImprovementAtLeast10Percent": token_gain >= 0.10 or wall_gain >= 0.10,
        "wallP95NotWorseThan10Percent": p95_ratio <= 1.10,
        "warmPhysicalCallsZero": (
            warm is not None and warm["metrics"]["physicalLlmCalls"] == 0
        ),
        # Qualitative review is intentionally offline and must be attached before adoption.
        "qualitativeIssueCountNotIncreased": False,
    }
    return {
        "adopted": all(checks.values()),
        "checks": checks,
        "compactInputReduction": compact_reduction,
        "tokenImprovement": token_gain,
        "wallImprovement": wall_gain,
        "wallP95Ratio": p95_ratio,
        "candidateTokenLimit": token_limit,
        "candidateCaps": dict(candidate_caps),
        "qualitativeReview": "pending",
    }


def apply_qualitative_review(
    report: dict[str, Any], *, baseline_issues: int, candidate_issues: int
) -> dict[str, Any]:
    """Attach an offline rubric review without performing another LLM generation."""

    reviewed = deepcopy(report)
    decision = reviewed["decision"]
    passed = candidate_issues <= baseline_issues
    decision["checks"]["qualitativeIssueCountNotIncreased"] = passed
    decision["qualitativeReview"] = {
        "baselineIssues": baseline_issues,
        "candidateIssues": candidate_issues,
    }
    decision["adopted"] = all(decision["checks"].values())
    return reviewed


def record_failed_baseline_inflight(
    report: Mapping[str, Any], *, error_type: str, error_message: str
) -> dict[str, Any]:
    """Close one observed failed baseline without repeating its provider calls.

    This recovery is intentionally limited to a terminated baseline process. New
    executions record generation failures directly in ``_run_cell``; this adapter
    exists for a checkpoint written by an older runner before that boundary.
    """

    if report.get("schemaVersion") != SCHEMA_VERSION or report.get("caseId") != CASE_ID:
        raise ValueError("failed baseline report identity does not match")
    if report.get("status") != "in_progress" or report.get("retryBudget") != 0:
        raise ValueError("failed baseline report is not recoverable")
    runs = deepcopy(report.get("runs"))
    if not isinstance(runs, list) or report.get("coldGenerationCount") != len(runs):
        raise ValueError("failed baseline report run count is inconsistent")
    cell = report.get("inFlight")
    expected = _COLD_CELL_ORDER[len(runs)] if len(runs) < len(_COLD_CELL_ORDER) else None
    if cell != expected or not isinstance(cell, str) or not cell.startswith("baseline-"):
        raise ValueError("only the next in-flight baseline can be recorded as failed")
    run = {
        "runId": f"recovered-failure:{CASE_ID}:{cell}",
        "cell": cell,
        "treatment": "baseline",
        "settings": _baseline_settings(),
        "metrics": {
            **_event_metrics([], 0.0),
            "measurementStatus": "unavailable-after-process-exit",
        },
        "timingEvents": [],
        "machineGates": {
            "status": "failed",
            "generation": {
                "status": "failed",
                "errorType": error_type,
                "message": " ".join(error_message.split())[:4000],
                "recoveredFromTerminatedProcess": True,
            },
        },
        "artifacts": {},
        "status": "failed",
    }
    runs.append(run)
    return _report(
        runs,
        stopped_at=None,
        decision={"adopted": False, "status": "in_progress"},
        status="in_progress",
    )


def _report(
    runs: list[dict[str, Any]],
    *,
    stopped_at: str | None,
    decision: Mapping[str, Any],
    warm: dict[str, Any] | None = None,
    status: str | None = None,
    in_flight: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "caseId": CASE_ID,
        "maxColdGenerations": MAX_COLD_GENERATIONS,
        "coldGenerationCount": len(runs),
        "retryBudget": 0,
        "status": status or ("stopped" if stopped_at else "completed"),
        "stoppedAt": stopped_at,
        "inFlight": in_flight,
        "runs": runs,
        "warmVerification": warm,
        "decision": dict(decision),
    }


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Atomically replace one experiment report without accumulating temp files."""

    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.easydep-class-opt.tmp")
    try:
        pending.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--review-report", type=Path)
    parser.add_argument("--resume-report", type=Path)
    parser.add_argument("--record-failed-baseline", type=Path)
    parser.add_argument("--failure-type")
    parser.add_argument("--failure-message")
    parser.add_argument("--baseline-issues", type=int, default=0)
    parser.add_argument("--candidate-issues", type=int, default=0)
    args = parser.parse_args(argv)
    selected_modes = sum(bool(value) for value in (
        args.review_report,
        args.resume_report,
        args.record_failed_baseline,
    ))
    if selected_modes > 1:
        parser.error("review, resume, and failed-baseline modes are mutually exclusive")
    if args.record_failed_baseline:
        if not args.failure_type or not args.failure_message:
            parser.error("failed-baseline mode requires --failure-type and --failure-message")
        report = record_failed_baseline_inflight(
            _read_json(args.record_failed_baseline),
            error_type=args.failure_type,
            error_message=args.failure_message,
        )
    elif args.review_report:
        report = apply_qualitative_review(
            _read_json(args.review_report),
            baseline_issues=args.baseline_issues,
            candidate_issues=args.candidate_issues,
        )
    else:
        resume = _read_json(args.resume_report) if args.resume_report else None
        report = execute_live_e1(
            resume_report=resume,
            progress=lambda partial: _write_json(args.output, partial),
        )
    _write_json(args.output, report)
    return 0 if report["decision"].get("adopted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
