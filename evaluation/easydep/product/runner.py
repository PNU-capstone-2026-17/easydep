"""요구사항 여러 세트를 공개 제품 경로로 실행하고 manifest를 저장한다."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.easydep.product.catalog import (
    DatasetCase,
    EvaluationProfile,
    load_profile_catalog,
)
from evaluation.easydep.product.recording import (
    RecordingTransport,
    collect_metric_evidence,
    merge_actions,
    merge_metric_evidence,
    merge_workspace_events,
    summarize_metric_evidence,
)
from evaluation.easydep.product_scenario import (
    AutoActionPolicy,
    ProductScenarioCheckpoint,
    ProductScenarioFailed,
    ProductScenarioNeedsInput,
    ProductScenarioRunner,
    ProductScenarioStopped,
    ProductScenarioTimeout,
    ProductScenarioTransport,
    PublicAction,
)

JsonObject = dict[str, Any]
TransportFactory = Callable[[], ProductScenarioTransport]
RunnerFactory = Callable[..., ProductScenarioRunner]


class _ResumeActionPolicy:
    """명시적 재개 때만 화면에 보이는 실패 단계 재실행 버튼을 누른다."""

    def __init__(self, base: AutoActionPolicy) -> None:
        self.base = base

    def choose(
        self,
        actions: Sequence[PublicAction],
        command: Mapping[str, Any],
    ) -> PublicAction | None:
        status = str(command.get("status") or "")
        if status in {"FAILED", "INTERRUPTED"}:
            retry_names = {
                "retry_requirements",
                "retry_design",
                "rerun_implementation",
                "start_testing",
            }
            return next(
                (action for action in actions if action.action in retry_names), None
            )
        return self.base.choose(actions, command)


@dataclass(frozen=True)
class RunEnvironment:
    """결과를 다시 해석할 때 필요한 코드와 LLM 설정 정보다."""

    commit: str
    provider: str
    model: str
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.commit.strip() or not self.provider.strip() or not self.model.strip():
            raise ValueError("commit, provider, model은 비어 있을 수 없습니다.")

    @property
    def settings_digest(self) -> str:
        """설정 JSON이 같은지 비교할 수 있는 SHA-256을 반환한다."""
        encoded = json.dumps(
            dict(self.settings),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def as_dict(self) -> JsonObject:
        return {
            "commit": self.commit,
            "provider": self.provider,
            "model": self.model,
            "settings": dict(self.settings),
            "settingsDigest": self.settings_digest,
        }


def _artifact_manifest(checkpoint: ProductScenarioCheckpoint) -> JsonObject:
    """typed 산출물 참조를 장기 저장 가능한 JSON 모양으로 바꾼다."""
    return {
        name: {
            "versionNo": reference.version_no,
            "digest": reference.digest,
            "fileCount": reference.file_count,
            "verifiedFileCount": reference.verified_file_count,
        }
        for name, reference in checkpoint.artifact_references.items()
    }


class ProductEvaluationRunner:
    """한 profile의 각 사례를 독립 run ID로 실행하는 상위 실행기다."""

    def __init__(
        self,
        transport_factory: TransportFactory,
        output_root: Path,
        *,
        runner_factory: RunnerFactory = ProductScenarioRunner,
        timeout_seconds: float = 7200.0,
        poll_interval_seconds: float = 1.0,
        event_wait_seconds: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.transport_factory = transport_factory
        self.output_root = output_root
        self.runner_factory = runner_factory
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.event_wait_seconds = event_wait_seconds
        self.monotonic = monotonic
        self.now = now

    def _write(self, path: Path, manifest: Mapping[str, Any]) -> None:
        """완성된 JSON만 보이도록 같은 디렉터리의 임시 파일을 원자적으로 교체한다."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temporary.replace(path)

    def _new_manifest(
        self,
        case: DatasetCase,
        profile: EvaluationProfile,
        attempt_no: int,
        environment: RunEnvironment,
        run_id: str,
    ) -> JsonObject:
        return {
            "schemaVersion": "easydep-product-evaluation-run/v1",
            "runId": run_id,
            "dataset": {
                "id": case.dataset_id,
                "partition": case.partition,
                "domain": case.domain,
                "source": case.source,
                "inputDigest": case.input_digest,
            },
            "profile": {
                "name": profile.name,
                "targetStage": profile.target_stage,
                "repetition": attempt_no,
            },
            "environment": environment.as_dict(),
            "status": "RUNNING",
            "finalStage": None,
            "firstFailure": None,
            "finalFailure": None,
            "appId": None,
            "startedAt": self.now().isoformat(),
            "finishedAt": None,
            "wallSeconds": None,
            "stageTimings": [],
            "events": [],
            "actions": [],
            "llm": {},
            "metricEvidence": [],
            "artifactVersions": {},
            "resumeRecord": None,
            "resumeHistory": [],
            "attempts": [],
        }

    def run_case(
        self,
        case: DatasetCase,
        profile: EvaluationProfile,
        attempt_no: int,
        environment: RunEnvironment,
        *,
        manifest_path: Path | None = None,
    ) -> JsonObject:
        """새 사례를 실행하거나 실패 manifest의 같은 앱에서 재개한다."""
        if case.partition != profile.partition:
            raise ValueError("profile과 dataset의 development/holdout 구분이 다릅니다.")

        previous: JsonObject | None = None
        if manifest_path is not None and manifest_path.exists():
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("재개 manifest는 JSON 객체여야 합니다.")
            previous = loaded
            dataset = previous.get("dataset")
            if not isinstance(dataset, Mapping) or dataset.get("inputDigest") != case.input_digest:
                raise ValueError("재개 manifest와 현재 요구사항 입력 digest가 다릅니다.")
            old_environment = previous.get("environment")
            if not isinstance(old_environment, Mapping) or (
                old_environment.get("settingsDigest") != environment.settings_digest
                or old_environment.get("model") != environment.model
                or old_environment.get("provider") != environment.provider
            ):
                raise ValueError("재개할 때 model, provider, settings를 바꿀 수 없습니다.")
            manifest = previous
            run_id = str(manifest.get("runId") or "")
            if not run_id:
                raise ValueError("재개 manifest에 runId가 없습니다.")
        else:
            run_id = uuid.uuid4().hex
            manifest_path = self.output_root / profile.name / run_id / "manifest.json"
            manifest = self._new_manifest(case, profile, attempt_no, environment, run_id)

        started_at = self.now()
        started_tick = self.monotonic()
        resume_record = manifest.get("resumeRecord") if previous else None
        if previous:
            if not isinstance(resume_record, Mapping) or not resume_record.get("app_id"):
                raise ValueError("실패 위치가 없는 manifest는 재개할 수 없습니다.")
            manifest.setdefault("resumeHistory", []).append(
                {
                    "resumedAt": started_at.isoformat(),
                    "fromStage": resume_record.get("current_stage"),
                    "eventCursor": resume_record.get("event_cursor"),
                }
            )
        manifest["status"] = "RUNNING"
        self._write(manifest_path, manifest)

        prior_evidence = [
            dict(item)
            for item in manifest.get("metricEvidence") or []
            if isinstance(item, Mapping)
        ]
        prior_events = [
            dict(item)
            for item in manifest.get("events") or []
            if isinstance(item, Mapping)
        ]
        prior_actions = [
            dict(item)
            for item in manifest.get("actions") or []
            if isinstance(item, Mapping)
        ]
        prior_stage_timings = [
            dict(item)
            for item in manifest.get("stageTimings") or []
            if isinstance(item, Mapping)
        ]
        prior_wall = manifest.get("wallSeconds")
        previous_wall_seconds = (
            float(prior_wall) if isinstance(prior_wall, (int, float)) else 0.0
        )
        attempt_number = len(manifest.get("attempts") or []) + 1

        def persist_running_progress(recorder: RecordingTransport) -> None:
            """각 공개 HTTP 응답 직후 현재 앱과 계측 근거를 안전하게 저장한다."""
            current_evidence = collect_metric_evidence(
                [*recorder.observations, *recorder.actions]
            )
            combined_evidence = merge_metric_evidence(
                prior_evidence, current_evidence
            )
            manifest["metricEvidence"] = combined_evidence
            manifest["llm"] = summarize_metric_evidence(combined_evidence)
            manifest["events"] = merge_workspace_events(
                prior_events, recorder.events
            )
            manifest["actions"] = merge_actions(prior_actions, recorder.actions)
            manifest["stageTimings"] = [
                *prior_stage_timings,
                *(
                    {"attempt": attempt_number, **item}
                    for item in recorder.stage_timings
                ),
            ]
            manifest["wallSeconds"] = previous_wall_seconds + max(
                0.0, self.monotonic() - started_tick
            )
            if recorder.app_id:
                manifest["appId"] = recorder.app_id
                manifest["resumeRecord"] = recorder.resume_record()
                manifest["finalStage"] = recorder.current_stage
            self._write(manifest_path, manifest)

        recorder = RecordingTransport(
            self.transport_factory(),
            monotonic=self.monotonic,
            now=self.now,
            on_progress=persist_running_progress,
        )
        auto_policy = AutoActionPolicy(
            (lambda _command: case.question_answer) if case.question_answer else None
        )
        policy = _ResumeActionPolicy(auto_policy) if previous else auto_policy
        scenario = self.runner_factory(
            recorder,
            policy=policy,
            timeout_seconds=self.timeout_seconds,
            poll_interval_seconds=self.poll_interval_seconds,
            event_wait_seconds=self.event_wait_seconds,
            monotonic=self.monotonic,
            sleep=time.sleep,
        )

        checkpoint: ProductScenarioCheckpoint | None = None
        failure_report: JsonObject | None = None
        failure_kind: str | None = None
        try:
            if previous:
                assert isinstance(resume_record, Mapping)
                checkpoint = scenario.resume_from(
                    resume_record, stop_after_stage=profile.target_stage
                )
            else:
                checkpoint = scenario.run_until(
                    case.message, stop_after_stage=profile.target_stage
                )
        except ProductScenarioStopped as error:
            failure_report = error.report.as_dict()
            if isinstance(error, ProductScenarioNeedsInput):
                failure_kind = "NEEDS_INPUT"
            elif isinstance(error, ProductScenarioTimeout):
                failure_kind = "TIMEOUT"
            elif isinstance(error, ProductScenarioFailed):
                failure_kind = "FAILED"
            else:
                failure_kind = type(error).__name__
        except Exception as error:  # noqa: BLE001 - 평가 manifest에 첫 실패를 반드시 남긴다.
            failure_kind = type(error).__name__
            failure_report = {
                "app_id": recorder.app_id,
                "last_command_id": None,
                "current_stage": None,
                "event_cursor": recorder.event_cursor,
                "implementation_job_id": None,
                "testing_job_id": None,
                "artifact_versions": {},
                "reason": str(error),
            }

        finished_at = self.now()
        wall_seconds = max(0.0, self.monotonic() - started_tick)
        current_evidence = collect_metric_evidence(
            [*recorder.observations, *recorder.actions]
        )
        combined_evidence = merge_metric_evidence(
            prior_evidence, current_evidence
        )
        prior_evidence_ids = {
            str(item.get("identity") or "") for item in prior_evidence
        }
        new_attempt_evidence = [
            item
            for item in current_evidence
            if str(item.get("identity") or "") not in prior_evidence_ids
        ]
        current_metrics = summarize_metric_evidence(new_attempt_evidence)
        manifest["metricEvidence"] = combined_evidence
        manifest["llm"] = summarize_metric_evidence(combined_evidence)
        manifest["wallSeconds"] = previous_wall_seconds + wall_seconds
        manifest["finishedAt"] = finished_at.isoformat()
        manifest["stageTimings"] = [
            *prior_stage_timings,
            *(
                {"attempt": attempt_number, **item}
                for item in recorder.stage_timings
            ),
        ]
        manifest["events"] = merge_workspace_events(prior_events, recorder.events)
        manifest["actions"] = merge_actions(prior_actions, recorder.actions)

        attempt_record: JsonObject = {
            "number": attempt_number,
            "startedAt": started_at.isoformat(),
            "finishedAt": finished_at.isoformat(),
            "wallSeconds": wall_seconds,
            "llm": current_metrics,
        }
        if checkpoint is not None:
            manifest["status"] = "COMPLETED"
            manifest["finalStage"] = checkpoint.current_stage
            manifest["appId"] = checkpoint.app_id
            manifest["artifactVersions"] = _artifact_manifest(checkpoint)
            manifest["resumeRecord"] = checkpoint.as_dict()
            manifest["finalFailure"] = None
            attempt_record["status"] = "COMPLETED"
        else:
            assert failure_report is not None
            manifest["status"] = failure_kind or "FAILED"
            manifest["finalStage"] = failure_report.get("current_stage")
            manifest["appId"] = failure_report.get("app_id") or recorder.app_id
            manifest["resumeRecord"] = failure_report
            raw_versions = failure_report.get("artifact_versions")
            if isinstance(raw_versions, Mapping):
                manifest["artifactVersions"] = {
                    str(name): {
                        "versionNo": int(version),
                        "digest": None,
                        "fileCount": None,
                        "verifiedFileCount": None,
                    }
                    for name, version in raw_versions.items()
                }
            first_failure = {
                "stage": failure_report.get("current_stage") or "unknown",
                "kind": failure_kind or "FAILED",
                "reason": failure_report.get("reason") or "원인을 확인할 수 없습니다.",
            }
            if not manifest.get("firstFailure"):
                manifest["firstFailure"] = first_failure
            manifest["finalFailure"] = first_failure
            attempt_record["status"] = failure_kind or "FAILED"
            attempt_record["failure"] = first_failure
        manifest.setdefault("attempts", []).append(attempt_record)
        self._write(manifest_path, manifest)
        return manifest

    def run_profile(
        self,
        profile: EvaluationProfile,
        environment: RunEnvironment,
        *,
        catalog: Mapping[str, DatasetCase] | None = None,
    ) -> list[JsonObject]:
        """profile에 적힌 사례와 반복 수만큼 각각 새 run ID로 실행한다."""
        # Python API를 직접 쓰는 경우에도 현재 profile에 포함된 원문만 연다.
        # CLI뿐 아니라 이 기본 경로에서도 development 실행이 holdout 원문을
        # 미리 읽지 않아야 두 데이터 묶음의 역할이 분명하게 유지된다.
        cases = dict(catalog or load_profile_catalog(profile))
        results: list[JsonObject] = []
        for dataset_id in profile.dataset_ids:
            try:
                case = cases[dataset_id]
            except KeyError as error:
                raise ValueError(f"catalog에 profile 입력이 없습니다: {dataset_id}") from error
            for repetition in range(1, profile.repetitions + 1):
                results.append(self.run_case(case, profile, repetition, environment))
        return results


def find_manifests(root: Path) -> Sequence[Path]:
    """집계나 재개 화면에서 사용할 manifest 파일을 정렬해 찾는다."""
    return sorted(root.rglob("manifest.json"))
