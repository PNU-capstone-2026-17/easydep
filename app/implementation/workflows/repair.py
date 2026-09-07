"""Route implementation failures back to the owning implementation agent.

Repair ownership comes from structured evidence, the failed task, or declared write roots.
Diagnostic wording is evidence for the owner, never a heuristic routing API.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

REPAIR_SCHEMA = "implementation-repair-plan/v4"
REPAIR_PLAN = Path("reports/repair-plan.json")
REPAIR_PROMPT_DIR = Path("reports/implementation-tasks")
REPAIR_PROMPT_HEADING = "## Automatic repair task"
REPAIR_PROMPT_START = "<!-- easydep:repair-directives:start -->"
REPAIR_PROMPT_END = "<!-- easydep:repair-directives:end -->"

def schedule_cross_phase_repair(
    run_root: Path,
    failed_task_id: str,
    evidence: dict[str, object],
) -> dict[str, object] | None:
    """Schedule repair with the explicitly declared implementation owner."""
    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = _read_json(manifest_path)
    tasks = [
        task
        for task in manifest.get("implementation_tasks", [])
        if isinstance(task, dict) and task.get("task_id")
    ]
    if not tasks:
        return None

    paths = referenced_source_paths(evidence)
    failed = next(
        (task for task in tasks if str(task.get("task_id")) == failed_task_id),
        None,
    )

    explicit_owner = evidence.get("owner")
    owner = (
        explicit_owner.strip()
        if isinstance(explicit_owner, str) and explicit_owner.strip()
        else ""
    )
    if not owner and failed is not None:
        owner = str(failed.get("owner", "")).strip()
    if not owner:
        owner = _owner_for_paths(tasks, paths)
    owner_ids = {
        str(task["task_id"])
        for task in tasks
        if owner and str(task.get("owner", "")) == owner
    }
    if not owner_ids:
        return None

    current_text = _evidence_text(evidence)
    plan_path = run_root / REPAIR_PLAN
    plan = (
        _read_json(plan_path)
        if plan_path.is_file()
        else {"schemaVersion": REPAIR_SCHEMA, "entries": []}
    )
    entries = [
        entry
        for entry in plan.get("entries", [])
        if isinstance(entry, dict) and entry.get("failedTaskId") == failed_task_id
    ]
    repair_paths = _repair_paths(tasks, owner_ids, paths)
    source_digest = _source_digest(
        run_root,
        repair_paths or _owner_digest_paths(tasks, owner_ids),
    )
    failure_digest = hashlib.sha256(current_text.encode("utf-8")).hexdigest()
    same_failure_count = sum(
        1
        for item in entries
        if item.get("failureDigest") == failure_digest
        and item.get("acceptedSourceDigest") == source_digest
    )
    strategy = _repair_strategy(same_failure_count)
    now = datetime.now(UTC).isoformat()
    entry = {
        "failedTaskId": failed_task_id,
        "owner": owner,
        "ownerTaskIds": sorted(owner_ids),
        "outcome": "scheduled",
        "evidence": _bounded_evidence(current_text),
        "relatedPaths": paths,
        "repairPaths": repair_paths,
        "failureDigest": failure_digest,
        "acceptedSourceDigest": source_digest,
        "acceptedSourceRoot": "application",
        "strategy": strategy,
        "revision": len(entries) + 1,
        "createdAt": str(entries[0].get("createdAt")) if entries else now,
        "updatedAt": now,
    }
    all_entries = [item for item in plan.get("entries", []) if isinstance(item, dict)]
    plan.update(
        {
            "schemaVersion": REPAIR_SCHEMA,
            "status": "ACTIVE",
            "entries": [*all_entries, entry],
            "updatedAt": now,
        }
    )
    plan.pop("stallReason", None)
    _write_json(plan_path, plan)
    return entry


def schedule_source_conformance_repair(
    run_root: Path, report: dict[str, object]
) -> dict[str, object] | None:
    """공개 계약 또는 ERD 검사 결과를 일반 자동 수리 흐름에 넣는다."""
    violations = [
        item for item in report.get("violations", []) if isinstance(item, dict)
    ]
    if not violations:
        return None
    evidence = {
        "owner": "backend",
        "command": ["source-design-conformance"],
        "stderr": json.dumps(violations, ensure_ascii=False, indent=2),
    }
    return schedule_cross_phase_repair(
        run_root,
        "source-design-conformance",
        evidence,
    )


def apply_repair_directives(run_root: Path) -> None:
    """초기 구현 설명과 분리된 짧은 수리 prompt를 만든다.

    초기 prompt는 기능 전체를 처음 만드는 데 유용하지만, 작은 compile 또는 HTTP 오류를
    고칠 때 다시 보내면 모델이 이미 정상인 코드를 재검토하게 된다. 작업 정의에는 별도
    ``repair_prompt_file``만 연결하고 원본 prompt는 그대로 보존한다.
    """
    plan_path = run_root / REPAIR_PLAN
    if not plan_path.is_file():
        return
    plan = _read_json(plan_path)
    entries = [item for item in plan.get("entries", []) if isinstance(item, dict)]
    if not entries:
        return

    active = entries[-1]
    active_ids = {str(value) for value in active.get("ownerTaskIds", [])}
    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = _read_json(manifest_path)
    task_files = _task_files(run_root)

    for task in manifest.get("implementation_tasks", []):
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("task_id", ""))
        prompt_path = run_root / str(task.get("prompt_file", ""))
        if not task_id or not prompt_path.is_file():
            continue
        original = prompt_path.read_text(encoding="utf-8")
        base_prompt = _without_repair_directives(original)
        if base_prompt != original:
            prompt_path.write_text(base_prompt, encoding="utf-8")
        base_digest = hashlib.sha256(base_prompt.encode("utf-8")).hexdigest()
        relevant = [
            entry for entry in entries if task_id in entry.get("ownerTaskIds", [])
        ]
        repair_prompt_path = run_root / REPAIR_PROMPT_DIR / f"{task_id}.repair.md"
        repair_prompt = ""
        if task_id in active_ids and relevant:
            current = relevant[-1]
            previous = relevant[-4:-1]
            plan_history = "\n".join(
                f"- {entry.get('strategy', 'previous approach')}: "
                + _first_evidence_line(str(entry.get("evidence", "")))
                for entry in previous
            ) or "- No previous failures"
            execution_history = _recent_execution_history(run_root, task_id)
            history = plan_history
            if execution_history:
                history += "\n\n### Previous changes and verification results\n\n" + execution_history
            source_hints = "\n".join(
                f"- `{path}`" for path in current.get("relatedPaths", [])
            ) or "- Start with the source paths from the task definition"
            immutable = "\n".join(
                f"- `{path}`" for path in task.get("immutable_paths", [])
            ) or "- None"
            repair_prompt = (
                f"# {REPAIR_PROMPT_HEADING.removeprefix('## ')}\n\n"
                "Resolve the technical failure below. Choose the "
                "implementation, tests, and edit order autonomously. Do not change unrelated "
                "features or generated public contracts. Read needed source with the file editor.\n\n"
                "Use the terminal to reproduce the failure against the current source before "
                "editing. The history below may describe source that has already changed; do not "
                "waste time searching for names absent from the current check and files.\n\n"
                f"## Current approach\n\n{current.get('strategy', 'focused-fix')}\n\n"
                "## Starting source hints\n\n"
                f"{source_hints}\n\n"
                "These paths come from failure evidence and traceability. They are investigation "
                "hints, not an exhaustive list of relevant source.\n\n"
                f"## Read-only public contracts\n\n{immutable}\n\n"
                f"## Previous failed approaches\n\n{history}\n\n"
                "## Current failure\n\n```text\n"
                f"{current.get('evidence', '')}\n```\n\n"
                "After editing, rerun the relevant build or test in the terminal. If it fails, "
                "inspect the cause and continue repairing in this conversation.\n"
            )
            repair_prompt_path.write_text(repair_prompt, encoding="utf-8")
            task["repair_prompt_file"] = str(
                repair_prompt_path.relative_to(run_root)
            ).replace("\\", "/")
        else:
            task.pop("repair_prompt_file", None)
            if repair_prompt_path.is_file():
                repair_prompt_path.unlink()
        digest_material = (
            base_prompt if not repair_prompt else base_prompt + "\0" + repair_prompt
        )
        digest = hashlib.sha256(digest_material.encode("utf-8")).hexdigest()
        task["initial_prompt_sha256"] = base_digest
        task["prompt_sha256"] = digest
        sources = dict(task.get("source_artifacts", {}))
        if task_id in active_ids:
            sources["repairEvidence"] = str(plan_path)
        else:
            sources.pop("repairEvidence", None)
        task["source_artifacts"] = sources
        if task_id in task_files:
            path, definition = task_files[task_id]
            definition["prompt_sha256"] = digest
            definition["initial_prompt_sha256"] = base_digest
            definition["source_artifacts"] = sources
            if task_id in active_ids and repair_prompt:
                definition["repair_prompt_file"] = task["repair_prompt_file"]
            else:
                definition.pop("repair_prompt_file", None)
            _write_json(path, definition)
    _write_json(manifest_path, manifest)


def repair_task_ids(run_root: Path) -> set[str]:
    """현재 자동 수리를 수행할 기능 작업 ID를 반환한다."""
    plan_path = run_root / REPAIR_PLAN
    if not plan_path.is_file():
        return set()
    entries = [
        entry
        for entry in _read_json(plan_path).get("entries", [])
        if isinstance(entry, dict)
    ]
    if not entries:
        return set()
    return {str(value) for value in entries[-1].get("ownerTaskIds", [])}


def active_repair_for_task(
    run_root: Path, task_id: str
) -> dict[str, object] | None:
    """현재 작업에 배정된 최신 수리 항목을 반환한다."""
    plan_path = run_root / REPAIR_PLAN
    if not plan_path.is_file():
        return None
    entries = [
        item
        for item in _read_json(plan_path).get("entries", [])
        if isinstance(item, dict)
    ]
    if not entries or task_id not in entries[-1].get("ownerTaskIds", []):
        return None
    return entries[-1]


def referenced_source_paths(evidence: dict[str, object]) -> list[str]:
    """compiler, test와 JSON 보고서에서 application 상대 경로를 읽는다."""
    text = _evidence_text(evidence).replace("\\", "/")
    paths = re.findall(
        r"(application/(?:src|frontend|terraform)/[A-Za-z0-9_./@+-]+"
        r"\.(?:java|kt|tsx|ts|jsx|js|svelte|sql|ya?ml|json|tf))",
        text,
        flags=re.IGNORECASE,
    )
    return list(dict.fromkeys(path.rstrip(".,;:)") for path in paths))


def repair_rounds(plan: dict[str, object]) -> int:
    """한 실패가 자동 수리된 최대 횟수를 반환한다."""
    revisions = [
        int(entry.get("revision", 0))
        for entry in plan.get("entries", [])
        if isinstance(entry, dict)
    ]
    return max(revisions, default=0)


def _owner_for_paths(
    tasks: list[dict[str, object]], paths: list[str]
) -> str:
    """Return one unambiguous owner whose declared scope contains all paths."""
    if not paths:
        return ""
    owners: set[str] = set()
    for task in tasks:
        exact_paths = {
            str(path).replace("\\", "/")
            for path in task.get("allowed_write_paths", [])
        }
        roots = [
            str(path).replace("\\", "/").rstrip("/")
            for path in task.get("allowed_write_roots", [])
        ]
        if all(
            path in exact_paths
            or any(path == root or path.startswith(root + "/") for root in roots)
            for path in paths
        ):
            owner = str(task.get("owner", ""))
            if owner:
                owners.add(owner)
    return next(iter(owners)) if len(owners) == 1 else ""


def _repair_paths(
    tasks: list[dict[str, object]], owner_ids: set[str], evidence_paths: list[str]
) -> list[str]:
    """Return evidence hints already inside the owner's immutable-safe base scope.

    ``repairPaths`` is consumed by the runtime, so it must never grant a permission the
    original task did not have. ``relatedPaths`` retains the complete evidence for navigation.
    """
    owner_tasks = [
        task for task in tasks if str(task.get("task_id")) in owner_ids
    ]
    return list(
        dict.fromkeys(
            path
            for path in evidence_paths
            if any(_path_in_base_write_scope(task, path) for task in owner_tasks)
        )
    )


def _owner_digest_paths(
    tasks: list[dict[str, object]], owner_ids: set[str]
) -> list[str]:
    """Use existing owner files to notice progress when evidence names no source file."""
    return sorted(
        {
            str(path).replace("\\", "/")
            for task in tasks
            if str(task.get("task_id")) in owner_ids
            for path in task.get("allowed_write_paths", [])
            if _path_in_base_write_scope(task, str(path).replace("\\", "/"))
        }
    )


def _path_in_base_write_scope(task: dict[str, object], path: str) -> bool:
    normalized = path.replace("\\", "/").strip("/")
    immutable = {
        str(value).replace("\\", "/").strip("/")
        for value in task.get("immutable_paths", [])
    }
    if any(
        normalized == root or normalized.startswith(root + "/")
        for root in immutable
    ):
        return False
    exact = {
        str(value).replace("\\", "/").strip("/")
        for value in task.get("allowed_write_paths", [])
    }
    roots = {
        str(value).replace("\\", "/").strip("/")
        for value in task.get("allowed_write_roots", [])
    }
    return normalized in exact or any(
        normalized == root or normalized.startswith(root + "/")
        for root in roots
    )


def _source_digest(run_root: Path, paths: list[str]) -> str:
    """마지막으로 승인된 run source 중 수리 대상의 내용을 식별한다."""
    content: list[tuple[str, str | None]] = []
    for relative in sorted(set(paths)):
        path = run_root / relative
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        content.append((relative, digest))
    return hashlib.sha256(
        json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _repair_strategy(repeated_count: int) -> str:
    """같은 실패가 반복되면 이전 증거와 다른 진단 관점을 제안한다."""
    strategies = (
        "Edit the files named by the failure using the verification result",
        "Reproduce the failure, trace the call path, diagnose the cause, and then edit",
        "Reimplement the smallest failing part while preserving public contracts",
        "Reread the assigned feature and consistently rebuild only the failing part",
    )
    if repeated_count < len(strategies):
        strategy = strategies[repeated_count]
    else:
        # 네 문구를 다시 순환하면 요청만 달라 보일 뿐 실제 전략은 반복된다. 이전 실행의
        # 변경 파일과 검사 결과가 prompt에 함께 들어가므로, 이후에는 아직 시험하지 않은
        # 가설을 먼저 세우고 그 가설을 확인하는 새 접근을 선택하게 한다.
        strategy = (
            f"State new diagnostic hypothesis {repeated_count - len(strategies) + 1}, "
            "verify evidence not covered by prior changes, and then edit"
        )
    return strategy


def _recent_execution_history(run_root: Path, task_id: str) -> str:
    """최근 OpenHands 수리 결과를 다음 대화에 짧게 전달한다.

    repair plan만 보면 전략 이름은 알 수 있지만 실제로 어느 파일을 바꿨고 어떤 검사가 다시
    실패했는지는 알 수 없다. 다만 과거 compiler 출력을 그대로 반복하면 이미 바뀐 source의
    class나 test 이름을 현재 오류로 오해할 수 있다. 최신 세 시도의 결과와 대표 진단 한 줄만
    전달하고, 원문은 JSON 실행 기록에 보존한다.
    """
    result_path = (
        run_root / "reports" / "agent-executions" / f"{task_id}.result.json"
    )
    if not result_path.is_file():
        return ""
    try:
        result = _read_json(result_path)
    except (OSError, json.JSONDecodeError):
        return ""
    repair_history = result.get("repairHistory")
    if not isinstance(repair_history, dict):
        return ""
    attempts = [
        item for item in repair_history.get("attempts", []) if isinstance(item, dict)
    ][-3:]
    lines: list[str] = []
    for index, attempt in enumerate(attempts, 1):
        detail = _representative_diagnostic(str(attempt.get("detail", "")))
        lines.append(
            f"- Run {index}: strategy={attempt.get('strategy_key', 'unknown')}, "
            f"outcome={attempt.get('outcome', 'unknown')}, "
            f"candidate={str(attempt.get('candidate_digest', ''))[:12] or 'none'}, "
            f"evidence={detail or 'not recorded'}"
        )
    return "\n".join(lines)


def _representative_diagnostic(value: str, limit: int = 320) -> str:
    """긴 build 출력에서 다음 대화가 구분할 수 있는 대표 실패 한 줄만 고른다."""
    lines = [" ".join(line.split()) for line in value.splitlines() if line.strip()]
    markers = ("error:", "failed", "failure", "expected:", "violation", "missing")
    selected = next(
        (line for line in lines if any(marker in line.lower() for marker in markers)),
        lines[0] if lines else "",
    )
    return selected[:limit]


def _bounded_evidence(value: str, limit: int = 8000) -> str:
    if len(value) <= limit:
        return value
    half = limit // 2
    return value[:half] + "\n... middle of log omitted ...\n" + value[-half:]


def _first_evidence_line(value: str) -> str:
    """이전 실패 목록에는 첫 번째 읽을 수 있는 한 줄만 사용한다."""
    return next((line.strip() for line in value.splitlines() if line.strip()), "Failure recorded")


def _evidence_text(evidence: dict[str, object]) -> str:
    return "\n".join(
        str(evidence.get(key, ""))
        for key in ("command", "stderr", "stdout", "testResults")
        if evidence.get(key)
    ).strip()


def _task_files(run_root: Path) -> dict[str, tuple[Path, dict[str, object]]]:
    result: dict[str, tuple[Path, dict[str, object]]] = {}
    for path in (run_root / "reports" / "implementation-tasks").glob("*.task.json"):
        task = _read_json(path)
        if task.get("task_id"):
            result[str(task["task_id"])] = (path, task)
    return result


def _without_repair_directives(prompt: str) -> str:
    if REPAIR_PROMPT_START in prompt:
        return prompt.split(REPAIR_PROMPT_START, 1)[0].rstrip()
    legacy = "\n\n## Orchestrated repair and revalidation directives"
    if legacy in prompt:
        return prompt.split(legacy, 1)[0].rstrip()
    return prompt


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
