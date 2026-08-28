"""동일 스냅샷 파일럿의 진단을 실제 소유 하위 작업에 한 번 전달한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from app.orchestration.adapters.testing import TestingAdapter
from app.orchestration.contracts import RunMode, StepContext, StepStatus
from app.orchestration.providers import LlmLogicProvider, LlmVmDeliveryProvider
from app.requirements.resources.application_cloud import (
    cloud_capability_contract_from_requirements,
    derive_deployment_bindings,
    infer_application_contract,
)
from evaluation.research_protocol.core.paths import REPOSITORY_ROOT
from evaluation.research_protocol.core.snapshot_context import (
    load_context,
    source_app_id,
)
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


def _file_hashes(root: Path) -> dict[str, str]:
    ignored = {"build", ".gradle", ".terraform"}
    result: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if any(part in ignored or part.startswith(".easydep-test-") for part in relative.parts):
            continue
        result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _changed_files(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    return {
        "added": sorted(after.keys() - before.keys()),
        "modified": sorted(path for path in before.keys() & after.keys() if before[path] != after[path]),
        "removed": sorted(before.keys() - after.keys()),
    }


def _diagnostic_feedback(preflight: dict[str, Any], expected: str) -> list[dict[str, Any]]:
    diagnostics = [item for item in preflight["diagnostics"] if item.get("code") == expected]
    if not diagnostics:
        raise ValueError(f"수정 전에 기대 진단이 관측되지 않았다: {expected}")
    return diagnostics


def run_case(
    case: dict[str, Any],
    *,
    logic_provider: LlmLogicProvider | None = None,
    delivery_provider: LlmVmDeliveryProvider | None = None,
    retain_root: Path | None = None,
) -> dict[str, Any]:
    source = (ROOT / case["sourceApplication"]).resolve()
    requirements, design, cloud_design = load_context(case, repository_root=ROOT)
    with tempfile.TemporaryDirectory(prefix="easydep-snapshot-repair-") as directory:
        temporary = Path(directory)
        run_root = temporary / "run"
        application = run_root / "application"
        copy_source(source, application)
        mutation_files = apply_mutations(application, case.get("mutations") or [])
        before_sha = tree_sha256(application)
        before_files = _file_hashes(application)
        boundary = case["boundary"]
        preflight_before = preflight(application, boundary)
        feedback = _diagnostic_feedback(preflight_before, case["expectedDiagnostic"])
        app_contract = infer_application_contract(application)
        cloud_contract = cloud_capability_contract_from_requirements(requirements)
        cloud_contract, binding_contract = derive_deployment_bindings(
            app_contract, cloud_contract
        )
        payload = {
            "run_root": str(run_root),
            "requirements_result": requirements,
            "design_result": design,
            "cloud_design_result": cloud_design,
            "application_runtime_contract": app_contract.model_dump(mode="json", by_alias=True),
            "cloud_capability_contract": cloud_contract.model_dump(mode="json", by_alias=True),
            "deployment_binding_contract": binding_contract.model_dump(mode="json", by_alias=True),
            "repair_feedback": feedback,
            "enable_repair_feedback": True,
            "enable_consistency_validator": True,
        }
        context = StepContext(
            run_id=f"snapshot-repair-{case['id']}",
            app_id=source_app_id(source),
            mode=RunMode.BATCH,
        )
        started = perf_counter()
        if case["repairMode"] == "logic":
            result = (logic_provider or LlmLogicProvider()).run(payload, context)
        elif case["repairMode"] == "vm-delivery":
            result = (delivery_provider or LlmVmDeliveryProvider()).run(payload, context)
        else:
            raise ValueError(f"지원하지 않는 수정 방식: {case['repairMode']}")
        repair_elapsed = round(perf_counter() - started, 6)
        after_files = _file_hashes(application)
        changed = _changed_files(before_files, after_files)
        after_sha = tree_sha256(application)
        postflight = preflight(application, boundary)
        testing = None
        if result.status == StepStatus.COMPLETED and not postflight["diagnostics"]:
            testing = TestingAdapter(timeout_seconds=int(case.get("timeoutSeconds", 600))).run(
                implementation_result={"run_root": str(run_root)},
                case_id=case["id"],
            )
        retained = None
        if retain_root is not None and bool((testing or {}).get("passed")):
            resolved_root = retain_root.resolve()
            if resolved_root != ROOT and ROOT not in resolved_root.parents:
                raise ValueError(f"보존 경로가 작업공간 밖이다: {resolved_root}")
            destination = resolved_root / f"{case['id']}-{after_sha[:12]}" / "application"
            if destination.parent.exists():
                raise FileExistsError(f"동일 후보 보존 경로가 이미 존재한다: {destination.parent}")
            destination.parent.mkdir(parents=True)
            shutil.copytree(
                application,
                destination,
                ignore=shutil.ignore_patterns("build", ".gradle", ".terraform"),
            )
            retained = {
                "path": destination.relative_to(ROOT).as_posix(),
                "sha256": tree_sha256(destination),
            }
        return portable_result(
            {
                "id": case["id"],
                "group": case["group"],
                "repairMode": case["repairMode"],
                "repairOwner": case["expectedRepairOwner"],
                "contextRun": case["contextRun"],
                "mutationFiles": mutation_files,
                "inputSha256": before_sha,
                "outputSha256": after_sha,
                "stepStatus": result.status.value,
                "stepDiagnostics": [item.model_dump(mode="json") for item in result.diagnostics],
                "stepMetrics": result.metrics,
                "repairElapsedSeconds": repair_elapsed,
                "changedFiles": changed,
                "upstreamStagesExecuted": [],
                "executedSubtasks": [case["expectedRepairOwner"]],
                "preflightBefore": preflight_before,
                "preflightAfter": postflight,
                "diagnosticResolved": not postflight["diagnostics"],
                "applicationTests": testing,
                "applicationTestsPassed": bool((testing or {}).get("passed")),
                "cloudApply": False,
                "retainedCandidate": retained,
            },
            temporary=temporary,
            repository_root=ROOT,
        )


def evaluate(
    cases: dict[str, Any],
    selected: set[str] | None = None,
    *,
    retain_root: Path | None = None,
) -> dict[str, Any]:
    started = perf_counter()
    rows: list[dict[str, Any]] = []
    for case in cases.get("cases") or []:
        if selected and case["id"] not in selected:
            continue
        try:
            rows.append(run_case(case, retain_root=retain_root))
        except Exception as error:  # keep independent cases and raw failure evidence
            rows.append({
                "id": case["id"],
                "group": case["group"],
                "repairMode": case.get("repairMode"),
                "status": "runner-error",
                "errorType": type(error).__name__,
                "error": str(error),
            })
    return {
        "schemaVersion": "easydep-app-cloud-snapshot-repair/v1",
        "createdAt": datetime.now(UTC).isoformat(),
        "kind": "single-owner-subtask-repair-pilot",
        "configuration": {"cloudApply": False, "maximumRepairAttemptsPerCase": 1},
        "cases": rows,
        "summary": {
            "caseCount": len(rows),
            "stepCompletedCount": sum(item.get("stepStatus") == "completed" for item in rows),
            "diagnosticResolvedCount": sum(item.get("diagnosticResolved") is True for item in rows),
            "applicationTestsPassedCount": sum(
                item.get("applicationTestsPassed") is True for item in rows
            ),
            "upstreamStageExecutionCount": sum(
                len(item.get("upstreamStagesExecuted") or []) for item in rows
            ),
            "elapsedSeconds": round(perf_counter() - started, 6),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--case", action="append", dest="selected")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--retain-root",
        type=Path,
        help="앱 테스트까지 통과한 정확한 후보를 작업공간 내부에 해시 이름으로 보존한다.",
    )
    args = parser.parse_args()
    result = evaluate(
        json.loads(args.cases.read_text(encoding="utf-8")),
        set(args.selected or []) or None,
        retain_root=args.retain_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
