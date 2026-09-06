"""Record MetaGPT provider-reported token usage independently of its price table.

Python imports ``sitecustomize`` during interpreter startup when this directory is
prepended to ``PYTHONPATH``. The comparison launcher supplies output paths through
environment variables. This hook records only token counts and model identifiers;
it never records prompts, responses, or API keys.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


USAGE_PATH_VARIABLE = "EASYDEP_METAGPT_USAGE_LOG"
STATUS_PATH_VARIABLE = "EASYDEP_METAGPT_USAGE_STATUS"
EVENT_SCHEMA = "easydep-metagpt-provider-usage-event/v1"
STATUS_SCHEMA = "easydep-metagpt-usage-instrumentation/v1"


def _write_status(status: str, detail: str) -> None:
    raw_path = os.environ.get(STATUS_PATH_VARIABLE, "")
    if not raw_path:
        return
    try:
        path = Path(raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": STATUS_SCHEMA,
                    "status": status,
                    "detail": detail,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        # Instrumentation must not change the generated application or MetaGPT flow.
        pass


def _install() -> None:
    usage_path_value = os.environ.get(USAGE_PATH_VARIABLE, "")
    if not usage_path_value:
        return

    try:
        from metagpt.utils.cost_manager import CostManager
    except Exception as exc:  # pragma: no cover - exposed by the status artifact
        _write_status("installationFailed", f"CostManager import failed: {type(exc).__name__}: {exc}")
        return

    if getattr(CostManager.update_cost, "__easydep_usage_instrumented__", False):
        _write_status("installed", "CostManager.update_cost was already instrumented.")
        return

    original_update_cost = CostManager.update_cost
    usage_path = Path(usage_path_value)
    lock = threading.Lock()
    sequence = 0

    def instrumented_update_cost(
        self: Any,
        prompt_tokens: int,
        completion_tokens: int,
        model: str,
    ) -> Any:
        nonlocal sequence
        try:
            prompt = int(prompt_tokens)
            completion = int(completion_tokens)
            with lock:
                sequence += 1
                event = {
                    "schemaVersion": EVENT_SCHEMA,
                    "eventId": f"{os.getpid()}-{sequence}",
                    "sequence": sequence,
                    "recordedAtUtc": datetime.now(UTC).isoformat(),
                    "model": str(model),
                    "promptTokens": prompt,
                    "completionTokens": completion,
                    "totalTokens": prompt + completion,
                }
                usage_path.parent.mkdir(parents=True, exist_ok=True)
                with usage_path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception as exc:  # pragma: no cover - parent detects non-complete status
            _write_status("writeFailed", f"Usage event write failed: {type(exc).__name__}: {exc}")
        return original_update_cost(self, prompt_tokens, completion_tokens, model)

    setattr(instrumented_update_cost, "__easydep_usage_instrumented__", True)
    CostManager.update_cost = instrumented_update_cost
    _write_status(
        "installed",
        "CostManager.update_cost records provider-reported tokens before price-table lookup.",
    )


_install()
