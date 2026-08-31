from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

REPAIR_SCHEMA = "implementation-repair-plan/v2"
REPAIR_PLAN = Path("reports/repair-plan.json")
REPAIR_PROMPT_HEADING = "## Orchestrated repair and revalidation directives"
REPAIR_PROMPT_START = "<!-- easydep:repair-directives:start -->"
REPAIR_PROMPT_END = "<!-- easydep:repair-directives:end -->"
TASK_PHASES = {
    "scaffold-completion": "scaffold-completion",
    "entity": "entity",
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
    "scaffold-completion": (),
    "entity": ("scaffold-completion",),
    "persistence": ("entity",),
    "control": ("persistence",),
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
    *,
    failed_task_type: str | None = None,
) -> dict[str, object] | None:
    """실패 원인을 소유한 작업과 그 뒤의 재검증 작업을 다시 계획한다.

    보통 ``failed_task_id``는 OpenHands 작업 ID다. 여러 작업을 합쳐 빌드하는 phase
    검증에서 실패했다면 별도 작업 ID가 없으므로 ``failed_task_type``을 함께 받아 같은
    원인 판별 규칙을 사용한다. 이 경우에도 compiler가 출력한 파일 경로가 우선한다.
    """
    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = _read_json(manifest_path)
    tasks = list(manifest.get("implementation_tasks", []))
    task_by_id = {str(task["task_id"]): task for task in tasks}
    failed = task_by_id.get(failed_task_id)
    if failed is None:
        if not failed_task_type:
            return None
        failed = {
            "task_id": failed_task_id,
            "task_type": failed_task_type,
            "allowed_write_paths": [],
        }

    completion_audit = evidence.get("command") == ["completion-audit"]
    e2e_semantic_gate = evidence.get("command") == ["e2e-semantic-contract-gate"]

    evidence_text = _evidence_text(evidence)
    causal_evidence = _causal_evidence_text(evidence)
    mapped_by_failure = (
        "is 'mappedby' a property named" in causal_evidence.lower()
        or (
            "annotationexception" in causal_evidence.lower()
            and "mappedby" in causal_evidence.lower()
        )
    )
    # 마지막 감사는 이미 어느 task의 산출물이 부족한지 task_id로 알려 준다. 이때는
    # 로그 문구를 다시 추측해 다른 작업으로 보내지 않고 그 task를 그대로 수리한다.
    if completion_audit:
        owner_ids = {failed_task_id}
    elif e2e_semantic_gate:
        owner_ids = {
            str(task["task_id"])
            for task in tasks
            if task.get("task_type") == "integration-test"
        }
    else:
        direct_owners = (
            _owners_named_in_evidence(tasks, failed_task_id, causal_evidence)
            | _owners_for_application_stack_frames(
                tasks, failed_task_id, causal_evidence
            )
            | _owners_for_matching_exception_messages(
                run_root, tasks, failed_task_id, causal_evidence
            )
            | _owners_for_known_test_failures(tasks, causal_evidence)
            | _owners_for_failed_test_classes(
                tasks, failed_task_id, causal_evidence
            )
        )
        inferred_owners = _infer_upstream_owners(tasks, failed, causal_evidence)
        # ``mappedBy`` 오류는 검사를 실행한 wiring 파일이 아니라 연관관계를 선언한
        # 영속성 엔티티가 고쳐야 한다. 검사를 소유했다는 이유만으로 wiring 작업까지
        # 다시 생성하면 LLM 호출만 늘어나므로, 이 오류는 추론한 원인 작업만 선택한다.
        upstream_persistence_failure = mapped_by_failure or any(
            marker in causal_evidence.lower()
            for marker in (
                "jdbctyperecommendationexception",
                "schemamanagementexception",
                "jdbcsqlsyntaxerror",
            )
        )
        # 같은 Gradle 결과에 별개의 API·Control·테스트 오류가 함께 있으면 그 담당도
        # 잃지 않는다. 아래 표식이 없고 영속성 시작 오류만 있을 때에만 관찰자 작업을 뺀다.
        has_independent_failure = any(
            marker in causal_evidence.lower()
            for marker in (
                "notamockexception",
                "invaliddefinitionexception",
                "no serializer found",
                "dataintegrityviolationexception",
                "assertionfailederror",
                "cannot find symbol",
                "no suitable constructor",
                "nosuchbeandefinitionexception",
            )
        )
        if upstream_persistence_failure and not has_independent_failure and inferred_owners:
            owner_ids = inferred_owners
        else:
            # 그 밖의 복합 실행 오류는 실패한 테스트 파일과 실제 원인 파일을 함께
            # 고쳐야 할 수 있다. 예를 들어 Bean 등록 오류는 wiring 자체가 원인이다.
            multi_owner_runtime_failure = any(
                marker in causal_evidence.lower()
                for marker in (
                    "jdbctyperecommendationexception",
                    "schemamanagementexception",
                    "dataintegrityviolationexception",
                    "jdbcsqlintegrityconstraintviolationexception",
                    "jdbcsqlsyntaxerror",
                    "nosuchbeandefinitionexception",
                )
            )
            owner_ids = (
                direct_owners | inferred_owners
                if multi_owner_runtime_failure
                else direct_owners or inferred_owners
            )
    if not owner_ids:
        # 구조화된 검증 로그에 파일 경로나 알려진 오류 이름이 없어도 기술 오류를 사용자
        # 클릭으로 넘기지 않는다. 실제 작업이면 그 작업을, 통합 검증이면 같은 종류의
        # 가장 좁은 작업을 다시 실행해 새 대화에서 전체 진단을 살펴보게 한다.
        if failed_task_id in task_by_id:
            owner_ids = {failed_task_id}
        else:
            owner_ids = {
                str(task["task_id"])
                for task in tasks
                if task.get("task_type") == failed_task_type
            }
    if not owner_ids:
        return None

    plan_path = run_root / REPAIR_PLAN
    plan = _read_json(plan_path) if plan_path.is_file() else {
        "schemaVersion": REPAIR_SCHEMA,
        "entries": [],
    }
    evidence_sha = hashlib.sha256(evidence_text.encode("utf-8")).hexdigest()
    matching_entries = _entries_for_failure(plan, failed_task_id)
    owner_phases = {_phase(task_by_id[task_id]) for task_id in owner_ids}
    revalidation_ids = sorted(
        str(task["task_id"])
        for task in tasks
        if str(task["task_id"]) not in owner_ids
        and any(_depends_on(_phase(task), phase) for phase in owner_phases)
    )
    failure_fingerprint = _failure_fingerprint(causal_evidence)
    strategy_key = f"cross-phase:{','.join(sorted(owner_ids))}"
    candidate_digest = _owner_candidate_digest(run_root, task_by_id, owner_ids)
    repeated_candidate = any(
        entry.get("failureFingerprint") == failure_fingerprint
        and entry.get("strategyKey") == strategy_key
        and entry.get("candidateDigest") == candidate_digest
        for entry in matching_entries
    )
    revision = len(matching_entries) + 1
    entry = {
        "failedTaskId": failed_task_id,
        "repairChainId": _repair_chain_id(failed_task_id),
        "ownerTaskIds": sorted(owner_ids),
        "revalidationTaskIds": revalidation_ids,
        "evidenceSha256": evidence_sha,
        "failureFingerprint": failure_fingerprint,
        "strategyKey": strategy_key,
        "candidateDigest": candidate_digest,
        "inputDigest": evidence_sha,
        # 같은 코드와 진단이 다시 나와도 숫자 상한으로 멈추지 않는다. 이전 revision이
        # 다음 prompt에 함께 들어가므로 모델은 실패 이력을 보고 다른 수정안을 시도한다.
        "outcome": "scheduled_after_no_change" if repeated_candidate else "scheduled",
        "evidence": causal_evidence[-8000:],
        "revision": revision,
        "createdAt": min(
            (
                str(item.get("createdAt"))
                for item in matching_entries
                if item.get("createdAt")
            ),
            default=datetime.now(UTC).isoformat(),
        ),
        "updatedAt": datetime.now(UTC).isoformat(),
    }
    plan["entries"].append(entry)
    plan["status"] = "ACTIVE"
    plan.pop("stallReason", None)
    plan["updatedAt"] = entry["updatedAt"]
    _write_json(plan_path, plan)
    return entry


