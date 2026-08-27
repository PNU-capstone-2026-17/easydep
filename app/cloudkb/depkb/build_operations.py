"""Validate and materialize reviewed VM operation characteristics."""

from __future__ import annotations

import json
from pathlib import Path

from app.cloudkb.depkb.scope import VM_RESOURCE_TYPES

_HERE = Path(__file__).resolve().parent
_SOURCE = _HERE / "operations.source.json"
_ARTIFACT = _HERE / "operations.json"


def build() -> dict:
    source = json.loads(_SOURCE.read_text(encoding="utf-8"))
    operations = list(source.get("operations") or [])
    for operation in operations:
        if operation.get("resource") not in VM_RESOURCE_TYPES:
            raise ValueError(
                f"operation outside Docker-on-VM scope: {operation.get('resource')}"
            )
        if operation.get("csp") not in {"aws", "azure", "gcp"}:
            raise ValueError(f"unsupported CSP: {operation.get('csp')}")
        evidence = operation.get("evidence") or {}
        result_file = (
            _HERE
            / "experiments"
            / str(evidence.get("experiment"))
            / "results.json"
        )
        if not result_file.is_file():
            raise ValueError(f"missing operation evidence: {result_file}")
    operations.sort(key=lambda row: (row["csp"], row["resource"], row["op"]))
    counts: dict[str, int] = {}
    for operation in operations:
        key = f"{operation['csp']}.{operation['status']}"
        counts[key] = counts.get(key, 0) + 1
    return {
        "_note": "Reviewed asynchronous operation observations for VM resources.",
        "statusMeaning": source.get("statusMeaning") or {},
        "cspCharacter": source.get("cspCharacter") or {},
        "counts": counts,
        "operations": operations,
    }


if __name__ == "__main__":
    result = build()
    _ARTIFACT.write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("operations:", len(result["operations"]), "|", result["counts"])
