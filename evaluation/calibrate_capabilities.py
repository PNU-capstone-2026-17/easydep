"""개발 세트 검토 정답으로 CapabilityContract 자동수락 정책을 생성한다."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app.requirements.capability_contract import CalibrationPoint, fit_policy


def load_labels(path: Path) -> list[CalibrationPoint]:
    points: list[CalibrationPoint] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value: dict[str, Any] = json.loads(line)
        if value.get("split") != "development":
            raise ValueError(f"line {line_number}: only development labels are allowed")
        if value.get("origin") != "inferred":
            raise ValueError(f"line {line_number}: only inferred capabilities calibrate policy")
        if not isinstance(value.get("correct"), bool):
            raise TypeError(f"line {line_number}: correct must be boolean")
        score = float(value.get("rawScore"))
        if not 0 <= score <= 1:
            raise ValueError(f"line {line_number}: rawScore must be between zero and one")
        if not value.get("reviewerA") or not value.get("reviewerB"):
            raise ValueError(f"line {line_number}: two reviewer identities are required")
        if value["reviewerA"] == value["reviewerB"]:
            raise ValueError(f"line {line_number}: reviewer identities must be distinct")
        points.append(CalibrationPoint(score, value["correct"]))
    if not points:
        raise ValueError("no development calibration labels")
    return points


def build_policy(labels_path: Path, *, version: str) -> dict[str, Any]:
    policy = fit_policy(load_labels(labels_path), version=version)
    policy["labelsSha256"] = hashlib.sha256(labels_path.read_bytes()).hexdigest()
    return policy


def build_conservative_policy(proposals_path: Path, *, version: str) -> dict[str, Any]:
    packet = json.loads(proposals_path.read_text(encoding="utf-8"))
    if packet.get("schemaVersion") != "easydep-capability-proposals/v1":
        raise ValueError("unsupported capability proposal packet")
    if packet.get("split") != "development" or packet.get("holdoutAccessed") is not False:
        raise ValueError("conservative policy requires a development-only packet")
    proposals = packet.get("proposals") or []
    inferred = [item for item in proposals if item.get("origin") == "inferred"]
    if inferred:
        raise ValueError("inferred proposals exist; use independent labels and calibration")
    canonical = json.dumps(
        packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "schemaVersion": "easydep-capability-threshold/v1",
        "status": "frozen",
        "version": version,
        "method": "development-no-inference-always-question",
        "sampleCount": 0,
        "targetPrecision": 0.90,
        "minimumWilsonLower95": 0.80,
        "autoAcceptEnabled": False,
        "acceptThreshold": None,
        "mapping": [],
        "qualification": {
            "reason": "no-inferred-proposals-in-development-campaign",
            "proposalCount": len(proposals),
            "inferredCount": 0,
        },
        "proposalsSha256": hashlib.sha256(canonical).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("labels", type=Path, nargs="?")
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--version", required=True)
    parser.add_argument("--no-inferred-proposals", type=Path)
    args = parser.parse_args()
    if args.no_inferred_proposals and args.output is None and args.labels is not None:
        args.output, args.labels = args.labels, None
    if args.output is None:
        parser.error("output path is required")
    if bool(args.labels) == bool(args.no_inferred_proposals):
        parser.error("provide labels or --no-inferred-proposals, but not both")
    policy = (
        build_conservative_policy(args.no_inferred_proposals, version=args.version)
        if args.no_inferred_proposals
        else build_policy(args.labels, version=args.version)
    )
    args.output.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "output": str(args.output),
        "autoAcceptEnabled": policy["autoAcceptEnabled"],
        "acceptThreshold": policy["acceptThreshold"],
        "evidenceSha256": policy.get("labelsSha256") or policy.get("proposalsSha256"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