def schedule_source_conformance_repair(
    run_root: Path, report: dict[str, object]
) -> dict[str, object] | None:
    """설계와 맞지 않는 구현을 담당 작업에 다시 맡긴다.

    같은 결과가 반복되어도 숫자나 후보 hash만으로 중단하지 않는다. 이전 실패와 현재
    파일을 다음 prompt에 함께 전달하므로 에이전트가 다른 수정 방법을 선택할 수 있다.
    """
    violations = report.get("violations", [])
    codes = {
        str(item.get("code"))
        for item in violations
        if isinstance(item, dict)
    }
    erd_codes = {"ERD_ENTITY_NOT_IMPLEMENTED", "ERD_RELATION_NOT_IMPLEMENTED"}
    if not (codes & erd_codes):
        return None
    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = _read_json(manifest_path)
    tasks = list(manifest.get("implementation_tasks", []))
    owners: set[str] = set()
    for violation in violations:
        if not isinstance(violation, dict):
            continue
        path = str(violation.get("path", "")).replace("\\", "/")
        if path:
            owners.update(
                str(task["task_id"])
                for task in tasks
                if path in {
                    str(candidate).replace("\\", "/")
                    for candidate in task.get("allowed_write_paths", [])
                }
            )
        code = str(violation.get("code", ""))
        message = str(violation.get("message", "")).lower()
        if code == "ERD_RELATION_NOT_IMPLEMENTED":
            owners.update(
                str(task["task_id"])
                for task in tasks
                if task.get("task_type") == "persistence-entities"
            )
        if "column" in message:
            owners.update(
                str(task["task_id"])
                for task in tasks
                if task.get("task_type") == "persistence-schema"
            )
    owner_types: set[str] = set()
    if not owners and codes & erd_codes:
        owner_types.update({
            "persistence-entities", "persistence-repositories",
            "persistence-mapping", "persistence-schema", "gateway-adapter",
        })
        owners.update(
            str(task["task_id"])
            for task in tasks
            if task.get("task_type") in owner_types
        )
    if not owners:
        return None
    evidence = json.dumps(violations, ensure_ascii=False, indent=2)
    plan_path = run_root / REPAIR_PLAN
    plan = _read_json(plan_path) if plan_path.is_file() else {"schemaVersion": REPAIR_SCHEMA, "entries": []}
    evidence_sha = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
    matching_entries = _entries_for_failure(plan, "source-design-conformance")
    strategy_key = f"source-conformance:{','.join(sorted(codes))}:{','.join(sorted(owners))}"
    task_by_id = {str(task["task_id"]): task for task in tasks}
    candidate_digest = _owner_candidate_digest(run_root, task_by_id, owners)
    repeated_candidate = any(
        entry.get("evidenceSha256") == evidence_sha
        and entry.get("strategyKey") == strategy_key
        and entry.get("candidateDigest") == candidate_digest
        for entry in matching_entries
    )
    revision = len(matching_entries) + 1
    entry = {
        "failedTaskId": "source-design-conformance",
        "repairChainId": _repair_chain_id("source-design-conformance"),
        "ownerTaskIds": sorted(owners),
        "revalidationTaskIds": [str(task["task_id"]) for task in tasks if task.get("task_type") == "integration-test"],
        "evidenceSha256": evidence_sha,
        "failureFingerprint": _failure_fingerprint(evidence),
        "strategyKey": strategy_key,
        "candidateDigest": candidate_digest,
        "inputDigest": evidence_sha,
        "outcome": "scheduled_after_no_change" if repeated_candidate else "scheduled",
        "evidence": evidence,
        "revision": revision,
        "createdAt": min(
            (
                str(item.get("createdAt"))
                for item in matching_entries
                if item.get("createdAt")
            ),
            default=datetime.now(UTC).isoformat(),
        ),
        "updatedAt": datetime.now(UTC).isoformat(),
    }
    plan["entries"].append(entry)
    plan["status"] = "ACTIVE"
    plan.pop("stallReason", None)
    plan["updatedAt"] = entry["updatedAt"]
    _write_json(plan_path, plan)
    return entry


