"""공개 Workspace 통신에서 평가에 필요한 시간·동작·LLM 사용량을 기록한다.

Workspace snapshot은 과거 event 전체를 포함하고, SSE는 같은 event를 다시 보낸다. 다음
command의 snapshot에도 그 과거 event가 들어간다. 따라서 응답을 본 횟수대로 더하면 LLM 호출과
token이 실제보다 커진다. 이 모듈은 먼저 각 측정값을 안정적인 ID가 있는 ``evidence``로 바꾸고,
ID가 같은 evidence를 한 번만 남긴 뒤 합계를 계산한다.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from evaluation.easydep.product_scenario import ProductScenarioTransport

JsonObject = dict[str, Any]
ProgressCallback = Callable[["RecordingTransport"], None]


def _stable_digest(value: object) -> str:
    """JSON 값의 위치와 관계없이 같은 내용에 같은 SHA-256을 만든다."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class _WalkContext:
    """중첩된 metadata가 어느 공개 event와 command에 속하는지 기억한다."""

    event_id: str | None = None
    command_id: str | None = None
    stage: str | None = None


def _walk(
    value: object,
    context: _WalkContext = _WalkContext(),
) -> list[tuple[Mapping[str, Any], _WalkContext]]:
    """중첩 dict를 순회하며 가장 가까운 event_id와 command_id를 함께 반환한다."""
    found: list[tuple[Mapping[str, Any], _WalkContext]] = []
    if isinstance(value, Mapping):
        # RecordingTransport가 붙인 바깥쪽 관측 ID는 metric 정체성이 아니다. 실제 공개
        # payload 안의 event ID나 timing 내용으로 중복을 판단한다.
        if value.get("_evaluationObservationId") and "payload" in value:
            return _walk(value.get("payload"), context)
        event_id = context.event_id
        command_id = context.command_id
        stage = context.stage
        if value.get("event_id") is not None:
            event_id = str(value["event_id"])
        if value.get("command_id"):
            command_id = str(value["command_id"])
        if value.get("stage"):
            stage = str(value["stage"])
        current = _WalkContext(
            event_id=event_id,
            command_id=command_id,
            stage=stage,
        )
        found.append((value, current))
        for child in value.values():
            found.extend(_walk(child, current))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            found.extend(_walk(child, context))
    return found


def _clean_mapping(value: Mapping[str, Any]) -> JsonObject:
    """평가기가 붙인 임시 필드를 제외하고 stable digest용 dict를 만든다."""
    return {
        str(key): item
        for key, item in value.items()
        if not str(key).startswith("_evaluation")
    }


def _evidence(identity: str, kind: str, **values: Any) -> JsonObject:
    return {"identity": identity, "kind": kind, **values}


