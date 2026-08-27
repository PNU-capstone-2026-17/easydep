"""Run the paired VM capacity/cost/performance knowledge experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.orchestration.vm_selection import select_vm_candidates
from app.orchestration.run_identity import identity_manifest, make_run_id

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT.parents[2] / "artifacts" / "runs"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(case_id: str, variant: str, output_root: Path = ARTIFACTS) -> Path:
    cases = {case["caseId"]: case for case in _load(ROOT / "selection_cases.json")["cases"]}
    if case_id not in cases:
        raise KeyError(f"unknown case: {case_id}")
    if variant not in {"full", "no-vm-knowledge"}:
        raise ValueError("variant must be full or no-vm-knowledge")
    case = cases[case_id]
    result = (
        select_vm_candidates(case["resourceSpec"], case["deploymentNeeds"])
        if variant == "full"
        else {
            "schemaVersion": "easydep-vm-selection/v1",
            "status": "knowledge_unavailable",
            "reason": "vm_catalogs_disabled",
            "kbDisabled": True,
        }
    )
    run_id = make_run_id("easydep", variant, case_id)
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest = identity_manifest(
        run_id,
        system="easydep",
        variant=variant,
        case_id=case_id,
        purpose="evaluation",
        completed_stages=["implementation"],
    )
    for name, value in (
        ("input.json", case),
        ("vm-selection.json", result),
        ("manifest.json", manifest),
    ):
        (run_dir / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id")
    parser.add_argument("--variant", choices=("full", "no-vm-knowledge"), required=True)
    parser.add_argument("--output-root", type=Path, default=ARTIFACTS)
    args = parser.parse_args()
    print(run(args.case_id, args.variant, args.output_root))


if __name__ == "__main__":
    main()
