"""Single-LLM chain-of-thought-prompted baseline.

The model receives no EasyDep KB or intermediate agent output. It makes one request
and returns the complete experiment artifact bundle. Only a concise decision rationale
is retained; private token-level reasoning is neither requested nor scored.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

from evaluation.baselines.common import (
    ExperimentCase,
    base_url,
    begin_run,
    model,
    require_api_key,
    run_manifest,
    seed,
    temperature,
    write_json,
)

SYSTEM = """You are the single-agent baseline in a controlled software-engineering experiment.
Work through requirements, design, implementation, and tests in that order. Check your work
step by step before answering, but expose only a concise decision rationale, not hidden reasoning.
Do not browse the web and do not assume access to EasyDep or any cloud knowledge base.

Build a Java 21 Spring Boot Gradle application for Docker-on-VM deployment. Support only the
CSP and region stated by the user. Return exactly one JSON object with these keys:
- rationale: array of short design decisions
- unresolved: array of unresolved constraints or contradictions
- requirements: Markdown requirements with R1... trace identifiers
- design: Markdown architecture and deployment decisions
- deploymentDiagram: Mermaid flowchart text
- files: object mapping relative repository paths to complete UTF-8 file contents
- traceability: array of {requirementId, designElements, codeFiles, testFiles}

The files must form a buildable repository and include Dockerfile, application source, tests,
deployment documentation or IaC, and README instructions. Never include credentials. Paths must
be relative and must not contain '..'. Use only Docker on Linux VMs; do not use Kubernetes or
managed application platforms."""


def _extract_object(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model response did not contain a JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise TypeError("model response root must be an object")
    for field in ("rationale", "unresolved", "requirements", "design",
                  "deploymentDiagram", "files", "traceability"):
        if field not in value:
            raise ValueError(f"model response missing field: {field}")
    return value


def _safe_files(files: object) -> dict[str, str]:
    if not isinstance(files, dict):
        raise TypeError("files must be an object")
    result: dict[str, str] = {}
    for name, content in files.items():
        path = Path(str(name))
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe generated path: {name}")
        result[path.as_posix()] = str(content)
    return result


def run(case_path: Path, output_root: Path | None = None, dry_run: bool = False) -> Path:
    case = ExperimentCase.load(case_path)
    command = ["python", "-m", "evaluation.baselines.cot", str(case_path)]
    run_dir = begin_run("cot", case, output_root)
    manifest = run_manifest("cot", case, command, run_dir.name)
    write_json(run_dir / "input.json", {
        "caseId": case.case_id,
        "requirements": case.requirements,
        "cloudConstraints": case.cloud_constraints,
        "scope": case.scope,
    })
    if dry_run:
        manifest.update({"status": "dry-run", "finishedAt": time.time()})
        write_json(run_dir / "manifest.json", manifest)
        (run_dir / "prompt.txt").write_text(SYSTEM + "\n\n" + case.prompt(), encoding="utf-8")
        return run_dir

    require_api_key()
    started = time.perf_counter()
    client = OpenAI(
        api_key=os.environ["API_KEY"],
        base_url=base_url(),
        timeout=300,
        max_retries=2,
    )
    response = client.chat.completions.create(
        model=model(),
        temperature=temperature(),
        seed=seed(),
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": case.prompt()}],
    )
    text = response.choices[0].message.content or ""
    (run_dir / "raw-response.txt").write_text(text, encoding="utf-8")
    bundle = _extract_object(text)
    files = _safe_files(bundle["files"])
    repo = run_dir / "repo"
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (run_dir / "requirements.md").write_text(str(bundle["requirements"]), encoding="utf-8")
    (run_dir / "design.md").write_text(str(bundle["design"]), encoding="utf-8")
    (run_dir / "deployment.mmd").write_text(str(bundle["deploymentDiagram"]), encoding="utf-8")
    write_json(run_dir / "traceability.json", bundle["traceability"])
    write_json(run_dir / "decisions.json", {
        "rationale": bundle["rationale"], "unresolved": bundle["unresolved"]
    })
    usage = response.usage
    manifest.update({
        "status": "completed",
        "completedStages": ["requirements", "design", "implementation", "testing"],
        "elapsedSeconds": round(time.perf_counter() - started, 3),
        "promptTokens": getattr(usage, "prompt_tokens", None),
        "completionTokens": getattr(usage, "completion_tokens", None),
        "totalTokens": getattr(usage, "total_tokens", None),
        "systemFingerprint": getattr(response, "system_fingerprint", None),
    })
    write_json(run_dir / "manifest.json", manifest)
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the single-LLM CoT baseline")
    parser.add_argument("case", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(run(args.case, args.output_root, args.dry_run))


if __name__ == "__main__":
    main()
