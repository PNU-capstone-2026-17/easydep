"""EasyDep 공개 Workspace API를 끝까지 실행하고 산출물을 평가 폴더로 내보낸다."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any

from evaluation.easydep.product_scenario import (
    HttpProductScenarioTransport,
    ProductScenarioRunner,
    ProductScenarioStopped,
)

from ..adapters.common import write_subject_result
from .artifacts import write_evidence_files
from .common import prompt_sha256, requirement_ids


def _write_artifact(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)):
        path.with_suffix(".json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        text = str(value)
        suffix = ".puml" if "@startuml" in text.lower() else ".md"
        path.with_suffix(suffix).write_text(text, encoding="utf-8")


def _export_product(result: dict[str, Any], workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "raw-product-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    artifacts = result.get("artifacts", {}).get("artifacts", {})
    if isinstance(artifacts, dict):
        for name, value in artifacts.items():
            if value not in (None, "", {}, []):
                _write_artifact(workspace / "design" / str(name), value)


def _copy_implementation(repository: Path, job_id: str | None, workspace: Path) -> None:
    if not job_id:
        return
    source = repository / ".easydep" / "implementation-runs" / job_id
    if not source.is_dir():
        return
    destination = workspace / "implementation"
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(
            ".git", ".gradle", "node_modules", "build", "target", "*.class", "*.jar"
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="실제 EasyDep 비교 arm 실행")
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--timeout-seconds", type=float, default=14400.0)
    args = parser.parse_args(argv)
    repository = args.repository.resolve()
    run_dir = args.run_dir.resolve()
    prompt_file = args.prompt_file.resolve()
    workspace = run_dir / "workspace"
    run_dir.mkdir(parents=True, exist_ok=True)
    runner = ProductScenarioRunner(
        HttpProductScenarioTransport(args.base_url), timeout_seconds=args.timeout_seconds
    )
    try:
        product = runner.run(
            prompt_file.read_text(encoding="utf-8"), stop_after_stage="testing"
        )
        raw = product.as_dict()
        _export_product(raw, workspace)
        _copy_implementation(repository, product.implementation_job_id, workspace)
        status = "completed"
        exit_code = 0
        location = product.location.as_dict()
    except ProductScenarioStopped as error:
        raw = {"location": error.location.as_dict(), "error": str(error)}
        _export_product(raw, workspace)
        status = "failed"
        exit_code = 2
        location = error.location.as_dict()
    artifact_path, requirement_path, _ = write_evidence_files(
        workspace, run_dir, requirement_ids(prompt_file)
    )
    write_subject_result(
        run_dir / "subject-result.json",
        framework="EasyDep",
        framework_version="current",
        status=status,
        workspace=workspace,
        input_tokens=None,
        output_tokens=None,
        total_tokens=None,
        llm_calls=None,
        missing_usage_calls=0,
        source="not-reported",
        evidence=json.loads(requirement_path.read_text(encoding="utf-8")),
        artifact_evidence=json.loads(artifact_path.read_text(encoding="utf-8")),
        metadata={
            "appId": location.get("app_id"),
            "promptSha256": prompt_sha256(prompt_file),
            "baseUrl": args.base_url,
        },
    )
    print(f"EasyDep workspace: {workspace}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

