"""Run member implementation with an orchestration-side repair-prompt fix."""

from __future__ import annotations

import hashlib
import json


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


def main() -> int:
    from app.implementation.engine import workflow

    workflow.apply_repair_directives = _apply_repair_directives
    from app.implementation.engine.cli import main as member_main

    return member_main()


if __name__ == "__main__":
    raise SystemExit(main())
