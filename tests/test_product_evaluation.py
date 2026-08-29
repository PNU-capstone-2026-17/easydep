from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from evaluation.easydep.product import (
    HoldoutAccessError,
    ProductEvaluationRunner,
    RunEnvironment,
    aggregate_manifests,
    load_catalog,
    load_profile,
)
from evaluation.easydep.product.recording import (
    collect_metric_evidence,
    extract_llm_metrics,
    merge_metric_evidence,
    summarize_metric_evidence,
)


def _command(
    number: int,
    stage: str,
    status: str,
    *,
    result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "command_id": f"command-{number}",
        "app_id": "app-evaluation",
        "action": "message",
        "stage": stage,
        "status": status,
        "payload": {},
        "result": dict(result or {}),
        "error": None,
    }


class EvaluationTransport:
    """공개 Workspace 응답만으로 design 완료 또는 중간 실패를 재현한다."""

    def __init__(
        self,
        *,
        fail_requirements: bool = False,
        fail_design: bool = False,
    ) -> None:
        self.fail_requirements = fail_requirements
        self.fail_design = fail_design
        self.state = "requirements"
        self.create_calls = 0
        self.commands: list[dict[str, Any]] = []
        self.event_after: list[int] = []
        self.requirements_llm_event: dict[str, Any] | None = None

    def create_app(self, message: str) -> Mapping[str, Any]:
        assert "API consumers convert values" in message
        self.create_calls += 1
        return {
            "app_id": "app-evaluation",
            "command": _command(0, "requirements", "QUEUED"),
        }

    def get_workspace(self, app_id: str) -> Mapping[str, Any]:
        assert app_id == "app-evaluation"
        if self.state == "requirements":
            command = _command(
                1,
                "requirements",
                "FAILED" if self.fail_requirements else "COMPLETED",
            )
        elif self.fail_design:
            command = _command(2, "design", "FAILED")
        else:
            command = _command(
                3,
                "design",
                "COMPLETED",
                result={
                    "llm_timing_events": [
                        {
                            "operation": "class-inventory",
                            "logicalRequestDigest": "logical-1",
                            "physicalRequest": True,
                            "physicalRequestIndex": 1,
                            "inputTokens": 100,
                            "outputTokens": 20,
                            "totalTokens": 120,
                        },
                        {
                            "operation": "class-inventory",
                            "logicalRequestDigest": "logical-1",
                            "physicalRequest": True,
                            "physicalRequestIndex": 2,
                            "inputTokens": 30,
                            "outputTokens": 10,
                            "totalTokens": 40,
                            "repairKind": "schema",
                            "schemaRepairAttempt": 1,
                            "handoff": "schema-repair",
                        },
                        {
                            "operation": "class-cache",
                            "physicalRequest": False,
                            "cacheStatus": "hit",
                        },
                    ]
                },
            )
        return {
            "app_id": app_id,
            "current_stage": command["stage"],
            "command": command,
            "events": (
                [dict(self.requirements_llm_event)]
                if self.requirements_llm_event is not None
                else []
            ),
        }

    def submit_command(
        self, app_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        assert app_id == "app-evaluation"
        self.commands.append(dict(payload))
        if payload.get("action") == "retry_requirements":
            self.fail_requirements = False
            return {
                "app_id": app_id,
                "command": _command(1, "requirements", "RUNNING"),
            }
        if payload.get("action") == "start_design":
            self.state = "design"
            return {
                "app_id": app_id,
                "command": _command(2, "design", "RUNNING"),
            }
        if payload.get("action") == "retry_design":
            self.fail_design = False
            return {
                "app_id": app_id,
                "command": _command(3, "design", "RUNNING"),
            }
        raise AssertionError(f"예상하지 않은 action입니다: {payload}")

    def read_events(
        self, app_id: str, after: int, timeout_seconds: float
    ) -> Sequence[Mapping[str, Any]]:
        assert app_id == "app-evaluation"
        assert timeout_seconds >= 0
        self.event_after.append(after)
        if self.requirements_llm_event is not None:
            event_id = int(self.requirements_llm_event["event_id"])
            return [dict(self.requirements_llm_event)] if after < event_id else []
        if after >= 1:
            return []
        return [
            {
                "event_id": 1,
                "app_id": app_id,
                "stage": self.state,
                "kind": "status",
                "metadata": {},
            }
        ]

    def get_artifacts(self, app_id: str) -> Mapping[str, Any]:
        assert app_id == "app-evaluation"
        return {
            "app_id": app_id,
            "artifacts": {
                "refined_requirements": {"requirements": [{"id": "FR-1"}]},
                "deployment_diagram": "@startuml\nnode app\n@enduml",
            },
        }

    def get_stage_versions(
        self, app_id: str, stage: str
    ) -> Sequence[Mapping[str, Any]]:
        assert app_id == "app-evaluation"
        assert stage in {"refined_requirements", "deployment_diagram"}
        return [{"version_no": 1, "is_current": True}]

    def get_stage_version(
        self, app_id: str, stage: str, version_no: int
    ) -> Mapping[str, Any]:
        assert app_id == "app-evaluation"
        assert version_no == 1
        return {"version_no": 1, "content": {"stage": stage}}

    def get_file_artifact(
        self, app_id: str, artifact_type: str
    ) -> Mapping[str, Any] | None:
        assert app_id == "app-evaluation"
        return None

    def get_artifact_file(
        self, app_id: str, artifact_type: str, path: str
    ) -> Mapping[str, Any]:
        raise AssertionError("design까지만 실행할 때 구현 파일을 읽으면 안 됩니다.")


class AwaitingDesignTransport(EvaluationTransport):
    """design이 사람의 수정 지시를 기다리는 실제 AWAITING_INPUT 모양을 만든다."""

    def get_workspace(self, app_id: str) -> Mapping[str, Any]:
        if self.state != "design":
            return super().get_workspace(app_id)
        command = _command(
            2,
            "design",
            "AWAITING_INPUT",
            result={
                "requires_revision": True,
                "can_delegate_repair": False,
                "blocking_findings": [{"message": "수정 지시가 필요합니다."}],
            },
        )
        return {
            "app_id": app_id,
            "current_stage": "design",
            "command": command,
            "events": [],
        }


def _environment() -> RunEnvironment:
    return RunEnvironment(
        commit="abc123",
        provider="nvidia-nim",
        model="test-model",
        settings={"temperature": 0, "classConcurrency": 2},
    )


def _requirements_llm_event(event_id: int = 41) -> dict[str, Any]:
    """실제 Workspace snapshot과 SSE가 함께 반환하는 요구사항 progress 모양이다."""
    return {
        "event_id": event_id,
        "app_id": "app-evaluation",
        "command_id": "command-1",
        "stage": "requirements",
        "kind": "progress",
        "actor": "system",
        "text": "AI requirement refinement completed in 1.2s",
        "metadata": {
            "progress_event": "llmOperationFinished",
            "operation": "structured:ClarifyOnlyResult",
            "status": "completed",
            "errorType": None,
            "elapsedSeconds": 1.25,
            "promptTokens": 11,
            "completionTokens": 7,
            "structuredFallback": True,
            "analysis_step": "clarify",
        },
    }


def test_catalog_and_profiles_keep_development_separate_from_holdout() -> None:
    catalog = load_catalog()

    assert len(catalog) >= 8
    assert all(case.message.strip() for case in catalog.values())
    assert all(
        "다음 요구사항을 만족하는 서비스를 만들어 주세요." not in case.message
        for case in catalog.values()
    )
    assert len({case.input_digest for case in catalog.values()}) == len(catalog)
    assert load_profile("quick").planned_run_count == 8
    assert load_profile("stability").planned_run_count == 24
    full = load_profile("full")
    assert full.planned_run_count == 4
    assert full.target_stage == "testing"
    with pytest.raises(HoldoutAccessError):
        load_profile("holdout")
    holdout = load_profile("holdout", allow_holdout_after_settings_lock=True)
    assert holdout.partition == "holdout"
    assert all(catalog[item].partition == "holdout" for item in holdout.dataset_ids)


def test_metric_extraction_supports_requirements_summary_and_unavailable_values() -> None:
    measured = extract_llm_metrics(
        [
            {
                "_evaluationObservationId": "command:requirements-1",
                "payload": {
                    "telemetry": {
                        "llm_calls": 2,
                        "structured_fallbacks": 1,
                        "prompt_tokens": 90,
                        "completion_tokens": 30,
                    }
                },
            }
        ]
    )
    unavailable = extract_llm_metrics([])

    assert measured["logicalCalls"] == 2
    assert measured["physicalCalls"] == 3
    assert measured["totalTokens"] == 120
    assert unavailable["totalTokens"] is None
    assert unavailable["repairs"]["total"] is None
    assert unavailable["measuredUnavailable"]


def test_actual_workspace_event_is_counted_once_across_snapshot_sse_and_new_command() -> None:
    event = _requirements_llm_event()
    first_snapshot = {
        "app_id": "app-evaluation",
        "command": {
            "command_id": "command-1",
            "stage": "requirements",
            "status": "RUNNING",
            "result": {
                # 완료 event와 같은 실행의 telemetry 합계다. event가 있으면 다시 더하지 않는다.
                "telemetry": {
                    "llm_calls": 1,
                    "structured_fallbacks": 1,
                    "prompt_tokens": 11,
                    "completion_tokens": 7,
                }
            },
        },
        "events": [event],
    }
    later_snapshot = {
        "app_id": "app-evaluation",
        "command": {
            "command_id": "command-2",
            "stage": "design",
            "status": "RUNNING",
        },
        "events": [event],
    }

    metrics = extract_llm_metrics([first_snapshot, event, later_snapshot])

    assert metrics["logicalCalls"] == 1
    assert metrics["physicalCalls"] == 2
    assert metrics["inputTokens"] == 11
    assert metrics["outputTokens"] == 7
    assert metrics["totalTokens"] == 18


def test_timing_and_delegate_action_use_stable_identity_outside_nested_payload() -> None:
    timing = {
        "operation": "ClassFragment",
        "startedAt": "2026-08-29T01:00:00+00:00",
        "logicalRequestDigest": "logical-one",
        "physicalRequest": True,
        "physicalRequestIndex": 1,
        "inputTokens": 20,
        "outputTokens": 5,
        "repairKind": None,
        "handoff": None,
    }
    action = {
        "selectedAt": "2026-08-29T01:01:00+00:00",
        "action": "delegate_repair",
        "payload": {
            "action": "delegate_repair",
            "action_id": "command-2",
        },
        "commandId": "command-3",
    }
    observations = [
        {"command": {"command_id": "command-2", "result": {"timing": [timing]}}},
        {
            "command": {"command_id": "command-3"},
            "events": [
                {
                    "event_id": 55,
                    "command_id": "command-2",
                    "metadata": {"llm_timing_events": [timing]},
                }
            ],
        },
        action,
    ]

    metrics = extract_llm_metrics(observations)

    assert metrics["logicalCalls"] == 1
    assert metrics["physicalCalls"] == 1
    assert metrics["totalTokens"] == 25
    assert metrics["repairs"]["semantic"] == 1
    assert metrics["repairs"]["total"] == 1


def test_class_cold_cache_miss_and_physical_request_are_one_logical_unit() -> None:
    observations = [
        {
            "stage": "design",
            "operation": "class-fragment-cache",
            "startedAt": "2026-08-29T01:00:00+00:00",
            "logicalRequestDigest": "same-logical-unit",
            "physicalRequest": False,
            "cacheStatus": "miss",
        },
        {
            "stage": "design",
            "operation": "class-fragment",
            "startedAt": "2026-08-29T01:00:01+00:00",
            "logicalRequestDigest": "same-logical-unit",
            "physicalRequest": True,
            "physicalRequestIndex": 1,
            "inputTokens": 20,
            "outputTokens": 5,
        },
        {
            "stage": "design",
            "operation": "class-fragment-cache",
            "startedAt": "2026-08-29T01:00:02+00:00",
            "logicalRequestDigest": "coalesced-logical-unit",
            "physicalRequest": False,
            "cacheStatus": "coalesced",
        },
    ]

    metrics = extract_llm_metrics(observations)

    assert metrics["logicalCalls"] == 2
    assert metrics["physicalCalls"] == 1
    assert metrics["cache"]["miss"] == 1
    assert metrics["cache"]["singleFlight"] == 1


def test_whole_run_totals_are_hidden_when_required_stage_coverage_is_partial() -> None:
    evidence = collect_metric_evidence([{"events": [_requirements_llm_event()]}])

    metrics = summarize_metric_evidence(
        evidence,
        required_stages=("requirements", "design"),
    )

    assert metrics["logicalCalls"] is None
    assert metrics["physicalCalls"] is None
    assert metrics["totalTokens"] is None
    assert metrics["repairs"]["total"] is None
    assert metrics["coverage"] == {
        "status": "partial",
        "requiredStages": ["requirements", "design"],
        "measuredStages": ["requirements"],
        "missingStages": ["design"],
    }
    assert any("design" in reason for reason in metrics["measuredUnavailable"])


def test_full_profile_does_not_claim_implementation_and_testing_are_zero_llm() -> None:
    evidence = collect_metric_evidence(
        [
            {"events": [_requirements_llm_event()]},
            {
                "stage": "design",
                "operation": "ClassFragment",
                "logicalRequestDigest": "design-logical",
                "physicalRequest": True,
                "physicalRequestIndex": 1,
                "inputTokens": 20,
                "outputTokens": 5,
                "repairKind": None,
                "handoff": None,
            },
        ]
    )

    metrics = summarize_metric_evidence(
        evidence,
        required_stages=("requirements", "design", "implementation", "testing"),
    )

    assert metrics["coverage"]["measuredStages"] == ["design", "requirements"]
    assert metrics["coverage"]["missingStages"] == ["implementation", "testing"]
    assert metrics["logicalCalls"] is None
    assert metrics["totalTokens"] is None


def test_saved_and_resumed_metric_evidence_is_recalculated_without_double_counting() -> None:
    event = _requirements_llm_event()
    saved = collect_metric_evidence([{"events": [event]}])
    resumed = collect_metric_evidence(
        [
            event,
            {"command": {"command_id": "new-command"}, "events": [event]},
        ]
    )

    combined = merge_metric_evidence(saved, resumed)
    metrics = summarize_metric_evidence(combined)

    assert len([item for item in combined if item["kind"] == "requirements_call"]) == 1
    assert metrics["logicalCalls"] == 1
    assert metrics["physicalCalls"] == 2
    assert metrics["totalTokens"] == 18


def test_evaluation_run_uses_public_runner_and_writes_complete_manifest(
    tmp_path: Path,
) -> None:
    transport = EvaluationTransport()
    transport.requirements_llm_event = _requirements_llm_event()
    case = load_catalog()["dev_stateless_conversion"]
    profile = load_profile("quick")
    runner = ProductEvaluationRunner(
        lambda: transport,
        tmp_path,
        poll_interval_seconds=0,
        event_wait_seconds=0,
    )

    manifest = runner.run_case(case, profile, 1, _environment())

    assert transport.create_calls == 1
    assert transport.commands == [{"action": "start_design"}]
    assert manifest["status"] == "COMPLETED"
    assert manifest["finalStage"] == "design"
    assert manifest["llm"]["logicalCalls"] == 3
    assert manifest["llm"]["physicalCalls"] == 4
    assert manifest["llm"]["totalTokens"] == 178
    assert manifest["llm"]["repairs"] == {
        "schema": 1,
        "semantic": 0,
        "handoff": 1,
        "total": 1,
    }
    assert manifest["llm"]["cache"]["hit"] == 1
    assert manifest["artifactVersions"]["deployment_diagram"]["versionNo"] == 1
    saved = next(tmp_path.rglob("manifest.json"))
    assert json.loads(saved.read_text(encoding="utf-8"))["runId"] == manifest["runId"]


def test_failed_run_resumes_same_app_without_restarting_completed_stage(
    tmp_path: Path,
) -> None:
    transport = EvaluationTransport(fail_design=True)
    transport.requirements_llm_event = _requirements_llm_event()
    case = load_catalog()["dev_stateless_conversion"]
    profile = load_profile("quick")
    runner = ProductEvaluationRunner(
        lambda: transport,
        tmp_path,
        poll_interval_seconds=0,
        event_wait_seconds=0,
    )

    failed = runner.run_case(case, profile, 1, _environment())
    manifest_path = next(tmp_path.rglob("manifest.json"))
    assert failed["status"] == "NEEDS_INPUT"
    assert failed["resumeRecord"]["app_id"] == "app-evaluation"
    first_run_id = failed["runId"]
    assert transport.commands == [{"action": "start_design"}]

    resumed = runner.run_case(
        case,
        profile,
        1,
        _environment(),
        manifest_path=manifest_path,
    )

    assert resumed["status"] == "COMPLETED"
    assert resumed["runId"] == first_run_id
    assert transport.create_calls == 1
    assert transport.commands == [
        {"action": "start_design"},
        {"action": "retry_design", "action_id": "command-2"},
    ]
    assert len(resumed["resumeHistory"]) == 1
    assert resumed["firstFailure"]["stage"] == "design"
    assert resumed["llm"]["totalTokens"] == 178
    assert len(
        [
            item
            for item in resumed["metricEvidence"]
            if item["kind"] == "requirements_call"
        ]
    ) == 1
    assert resumed["attempts"][-1]["llm"]["totalTokens"] == 160


def test_awaiting_input_closes_current_stage_wall_time(tmp_path: Path) -> None:
    transport = AwaitingDesignTransport()
    case = load_catalog()["dev_stateless_conversion"]
    profile = load_profile("quick")
    runner = ProductEvaluationRunner(
        lambda: transport,
        tmp_path,
        poll_interval_seconds=0,
        event_wait_seconds=0,
    )

    manifest = runner.run_case(case, profile, 1, _environment())

    assert manifest["status"] == "NEEDS_INPUT"
    design = next(
        item
        for item in manifest["stageTimings"]
        if item["stage"] == "design"
    )
    assert design["finishedAt"] is not None
    assert isinstance(design["wallSeconds"], float)
    assert design["wallSeconds"] >= 0


def test_requirements_failure_resumes_from_its_public_retry_action(
    tmp_path: Path,
) -> None:
    transport = EvaluationTransport(fail_requirements=True)
    case = load_catalog()["dev_stateless_conversion"]
    profile = load_profile("quick")
    runner = ProductEvaluationRunner(
        lambda: transport,
        tmp_path,
        poll_interval_seconds=0,
        event_wait_seconds=0,
    )

    failed = runner.run_case(case, profile, 1, _environment())
    manifest_path = next(tmp_path.rglob("manifest.json"))
    resumed = runner.run_case(
        case,
        profile,
        1,
        _environment(),
        manifest_path=manifest_path,
    )

    assert failed["firstFailure"]["stage"] == "requirements"
    assert resumed["status"] == "COMPLETED"
    assert resumed["appId"] == failed["appId"] == "app-evaluation"
    assert transport.create_calls == 1
    assert transport.commands == [
        {"action": "retry_requirements", "action_id": "command-1"},
        {"action": "start_design"},
    ]


class _InterruptAfterWorkspaceSnapshot:
    """앱 생성과 첫 snapshot 뒤 프로세스가 종료되는 상황을 재현한다."""

    def __init__(self, transport: Any, **_kwargs: Any) -> None:
        self.transport = transport

    def run_until(self, message: str, *, stop_after_stage: str) -> None:
        assert stop_after_stage == "design"
        created = self.transport.create_app(message)
        self.transport.get_workspace(str(created["app_id"]))
        raise KeyboardInterrupt


class _RaiseBeforeFirstGet:
    """재개 runner가 공개 GET을 보내기 전에 환경 오류가 나는 상황이다."""

    def __init__(self, _transport: Any, **_kwargs: Any) -> None:
        pass

    def resume_from(self, _report: Any, *, stop_after_stage: str) -> None:
        assert stop_after_stage == "design"
        raise RuntimeError("runner setup failed before first GET")


def test_resume_rejects_a_different_commit_before_touching_same_app(
    tmp_path: Path,
) -> None:
    transport = EvaluationTransport(fail_design=True)
    case = load_catalog()["dev_stateless_conversion"]
    profile = load_profile("quick")
    runner = ProductEvaluationRunner(
        lambda: transport,
        tmp_path,
        poll_interval_seconds=0,
        event_wait_seconds=0,
    )
    runner.run_case(case, profile, 1, _environment())
    manifest_path = next(tmp_path.rglob("manifest.json"))
    changed_commit = RunEnvironment(
        commit="different-commit",
        provider="nvidia-nim",
        model="test-model",
        settings={"temperature": 0, "classConcurrency": 2},
    )

    with pytest.raises(ValueError, match="commit"):
        runner.run_case(
            case,
            profile,
            1,
            changed_commit,
            manifest_path=manifest_path,
        )

    assert transport.create_calls == 1


def test_generic_resume_failure_before_first_get_preserves_saved_location(
    tmp_path: Path,
) -> None:
    transport = EvaluationTransport(fail_design=True)
    case = load_catalog()["dev_stateless_conversion"]
    profile = load_profile("quick")
    first_runner = ProductEvaluationRunner(
        lambda: transport,
        tmp_path,
        poll_interval_seconds=0,
        event_wait_seconds=0,
    )
    failed = first_runner.run_case(case, profile, 1, _environment())
    manifest_path = next(tmp_path.rglob("manifest.json"))
    saved = dict(failed["resumeRecord"])

    broken_runner = ProductEvaluationRunner(
        lambda: transport,
        tmp_path,
        runner_factory=_RaiseBeforeFirstGet,
        poll_interval_seconds=0,
        event_wait_seconds=0,
    )
    failed_again = broken_runner.run_case(
        case,
        profile,
        1,
        _environment(),
        manifest_path=manifest_path,
    )

    assert failed_again["status"] == "RuntimeError"
    assert failed_again["resumeRecord"]["app_id"] == saved["app_id"]
    assert failed_again["resumeRecord"]["current_stage"] == saved["current_stage"]
    assert failed_again["resumeRecord"]["last_command_id"] == saved["last_command_id"]
    assert failed_again["resumeRecord"]["event_cursor"] == saved["event_cursor"]
    assert failed_again["resumeRecord"]["implementation_job_id"] == saved[
        "implementation_job_id"
    ]
    assert failed_again["resumeRecord"]["testing_job_id"] == saved["testing_job_id"]


class _InterruptAfterAppCreation:
    """앱 생성 응답 직후 종료되어도 app ID가 저장되는지 확인하는 실행기다."""

    def __init__(self, transport: Any, **_kwargs: Any) -> None:
        self.transport = transport

    def run_until(self, message: str, *, stop_after_stage: str) -> None:
        assert stop_after_stage == "design"
        self.transport.create_app(message)
        raise KeyboardInterrupt


def test_running_manifest_saves_app_immediately_after_creation(tmp_path: Path) -> None:
    transport = EvaluationTransport()
    case = load_catalog()["dev_stateless_conversion"]
    profile = load_profile("quick")
    runner = ProductEvaluationRunner(
        lambda: transport,
        tmp_path,
        runner_factory=_InterruptAfterAppCreation,
        poll_interval_seconds=0,
        event_wait_seconds=0,
    )

    with pytest.raises(KeyboardInterrupt):
        runner.run_case(case, profile, 1, _environment())

    running = json.loads(next(tmp_path.rglob("manifest.json")).read_text(encoding="utf-8"))
    assert running["status"] == "RUNNING"
    assert running["appId"] == "app-evaluation"
    assert running["resumeRecord"]["app_id"] == "app-evaluation"
    assert running["resumeRecord"]["last_command_id"] == "command-0"


def test_running_manifest_is_atomic_and_can_resume_same_app_after_interrupt(
    tmp_path: Path,
) -> None:
    transport = EvaluationTransport()
    transport.requirements_llm_event = _requirements_llm_event()
    case = load_catalog()["dev_stateless_conversion"]
    profile = load_profile("quick")
    interrupted_runner = ProductEvaluationRunner(
        lambda: transport,
        tmp_path,
        runner_factory=_InterruptAfterWorkspaceSnapshot,
        poll_interval_seconds=0,
        event_wait_seconds=0,
    )

    with pytest.raises(KeyboardInterrupt):
        interrupted_runner.run_case(case, profile, 1, _environment())

    manifest_path = next(tmp_path.rglob("manifest.json"))
    running = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert running["status"] == "RUNNING"
    assert running["appId"] == "app-evaluation"
    assert running["resumeRecord"]["app_id"] == "app-evaluation"
    assert running["resumeRecord"]["last_command_id"] == "command-1"
    assert running["resumeRecord"]["event_cursor"] == 41
    assert running["llm"]["totalTokens"] is None
    assert running["llm"]["coverage"]["missingStages"] == ["design"]
    assert not list(manifest_path.parent.glob("*.tmp"))

    resumed_runner = ProductEvaluationRunner(
        lambda: transport,
        tmp_path,
        poll_interval_seconds=0,
        event_wait_seconds=0,
    )
    completed = resumed_runner.run_case(
        case,
        profile,
        1,
        _environment(),
        manifest_path=manifest_path,
    )

    assert completed["status"] == "COMPLETED"
    assert completed["appId"] == "app-evaluation"
    assert transport.create_calls == 1
    assert completed["llm"]["totalTokens"] == 178
    assert len(
        [
            item
            for item in completed["metricEvidence"]
            if item["kind"] == "requirements_call"
        ]
    ) == 1
    assert completed["attempts"][-1]["llm"]["totalTokens"] == 160


def test_aggregate_counts_failures_and_unavailable_measurements(tmp_path: Path) -> None:
    complete = {
        "runId": "run-1",
        "dataset": {"id": "one"},
        "status": "COMPLETED",
        "finalStage": "design",
        "wallSeconds": 10,
        "firstFailure": None,
        "llm": {
            "totalTokens": 100,
            "repairs": {"total": 2},
            "cache": {"hit": 1},
            "providerErrors": {"429": 0},
            "measuredUnavailable": [],
        },
        "stageTimings": [{"stage": "design", "wallSeconds": 8}],
    }
    failed = {
        "runId": "run-2",
        "dataset": {"id": "two"},
        "status": "FAILED",
        "finalStage": "requirements",
        "wallSeconds": 20,
        "firstFailure": {"stage": "requirements", "reason": "schema"},
        "llm": {
            "totalTokens": None,
            "repairs": {"total": 4},
            "cache": {},
            "providerErrors": {"429": 1},
            "measuredUnavailable": ["공개 응답에 token 사용량이 없습니다."],
        },
        "stageTimings": [{"stage": "requirements", "wallSeconds": 19}],
    }
    paths: list[Path] = []
    for index, value in enumerate((complete, failed), start=1):
        path = tmp_path / f"run-{index}" / "manifest.json"
        path.parent.mkdir()
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        paths.append(path)

    report = aggregate_manifests(paths)

    assert report["completionRate"] == 0.5
    assert report["stageFailures"] == {"requirements": 1}
    assert report["stageFailureRates"] == {"requirements": 0.5}
    assert report["wallSeconds"]["sampleCount"] == 2
    assert report["wallSeconds"]["p50"] == 15
    assert report["totalTokens"]["sampleCount"] == 1
    assert report["totalTokens"]["unavailableCount"] == 1
    assert report["repairMedian"] == 3
    assert report["providerErrors"]["429"] == 1
    assert report["measuredUnavailableReasons"][
        "공개 응답에 token 사용량이 없습니다."
    ] == 1
