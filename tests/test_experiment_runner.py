import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import evaluation.experiment as experiment
from app.core.orchestration.contracts import ProviderKind, StepStatus
from evaluation.baselines.common import (
    BUILD_COMPLETENESS_CONTRACT,
    ExperimentCase,
    ExperimentSuite,
)
from evaluation.baselines.cot import SYSTEM as COT_SYSTEM
from evaluation.baselines.metagpt import _task
from evaluation.experiment import (
    Job,
    _result_stem,
    _run_isolated,
    aggregate,
    build_schedule,
    environment_report,
    refresh_completed_records,
    select_jobs,
)


def test_default_timeout_exceeds_verified_easydep_pilot_path():
    assert experiment.DEFAULT_JOB_TIMEOUT_SECONDS == 7200
    assert experiment.DEFAULT_JOB_TIMEOUT_SECONDS > 4635


def test_schedule_is_reproducible_and_balanced():
    suite = ExperimentSuite.load(Path("evaluation/baselines/cases/suite.json"))

    first = build_schedule(suite, "development", 42)
    second = build_schedule(suite, "development", 42)

    assert first == second
    assert len(first) == 9 * 3 * 3
    assert len({job.key for job in first}) == len(first)
    assert {job.arm for job in first} == set(suite.arms)


def test_holdout_uses_three_domains_and_three_provider_assignments():
    suite = ExperimentSuite.load(Path("evaluation/baselines/cases/suite.json"))
    jobs = build_schedule(suite, "holdout", 42)

    assert len(jobs) == 3 * 3 * 3
    assert {job.case_id for job in jobs} == {"H1-azure", "H2-gcp", "H3-aws"}


def test_baselines_share_the_same_build_completeness_contract():
    case = ExperimentCase.load(
        Path("evaluation/baselines/cases/p1-stateless-aws.json")
    )

    assert BUILD_COMPLETENESS_CONTRACT in COT_SYSTEM
    assert BUILD_COMPLETENESS_CONTRACT in _task(case)


def test_aggregate_keeps_failures_and_reports_distributions(tmp_path):
    run = tmp_path / "run-1"
    run.mkdir()
    (run / "evaluation.json").write_text(json.dumps({
        "experimentEligible": True,
        "repository": {
            "implementationComplete": False,
            "markdownContaminatedFiles": ["build.gradle"],
        },
        "score": {"passRate": 0.75, "unknown": 2},
        "codeQuality": {
            "complexity": {
                "status": "available",
                "cyclomaticComplexity": {
                    "mean": 2,
                    "p95": 3.5,
                    "max": 4,
                    "functionsAbove10Ratio": 0.25,
                },
                "decisionPointDensityPer100Nloc": 7.5,
            },
            "coverage": {
                "status": "available",
                "counters": {
                    "branch": {"ratio": 0.75},
                    "complexity": {"ratio": 0.6},
                },
            },
        },
        "externalTools": {"container": {"status": "passed"}},
    }), encoding="utf-8")
    (run / "manifest.json").write_text(
        json.dumps({"elapsedSeconds": 51.4}), encoding="utf-8"
    )
    records = [
        {"job": "cot-standard:P1-aws:r1", "status": "completed", "runId": "run-1"},
        {"job": "cot-standard:P1-aws:r2", "status": "failed", "runId": None},
    ]

    summary = aggregate(records, tmp_path)

    arm = summary["arms"]["cot-standard"]
    assert arm["scheduled"] == 2
    assert arm["completed"] == 1
    assert arm["failed"] == 1
    assert arm["experimentEligible"] == 1
    assert arm["implementationCompleteRuns"] == 0
    assert arm["markdownContaminatedRuns"] == 1
    assert arm["semanticUnknownChecks"] == 2
    assert arm["elapsedSeconds"]["mean"] == 51.4
    assert arm["semanticPassRate"]["mean"] == 0.75
    assert arm["codeQuality"]["cyclomaticComplexity"]["p95"]["mean"] == 3.5
    assert arm["codeQuality"]["decisionPointDensityPer100Nloc"]["mean"] == 7.5
    assert arm["codeQuality"]["coverage"]["branchRatio"]["mean"] == 0.75
    assert arm["codeQuality"]["coverage"]["missingRuns"] == 0
    assert arm["containerFunctionalPassRate"]["mean"] == 1.0


