"""세 capability 축의 LLM 추출과 CSP projection을 분리 측정한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from app.core.orchestration.adapters.cloud_design import CloudDesignAdapter
from app.requirements.agent.steps.step_cloud import derive_deployment_needs
from app.requirements.capability_contract import link_dependency_capability
from evaluation.baselines.common import model, seed, temperature
from evaluation.research_protocol.core.paths import REPOSITORY_ROOT

ROOT = REPOSITORY_ROOT
CASES = ROOT / "evaluation/baselines/component-cases"
PROVIDERS = {
    "aws": "ap-northeast-2",
    "azure": "koreacentral",
    "gcp": "asia-northeast3",
}
CELLS = (
    (
        "persistent-storage",
        "treatment",
        "ps-treatment-aws.json",
        {
            "capabilityIds": ["persistent-block-storage"],
            "anchors": ["vm", "disk"],
            "outcome": "disk",
        },
    ),
    (
        "load-balanced-ingress",
        "treatment",
        "lb-treatment-aws.json",
        {
            "capabilityIds": ["load-balanced-ingress"],
            "anchors": ["vm", "loadBalancer"],
            "outcome": "load-balanced-ingress",
        },
    ),
    (
        "https-load-balanced-ingress",
        "treatment",
        "tls-treatment-aws.json",
        {
            "capabilityIds": ["https-load-balanced-ingress"],
            "anchors": ["vm", "loadBalancer"],
            "outcome": "https-load-balanced-ingress",
        },
    ),
)
CONFIRMATORY_CAPABILITY_SAMPLES = 5


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _revision() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _classified(requirements: list[str]) -> list[dict[str, str]]:
    return [
        {"id": f"R{index}", "text": text, "type": "NFR"}
        for index, text in enumerate(requirements, start=1)
    ]


def _positive_capability_requirements(source: dict[str, Any]) -> list[str]:
    """Select existing positive capability clauses without synthesizing new text."""
    capability_text = str(source["requirements"][1])
    clauses = [item.strip() for item in re.split(r"(?<=\.)\s+", capability_text) if item.strip()]
    return [item for item in clauses if not item.casefold().startswith("do not provision")]


def _accepted_ids(needs: dict[str, Any]) -> list[str]:
    supported = {
        "persistent-block-storage",
        "load-balanced-ingress",
        "https-load-balanced-ingress",
    }
    found = set()
    for key, need in needs.items():
        if not isinstance(need, dict) or need.get("decision", "accepted") != "accepted":
            continue
        found.update(set(need.get("dependencyCapabilityIds") or []) & supported)
        linked = link_dependency_capability(key, str(need.get("role") or ""))
        if linked:
            found.add(linked)
    return sorted(found)


def _project(
    requirements_result: dict[str, Any],
    provider: str,
    region: str,
    *,
    use_cloud_kb: bool = True,
) -> dict[str, Any]:
    value = {
        **requirements_result,
        "resource_spec": {"provider": provider, "region": region},
    }
    result = CloudDesignAdapter().finalize(
        requirements_result=value,
        design_result={},
        use_cloud_kb=use_cloud_kb,
    )
    coverage = result.get("dependency_coverage") or {}
    modeled = coverage.get("modeledInputs") or []
    intent = result.get("infra_intent") or {}
    return {
        "provider": provider,
        "anchors": result.get("anchors") or [],
        "modeledOutcomes": [
            item["outcome"] for item in modeled if item.get("source") == "deployment_needs"
        ],
        "unmodeledAcceptedNeeds": coverage.get("unmodeledAcceptedNeeds") or [],
        "realizationIds": [item["id"] for item in intent.get("capabilityRealizations") or []],
        "kbUsed": result.get("kb_used") or [],
        "deferred": result.get("deferred") or [],
    }


def measure_llm(
    *, capability_samples: int = CONFIRMATORY_CAPABILITY_SAMPLES
) -> dict[str, Any]:
    capability_samples = max(1, int(capability_samples))
    cells: list[dict[str, Any]] = []
    started = perf_counter()
    for axis, condition, filename, expected in CELLS:
        source = _read(CASES / filename)
        selected_requirements = _positive_capability_requirements(source)
        before = perf_counter()
        result = derive_deployment_needs(
            {
                "classified": _classified(selected_requirements),
            },
            sample_count=capability_samples,
        )
        elapsed = perf_counter() - before
        needs = result.get("deployment_needs") or {}
        observed_ids = _accepted_ids(needs)
        projections = [_project(result, provider, region) for provider, region in PROVIDERS.items()]
        expected_outcome = expected["outcome"]
        projection_passes = [
            projection["anchors"] == expected["anchors"]
            and expected_outcome in projection["modeledOutcomes"]
            for projection in projections
        ]
        cells.append(
            {
                "axis": axis,
                "condition": condition,
                "sourceCase": filename,
                "sourceSha256": _digest(source),
                "selectedRequirements": selected_requirements,
                "selectedRequirementsSha256": _digest(selected_requirements),
                "elapsedSeconds": elapsed,
                "expected": expected,
                "acceptedCapabilityIds": observed_ids,
                "capabilityExtractionPassed": observed_ids == expected["capabilityIds"],
                "deploymentNeeds": needs,
                "capabilityContract": result.get("capability_contract") or {},
                "projections": projections,
                "allProviderProjectionsPassed": all(projection_passes),
            }
        )
    baseline_projections = [
        _project({"deployment_needs": {}}, provider, region)
        for provider, region in PROVIDERS.items()
    ]
    baseline_passed = all(
        item["anchors"] == ["vm"]
        and not item["realizationIds"]
        and not item["unmodeledAcceptedNeeds"]
        for item in baseline_projections
    )
    return {
        "schemaVersion": "easydep-capability-projection-measurement/v1",
        "kind": "live-llm-development-measurement",
        "createdAt": datetime.now(UTC).isoformat(),
        "gitRevision": _revision(),
        "configuration": {
            "model": model(),
            "temperature": temperature(),
            "seed": seed(),
            "llmSamplesPerCell": capability_samples,
            "cloudApply": False,
        },
        "cells": cells,
        "deterministicNoCapabilityBaseline": {
            "projections": baseline_projections,
            "passed": baseline_passed,
        },
        "summary": {
            "cellCount": len(cells),
            "llmCallCount": len(cells) * capability_samples,
            "capabilityExtractionPassCount": sum(
                item["capabilityExtractionPassed"] for item in cells
            ),
            "providerProjectionCellCount": len(cells) * len(PROVIDERS),
            "providerProjectionPassCount": sum(
                len(PROVIDERS) if item["allProviderProjectionsPassed"] else 0 for item in cells
            ),
            "deterministicNoCapabilityBaselinePassed": baseline_passed,
            "elapsedSeconds": perf_counter() - started,
        },
    }


def ablate(previous: dict[str, Any]) -> dict[str, Any]:
    """동일한 저장 LLM 출력을 고정하고 DepKB projection만 켜고 끈다."""
    cells = []
    for old in previous.get("cells") or []:
        needs = old.get("deploymentNeeds") or {}
        fixed_input = {"deployment_needs": needs}
        provider_cells = []
        for provider, region in PROVIDERS.items():
            full = _project(fixed_input, provider, region, use_cloud_kb=True)
            no_depkb = _project(fixed_input, provider, region, use_cloud_kb=False)
            provider_cells.append(
                {
                    "provider": provider,
                    "fullInputSha256": _digest(fixed_input),
                    "noDepkbInputSha256": _digest(fixed_input),
                    "easydepFull": full,
                    "easydepNoDepkb": no_depkb,
                    "modeledOutcomeDelta": (
                        len(full["modeledOutcomes"]) - len(no_depkb["modeledOutcomes"])
                    ),
                    "realizationDelta": (
                        len(full["realizationIds"]) - len(no_depkb["realizationIds"])
                    ),
                }
            )
        cells.append(
            {
                "axis": old.get("axis"),
                "condition": old.get("condition"),
                "sourceMeasurementCellSha256": _digest(old),
                "fixedDeploymentNeedsSha256": _digest(needs),
                "providers": provider_cells,
            }
        )
    provider_cells = [provider_cell for cell in cells for provider_cell in cell["providers"]]
    return {
        "schemaVersion": "easydep-capability-projection-ablation/v1",
        "kind": "offline-fixed-llm-output-depkb-ablation",
        "createdAt": datetime.now(UTC).isoformat(),
        "gitRevision": _revision(),
        "sourceMeasurementSha256": _digest(previous),
        "configuration": {
            **(previous.get("configuration") or {}),
            "llmCalls": 0,
            "cloudApply": False,
            "fixedInputAcrossArms": True,
        },
        "cells": cells,
        "summary": {
            "axisCount": len(cells),
            "providerCellCount": len(provider_cells),
            "sameInputCellCount": sum(
                item["fullInputSha256"] == item["noDepkbInputSha256"] for item in provider_cells
            ),
            "fullModeledOutcomeCount": sum(
                len(item["easydepFull"]["modeledOutcomes"]) for item in provider_cells
            ),
            "noDepkbModeledOutcomeCount": sum(
                len(item["easydepNoDepkb"]["modeledOutcomes"]) for item in provider_cells
            ),
            "fullRealizationCount": sum(
                len(item["easydepFull"]["realizationIds"]) for item in provider_cells
            ),
            "noDepkbRealizationCount": sum(
                len(item["easydepNoDepkb"]["realizationIds"]) for item in provider_cells
            ),
        },
    }


def replay(previous: dict[str, Any]) -> dict[str, Any]:
    """현재 결정론적 계약으로 저장된 LLM 결과를 재투영한다."""
    expected_by_cell = {
        (axis, condition): expected for axis, condition, _filename, expected in CELLS
    }
    cells = []
    for old in previous.get("cells") or []:
        expected = expected_by_cell[(old["axis"], old["condition"])]
        needs = old.get("deploymentNeeds") or {}
        current_needs = {
            key: {
                **need,
                "dependencyCapabilityIds": sorted(
                    set(need.get("dependencyCapabilityIds") or [])
                    | (
                        {linked}
                        if (linked := link_dependency_capability(key, str(need.get("role") or "")))
                        else set()
                    )
                ),
            }
            for key, need in needs.items()
        }
        current_result = {"deployment_needs": current_needs}
        projections = [
            _project(current_result, provider, region) for provider, region in PROVIDERS.items()
        ]
        expected_outcome = expected["outcome"]
        cells.append(
            {
                **old,
                "acceptedCapabilityIds": _accepted_ids(current_needs),
                "capabilityExtractionPassed": (
                    _accepted_ids(current_needs) == expected["capabilityIds"]
                ),
                "projections": projections,
                "allProviderProjectionsPassed": all(
                    item["anchors"] == expected["anchors"]
                    and expected_outcome in item["modeledOutcomes"]
                    for item in projections
                ),
            }
        )
    return {
        "schemaVersion": "easydep-capability-projection-measurement/v1",
        "kind": "offline-replay-of-live-llm-measurement",
        "createdAt": datetime.now(UTC).isoformat(),
        "gitRevision": _revision(),
        "sourceMeasurementSha256": _digest(previous),
        "configuration": {**(previous.get("configuration") or {}), "llmCalls": 0},
        "cells": cells,
        "summary": {
            "cellCount": len(cells),
            "llmCallCount": 0,
            "capabilityExtractionPassCount": sum(
                item["capabilityExtractionPassed"] for item in cells
            ),
            "providerProjectionCellCount": len(cells) * len(PROVIDERS),
            "providerProjectionPassCount": sum(
                len(PROVIDERS) if item["allProviderProjectionsPassed"] else 0 for item in cells
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--ablate", type=Path)
    parser.add_argument(
        "--capability-samples",
        type=int,
        default=CONFIRMATORY_CAPABILITY_SAMPLES,
    )
    args = parser.parse_args()
    if args.replay and args.ablate:
        parser.error("--replay and --ablate are mutually exclusive")
    result = (
        ablate(_read(args.ablate))
        if args.ablate
        else replay(_read(args.replay))
        if args.replay
        else measure_llm(capability_samples=args.capability_samples)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
