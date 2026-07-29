from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


REPAIR_SCHEMA = "implementation-repair-plan/v1alpha1"
REPAIR_PLAN = Path("reports/repair-plan.json")
TASK_PHASES = {
    "control": "control",
    "persistence-entities": "persistence",
    "persistence-repositories": "persistence",
    "persistence-mapping": "persistence",
    "persistence-schema": "persistence",
    "api-adapter": "api-adapters",
    "boundary-adapter": "boundary-adapters",
    "gateway-adapter": "outbound-adapters",
    "configuration": "wiring",
    "integration-test": "end-to-end",
}
PHASE_DEPENDENCIES = {
    "control": (),
    "persistence": ("control",),
    "api-adapters": ("control",),
    "boundary-adapters": ("control",),
    "outbound-adapters": ("control", "persistence"),
    "wiring": (
        "persistence",
        "api-adapters",
        "boundary-adapters",
        "outbound-adapters",
    ),
    "end-to-end": ("wiring",),
}


def schedule_cross_phase_repair(
    run_root: Path,
    failed_task_id: str,
    evidence: dict[str, object],
) -> dict[str, object] | None:
    """Plan bounded upstream repairs and downstream revalidation from build evidence."""
    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = _read_json(manifest_path)
    tasks = list(manifest.get("implementation_tasks", []))
    task_by_id = {str(task["task_id"]): task for task in tasks}
    failed = task_by_id.get(failed_task_id)
    if failed is None:
        return None

    evidence_text = _evidence_text(evidence)
    owner_ids = _owners_named_in_evidence(tasks, failed_task_id, evidence_text)
    if not owner_ids:
        owner_ids = _infer_upstream_owners(tasks, failed, evidence_text)
    if not owner_ids:
        return None

    plan_path = run_root / REPAIR_PLAN
    plan = _read_json(plan_path) if plan_path.is_file() else {
        "schemaVersion": REPAIR_SCHEMA,
        "entries": [],
    }
    evidence_sha = hashlib.sha256(evidence_text.encode("utf-8")).hexdigest()
    matching = next(
        (
            item for item in plan.get("entries", [])
            if item.get("failedTaskId") == failed_task_id
            and item.get("evidenceSha256") == evidence_sha
        ),
        None,
    )
    max_revisions = int(os.environ.get("IMPLEMENTATION_MAX_CROSS_PHASE_REPAIRS", "3"))
    revision = int(matching.get("revision", 0)) + 1 if matching else 1
    if revision > max_revisions:
        return None

    owner_phases = {_phase(task_by_id[task_id]) for task_id in owner_ids}
    revalidation_ids = sorted(
        str(task["task_id"])
        for task in tasks
        if str(task["task_id"]) not in owner_ids
        and any(_depends_on(_phase(task), phase) for phase in owner_phases)
    )
    entry = {
        "failedTaskId": failed_task_id,
        "ownerTaskIds": sorted(owner_ids),
        "revalidationTaskIds": revalidation_ids,
        "evidenceSha256": evidence_sha,
        "evidence": evidence_text[-12000:],
        "revision": revision,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    if matching:
        matching.clear()
        matching.update(entry)
    else:
        plan.setdefault("entries", []).append(entry)
    plan["updatedAt"] = entry["createdAt"]
    _write_json(plan_path, plan)
    return entry


def schedule_source_conformance_repair(
    run_root: Path, report: dict[str, object]
) -> dict[str, object] | None:
    """Re-plan bounded source repairs using deterministic conformance evidence."""
    violations = report.get("violations", [])
    if not any(
        isinstance(item, dict) and item.get("code") == "SEQUENCE_CALL_NOT_IMPLEMENTED"
        for item in violations
    ):
        return None
    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = _read_json(manifest_path)
    tasks = list(manifest.get("implementation_tasks", []))
    owners = [
        str(task["task_id"]) for task in tasks
        if task.get("task_type") in {
            "control", "api-adapter", "boundary-adapter", "gateway-adapter", "configuration"
        }
    ]
    if not owners:
        return None
    evidence = json.dumps(violations, ensure_ascii=False, indent=2)
    plan_path = run_root / REPAIR_PLAN
    plan = _read_json(plan_path) if plan_path.is_file() else {"schemaVersion": REPAIR_SCHEMA, "entries": []}
    evidence_sha = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
    matching = next((item for item in plan.get("entries", []) if item.get("failedTaskId") == "source-design-conformance" and item.get("evidenceSha256") == evidence_sha), None)
    revision = int(matching.get("revision", 0)) + 1 if matching else 1
    if revision > int(os.environ.get("IMPLEMENTATION_MAX_CONFORMANCE_REPAIRS", "3")):
        return None
    entry = {
        "failedTaskId": "source-design-conformance",
        "ownerTaskIds": sorted(owners),
        "revalidationTaskIds": [str(task["task_id"]) for task in tasks if task.get("task_type") == "integration-test"],
        "evidenceSha256": evidence_sha,
        "evidence": evidence,
        "revision": revision,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    if matching:
        matching.clear(); matching.update(entry)
    else:
        plan.setdefault("entries", []).append(entry)
    plan["updatedAt"] = entry["createdAt"]
    _write_json(plan_path, plan)
    return entry


def apply_repair_directives(run_root: Path) -> None:
    """Make repair evidence part of task prompts and therefore HITL request hashes."""
    plan_path = run_root / REPAIR_PLAN
    if not plan_path.is_file():
        return
    plan = _read_json(plan_path)
    entries = [item for item in plan.get("entries", []) if isinstance(item, dict)]
    if not entries:
        return

    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = _read_json(manifest_path)
    task_files = {}
    task_dir = run_root / "reports" / "implementation-tasks"
    for path in task_dir.glob("*.task.json"):
        task = _read_json(path)
        task_files[str(task.get("task_id"))] = (path, task)

    for task in manifest.get("implementation_tasks", []):
        task_id = str(task.get("task_id"))
        relevant = [
            entry for entry in entries
            if task_id in entry.get("ownerTaskIds", [])
            or task_id in entry.get("revalidationTaskIds", [])
        ]
        if not relevant:
            continue
        prompt_path = run_root / str(task["prompt_file"])
        prompt = prompt_path.read_text(encoding="utf-8")
        additions = ["\n\n## Orchestrated repair and revalidation directives\n"]
        for entry in relevant:
            role = (
                "repair the failure in your owned files"
                if task_id in entry.get("ownerTaskIds", [])
                else "regenerate and revalidate after an upstream repair"
            )
            additions.append(
                f"\n### Revision {entry['revision']} from `{entry['failedTaskId']}`\n"
                f"Your role is to {role}. Use the verification evidence below, preserve "
                "the exact generated contracts, and keep all changes inside this task's "
                "existing allowlist.\n\n```text\n{entry['evidence']}\n```\n"
            )
        prompt += "".join(additions)
        prompt_path.write_text(prompt, encoding="utf-8")
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        task["prompt_sha256"] = prompt_sha
        sources = dict(task.get("source_artifacts", {}))
        sources["repairEvidence"] = str(plan_path)
        task["source_artifacts"] = sources
        task_file = task_files.get(task_id)
        if task_file:
            path, definition = task_file
            definition["prompt_sha256"] = prompt_sha
            definition["source_artifacts"] = sources
            _write_json(path, definition)
    _write_json(manifest_path, manifest)


def referenced_source_paths(evidence: dict[str, object]) -> list[str]:
    text = _evidence_text(evidence).replace("\\", "/")
    matches = re.findall(r"(?:[A-Za-z]:)?[^\r\n:]*?(application/(?:src|build)/[^\r\n:]+?\.(?:java|sql|yml))(?=:\d|:|\s|$)", text)
    return sorted({match.strip().lstrip("/") for match in matches})


def _owners_named_in_evidence(
    tasks: list[dict[str, object]], failed_task_id: str, evidence: str
) -> set[str]:
    normalized = evidence.replace("\\", "/").lower()
    owners: set[str] = set()
    for task in tasks:
        task_id = str(task.get("task_id"))
        if task_id == failed_task_id:
            continue
        for output in task.get("allowed_write_paths", []):
            relative = str(output).replace("\\", "/")
            if relative.lower() in normalized or Path(relative).name.lower() in normalized:
                owners.add(task_id)
                break
    return owners


def _infer_upstream_owners(
    tasks: list[dict[str, object]], failed: dict[str, object], evidence: str
) -> set[str]:
    failed_type = str(failed.get("task_type", ""))
    lowered = evidence.lower()
    if failed_type == "configuration" and re.search(r"repository|jpa|entitymanager|bean", lowered):
        return {
            str(task["task_id"]) for task in tasks
            if task.get("task_type") == "persistence-repositories"
        }
    if failed_type == "integration-test":
        api_tasks = [task for task in tasks if task.get("task_type") == "api-adapter"]
        named = {
            str(task["task_id"]) for task in api_tasks
            if str(task.get("control", "")).lower() in lowered
            or any(
                Path(str(path)).stem.lower() in lowered
                for path in task.get("allowed_write_paths", [])
            )
        }
        return named or {str(task["task_id"]) for task in api_tasks}
    return set()


def _phase(task: dict[str, object]) -> str:
    return TASK_PHASES.get(str(task.get("task_type", "")), "unclassified")


def _depends_on(candidate: str, dependency: str) -> bool:
    if candidate == dependency or candidate not in PHASE_DEPENDENCIES:
        return False
    direct = PHASE_DEPENDENCIES[candidate]
    return dependency in direct or any(_depends_on(item, dependency) for item in direct)


def _evidence_text(evidence: dict[str, object]) -> str:
    return "\n".join(
        str(evidence.get(key, "")) for key in ("stdout", "stderr", "testResults")
    ).strip()


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
