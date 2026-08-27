"""동일 앱·계약 입력에서 consistency validator만 켜고 끈다."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from app.requirements.resources.application_cloud import (
    ApplicationRuntimeContract,
    BindingEndpoint,
    CloudCapabilityContract,
    ContractBinding,
    ContractFact,
    DeploymentBindingContract,
    infer_application_contract,
    validate_application_consistency,
    validate_binding_consistency,
)
from app.orchestration.repair_routing import DIAGNOSTIC_REPAIR_OWNER
from evaluation.research_protocol.core.paths import REPOSITORY_ROOT

ROOT = REPOSITORY_ROOT
DEFAULT_CASES = (
    ROOT / "evaluation/research_protocol/protocols/app-cloud-ablation-cases.json"
)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _application_diagnostics(case: dict[str, Any]) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="easydep-app-contract-") as directory:
        application = Path(directory)
        for name, content in (case.get("files") or {}).items():
            target = application / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")
        contract = infer_application_contract(application)
        return [item.code for item in validate_application_consistency(application, contract)]


def _binding_diagnostics(case: dict[str, Any]) -> list[str]:
    application = ApplicationRuntimeContract(
        facts=[
            ContractFact(
                id="application-value",
                kind=case["applicationKind"],
                attributes={case["attribute"]: case["applicationValue"]},
            )
        ]
    )
    cloud = CloudCapabilityContract(
        facts=[
            ContractFact(
                id="cloud-value",
                kind=case["cloudKind"],
                attributes={case["attribute"]: case["cloudValue"]},
            )
        ]
    )
    binding = DeploymentBindingContract(
        bindings=[
            ContractBinding(
                id="fixed-input-binding",
                kind=case["bindingKind"],
                consumes=BindingEndpoint(
                    contract="application",
                    factId="application-value",
                    attribute=case["attribute"],
                ),
                provides=BindingEndpoint(
                    contract="cloud",
                    factId="cloud-value",
                    attribute=case["attribute"],
                ),
                invariants=[{"operator": "equals"}],
            )
        ]
    )
    return [item.code for item in validate_binding_consistency(application, cloud, binding)]


def evaluate(cases: dict[str, Any]) -> dict[str, Any]:
    evaluation_started = perf_counter()
    rows: list[dict[str, Any]] = []
    for case in cases.get("cases") or []:
        validation_started = perf_counter()
        observed = (
            _application_diagnostics(case)
            if case["inputKind"] == "applicationFiles"
            else _binding_diagnostics(case)
        )
        validation_elapsed = perf_counter() - validation_started
        expected = case.get("expectedDiagnostic")
        input_sha = _digest(
            {key: value for key, value in case.items() if not key.startswith("expected")}
        )
        owner = DIAGNOSTIC_REPAIR_OWNER.get(expected or "")
        rows.append(
            {
                "id": case["id"],
                "group": case["group"],
                "inputSha256": input_sha,
                "expectedDiagnostic": expected,
                "expectedRepairOwner": case.get("expectedRepairOwner"),
                "easydepFull": {
                    "validatorEnabled": True,
                    "diagnostics": observed,
                    "blockedBeforeDownstream": bool(observed),
                    "repairOwner": owner if observed else None,
                    "validationElapsedSeconds": validation_elapsed,
                },
                "easydepNoConsistencyValidator": {
                    "validatorEnabled": False,
                    "diagnostics": [],
                    "blockedBeforeDownstream": False,
                    "repairOwner": None,
                },
                "fullDecisionCorrect": (expected in observed if expected else not observed),
                "repairOwnerCorrect": (
                    owner == case.get("expectedRepairOwner") if expected else None
                ),
            }
        )
    mismatch = [row for row in rows if row["expectedDiagnostic"]]
    controls = [row for row in rows if not row["expectedDiagnostic"]]
    return {
        "schemaVersion": "easydep-app-cloud-ablation-result/v1",
        "kind": "offline-fixed-input-consistency-validator-ablation",
        "createdAt": datetime.now(UTC).isoformat(),
        "sourceCasesSha256": _digest(cases),
        "configuration": {"llmCalls": 0, "cloudApply": False},
        "cases": rows,
        "summary": {
            "mismatchCaseCount": len(mismatch),
            "controlCaseCount": len(controls),
            "fullTruePositiveCount": sum(row["fullDecisionCorrect"] for row in mismatch),
            "fullFalsePositiveCount": sum(not row["fullDecisionCorrect"] for row in controls),
            "noValidatorEarlyDetectionCount": 0,
            "repairOwnerCorrectCount": sum(row["repairOwnerCorrect"] is True for row in mismatch),
            "sameInputAcrossArmsCount": len(rows),
            "functionalSuccessMeasured": False,
            "repairExecutionMeasured": False,
            "evaluationElapsedSeconds": perf_counter() - evaluation_started,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    result = evaluate(cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
