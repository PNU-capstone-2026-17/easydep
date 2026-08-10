"""Deterministically sample unmapped native elements for provider-extension audit."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def _family(provider: str, native_id: str) -> str:
    if provider == "aws":
        parts = native_id.split("::")
        return "::".join(parts[:2]) if len(parts) > 1 else native_id
    if provider == "azure":
        marker = "/providers/"
        tail = native_id.split(marker, 1)[-1]
        return tail.split("/", 2)[0] if marker in native_id else "arm-root"
    return native_id.split(".", 2)[1] if native_id.startswith("compute.") else "compute"


def _rank(provider: str, native_id: str) -> str:
    return hashlib.sha256(f"{provider}\0{native_id}".encode()).hexdigest()


def make_extension_sample(
    inventory: dict[str, Any], projection: dict[str, Any], *, limit: int = 18
) -> dict[str, Any]:
    provider = inventory["provider"]
    mapped = {
        native_id
        for mapping in projection["mappings"]
        for native_id in mapping["nativeIds"]
    }
    unmapped = [item for item in inventory["elements"] if item["nativeId"] not in mapped]
    strata: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for element in unmapped:
        strata[(_family(provider, element["nativeId"]), element["nativeForm"])].append(
            element
        )
    ordered_strata = sorted(strata)
    selected: list[dict[str, Any]] = []
    offset = 0
    while len(selected) < min(limit, len(unmapped)):
        progressed = False
        for stratum in ordered_strata:
            ranked = sorted(
                strata[stratum], key=lambda item: _rank(provider, item["nativeId"])
            )
            if offset >= len(ranked):
                continue
            element = ranked[offset]
            selected.append(
                {
                    "nativeId": element["nativeId"],
                    "nativeForm": element["nativeForm"],
                    "family": stratum[0],
                    "sourceLocator": element["sourceLocator"],
                    "classification": "unreviewed",
                    "candidateConceptId": None,
                    "rationale": "",
                }
            )
            progressed = True
            if len(selected) == min(limit, len(unmapped)):
                break
        if not progressed:
            break
        offset += 1
    return {
        "schemaVersion": "easydep-provider-extension-audit/v1",
        "provider": provider,
        "inventorySource": inventory["source"],
        "population": len(unmapped),
        "sampleSize": len(selected),
        "selection": "round-robin strata by provider family and nativeForm; sha256 rank",
        "samples": selected,
    }


def validate_extension_audit(packet: dict[str, Any], *, require_complete: bool) -> None:
    if packet.get("schemaVersion") != "easydep-provider-extension-audit/v1":
        raise ValueError("unsupported provider extension audit schemaVersion")
    samples = packet.get("samples")
    if not isinstance(samples, list) or len(samples) != packet.get("sampleSize"):
        raise ValueError("provider extension audit sample count mismatch")
    ids = [item.get("nativeId") for item in samples]
    if len(ids) != len(set(ids)):
        raise ValueError("provider extension audit contains duplicate native ids")
    for item in samples:
        classification = item.get("classification")
        if classification not in {"unreviewed", "covered", "extension", "outsideScope"}:
            raise ValueError(f"invalid extension audit classification: {item.get('nativeId')}")
        if require_complete and classification == "unreviewed":
            raise ValueError(f"extension audit is not complete: {item.get('nativeId')}")
        if classification != "unreviewed" and not str(item.get("rationale") or "").strip():
            raise ValueError(f"extension audit lacks rationale: {item.get('nativeId')}")
        if classification == "extension" and not str(
            item.get("candidateConceptId") or ""
        ).strip():
            raise ValueError(f"extension audit lacks candidate concept: {item.get('nativeId')}")


def main() -> None:
    here = Path(__file__).resolve().parent
    native_root = here.parent / "native"
    for provider in ("aws", "azure", "gcp"):
        inventory = json.loads(
            (native_root / f"{provider}-inventory.json").read_text(encoding="utf-8")
        )
        projection = json.loads(
            (here / f"{provider}-projection.json").read_text(encoding="utf-8")
        )
        packet = make_extension_sample(inventory, projection)
        target = here / f"{provider}-extension-audit.json"
        target.write_text(
            json.dumps(packet, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        print(provider, f"sample={packet['sampleSize']}/{packet['population']}")


if __name__ == "__main__":
    main()
