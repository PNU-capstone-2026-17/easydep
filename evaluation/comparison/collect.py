"""각 비교 대상이 만든 산출물을 사람이 열어볼 수 있는 폴더로 모은다.

보고서는 개수만 담는다. 실제 파일은 실행 폴더 깊숙이 흩어져 있고 프레임워크마다
경로 규칙이 다르므로, 같은 의미 범주끼리 나란히 놓아야 내용을 비교할 수 있다.

    artifacts/
      easydep/classDiagram/design/class-diagram.puml
      metagpt/classDiagram/docs/class_view.mmd
      chatdev/sourceCode/main.py
      INDEX.md
"""

from __future__ import annotations

import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

MAX_COPY_BYTES = 10 * 1024 * 1024


def _artifact_ids(report: dict[str, Any]) -> list[str]:
    protocol = report.get("promptProtocol") or {}
    contract = protocol.get("artifactContract") or []
    return [str(item.get("id")) for item in contract if isinstance(item, dict)]


def _source_path(workspace: Path, raw: str) -> Path:
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else workspace / candidate


def _destination(
    root: Path,
    run: dict[str, Any],
    artifact_id: str,
    relative: str,
    *,
    with_case: bool,
    with_run: bool,
) -> Path:
    parts = [str(run["armId"])]
    if with_case and run.get("caseId"):
        parts.append(str(run["caseId"]))
    if with_run:
        parts.append(f"run-{int(run['repetition']):03d}")
    parts.append(artifact_id)
    return root.joinpath(*parts, relative)


def _copy(source: Path, destination: Path) -> bool:
    try:
        if not source.is_file() or source.stat().st_size > MAX_COPY_BYTES:
            return False
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return True
    except OSError:
        return False


def _index(
    report: dict[str, Any],
    artifact_ids: list[str],
    counts: dict[tuple[str, str], int],
    arms: list[str],
) -> str:
    lines = [
        f"# 산출물 모음: {report['experimentId']}",
        "",
        "각 비교 대상이 실제로 만든 파일을 공통 산출물 범주별로 모았습니다. 표기 형식은",
        "프레임워크마다 다를 수 있으며, 같은 칸에 놓인 것은 같은 의미 범주라는 뜻입니다.",
        "숫자는 그 범주에 모인 파일 수이고 `-`는 그 대상이 만들지 않았다는 뜻입니다.",
        "",
        "| 공통 산출물 | " + " | ".join(arms) + " |",
        "|---" * (len(arms) + 1) + "|",
    ]
    for artifact_id in artifact_ids:
        cells = []
        for arm in arms:
            total = counts.get((arm, artifact_id), 0)
            cells.append(str(total) if total else "-")
        lines.append(f"| {artifact_id} | " + " | ".join(cells) + " |")
    lines.extend(
        [
            "",
            "## 폴더 구조",
            "",
            "```",
            "<대상>/<공통 산출물>/<원래 경로 그대로>",
            "```",
            "",
            "원래 경로를 유지하므로 프레임워크가 파일을 어디에 두었는지도 함께 볼 수 있습니다.",
            "",
        ]
    )
    return "\n".join(lines)


def collect_artifacts(report: dict[str, Any], directory: Path) -> Path:
    """보고서가 가리키는 산출물 파일을 `directory/artifacts` 아래로 복사한다."""
    root = directory / "artifacts"
    if root.exists():
        shutil.rmtree(root)
    runs = report.get("runs") or []
    artifact_ids = _artifact_ids(report)
    repetitions_by_arm: dict[str, set[int]] = defaultdict(set)
    for run in runs:
        repetitions_by_arm[str(run["armId"])].add(int(run["repetition"]))
    with_case = any(run.get("caseId") for run in runs)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    arms: list[str] = []
    for run in runs:
        arm = str(run["armId"])
        if arm not in arms:
            arms.append(arm)
        workspace = Path(str(run.get("workspace") or ""))
        evidence = run.get("artifactEvidence") or {}
        with_run = len(repetitions_by_arm[arm]) > 1
        for artifact_id in artifact_ids:
            for raw in evidence.get(artifact_id, []):
                source = _source_path(workspace, str(raw))
                try:
                    relative = source.relative_to(workspace).as_posix()
                except ValueError:
                    relative = source.name
                destination = _destination(
                    root, run, artifact_id, relative, with_case=with_case, with_run=with_run
                )
                if _copy(source, destination):
                    counts[(arm, artifact_id)] += 1
    root.mkdir(parents=True, exist_ok=True)
    (root / "INDEX.md").write_text(
        _index(report, artifact_ids, counts, arms), encoding="utf-8"
    )
    return root
