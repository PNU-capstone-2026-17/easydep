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
REPAIR_PROMPT_DIR = Path("reports/implementation-tasks")
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

    # 최종 검증에서 발견됐다는 이유만으로 wiring이 모든 업무 코드를 소유하지 않는다.
    # 경로가 있으면 먼저 그 파일을 원래 만들었던 기능 작업으로 돌려보낸다. 서로 다른
    # 작업의 파일이 함께 실패했을 때에만 wiring이 그 파일 목록만 통합해서 고친다.
    covering = _covering_task(tasks, paths)
    if covering is not None:
        owner_ids = {str(covering["task_id"])}
    elif len(owner_ids) > 1:
        integration = _integration_task(tasks)
        owner_ids = {str(integration["task_id"])} if integration else owner_ids
    elif failed is not None and not owner_ids and not paths:
        owner_ids = {failed_task_id}
    elif not owner_ids and failed_task_type and not paths:
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
    repair_paths = _repair_paths(tasks, owner_ids, paths)
    source_digest = _source_digest(run_root, repair_paths)
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
        "ownerTaskIds": sorted(owner_ids),
        "revalidationTaskIds": _later_task_ids(tasks, owner_ids),
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
                f"- {entry.get('strategy', '기존 방식')}: "
                + _first_evidence_line(str(entry.get("evidence", "")))
                for entry in previous
            ) or "- 이전 실패 없음"
            execution_history = _recent_execution_history(run_root, task_id)
            history = plan_history
            if execution_history:
                history += "\n\n### 실제 변경과 검사 결과\n\n" + execution_history
            editable = "\n".join(
                f"- `{path}`" for path in current.get("repairPaths", [])
            ) or "- 작업 정의에 있는 기존 편집 파일"
            immutable = "\n".join(
                f"- `{path}`" for path in task.get("immutable_paths", [])
            ) or "- 없음"
            repair_prompt = (
                f"# {REPAIR_PROMPT_HEADING.removeprefix('## ')}\n\n"
                "아래 기술 오류를 해결한다. 맡은 파일 안에서는 구현 방법, 테스트 추가와 "
                "수정 순서를 스스로 결정해도 된다. 관련 없는 기능과 생성된 공개 계약은 "
                "바꾸지 않는다. 필요한 source는 파일 편집기로 직접 읽는다.\n\n"
                f"## 이번 접근 방법\n\n{current.get('strategy', 'focused-fix')}\n\n"
                f"## 수정 가능한 파일\n\n{editable}\n\n"
                f"## 읽기 전용 공개 계약\n\n{immutable}\n\n"
                f"## 최근 실패 방법\n\n{history}\n\n"
                "## 현재 실패\n\n```text\n"
                f"{current.get('evidence', '')}\n```\n\n"
                "수정을 마치면 `run_task_check`를 실행하고, 실패하면 같은 대화 안에서 "
                "원인을 읽어 계속 고친다. 같은 source와 같은 오류가 다시 나오면 EasyDep이 "
                "성공 source에서 새 대화를 시작한다.\n"
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


def _repair_paths(
    tasks: list[dict[str, object]], owner_ids: set[str], evidence_paths: list[str]
) -> list[str]:
    """작업 내부 자율성을 유지하되 통합 수리는 실제 오류 파일로만 좁힌다."""
    owner_tasks = [
        task for task in tasks if str(task.get("task_id")) in owner_ids
    ]
    if len(owner_tasks) == 1:
        task = owner_tasks[0]
        task_paths = [
            str(path).replace("\\", "/")
            for path in task.get("allowed_write_paths", [])
        ]
        # 하나의 기능 작업 안에서는 test에서 드러난 원인을 Service나 Entity에서 고칠 수
        # 있어야 한다. wiring이 여러 기능을 대신 고칠 때만 실제 오류 파일로 제한한다.
        if str(task.get("task_type")) == "wiring" and evidence_paths:
            outside = [path for path in evidence_paths if path not in task_paths]
            if outside:
                return list(dict.fromkeys(evidence_paths))
        return task_paths
    return list(dict.fromkeys(evidence_paths))


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
    """같은 기준 source에서도 바로 전과 다른 방식으로 새 대화를 시작한다."""
    strategies = (
        "오류가 가리킨 파일과 검사 결과부터 직접 수정",
        "검사를 먼저 재현하고 호출 흐름을 따라 원인을 진단한 뒤 수정",
        "공개 계약을 유지하는 가장 작은 변경으로 다시 구현",
        "담당 기능 내부 구현을 다시 읽고 실패한 부분만 일관되게 재구성",
    )
    if repeated_count < len(strategies):
        strategy = strategies[repeated_count]
    else:
        # 네 문구를 다시 순환하면 요청만 달라 보일 뿐 실제 전략은 반복된다. 이전 실행의
        # 변경 파일과 검사 결과가 prompt에 함께 들어가므로, 이후에는 아직 시험하지 않은
        # 가설을 먼저 세우고 그 가설을 확인하는 새 접근을 선택하게 한다.
        strategy = (
            f"새 진단 가설 {repeated_count - len(strategies) + 1}을 먼저 제시하고, "
            "기록된 이전 변경과 겹치지 않는 근거를 확인한 뒤 수정"
        )
    return f"{strategy} (새 대화 {repeated_count + 1})"


def _recent_execution_history(run_root: Path, task_id: str) -> str:
    """최근 OpenHands 수리 결과를 다음 대화에 짧게 전달한다.

    repair plan만 보면 전략 이름은 알 수 있지만 실제로 어느 파일을 바꿨고 어떤 검사가 다시
    실패했는지는 알 수 없다. 최신 실행 결과의 기존 ``repairHistory``에서 마지막 다섯
    시도만 재사용하므로 별도 저장 형식이나 무한히 커지는 prompt는 만들지 않는다.
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
    ][-5:]
    lines: list[str] = []
    for index, attempt in enumerate(attempts, 1):
        detail = " ".join(str(attempt.get("detail", "")).split())[:1200]
        lines.append(
            f"- 실행 {index}: 전략={attempt.get('strategy_key', '알 수 없음')}, "
            f"결과={attempt.get('outcome', '알 수 없음')}, "
            f"후보={str(attempt.get('candidate_digest', ''))[:12] or '없음'}, "
            f"근거={detail or '기록 없음'}"
        )
    return "\n".join(lines)


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
    if legacy in prompt:
        return prompt.split(legacy, 1)[0].rstrip()
    return prompt


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
