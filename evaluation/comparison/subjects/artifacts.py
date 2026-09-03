"""프레임워크별 파일 배치를 공통 산출물 근거로 정규화한다."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

SKIP_PARTS = {
    ".git",
    ".gradle",
    ".idea",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "target",
    "build",
    "dist",
}
TEXT_SUFFIXES = {
    ".c",
    ".cpp",
    ".css",
    ".go",
    ".graphql",
    ".h",
    ".html",
    ".java",
    ".js",
    ".json",
    ".kt",
    ".md",
    ".mermaid",
    ".mmd",
    ".php",
    ".properties",
    ".puml",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".tf",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
}
SOURCE_SUFFIXES = {
    ".c",
    ".cpp",
    ".go",
    ".java",
    ".js",
    ".kt",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".ts",
    ".tsx",
    ".vue",
}


def iter_files(workspace: Path) -> Iterable[Path]:
    for path in workspace.rglob("*"):
        if not path.is_file() or any(part in SKIP_PARTS for part in path.parts):
            continue
        try:
            if path.stat().st_size > 10 * 1024 * 1024:
                continue
        except OSError:
            continue
        yield path


def _relative(path: Path, workspace: Path) -> str:
    return path.relative_to(workspace).as_posix()


def _contains(relative: str, *needles: str) -> bool:
    """workspace 기준 상대 경로만 본다. 출력 폴더 이름이 판정을 바꾸면 안 된다."""
    value = relative.lower()
    return any(needle in value for needle in needles)


API_SPEC_HINTS = ("openapi", "swagger", "api_spec", "api-spec", "apispec")
# `test`/`spec`을 경로 구분자나 밑줄로 끊어진 낱말로만 인정한다. `latest.py`나
# `api_spec.json`이 시험 산출물로 잡히면 공통 산출물 개수가 왜곡된다.
TEST_TOKEN = re.compile(r"(?:^|[/_.-])(?:tests?|specs?)(?:[/_.-]|$)")


def _is_test(relative: str) -> bool:
    value = relative.lower()
    if any(hint in value for hint in API_SPEC_HINTS):
        return False
    return bool(TEST_TOKEN.search(value))


def collect_artifact_evidence(workspace: Path) -> dict[str, list[str]]:
    """존재하는 파일만 공통 산출물 ID에 매핑한다."""
    evidence: dict[str, set[str]] = {
        "requirements": set(),
        "classDiagram": set(),
        "sequenceDiagram": set(),
        "apiSpecification": set(),
        "dataModel": set(),
        "sourceCode": set(),
        "tests": set(),
        "container": set(),
        "infrastructure": set(),
    }
    for path in iter_files(workspace):
        relative = _relative(path, workspace)
        name = path.name.lower()
        suffix = path.suffix.lower()
        if _contains(relative, "requirement", "prd", "usecase", "use_case"):
            evidence["requirements"].add(relative)
        if _contains(
            relative,
            "class_diagram",
            "class-diagram",
            "classdiagram",
            "system_design",
            "data_api_design",
        ):
            evidence["classDiagram"].add(relative)
        if _contains(relative, "sequence", "seq_flow", "seq-flow"):
            evidence["sequenceDiagram"].add(relative)
        if _contains(relative, *API_SPEC_HINTS):
            evidence["apiSpecification"].add(relative)
        if _contains(relative, "data_model", "data-model", "erd", "schema.sql"):
            evidence["dataModel"].add(relative)
        is_test = _is_test(relative)
        if suffix in SOURCE_SUFFIXES and not is_test:
            evidence["sourceCode"].add(relative)
        if is_test and suffix in SOURCE_SUFFIXES | {".xml", ".json", ".yaml", ".yml"}:
            evidence["tests"].add(relative)
        if name in {"dockerfile", "compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml"}:
            evidence["container"].add(relative)
        if suffix == ".tf":
            evidence["infrastructure"].add(relative)
    return {key: sorted(paths) for key, paths in evidence.items() if paths}


def collect_requirement_evidence(
    workspace: Path,
    requirement_ids: Iterable[str],
    artifact_evidence: dict[str, list[str]],
) -> dict[str, dict[str, list[str]]]:
    """파일 본문에 요구사항 ID가 명시된 경우에만 추적 근거로 인정한다."""
    by_path: dict[str, str] = {}
    for path in iter_files(workspace):
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            by_path[_relative(path, workspace)] = path.read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            continue
    path_stage: dict[str, str] = {}
    stage_artifacts = {
        "requirements": ("requirements",),
        "design": ("classDiagram", "sequenceDiagram", "apiSpecification", "dataModel"),
        "code": ("sourceCode",),
        "test": ("tests",),
        "deployment": ("container", "infrastructure"),
    }
    for stage, artifact_ids in stage_artifacts.items():
        for artifact_id in artifact_ids:
            for relative in artifact_evidence.get(artifact_id, []):
                path_stage.setdefault(relative, stage)
    result: dict[str, dict[str, list[str]]] = {}
    for requirement_id in requirement_ids:
        pattern = re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(requirement_id)}(?![A-Za-z0-9_-])", re.IGNORECASE)
        stages: dict[str, list[str]] = {}
        for relative, text in by_path.items():
            if pattern.search(text):
                stages.setdefault(path_stage.get(relative, "other"), []).append(relative)
        if stages:
            result[requirement_id] = {
                stage: sorted(paths) for stage, paths in stages.items()
            }
    return result


def write_evidence_files(
    workspace: Path, run_dir: Path, requirement_ids: Iterable[str]
) -> tuple[Path, Path, dict[str, list[str]]]:
    artifact_evidence = collect_artifact_evidence(workspace)
    requirement_evidence = collect_requirement_evidence(
        workspace, requirement_ids, artifact_evidence
    )
    artifact_path = run_dir / "artifact-evidence.json"
    requirement_path = run_dir / "requirement-evidence.json"
    artifact_path.write_text(
        json.dumps(artifact_evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    requirement_path.write_text(
        json.dumps(requirement_evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return artifact_path, requirement_path, artifact_evidence
