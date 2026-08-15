"""Test-only entry point for the fixed Linux runner image."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .adapter import TestingAdapter


def _test(arguments: list[str]) -> int:
    if len(arguments) != 1:
        raise SystemExit("test requires an input JSON path")
    request = json.loads(Path(arguments[0]).read_text(encoding="utf-8"))
    implementation_result = dict(request["implementationResult"])
    # Prevent this inner execution from starting another container runner.
    implementation_result.pop("member_runner", None)
    result = TestingAdapter(
        timeout_seconds=int(request.get("timeoutSeconds", 600))
    ).run(
        implementation_result=implementation_result,
        case_id=str(request.get("caseId", "adhoc")),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = argv or sys.argv[1:]
    if not arguments:
        raise SystemExit("usage: testing_linux_runner test <input-json>")
    if arguments[0] == "test":
        return _test(arguments[1:])
    raise SystemExit(f"unsupported testing runner operation: {arguments[0]}")


if __name__ == "__main__":
    raise SystemExit(main())
