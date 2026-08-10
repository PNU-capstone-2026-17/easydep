"""저장된 실행 체크포인트에서 스냅숏 실험 문맥을 읽는다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _load_stage(run: Path, stage: str) -> dict[str, Any]:
    value = json.loads((run / stage / "result.json").read_text(encoding="utf-8"))
    return dict(value.get("data") or {})


def load_context(
    case: dict[str, Any], *, repository_root: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    run = (repository_root / case["contextRun"]).resolve()
    if not run.is_dir() or repository_root not in run.parents:
        raise ValueError(f"유효한 저장소 내부 context run이 아니다: {run}")
    requirements = _load_stage(run, "01-requirements")["member_result"]
    design = _load_stage(run, "02-design")
    return requirements, design["design_result"], design["cloud_design_result"]


def source_app_id(source: Path) -> str:
    manifest = source.parent.parent / "manifest.json"
    if not manifest.is_file():
        raise ValueError(f"기준 스냅숏 manifest가 없다: {manifest}")
    app_id = str(json.loads(manifest.read_text(encoding="utf-8")).get("appId") or "")
    if not app_id:
        raise ValueError(f"기준 스냅숏 appId가 없다: {manifest}")
    return app_id