def apply_repair_directives(run_root: Path) -> None:
    """현재 수리 지시와 과거 실패 이력을 담당 작업의 prompt에 반영한다."""
    plan_path = run_root / REPAIR_PLAN
    if not plan_path.is_file():
        return
    plan = _read_json(plan_path)
    entries = [item for item in plan.get("entries", []) if isinstance(item, dict)]
    if not entries:
        return
    active_entry = entries[-1]
    active_owner_ids = {
        str(task_id) for task_id in active_entry.get("ownerTaskIds", [])
    }
    state_path = run_root / "reports" / "workflow-state.json"
    previous_state = _read_json(state_path) if state_path.is_file() else {}
    unfinished_task_ids = {
        str(task.get("taskId"))
        for task in previous_state.get("tasks", [])
        if isinstance(task, dict) and task.get("status") != "SUCCEEDED"
    }

    manifest_path = run_root / "reports" / "run-manifest.json"
    manifest = _read_json(manifest_path)
    task_files = {}
    task_dir = run_root / "reports" / "implementation-tasks"
    for path in task_dir.glob("*.task.json"):
        task = _read_json(path)
        task_files[str(task.get("task_id"))] = (path, task)

    for task in manifest.get("implementation_tasks", []):
        task_id = str(task.get("task_id"))
        # 실패한 파일을 소유한 작업만 다시 LLM에 맡긴다. 뒤 단계는 성공한 checkpoint를
        # 그대로 두고 phase/final 검증으로 확인한다. 실제로 깨졌다면 그 검증 결과가 정확한
        # 소유 작업을 새로 지정하므로, 성공한 API·프론트엔드 작업을 미리 재생성할 필요가 없다.
        relevant = [
            entry
            for entry in entries
            if task_id in entry.get("ownerTaskIds", [])
        ]
        prompt_path = run_root / str(task["prompt_file"])
        original_prompt = prompt_path.read_text(encoding="utf-8")
        prompt = _without_repair_directives(original_prompt)
        additions: list[str] = []
        has_active_repair = bool(relevant) and (
            task_id in active_owner_ids or task_id in unfinished_task_ids
        )
        if has_active_repair:
            task_active_entry = relevant[-1]
            additions.append(
                f"\n\n{REPAIR_PROMPT_START}\n{REPAIR_PROMPT_HEADING}\n"
            )
            previous = relevant[:-1]
            compacted = previous[:-4]
            recent_history = previous[-4:]
            if compacted:
                additions.append(
                    "\n### Earlier repair history (context only, compacted)\n"
                    + "\n".join(
                        "- "
                        f"revision={entry.get('revision')} "
                        f"failure={entry.get('failedTaskId')} "
                        f"strategy={entry.get('strategyKey')} "
                        f"fingerprint={entry.get('failureFingerprint')} "
                        f"outcome={entry.get('outcome')}"
                        for entry in compacted
                    )
                    + "\n"
                )
            if recent_history:
                additions.append(
                    "\n### Recent repair history (context only)\n"
                    "These are previous failures, not current instructions. Use them only "
                    "to avoid repeating an unsuccessful change.\n"
                )
            for entry in recent_history:
                evidence = str(entry.get("evidence", ""))
                if len(evidence) > 1200:
                    evidence = (
                        evidence[:600]
                        + "\n... [history shortened] ...\n"
                        + evidence[-600:]
                    )
                additions.append(
                    f"\n### Previous revision {entry['revision']} from "
                    f"`{entry['failedTaskId']}`\n"
                    f"outcome={entry.get('outcome')} "
                    f"fingerprint={entry.get('failureFingerprint')}\n"
                    f"```text\n{evidence}\n```\n"
                )
            current_evidence = str(task_active_entry.get("evidence", ""))
            additions.append(
                f"\n### Current revision {task_active_entry['revision']} from "
                f"`{task_active_entry['failedTaskId']}`\n"
                "Repair this current failure in your owned files. Preserve the exact "
                "generated contracts and keep every change inside this task's existing "
                f"allowlist.\n\n```text\n{current_evidence}\n```\n"
            )
            additions.append(f"\n{REPAIR_PROMPT_END}\n")
            prompt += "".join(additions)
        if prompt == original_prompt:
            continue
        prompt_path.write_text(prompt, encoding="utf-8")
        prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        task["prompt_sha256"] = prompt_sha
        sources = dict(task.get("source_artifacts", {}))
        if has_active_repair:
            sources["repairEvidence"] = str(plan_path)
        else:
            sources.pop("repairEvidence", None)
        task["source_artifacts"] = sources
        task_file = task_files.get(task_id)
        if task_file:
            path, definition = task_file
            definition["prompt_sha256"] = prompt_sha
            definition["source_artifacts"] = sources
            _write_json(path, definition)
    _write_json(manifest_path, manifest)


