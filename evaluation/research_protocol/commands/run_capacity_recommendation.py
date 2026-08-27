"""저장된 실측 한 점을 용량 하한과 CSP별 VM 후보로 변환한다."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.implementation.planning.capacity_estimation import recommend_measured_capacity


def run(measurement_path: Path, cases_path: Path) -> dict[str, Any]:
    measurement = json.loads(measurement_path.read_text(encoding="utf-8"))
    case_document = json.loads(cases_path.read_text(encoding="utf-8"))
    results = []
    for case in case_document["cases"]:
        result = recommend_measured_capacity(
            measurement,
            case["target"],
            case["resourceSpec"],
            case.get("deploymentNeeds"),
        )
        results.append({"caseId": case["caseId"], **result})
    return {
        "schemaVersion": "easydep-capacity-recommendation-evaluation/v1",
        "measurementPath": measurement_path.as_posix(),
        "measurementKind": measurement.get("measurementKind"),
        "casesPath": cases_path.as_posix(),
        "cases": results,
        "interpretation": {
            "capacity": "single-development-load-point-floor",
            "price": "on-demand-compute-list-price-only",
            "notClaimed": [
                "maximum-sustainable-throughput",
                "cloud-instance-performance-equivalence",
                "total-cloud-cost-optimum",
            ],
        },
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measurement", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.measurement, args.cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
