"""Deterministic scorer for the requirements-agent evaluation suite.

The scorer reads persisted EasyDep run artifacts. It never sends oracle data to the agent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def preclassified_errors(classified: object) -> list[str]:
    """Validate the portable, preclassified requirements checkpoint.

    Evaluation runs deliberately begin after classification.  Keeping this check
    here makes that boundary explicit without importing the production classifier
    (or its BERT runtime).
    """
    if not isinstance(classified, list) or not classified:
        return ["classified requirements must be a non-empty list"]

    errors: list[str] = []
    identifiers: set[str] = set()
    for index, item in enumerate(classified, start=1):
        label = f"classified[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier.strip():
            errors.append(f"{label}.id must be a non-empty string")
        elif identifier in identifiers:
            errors.append(f"duplicate classified requirement id: {identifier}")
        else:
            identifiers.add(identifier)
        if not isinstance(item.get("text"), str) or not item["text"].strip():
            errors.append(f"{label}.text must be a non-empty string")
        if item.get("type") not in {"FR", "NFR"}:
            errors.append(f"{label}.type must be FR or NFR")
    return errors


def requirements_semantic_oracle(classified: object) -> dict:
    """Return an order-independent signature of the persisted BERT checkpoint."""
    errors = preclassified_errors(classified)
    items = classified if isinstance(classified, list) else []
    signature = sorted(
        (
            {
                "id": str(item.get("id") or ""),
                "type": str(item.get("type") or ""),
                "text": " ".join(str(item.get("text") or "").split()),
                "qualifies": sorted(str(value) for value in item.get("qualifies") or []),
            }
            for item in items
            if isinstance(item, dict)
        ),
        key=lambda item: item["id"],
    )
    return {
        "valid": not errors,
        "requirements": signature,
    }


def require_preclassified(classified: object) -> list[dict]:
    errors = preclassified_errors(classified)
    if errors:
        raise ValueError("Invalid preclassified requirements checkpoint: " + "; ".join(errors))
    return list(classified)


def _norm(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.lower())
    return " ".join(word[:-1] if word.endswith("s") and len(word) > 3 else word for word in words)


def _same_actor(actual: str, expected: str) -> bool:
    a, e = _norm(actual), _norm(expected)
    return a == e or a.endswith(f" {e}") or e.endswith(f" {a}")


def verify_holdout_hashes() -> list[str]:
    suite = _load(ROOT / "suite.json")
    failures = []
    for relative, expected in suite["frozenHashes"].items():
        content = (ROOT / relative).read_text(encoding="utf-8").replace("\r\n", "\n")
        actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if actual != expected:
            failures.append(relative)
    return failures


def score(run_dir: Path) -> dict:
    manifest = _load(run_dir / "manifest.json")
    input_obj = _load(run_dir / "input.json")
    actors = _load(run_dir / "actors.json")
    use_cases = _load(run_dir / "use_cases.json")
    relationships = _load(run_dir / "relationships.json")
    oracle = _load(ROOT / "oracle.json")[manifest["dataset"]]

    actor_names = [actor["name"] for actor in actors]
    required = oracle.get("requiredActors", [])
    found_required = [
        expected for expected in required if any(_same_actor(actual, expected) for actual in actor_names)
    ]

    facts = oracle.get("roleFacts", [])
    fact_results = []
    for fact in facts:
        required_ids = set(fact["requirementIds"])
        candidates = [
            uc for uc in use_cases if required_ids.intersection(uc.get("requirement_ids", []))
        ]
        if fact["role"] == "primary":
            matched = any(_same_actor(uc.get("primary_actor", ""), fact["actor"]) for uc in candidates)
        else:
            matched = any(
                any(_same_actor(actor, fact["actor"]) for actor in uc.get("supporting_actors", []))
                for uc in candidates
            )
        fact_results.append({**fact, "matched": matched})

    known_names = required
    unsupported = [
        name for name in actor_names if not any(_same_actor(name, expected) for expected in known_names)
    ]
    forbidden_supporting = oracle.get("forbiddenSupportingActors", [])
    forbidden_hits = sorted({
        actor
        for uc in use_cases
        for actor in uc.get("supporting_actors", [])
        if any(_same_actor(actor, forbidden) for forbidden in forbidden_supporting)
    })

    metrics = manifest.get("metrics", {})
    spec_issues = manifest.get("summary", {}).get("spec_issues", {})
    return {
        "dataset": manifest["dataset"],
        "requirementsCheckpoint": requirements_semantic_oracle(
            input_obj.get("classified")
        ),
        "actorRecall": len(found_required) / len(required) if required else 1.0,
        "roleAccuracy": sum(row["matched"] for row in fact_results) / len(fact_results) if fact_results else 1.0,
        "unsupportedActors": unsupported,
        "forbiddenSupportingActors": forbidden_hits,
        "failedRoleFacts": [row for row in fact_results if not row["matched"]],
        "coverage": manifest.get("summary", {}).get("coverage", {}).get("coverage_ratio"),
        "specificationIssueCount": sum(len(items) for items in spec_issues.values()),
        "llmCalls": metrics.get("llm_calls"),
        "totalTokens": (metrics.get("prompt_tokens") or 0) + (metrics.get("completion_tokens") or 0),
        "wallSeconds": metrics.get("wall_seconds"),
        "associationCount": len(relationships.get("associations", [])),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dirs", nargs="*", type=Path)
    parser.add_argument("--verify-holdout", action="store_true")
    args = parser.parse_args()
    if args.verify_holdout:
        failures = verify_holdout_hashes()
        print(json.dumps({"holdoutHashFailures": failures}, indent=2))
        if failures:
            return 2
    for run_dir in args.run_dirs:
        print(json.dumps(score(run_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
