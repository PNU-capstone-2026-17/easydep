"""구현 검증 실패를 관련 기능 작업으로 다시 연결한다.

예외 문구를 하나씩 외워 담당자를 고르지 않는다. compiler, test와 최종 검사가 알려 준
source 경로를 우선 사용하고, 경로가 없으면 실패한 작업 또는 전체 연결을 담당하는 wiring
작업에서 계속 수리한다. 모든 이력은 남기되 숫자 상한으로 중단하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

REPAIR_SCHEMA = "implementation-repair-plan/v4"
REPAIR_PLAN = Path("reports/repair-plan.json")
REPAIR_PROMPT_HEADING = "## 자동 수리 작업"
REPAIR_PROMPT_START = "<!-- easydep:repair-directives:start -->"
REPAIR_PROMPT_END = "<!-- easydep:repair-directives:end -->"

# 현재 구현 흐름의 네 작업 종류와 피드백 수정 작업만 단계에 연결한다. 이전 구현 run은
# 지원하지 않으므로 과거 파일별 task 이름을 계속 번역하지 않는다.
TASK_PHASES = {
    "persistence": "persistence",
    "use-case": "use-cases",
    "frontend-implementation": "frontend",
    "wiring": "wiring",
    # 자연어 피드백으로 기존 application source를 고치는 현재 작업이다.
    "control": "use-cases",
}


def schedule_cross_phase_repair(
    run_root: Path,
    failed_task_id: str,
    evidence: dict[str, object],
    *,
    failed_task_type: str | None = None,
) -> dict[str, object] | None:
    """실패 경로를 편집할 수 있는 가장 작은 기능 작업을 다시 예약한다."""
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
    owner_ids = _owners_for_paths(tasks, paths)
    failed = next(
        (task for task in tasks if str(task.get("task_id")) == failed_task_id),
        None,
    )

    # 마지막 build, HTTP 흐름 또는 설계 일치 검사는 여러 기능을 한꺼번에 본다. 이 실패를
    # 개별 유스케이스 사이로 되돌리지 않고 wiring 작업 하나가 통합 수리한다.
    integration = _integration_task(tasks) if failed_task_type == "wiring" else None
    if integration is not None:
        owner_ids = {str(integration["task_id"])}
    else:
        # 일반 작업 실패는 모든 실패 파일을 다룰 수 있는 가장 작은 작업에서 수리한다.
        covering = _covering_task(tasks, paths)
        if covering is not None:
            owner_ids = {str(covering["task_id"])}
        elif failed is not None and not owner_ids:
            owner_ids = {failed_task_id}
        elif not owner_ids and failed_task_type:
            owner_ids = {
                str(task["task_id"])
                for task in tasks
                if str(task.get("task_type")) == failed_task_type
            }
    if not owner_ids:
        fallback = _integration_task(tasks)
        if fallback is not None:
            owner_ids = {str(fallback["task_id"])}
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
    now = datetime.now(UTC).isoformat()
    entry = {
        "failedTaskId": failed_task_id,
        "ownerTaskIds": sorted(owner_ids),
        "revalidationTaskIds": _later_task_ids(tasks, owner_ids),
        "outcome": "scheduled",
        "evidence": _bounded_evidence(current_text),
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
        "command": ["source-design-conformance"],
        "stderr": json.dumps(violations, ensure_ascii=False, indent=2),
    }
    return schedule_cross_phase_repair(
        run_root,
        "source-design-conformance",
        evidence,
        failed_task_type="wiring",
    )


def apply_repair_directives(run_root: Path) -> None:
    """현재 실패와 최근 실패 방법을 해당 기능 작업 prompt에 추가한다."""
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
        prompt = _without_repair_directives(original)
        relevant = [
            entry for entry in entries if task_id in entry.get("ownerTaskIds", [])
        ]
        if task_id in active_ids and relevant:
            current = relevant[-1]
            previous = relevant[:-1]
            history = "\n".join(
                f"- {entry.get('revision')}차: "
                + _first_evidence_line(str(entry.get("evidence", "")))
                for entry in previous
            ) or "- 이전 실패 없음"
            prompt += (
                f"\n\n{REPAIR_PROMPT_START}\n{REPAIR_PROMPT_HEADING}\n\n"
                "현재 source를 유지한 채 아래 기술 오류를 고친다. compiler와 test를 실행하고 "
                "성공할 때까지 같은 작업에서 계속 수정한다. 생성된 공개 계약은 바꾸지 않는다.\n\n"
                f"### 이전 시도 요약\n{history}\n\n"
                "### 현재 실패\n```text\n"
                f"{current.get('evidence', '')}\n```\n{REPAIR_PROMPT_END}\n"
            )
        if prompt == original:
            continue
        prompt_path.write_text(prompt, encoding="utf-8")
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
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
            definition["source_artifacts"] = sources
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


def referenced_source_paths(evidence: dict[str, object]) -> list[str]:
    """compiler, test와 JSON 보고서에서 application 상대 경로를 읽는다."""
    text = _evidence_text(evidence).replace("\\", "/")
    paths = re.findall(
        r"(application/(?:src|frontend|terraform)/[A-Za-z0-9_./@+-]+"
        r"\.(?:java|kt|ts|tsx|js|jsx|svelte|sql|ya?ml|json|tf))",
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


def _owners_for_paths(
    tasks: list[dict[str, object]], paths: list[str]
) -> set[str]:
    owners: set[str] = set()
    for task in tasks:
        editable = {
            str(path).replace("\\", "/")
            for path in task.get("allowed_write_paths", [])
        }
        if editable.intersection(paths):
            owners.add(str(task["task_id"]))
    return owners


def _covering_task(
    tasks: list[dict[str, object]], paths: list[str]
) -> dict[str, object] | None:
    if not paths:
        return None
    required = set(paths)
    candidates = []
    for task in tasks:
        editable = {
            str(path).replace("\\", "/")
            for path in task.get("allowed_write_paths", [])
        }
        if required.issubset(editable):
            candidates.append((len(editable), task))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _integration_task(tasks: list[dict[str, object]]) -> dict[str, object] | None:
    preferred = ("wiring", "use-case")
    for task_type in preferred:
        match = next(
            (task for task in tasks if str(task.get("task_type")) == task_type),
            None,
        )
        if match is not None:
            return match
    return tasks[-1] if tasks else None


def _later_task_ids(
    tasks: list[dict[str, object]], owner_ids: set[str]
) -> list[str]:
    indexes = [
        index
        for index, task in enumerate(tasks)
        if str(task.get("task_id")) in owner_ids
    ]
    if not indexes:
        return []
    first = min(indexes)
    return [
        str(task["task_id"])
        for task in tasks[first + 1 :]
        if str(task.get("task_id")) not in owner_ids
    ]


def _bounded_evidence(value: str, limit: int = 16000) -> str:
    if len(value) <= limit:
        return value
    half = limit // 2
    return value[:half] + "\n... 중간 로그 생략 ...\n" + value[-half:]


def _first_evidence_line(value: str) -> str:
    """이전 실패 목록에는 첫 번째 읽을 수 있는 한 줄만 사용한다."""
    return next((line.strip() for line in value.splitlines() if line.strip()), "실패 기록")


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
    return prompt.split(legacy, 1)[0].rstrip()


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
