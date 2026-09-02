"""외부 API 없이 비교 실행기 설치 상태를 확인하는 예제 대상."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.comparison.models import SUBJECT_RESULT_SCHEMA


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--framework", required=True)
    parser.add_argument("--tokens", type=int, required=True)
    args = parser.parse_args()
    workspace = args.run_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "app.py").write_text("print('ready')\n", encoding="utf-8")
    result = {
        "schemaVersion": SUBJECT_RESULT_SCHEMA,
        "framework": args.framework,
        "frameworkVersion": "smoke-test",
        "status": "completed",
        "workspace": str(workspace),
        "usage": {
            "inputTokens": args.tokens - 20,
            "outputTokens": 20,
            "totalTokens": args.tokens,
            "llmCalls": 1,
            "missingUsageCalls": 0,
            "source": "example",
        },
        "requirementEvidence": {"REQ-01": {"code": ["app.py"]}},
        "metadata": {},
    }
    (args.run_dir / "subject-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
