"""Run the deterministic EasyDep arms of the VM dependency experiment."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.cloudkb.infra_planning import plan_for_anchors
from app.orchestration.run_identity import identity_manifest, make_run_id

ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT.parents[2] / "artifacts" / "runs"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(case_id: str, variant: str, output_root: Path = ARTIFACTS) -> Path:
    cases = {case["caseId"]: case for case in _load(ROOT / "cases.json")["cases"]}
    if case_id not in cases:
        raise KeyError(f"unknown case: {case_id}")
    if variant not in {"full", "no-cloud-kb"}:
        raise ValueError("variant must be full or no-cloud-kb")
    case = cases[case_id]
    run_id = make_run_id("easydep", variant, case_id)
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    if variant == "full":
        infra = plan_for_anchors(
            case["anchors"], case["provider"], case["region"]
        )
        plan = dict(infra.design)
        plan["createOrder"] = infra.provision.get("createOrder", [])
    else:
        plan = {
            "view": "design",
            "csp": case["provider"],
            "region": case["region"],
            "nodes": [
                {"id": anchor, "provisioningStatus": "selectedStartResource"}
                for anchor in case["anchors"]
            ],
            "edges": [],
            "kbDisabled": True,
        }
    manifest = identity_manifest(
        run_id,
        system="easydep",
        variant=variant,
        case_id=case_id,
        purpose="evaluation",
        completed_stages=["design"],
    )
    (run_dir / "input.json").write_text(
        json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "cloud-plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id")
    parser.add_argument("--variant", choices=("full", "no-cloud-kb"), required=True)
    parser.add_argument("--output-root", type=Path, default=ARTIFACTS)
    args = parser.parse_args()
    print(run(args.case_id, args.variant, args.output_root))


if __name__ == "__main__":
    main()
