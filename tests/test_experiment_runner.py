import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import evaluation.experiment as experiment
from app.orchestration.contracts import ProviderKind, StepStatus
from app.orchestration.store import RunStore
from evaluation.baselines.chatdev import _task as chatdev_task
from evaluation.baselines.common import (
    BUILD_COMPLETENESS_CONTRACT,
    ExperimentCase,
    ExperimentSuite,
    canonical_json_sha256,
)
from evaluation.baselines.cot import SYSTEM as COT_SYSTEM
from evaluation.baselines.metagpt import _task
from evaluation.experiment import (
    Job,
    _find_created_run,
    _result_stem,
    _run_isolated,
    aggregate,
    build_schedule,
    environment_report,
    invalid_cloud_claims,
    limit_jobs,
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
    assert len(first) == 9 * 4 * 3
    assert len({job.key for job in first}) == len(first)
    assert {job.arm for job in first} == set(suite.arms)


def test_ablation_schedule_is_separate_and_balanced():
    suite = ExperimentSuite.load(
        Path("evaluation/baselines/cases/ablation-suite.json"),
        expected_arms=experiment.ABLATION_ARMS,
    )

    jobs = build_schedule(suite, "development", 42)

    assert len(jobs) == 9 * 3 * 3
    assert {job.arm for job in jobs} == experiment.ABLATION_ARMS
    assert "cot-standard" not in {job.arm for job in jobs}


def test_component_schedule_is_paired_and_balanced():
    suite = ExperimentSuite.load(
        Path("evaluation/baselines/component-cases/suite.json"),
        expected_arms=experiment.COMPONENT_ARMS,
    )

    jobs = build_schedule(suite, "development", 42)

    assert suite.study_design == "paired-components"
    assert len(jobs) == 18 * 2 * 3
    assert {job.arm for job in jobs} == experiment.COMPONENT_ARMS
    assert {Path(job.oracle_path).name for job in jobs} == {"oracle.json"}


def test_component_pairs_change_only_the_declared_cloud_requirement():
    root = Path("evaluation/baselines/component-cases")
    for pair in ("ps", "lb", "tls"):
        for provider in ("aws", "azure", "gcp"):
            control = ExperimentCase.load(root / f"{pair}-control-{provider}.json")
            treatment = ExperimentCase.load(root / f"{pair}-treatment-{provider}.json")
            assert control.requirements[0] == treatment.requirements[0]
            assert control.requirements[2] == treatment.requirements[2]
            assert control.cloud_constraints == treatment.cloud_constraints
            assert control.scope["pairId"] == treatment.scope["pairId"]
            assert control.scope["condition"] == "control"
            assert treatment.scope["condition"] == "treatment"


def test_paired_component_suite_requires_both_conditions_per_provider(tmp_path):
    cases = []
    for provider in ("aws", "azure", "gcp"):
        for condition in ("control", "treatment"):
            path = tmp_path / f"storage-{condition}-{provider}.json"
            path.write_text(json.dumps({
                "caseId": f"storage-{condition}-{provider}",
                "requirements": ["Provide the same notes API."],
                "cloudConstraints": f"Deploy to {provider}.",
                "scope": {
                    "providers": [provider],
                    "workload": "docker-on-vm",
                    "pairId": "storage",
                    "condition": condition,
                },
            }), encoding="utf-8")
            cases.append(path)
    oracle = tmp_path / "component-oracle.json"
    oracle.write_text("{}", encoding="utf-8")
    suite_path = tmp_path / "component-suite.json"
    suite_path.write_text(json.dumps({
        "studyDesign": "paired-components",
        "development": [path.name for path in cases],
        "holdout": [],
        "pairs": [{"id": "storage"}],
        "repetitions": 2,
        "arms": ["easydep-full", "easydep-no-depkb"],
        "oracle": oracle.name,
        "oracleHash": canonical_json_sha256(oracle),
        "frozenHashes": {
            path.name: canonical_json_sha256(path) for path in cases
        },
    }), encoding="utf-8")

    suite = ExperimentSuite.load(
        suite_path, expected_arms={"easydep-full", "easydep-no-depkb"}
    )
    jobs = build_schedule(suite, "development", 42)

    assert len(jobs) == 6 * 2 * 2
    assert {job.oracle_path for job in jobs} == {str(oracle)}
    assert build_schedule(suite, "holdout", 42) == []


def test_holdout_uses_three_domains_and_three_provider_assignments():
    suite = ExperimentSuite.load(Path("evaluation/baselines/cases/suite.json"))
    jobs = build_schedule(suite, "holdout", 42)

    assert len(jobs) == 3 * 4 * 3
    assert {job.case_id for job in jobs} == {"H1-azure", "H2-gcp", "H3-aws"}


def test_baselines_share_the_same_build_completeness_contract():
    case = ExperimentCase.load(
        Path("evaluation/baselines/cases/p1-stateless-aws.json")
    )

    assert BUILD_COMPLETENESS_CONTRACT in COT_SYSTEM
    assert BUILD_COMPLETENESS_CONTRACT in _task(case)
    assert BUILD_COMPLETENESS_CONTRACT in chatdev_task(case)
    assert "Never place Markdown headings" in BUILD_COMPLETENESS_CONTRACT


def test_aggregate_keeps_failures_and_reports_distributions(tmp_path):
    run = tmp_path / "run-1"
    run.mkdir()
    (run / "evaluation.json").write_text(json.dumps({
        "experimentEligible": True,
        "repository": {
            "implementationComplete": False,
            "markdownContaminatedFiles": ["build.gradle"],
        },
        "score": {"passRate": 0.75, "unknown": 2, "checks": [
            {"kind": "providerProjection", "passed": True},
            {"kind": "providerProjection", "passed": False},
        ]},
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
        "externalTools": {
            "container": {"status": "passed"},
            "iacEngine": {"modules": [{
                "path": "infra",
                "validate": {"json": {"diagnostics": [{
                    "summary": "Unsupported argument", "detail": "fake_field"
                }, {
                    "summary": "Missing required argument", "detail": "real_field"
                }]}},
            }]},
        },
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
    assert arm["providerProjection"]["componentCompleteness"]["mean"] == 0.5
    assert arm["providerProjection"]["missingComponents"] == 1
    assert arm["invalidCloudClaimsPerRun"]["mean"] == 1.0
    assert arm["elapsedSeconds"]["mean"] == 51.4
    assert arm["semanticPassRate"]["mean"] == 0.75
    assert arm["codeQuality"]["cyclomaticComplexity"]["p95"]["mean"] == 3.5
    assert arm["codeQuality"]["decisionPointDensityPer100Nloc"]["mean"] == 7.5
    assert arm["codeQuality"]["coverage"]["branchRatio"]["mean"] == 0.75
    assert arm["codeQuality"]["coverage"]["missingRuns"] == 0
    assert arm["containerFunctionalPassRate"]["mean"] == 1.0


def test_component_summary_reports_paired_difference_in_differences(tmp_path):
    suite = ExperimentSuite.load(
        Path("evaluation/baselines/component-cases/suite.json"),
        expected_arms=experiment.COMPONENT_ARMS,
    )
    records = []
    values = {
        ("easydep-full", "control"): 0.8,
        ("easydep-full", "treatment"): 0.9,
        ("easydep-no-depkb", "control"): 0.8,
        ("easydep-no-depkb", "treatment"): 0.7,
    }
    for (arm, condition), pass_rate in values.items():
        run_id = f"{arm}-{condition}"
        run = tmp_path / run_id
        run.mkdir()
        (run / "evaluation.json").write_text(json.dumps({
            "experimentEligible": True,
            "score": {"passRate": pass_rate, "unknown": 0, "checks": []},
            "externalTools": {"container": {"status": "passed"}},
        }), encoding="utf-8")
        case_id = f"PS-{condition}-aws"
        records.append({
            "job": f"{arm}:{case_id}:r1", "status": "completed", "runId": run_id,
        })

    summary = aggregate(records, tmp_path, suite)

    paired = summary["pairedComponents"]
    assert paired["completeWithinArmPairs"] == 2
    did = paired["differenceInDifferences"][0]
    assert did["pairId"] == "persistent-storage"
    assert did["provider"] == "aws"
    assert did["differenceInDifferences"]["semanticPassRate"] == pytest.approx(0.2)


def test_invalid_cloud_claims_excludes_omissions_and_runtime_errors():
    tools = {"iacEngine": {"modules": [{
        "path": ".",
        "validate": {"json": {"diagnostics": [
            {"summary": "Invalid resource type", "detail": "invented"},
            {"summary": "Missing required argument", "detail": "omitted"},
            {"summary": "Error acquiring the state lock", "detail": "runtime"},
        ]}},
    }]}}

    claims = invalid_cloud_claims(tools)

    assert [item["summary"] for item in claims] == ["Invalid resource type"]


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


def test_llm_request_timeout_is_censored_not_counted_as_subject_failure(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        experiment,
        "_run_arm",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("Request timed out.")),
    )
    record = experiment.run_job(
        Job("easydep-full", "case.json", "P2-azure", 1),
        artifact_root=tmp_path,
    )

    summary = aggregate([record], tmp_path)["arms"]["easydep-full"]

    assert record["executionStatus"] == "censored"
    assert record["censorReason"] == "llmResponseCompletionTimeout"
    assert summary["censored"] == 1
    assert summary["failed"] == 0


def test_saved_llm_timeout_record_is_backfilled_without_rerun():
    records = [{
        "job": "easydep-full:P2-azure:r1",
        "status": "failed",
        "generationStatus": "failed",
        "generationError": "RuntimeError: EasyDep stopped at design: Request timed out.",
    }]

    experiment.refresh_execution_classification(records)

    assert records[0]["executionStatus"] == "censored"
    assert records[0]["censorReason"] == "llmResponseCompletionTimeout"


def test_provider_timeout_is_budget_censored_separately_from_llm_timeout():
    records = [{
        "generationStatus": "failed",
        "generationError": "Command ['tofu', 'init'] timed out after 180 seconds",
    }]

    experiment.refresh_execution_classification(records)

    assert records[0]["executionStatus"] == "censored"
    assert records[0]["censorReason"] == "providerOperationTimeout"
    assert records[0]["budgetCensored"] is True


def test_llm_connection_error_is_an_infrastructure_failure_not_a_timeout():
    records = [{
        "generationStatus": "failed",
        "generationError": "RuntimeError: Connection error.",
    }]

    experiment.refresh_execution_classification(records)

    assert records[0]["executionStatus"] == "infrastructureFailure"
    assert records[0]["infrastructureReason"] == "llmTransportError"
    assert records[0].get("censorReason") is None


def test_custom_index_root_resolves_central_easydep_run(tmp_path, monkeypatch):
    central = tmp_path / "central-runs"
    index_root = tmp_path / "indexes"
    run = central / "easydep-run-1"
    run.mkdir(parents=True)
    index_root.mkdir()
    (run / "manifest.json").write_text(
        json.dumps({
            "system": "easydep",
            "variant": "full",
            "caseId": "P2-azure",
        }),
        encoding="utf-8",
    )
    (run / "evaluation.json").write_text(
        json.dumps({"experimentEligible": True, "score": {"passRate": 1.0}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(experiment, "DEFAULT_ARTIFACT_ROOT", central)
    records = [{
        "job": "easydep-full:P2-azure:r1",
        "status": "completed",
        "runId": "easydep-run-1",
    }]

    experiment.refresh_completed_records(records, index_root)
    summary = aggregate(records, index_root)["arms"]["easydep-full"]

    assert records[0]["experimentEligible"] is True
    assert summary["semanticPassRate"]["mean"] == 1.0


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


def test_generation_failure_still_scores_a_preserved_repository(tmp_path, monkeypatch):
    run = tmp_path / "easydep-full-partial"
    (run / "03-implementation" / "application").mkdir(parents=True)
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        experiment,
        "_run_arm",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("generated tests failed")),
    )
    monkeypatch.setattr(experiment, "_find_created_run", lambda *_args: run.name)
    monkeypatch.setattr(
        experiment, "evaluate_repository",
        lambda *_args, **_kwargs: {"experimentEligible": False},
    )
    monkeypatch.setattr(experiment, "write_evaluation", lambda path, _value: path.write_text(
        "{}", encoding="utf-8"
    ))

    result = experiment.run_job(
        Job("easydep-full", "case.json", "P1-gcp", 1),
        artifact_root=tmp_path,
    )

    assert result["generationStatus"] == "failed"
    assert result["evaluationStatus"] == "completed"
    assert result["experimentEligible"] is False
    assert (run / "evaluation.json").is_file()


def test_job_key_includes_repetition():
    assert Job("easydep-full", "case.json", "P1-aws", 3).key == "easydep-full:P1-aws:r3"


def test_find_created_run_recovers_an_unpersisted_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "ROOT", tmp_path)
    monkeypatch.setattr(experiment, "DEFAULT_ARTIFACT_ROOT", tmp_path / "artifacts")
    store = RunStore(tmp_path / ".easydep" / "orchestration" / "runs.sqlite3")
    store.save(
        "active-checkpoint",
        {"request": {"case_id": "PS-treatment-aws", "variant": "full"}},
    )

    found = _find_created_run(
        Job("easydep-full", "case.json", "PS-treatment-aws", 1),
        tmp_path / "artifacts",
        0,
    )

    assert found == "active-checkpoint"


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


def test_job_selection_can_pair_all_arms_at_one_case_and_repetition():
    suite = ExperimentSuite.load(Path("evaluation/baselines/cases/suite.json"))
    schedule = build_schedule(suite, "development", 42)

    selected = select_jobs(schedule, cases={"P1-gcp"}, repetitions={1})

    assert len(selected) == 4
    assert {job.arm for job in selected} == set(suite.arms)
    assert {job.case_id for job in selected} == {"P1-gcp"}
    assert {job.repetition for job in selected} == {1}


def test_job_limit_is_shared_by_schedule_preview_and_execution():
    jobs = [Job("easydep-full", "case.json", "P1-gcp", repetition) for repetition in range(1, 4)]

    assert limit_jobs(jobs, 1) == jobs[:1]
    assert limit_jobs(jobs, None) == jobs
    with pytest.raises(ValueError, match="at least 1"):
        limit_jobs(jobs, 0)


def test_job_selection_rejects_names_outside_the_split():
    suite = ExperimentSuite.load(Path("evaluation/baselines/cases/suite.json"))
    schedule = build_schedule(suite, "development", 42)

    with pytest.raises(ValueError, match="selected split"):
        select_jobs(schedule, cases={"H1-azure"})
    with pytest.raises(ValueError, match="repetition"):
        select_jobs(schedule, repetitions={4})


def test_selected_pilot_has_an_index_separate_from_the_full_experiment():
    assert _result_stem("development") == "experiment-development"
    assert _result_stem(
        "development",
        arms={"easydep-full"},
        cases={"P1-gcp"},
        repetitions={1},
    ) == "experiment-development-easydep-full-P1-gcp-r1"


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
    assert report["configuration"]["maxConcurrentJobs"] == 1
    assert report["configuration"]["minimumFreeDiskBytes"] == 5 * 1024**3
    assert report["configuration"]["designLlmClientTimeoutSeconds"] == 300
    assert report["configuration"]["designLlmWallTimeoutSeconds"] == 330


def test_easydep_experiment_uses_the_member_implementation_provider(tmp_path, monkeypatch):
    captured = []

    import app.orchestration as orchestration

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
    assert captured[0].providers.implementation_scaffold == ProviderKind.MEMBER


def test_easydep_experiment_retries_the_same_failed_run(tmp_path, monkeypatch):
    captured = []

    import app.orchestration as orchestration

    fake_result = SimpleNamespace(
        status=StepStatus.COMPLETED,
        run_id="same-run-id",
        state={},
    )
    monkeypatch.setattr(
        orchestration,
        "retry_failed_run",
        lambda run_id, **kwargs: captured.append((run_id, kwargs)) or fake_result,
    )
    monkeypatch.setattr(
        orchestration,
        "run_batch",
        lambda _request: pytest.fail("새 실행을 시작하면 안 됩니다"),
    )
    monkeypatch.setattr(experiment, "DEFAULT_ARTIFACT_ROOT", tmp_path)

    result = experiment._easydep(
        Path("evaluation/baselines/cases/p1-stateless-gcp.json"),
        resume_run_id="same-run-id",
    )

    assert result == tmp_path / "same-run-id"
    assert captured[0][0] == "same-run-id"
    assert "checkpoint retry" in captured[0][1]["reason"]


def test_easydep_uses_a_bounded_same_run_repair_budget(tmp_path, monkeypatch):
    captured = []
    import app.orchestration as orchestration

    failed = SimpleNamespace(
        status=StepStatus.FAILED,
        stage=SimpleNamespace(value="implementation"),
        run_id="bounded-run",
        state={"error": "validated mismatch"},
    )
    completed = SimpleNamespace(
        status=StepStatus.COMPLETED,
        stage=SimpleNamespace(value="testing"),
        run_id="bounded-run",
        state={},
    )
    (tmp_path / "bounded-run").mkdir()
    monkeypatch.setattr(experiment, "DEFAULT_ARTIFACT_ROOT", tmp_path)
    monkeypatch.setenv("EASYDEP_MAX_CHECKPOINT_REPAIRS", "2")
    monkeypatch.setattr(orchestration, "run_batch", lambda _request: failed)
    monkeypatch.setattr(
        orchestration,
        "retry_failed_run",
        lambda run_id, **kwargs: captured.append((run_id, kwargs)) or completed,
    )

    result = experiment._easydep(
        Path("evaluation/baselines/cases/p1-stateless-gcp.json")
    )

    assert result == tmp_path / "bounded-run"
    assert len(captured) == 1
    repair = json.loads(
        (tmp_path / "bounded-run" / "generation-repair.json").read_text(encoding="utf-8")
    )
    assert repair["initialStatus"] == "failed"
    assert repair["repairsUsed"] == 1
    assert repair["finalStatus"] == "completed"


def test_easydep_stops_when_bounded_repair_budget_is_exhausted(tmp_path, monkeypatch):
    import app.orchestration as orchestration

    failed = SimpleNamespace(
        status=StepStatus.FAILED,
        stage=SimpleNamespace(value="implementation"),
        run_id="bounded-failure",
        state={"error": "still invalid", "implementation": {"data": {}}},
    )
    (tmp_path / "bounded-failure").mkdir()
    monkeypatch.setattr(experiment, "DEFAULT_ARTIFACT_ROOT", tmp_path)
    monkeypatch.setenv("EASYDEP_MAX_CHECKPOINT_REPAIRS", "2")
    monkeypatch.setattr(orchestration, "run_batch", lambda _request: failed)
    monkeypatch.setattr(orchestration, "retry_failed_run", lambda *_args, **_kwargs: failed)

    with pytest.raises(RuntimeError, match="still invalid"):
        experiment._easydep(Path("evaluation/baselines/cases/p1-stateless-gcp.json"))

    repair = json.loads(
        (tmp_path / "bounded-failure" / "generation-repair.json").read_text(encoding="utf-8")
    )
    assert repair["repairsUsed"] == 2
    assert repair["finalStatus"] == "failed"


def test_easydep_experiment_removes_only_its_copied_evaluation_workspace(
    tmp_path, monkeypatch
):
    import app.orchestration as orchestration

    monkeypatch.setattr(experiment, "ROOT", tmp_path)
    monkeypatch.setattr(experiment, "DEFAULT_ARTIFACT_ROOT", tmp_path / "artifacts")
    workspace = tmp_path / ".easydep" / "orchestration" / "workspaces" / "run-1"
    (workspace / "application").mkdir(parents=True)
    (workspace / "application" / "source.java").write_text("class Source {}")
    fake_result = SimpleNamespace(
        status=StepStatus.COMPLETED,
        run_id="run-1",
        state={"implementation": {"data": {"run_root": str(workspace)}}},
    )
    monkeypatch.setattr(orchestration, "run_batch", lambda _request: fake_result)

    result = experiment._easydep(
        Path("evaluation/baselines/cases/p1-stateless-gcp.json")
    )

    assert result == tmp_path / "artifacts" / "run-1"
    assert not workspace.exists()


def test_easydep_experiment_preserves_member_owned_workspace(tmp_path, monkeypatch):
    import app.orchestration as orchestration

    monkeypatch.setattr(experiment, "ROOT", tmp_path)
    monkeypatch.setattr(experiment, "DEFAULT_ARTIFACT_ROOT", tmp_path / "artifacts")
    workspace = tmp_path / ".easydep" / "implementation-runs" / "member-run"
    (workspace / "application").mkdir(parents=True)
    fake_result = SimpleNamespace(
        status=StepStatus.FAILED,
        stage=SimpleNamespace(value="testing"),
        run_id="run-1",
        state={
            "error": "application test failed",
            "implementation": {"data": {"run_root": str(workspace)}},
        },
    )
    monkeypatch.setattr(orchestration, "run_batch", lambda _request: fake_result)

    with pytest.raises(RuntimeError, match="application test failed"):
        experiment._easydep(
            Path("evaluation/baselines/cases/p1-stateless-gcp.json")
        )

    assert workspace.is_dir()


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
    monkeypatch.setattr(
        experiment,
        "_stop_experiment_member_runners",
        lambda _session_id: ["runner-id"],
    )

    result = _run_isolated(
        Job("easydep-full", "case.json", "P1-aws", 1),
        artifact_root=tmp_path,
        run_tools=False,
        timeout_seconds=1,
    )

    assert result["status"] == "timeout"
    assert result["elapsedSeconds"] == 1.0
    assert result["cleanedMemberRunners"] == ["runner-id"]
    assert result["runId"] == run.name
    assert result["workerLog"].startswith("worker-logs/")
    assert (run / "experiment-timeout.json").is_file()


def test_isolated_worker_streams_output_to_a_run_log(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "ROOT", tmp_path)
    monkeypatch.setattr(experiment, "DEFAULT_ARTIFACT_ROOT", tmp_path / "artifacts")

    def complete(*_args, **kwargs):
        assert kwargs["env"]["EASYDEP_LLM_STALL_PROBE_AFTER_SECONDS"] == "120"
        assert kwargs["env"]["EASYDEP_LLM_STALL_PROBE_TIMEOUT_SECONDS"] == "60"
        assert kwargs["env"]["EASYDEP_APPROVE_MEMBER_IMPLEMENTATION"] == "1"
        assert kwargs["env"]["LLM_MAX_COMPLETION_TOKENS"] == "16384"
        kwargs["stdout"].write("provider validation started\n")
        command = list(_args[0])
        result_path = Path(command[command.index("--worker-result") + 1])
        result_path.write_text(json.dumps({
            "job": "easydep-full:P2-gcp:r1", "status": "completed"
        }), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(experiment, "run_process_tree", complete)
    artifact_root = tmp_path / "custom-artifacts"
    result = _run_isolated(
        Job("easydep-full", "case.json", "P2-gcp", 1),
        artifact_root=artifact_root,
        run_tools=False,
        timeout_seconds=30,
        enable_stall_probe=True,
        approve_member_implementation=True,
    )

    log_path = artifact_root / result["workerLog"]
    log_text = log_path.read_text(encoding="utf-8")
    assert "workerStarted" in log_text
    assert "provider validation started" in log_text
    assert "workerExited" in log_text


def test_isolated_worker_preserves_explicit_completion_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "ROOT", tmp_path)
    monkeypatch.setenv("LLM_MAX_COMPLETION_TOKENS", "24576")

    def complete(*_args, **kwargs):
        assert kwargs["env"]["LLM_MAX_COMPLETION_TOKENS"] == "24576"
        command = list(_args[0])
        result_path = Path(command[command.index("--worker-result") + 1])
        result_path.write_text(
            json.dumps({"job": "easydep-full:P2-gcp:r1", "status": "completed"}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(experiment, "run_process_tree", complete)
    _run_isolated(
        Job("easydep-full", "case.json", "P2-gcp", 1),
        artifact_root=tmp_path / "artifacts",
        run_tools=False,
        timeout_seconds=30,
    )


def test_execute_creates_a_custom_artifact_root(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "_run_isolated", lambda job, **_kwargs: {
        "job": job.key, "status": "failed", "runId": None,
    })
    artifact_root = tmp_path / "missing" / "artifact-root"

    index_path = experiment.execute(
        "development", artifact_root=artifact_root, run_tools=False, limit=1,
    )

    assert index_path.is_file()


def test_execute_recovers_the_checkpoint_from_an_interrupted_running_attempt(
    tmp_path, monkeypatch
):
    captured = []

    def isolated(job, **_kwargs):
        captured.append(job.resume_run_id)
        return {
            "job": job.key,
            "status": "failed",
            "runId": "same-run-id",
        }

    monkeypatch.setattr(experiment, "_run_isolated", isolated)
    index_path = experiment.execute(
        "development", artifact_root=tmp_path, run_tools=False, limit=1,
    )
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["results"][0] = {
        "job": index["results"][0]["job"],
        "status": "running",
        "resumeRunId": "same-run-id",
    }
    index_path.write_text(json.dumps(index), encoding="utf-8")

    experiment.execute(
        "development",
        artifact_root=tmp_path,
        run_tools=False,
        resume=True,
        limit=1,
        retry_failed_checkpoints=True,
    )

    assert captured == [None, "same-run-id"]


def test_holdout_cannot_run_before_research_freeze(tmp_path, monkeypatch):
    import evaluation.research_protocol.commands.readiness as readiness_module

    monkeypatch.setattr(readiness_module, "readiness", lambda: {
        "ready": False,
        "blockers": [{"kind": "nativeModelMissing"}],
    })

    with pytest.raises(RuntimeError, match="nativeModelMissing"):
        experiment.execute("holdout", artifact_root=tmp_path, run_tools=False)


def test_development_pilot_does_not_claim_confirmatory_status(tmp_path, monkeypatch):
    monkeypatch.setattr(experiment, "_run_isolated", lambda job, **_kwargs: {
        "job": job.key, "status": "failed", "runId": None,
    })

    index_path = experiment.execute(
        "development", artifact_root=tmp_path, run_tools=False, limit=1,
    )
    index = json.loads(index_path.read_text(encoding="utf-8"))

    assert index["confirmatory"] is False
    assert index["researchLock"] is None
    assert len(index["suiteSha256"]) == 64
