"""자연어 앱 상태 의도 추출과 HA 계약 대조를 실제 LLM으로 측정한다."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

from app.requirements.resources.application_cloud import (
    DeploymentBindingContract,
    application_intent_contract_from_requirements,
    cloud_capability_contract_from_requirements,
    validate_binding_consistency,
)
from app.requirements.resources.capability_extraction import derive_deployment_needs

CASES = {
    "explicit-node-scope": [
        {
            "id": "R-state",
            "text": "The application shall persist mutable state on the VM filesystem.",
            "type": "NFR",
        },
        {
            "id": "R-ha",
            "text": "The service shall remain available if one availability zone fails.",
            "type": "NFR",
        },
    ],
    "unspecified-state-scope": [
        {
            "id": "R-state",
            "text": "The application shall persist mutable state across restarts.",
            "type": "NFR",
        },
        {
            "id": "R-ha",
            "text": "The service shall remain available if one availability zone fails.",
            "type": "NFR",
        },
    ],
}
CONFIRMATORY_CAPABILITY_SAMPLES = 5


def run(*, capability_samples: int = CONFIRMATORY_CAPABILITY_SAMPLES) -> dict:
    capability_samples = max(1, int(capability_samples))
    records = []
    for case_id, requirements in CASES.items():
        started = perf_counter()
        extracted = derive_deployment_needs(
            {"classified": requirements},
            sample_count=capability_samples,
        )
        requirements_result = {
            **extracted,
            "resource_spec": {"multiZone": True},
        }
        intent = application_intent_contract_from_requirements(requirements_result)
        cloud = cloud_capability_contract_from_requirements(requirements_result)
        diagnostics = validate_binding_consistency(
            intent, cloud, DeploymentBindingContract()
        )
        records.append({
            "caseId": case_id,
            "requirements": requirements,
            "deploymentNeeds": extracted.get("deployment_needs") or {},
            "capabilityContract": extracted.get("capability_contract") or {},
            "applicationIntentContract": intent.model_dump(mode="json", by_alias=True),
            "diagnostics": [item.model_dump(mode="json") for item in diagnostics],
            "wallSeconds": round(perf_counter() - started, 6),
        })
    explicit = records[0]
    unspecified = records[1]
    explicit_scopes = {
        fact["attributes"].get("accessScope")
        for fact in explicit["applicationIntentContract"]["facts"]
    }
    unspecified_scopes = {
        fact["attributes"].get("accessScope")
        for fact in unspecified["applicationIntentContract"]["facts"]
    }
    return {
        "schemaVersion": "easydep-application-intent-pilot/v1",
        "recordedAt": datetime.now(UTC).isoformat(),
        "scope": "Docker-on-Linux-VM",
        "configuration": {"capabilitySamples": capability_samples},
        "cases": records,
        "checks": {
            "explicitNodeScopeExtracted": "node-filesystem" in explicit_scopes,
            "unspecifiedScopeNotInvented": "node-filesystem" not in unspecified_scopes,
            "explicitConflictQuestioned": any(
                item["code"] == "BIND-STATE-HA-001"
                for item in explicit["diagnostics"]
            ),
        },
        "claimLimits": {
            "naturalLanguageExtractionMeasured": True,
            "caseCount": len(records),
            "applicationGenerationMeasured": False,
            "applicationFunctionMeasured": False,
            "cloudApplyMeasured": False,
            "generalizationMeasured": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--capability-samples",
        type=int,
        default=CONFIRMATORY_CAPABILITY_SAMPLES,
    )
    args = parser.parse_args()
    result = run(capability_samples=args.capability_samples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["checks"], ensure_ascii=False))


if __name__ == "__main__":
    main()