def collect_metric_evidence(observations: Sequence[object]) -> list[JsonObject]:
    """공개 응답을 중복 제거 가능한 최소 측정 근거 목록으로 바꾼다.

    production 요구사항 단계는 ``metadata.progress_event=llmOperationFinished``에 token과
    structured fallback을 기록한다. 설계 단계는 ``physicalRequest``가 있는 timing event를
    기록한다. 두 모양을 각각 처리하며, Workspace event ID 또는 timing 내용 digest를 ID로 쓴다.
    """
    collected: dict[str, JsonObject] = {}
    for observation in observations:
        for item, context in _walk(observation):
            metadata = item.get("metadata")
            metadata_map = metadata if isinstance(metadata, Mapping) else None
            progress = metadata_map or item

            # 요구사항 LLM 한 번의 실제 공개 progress event다. snapshot과 SSE에 같은
            # event_id가 있으므로 command가 바뀌어도 한 번만 센다.
            if str(progress.get("progress_event") or "") == "llmOperationFinished":
                raw = _clean_mapping(progress)
                event_identity = (
                    f"workspace-event:{context.event_id}"
                    if context.event_id is not None
                    else "requirements-progress:"
                    + _stable_digest(
                        {"commandId": context.command_id, "metadata": raw}
                    )
                )
                collected[event_identity] = _evidence(
                    event_identity,
                    "requirements_call",
                    commandId=context.command_id,
                    stage=context.stage or "requirements",
                    operation=str(progress.get("operation") or ""),
                    promptTokens=int(progress.get("promptTokens") or 0),
                    completionTokens=int(progress.get("completionTokens") or 0),
                    structuredFallback=bool(progress.get("structuredFallback")),
                    status=str(progress.get("status") or ""),
                    errorType=progress.get("errorType"),
                )

            looks_like_timing = any(
                key in item
                for key in (
                    "physicalRequest",
                    "logicalRequestDigest",
                    "inputTokens",
                    "outputTokens",
                    "schemaRepairAttempt",
                    "repairKind",
                    "cacheStatus",
                )
            )
            if looks_like_timing:
                raw = _clean_mapping(item)
                # 동일 timing event가 command 결과와 designLlmMetrics event에 함께 있어도
                # 내용이 같으면 같은 ID가 된다. 시작 시각이 다른 실제 재호출은 다른 ID다.
                identity = f"timing:{_stable_digest(raw)}"
                collected[identity] = _evidence(
                    identity,
                    "timing",
                    **{
                        **raw,
                        "commandId": context.command_id,
                        "stage": context.stage,
                    },
                )

            # requirements 결과의 telemetry 합계는 progress event를 제공하지 않는 구형 또는
            # 잘린 응답에서만 쓰는 보조 자료다. summarize 단계에서 같은 command의 progress
            # event가 있으면 이 합계는 제외한다.
            if "llm_calls" in item and any(
                key in item
                for key in ("prompt_tokens", "completion_tokens", "structured_fallbacks")
            ):
                raw = _clean_mapping(item)
                identity = "telemetry-summary:" + _stable_digest(
                    {"commandId": context.command_id, "value": raw}
                )
                collected[identity] = _evidence(
                    identity,
                    "telemetry_summary",
                    commandId=context.command_id,
                    stage=context.stage,
                    logicalCalls=int(item.get("llm_calls") or 0),
                    physicalCalls=int(item.get("llm_calls") or 0)
                    + int(item.get("structured_fallbacks") or 0),
                    inputTokens=int(item.get("prompt_tokens") or 0),
                    outputTokens=int(item.get("completion_tokens") or 0),
                )

            # RecordingTransport가 만든 action record만 센다. 그 안의 payload에도 같은 action
            # 문자열이 있으므로 selectedAt이 없는 중첩 dict는 의도적으로 제외한다.
            if item.get("selectedAt") and item.get("action"):
                raw = _clean_mapping(item)
                identity = f"action:{_stable_digest(raw)}"
                collected[identity] = _evidence(
                    identity,
                    "action",
                    action=str(item.get("action") or ""),
                    commandId=item.get("commandId"),
                    stage=item.get("stage"),
                )

            # command 자체의 provider 오류도 timing event가 없을 때 분류할 수 있게 남긴다.
            status = str(item.get("status") or "")
            if item.get("command_id") and (
                status in {"FAILED", "INTERRUPTED"} or item.get("error")
            ):
                identity = f"command-error:{item['command_id']}"
                collected[identity] = _evidence(
                    identity,
                    "command_error",
                    stage=context.stage,
                    status=status,
                    error=item.get("error"),
                )
    return list(collected.values())


def merge_metric_evidence(*groups: Sequence[Mapping[str, Any]]) -> list[JsonObject]:
    """재개 전후 evidence를 ID 기준으로 합쳐 오래된 관측의 재합산을 막는다."""
    merged: dict[str, JsonObject] = {}
    for group in groups:
        for item in group:
            identity = str(item.get("identity") or "")
            if identity:
                merged[identity] = dict(item)
    return [merged[key] for key in sorted(merged)]


def _provider_error_category(item: Mapping[str, Any]) -> str | None:
    combined = " ".join(
        str(item.get(key) or "")
        for key in ("status", "errorType", "failureCategory", "error", "httpStatus")
    ).lower()
    if "429" in combined or ("rate" in combined and "limit" in combined):
        return "429"
    if any(code in combined for code in ("500", "502", "503", "504", "5xx")):
        return "5xx"
    if "timeout" in combined or "timed out" in combined:
        return "timeout"
    return None