def repair_task_ids(run_root: Path) -> set[str]:
    """실패 원인을 소유하여 LLM 수리가 필요한 작업 ID만 반환한다.

    뒤 단계의 성공한 checkpoint는 다시 생성하지 않는다. 전체 phase/final 검증이 실제
    호환성을 확인하고, 문제가 남아 있을 때에만 그 파일의 소유 작업을 별도로 수리한다.
    """
    plan_path = run_root / REPAIR_PLAN
    if not plan_path.is_file():
        return set()
    plan = _read_json(plan_path)
    entries = [entry for entry in plan.get("entries", []) if isinstance(entry, dict)]
    if not entries:
        return set()
    return {str(task_id) for task_id in entries[-1].get("ownerTaskIds", [])}


def referenced_source_paths(evidence: dict[str, object]) -> list[str]:
    # Gradle/Javac의 ``warning:``·``Note:``는 이번 실패와 무관한 다른 test 파일을
    # 함께 출력할 수 있다. 그 경로 때문에 현재 작업이 자기 파일을 수리하지 못했다고
    # 오판하지 않도록 실제 오류와 test failure 줄만 사용한다.
    text = "\n".join(
        line
        for line in _evidence_text(evidence).splitlines()
        if not re.match(r"\s*(?:warning:|note:)", line, re.IGNORECASE)
    ).replace("\\", "/")
    matches = re.findall(r"(?:[A-Za-z]:)?[^\r\n:]*?(application/(?:src|build)/[^\r\n:]+?\.(?:java|sql|yml))(?=:\d|:|\s|$)", text)
    return sorted({match.strip().lstrip("/") for match in matches})


