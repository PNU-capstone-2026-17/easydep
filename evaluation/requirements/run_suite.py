r"""Run and score a requirements benchmark split.

Run from the repository root:
  .venv\Scripts\python.exe -m evaluation.requirements.run_suite --split development
  .venv\Scripts\python.exe -m evaluation.requirements.run_suite --split holdout
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.requirements.common import telemetry
from app.requirements.runner import ARTIFACTS_DIR, load_input, persist_run, run_pipeline
from evaluation.requirements.evaluate import ROOT, score, verify_holdout_hashes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--split",
        choices=("development", "holdout", "domainExpansion"),
        required=True,
    )
    parser.add_argument("--output", type=Path, default=ARTIFACTS_DIR)
    args = parser.parse_args()

    if args.split == "holdout":
        failures = verify_holdout_hashes()
        if failures:
            raise SystemExit(f"Holdout inputs changed; refusing to run: {failures}")

    suite = json.loads((ROOT / "suite.json").read_text(encoding="utf-8"))
    oracle = json.loads((ROOT / "oracle.json").read_text(encoding="utf-8"))
    targets = suite[args.split]
    if not targets:
        print(f"No inputs registered for split: {args.split}")
        return 0

    scores = []
    for relative in targets:
        path = ROOT / relative
        obj = load_input(str(path))
        dataset = obj["name"]
        print(f"[run] {dataset}")
        with telemetry.run_scope(f"benchmark:{args.split}:{dataset}") as stats:
            state = run_pipeline(
                obj.get("classified") or [],
                resource_constraints_text=str(obj.get("resource_constraints_text") or ""),
                resource_answers=obj.get("resource_answers") or {},
            )
        run_dir = persist_run(
            obj,
            state,
            dataset_name=dataset,
            artifact_root=args.output,
            run_metrics=stats.as_dict(),
        )
        print(f"  artifact: {run_dir}")
        if dataset in oracle:
            result = score(run_dir)
            scores.append(result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("  score: skipped (add an oracle entry to enable scoring)")

    if scores:
        macro = {
            "applications": len(scores),
            "meanCoverage": sum(row["coverage"] for row in scores) / len(scores),
            "meanActorRecall": sum(row["actorRecall"] for row in scores) / len(scores),
            "meanRoleAccuracy": sum(row["roleAccuracy"] for row in scores) / len(scores),
            "totalSpecificationIssues": sum(row["specificationIssueCount"] for row in scores),
            "totalLlmCalls": sum(row["llmCalls"] for row in scores),
            "totalTokens": sum(row["totalTokens"] for row in scores),
            "totalWallSeconds": round(sum(row["wallSeconds"] for row in scores), 3),
        }
        print("[macro]")
        print(json.dumps(macro, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
