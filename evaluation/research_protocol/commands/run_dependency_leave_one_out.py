"""검증된 provider fixture의 각 근거 edge를 하나씩 제거해 누락 검출 민감도를 측정한다."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from evaluation.component_projection import derive_component_dependency_expectations
from evaluation.terraform_semantics import analyze_terraform_semantics, score_semantics
from evaluation.research_protocol.core.paths import REPOSITORY_ROOT

ROOT = REPOSITORY_ROOT
DEFAULT_FIXTURES = ROOT / "evaluation/research_protocol/provider-fixtures"
DELTAS = ("persistent-storage", "load-balanced-multi-vm", "https-termination")


def _reference_checks(score: dict[str, Any]) -> dict[tuple[str, str, str], str]:
    return {
        (str(item["delta"]), str(item["from"]), str(item["to"])): str(item["status"])
        for item in score["checks"]
        if item.get("kind") == "componentDependencyReference"
    }


def run(fixture_root: Path = DEFAULT_FIXTURES) -> dict[str, Any]:
    rows = []
    baseline_passed = 0
    for provider in ("aws", "azure", "gcp"):
        actual = analyze_terraform_semantics(fixture_root / provider)
        for delta in DELTAS:
            expected = derive_component_dependency_expectations(provider, [delta])
            oracle = {
                "provider": provider,
                "requiredCapabilities": {},
                "requiredDependencies": [],
                "componentDeltas": [delta],
                "componentDependencyExpectations": expected,
            }
            baseline = _reference_checks(score_semantics(actual, oracle))
            baseline_passed += sum(status == "passed" for status in baseline.values())
            for reference in expected["structuralReferences"]:
                key = (delta, reference["from"], reference["to"])
                if baseline.get(key) != "passed":
                    raise ValueError(f"baseline edge가 통과하지 않았다: {provider} {key}")
                intervened = deepcopy(actual)
                relations = intervened["componentProjections"]["deltas"][delta][
                    "relations"
                ]
                matches = [
                    item
                    for item in relations
                    if item.get("from") == reference["from"]
                    and item.get("to") == reference["to"]
                ]
                if len(matches) != 1:
                    raise ValueError(f"edge 관측기는 정확히 하나여야 한다: {provider} {key}")
                matches[0]["observedPairs"] = []
                after = _reference_checks(score_semantics(intervened, oracle))
                changed = [item for item in baseline if baseline[item] != after.get(item)]
                rows.append({
                    "provider": provider,
                    "delta": delta,
                    "from": reference["from"],
                    "to": reference["to"],
                    "baselineStatus": baseline[key],
                    "interventionStatus": after.get(key),
                    "onlyTargetChanged": changed == [key],
                    "detected": after.get(key) == "failed" and changed == [key],
                    "evidence": reference.get("evidence") or [],
                })
    return {
        "schemaVersion": "easydep-dependency-leave-one-out/v1",
        "fixtureRoot": fixture_root.as_posix(),
        "measurementBoundary": {
            "source": "provider-validated-static-fixtures",
            "mutation": "one-observed-reference-pair-removed-in-semantic-view",
            "providerValidateRerun": False,
            "cloudApply": False,
            "functionTest": False,
        },
        "baselineExpectedReferenceCount": baseline_passed,
        "interventionCount": len(rows),
        "detectedCount": sum(item["detected"] for item in rows),
        "nonTargetChangeCount": sum(not item["onlyTargetChanged"] for item in rows),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.fixture_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
