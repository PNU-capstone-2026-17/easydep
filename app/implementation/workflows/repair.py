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

    # E2E 테스트 파일 자체의 의미 검사 실패는 그 테스트 작업 안에서 고쳐야 한다.
    # API나 Control 문제로 잘못 분류하면 이미 성공한 구현 작업을 불필요하게 다시 생성한다.
    if (
        str(failed.get("task_type")) == "integration-test"
        and evidence.get("command") == ["e2e-semantic-contract-gate"]
    ):
        return None

    evidence_text = _evidence_text(evidence)
    causal_evidence = _causal_evidence_text(evidence)
    mapped_by_failure = (
        "is 'mappedby' a property named" in causal_evidence.lower()
        or (
            "annotationexception" in causal_evidence.lower()
            and "mappedby" in causal_evidence.lower()
        )
    )
    # A compiler path normally identifies one owner. Hibernate's mappedBy
    # error is different: it describes an inconsistent pair, so prefer the
    # pair-aware inference even when its stack trace happens to include only
    # one entity source path.
    owner_ids = (
        _infer_upstream_owners(tasks, failed, causal_evidence)
        if mapped_by_failure
        else _owners_named_in_evidence(tasks, failed_task_id, causal_evidence)
    )
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
    """Re-plan source repairs while rejecting an identical failure strategy."""
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
    strategy_key = f"source-conformance:{','.join(sorted(codes))}:{','.join(sorted(owners))}"
    task_by_id = {str(task["task_id"]): task for task in tasks}
    candidate_digest = _owner_candidate_digest(run_root, task_by_id, set(owners))
    if any(
        entry.get("evidenceSha256") == evidence_sha
        and entry.get("strategyKey") == strategy_key
        and entry.get("candidateDigest") == candidate_digest
        for entry in matching_entries
    ):
        plan["status"] = "STALLED"
        plan["stallReason"] = "No untried source-conformance repair strategy remains."
        plan["updatedAt"] = datetime.now(UTC).isoformat()
        _write_json(plan_path, plan)
        return None
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
        "outcome": "scheduled",
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
        additions = [
            f"\n\n{REPAIR_PROMPT_START}\n{REPAIR_PROMPT_HEADING}\n"
        ]
        older = relevant[:-5]
        if older:
            additions.append(
                "\n### Earlier repair history (deterministically compacted)\n"
                + "\n".join(
                    "- "
                    f"revision={entry.get('revision')} "
                    f"failure={entry.get('failedTaskId')} "
                    f"strategy={entry.get('strategyKey')} "
                    f"fingerprint={entry.get('failureFingerprint')} "
                    f"outcome={entry.get('outcome')}"
                    for entry in older
                )
                + "\n"
            )
        for entry in relevant[-5:]:
            role = "repair the failure in your owned files"
            evidence = str(entry.get("evidence", ""))
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
    if (
        failed_type != "persistence-entities"
        and (
            "is 'mappedby' a property named" in lowered
            or ("annotationexception" in lowered and "mappedby" in lowered)
        )
    ):
        # Hibernate's mappedBy error names the entity that owns the missing
        # property, but the inverse entity can also need correction. Re-plan
        # every named persistence entity task; if an older Hibernate message
        # does not expose a name, repair the small persistence-entity phase as
        # a unit rather than retrying configuration or an integration test.
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
                    Path(str(path)).stem.lower() in lowered
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
    """Return whether an E2E test exercised, but observed a broken, app contract.

    Compile errors and static-contract violations stay local to the test.  This
    narrow set instead identifies JUnit/HTTP execution evidence, for which the
    test source cannot repair the responsible production collaboration.
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
