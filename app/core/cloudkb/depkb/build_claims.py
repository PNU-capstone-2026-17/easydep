"""Validate and materialize the reviewed Docker-on-VM dependency ledger."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.cloudkb.depkb.scope import is_vm_claim

_HERE = Path(__file__).resolve().parent
_SOURCE = _HERE / "claims.source.json"
_ARTIFACT = _HERE / "claims.json"


def _experiment_has_step(experiment: str, step: str) -> bool:
    result_file = _HERE / "experiments" / experiment / "results.json"
    if not result_file.is_file():
        return False
    result = json.loads(result_file.read_text(encoding="utf-8"))
    entries = result.get("steps") or result.get("tests") or {}
    if step in entries:
        return True
    head, separator, tail = step.partition(".")
    nested = entries.get(head)
    return bool(separator and isinstance(nested, dict) and tail in nested)


def build() -> dict:
    """Rebuild the product artifact after validating scope and local evidence."""
    source = json.loads(_SOURCE.read_text(encoding="utf-8"))
    claims = list(source.get("claims") or [])
    if not claims:
        raise ValueError("the reviewed VM claim ledger is empty")

    seen: set[tuple[str, str, str, str, str]] = set()
    for claim in claims:
        if not is_vm_claim(claim):
            raise ValueError(
                f"claim outside Docker-on-VM scope: "
                f"{claim.get('subject')}->{claim.get('object')}"
            )
        key = (
            str(claim.get("csp")),
            str(claim.get("subject")),
            str(claim.get("object")),
            str(claim.get("question")),
            str(claim.get("signal") or ""),
        )
        if key in seen:
            raise ValueError(f"duplicate claim key: {key}")
        seen.add(key)
        if claim.get("csp") not in {"aws", "azure", "gcp"}:
            raise ValueError(f"unsupported CSP in claim: {claim.get('csp')}")
        if claim.get("verdict") not in {"required", "optional", "holds", "unknown"}:
            raise ValueError(f"unsupported verdict in claim: {claim.get('verdict')}")
        evidence = claim.get("evidence") or []
        if not evidence:
            raise ValueError(f"claim has no evidence: {key}")
        for item in evidence:
            experiment = item.get("experiment")
            step = item.get("step")
            if experiment and step and not _experiment_has_step(str(experiment), str(step)):
                raise ValueError(f"missing experiment evidence: {experiment}/{step}")

    claims.sort(
        key=lambda claim: (
            claim["csp"], claim["subject"], claim["object"], claim["question"]
        )
    )
    counts: dict[str, int] = {}
    for claim in claims:
        key = f"{claim['csp']}.{claim['verdict']}"
        counts[key] = counts.get(key, 0) + 1
    return {
        "_note": (
            "Reviewed Docker-on-VM cloud resource dependency claims. Each claim is "
            "scoped to AWS, Azure, or GCP and retains its schema or experiment evidence."
        ),
        "verdictCounts": counts,
        "claims": claims,
    }


if __name__ == "__main__":
    result = build()
    _ARTIFACT.write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("claims:", len(result["claims"]), "|", result["verdictCounts"])
