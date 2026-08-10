"""공식 문서로 미확정인 필수성 주장만 기능 개입 실험으로 등록한다."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.core.cloudkb.depkb.evidence_model import validate_frozen_model
from app.core.cloudkb.depkb.projection_model import projection_gaps, validate_projection
from evaluation.research_protocol.core.paths import DEFINITION_ROOT, PROTOCOL_ROOT, REPOSITORY_ROOT

HERE = PROTOCOL_ROOT
NATIVE = HERE / "native-v2"
PLAN = DEFINITION_ROOT / "dependency-experiment-plan.json"
PROJECTIONS = REPOSITORY_ROOT / "app/core/cloudkb/depkb/provider-projections.json"
RESULTS = HERE / "intervention-results"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build() -> dict[str, Any]:
    plan = _read(PLAN)
    projections = _read(PROJECTIONS)
    validate_projection(projections)
    gaps = projection_gaps(projections)
    if gaps:
        raise ValueError(f"provider projection has unresolved boundaries: {gaps}")
    cases = []
    model_hashes = {}
    for provider in ("aws", "azure", "gcp"):
        model = _read(NATIVE / f"{provider}-evidence-model.json")
        validate_frozen_model(model)
        model_hashes[provider] = model["freeze"]["sha256"]
        for claim in model["claims"]:
            if (claim["claimType"] == "dependencyNecessity"
                    and claim["decision"] in {"candidate", "confirmed"}
                    and claim.get("eligibleForIntervention") is True):
                experiment_id = f"intervention.{claim['claimId']}"
                result_path = RESULTS / f"{experiment_id}.json"
                cases.append({
                    "experimentId": experiment_id,
                    "provider": provider,
                    "claimId": claim["claimId"],
                    "fromResourceId": claim["fromResourceId"],
                    "toResourceId": claim["toResourceId"],
                    "condition": claim["condition"],
                    "intervention": "대상 참조를 제거한 한 요인 개입",
                    "functionalOracle": plan["functionalOracle"],
                    "replications": plan["executionPolicy"]["replications"],
                    "status": (
                        "completed-confirmed"
                        if claim["decision"] == "confirmed" else "registered-not-run"
                    ),
                    "resultSha256": (
                        hashlib.sha256(result_path.read_bytes()).hexdigest()
                        if result_path.is_file() else None
                    ),
                })
    manifest = {
        "schemaVersion": "easydep-dependency-interventions/v1",
        "planSha256": _digest(plan),
        "projectionSha256": _digest(projections),
        "modelFreezeSha256": model_hashes,
        "cases": cases,
    }
    manifest["manifestSha256"] = _digest(manifest)
    return manifest


def main() -> int:
    output = DEFINITION_ROOT / "dependency-interventions.json"
    output.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
