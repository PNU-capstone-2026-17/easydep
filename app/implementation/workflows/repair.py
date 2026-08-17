from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import settings


REPAIR_SCHEMA = "implementation-repair-plan/v1alpha1"
REPAIR_PLAN = Path("reports/repair-plan.json")
REPAIR_PROMPT_HEADING = "## Orchestrated repair and revalidation directives"
REPAIR_PROMPT_START = "<!-- easydep:repair-directives:start -->"
REPAIR_PROMPT_END = "<!-- easydep:repair-directives:end -->"
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
    "frontend-implementation": "frontend",
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
    "frontend": ("api-adapters",),
    "end-to-end": ("wiring", "frontend"),
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
    causal_evidence = _causal_evidence_text(evidence)
    owner_ids = _owners_named_in_evidence(tasks, failed_task_id, causal_evidence)
    if not owner_ids:
        owner_ids = _infer_upstream_owners(tasks, failed, causal_evidence)
    if not owner_ids:
        return None

    plan_path = run_root / REPAIR_PLAN
    plan = _read_json(plan_path) if plan_path.is_file() else {
        "schemaVersion": REPAIR_SCHEMA,
        "entries": [],
    }
    evidence_sha = hashlib.sha256(evidence_text.encode("utf-8")).hexdigest()
    matching_entries = _entries_for_failure(plan, failed_task_id)
    max_revisions = settings.implementation_max_cross_phase_repairs
    revision = _chain_revision(matching_entries) + 1
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
        "repairChainId": _repair_chain_id(failed_task_id),
        "ownerTaskIds": sorted(owner_ids),
        "revalidationTaskIds": revalidation_ids,
        "evidenceSha256": evidence_sha,
        "failureFingerprint": _failure_fingerprint(causal_evidence),
        "evidence": causal_evidence[-8000:],
        "revision": revision,
        "createdAt": min(
            (
                str(item.get("createdAt"))
                for item in matching_entries
                if item.get("createdAt")
            ),
            default=datetime.now(timezone.utc).isoformat(),
        ),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    plan["entries"] = [
        item
        for item in plan.get("entries", [])
        if not isinstance(item, dict) or item.get("failedTaskId") != failed_task_id
    ]
    plan["entries"].append(entry)
    plan["updatedAt"] = entry["updatedAt"]
    _write_json(plan_path, plan)
    return entry


