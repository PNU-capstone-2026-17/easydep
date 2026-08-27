from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CASE_ROOT = ROOT / "evaluation" / "baselines" / "course-registration-cases"
HARNESS_CASE_ROOT = ROOT / "evaluation" / "checkpoint_e2e" / "cases"
GOLD_ROOT = CASE_ROOT / "goldset"
RUN_ROOT = ROOT / "artifacts" / "checkpoint-e2e"
# Experiment output is deliberately separate from the frozen goldset.  The
# directory is stable so repeated local evaluation does not leave a collection
# of timestamped run directories behind.
CURRENT_EXPERIMENT_ROOT = RUN_ROOT / "current"

CHECKPOINTS = (
    "input",
    "requirements",
    "use_cases",
    "specifications",
    "usecase_diagram",
    "class_diagram",
    "sequence_diagram",
    "api_spec",
    "erd",
    "deployment_diagram",
)

CASES = {
    "e1-aws": {
        "input": HARNESS_CASE_ROOT / "e1-aws.json",
        "gold": GOLD_ROOT / "e1-aws",
    }
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    return str(value)


def jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=_json_default))


def checkpoint_after(checkpoint: str) -> str:
    try:
        index = CHECKPOINTS.index(checkpoint)
    except ValueError as error:
        raise ValueError(f"Unknown checkpoint: {checkpoint}") from error
    if index + 1 >= len(CHECKPOINTS):
        raise ValueError(f"Checkpoint has no successor: {checkpoint}")
    return CHECKPOINTS[index + 1]


def case_definition(case_id: str) -> dict[str, Any]:
    try:
        case = CASES[case_id]
    except KeyError as error:
        raise ValueError(f"Unknown gold case: {case_id}") from error
    payload = read_json(case["input"])
    base_input = payload.get("baseInput")
    if base_input:
        base_path = ROOT / str(base_input)
        base = read_json(base_path)
        payload = {**base, **payload}
        payload["requirements"] = list(base.get("requirements") or [])
        payload["cloudConstraints"] = str(base.get("cloudConstraints") or "")
    scope = payload.get("scope") or {}
    budget_match = re.search(
        r"(?:not exceed|within)\s+([0-9]+(?:\.[0-9]+)?)\s+([A-Z]{3})",
        str(payload.get("cloudConstraints") or ""),
        re.IGNORECASE,
    )
    initial = {
        "provider": (scope.get("providers") or [""])[0],
        "region": scope.get("region") or "",
    }
    if budget_match:
        initial.update(
            monthly_budget_amount=float(budget_match.group(1)),
            monthly_budget_currency=budget_match.group(2).upper(),
        )
    try:
        input_path = case["input"].relative_to(ROOT)
    except ValueError:
        input_path = case["input"]
    return {
        "caseId": case_id,
        "requirements": list(payload.get("requirements") or []),
        "resourceConstraintsText": str(payload.get("cloudConstraints") or ""),
        "initialCloudConstraints": initial,
        "deploymentPlanningFacts": list(payload.get("deploymentPlanningFacts") or []),
        "inputPath": str(input_path).replace("\\", "/"),
        "goldPath": case["gold"],
    }


def snapshot_path(root: Path, checkpoint: str) -> Path:
    return root / "snapshots" / checkpoint / "state.json"


def oracle_path(root: Path, checkpoint: str) -> Path:
    return root / "oracles" / f"{checkpoint}.json"


def current_experiment_path(case_id: str) -> Path:
    """Return the one replaceable experiment destination for a case."""

    if case_id not in CASES:
        raise ValueError(f"Unknown gold case: {case_id}")
    return CURRENT_EXPERIMENT_ROOT / case_id


def current_chain_path(case_id: str) -> Path:
    """Return the current sequential candidate chain for a case."""

    return current_experiment_path(case_id) / "chain"


def current_stage_sample_path(case_id: str, source_checkpoint: str) -> Path:
    """Return the current isolated sample destination for one transition."""

    target = checkpoint_after(source_checkpoint)
    return current_experiment_path(case_id) / "stages" / _stage_name(
        source_checkpoint, target
    )


def _stage_name(source_checkpoint: str, target_checkpoint: str) -> str:
    return f"{CHECKPOINTS.index(target_checkpoint):02d}-{source_checkpoint}-to-{target_checkpoint}"


def load_gold(case_id: str, checkpoint: str) -> tuple[dict[str, Any], dict[str, Any]]:
    case = case_definition(case_id)
    root = Path(case["goldPath"])
    manifest = read_json(root / "manifest.json")
    if manifest.get("schemaVersion") != "easydep-checkpoint-goldset":
        raise ValueError("Unsupported gold manifest schema")
    entry = next(
        (item for item in manifest.get("checkpoints") or [] if item.get("id") == checkpoint),
        None,
    )
    if entry is None:
        raise ValueError(f"Gold checkpoint is absent: {checkpoint}")
    state = read_json(snapshot_path(root, checkpoint))
    if digest(state) != entry.get("sha256"):
        raise ValueError(f"Gold checkpoint digest mismatch: {checkpoint}")
    return state, read_json(oracle_path(root, checkpoint))