def test_re_evaluation_refreshes_cached_index_eligibility(tmp_path):
    run = tmp_path / "run-1"
    run.mkdir()
    (run / "evaluation.json").write_text(
        json.dumps({"experimentEligible": True}), encoding="utf-8"
    )
    records = [{
        "job": "cot-standard:P1-gcp:r1",
        "status": "completed",
        "runId": "run-1",
        "experimentEligible": False,
    }]

    refresh_completed_records(records, tmp_path)

    assert records[0]["experimentEligible"] is True


def test_saved_re_evaluation_does_not_rewrite_original_generation_status(tmp_path):
    run = tmp_path / "run-1"
    run.mkdir()
    (run / "evaluation.json").write_text(
        json.dumps({"experimentEligible": False}), encoding="utf-8"
    )
    records = [{
        "job": "metagpt-standard:P1-gcp:r1",
        "status": "failed",
        "runId": "run-1",
        "error": "AttributeError: evaluator defect",
    }]

    refresh_completed_records(records, tmp_path)

    assert records[0]["status"] == "failed"
    assert records[0]["evaluationStatus"] == "completed"
    assert records[0]["evaluation"] == "evaluation.json"
    assert records[0]["experimentEligible"] is False


def test_run_job_separates_evaluator_failure_from_generation_failure(tmp_path, monkeypatch):
    run = tmp_path / "cot-standard-p1-gcp-test"
    (run / "repo").mkdir(parents=True)
    (run / "manifest.json").write_text(
        json.dumps({"status": "completed", "generationStatus": "completed"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(experiment, "_run_arm", lambda *_args: run)
    monkeypatch.setattr(
        experiment,
        "evaluate_repository",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("evaluator defect")),
    )

    result = experiment.run_job(
        Job("cot-standard", "case.json", "P1-gcp", 1),
        artifact_root=tmp_path,
    )

    assert result["status"] == "completed"
    assert result["generationStatus"] == "completed"
    assert result["evaluationStatus"] == "failed"
    assert "evaluator defect" in result["evaluationError"]
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["evaluationStatus"] == "failed"
    assert manifest["elapsedSecondsTotal"] >= manifest["elapsedSecondsGeneration"]
    assert manifest["elapsedSecondsTotal"] >= manifest["elapsedSecondsEvaluation"]


def test_run_job_records_generation_evaluation_and_total_time(tmp_path, monkeypatch):
    run = tmp_path / "cot-standard-p1-gcp-test"
    (run / "repo").mkdir(parents=True)
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    ticks = iter((10.0, 12.0, 12.0, 17.0, 17.0))
    monkeypatch.setattr(experiment.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(experiment, "_run_arm", lambda *_args: run)
    monkeypatch.setattr(
        experiment,
        "evaluate_repository",
        lambda *_args, **_kwargs: {"experimentEligible": True},
    )
    monkeypatch.setattr(experiment, "write_evaluation", lambda *_args: None)

    result = experiment.run_job(
        Job("cot-standard", "case.json", "P1-gcp", 1),
        artifact_root=tmp_path,
    )

    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert result["elapsedSeconds"] == 7.0
    assert manifest["elapsedSecondsGeneration"] == 2.0
    assert manifest["elapsedSecondsEvaluation"] == 5.0
    assert manifest["elapsedSecondsTotal"] == 7.0


def test_run_job_records_generation_failure_without_evaluation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        experiment,
        "_run_arm",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("generation defect")),
    )
    monkeypatch.setattr(experiment, "_find_created_run", lambda *_args: "partial-run")

    result = experiment.run_job(
        Job("cot-standard", "case.json", "P1-gcp", 1),
        artifact_root=tmp_path,
    )

    assert result["status"] == "failed"
    assert result["generationStatus"] == "failed"
    assert result["evaluationStatus"] == "not-run"
    assert result["runId"] == "partial-run"
    assert "generation defect" in result["generationError"]


def test_job_key_includes_repetition():
    assert Job("easydep-full", "case.json", "P1-aws", 3).key == "easydep-full:P1-aws:r3"


def test_job_selection_keeps_frozen_order_and_repetitions():
    suite = ExperimentSuite.load(Path("evaluation/baselines/cases/suite.json"))
    schedule = build_schedule(suite, "development", 42)

    selected = select_jobs(
        schedule, arms={"easydep-full"}, cases={"P1-gcp"}
    )

    assert selected == [
        job for job in schedule
        if job.arm == "easydep-full" and job.case_id == "P1-gcp"
    ]
    assert {job.repetition for job in selected} == {1, 2, 3}


def test_job_selection_rejects_names_outside_the_split():
    suite = ExperimentSuite.load(Path("evaluation/baselines/cases/suite.json"))
    schedule = build_schedule(suite, "development", 42)

    with pytest.raises(ValueError, match="selected split"):
        select_jobs(schedule, cases={"H1-azure"})


def test_selected_pilot_has_an_index_separate_from_the_full_experiment():
    assert _result_stem("development") == "experiment-development"
    assert _result_stem(
        "development", arms={"easydep-full"}, cases={"P1-gcp"}
    ) == "experiment-development-easydep-full-P1-gcp"


def test_environment_report_never_exposes_the_api_key(monkeypatch):
    import evaluation.implementation as implementation

    monkeypatch.setenv("API_KEY", "secret-value")
    monkeypatch.setattr(
        implementation,
        "_tool_path",
        lambda name, _environment_name: f"C:/tools/{name}.exe",
    )
    monkeypatch.setattr(
        implementation,
        "_command",
        lambda *_args, **_kwargs: {"status": "passed"},
    )
    monkeypatch.setattr(experiment.shutil, "which", lambda name: f"C:/tools/{name}.exe")

    report = environment_report()

    assert report["configuration"]["apiKeyPresent"] is True
    assert "secret-value" not in json.dumps(report)


def test_easydep_experiment_explicitly_selects_the_llm_scaffold(tmp_path, monkeypatch):
    captured = []

    import app.core.orchestration as orchestration

    fake_result = SimpleNamespace(
        status=StepStatus.COMPLETED,
        run_id="easydep-full-test",
        state={},
    )
    monkeypatch.setattr(
        orchestration,
        "run_batch",
        lambda request: captured.append(request) or fake_result,
    )
    monkeypatch.setattr(experiment, "DEFAULT_ARTIFACT_ROOT", tmp_path)

    result = experiment._easydep(
        Path("evaluation/baselines/cases/p1-stateless-gcp.json")
    )

    assert result == tmp_path / "easydep-full-test"
    assert captured[0].providers.implementation_scaffold == ProviderKind.LLM


def test_isolated_timeout_is_preserved_with_discovered_run(tmp_path, monkeypatch):
    run = tmp_path / "easydep-full-p1"
    run.mkdir()
    (run / "manifest.json").write_text(json.dumps({
        "system": "easydep", "variant": "full", "caseId": "P1-aws"
    }), encoding="utf-8")
    monkeypatch.setattr(experiment, "ROOT", tmp_path)
    monkeypatch.setattr(experiment.time, "time", lambda: run.stat().st_mtime - 1)
    def timeout(*_args, **kwargs):
        assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
        assert kwargs["env"]["PYTHONUTF8"] == "1"
        raise subprocess.TimeoutExpired("worker", 1)

    monkeypatch.setattr(experiment, "run_process_tree", timeout)

    result = _run_isolated(
        Job("easydep-full", "case.json", "P1-aws", 1),
        artifact_root=tmp_path,
        run_tools=False,
        timeout_seconds=1,
    )

    assert result["status"] == "timeout"
    assert result["elapsedSeconds"] == 1.0
    assert result["runId"] == run.name
    assert (run / "experiment-timeout.json").is_file()
