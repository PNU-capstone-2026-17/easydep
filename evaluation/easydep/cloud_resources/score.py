"""Method-neutral scorer for a normalized cloud-plan.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _metric(actual: set, expected: set) -> dict[str, float | int]:
    true_positive = len(actual & expected)
    precision = true_positive / len(actual) if actual else (1.0 if not expected else 0.0)
    recall = true_positive / len(expected) if expected else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "truePositive": true_positive,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def score(run_dir: Path) -> dict:
    manifest = _load(run_dir / "manifest.json")
    plan = _load(run_dir / "cloud-plan.json")
    expected = _load(ROOT / "oracle.json")[manifest["caseId"]]
    actual_nodes = {
        str(node["id"])
        for node in plan.get("nodes", [])
        if node.get("role") in {None, "anchor", "required"}
    }
    actual_edges = {
        (str(edge["from"]), str(edge["to"])) for edge in plan.get("edges", [])
    }
    expected_nodes = set(expected["requiredNodes"])
    expected_edges = {tuple(edge) for edge in expected["requiredEdges"]}
    order = [
        str(item.get("id") if isinstance(item, dict) else item)
        for item in plan.get("createOrder", [])
    ]
    positions = {item: index for index, item in enumerate(order)}
    order_violations = sum(
        1
        for source, target in expected_edges
        if source in positions and target in positions and positions[target] > positions[source]
    )
    result = {
        "runId": manifest["runId"],
        "system": manifest["system"],
        "variant": manifest["variant"],
        "caseId": manifest["caseId"],
        "nodes": _metric(actual_nodes, expected_nodes),
        "edges": _metric(actual_edges, expected_edges),
        "creationOrderViolations": order_violations,
        "missingNodes": sorted(expected_nodes - actual_nodes),
        "extraNodes": sorted(actual_nodes - expected_nodes),
        "missingEdges": sorted([list(edge) for edge in expected_edges - actual_edges]),
        "extraEdges": sorted([list(edge) for edge in actual_edges - expected_edges]),
    }
    (run_dir / "evaluation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dirs", nargs="+", type=Path)
    args = parser.parse_args()
    for run_dir in args.run_dirs:
        print(json.dumps(score(run_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
