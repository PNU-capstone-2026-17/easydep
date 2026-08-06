"""Run member implementation with an orchestration-side repair-prompt fix."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy


def _apply_repair_directives(run_root) -> None:
    from app.implementation.engine.repair_planner import (
        apply_repair_directives as member_apply,
    )

    marker = "\n\n## Orchestrated repair and revalidation directives\n"
    task_dir = run_root / "reports" / "implementation-tasks"
    for path in task_dir.glob("*.prompt.md"):
        prompt = path.read_text(encoding="utf-8")
        path.write_text(prompt.split(marker, 1)[0], encoding="utf-8")
    member_apply(run_root)

    plan_path = run_root / "reports" / "repair-plan.json"
    if not plan_path.is_file():
        return
    plan = json.loads(plan_path.read_text("utf-8"))
    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    definitions = {
        json.loads(path.read_text("utf-8"))["task_id"]: path
        for path in task_dir.glob("*.task.json")
    }
    for task in manifest.get("implementation_tasks", []):
        task_id = task["task_id"]
        relevant = [
            entry
            for entry in plan.get("entries", [])
            if task_id in entry.get("ownerTaskIds", [])
            or task_id in entry.get("revalidationTaskIds", [])
        ]
        if not relevant:
            continue
        prompt_path = run_root / task["prompt_file"]
        prompt = prompt_path.read_text("utf-8")
        if task.get("task_type") == "persistence-entities":
            prompt += """

## BCE foreign-key compatibility contract

- Every scalar BCE field ending in `Id` is an immutable cross-layer contract. Keep
  the corresponding persistence getter and setter even when the ERD also requires
  a JPA relationship object.
- Map the scalar foreign-key value and relationship without mapping the same column
  as writable twice. Relationship setters and helpers must keep the scalar ID value
  consistent whenever the related entity has an ID.
- Do not replace a required scalar `get...Id()`/`set...Id()` pair with only an
  object-valued relationship accessor; the generated mapper must compile against
  both the BCE scalar contract and the JPA relationship contract.
"""
        for entry in relevant:
            prompt = prompt.replace("{entry['evidence']}", entry.get("evidence", ""), 1)
        prompt_path.write_text(prompt, encoding="utf-8")
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        task["prompt_sha256"] = digest
        definition_path = definitions[task_id]
        definition = json.loads(definition_path.read_text("utf-8"))
        definition["prompt_sha256"] = digest
        definition_path.write_text(json.dumps(definition, indent=2), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _prioritize_repair_owners(original, run_root, state):
    """Request the repair owner before retrying the task that exposed its failure."""
    plan_path = run_root / "reports" / "repair-plan.json"
    runnable = set(state.get("nextRunnableTasks") or [])
    failed = {
        task["taskId"]
        for task in state.get("tasks", [])
        if task.get("status") == "FAILED"
    }
    if not plan_path.is_file() or not runnable or not failed:
        return original(run_root, state)
    plan = json.loads(plan_path.read_text("utf-8"))
    owners: set[str] = set()
    for entry in reversed(plan.get("entries", [])):
        if entry.get("failedTaskId") in failed:
            owners.update(entry.get("ownerTaskIds", []))
    owners &= runnable
    if not owners:
        return original(run_root, state)
    prioritized = deepcopy(state)
    prioritized["nextRunnableTasks"] = sorted(owners)
    return original(run_root, prioritized)


def main() -> int:
    from app.implementation.engine import workflow

    workflow.apply_repair_directives = _apply_repair_directives
    member_write_request = workflow.write_transmission_request
    workflow.write_transmission_request = lambda run_root, state: (
        _prioritize_repair_owners(member_write_request, run_root, state)
    )
    from app.implementation.engine.cli import main as member_main

    return member_main()


if __name__ == "__main__":
    raise SystemExit(main())
