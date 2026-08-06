"""Validate an independent review and freeze it as the component gold set."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PACKET = ROOT / "review_packet.json"
GOLD = ROOT / "gold.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def packet_sha256() -> str:
    return hashlib.sha256(PACKET.read_bytes()).hexdigest()


def validate_review(review: dict) -> dict:
    packet = _load(PACKET)
    reviewer = str(review.get("reviewerId") or "").strip()
    reviewed_at = str(review.get("reviewedAt") or "").strip()
    if not reviewer or reviewer.lower() in {"todo", "pending", "unknown"}:
        raise ValueError("a non-placeholder reviewerId is required")
    if review.get("independenceAttestation") is not True:
        raise ValueError("independenceAttestation=true is required")
    try:
        datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("reviewedAt must be an ISO-8601 timestamp") from exc

    allowed = set(packet["allowedResourceTerms"])
    expected_ids = [case["caseId"] for case in packet["cases"]]
    submitted = review.get("cases")
    if not isinstance(submitted, list):
        raise ValueError("cases must be a list")
    by_id = {case.get("caseId"): case for case in submitted}
    if set(by_id) != set(expected_ids) or len(by_id) != len(submitted):
        raise ValueError("review must contain every packet case exactly once")

    frozen: dict[str, object] = {
        "_metadata": {
            "schemaVersion": "easydep-cloud-resource-gold/v2",
            "independenceStatus": "independently-reviewed",
            "reviewerId": reviewer,
            "reviewedAt": reviewed_at,
            "independenceAttestation": True,
            "reviewPacketSha256": packet_sha256(),
            "rule": "Independent reviewer used only review_packet.json sources and did not inspect system output.",
            "reviewRequired": False,
        }
    }
    for case_id in expected_ids:
        case = by_id[case_id]
        nodes = case.get("mandatoryNodes")
        relations = case.get("mandatoryRelations")
        rationale = str(case.get("rationale") or "").strip()
        if not isinstance(nodes, list) or not nodes or not set(nodes) <= allowed:
            raise ValueError(f"{case_id}: mandatoryNodes must use registered terms")
        if len(nodes) != len(set(nodes)):
            raise ValueError(f"{case_id}: duplicate mandatory node")
        if not isinstance(relations, list):
            raise ValueError(f"{case_id}: mandatoryRelations must be a list")
        normalized: list[list[str]] = []
        for relation in relations:
            if not isinstance(relation, list) or len(relation) != 2:
                raise ValueError(f"{case_id}: every relation must be [subject, object]")
            if relation[0] not in nodes or relation[1] not in nodes:
                raise ValueError(f"{case_id}: relation endpoint is not a mandatory node")
            normalized.append([str(relation[0]), str(relation[1])])
        if not rationale:
            raise ValueError(f"{case_id}: rationale is required")
        frozen[case_id] = {
            "mandatoryNodes": sorted(nodes),
            "mandatoryRelations": sorted(normalized),
            "rationale": rationale,
        }
    return frozen


def freeze(review_path: Path, output: Path = GOLD) -> Path:
    frozen = validate_review(_load(review_path))
    output.write_text(json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("review", type=Path, help="completed copy of review_packet.json")
    parser.add_argument("--output", type=Path, default=GOLD)
    args = parser.parse_args()
    print(freeze(args.review, args.output))


if __name__ == "__main__":
    main()
