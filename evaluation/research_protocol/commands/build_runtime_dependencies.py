"""동결된 공급자 근거 모델에서 런타임용 의존관계 뷰를 생성한다."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from evaluation.research_protocol.core.paths import PROTOCOL_ROOT, REPOSITORY_ROOT

HERE = PROTOCOL_ROOT
NATIVE = HERE / "native-v2"
OUTPUT = REPOSITORY_ROOT / "app/core/cloudkb/depkb/official-dependencies.json"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def build(native_dir: Path = NATIVE) -> dict[str, Any]:
    providers: dict[str, list[dict[str, Any]]] = {}
    source_hashes: dict[str, str] = {}
    for provider in ("aws", "azure", "gcp"):
        path = native_dir / f"{provider}-evidence-model.json"
        model = json.loads(path.read_text(encoding="utf-8"))
        source_hashes[path.name] = hashlib.sha256(_canonical(model)).hexdigest()
        necessity = {
            claim["claimId"].removesuffix(".necessity"): claim
            for claim in model["claims"]
            if claim["claimType"] == "dependencyNecessity"
        }
        rows = []
        for claim in model["claims"]:
            if claim["claimType"] != "dependencyExistence":
                continue
            key = claim["claimId"].removesuffix(".existence")
            necessity_claim = necessity.get(key)
            rows.append({
                "id": key,
                "from": claim["fromResourceId"],
                "to": claim["toResourceId"],
                "semantics": claim["semantics"],
                "existenceDecision": claim["decision"],
                "necessityDecision": (
                    necessity_claim["decision"] if necessity_claim else "notAssessed"
                ),
                "sourceClaimIds": [
                    claim["claimId"],
                    *([necessity_claim["claimId"]] if necessity_claim else []),
                ],
            })
        providers[provider] = rows
    unsigned = {
        "schemaVersion": "easydep-official-dependencies/v1",
        "sourceModelSha256": source_hashes,
        "providers": providers,
    }
    return {**unsigned, "freeze": {"sha256": hashlib.sha256(_canonical(unsigned)).hexdigest()}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
