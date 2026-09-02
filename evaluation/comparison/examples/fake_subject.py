"""외부 API 없이 비교 실행기 설치 상태를 확인하는 예제 대상."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from evaluation.comparison.models import SUBJECT_RESULT_SCHEMA


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--framework", required=True)
    parser.add_argument("--tokens", type=int, required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.run_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    prompt = args.prompt_file.read_text(encoding="utf-8")
    (workspace / "app.py").write_text("print('ready')\n", encoding="utf-8")
    (workspace / "requirements.md").write_text("# Requirements\n", encoding="utf-8")
    (workspace / "class-diagram.puml").write_text("@startuml\nclass App\n@enduml\n", encoding="utf-8")
    (workspace / "sequence-diagram.puml").write_text("@startuml\nactor User\n@enduml\n", encoding="utf-8")
    (workspace / "openapi.json").write_text("{}\n", encoding="utf-8")
    (workspace / "erd.puml").write_text("@startuml\nentity app\n@enduml\n", encoding="utf-8")
    tests = workspace / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "test_app.py").write_text("def test_ready(): assert True\n", encoding="utf-8")
    (workspace / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (workspace / "main.tf").write_text("terraform {}\n", encoding="utf-8")
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
        "requirementEvidence": {
            "REQ-01": {
                "design": ["class-diagram.puml", "sequence-diagram.puml"],
                "api": ["openapi.json"],
                "code": ["app.py"],
                "test": ["tests/test_app.py"],
            }
        },
        "artifactEvidence": {
            "requirements": ["requirements.md"],
            "classDiagram": ["class-diagram.puml"],
            "sequenceDiagram": ["sequence-diagram.puml"],
            "apiSpecification": ["openapi.json"],
            "dataModel": ["erd.puml"],
            "sourceCode": ["app.py"],
            "tests": ["tests/test_app.py"],
            "container": ["Dockerfile"],
            "infrastructure": ["main.tf"],
        },
        "metadata": {
            "promptSha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        },
    }
    (args.run_dir / "subject-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
