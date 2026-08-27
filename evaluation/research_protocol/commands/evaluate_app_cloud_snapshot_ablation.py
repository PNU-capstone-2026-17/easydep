"""완료된 동일 앱 스냅샷을 두 arm에 복제해 validator의 실제 경계를 측정한다."""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from app.orchestration.adapters.testing import TestingAdapter
from app.orchestration.repair_routing import DIAGNOSTIC_REPAIR_OWNER
from evaluation.research_protocol.core.paths import REPOSITORY_ROOT
from evaluation.research_protocol.core.snapshot_support import (
    apply_mutations,
    copy_source,
    portable_result,
    preflight,
    tree_sha256,
)

ROOT = REPOSITORY_ROOT
DEFAULT_CASES = (
    ROOT / "evaluation/research_protocol/protocols/app-cloud-snapshot-cases.json"
)


def evaluate(cases: dict[str, Any], *, run_downstream: bool = True) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    started = perf_counter()
    for case in cases.get("cases") or []:
        source = (ROOT / case["sourceApplication"]).resolve()
        if not source.is_dir() or ROOT not in source.parents:
            raise ValueError(f"유효한 저장소 내부 앱 스냅샷이 아니다: {source}")
        source_application_preflight = preflight(source, "application")
        source_deployment_preflight = preflight(source, "deployment")
        source_eligible = not (
            source_application_preflight["diagnostics"]
            or source_deployment_preflight["diagnostics"]
        )
        if not source_eligible:
            raise ValueError(
                f"현재 계약 검증을 통과하지 못한 기준 스냅샷이다: {case['sourceApplication']}"
            )
        with tempfile.TemporaryDirectory(prefix="easydep-snapshot-ablation-") as directory:
            temporary = Path(directory)
            manipulated = temporary / "manipulated"
            copy_source(source, manipulated)
            mutation_files = apply_mutations(manipulated, case.get("mutations") or [])
            manipulated_sha = tree_sha256(manipulated)
            arms: dict[str, Any] = {}
            for arm, enabled in (("full", True), ("noConsistencyValidator", False)):
                application = temporary / arm / "application"
                copy_source(manipulated, application)
                input_sha = tree_sha256(application)
                preflight_result = preflight(application, case["boundary"]) if enabled else {
                    "diagnostics": [],
                    "observations": [],
                    "elapsedSeconds": 0.0,
                }
                downstream = None
                if run_downstream and not preflight_result["diagnostics"]:
                    downstream = TestingAdapter(timeout_seconds=int(case.get("timeoutSeconds", 600))).run(
                        implementation_result={"run_root": str(application.parent)},
                        case_id=case["id"],
                    )
                    downstream = portable_result(
                        downstream, temporary=temporary, repository_root=ROOT
                    )
                arms[arm] = {
                    "validatorEnabled": enabled,
                    "inputSha256": input_sha,
                    "preflight": preflight_result,
                    "blockedBeforeDownstream": bool(preflight_result["diagnostics"]),
                    "downstream": downstream,
                }
            expected = case["expectedDiagnostic"]
            observed = [item["code"] for item in arms["full"]["preflight"]["diagnostics"]]
            rows.append({
                "id": case["id"],
                "group": case["group"],
                "sourceApplication": case["sourceApplication"],
                "sourceSha256": tree_sha256(source),
                "sourceQualification": {
                    "eligible": source_eligible,
                    "applicationPreflight": source_application_preflight,
                    "deploymentPreflight": source_deployment_preflight,
                },
                "manipulatedSha256": manipulated_sha,
                "mutationFiles": mutation_files,
                "sameInputAcrossArms": len({item["inputSha256"] for item in arms.values()}) == 1,
                "expectedDiagnostic": expected,
                "expectedRepairOwner": case["expectedRepairOwner"],
                "observedRepairOwner": DIAGNOSTIC_REPAIR_OWNER.get(expected),
                "fullDecisionCorrect": expected in observed,
                "arms": arms,
            })
    return {
        "schemaVersion": "easydep-app-cloud-snapshot-ablation/v1",
        "createdAt": datetime.now(UTC).isoformat(),
        "kind": "same-generated-application-snapshot-validator-ablation",
        "configuration": {"llmCalls": 0, "cloudApply": False, "downstreamTests": run_downstream},
        "cases": rows,
        "summary": {
            "caseCount": len(rows),
            "sameInputAcrossArmsCount": sum(item["sameInputAcrossArms"] for item in rows),
            "fullEarlyDetectionCount": sum(item["fullDecisionCorrect"] for item in rows),
            "noValidatorEarlyDetectionCount": 0,
            "repairOwnerCorrectCount": sum(
                item["expectedRepairOwner"] == item["observedRepairOwner"] for item in rows
            ),
            "noValidatorDownstreamPassCount": sum(
                bool((item["arms"]["noConsistencyValidator"]["downstream"] or {}).get("passed"))
                for item in rows
            ),
            "noValidatorDownstreamFailureCount": sum(
                (item["arms"]["noConsistencyValidator"]["downstream"] or {}).get("passed")
                is False
                for item in rows
            ),
            "repairExecutionMeasured": False,
            "cloudFunctionMeasured": False,
            "elapsedSeconds": round(perf_counter() - started, 6),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-downstream", action="store_true")
    args = parser.parse_args()
    result = evaluate(
        json.loads(args.cases.read_text(encoding="utf-8")),
        run_downstream=not args.skip_downstream,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
