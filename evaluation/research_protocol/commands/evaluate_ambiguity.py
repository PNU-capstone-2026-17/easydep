"""동결된 선택 정책의 수락·질문·보류 결정을 개발 사례로 평가한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.requirements.capability_contract import decide, load_policy
from evaluation.research_protocol.core.paths import PROTOCOL_ROOT, REPOSITORY_ROOT
from evaluation.research_protocol.core.selective_metrics import score_selective_decisions

HERE = PROTOCOL_ROOT
DEFAULT_CASES = HERE / "protocols" / "ambiguity-cases.json"
DEFAULT_POLICY = (
    REPOSITORY_ROOT / "app/requirements/knowledge/capability-threshold.json"
)


def evaluate(cases_path: Path = DEFAULT_CASES, policy_path: Path = DEFAULT_POLICY) -> dict[str, Any]:
    packet = json.loads(cases_path.read_text(encoding="utf-8"))
    if packet.get("schemaVersion") != "easydep-ambiguity-cases/v1":
        raise ValueError("unsupported ambiguity case schema")
    policy = load_policy(policy_path)
    records = []
    for case in packet.get("cases") or []:
        decision, reason, calibrated = decide(
            raw_score=float(case["rawScore"]), origin=str(case["origin"]),
            evidence_valid=bool(case["evidenceValid"]),
            unresolved_fields=case.get("unresolvedFields") or [],
            impossible=bool(case.get("impossible")),
            out_of_scope=bool(case.get("outOfScope")), policy=policy,
        )
        records.append({
            "caseId": case["caseId"], "expectedDecision": case["expectedDecision"],
            "expectedReason": case["expectedReason"], "systemDecision": decision,
            "systemReason": reason, "calibratedConfidence": calibrated,
            "passed": decision == {
                "accept": "accepted", "question": "needsQuestion", "abstain": "abstained",
            }[case["expectedDecision"]] and reason == case["expectedReason"],
        })
    return {
        "schemaVersion": "easydep-ambiguity-evaluation/v1",
        "caseSchemaVersion": packet["schemaVersion"],
        "policyVersion": policy.get("version"),
        "policy": {
            "autoAcceptEnabled": policy.get("autoAcceptEnabled"),
            "acceptThreshold": policy.get("acceptThreshold"),
            "targetPrecision": policy.get("targetPrecision"),
            "minimumWilsonLower95": policy.get("minimumWilsonLower95"),
        },
        "allPassed": all(record["passed"] for record in records),
        "records": records,
        "metrics": score_selective_decisions(records),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate(args.cases, args.policy)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
