"""Validate and materialize the reviewed Docker-on-VM dependency ledger."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.cloudkb.depkb.scope import is_vm_claim
from app.core.cloudkb.depkb.terminology import validate_claim

_HERE = Path(__file__).resolve().parent
_SOURCE = _HERE / "claims.source.json"
_ARTIFACT = _HERE / "claims.json"
_PRODUCT_RELATION_FAMILIES = frozenset({"provisioning", "runtime"})


def _inside_depkb(relative: str) -> Path:
    path = (_HERE / relative).resolve()
    if _HERE.resolve() not in path.parents:
        raise ValueError(f"evidence path escapes DepKB: {relative}")
    return path


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
    # The historical source ledger also contains teardown-only observations.
    # EasyDep creates new deployments; Terraform owns teardown ordering.  Keep
    # those raw observations auditable, but do not ship them in the product KB.
    claims = [
        claim
        for claim in (source.get("claims") or [])
        if claim.get("relationFamily") in _PRODUCT_RELATION_FAMILIES
    ]
    if not claims:
        raise ValueError("the reviewed VM claim ledger is empty")

    seen: set[tuple[str, str, str, str, str]] = set()
    for claim in claims:
        validate_claim(claim)
        if not is_vm_claim(claim):
            raise ValueError(
                f"claim outside Docker-on-VM scope: "
                f"{claim.get('subject')}->{claim.get('object')}"
            )
        key = (
            str(claim.get("csp")),
            str(claim.get("subject")),
            str(claim.get("object")),
            str(claim.get("relationFamily")),
            str(claim.get("signal") or ""),
        )
        if key in seen:
            raise ValueError(f"duplicate claim key: {key}")
        seen.add(key)
        if claim.get("csp") not in {"aws", "azure", "gcp"}:
            raise ValueError(f"unsupported CSP in claim: {claim.get('csp')}")
        for item in claim["observations"]:
            experiment = item.get("experiment")
            step = item.get("step")
            if experiment and step and not _experiment_has_step(str(experiment), str(step)):
                raise ValueError(f"missing experiment evidence: {experiment}/{step}")
            if experiment:
                expected_result = f"experiments/{experiment}/results.json"
                if item.get("resultFile") != expected_result:
                    raise ValueError(f"inconsistent result coordinate: {item.get('resultFile')}")
                if not _inside_depkb(expected_result).is_file():
                    raise ValueError(f"missing result file: {expected_result}")
                definition = item.get("definition") or {}
                definition_file = str(definition.get("file") or "")
                definition_path = _inside_depkb(definition_file)
                line = definition.get("line")
                if not definition_path.is_file():
                    raise ValueError(f"missing experiment definition: {definition_file}")
                if not isinstance(line, int) or line < 1:
                    raise ValueError(f"invalid definition line: {definition_file}:{line}")
                line_count = len(definition_path.read_text(encoding="utf-8").splitlines())
                if line > line_count:
                    raise ValueError(f"definition line outside file: {definition_file}:{line}")

    claims.sort(
        key=lambda claim: (
            claim["csp"], claim["subject"], claim["object"], claim["relationFamily"]
        )
    )
    counts: dict[str, int] = {}
    for claim in claims:
        key = f"{claim['csp']}.{claim['finding']}"
        counts[key] = counts.get(key, 0) + 1
    return {
        "schemaVersion": "easydep-dependency-claims/v3",
        "methodology": source.get("methodology", {}),
        "_note": (
            "Docker-on-VM creation and runtime dependency claims for AWS, Azure, "
            "and GCP. Teardown-only relationships are intentionally excluded."
        ),
        "findingCounts": counts,
        "claims": claims,
    }


if __name__ == "__main__":
    result = build()
    _ARTIFACT.write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("claims:", len(result["claims"]), "|", result["findingCounts"])
