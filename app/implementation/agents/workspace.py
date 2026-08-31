from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from ..domain.implementation_ir import remove_readonly


def missing_required_outputs(sandbox: Path, relative_paths: list[str]) -> list[str]:
    """Return contracted task outputs that the agent has not created as files."""
    return [relative for relative in relative_paths if not (sandbox / relative).is_file()]


def load_task(run_root: Path, task_id: str) -> dict[str, object]:
    task_dir = run_root / "reports" / "implementation-tasks"
    for candidate in task_dir.glob("*.task.json"):
        task = json.loads(candidate.read_text(encoding="utf-8"))
        if task["task_id"] == task_id:
            return task
    raise ValueError(f"Unknown task: {task_id}")


def task_base_package(task: dict[str, object]) -> str:
    package_markers = {
        "application", "persistence", "adapter", "integration", "config", "bce", "api"
    }
    for output in task["allowed_write_paths"]:
        relative = Path(str(output))
        parts = relative.parts
        if "java" not in parts:
            continue
        java_index = parts.index("java")
        marker_index = next(
            (
                index for index in range(java_index + 1, len(parts))
                if parts[index] in package_markers
            ),
            None,
        )
        if marker_index is not None and marker_index > java_index + 1:
            return ".".join(parts[java_index + 1 : marker_index])
    raise ValueError("Cannot derive base package from task outputs")


def read_persistence_entity_contracts(run_root: Path, base_package: str) -> str:
    root = (
        run_root
        / "application"
        / "src"
        / "main"
        / "java"
        / Path(base_package.replace(".", "/"))
        / "persistence"
        / "entity"
    )
    contracts: list[str] = []
    for path in sorted(root.glob("*Entity.java")):
        contracts.append(
            f"// persistence/entity/{path.name}\n"
            + path.read_text(encoding="utf-8").strip()
        )
    return "\n\n".join(contracts) or "// No persistence entity contracts found"


def ensure_mapper_accessible_persistence_constructor(
    sandbox: Path, relative_paths: list[str]
) -> list[str]:
    """Promote generated entity no-arg constructors required by the mapper.

    Persistence entities live in ``persistence.entity`` while the generated
    mapper lives in the sibling ``persistence.mapper`` package.  A protected
    JPA constructor is therefore not usable by a mapper that deliberately has
    no permission to edit the entity.  JPA permits public no-arg constructors,
    so normalize only the matching entity constructor in its contracted output.
    """
    repaired: list[str] = []
    for relative in relative_paths:
        normalized = relative.replace("\\", "/")
        if "/persistence/entity/" not in normalized or not normalized.endswith("Entity.java"):
            continue
        path = sandbox / relative
        if not path.is_file():
            continue
        class_name = path.stem
        source = path.read_text(encoding="utf-8")
        updated, replacements = re.subn(
            rf"\b(?:protected|private)\s+{re.escape(class_name)}\s*\(\s*\)",
            f"public {class_name}()",
            source,
            count=1,
        )
        if replacements:
            path.write_text(updated, encoding="utf-8")
            repaired.append(normalized)
    return repaired


def prepare_agent_workspace(run_root: Path, task: dict[str, object]) -> Path:
    run_key = run_root.name.removeprefix("run_")[:12]
    task_key = str(task["task_id"]).removeprefix("implement-")
    # 작업 ID는 보고서에서 읽기 쉬운 전체 이름을 유지한다. 다만 Windows 임시 경로에 같은
    # 이름을 그대로 붙이면 persistence처럼 여러 Entity를 묶은 작업이 260자 제한에 닿는다.
    # 임시 폴더만 앞부분과 해시로 줄이면 충돌을 피하면서 어떤 작업인지도 알아볼 수 있다.
    sandbox_parent = Path(tempfile.gettempdir()) / "easydep-agent-workspaces" / run_key
    longest_output = max(
        (len(str(Path(str(path)))) for path in task["allowed_write_paths"]),
        default=0,
    )
    # ``-2`` 같은 충돌 회피 suffix까지 붙을 수 있도록 네 글자를 남긴다.
    available_task_length = 240 - len(str(sandbox_parent.resolve())) - longest_output - 6
    if available_task_length < 8:
        raise ValueError("Agent workspace root leaves no safe Windows path budget")
    if len(task_key) > available_task_length:
        digest = hashlib.sha256(task_key.encode("utf-8")).hexdigest()[:10]
        prefix_length = available_task_length - len(digest) - 1
        task_key = (
            f"{task_key[:prefix_length]}-{digest}"
            if prefix_length > 0
            else digest[:available_task_length]
        )
    sandbox_base = sandbox_parent / task_key
    sandbox = sandbox_base
    suffix = 1
    while sandbox.exists():
        try:
            shutil.rmtree(sandbox, onerror=remove_readonly)
        except PermissionError:
            suffix += 1
            sandbox = sandbox_base.with_name(f"{sandbox_base.name}-{suffix}")
            continue
        break
    shutil.copytree(
        run_root / "application",
        sandbox / "application",
        ignore=shutil.ignore_patterns(
            "deployment-bundle", "build", ".gradle", "node_modules", "dist"
        ),
    )
    for relative in task["allowed_write_paths"]:
        target = sandbox / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt" and len(str(target.resolve())) > 240:
            raise ValueError(f"Agent write path exceeds safe Windows path budget: {target}")
    return sandbox


def read_allowed_sources(sandbox: Path, relative_paths: list[str]) -> str:
    sections: list[str] = []
    for relative in relative_paths:
        path = sandbox / relative
        content = path.read_text(encoding="utf-8") if path.is_file() else "// File missing"
        sections.append(f"### {relative}\n```java\n{content}\n```")
    return "\n\n".join(sections)


def snapshot_files(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_file():
            relative = path.relative_to(root)
            if path.name == "package-lock.json" or path.name.endswith(".tsbuildinfo"):
                continue
            if any(
                part in {"build", ".gradle", "node_modules", "dist"}
                for part in relative.parts
            ):
                continue
            result[str(relative).replace("\\", "/")] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


def changed_files(before: dict[str, str], after: dict[str, str]) -> set[str]:
    return {
        path for path in before.keys() | after.keys() if before.get(path) != after.get(path)
    }