def schedule_source_conformance_repair(
    run_root: Path, report: dict[str, object]
) -> dict[str, object] | None:
    """Re-plan bounded source repairs using deterministic conformance evidence."""
    violations = report.get("violations", [])
    codes = {
        str(item.get("code"))
        for item in violations
        if isinstance(item, dict)
    }
    sequence_codes = {
        "SEQUENCE_CALL_NOT_IMPLEMENTED",
        "SEQUENCE_BRANCH_NOT_IMPLEMENTED",
        "SEQUENCE_CALL_ORDER_NOT_IMPLEMENTED",
        "UNMAPPABLE_SEQUENCE_TARGET",
    }
    erd_codes = {"ERD_ENTITY_NOT_IMPLEMENTED", "ERD_RELATION_NOT_IMPLEMENTED"}
    if not (codes & (sequence_codes | erd_codes)):
        return None
    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = _read_json(manifest_path)
    tasks = list(manifest.get("implementation_tasks", []))
    owner_types: set[str] = set()
    if codes & sequence_codes:
        owner_types.update({
            "control", "api-adapter", "boundary-adapter", "gateway-adapter",
            "configuration",
        })
    if codes & erd_codes:
        owner_types.update({
            "persistence-entities", "persistence-repositories",
            "persistence-mapping", "persistence-schema", "gateway-adapter",
        })
    owners = [
        str(task["task_id"])
        for task in tasks
        if task.get("task_type") in owner_types
    ]
    if not owners:
        return None
    evidence = json.dumps(violations, ensure_ascii=False, indent=2)
    plan_path = run_root / REPAIR_PLAN
    plan = _read_json(plan_path) if plan_path.is_file() else {"schemaVersion": REPAIR_SCHEMA, "entries": []}
    evidence_sha = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
    matching_entries = _entries_for_failure(plan, "source-design-conformance")
    revision = _chain_revision(matching_entries) + 1
    if revision > settings.implementation_max_conformance_repairs:
        return None
    entry = {
        "failedTaskId": "source-design-conformance",
        "repairChainId": _repair_chain_id("source-design-conformance"),
        "ownerTaskIds": sorted(owners),
        "revalidationTaskIds": [str(task["task_id"]) for task in tasks if task.get("task_type") == "integration-test"],
        "evidenceSha256": evidence_sha,
        "evidence": evidence,
        "revision": revision,
        "createdAt": min(
            (
                str(item.get("createdAt"))
                for item in matching_entries
                if item.get("createdAt")
            ),
            default=datetime.now(timezone.utc).isoformat(),
        ),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    plan["entries"] = [
        item
        for item in plan.get("entries", [])
        if not isinstance(item, dict)
        or item.get("failedTaskId") != "source-design-conformance"
    ]
    plan["entries"].append(entry)
    plan["updatedAt"] = entry["updatedAt"]
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
        prompt_path = run_root / str(task["prompt_file"])
        original_prompt = prompt_path.read_text(encoding="utf-8")
        prompt = _without_repair_directives(original_prompt)
        additions = [
            f"\n\n{REPAIR_PROMPT_START}\n{REPAIR_PROMPT_HEADING}\n"
        ]
        for entry in relevant:
            role = (
                "repair the failure in your owned files"
                if task_id in entry.get("ownerTaskIds", [])
                else "regenerate and revalidate after an upstream repair"
            )
            evidence = str(entry.get("evidence", ""))
            if task_id not in entry.get("ownerTaskIds", []):
                evidence = _compact_evidence(evidence)
            additions.append(
                f"\n### Revision {entry['revision']} from `{entry['failedTaskId']}`\n"
                f"Your role is to {role}. Use the verification evidence below, preserve "
                "the exact generated contracts, and keep all changes inside this task's "
                f"existing allowlist.\n\n```text\n{evidence}\n```\n"
            )
        if relevant:
            additions.append(f"\n{REPAIR_PROMPT_END}\n")
            prompt += "".join(additions)
        if prompt == original_prompt:
            continue
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
            # Basenames commonly occur in warnings and test reports. Only a
            # complete contracted source path is strong enough ownership evidence.
            if relative.lower() in normalized:
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


def _causal_evidence_text(evidence: dict[str, object]) -> str:
    """Keep failure causes while excluding incidental warning/file mentions."""
    test_results = str(evidence.get("testResults", "")).strip()
    if test_results:
        return test_results
    combined = "\n".join(
        str(evidence.get(key, "")) for key in ("stderr", "stdout")
    )
    causal = [
        line
        for line in combined.splitlines()
        if "warning" not in line.lower()
        and re.search(r"error|exception|caused by|failed|failure", line, re.IGNORECASE)
    ]
    return "\n".join(causal).strip() or combined.strip()


def _failure_fingerprint(evidence: str) -> str:
    normalized = evidence.lower().replace("\\", "/")
    normalized = re.sub(
        r"(?:[a-z]:)?[^\s:]+/application/", "application/", normalized
    )
    normalized = re.sub(r":\d+(?::\d+)?", ":<line>", normalized)
    normalized = re.sub(r"\b[0-9a-f]{12,}\b", "<id>", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _repair_chain_id(failed_task_id: str) -> str:
    return hashlib.sha256(failed_task_id.encode("utf-8")).hexdigest()[:16]


def _entries_for_failure(
    plan: dict[str, object], failed_task_id: str
) -> list[dict[str, object]]:
    return [
        item
        for item in plan.get("entries", [])
        if isinstance(item, dict) and item.get("failedTaskId") == failed_task_id
    ]


def _chain_revision(entries: list[dict[str, object]]) -> int:
    """Count legacy split entries and the new cumulative chain equally."""
    return sum(max(1, int(item.get("revision", 0))) for item in entries)


def repair_rounds(plan: dict[str, object]) -> int:
    """Return the largest cumulative repair chain, including legacy plans."""
    entries = [
        item for item in plan.get("entries", []) if isinstance(item, dict)
    ]
    failed_ids = {
        str(item.get("failedTaskId"))
        for item in entries
        if item.get("failedTaskId")
    }
    return max(
        [
            _chain_revision(_entries_for_failure(plan, failed_id))
            for failed_id in failed_ids
        ]
        + [
            max(
                (int(item.get("revision", 0)) for item in entries if not item.get("failedTaskId")),
                default=0,
            )
        ],
        default=0,
    )


def _without_repair_directives(prompt: str) -> str:
    if REPAIR_PROMPT_START in prompt:
        return prompt.split(REPAIR_PROMPT_START, 1)[0].rstrip()
    legacy = f"\n\n{REPAIR_PROMPT_HEADING}"
    return prompt.split(legacy, 1)[0].rstrip()


def _compact_evidence(evidence: str, limit: int = 1200) -> str:
    if len(evidence) <= limit:
        return evidence
    return "..." + evidence[-limit:]


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
