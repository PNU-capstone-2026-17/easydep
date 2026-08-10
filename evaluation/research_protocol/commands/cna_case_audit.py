"""CNA 합성 사례가 기존 근거와 단일 변수 계약을 지키는지 감사한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from evaluation.component_projection import derive_component_dependency_expectations
from evaluation.research_protocol.core.paths import REPOSITORY_ROOT

ROOT = REPOSITORY_ROOT
PROJECTIONS = ROOT / "evaluation/research_protocol/definitions/component-projections.json"
CASE_ROOT = ROOT / "evaluation/baselines/component-cases"
SUITE = CASE_ROOT / "suite.json"
ORACLE = CASE_ROOT / "oracle.json"
OFFICIAL_DOMAINS = {
    "aws": "docs.aws.amazon.com",
    "azure": "learn.microsoft.com",
    "gcp": "cloud.google.com",
}
PREFIXES = {
    "persistent-storage": "PS",
    "load-balanced-multi-vm": "LB",
    "https-termination": "TLS",
}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def audit() -> dict[str, Any]:
    projections = _read(PROJECTIONS)
    suite = _read(SUITE)
    oracle = _read(ORACLE)
    development = set(suite.get("development") or [])
    axes: list[dict[str, Any]] = []

    for delta in projections.get("deltas") or []:
        axis_id = str(delta["id"])
        prefix = PREFIXES[axis_id]
        provider_checks: list[dict[str, Any]] = []
        for provider in ("aws", "azure", "gcp"):
            realization = (delta.get("realizations") or {}).get(provider) or {}
            evidence = realization.get("evidence") or []
            official_count = sum(
                urlparse(str(source)).netloc == OFFICIAL_DOMAINS[provider] for source in evidence
            )
            control_name = f"{prefix.lower()}-control-{provider}.json"
            treatment_name = f"{prefix.lower()}-treatment-{provider}.json"
            control = _read(CASE_ROOT / control_name)
            treatment = _read(CASE_ROOT / treatment_name)
            changed = [
                index
                for index, (left, right) in enumerate(
                    zip(control.get("requirements") or [], treatment.get("requirements") or [])
                )
                if left != right
            ]
            control_oracle = oracle["profiles"][f"{prefix}-control"]
            treatment_oracle = oracle["profiles"][f"{prefix}-treatment"]
            derived = derive_component_dependency_expectations(provider, [axis_id])
            provider_checks.append(
                {
                    "provider": provider,
                    "officialEvidencePresent": official_count > 0,
                    "projectionComponentsPresent": bool(realization.get("components")),
                    "projectionRelationsPresent": bool(realization.get("relations")),
                    "pairedCasesInDevelopmentSuite": (
                        control_name in development and treatment_name in development
                    ),
                    "singleRequirementEntryChanged": (
                        len(control.get("requirements") or [])
                        == len(treatment.get("requirements") or [])
                        and len(changed) == 1
                    ),
                    "applicationFunctionFixed": (
                        control_oracle.get("functionalAcceptance")
                        == treatment_oracle.get("functionalAcceptance")
                    ),
                    "treatmentUsesProjectionDelta": (
                        axis_id in (treatment_oracle.get("componentDeltas") or [])
                    ),
                    "dependencyExpectationsDerived": bool(derived["structuralReferences"]),
                    "dependencyEvidenceLinked": all(
                        item.get("evidence") for item in derived["structuralReferences"]
                    ),
                    "constraintDeclarationsPreserved": all(
                        item.get("constraint") for item in derived["constraints"]
                    ),
                }
            )
        axes.append(
            {
                "axisId": axis_id,
                "providers": provider_checks,
                "evidenceAndPairContractComplete": all(
                    all(value for key, value in check.items() if key != "provider")
                    for check in provider_checks
                ),
            }
        )

    provenance = suite.get("synthesisProvenance")
    provenance_complete = bool(
        isinstance(provenance, dict)
        and provenance.get("model")
        and provenance.get("promptSha256")
        and provenance.get("seed") is not None
        and provenance.get("evidenceCardSha256")
    )
    return {
        "schemaVersion": "easydep-cna-case-audit/v2",
        "scope": "docker-on-vm development component cases",
        "axes": axes,
        "projectionAndPairEvidenceComplete": all(
            item["evidenceAndPairContractComplete"] for item in axes
        ),
        "synthesisProvenanceComplete": provenance_complete,
        "eligibleForDevelopmentPilot": all(
            item["evidenceAndPairContractComplete"] for item in axes
        ),
        "eligibleForDependencyStructureMeasurement": all(
            all(
                check["dependencyExpectationsDerived"] and check["dependencyEvidenceLinked"]
                for check in item["providers"]
            )
            for item in axes
        ),
        "eligibleForCardinalityOrConstraintClaim": False,
        "eligibleAsReproducibleSyntheticCorpus": (
            provenance_complete and all(item["evidenceAndPairContractComplete"] for item in axes)
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit()
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
