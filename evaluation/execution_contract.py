"""Orthogonal outcome, censoring, and budget controls for cloud experiments."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

SubjectOutcome = Literal["pass", "fail", "notObserved"]
ExecutionStatus = Literal["completed", "censored", "infrastructureFailure"]
Phase = Literal["preflight", "create", "stabilize", "mutate", "delete", "cleanup"]
CensorReason = Literal[
    "measurementWallClock", "costCap", "providerOperationTimeout",
    "providerThrottling", "schedulerDelay", "llmEndpointTimeout",
    "llmResponseCompletionTimeout",
    "cleanupDeadline", "unknown",
]

MEASUREMENT_WINDOW_SECONDS = 45 * 60
CLEANUP_WINDOW_SECONDS = 60 * 60
BUNDLE_COST_CAP_USD = 10.0
CAMPAIGN_COST_CAP_USD = 150.0
CLEANUP_RESERVE_USD = 15.0
NEW_WORK_COST_LIMIT_USD = CAMPAIGN_COST_CAP_USD - CLEANUP_RESERVE_USD
TRANSIENT_RETRY_DELAYS_SECONDS = (15, 30)


@dataclass(frozen=True)
class OperationEvent:
    phase: Phase
    started_at: str
    finished_at: str | None = None
    provider_operation_id: str | None = None
    status: str = "running"
    retry: int = 0


@dataclass
class ExecutionRecord:
    subject_outcome: SubjectOutcome = "notObserved"
    execution_status: ExecutionStatus = "completed"
    phase: Phase = "preflight"
    censor_reason: CensorReason | None = None
    provider_operation_ids: list[str] = field(default_factory=list)
    events: list[OperationEvent] = field(default_factory=list)
    queue_delay_seconds: float = 0.0
    provider_wait_seconds: float = 0.0
    active_execution_seconds: float = 0.0
    cleanup_seconds: float = 0.0
    estimated_cost_usd: float = 0.0
    actual_cost_usd: float = 0.0
    residual_resources: list[str] = field(default_factory=list)
    protocol_deviations: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        return {
            "subjectOutcome": value["subject_outcome"],
            "executionStatus": value["execution_status"],
            "phase": value["phase"],
            "censorReason": value["censor_reason"],
            "providerOperationIds": value["provider_operation_ids"],
            "events": value["events"],
            "queueDelaySeconds": value["queue_delay_seconds"],
            "providerWaitSeconds": value["provider_wait_seconds"],
            "activeExecutionSeconds": value["active_execution_seconds"],
            "cleanupSeconds": value["cleanup_seconds"],
            "estimatedCostUSD": value["estimated_cost_usd"],
            "actualCostUSD": value["actual_cost_usd"],
            "residualResources": value["residual_resources"],
            "protocolDeviations": value["protocol_deviations"],
        }


def censored(*, phase: Phase, reason: CensorReason, elapsed_seconds: float) -> dict[str, Any]:
    return ExecutionRecord(
        execution_status="censored",
        phase=phase,
        censor_reason=reason,
        active_execution_seconds=elapsed_seconds,
    ).as_dict()


def may_start_bundle(
    *, actual_campaign_cost_usd: float, estimated_bundle_cost_usd: float,
    residual_resources: list[str] | None = None,
) -> tuple[bool, str]:
    if residual_resources:
        return False, "residual-resources-block-provider"
    if estimated_bundle_cost_usd > BUNDLE_COST_CAP_USD:
        return False, "bundle-estimate-exceeds-cap"
    if actual_campaign_cost_usd + estimated_bundle_cost_usd > NEW_WORK_COST_LIMIT_USD:
        return False, "cleanup-reserve-would-be-consumed"
    return True, "within-budget"


def retry_delay(retry_number: int) -> int | None:
    """Return the preregistered delay for transient 429/5xx retries."""
    if 1 <= retry_number <= len(TRANSIENT_RETRY_DELAYS_SECONDS):
        return TRANSIENT_RETRY_DELAYS_SECONDS[retry_number - 1]
    return None
