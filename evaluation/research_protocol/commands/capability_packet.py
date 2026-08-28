"""개발 세트만 실행해 Capability 독립 검토 패킷을 생성한다."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from app.requirements.resources.capability_extraction import derive_deployment_needs
from evaluation.baselines.common import model, seed, temperature
from evaluation.capability_campaign import make_review, validate_proposals
from evaluation.research_protocol.core.paths import REPOSITORY_ROOT

ROOT = REPOSITORY_ROOT
SUITE = ROOT / "evaluation/easydep/requirements/suite.json"
INPUT_ROOT = SUITE.parent
CONFIRMATORY_CAPABILITY_SAMPLES = 5


def _digest(path: Path) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _revision() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT,
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def build_packet(
    suite_path: Path = SUITE,
    *,
    capability_samples: int = CONFIRMATORY_CAPABILITY_SAMPLES,
) -> dict[str, Any]:
    capability_samples = max(1, int(capability_samples))
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    targets = suite.get("development") or []
    holdout = set(suite.get("holdout") or [])
    if not targets or set(targets) & holdout:
        raise ValueError("development inputs must be non-empty and disjoint from holdout")
    proposals: list[dict[str, Any]] = []
    inputs: list[dict[str, str]] = []
    for relative in targets:
        path = suite_path.parent / relative
        if "holdout" in path.name.casefold():
            raise ValueError(f"holdout-like input is forbidden: {path.name}")
        value = json.loads(path.read_text(encoding="utf-8"))
        dataset = str(value["name"])
        inputs.append({"dataset": dataset, "path": relative, "sha256": _digest(path)})
        result = derive_deployment_needs(
            {"classified": value["classified"]},
            sample_count=capability_samples,
        )
        contract = result["capability_contract"]
        for index, capability in enumerate(contract["capabilities"]):
            proposals.append({
                "proposalId": f"{dataset}:{index}:{capability['id']}",
                "split": "development",
                "dataset": dataset,
                "capabilityId": capability["id"],
                "statement": capability["statement"],
                "requirementIds": capability["requirementIds"],
                "evidenceSpans": capability["evidenceSpans"],
                "origin": capability["origin"],
                "rawScore": capability["rawConfidence"],
                "systemDecision": capability["decision"],
                "decisionReason": capability["decisionReason"],
            })
    packet = {
        "schemaVersion": "easydep-capability-proposals/v1",
        "split": "development",
        "holdoutAccessed": False,
        "suiteSha256": _digest(suite_path),
        "gitRevision": _revision(),
        "configuration": {
            "model": model(),
            "temperature": temperature(),
            "seed": seed(),
            "capabilitySamples": capability_samples,
        },
        "inputs": inputs,
        "proposals": proposals,
    }
    validate_proposals(packet)
    return packet


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--reviewers", nargs="*", default=[])
    parser.add_argument(
        "--capability-samples",
        type=int,
        default=CONFIRMATORY_CAPABILITY_SAMPLES,
    )
    args = parser.parse_args()
    packet = build_packet(capability_samples=args.capability_samples)
    _write(args.output, packet)
    for reviewer in args.reviewers:
        _write(
            args.output.with_name(f"{args.output.stem}-review-{reviewer}.json"),
            make_review(packet, reviewer),
        )
    print(json.dumps({
        "output": str(args.output), "proposalCount": len(packet["proposals"]),
        "inferredCount": sum(item["origin"] == "inferred" for item in packet["proposals"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
