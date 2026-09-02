"""ChatDev 로그의 provider usage를 공통 결과로 변환한다."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from .common import load_evidence, write_subject_result

PROMPT = re.compile(r"\bprompt_tokens\s*[:=]\s*(\d+)", re.IGNORECASE)
COMPLETION = re.compile(r"\bcompletion_tokens\s*[:=]\s*(\d+)", re.IGNORECASE)


def parse_chatdev_usage(text: str) -> dict[str, int | str | None]:
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
        "source": "chatdev-log-provider-usage" if calls else "not-reported",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ChatDev 로그를 비교 공통 결과로 변환")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--framework-version", default="1.1.6")
    parser.add_argument("--status", choices=["completed", "failed", "timeout"], default="completed")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args(argv)
    usage = parse_chatdev_usage(args.log.read_text(encoding="utf-8", errors="replace"))
    write_subject_result(
        args.output,
        framework="ChatDev",
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
