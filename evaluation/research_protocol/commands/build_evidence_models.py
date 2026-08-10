"""중립 경계 가설을 세 CSP 공식 수명주기 모델로 검증해 동결한다."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from app.core.cloudkb.depkb.evidence_capture import capture_digest
from app.core.cloudkb.depkb.evidence_model import freeze_model
from evaluation.dependency_audit.intervention_results import adjudicate_result
from evaluation.research_protocol.core.paths import DEFINITION_ROOT, PROTOCOL_ROOT, REPOSITORY_ROOT

HERE = PROTOCOL_ROOT
CONFIG = DEFINITION_ROOT / "resource-boundaries.json"
DEPENDENCIES = DEFINITION_ROOT / "dependency-evidence.json"
NATIVE = HERE / "native-v2"
ROOT = REPOSITORY_ROOT
INTERVENTION_RESULTS = HERE / "intervention-results"


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _canonical_digest(path: Path) -> str:
    value = _read(path)
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build(provider: str, config_path: Path = CONFIG, native_dir: Path = NATIVE,
          dependency_path: Path = DEPENDENCIES,
          intervention_results: Path = INTERVENTION_RESULTS) -> dict[str, Any]:
    config = _read(config_path)
    inventory = _read(native_dir / f"{provider}-observations.json")
    operations: dict[tuple[str, str], dict[str, Any]] = {}
    for item in inventory["observations"]:
        names = list(item.get("crudOperations") or [])
        if provider == "azure" and ".operation." in item["nativeId"]:
            names.append(item["nativeId"].rsplit(".operation.", 1)[-1])
        for operation in names:
            operations[(item["serviceFamily"], operation)] = item
    neutral_path = ROOT / config["neutralSource"]
    neutral = _read(neutral_path)
    neutral_ids = {item["id"] for item in neutral.get("concepts") or []}
    neutral_sha = _canonical_digest(neutral_path)
    dependencies = _read(dependency_path)
    claims: list[dict[str, Any]] = []
    for hypothesis in config["providers"][provider]:
        if hypothesis["conceptId"] not in neutral_ids:
            raise ValueError(f"unknown neutral concept: {hypothesis['conceptId']}")
        locators: dict[str, str] = {}
        for lifecycle, operation in hypothesis["operations"].items():
            observed = operations.get((hypothesis["serviceFamily"], operation))
            if observed is None:
                raise ValueError(
                    f"official lifecycle operation absent: {provider}/{hypothesis['id']}/{operation}"
                )
            locators[lifecycle] = observed["sourceLocator"]
        boundary_id = f"{provider}.{hypothesis['id']}.boundary"
        lifecycle_evidence = {
            "sourceRole": "vendorLifecycleSchema",
            "sourceLocator": " | ".join(locators.values()),
            "operationLocators": locators,
            "sourceSha256": inventory["source"]["sha256"],
            "supports": True,
            "independentIdentity": True,
            "lifecycleOperations": sorted(hypothesis["operations"]),
        }
        claims.append({
            "claimId": boundary_id,
            "claimType": "resourceBoundary",
            "resourceId": hypothesis["id"],
            "capabilityIds": hypothesis["capabilityIds"],
            "observations": [lifecycle_evidence],
        })
        claims.append({
            "claimId": f"{provider}.{hypothesis['id']}.crosswalk",
            "claimType": "neutralCrosswalk",
            "resourceId": hypothesis["id"],
            "conceptId": hypothesis["conceptId"],
            "mappingRelation": hypothesis.get("mappingRelation", "partial"),
            "observations": [
                lifecycle_evidence,
                {
                    "sourceRole": "neutralModel",
                    "sourceLocator": f"{config['neutralSource']}#{hypothesis['conceptId']}",
                    "sourceSha256": neutral_sha,
                    "supports": True,
                },
            ],
        })
    for dependency in dependencies["providers"][provider]:
        manual = dependency["manual"]
        observation = {
            "sourceRole": "vendorManual",
            "sourceLocator": manual["sourceLocator"],
            "sourceSha256": capture_digest(manual),
            "sourceVersion": manual["sourceVersion"],
            "retrievedOn": manual["retrievedOn"],
            "documentSection": manual["documentSection"],
            "finding": manual["finding"],
            "supports": True,
            "normativeRequirement": dependency["normativeRequirement"],
        }
        common = {
            "fromResourceId": dependency["from"],
            "toResourceId": dependency["to"],
            "semantics": dependency["semantics"],
            "condition": dependency["condition"],
            "modelGap": dependency.get("modelGap"),
        }
        claims.append({
            "claimId": f"{provider}.{dependency['id']}.existence",
            "claimType": "dependencyExistence",
            **common,
            "observations": [observation],
        })
        if dependency.get("assessNecessity", True):
            necessity_id = f"{provider}.{dependency['id']}.necessity"
            observations = [observation]
            result_path = intervention_results / f"intervention.{necessity_id}.json"
            if result_path.is_file():
                result = _read(result_path)
                if adjudicate_result(result, f"intervention.{necessity_id}") == "confirmed":
                    observations.append({
                        "sourceRole": "runtimeIntervention",
                        "sourceLocator": result_path.relative_to(ROOT).as_posix(),
                        "sourceSha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
                        "supports": True,
                        "controlPassed": True,
                        "removalFailed": True,
                        "restorationPassed": True,
                        "replications": 3,
                    })
            claims.append({
                "claimId": necessity_id,
                "claimType": "dependencyNecessity",
                "eligibleForIntervention": dependency.get("eligibleForIntervention", False),
                **common,
                "observations": observations,
            })
    return freeze_model(provider, claims)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=NATIVE)
    args = parser.parse_args()
    for provider in ("aws", "azure", "gcp"):
        model = build(provider)
        path = args.output / f"{provider}-evidence-model.json"
        path.write_text(
            json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"{provider}: {len(model['claims'])} claims -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
