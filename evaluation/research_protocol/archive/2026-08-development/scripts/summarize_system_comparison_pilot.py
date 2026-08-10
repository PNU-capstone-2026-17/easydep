"""저장된 P2-Azure 개발 실행을 검증 강도별로 요약한다.

새 실행을 만들지 않으며, 서로 다른 검증 강도를 성공으로 합치지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from evaluation.research_protocol.core.paths import REPOSITORY_ROOT as ROOT

CASE = ROOT / "evaluation/baselines/cases/p2-persistent-azure.json"
OUTPUT = ROOT / "evaluation/research_protocol/measurements/2026-08-development/system-comparison-p2-azure-pilot.json"
RUNS = (
    ("cot-standard", "cot-standard-p2-azure-20260805T214305Z-a09252", None),
    ("metagpt-standard", "metagpt-standard-p2-azure-20260805T214412Z-4855e6", None),
    ("easydep-full-initial", "easydep-full-p2-azure-20260808T064324Z-72f07e", None),
    (
        "easydep-full-repair-1",
        "easydep-full-p2-azure-20260808T064324Z-72f07e",
        "repairs/attempt-1",
    ),
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _status(value: Any) -> str | None:
    return value.get("status") if isinstance(value, dict) else None


def summarize() -> dict[str, Any]:
    case = _read(CASE)
    rows: list[dict[str, Any]] = []
    for arm, run_id, suffix in RUNS:
        run_root = ROOT / "artifacts/runs" / run_id
        evidence_root = run_root / suffix if suffix else run_root
        manifest = _read(evidence_root / "manifest.json")
        evaluation = _read(evidence_root / "evaluation.json")
        tools = evaluation.get("externalTools") or {}
        container = tools.get("container") or {}
        score = evaluation.get("score") or {}
        rows.append(
            {
                "arm": arm,
                "runId": run_id,
                "evidencePath": str(evidence_root.relative_to(ROOT)).replace("\\", "/"),
                "caseId": manifest.get("caseId"),
                "model": manifest.get("model"),
                "temperature": manifest.get("temperature"),
                "seed": manifest.get("seed"),
                "runStatus": manifest.get("status"),
                "elapsedSeconds": manifest.get("elapsedSeconds"),
                "staticPassRate": score.get("passRate"),
                "staticPassed": score.get("passed"),
                "staticFailed": score.get("failed"),
                "staticUnknown": score.get("unknown"),
                "iacValidation": _status(tools.get("iacEngine")),
                "containerValidation": _status(container),
                "containerBuild": _status(container.get("build")),
                "healthProbe": _status(container.get("health")),
                "functionalAcceptance": _status(container.get("functionalAcceptance")),
                "persistenceAcceptance": _status(container.get("persistenceAcceptance")),
                "experimentEligible": bool(evaluation.get("experimentEligible")),
            }
        )
    return {
        "schemaVersion": "easydep-system-comparison-pilot/v1",
        "case": {
            "caseId": case["caseId"],
            "sha256": hashlib.sha256(CASE.read_bytes()).hexdigest(),
            "path": str(CASE.relative_to(ROOT)).replace("\\", "/"),
        },
        "classification": "development-pilot",
        "rows": rows,
        "interpretationRules": [
            "정적 의미 점수는 애플리케이션 기능 성공을 뜻하지 않는다.",
            "실행하지 않은 외부 검증은 실패가 아니라 미측정으로 남긴다.",
            "EasyDep 최초 산출물과 부분 복구 산출물은 별개의 시점으로 보고한다.",
            "코드 리비전과 검증 강도가 달라 시스템 간 인과 효과를 주장하지 않는다.",
        ],
    }


def main() -> None:
    OUTPUT.write_text(
        json.dumps(summarize(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(OUTPUT)


if __name__ == "__main__":
    main()