def summarize_metric_evidence(
    evidence: Sequence[Mapping[str, Any]],
    *,
    required_stages: Sequence[str] = (),
) -> JsonObject:
    """중복 제거된 evidence에서 호출·token·repair·cache 합계를 계산한다."""
    requirements_calls = [item for item in evidence if item.get("kind") == "requirements_call"]
    progress_commands = {
        str(item.get("commandId"))
        for item in requirements_calls
        if item.get("commandId")
    }
    timing = [item for item in evidence if item.get("kind") == "timing"]
    summaries = [
        item
        for item in evidence
        if item.get("kind") == "telemetry_summary"
        and str(item.get("commandId")) not in progress_commands
    ]
    actions = [item for item in evidence if item.get("kind") == "action"]
    provider_sources = [
        item
        for item in evidence
        if item.get("kind") in {"requirements_call", "timing", "command_error"}
    ]

    physical_timing = [item for item in timing if item.get("physicalRequest") is True]
    # cache miss/coalesced 같은 logical-only event와 실제 physical event는 같은
    # logicalRequestDigest를 공유한다. schema repair의 두 번째 physical 요청도 같은 digest다.
    logical_digests = {
        str(item["logicalRequestDigest"])
        for item in timing
        if item.get("logicalRequestDigest")
    }
    logical_without_digest = sum(
        1
        for item in timing
        if not item.get("logicalRequestDigest")
        if (
            item.get("physicalRequest") is True
            and int(item.get("physicalRequestIndex") or 1) == 1
        )
        or item.get("physicalRequest") is False
    )
    logical_timing = len(logical_digests) + logical_without_digest
    logical_calls = (
        len(requirements_calls)
        + logical_timing
        + sum(int(item.get("logicalCalls") or 0) for item in summaries)
    )
    physical_calls = (
        sum(1 + int(bool(item.get("structuredFallback"))) for item in requirements_calls)
        + len(physical_timing)
        + sum(int(item.get("physicalCalls") or 0) for item in summaries)
    )
    input_tokens = (
        sum(int(item.get("promptTokens") or 0) for item in requirements_calls)
        + sum(int(item.get("inputTokens") or 0) for item in physical_timing)
        + sum(int(item.get("inputTokens") or 0) for item in summaries)
    )
    output_tokens = (
        sum(int(item.get("completionTokens") or 0) for item in requirements_calls)
        + sum(int(item.get("outputTokens") or 0) for item in physical_timing)
        + sum(int(item.get("outputTokens") or 0) for item in summaries)
    )

    schema_repairs = sum(
        1
        for item in timing
        if str(item.get("repairKind") or "").lower() == "schema"
        or int(item.get("schemaRepairAttempt") or 0) > 0
    )
    semantic_repairs = sum(
        1
        for item in timing
        if str(item.get("repairKind") or "").lower() == "semantic"
    ) + sum(1 for item in actions if item.get("action") == "delegate_repair")
    handoffs = sum(1 for item in timing if item.get("handoff"))
    repair_evidence_ids = {
        str(item.get("identity") or "")
        for item in timing
        if str(item.get("repairKind") or "").lower() in {"schema", "semantic"}
        or int(item.get("schemaRepairAttempt") or 0) > 0
        or bool(item.get("handoff"))
    }
    repair_evidence_ids.update(
        str(item.get("identity") or "")
        for item in actions
        if item.get("action") == "delegate_repair"
    )
    cache = Counter(
        str(item.get("cacheStatus") or "").lower()
        for item in timing
        if item.get("cacheStatus")
    )
    provider_errors = Counter(
        category
        for item in provider_sources
        if (category := _provider_error_category(item)) is not None
    )

    unavailable: list[str] = []
    calls_observable = bool(requirements_calls or timing or summaries)
    token_observable = bool(
        requirements_calls
        or summaries
        or any(
            any(key in item for key in ("inputTokens", "outputTokens", "totalTokens"))
            for item in physical_timing
        )
    )
    repair_observable = any(
        any(key in item for key in ("repairKind", "schemaRepairAttempt", "handoff"))
        for item in timing
    ) or any(item.get("action") == "delegate_repair" for item in actions)
    cache_observable = any("cacheStatus" in item for item in timing)
    provider_observable = bool(provider_sources or summaries)
    measured_stages = {
        str(item.get("stage") or "")
        for item in [*requirements_calls, *timing, *summaries]
        if item.get("stage")
    }
    missing_stages = [stage for stage in required_stages if stage not in measured_stages]
    coverage_complete = not missing_stages

    if not calls_observable:
        unavailable.append("공개 응답에 LLM timing event나 합계가 없습니다.")
    if not token_observable:
        unavailable.append("공개 응답에 token 사용량이 없습니다.")
    if not repair_observable:
        unavailable.append("공개 응답에 repair 종류와 횟수가 없습니다.")
    if not cache_observable:
        unavailable.append("공개 응답에 cache 결과가 없습니다.")
    if not provider_observable:
        unavailable.append("공개 응답에 provider 오류 상태가 없습니다.")
    if missing_stages:
        unavailable.append(
            "LLM 사용량을 관측하지 못한 단계가 있습니다: "
            + ", ".join(missing_stages)
        )

    expose_whole_run_calls = calls_observable and coverage_complete
    expose_whole_run_tokens = token_observable and coverage_complete
    expose_whole_run_repairs = repair_observable and coverage_complete

    return {
        "logicalCalls": logical_calls if expose_whole_run_calls else None,
        "physicalCalls": physical_calls if expose_whole_run_calls else None,
        "inputTokens": input_tokens if expose_whole_run_tokens else None,
        "outputTokens": output_tokens if expose_whole_run_tokens else None,
        "totalTokens": (
            input_tokens + output_tokens if expose_whole_run_tokens else None
        ),
        "repairs": {
            "schema": schema_repairs if expose_whole_run_repairs else None,
            "semantic": semantic_repairs if expose_whole_run_repairs else None,
            "handoff": handoffs if expose_whole_run_repairs else None,
            "total": len(repair_evidence_ids) if expose_whole_run_repairs else None,
        },
        "cache": {
            "hit": cache["hit"] if cache_observable else None,
            "miss": cache["miss"] if cache_observable else None,
            "bypass": cache["bypass"] if cache_observable else None,
            "singleFlight": (
                cache["single-flight"]
                + cache["single_flight"]
                + cache["coalesced"]
                if cache_observable
                else None
            ),
        },
        "providerErrors": {
            "429": provider_errors["429"] if provider_observable else None,
            "5xx": provider_errors["5xx"] if provider_observable else None,
            "timeout": provider_errors["timeout"] if provider_observable else None,
        },
        "measuredUnavailable": unavailable,
        "coverage": {
            "status": "complete" if coverage_complete else "partial",
            "requiredStages": list(required_stages),
            "measuredStages": sorted(measured_stages),
            "missingStages": missing_stages,
        },
    }


