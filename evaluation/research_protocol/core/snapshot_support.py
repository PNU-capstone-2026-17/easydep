"""고정 애플리케이션 스냅숏 실험의 공용 파일·검증 연산."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from time import perf_counter
from typing import Any

from app.orchestration.app_cloud_contracts import (
    contract_value,
    infer_application_contract,
    validate_application_consistency,
)
from app.implementation.delivery.iac_binding_validation import validate_iac_bindings
from evaluation.research_protocol.core.paths import REPOSITORY_ROOT

IGNORED_SNAPSHOT_PARTS = frozenset({"build", ".gradle", ".terraform"})


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root)
        if any(
            part in IGNORED_SNAPSHOT_PARTS or part.startswith(".easydep-test-")
            for part in relative.parts
        ):
            continue
        digest.update(relative.as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def portable_result(
    value: Any, *, temporary: Path, repository_root: Path = REPOSITORY_ROOT
) -> Any:
    """실행 PC에 종속되는 임시·저장소 절대 경로를 치환한다."""
    if isinstance(value, dict):
        return {
            key: portable_result(
                item, temporary=temporary, repository_root=repository_root
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            portable_result(item, temporary=temporary, repository_root=repository_root)
            for item in value
        ]
    if isinstance(value, str):
        result = value
        for path, replacement in (
            (temporary, "<temporary>"),
            (repository_root, "<repository>"),
        ):
            result = result.replace(str(path), replacement).replace(
                path.as_posix(), replacement
            )
        return result
    return value


def copy_source(source: Path, target: Path) -> None:
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(
            "build", ".gradle", ".terraform", ".easydep-test-*"
        ),
    )


def apply_mutations(
    application: Path, mutations: list[dict[str, Any]]
) -> list[str]:
    changed: list[str] = []
    for mutation in mutations:
        relative = Path(str(mutation["path"]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"스냅샷 밖의 변경 경로는 허용하지 않는다: {relative}")
        target = application / relative
        operation = mutation["operation"]
        if operation == "write":
            if target.exists():
                raise ValueError(f"write 변경은 기존 파일을 덮어쓸 수 없다: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(mutation["content"]), encoding="utf-8")
        elif operation == "replace":
            content = target.read_text(encoding="utf-8")
            old = str(mutation["old"])
            expected_count = int(mutation.get("count", 1))
            actual_count = content.count(old)
            if actual_count != expected_count:
                raise ValueError(
                    f"변경 전제 불일치: {relative}, "
                    f"expected={expected_count}, actual={actual_count}"
                )
            target.write_text(
                content.replace(old, str(mutation["new"])), encoding="utf-8"
            )
        else:
            raise ValueError(f"지원하지 않는 변경 연산: {operation}")
        changed.append(relative.as_posix())
    return sorted(changed)


def terraform_files(application: Path) -> dict[str, str]:
    infra = application / "infra"
    return {
        path.relative_to(infra).as_posix(): path.read_text(
            encoding="utf-8", errors="replace"
        )
        for path in sorted(infra.rglob("*"))
        if path.is_file()
        and path.suffix in {".tf", ".tpl", ".tftpl", ".yaml", ".yml", ".sh"}
    }


def preflight(application: Path, boundary: str) -> dict[str, Any]:
    started = perf_counter()
    contract = infer_application_contract(application)
    if boundary == "application":
        diagnostics = [
            item.model_dump(mode="json")
            for item in validate_application_consistency(application, contract)
        ]
        observations: list[dict[str, Any]] = []
    elif boundary == "deployment":
        port = int(contract_value(contract, "runtime.port", "port", 8080))
        mount = contract_value(contract, "runtime.storage", "accessPath")
        report = validate_iac_bindings(
            terraform_files(application),
            application_port=port,
            mount_path=str(mount) if mount else None,
        )
        diagnostics = report["diagnostics"]
        observations = report["observations"]
    else:
        raise ValueError(f"지원하지 않는 검증 경계: {boundary}")
    return {
        "diagnostics": diagnostics,
        "observations": observations,
        "elapsedSeconds": round(perf_counter() - started, 6),
    }
