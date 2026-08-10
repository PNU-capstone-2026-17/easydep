"""확인적 실험의 동결 전제조건을 읽기 전용으로 점검한다."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app.core.cloudkb.depkb.evidence_model import validate_frozen_model
from app.core.cloudkb.depkb.neutral_evidence import validate_neutral_evidence
from app.core.cloudkb.depkb.projection_model import projection_gaps, validate_projection
from app.requirements.capability_contract import DEFAULT_POLICY, load_policy
from evaluation.dependency_audit.intervention_results import adjudicate_result
from evaluation.research_protocol.commands.build_intervention_manifest import build as build_interventions
from evaluation.research_protocol.commands.build_runtime_dependencies import build as build_runtime
from evaluation.research_protocol.core.paths import DEFINITION_ROOT, PROTOCOL_ROOT, REPOSITORY_ROOT

HERE = PROTOCOL_ROOT
PROTOCOL = DEFINITION_ROOT / "protocol.json"
ANCHORS = DEFINITION_ROOT / "decision-anchors.json"
NATIVE_DIR = HERE / "native-v2"
PROJECTIONS = REPOSITORY_ROOT / "app/core/cloudkb/depkb/provider-projections.json"
NEUTRAL_EVIDENCE = DEFINITION_ROOT / "neutral-model-evidence.json"
INTERVENTIONS = DEFINITION_ROOT / "dependency-interventions.json"
INTERVENTION_RESULTS = HERE / "intervention-results"
RUNTIME_DEPENDENCIES = (
    REPOSITORY_ROOT / "app/core/cloudkb/depkb/official-dependencies.json"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def readiness() -> dict[str, Any]:
    blockers: list[dict[str, str]] = []
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    if protocol.get("status") != "frozen":
        blockers.append({"kind": "protocol", "detail": "protocol is not frozen"})
    anchors = json.loads(ANCHORS.read_text(encoding="utf-8"))
    if anchors.get("status") != "frozen":
        blockers.append({"kind": "decisionAnchors", "detail": "decision anchors are not frozen"})
    policy = load_policy(DEFAULT_POLICY)
    if policy.get("status") != "frozen":
        blockers.append({
            "kind": "capabilityCalibration",
            "detail": "capability decision policy is not frozen",
        })
    for provider in ("aws", "azure", "gcp"):
        path = NATIVE_DIR / f"{provider}-evidence-model.json"
        if not path.is_file():
            blockers.append({
                "kind": "nativeModelMissing",
                "detail": f"{provider}: {path}",
            })
            continue
        try:
            validate_frozen_model(json.loads(path.read_text(encoding="utf-8")))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            blockers.append({"kind": "nativeModelInvalid", "detail": f"{provider}: {exc}"})
    try:
        stored_runtime = json.loads(RUNTIME_DEPENDENCIES.read_text(encoding="utf-8"))
        if stored_runtime != build_runtime(NATIVE_DIR):
            blockers.append({
                "kind": "runtimeDependencyModelStale",
                "detail": str(RUNTIME_DEPENDENCIES),
            })
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        blockers.append({"kind": "runtimeDependencyModelInvalid", "detail": str(exc)})
    try:
        projections = json.loads(PROJECTIONS.read_text(encoding="utf-8"))
        validate_projection(projections)
        gaps = projection_gaps(projections)
        if gaps:
            blockers.append({"kind": "projectionGap", "detail": json.dumps(gaps)})
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        blockers.append({"kind": "projectionInvalid", "detail": str(exc)})
    try:
        validate_neutral_evidence(json.loads(NEUTRAL_EVIDENCE.read_text(encoding="utf-8")))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        blockers.append({"kind": "neutralEvidenceInvalid", "detail": str(exc)})
    try:
        stored = json.loads(INTERVENTIONS.read_text(encoding="utf-8"))
        expected = build_interventions()
        if stored != expected:
            blockers.append({"kind": "interventionManifestStale", "detail": str(INTERVENTIONS)})
        pending = []
        for case in stored.get("cases", []):
            result_path = INTERVENTION_RESULTS / f"{case['experimentId']}.json"
            if not result_path.is_file():
                pending.append(case["experimentId"])
                continue
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if adjudicate_result(result, case["experimentId"]) != "confirmed":
                pending.append(case["experimentId"])
        if pending:
            blockers.append({"kind": "dependencyInterventions", "detail": ", ".join(pending)})
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        blockers.append({"kind": "interventionManifestInvalid", "detail": str(exc)})
    return {
        "schemaVersion": "easydep-research-readiness/v1",
        "ready": not blockers,
        "blockers": blockers,
        "protocolSha256": _sha256(PROTOCOL),
        "decisionAnchorsSha256": _sha256(ANCHORS),
        "capabilityPolicyVersion": policy.get("version"),
        "providerProjectionSha256": _sha256(PROJECTIONS) if PROJECTIONS.is_file() else None,
        "runtimeDependencyModelSha256": (
            _sha256(RUNTIME_DEPENDENCIES) if RUNTIME_DEPENDENCIES.is_file() else None
        ),
        "neutralEvidenceSha256": _sha256(NEUTRAL_EVIDENCE) if NEUTRAL_EVIDENCE.is_file() else None,
        "interventionManifestSha256": _sha256(INTERVENTIONS) if INTERVENTIONS.is_file() else None,
    }


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    result = readiness()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