def repair_requires_owner_handoff(
    evidence: dict[str, object],
    allowed_paths: list[str],
    changed_paths: set[str],
) -> bool:
    """오류가 현재 작업의 수정 범위 밖 파일에만 있는지 판단한다.

    각 구현 작업은 자신에게 배정된 파일만 고칠 수 있다. 예를 들어 Control에서
    잘못된 Boundary 의존성을 제거한 뒤 wiring의 생성자 호출이 깨졌다면, Control에
    호환용 생성자를 억지로 추가하게 하지 않고 wiring 작업으로 넘겨야 한다. 다만 현재
    작업이 바꾼 Java 타입의 생성자나 메서드가 호출 파일에서 깨졌다면, 오류 줄이 호출
    파일에 있어도 현재 타입이 호환성을 복구해야 한다.
    """

    referenced = {
        path.replace("\\", "/").lower()
        for path in referenced_source_paths(evidence)
    }
    if not referenced:
        return False
    allowed = {path.replace("\\", "/").lower() for path in allowed_paths}
    if referenced & allowed:
        return False
    output = _evidence_text(evidence)
    if changed_paths and re.search(
        r"cannot find symbol|no suitable constructor|constructor .* cannot be applied",
        output,
        re.IGNORECASE,
    ):
        changed_types = {
            Path(path).stem
            for path in changed_paths
            if path.replace("\\", "/").endswith(".java")
        }
        if any(
            re.search(rf"\b{re.escape(type_name)}\b", output)
            for type_name in changed_types
        ):
            return False
    return True


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


