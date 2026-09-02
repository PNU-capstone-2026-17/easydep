from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import SUBJECT_RESULT_SCHEMA


def load_evidence(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("evidence JSON은 요구사항 ID를 키로 갖는 객체여야 합니다.")
    return value


def write_subject_result(
    output: Path,
    *,
    framework: str,
    framework_version: str,
    status: str,
    workspace: Path,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    llm_calls: int | None,
    missing_usage_calls: int,
    source: str,
    evidence: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schemaVersion": SUBJECT_RESULT_SCHEMA,
                "framework": framework,
                "frameworkVersion": framework_version,
                "status": status,
                "workspace": str(workspace.resolve()),
                "usage": {
                    "inputTokens": input_tokens,
                    "outputTokens": output_tokens,
                    "totalTokens": total_tokens,
                    "llmCalls": llm_calls,
                    "missingUsageCalls": missing_usage_calls,
                    "source": source,
                },
                "requirementEvidence": evidence,
                "metadata": metadata or {},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