def extract_llm_metrics(observations: Sequence[object]) -> JsonObject:
    """호환용 단일 함수: 공개 응답을 evidence로 바꾼 뒤 즉시 합산한다."""
    return summarize_metric_evidence(collect_metric_evidence(observations))


def _event_identity(event: Mapping[str, Any]) -> str:
    event_id = event.get("event_id")
    if event_id is not None:
        return f"workspace-event:{event_id}"
    return f"workspace-event-content:{_stable_digest(event)}"


def merge_workspace_events(*groups: Sequence[Mapping[str, Any]]) -> list[JsonObject]:
    """snapshot과 SSE, 재개 전후의 같은 Workspace event를 한 번만 남긴다."""
    merged: dict[str, JsonObject] = {}
    for group in groups:
        for event in group:
            merged[_event_identity(event)] = dict(event)
    return [merged[key] for key in sorted(merged)]


def merge_actions(*groups: Sequence[Mapping[str, Any]]) -> list[JsonObject]:
    """저장 재시도 때문에 같은 action record가 두 번 들어가는 일을 막는다."""
    merged: dict[str, JsonObject] = {}
    for group in groups:
        for action in group:
            identity = _stable_digest(action)
            merged[identity] = dict(action)
    return [merged[key] for key in sorted(merged)]