def _owners_for_matching_exception_messages(
    run_root: Path,
    tasks: list[dict[str, object]],
    failed_task_id: str,
    evidence: str,
) -> set[str]:
    """생략된 stack frame 대신 실제 예외 문자열로 오류 파일을 찾는다.

    MockMvc가 감싼 예외는 때때로 Spring 프레임만 남기고, 예외를 던진 애플리케이션
    프레임을 ``... more``로 줄인다. 예외 메시지가 생성 소스의 문자열과 정확히 같다면
    그 파일의 담당 작업을 수리 대상으로 삼을 수 있다. 특정 도메인 문구를 규칙에
    넣지 않고 현재 실행의 실제 소스만 대조한다.
    """

    messages = {
        match.strip()
        for match in re.findall(
            r"(?:[A-Za-z_$][\w.$]*(?:Exception|Error)):\s*([^\r\n]+)",
            evidence,
        )
        if len(match.strip()) >= 8
    }
    if not messages:
        return set()

    owners: set[str] = set()
    for task in tasks:
        task_id = str(task.get("task_id"))
        if task_id == failed_task_id:
            continue
        for output in task.get("allowed_write_paths", []):
            relative = str(output).replace("\\", "/")
            if not relative.endswith(".java"):
                continue
            path = run_root / relative.removeprefix("application/")
            if not path.is_file():
                path = run_root / relative
            if not path.is_file():
                continue
            source = path.read_text(encoding="utf-8")
            if any(message in source for message in messages):
                owners.add(task_id)
                break
    return owners


def _owners_for_application_stack_frames(
    tasks: list[dict[str, object]],
    failed_task_id: str,
    evidence: str,
) -> set[str]:
    """Java stack frame의 전체 클래스 이름을 구현 파일 담당 작업과 연결한다."""

    source_suffixes = {
        qualified.rsplit(".", 1)[0].replace(".", "/") + ".java"
        for qualified in re.findall(
            r"\bat (?:app//)?((?!org\.|java\.|jdk\.|worker\.)"
            r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)",
            evidence,
        )
    }
    if not source_suffixes:
        return set()

    owners: set[str] = set()
    for task in tasks:
        task_id = str(task.get("task_id"))
        if task_id == failed_task_id:
            continue
        outputs = {
            str(path).replace("\\", "/")
            for path in task.get("allowed_write_paths", [])
        }
        if any(
            output.endswith(suffix)
            for output in outputs
            for suffix in source_suffixes
        ):
            owners.add(task_id)
    return owners


def _owners_for_known_test_failures(
    tasks: list[dict[str, object]], evidence: str
) -> set[str]:
    """여러 E2E 실패가 한 번에 나온 경우 각각의 담당 작업을 찾는다.

    Gradle은 한 번의 실행에서 DB 오류와 잘못 작성된 테스트를 함께 보고할 수 있다.
    첫 번째 파일 경로 하나만 따르면 나머지 오류가 다음 실행까지 그대로 남으므로,
    원인이 분명한 두 종류는 같은 수리 계획에 함께 넣는다.
    """

    lowered = evidence.lower()
    owners: set[str] = set()
    if "notamockexception" in lowered or "argument passed to when() is not a mock" in lowered:
        integration_tasks = [
            task for task in tasks if task.get("task_type") == "integration-test"
        ]
        named = {
            str(task["task_id"])
            for task in integration_tasks
            if any(
                Path(str(path)).stem.lower() in lowered
                for path in task.get("allowed_write_paths", [])
            )
        }
        owners.update(named or {str(task["task_id"]) for task in integration_tasks})

    if any(
        marker in lowered
        for marker in (
            "dataintegrityviolationexception",
            "jdbcsqlintegrityconstraintviolationexception",
            "null not allowed for column",
        )
    ):
        production_types = {
            "control",
            "persistence-entities",
            "persistence-mapping",
            "persistence-schema",
        }
        candidates = [
            task for task in tasks if task.get("task_type") in production_types
        ]
        named = {
            str(task["task_id"])
            for task in candidates
            if any(
                Path(str(path)).stem.lower() in lowered
                for path in task.get("allowed_write_paths", [])
            )
        }
        owners.update(named or {str(task["task_id"]) for task in candidates})
    return owners


