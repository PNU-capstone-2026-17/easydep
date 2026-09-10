"""Run the exact configured OpenHands model/tool compatibility canary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import settings
from app.implementation.agents.canary import ensure_model_tool_canary
from app.implementation.agents.provider import openhands_connection


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-root",
        type=Path,
        default=Path("artifacts/openhands-harness-validation"),
    )
    parser.add_argument(
        "--tool-mode",
        choices=("restricted", "terminal"),
        default=settings.implementation_owner_tool_mode,
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=settings.implementation_openhands_canary_repetitions,
    )
    args = parser.parse_args()
    result = ensure_model_tool_canary(
        args.run_root.resolve(),
        openhands_connection(),
        {
            "temperature": settings.implementation_agent_temperature,
            "maxOutputTokens": settings.implementation_agent_max_output_tokens,
        },
        owner_tool_mode=args.tool_mode,
        reasoning_effort=settings.implementation_reasoning_effort,
        repetitions=args.repetitions,
        max_attempts=settings.implementation_openhands_canary_max_attempts,
        transient_failure_ttl_seconds=(
            settings.implementation_openhands_canary_transient_ttl_seconds
        ),
        retry_min_wait_seconds=settings.implementation_openhands_retry_min_wait_seconds,
        retry_max_wait_seconds=settings.implementation_openhands_retry_max_wait_seconds,
        retry_multiplier=settings.implementation_openhands_retry_multiplier,
    )
    summary = {
        "canaryResultId": result["canaryResultId"],
        "passed": result["passed"],
        "repetitions": result["repetitions"],
        "attemptCount": result["attemptCount"],
        "successfulAttempts": result["successfulAttempts"],
        "transientFailureCount": result["transientFailureCount"],
        "endpointRetryCount": result["endpointRetryCount"],
        "endpointHealth": result["endpointHealth"],
        "attempts": [
            {
                "passed": attempt["passed"],
                "executionStatus": attempt["executionStatus"],
                "actions": attempt["actions"],
                "terminationReason": attempt["terminationReason"],
                "durationMs": attempt["durationMs"],
                "backoffMs": attempt.get("backoffMs", 0),
                "endpointRetries": attempt.get("endpointRetries"),
            }
            for attempt in result["attempts"]
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