@dataclass
class _StageTiming:
    stage: str
    started_at: str
    started_tick: float
    finished_at: str | None = None
    finished_tick: float | None = None
    statuses: list[str] = field(default_factory=list)

    def as_dict(self) -> JsonObject:
        """manifest에 넣을 수 있는 단계별 시간 정보로 바꾼다."""
        elapsed = None
        if self.finished_tick is not None:
            elapsed = max(0.0, self.finished_tick - self.started_tick)
        return {
            "stage": self.stage,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "wallSeconds": elapsed,
            "statuses": list(self.statuses),
        }


class RecordingTransport(ProductScenarioTransport):
    """실제 transport를 호출하면서 공개 응답과 재개 위치를 기록한다."""

    def __init__(
        self,
        inner: ProductScenarioTransport,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self.inner = inner
        self.monotonic = monotonic
        self.now = now
        self.on_progress = on_progress
        self.app_id = ""
        self.last_command_id: str | None = None
        self.current_stage: str | None = None
        self.event_cursor = 0
        self.implementation_job_id: str | None = None
        self.testing_job_id: str | None = None
        self.artifact_versions: dict[str, int] = {}
        self.observations: list[object] = []
        self.events: list[JsonObject] = []
        self.actions: list[JsonObject] = []
        self._event_identities: set[str] = set()
        self._stage_timings: dict[str, _StageTiming] = {}

    def _timestamp(self) -> str:
        return self.now().isoformat()

    def _notify(self) -> None:
        if self.on_progress is not None:
            self.on_progress(self)

    def _remember_event(self, event: Mapping[str, Any]) -> None:
        identity = _event_identity(event)
        if identity not in self._event_identities:
            self._event_identities.add(identity)
            self.events.append(dict(event))
        event_id = event.get("event_id")
        if isinstance(event_id, int):
            self.event_cursor = max(self.event_cursor, event_id)

    def _observe(self, payload: object) -> None:
        """응답을 metric 입력으로 보관하고 공개 진행 위치를 갱신한다."""
        source = f"payload:{_stable_digest(payload)}"
        if isinstance(payload, Mapping):
            raw_command = payload.get("command")
            command_for_source = (
                raw_command if isinstance(raw_command, Mapping) else payload
            )
            if command_for_source.get("command_id"):
                source = f"command:{command_for_source['command_id']}"
            elif payload.get("event_id") is not None:
                source = f"event:{payload['event_id']}"
        self.observations.append(
            {"_evaluationObservationId": source, "payload": payload}
        )
        if not isinstance(payload, Mapping):
            return

        if payload.get("app_id"):
            self.app_id = str(payload["app_id"])
        raw_events = payload.get("events")
        if isinstance(raw_events, list):
            for event in raw_events:
                if isinstance(event, Mapping):
                    self._remember_event(event)
        if payload.get("event_id") is not None:
            self._remember_event(payload)

        raw_command = payload.get("command")
        command = raw_command if isinstance(raw_command, Mapping) else payload
        command_id = command.get("command_id")
        if command_id:
            self.last_command_id = str(command_id)
        stage = str(command.get("stage") or payload.get("current_stage") or "")
        status = str(command.get("status") or "")
        if stage:
            self.current_stage = stage

        raw_payload = command.get("payload")
        if isinstance(raw_payload, Mapping) and raw_payload.get("implementation_job_id"):
            self.implementation_job_id = str(raw_payload["implementation_job_id"])
        result = command.get("result")
        if isinstance(result, Mapping):
            job_id = result.get("job_id")
            if stage == "implementation" and job_id:
                self.implementation_job_id = str(job_id)
            elif stage == "testing" and job_id:
                self.testing_job_id = str(job_id)
            job = result.get("job")
            if isinstance(job, Mapping) and job.get("implementation_job_id"):
                self.implementation_job_id = str(job["implementation_job_id"])

        if not stage:
            return
        timing = self._stage_timings.get(stage)
        if timing is None:
            timing = _StageTiming(stage, self._timestamp(), self.monotonic())
            self._stage_timings[stage] = timing
        if status and (not timing.statuses or timing.statuses[-1] != status):
            timing.statuses.append(status)
        if status in {"COMPLETED", "FAILED", "INTERRUPTED"}:
            timing.finished_at = self._timestamp()
            timing.finished_tick = self.monotonic()

    @property
    def stage_timings(self) -> list[JsonObject]:
        return [timing.as_dict() for timing in self._stage_timings.values()]

    def finish_current_stage(self) -> None:
        """사용자 입력 대기로 attempt가 끝날 때 열린 stage의 실제 경과 시간을 닫는다."""
        if not self.current_stage:
            return
        timing = self._stage_timings.get(self.current_stage)
        if timing is None or timing.finished_tick is not None:
            return
        timing.finished_at = self._timestamp()
        timing.finished_tick = self.monotonic()

    def resume_record(self, *, reason: str = "평가 실행이 진행 중입니다.") -> JsonObject:
        """프로세스가 갑자기 끝나도 같은 앱에서 재개할 수 있는 공개 위치를 반환한다."""
        return {
            "app_id": self.app_id,
            "last_command_id": self.last_command_id,
            "current_stage": self.current_stage,
            "event_cursor": self.event_cursor,
            "implementation_job_id": self.implementation_job_id,
            "testing_job_id": self.testing_job_id,
            "artifact_versions": dict(self.artifact_versions),
            "reason": reason,
        }

    def create_app(self, message: str) -> Mapping[str, Any]:
        payload = self.inner.create_app(message)
        self._observe(payload)
        self._notify()
        return payload

    def get_workspace(self, app_id: str) -> Mapping[str, Any]:
        payload = self.inner.get_workspace(app_id)
        self.app_id = app_id
        self._observe(payload)
        self._notify()
        return payload

    def submit_command(
        self, app_id: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        action_record: JsonObject = {
            "selectedAt": self._timestamp(),
            "action": str(payload.get("action") or ""),
            "payload": dict(payload),
            "stage": self.current_stage,
        }
        response = self.inner.submit_command(app_id, payload)
        raw_command = response.get("command")
        if isinstance(raw_command, Mapping) and raw_command.get("command_id"):
            action_record["commandId"] = str(raw_command["command_id"])
        self.actions.append(action_record)
        self._observe(response)
        self._notify()
        return response

    def read_events(
        self, app_id: str, after: int, timeout_seconds: float
    ) -> Sequence[Mapping[str, Any]]:
        events = self.inner.read_events(app_id, after, timeout_seconds)
        for event in events:
            self._remember_event(event)
            self._observe(event)
        self._notify()
        return events

    def get_artifacts(self, app_id: str) -> Mapping[str, Any]:
        payload = self.inner.get_artifacts(app_id)
        self._observe(payload)
        self._notify()
        return payload

    def get_stage_versions(
        self, app_id: str, stage: str
    ) -> Sequence[Mapping[str, Any]]:
        versions = self.inner.get_stage_versions(app_id, stage)
        current = next((item for item in versions if item.get("is_current") is True), None)
        selected = current or (versions[-1] if versions else None)
        if selected and selected.get("version_no") is not None:
            self.artifact_versions[stage] = int(selected["version_no"])
        self._observe(list(versions))
        self._notify()
        return versions

    def get_stage_version(
        self, app_id: str, stage: str, version_no: int
    ) -> Mapping[str, Any]:
        payload = self.inner.get_stage_version(app_id, stage, version_no)
        self._observe(payload)
        self._notify()
        return payload

    def get_file_artifact(
        self, app_id: str, artifact_type: str
    ) -> Mapping[str, Any] | None:
        payload = self.inner.get_file_artifact(app_id, artifact_type)
        if payload is not None:
            self._observe(payload)
            if payload.get("version_no") is not None:
                self.artifact_versions[artifact_type] = int(payload["version_no"])
        self._notify()
        return payload

    def get_artifact_file(
        self, app_id: str, artifact_type: str, path: str
    ) -> Mapping[str, Any]:
        payload = self.inner.get_artifact_file(app_id, artifact_type, path)
        self._observe(payload)
        self._notify()
        return payload