def _owners_for_failed_test_classes(
    tasks: list[dict[str, object]], failed_task_id: str, evidence: str
) -> set[str]:
    """실패 보고서의 테스트 클래스 이름을 그 테스트를 만든 작업과 연결한다.

    Gradle XML 요약에는 전체 파일 경로가 빠지고 ``SomeControllerTest.method()``만 남을
    수 있다. API·Control·영속성 작업은 자신이 만든 단위 테스트와 생산 코드를 함께
    고칠 수 있으므로, 도메인 이름이나 예외 문구 대신 테스트 파일 이름으로 찾는다.
    통합 테스트 전용 작업은 상위 생산 코드 오류를 직접 고칠 수 없으므로 제외한다.
    """
    test_classes = {
        name.rsplit(".", 1)[-1]
        for name in re.findall(
            r"(?m)^([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*Test)\.[A-Za-z_$][\w$]*\(",
            evidence,
        )
    }
    if not test_classes:
        return set()
    return {
        str(task["task_id"])
        for task in tasks
        if str(task.get("task_id")) != failed_task_id
        and task.get("task_type") != "integration-test"
        and any(
            Path(str(path)).stem in test_classes
            for path in task.get("allowed_write_paths", [])
        )
    }


def _infer_upstream_owners(
    tasks: list[dict[str, object]], failed: dict[str, object], evidence: str
) -> set[str]:
    failed_type = str(failed.get("task_type", ""))
    lowered = evidence.lower()
    if failed_type != "persistence-entities" and (
        "jdbctyperecommendationexception" in lowered
        or "could not determine recommended jdbctype" in lowered
    ):
        # 테스트나 wiring은 Hibernate를 시작했을 뿐이다. 지원하지 않는 값 객체 필드는
        # 실제 JPA 엔티티가 소유하므로, 오류를 발견한 뒤 단계가 아니라 엔티티로 보낸다.
        return {
            str(task["task_id"])
            for task in tasks
            if task.get("task_type") == "persistence-entities"
        }
    if failed_type not in {"persistence-entities", "persistence-schema"} and (
        "schemamanagementexception" in lowered
        and "schema-validation:" in lowered
    ):
        return {
            str(task["task_id"])
            for task in tasks
            if task.get("task_type") in {"persistence-entities", "persistence-schema"}
        }
    if (
        failed_type != "persistence-entities"
        and (
            "is 'mappedby' a property named" in lowered
            or ("annotationexception" in lowered and "mappedby" in lowered)
        )
    ):
        # Hibernate의 mappedBy 오류에는 빠진 속성을 가진 엔티티 이름이 나온다. 양쪽
        # 엔티티 선언을 함께 맞춰야 할 수 있으므로, 로그에 나온 영속성 엔티티 작업을
        # 고른다. 이름이 없는 구형 메시지는 작은 엔티티 단계 전체에 맡긴다.
        mentioned = {
            name.lower()
            for name in re.findall(r"\b([A-Za-z_]\w*)Entity\b", evidence)
        }
        entity_tasks = [
            task for task in tasks
            if task.get("task_type") == "persistence-entities"
        ]
        named = {
            str(task["task_id"])
            for task in entity_tasks
            if any(
                Path(str(path)).stem.removesuffix("Entity").lower() in mentioned
                for path in task.get("allowed_write_paths", [])
            )
        }
        return named or {str(task["task_id"]) for task in entity_tasks}
    if failed_type == "configuration" and (
        "schema-validation: wrong column type" in lowered
        or ("schemamanagementexception" in lowered and "wrong column type" in lowered)
    ):
        # The wiring task merely exposes the mismatch while the JPA entity,
        # mapper, and Flyway migration jointly own its repair.
        return {
            str(task["task_id"])
            for task in tasks
            if task.get("task_type")
            in {"persistence-entities", "persistence-mapping", "persistence-schema"}
        }
    if failed_type == "configuration" and re.search(r"repository|jpa|entitymanager|bean", lowered):
        return {
            str(task["task_id"]) for task in tasks
            if task.get("task_type") == "persistence-repositories"
        }
    if failed_type == "integration-test":
        if (
            "nosuchbeandefinitionexception" in lowered
            or "no qualifying bean of type" in lowered
            or "expected at least 1 bean which qualifies" in lowered
            or "qualifies as autowire candidate" in lowered
        ):
            # The E2E test can observe a missing repository bean but cannot
            # repair it: its write allowlist contains only the test class.
            # Re-plan the repository and wiring owners directly instead of
            # spending local LLM repair rounds on an unrelated test rewrite.
            return {
                str(task["task_id"])
                for task in tasks
                if task.get("task_type") in {
                    "persistence-repositories", "configuration"
                }
            }
        if (
            "jdbcsqlsyntaxerror" in lowered
            or "syntax error in sql statement" in lowered
            or "reserved keyword" in lowered
            or "expected \"identifier\"" in lowered
        ):
            # SQL/H2 failures are owned by the persistence generators, not by
            # the end-to-end test that happened to execute the query. Repair
            # the entity mapping and migration together so their column names
            # remain consistent (for example, ``year`` -> ``academic_year``).
            return {
                str(task["task_id"])
                for task in tasks
                if task.get("task_type")
                in {"persistence-entities", "persistence-mapping", "persistence-schema"}
            }
        if _is_e2e_runtime_contract_failure(lowered):
            # 실제 stack trace에 나온 클래스가 있으면 그 파일의 작업만 고친다. 아무 이름도
            # 없을 때에만 HTTP 동작에 참여하는 작은 production 묶음을 후보로 사용한다.
            runtime_types = {
                "control", "api-adapter", "boundary-adapter", "configuration"
            }
            named = {
                str(task["task_id"])
                for task in tasks
                if task.get("task_type") in runtime_types
                and any(
                    re.search(
                        rf"\b{re.escape(Path(str(path)).stem)}\b",
                        evidence,
                        re.IGNORECASE,
                    )
                    for path in task.get("allowed_write_paths", [])
                )
            }
            if named:
                return named
            return {
                str(task["task_id"])
                for task in tasks
                if task.get("task_type") in runtime_types
            }
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


