"""MetaGPT CostManager JSON 또는 로그를 공통 결과로 변환한다."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from .common import load_evidence, write_subject_result

PROMPT = re.compile(r"\bprompt_tokens\s*[:=]\s*(\d+)", re.IGNORECASE)
COMPLETION = re.compile(r"\bcompletion_tokens\s*[:=]\s*(\d+)", re.IGNORECASE)


def parse_metagpt_log(text: str) -> dict[str, int | str | None]:
    prompts = [int(value) for value in PROMPT.findall(text)]
    completions = [int(value) for value in COMPLETION.findall(text)]
    calls = max(len(prompts), len(completions))
    input_tokens = sum(prompts) if prompts else None
    output_tokens = sum(completions) if completions else None
    return {
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "totalTokens": (
            input_tokens + output_tokens
            if input_tokens is not None and output_tokens is not None
            else None
        ),
        "llmCalls": calls or None,
        "missingUsageCalls": abs(len(prompts) - len(completions)),
        "source": "metagpt-log-provider-usage" if calls else "not-reported",
    }


def parse_cost_manager(data: dict[str, Any]) -> dict[str, int | str | None]:
    prompt = data.get("total_prompt_tokens", data.get("totalPromptTokens"))
    completion = data.get("total_completion_tokens", data.get("totalCompletionTokens"))
    prompt = int(prompt) if prompt is not None else None
    completion = int(completion) if completion is not None else None
    return {
        "inputTokens": prompt,
        "outputTokens": completion,
        "totalTokens": None if prompt is None or completion is None else prompt + completion,
        "llmCalls": (
            int(data.get("llm_calls", data.get("llmCalls")))
            if data.get("llm_calls", data.get("llmCalls")) is not None
            else None
        ),
        "missingUsageCalls": int(data.get("missing_usage_calls", data.get("missingUsageCalls", 0))),
        "source": "metagpt-cost-manager-provider-usage",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MetaGPT 사용량을 비교 공통 결과로 변환")
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--cost-manager-json", type=Path)
    sources.add_argument("--log", type=Path)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--framework-version", default="0.8.2")
    parser.add_argument("--status", choices=["completed", "failed", "timeout"], default="completed")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args(argv)
    if args.cost_manager_json:
        raw = json.loads(args.cost_manager_json.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("CostManager JSON은 객체여야 합니다.")
        usage = parse_cost_manager(raw)
    else:
        usage = parse_metagpt_log(args.log.read_text(encoding="utf-8", errors="replace"))
    write_subject_result(
        args.output,
        framework="MetaGPT",
        framework_version=args.framework_version,
        status=args.status,
        workspace=args.workspace,
        input_tokens=usage["inputTokens"],
        output_tokens=usage["outputTokens"],
        total_tokens=usage["totalTokens"],
        llm_calls=usage["llmCalls"],
        missing_usage_calls=int(usage["missingUsageCalls"]),
        source=str(usage["source"]),
        evidence=load_evidence(args.evidence),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
