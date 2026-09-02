from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from ..domain.implementation_ir import remove_readonly

_IGNORED_WORKSPACE_PARTS = {
    "build",
    ".gradle",
    "node_modules",
    "dist",
}


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


def prepare_agent_workspace(
    run_root: Path,
    task: dict[str, object],
    *,
    preserve_failed_edits: bool = True,
) -> Path:
    """작업별 임시 공간을 만들고 현재 run source와 맞춘다.

    한 대화 안에서는 OpenHands가 자유롭게 여러 번 수정한다. 프로세스가 끝난 뒤 시작하는
    자동 수리는 ``preserve_failed_edits=False``를 사용해 실패한 후보를 버리고, 마지막으로
    검사를 통과해 run에 반영된 source에서 새로 시작한다. build와 package cache는 복사하지
    않는다.
    """
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
    source_application = run_root / "application"
    sandbox_application = sandbox / "application"
    editable = {
        str(path).replace("\\", "/")
        for path in task.get("allowed_write_paths", [])
    }
    editable_roots = {
        str(path).replace("\\", "/").rstrip("/")
        for path in task.get("allowed_write_roots", [])
    }
    immutable = {
        str(path).replace("\\", "/").rstrip("/")
        for path in task.get("immutable_paths", [])
    }
    if sandbox_application.is_dir():
        _refresh_agent_workspace(
            run_root,
            source_application,
            sandbox,
            sandbox_application,
            editable,
            editable_roots,
            immutable,
            preserve_failed_edits=preserve_failed_edits,
        )
    else:
        sandbox.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            source_application,
            sandbox_application,
            ignore=shutil.ignore_patterns(*_IGNORED_WORKSPACE_PARTS),
        )
    for relative in task["allowed_write_paths"]:
        target = sandbox / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt" and len(str(target.resolve())) > 240:
            raise ValueError(f"Agent write path exceeds safe Windows path budget: {target}")
    return sandbox


def _refresh_agent_workspace(
    run_root: Path,
    source_application: Path,
    sandbox: Path,
    sandbox_application: Path,
    editable: set[str],
    editable_roots: set[str],
    immutable: set[str],
    *,
    preserve_failed_edits: bool,
) -> None:
    """선택에 따라 미완성 편집을 보존하거나 승인된 run source로 되돌린다."""
    source_files: set[str] = set()
    for source in source_application.rglob("*"):
        if not source.is_file():
            continue
        relative_application = source.relative_to(source_application)
        if any(part in _IGNORED_WORKSPACE_PARTS for part in relative_application.parts):
            continue
        relative_run = (Path("application") / relative_application).as_posix()
        source_files.add(relative_run)
        target = sandbox / relative_run
        if (
            preserve_failed_edits
            and path_is_editable(
                relative_run,
                editable,
                editable_roots,
                immutable,
            )
            and target.is_file()
        ):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    for target in sandbox_application.rglob("*"):
        if not target.is_file():
            continue
        relative_application = target.relative_to(sandbox_application)
        # Gradle/npm 산출물과 package cache는 source 동기화 대상이 아니다. 이전 검증이
        # 만든 파일을 여기서 지우면 증분 build 이점을 잃을 뿐 아니라, Windows에서는
        # 종료 중인 test worker가 output.bin을 잠시 잡고 있어 WinError 32가 발생한다.
        # 성공 뒤 cleanup_agent_workspace가 sandbox 전체를 별도로 정리한다.
        if any(
            part in _IGNORED_WORKSPACE_PARTS
            for part in relative_application.parts
        ):
            continue
        relative_run = target.relative_to(sandbox).as_posix()
        editable_extra = path_is_editable(
            relative_run,
            editable,
            editable_roots,
            immutable,
        )
        if relative_run not in source_files and (
            not preserve_failed_edits or not editable_extra
        ):
            target.unlink()


def path_is_editable(
    relative_path: str,
    allowed_files: set[str] | list[str],
    allowed_roots: set[str] | list[str],
    immutable_paths: set[str] | list[str],
) -> bool:
    """상대 경로가 쓰기 범위 안이고 읽기 전용 계약 밖인지 확인한다."""
    path = relative_path.replace("\\", "/").strip("/")
    immutable = {
        str(item).replace("\\", "/").strip("/") for item in immutable_paths
    }
    if any(path == root or path.startswith(root + "/") for root in immutable):
        return False
    files = {
        str(item).replace("\\", "/").strip("/") for item in allowed_files
    }
    if path in files:
        return True
    roots = {
        str(item).replace("\\", "/").strip("/") for item in allowed_roots
    }
    return any(path == root or path.startswith(root + "/") for root in roots)


def cleanup_agent_workspace(sandbox: Path) -> None:
    """성공한 작업의 임시 공간만 안전하게 삭제한다."""
    expected_root = (Path(tempfile.gettempdir()) / "easydep-agent-workspaces").resolve()
    resolved = sandbox.resolve()
    if expected_root not in resolved.parents:
        raise ValueError(f"Refusing to remove a non-agent workspace: {resolved}")
    if resolved.exists():
        try:
            shutil.rmtree(resolved, onerror=remove_readonly)
        except OSError:
            # OpenHands가 닫힌 직후 Windows가 파일 handle을 잠깐 유지할 수 있다. 이 경우
            # 구현 성공을 실패로 바꾸지 않고 다음 정리 때 다시 제거한다.
            return


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