def _is_e2e_runtime_contract_failure(evidence: str) -> bool:
    """E2E가 실행 중 애플리케이션 계약 오류를 발견했는지 판단한다.

    compile 오류와 정적 계약 누락은 테스트 작업 안에서 고친다. 반면 실제 JUnit·HTTP 실행에서
    드러난 오류는 테스트 소스가 생산 코드의 연결을 고칠 수 없으므로 해당 생산 작업으로 보낸다.
    """
    return any(
        marker in evidence
        for marker in (
            "assertionfailederror",
            "expected: <",
            "but was: <",
            "expected http ",
            "data integrity violation",
            "dataintegrityviolationexception",
            "stackoverflowerror",
            "requested bean is currently in creation",
            "circular reference",
            "circular dependency",
        )
    )


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


def _owner_candidate_digest(
    run_root: Path,
    task_by_id: dict[str, dict[str, object]],
    owner_ids: set[str],
) -> str:
    """담당 작업이 현재 소유한 파일 내용을 한 값으로 요약한다.

    오류 문구가 같더라도 수리 뒤 소스가 달라졌다면 새로운 후보이므로 다시 시도할 수 있다.
    파일이 아직 없다는 사실도 후보의 일부로 넣어, 아무것도 바꾸지 않은 반복만 정확히
    알아낸다.
    """

    files: list[dict[str, str]] = []
    for task_id in sorted(owner_ids):
        task = task_by_id.get(task_id, {})
        for relative in sorted(str(path) for path in task.get("allowed_write_paths", [])):
            path = run_root / relative
            digest = (
                hashlib.sha256(path.read_bytes()).hexdigest()
                if path.is_file()
                else "missing"
            )
            files.append({"taskId": task_id, "path": relative, "sha256": digest})
    return hashlib.sha256(
        json.dumps(files, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


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
    """Return the latest revision in the current v2 append-only history."""
    return max((int(item.get("revision", 0)) for item in entries), default=0)


def repair_rounds(plan: dict[str, object]) -> int:
    """Return the largest append-only repair revision."""
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


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
